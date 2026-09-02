from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel
from typing import Optional, List
import asyncpg, uuid, json

try:
    from backend.database import get_db_connection, get_current_user, require_admin, record_stock_movement, log_action, check_idempotency, save_idempotency
except ImportError:
    from database import get_db_connection, get_current_user, require_admin, record_stock_movement, log_action, check_idempotency, save_idempotency

router = APIRouter(tags=["Internal Movements"])

class MobileTransferScanInput(BaseModel):
    transfer_number: str
    sku: str
    quantity: float
    destination_location_code: Optional[str] = None
    serial_numbers: Optional[List[str]] = []

class TransferLineInput(BaseModel):
    sku: str
    quantity: float
    origin_location_code: Optional[str] = None
    destination_location_code: Optional[str] = None
    lot_number: Optional[str] = ""
    serial_numbers: Optional[List[str]] = []

class TransferOrderCreateInput(BaseModel):
    transfer_number: str
    origin_branch_id: str
    origin_sector_id: str
    destination_branch_id: str
    destination_sector_id: str
    lines: List[TransferLineInput]

@router.get("/api/admin/replenishment-suggestions")
async def get_replenishment_suggestions(admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    enabled = await conn.fetchval("SELECT value FROM system_settings WHERE key = 'enable_replenishment'")
    if enabled != "true":
        return {"status": "disabled", "suggestions": []}
        
    query = """
        SELECT il.item_sku as sku, i.description, 
               l.location_code as destination_location,
               COALESCE((SELECT SUM(quantity) FROM stock_inventory WHERE sku = il.item_sku AND location_id = il.location_id), 0) as stock_picking,
               COALESCE((SELECT SUM(quantity) FROM stock_inventory WHERE sku = il.item_sku AND location_id != il.location_id), 0) as stock_pulmon,
               (SELECT l2.location_code FROM stock_inventory si2 JOIN locations l2 ON si2.location_id = l2.id WHERE si2.sku = il.item_sku AND si2.location_id != il.location_id AND si2.quantity > 0 LIMIT 1) as origin_location
        FROM item_locations il
        JOIN locations l ON il.location_id = l.id
        JOIN items i ON il.item_sku = i.sku
    """
    rows = await conn.fetch(query)
    suggestions = [dict(r) for r in rows if r["stock_picking"] <= 0 and r["stock_pulmon"] > 0]
    return {"status": "enabled", "suggestions": suggestions}

@router.get("/api/admin/transfer-orders/next-number")
async def get_next_transfer_number(admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    import re
    val = await conn.fetchval("SELECT transfer_number FROM transfer_orders ORDER BY created_at DESC LIMIT 1")
    if val:
        digits = re.findall(r'\d+', val)
        if digits:
            last_num = int(digits[-1]) + 1
            next_num = f"TR-{last_num:06d}"
        else:
            next_num = "TR-000001"
    else:
        next_num = "TR-000001"
    return {"next_number": next_num}

@router.get("/api/admin/transfer-orders")
async def list_admin_transfer_orders(search: str = "", limit: int = 50, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    rows = await conn.fetch("SELECT t.id::text as id, t.transfer_number, t.status, t.created_at, COALESCE(ob.name, 'N/A') as origin_branch, COALESCE(db.name, 'N/A') as destination_branch, COALESCE(os.name, 'N/A') as origin_sector, COALESCE(ds.name, 'N/A') as destination_sector FROM transfer_orders t LEFT JOIN branches ob ON t.origin_branch_id = ob.id LEFT JOIN branches db ON t.destination_branch_id = db.id LEFT JOIN sectors os ON t.origin_sector_id = os.id LEFT JOIN sectors ds ON t.destination_sector_id = ds.id WHERE t.transfer_number ILIKE $1 ORDER BY t.created_at DESC LIMIT $2", f"%{search}%", limit)
    return [dict(r) for r in rows]

@router.post("/api/admin/transfer-orders")
async def create_transfer_order(
    data: TransferOrderCreateInput, 
    x_idempotency_key: Optional[str] = Header(None),
    admin: dict = Depends(require_admin), 
    conn: asyncpg.Connection = Depends(get_db_connection)
):
    cached_resp = await check_idempotency(conn, x_idempotency_key, "/api/admin/transfer-orders")
    if cached_resp:
        return cached_resp[0]

    async with conn.transaction():
        existing = await conn.fetchval("SELECT id FROM transfer_orders WHERE UPPER(transfer_number) = $1", data.transfer_number.strip().upper())
        if existing:
            raise HTTPException(400, "El número de traspaso ya existe.")

        tr_id = await conn.fetchval("""
            INSERT INTO transfer_orders (transfer_number, origin_branch_id, origin_sector_id, destination_branch_id, destination_sector_id, status, created_by)
            VALUES ($1, $2, $3, $4, $5, 'PENDING', $6)
            RETURNING id
        """, data.transfer_number.strip().upper(), uuid.UUID(data.origin_branch_id), uuid.UUID(data.origin_sector_id), uuid.UUID(data.destination_branch_id), uuid.UUID(data.destination_sector_id), admin["username"])

        for line in data.lines:
            orig_loc_id = None
            if line.origin_location_code and line.origin_location_code.strip():
                loc = await conn.fetchrow("SELECT id FROM locations WHERE sector_id = $1 AND UPPER(location_code) = $2", uuid.UUID(data.origin_sector_id), line.origin_location_code.strip().upper())
                if loc: orig_loc_id = loc["id"]
                else: raise HTTPException(400, f"Ubicación Origen '{line.origin_location_code}' no existe en el sector.")

            dest_loc_id = None
            if line.destination_location_code and line.destination_location_code.strip():
                loc = await conn.fetchrow("SELECT id FROM locations WHERE sector_id = $1 AND UPPER(location_code) = $2", uuid.UUID(data.destination_sector_id), line.destination_location_code.strip().upper())
                if loc: dest_loc_id = loc["id"]
                else: raise HTTPException(400, f"Ubicación Destino '{line.destination_location_code}' no existe en el sector.")

            await conn.execute("""
                INSERT INTO transfer_order_lines (transfer_order_id, sku, quantity_sent, quantity_received, origin_location_id, destination_location_id, lot_number, serial_numbers)
                VALUES ($1, $2, $3, 0, $4, $5, $6, $7::jsonb)
            """, tr_id, line.sku.strip().upper(), line.quantity, orig_loc_id, dest_loc_id, line.lot_number or "", json.dumps(line.serial_numbers or []))

        await log_action(conn, admin["username"], "TRANSFER_CREATED", f"Orden de traspaso {data.transfer_number} creada.")
        res_data = {"status": "success", "message": "Orden de Traspaso (ODT) generada correctamente."}
        
        await save_idempotency(conn, x_idempotency_key, "/api/admin/transfer-orders", res_data)
        return res_data

@router.get("/api/transfers/orders")
@router.get("/api/transfers/pending")
async def get_transfer_orders(user: dict = Depends(get_current_user), conn: asyncpg.Connection = Depends(get_db_connection)):
    rows = await conn.fetch("""
        SELECT t.id::text as id, t.transfer_number, t.status, t.created_at, 
               COALESCE(ob.name, 'Origen') as origin_branch, COALESCE(db.name, 'Destino') as destination_branch,
               COALESCE(os.name, 'Sector Origen') as origin_sector, COALESCE(ds.name, 'Sector Destino') as destination_sector
        FROM transfer_orders t
        LEFT JOIN branches ob ON t.origin_branch_id = ob.id
        LEFT JOIN branches db ON t.destination_branch_id = db.id
        LEFT JOIN sectors os ON t.origin_sector_id = os.id
        LEFT JOIN sectors ds ON t.destination_sector_id = ds.id
        WHERE t.status IN ('PENDING', 'PENDING_CONTROL', 'IN_PROGRESS')
        ORDER BY t.created_at ASC
    """)
    return [dict(r) for r in rows]

@router.get("/api/transfers/orders/{transfer_number}")
async def get_transfer_order_details(transfer_number: str, user: dict = Depends(get_current_user), conn: asyncpg.Connection = Depends(get_db_connection)):
    tr = await conn.fetchrow("""
        SELECT t.id, t.transfer_number, t.status, t.origin_branch_id, t.origin_sector_id, t.destination_branch_id, t.destination_sector_id,
               COALESCE(ob.name, 'Origen') as origin_branch, COALESCE(db.name, 'Destino') as destination_branch
        FROM transfer_orders t LEFT JOIN branches ob ON t.origin_branch_id = ob.id LEFT JOIN branches db ON t.destination_branch_id = db.id
        WHERE UPPER(t.transfer_number) = $1
    """, transfer_number.strip().upper())
    if not tr: raise HTTPException(404, "Traspaso no encontrado")
    lines = await conn.fetch("""
        SELECT tol.id::text as id, tol.sku, tol.quantity_sent::float as quantity_sent, tol.quantity_received::float as quantity_received,
               ol.location_code as origin_location, dl.location_code as destination_location, tol.serial_numbers
        FROM transfer_order_lines tol LEFT JOIN locations ol ON tol.origin_location_id = ol.id LEFT JOIN locations dl ON tol.destination_location_id = dl.id
        WHERE tol.transfer_order_id = $1 ORDER BY tol.sku ASC
    """, tr["id"])
    return {"transfer": dict(tr), "lines": [dict(l) for l in lines]}

@router.post("/api/transfers/orders/{transfer_number}/scan")
async def scan_transfer_item(transfer_number: str, data: MobileTransferScanInput, user: dict = Depends(get_current_user), conn: asyncpg.Connection = Depends(get_db_connection)):
    async with conn.transaction():
        tr = await conn.fetchrow("SELECT id, status, origin_branch_id, origin_sector_id, destination_branch_id, destination_sector_id FROM transfer_orders WHERE UPPER(transfer_number) = $1 FOR UPDATE", transfer_number.strip().upper())
        if not tr: raise HTTPException(404, "Traspaso no encontrado")
        if tr["status"] == "COMPLETED": raise HTTPException(400, "Traspaso ya completado.")
        sku_clean = data.sku.strip().upper()
        line = await conn.fetchrow("SELECT id, quantity_sent, quantity_received, origin_location_id FROM transfer_order_lines WHERE transfer_order_id = $1 AND UPPER(sku) = $2", tr["id"], sku_clean)
        if not line: raise HTTPException(400, "SKU no pertenece al traspaso.")

        dest_loc_id = None
        if data.destination_location_code and data.destination_location_code.strip():
            loc = await conn.fetchrow("SELECT id FROM locations WHERE UPPER(location_code) = $1", data.destination_location_code.strip().upper())
            if loc: dest_loc_id = loc["id"]

        await conn.execute("""
            UPDATE transfer_order_lines 
            SET quantity_received = quantity_received + $1,
                serial_numbers = COALESCE(serial_numbers, '[]'::jsonb) || $2::jsonb 
            WHERE id = $3
        """, data.quantity, json.dumps(data.serial_numbers or []), line["id"])
        
        await record_stock_movement(conn, sku_clean, tr["origin_branch_id"], tr["origin_sector_id"], line["origin_location_id"], -data.quantity, 'TRANSFER_OUT', transfer_number.strip().upper(), user.get("username"), serial_numbers=data.serial_numbers)
        await record_stock_movement(conn, sku_clean, tr["destination_branch_id"], tr["destination_sector_id"], dest_loc_id, data.quantity, 'TRANSFER_IN', transfer_number.strip().upper(), user.get("username"), serial_numbers=data.serial_numbers)

        await conn.execute("UPDATE transfer_orders SET status = 'IN_PROGRESS' WHERE id = $1 AND status IN ('PENDING', 'PENDING_CONTROL')", tr["id"])
        pending = await conn.fetchval("SELECT COUNT(*) FROM transfer_order_lines WHERE transfer_order_id = $1 AND quantity_received < quantity_sent", tr["id"])
        if pending == 0: await conn.execute("UPDATE transfer_orders SET status = 'COMPLETED' WHERE id = $1", tr["id"])

        return {"status": "success", "message": f"Transferido {data.quantity} un de {sku_clean}", "transfer_completed": pending == 0}