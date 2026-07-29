from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
import asyncpg, uuid

try:
    from backend.database import get_db_connection, verify_system_api_key, require_admin, queue_zpl_print_job
except ImportError:
    from database import get_db_connection, verify_system_api_key, require_admin, queue_zpl_print_job

router = APIRouter(tags=["Printing Agent & Labels"])

@router.get("/api/print-agent/queues")
async def list_print_queues(conn: asyncpg.Connection = Depends(get_db_connection)):
    rows = await conn.fetch("""
        SELECT s.print_queue_code as queue_code, s.name as sector_name, b.name as branch_name 
        FROM sectors s 
        LEFT JOIN branches b ON s.branch_id = b.id 
        ORDER BY b.name, s.name ASC
    """)
    return [dict(r) for r in rows]

@router.get("/api/print-agent/jobs")
async def get_print_jobs_for_agent(queue_code: str, authenticated: bool = Depends(verify_system_api_key), conn: asyncpg.Connection = Depends(get_db_connection)):
    rows = await conn.fetch("""
        SELECT id::text as id, zpl_content, created_at 
        FROM print_jobs 
        WHERE UPPER(queue_code) = $1 AND status = 'PENDING' 
        ORDER BY created_at ASC LIMIT 10
    """, queue_code.strip().upper())
    return [dict(r) for r in rows]

@router.post("/api/print-agent/jobs/{job_id}/ack")
async def acknowledge_print_job(job_id: str, authenticated: bool = Depends(verify_system_api_key), conn: asyncpg.Connection = Depends(get_db_connection)):
    await conn.execute("UPDATE print_jobs SET status = 'PRINTED' WHERE id = $1", uuid.UUID(job_id))
    return {"status": "success"}

@router.post("/api/admin/sales-orders/{document_number}/print-label")
async def print_order_label_again(document_number: str, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    doc = await conn.fetchrow("""
        SELECT d.id, d.document_number, d.status, COALESCE(c.company_name, 'Consumidor Final') as client_name, COALESCE(a.full_address, 'A coordinar') as delivery_address
        FROM documents d
        LEFT JOIN entities c ON d.customer_id = c.id
        LEFT JOIN entity_addresses a ON d.customer_address_id = a.id
        WHERE UPPER(d.document_number) = $1
    """, document_number.strip().upper())
    
    if not doc: raise HTTPException(404, "Pedido no encontrado.")
    if doc["status"] not in ("COMPLETED", "DISPATCHED"):
        raise HTTPException(400, "El pedido aún no está totalmente preparado.")

    template = await conn.fetchval("SELECT value FROM system_settings WHERE key = 'zpl_order_template'")
    if not template:
        template = "^XA^FO50,50^A0N,40,40^FDPEDIDO: {order_number}^FS^FO50,110^A0N,30,30^FDCLIENTE: {client_name}^FS^FO50,170^A0N,25,25^FDDIR: {delivery_address}^FS^FO50,230^BY3^BCN,100,Y,N,N^FD{order_number}^FS^XZ"

    zpl = template.replace("{order_number}", doc["document_number"])\
                  .replace("{client_name}", doc["client_name"])\
                  .replace("{delivery_address}", doc["delivery_address"])

    default_queue = await conn.fetchval("SELECT print_queue_code FROM sectors WHERE uses_locations = FALSE LIMIT 1") or "PRINT-SEC-01"
    await queue_zpl_print_job(conn, default_queue, zpl)
    return {"status": "success", "message": f"Etiqueta enviada a la cola {default_queue}."}

@router.post("/api/admin/purchase-remitos/{remito_id}/print-labels")
async def print_remito_item_labels(remito_id: str, queue_code: str = "PRINT-SEC-01", admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    lines = await conn.fetch("""
        SELECT prl.sku, prl.quantity_received, COALESCE(i.description, prl.sku) as description
        FROM purchase_remito_lines prl
        LEFT JOIN items i ON UPPER(prl.sku) = UPPER(i.sku)
        WHERE prl.purchase_remito_id = $1 AND prl.quantity_received > 0
    """, uuid.UUID(remito_id))

    if not lines or len(lines) == 0:
        raise HTTPException(400, "El remito no tiene ítems controlados/recibidos.")

    template = await conn.fetchval("SELECT value FROM system_settings WHERE key = 'zpl_item_template'")
    if not template:
        template = "^XA^FO50,30^A0N,30,30^FD{description}^FS^FO50,70^A0N,25,25^FDSKU: {sku}^FS^FO50,110^BY2^BCN,80,Y,N,N^FD{sku}^FS^XZ"

    total_queued = 0
    async with conn.transaction():
        for line in lines:
            sku_clean = line["sku"].strip().upper()
            qty = int(line["quantity_received"])
            zpl = template.replace("{sku}", sku_clean).replace("{description}", line["description"])
            for _ in range(qty):
                await queue_zpl_print_job(conn, queue_code, zpl)
                total_queued += 1

    return {"status": "success", "message": f"Enviadas {total_queued} etiquetas de artículos a la cola {queue_code}."}
