import subprocess, os, sys, re

print("=== 1. MOTIVO DE LA CAÍDA (LOGS DE DOCKER) ===")
try:
    logs = subprocess.check_output(["sudo", "docker", "logs", "tracker360_api", "--tail", "15"], stderr=subprocess.STDOUT).decode('utf-8', errors='ignore')
    print(logs)
except Exception as e:
    print(f"Error leyendo logs: {e}")

print("\n=== 2. RESTAURANDO backend/routers/printing.py ===")
printing_code = '''from fastapi import APIRouter, Depends, HTTPException
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

    return [{"id": str(j["id"]), "zpl": j["zpl_content"]} for j in jobs]

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
            zpl_lines = [
                "^XA",
                "^PW304",
                "^LL160",
                "^LS0",
                f"^FO40,25^A0N,24,24^FD{clean_sku}^FS",
                f"^FO40,65^A0N,18,18^FD{short_desc}^FS",
                f"^FO205,20^BQN,2,3^FDLA,{clean_sku}^FS",
                "^XZ"
            ]
            zpl = "\n".join(zpl_lines)

        await conn.execute("""
            INSERT INTO print_jobs (id, queue_code, zpl_content, status, created_at)
            VALUES ($1, $2, $3, 'PENDING', NOW())
        """, str(uuid.uuid4()), q_code, zpl)
        inserted += 1

    return {"status": "ok", "jobs_created": inserted}
'''

with open("backend/routers/printing.py", "w", encoding="utf-8") as f:
    f.write(printing_code)

print("\n=== 3. RESTAURANDO backend/routers/items.py ===")
if os.path.exists("backend/routers/items.py"):
    with open("backend/routers/items.py", "r", encoding="utf-8") as f:
        items_src = f.read()

    items_src = re.sub(r'@router\.post\(["\']/api/admin/items/batch-print-labels["\']\)[\s\S]*?(?=\n@|\Z)', '', items_src)

    batch_clean = '''
@router.post("/api/admin/items/batch-print-labels")
async def batch_print_items_labels(req: dict, conn: asyncpg.Connection = Depends(get_db_connection)):
    try:
        raw_queue = str(req.get("queue_code") or req.get("sector") or "RECEPCION").strip().upper()
        queue_code = "RECEPCION"
        
        if raw_queue and raw_queue not in ["1", "RECEPCION", "RECEPCIÓN"]:
            sector_row = await conn.fetchrow("""
                SELECT print_queue_code 
                FROM sectors 
                WHERE CAST(id AS TEXT) = $1 
                   OR UPPER(name) = UPPER($1) 
                   OR UPPER(print_queue_code) = UPPER($1)
                LIMIT 1
            """, raw_queue)
            if sector_row and sector_row["print_queue_code"]:
                queue_code = sector_row["print_queue_code"].strip().upper()
            else:
                queue_code = raw_queue

        skus = []
        if "skus" in req and isinstance(req["skus"], list):
            skus.extend(req["skus"])
        if "items" in req and isinstance(req["items"], list):
            for it in req["items"]:
                if isinstance(it, dict) and "sku" in it:
                    qty = int(it.get("quantity") or it.get("qty") or 1)
                    skus.extend([str(it["sku"]).strip()] * qty)
                elif isinstance(it, str):
                    skus.append(it.strip())

        if not skus:
            raise HTTPException(status_code=400, detail="Debe proporcionar al menos un SKU.")

        template_row = await conn.fetchrow("SELECT value FROM settings WHERE key = 'zpl_template'")
        custom_tpl = template_row["value"] if template_row and template_row["value"] else None

        inserted = 0
        for sku in skus:
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
                zpl_lines = [
                    "^XA",
                    "^PW304",
                    "^LL160",
                    "^LS0",
                    f"^FO40,25^A0N,24,24^FD{clean_sku}^FS",
                    f"^FO40,65^A0N,18,18^FD{short_desc}^FS",
                    f"^FO205,20^BQN,2,3^FDLA,{clean_sku}^FS",
                    "^XZ"
                ]
                zpl = "\n".join(zpl_lines)

            await conn.execute("""
                INSERT INTO print_jobs (id, queue_code, zpl_content, status, created_at)
                VALUES ($1, $2, $3, 'PENDING', NOW())
            """, str(uuid.uuid4()), queue_code, zpl)
            inserted += 1

        return {"status": "ok", "jobs_created": inserted, "queue_code": queue_code}

    except HTTPException as he:
        raise he
    except Exception as e:
        print(f"[BATCH PRINT ERROR]: {e}")
        raise HTTPException(status_code=500, detail=f"Error en backend: {str(e)}")
'''
    with open("backend/routers/items.py", "w", encoding="utf-8") as f:
        f.write(items_src.strip() + "\n\n" + batch_clean.strip() + "\n")

print("\n=== 4. COMPILANDO Y COMPROBANDO SINTAXIS PYTHON ===")
files_to_check = [
    "backend/main.py",
    "backend/database.py",
    "backend/routers/printing.py",
    "backend/routers/items.py",
    "backend/routers/operations.py",
    "backend/routers/settings.py"
]

all_ok = True
for filepath in files_to_check:
    if os.path.exists(filepath):
        res = subprocess.run(["python3", "-m", "py_compile", filepath])
        if res.returncode == 0:
            print(f" -> {filepath}: SINTAXIS OK")
        else:
            print(f" -> {filepath}: ERROR SINTÁCTICO DEDECIDO")
            all_ok = False

if not all_ok:
    print("\n[!] Error detectado en la verificación. Cancelando reinicio.")
    sys.exit(1)

print("\n=== 5. SINTAXIS 100% VALIDAD. REINICIANDO DOCKER ===")
