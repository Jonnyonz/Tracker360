from fastapi import APIRouter, Depends, HTTPException
from backend.database import get_db_connection
from pydantic import BaseModel
import asyncpg, uuid

router = APIRouter()

class PrintJobItem(BaseModel):
    sku: str
    quantity: int = 1

class PrintJobRequest(BaseModel):
    queue_code: str = "RECEPCION"
    skus: list[str] = []
    items: list[PrintJobItem] = []

@router.get("/api/print-agent/jobs")
async def get_pending_jobs(queue_code: str = "RECEPCION", conn: asyncpg.Connection = Depends(get_db_connection)):
    clean_q = queue_code.strip().upper() if queue_code else "RECEPCION"
    if clean_q in ["RECEPCION", "RECEPCIÓN", "1", ""]:
        jobs = await conn.fetch("""
            SELECT id, zpl_content 
            FROM print_jobs 
            WHERE status = 'PENDING' 
              AND (UPPER(TRIM(queue_code)) IN ('RECEPCION', 'RECEPCIÓN', '1', '') OR queue_code IS NULL)
            ORDER BY created_at ASC
        """)
    else:
        jobs = await conn.fetch("""
            SELECT id, zpl_content 
            FROM print_jobs 
            WHERE status = 'PENDING' AND UPPER(TRIM(queue_code)) = $1
            ORDER BY created_at ASC
        """, clean_q)

    return [{"id": str(j["id"]), "zpl": j["zpl_content"], "zpl_content": j["zpl_content"]} for j in jobs]

@router.post("/api/print-agent/jobs/{job_id}/ack")
async def ack_print_job(job_id: str, conn: asyncpg.Connection = Depends(get_db_connection)):
    try:
        await conn.execute("UPDATE print_jobs SET status = 'COMPLETED' WHERE CAST(id AS TEXT) = $1", str(job_id).strip())
    except Exception as e:
        print(f"[ACK ERROR]: {e}")
    return {"status": "ok", "job_id": job_id}

@router.post("/api/admin/print-jobs")
async def create_print_job(req: PrintJobRequest, conn: asyncpg.Connection = Depends(get_db_connection)):
    q_code = req.queue_code.strip().upper() if req.queue_code else "RECEPCION"
    sku_list = list(req.skus)
    for it in req.items:
        if it.sku:
            sku_list.extend([it.sku] * max(1, it.quantity))

    if not sku_list:
        raise HTTPException(status_code=400, detail="Debe ingresar al menos un SKU para imprimir.")

    template_row = await conn.fetchrow("SELECT value FROM settings WHERE key = 'zpl_template'")
    custom_tpl = template_row["value"] if template_row and template_row["value"] else None

    inserted = 0
    for sku in sku_list:
        clean_sku = str(sku).strip().upper()
        if not clean_sku: continue

        item_row = await conn.fetchrow("SELECT description FROM items WHERE UPPER(sku) = $1 LIMIT 1", clean_sku)
        clean_desc = item_row["description"] if item_row and item_row["description"] else clean_sku
        short_desc = clean_desc[:22]

        if custom_tpl:
            zpl = custom_tpl
            for tag in ["{{SKU}}", "{{sku}}", "{SKU}", "{sku}", "{{ SKU }}"]:
                zpl = zpl.replace(tag, clean_sku)
            for tag in ["{{DESC}}", "{{desc}}", "{DESC}", "{desc}", "{{DESCRIPTION}}", "{{description}}", "{{ DESC }}"]:
                zpl = zpl.replace(tag, short_desc)
        else:
            zpl = f"^XA\n^PW304\n^LL160\n^LS0\n^FO40,25^A0N,24,24^FD{clean_sku}^FS\n^FO40,65^A0N,18,18^FD{short_desc}^FS\n^FO205,20^BQN,2,3^FDLA,{clean_sku}^FS\n^XZ"

        await conn.execute("""
            INSERT INTO print_jobs (id, queue_code, zpl_content, status, created_at)
            VALUES ($1, $2, $3, 'PENDING', NOW())
        """, str(uuid.uuid4()), q_code, zpl)
        inserted += 1

    return {"status": "ok", "jobs_created": inserted}
