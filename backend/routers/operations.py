from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timezone
import asyncpg, uuid, re, json

try:
    from backend.database import (
        get_db_connection, get_current_user, require_admin,
        record_stock_movement, log_action, dispatch_event_to_channels, queue_zpl_print_job,
        check_idempotency, save_idempotency
    )
except ImportError:
    from database import (
        get_db_connection, get_current_user, require_admin,
        record_stock_movement, log_action, dispatch_event_to_channels, queue_zpl_print_job,
        check_idempotency, save_idempotency
    )

router = APIRouter(tags=["Operations & Logistics"])

class PickScanInput(BaseModel):
    sku: str
    quantity: float
    location_code: Optional[str] = None
    serial_numbers: Optional[List[str]] = []

class WavePickScanInput(BaseModel):
    order_numbers: List[str]
    sku: str
    quantity: float
    location_code: Optional[str] = None
    serial_numbers: Optional[List[str]] = []

class PackOrderInput(BaseModel):
    boxes: int

class MobileRemitoScanInput(BaseModel):
    remito_number: str
    sku: str
    quantity: float
    location_code: Optional[str] = None
    serial_numbers: Optional[List[str]] = []

class MobileTransferScanInput(BaseModel):
    transfer_number: str
    sku: str
    quantity: float
    destination_location_code: Optional[str] = None
    serial_numbers: Optional[List[str]] = []

class ManualOrderLine(BaseModel):
    sku: str
    quantity: float
    serial_numbers: Optional[List[str]] = []

class ManualOrderInput(BaseModel):
    document_number: str
    customer_tax_id: str
    customer_name: Optional[str] = None
    address_label: str = "Principal"
    lines: List[ManualOrderLine]

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

class InventorySessionCreate(BaseModel):
    branch_id: str
    sector_id: str
    count_type: str = "HOT"
    assigned_operator: str

class InventoryCountScan(BaseModel):
    sku: str
    quantity: float
    location_code: Optional[str] = None
    lot_number: str = ""

class SpotCheckInput(BaseModel):
    sku: str
    quantity: float
    location_code: Optional[str] = None
    lot_number: str = ""

class CustomerReturnLineInput(BaseModel):
    sku: str
    quantity: float
    condition: str = "OPERATIVO"
    location_code: Optional[str] = None
    serial_numbers: Optional[List[str]] = []

class CustomerReturnCreateInput(BaseModel):
    return_number: str
    customer_id: str
    document_id: str
    branch_id: Optional[str] = None
    sector_id: Optional[str] = None
    lines: List[CustomerReturnLineInput]

# === PUTAWAY & REPLENISHMENT (FASE 2) ===
@router.get("/api/admin/putaway/{sku}")
async def get_putaway_suggestion(sku: str, conn: asyncpg.Connection = Depends(get_db_connection)):
    enabled = await conn.fetchval("SELECT value FROM system_settings WHERE key = 'enable_putaway_suggestions'")
    if enabled != "true":
        return {"suggested_location": "", "type": "DISABLED"}
    
    sku_clean = sku.strip().upper()
    
    fixed = await conn.fetchrow("""
        SELECT l.location_code 
        FROM item_locations il 
        JOIN locations l ON il.location_id = l.id 
        WHERE UPPER(il.item_sku) = $1 LIMIT 1
    """, sku_clean)
    
    if fixed:
        return {"suggested_location": fixed["location_code"], "type": "FIXED_LOCATION"}
        
    current = await conn.fetchrow("""
        SELECT l.location_code 
        FROM stock_inventory si 
        JOIN locations l ON si.location_id = l.id 
        WHERE UPPER(si.sku) = $1 AND si.quantity > 0 LIMIT 1
    """, sku_clean)
    
    if current:
        return {"suggested_location": current["location_code"], "type": "EXISTING_STOCK"}
        
    return {"suggested_location": "", "type": "NONE"}

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

# === DASHBOARD E INTEGRACIONES ===
@router.get("/api/admin/dashboard")
async def get_admin_dashboard_op(admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    pending_orders = await conn.fetch("""
        SELECT d.document_number, COALESCE(e.company_name, 'Consumidor Final') as company_name, d.status 
        FROM documents d 
        LEFT JOIN entities e ON d.customer_id = e.id 
        WHERE d.status IN ('PENDING', 'IN_PROGRESS') 
        ORDER BY d.created_at ASC LIMIT 5
    """)
    
    active_transfers = await conn.fetch("""
        SELECT t.transfer_number, COALESCE(ob.name, 'N/A') as origin_branch, COALESCE(db.name, 'N/A') as destination_branch
        FROM transfer_orders t
        LEFT JOIN branches ob ON t.origin_branch_id = ob.id
        LEFT JOIN branches db ON t.destination_branch_id = db.id
        WHERE t.status IN ('PENDING', 'PENDING_CONTROL', 'IN_PROGRESS')
        ORDER BY t.created_at ASC LIMIT 5
    """)
    
    latest_logs = await conn.fetch("""
        SELECT created_at, username, action 
        FROM audit_logs 
        ORDER BY created_at DESC LIMIT 5
    """)
    
    return {
        "status": "ok",
        "pending_orders": [dict(r) for r in pending_orders],
        "active_transfers": [dict(r) for r in active_transfers],
        "latest_logs": [dict(r) for r in latest_logs]
    }

@router.get("/api/admin/integrations")
async def get_admin_integrations_op(admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    rows = await conn.fetch("""
        SELECT id::text, name, channel_type, target_url, is_active, created_at
        FROM integration_channels
        ORDER BY created_at DESC
    """)
    return [dict(r) for r in rows]

# === STOCK Y KARDEX ===
@router.get("/api/admin/stock")
async def list_admin_stock(admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    rows = await conn.fetch("""
        SELECT si.sku, i.description, b.name as branch_name, sec.name as sector_name, 
               COALESCE(l.location_code, 'Sin ubicación') as location_code, 
               si.quantity::float as quantity, COALESCE(si.condition, 'OPERATIVO') as condition, si.updated_at
        FROM stock_inventory si
        LEFT JOIN items i ON si.sku = i.sku
        LEFT JOIN branches b ON si.branch_id = b.id
        LEFT JOIN sectors sec ON si.sector_id = sec.id
        LEFT JOIN locations l ON si.location_id = l.id
        ORDER BY si.sku ASC
    """)
    return [dict(r) for r in rows]

@router.get("/api/admin/stock/kardex")
async def list_admin_stock_kardex(
    sku: Optional[str] = None,
    branch_id: Optional[str] = None,
    sector_id: Optional[str] = None,
    location_code: Optional[str] = None,
    date_from: Optional[str] = None,
    time_from: Optional[str] = None,
    date_to: Optional[str] = None,
    time_to: Optional[str] = None,
    movement_type: Optional[str] = None,
    admin: dict = Depends(require_admin), 
    conn: asyncpg.Connection = Depends(get_db_connection)
):
    query = """
        SELECT sm.id::text as id, sm.sku, COALESCE(i.description, 'Sin descripción') as description,
               sm.movement_type, sm.quantity::float as quantity, COALESCE(sm.condition, 'OPERATIVO') as condition,
               sm.reference_document, sm.username, sm.created_at,
               b.name as branch_name, sec.name as sector_name, l.location_code
        FROM stock_movements sm
        LEFT JOIN items i ON sm.sku = i.sku
        LEFT JOIN branches b ON sm.branch_id = b.id
        LEFT JOIN sectors sec ON sm.sector_id = sec.id
        LEFT JOIN locations l ON sm.location_id = l.id
        WHERE 1=1
    """
    params = []
    param_idx = 1

    if sku:
        query += f" AND (sm.sku ILIKE ${param_idx} OR i.description ILIKE ${param_idx})"
        params.append(f"%{sku.strip()}%")
        param_idx += 1
    
    if branch_id:
        try:
            b_uuid = uuid.UUID(branch_id)
            query += f" AND sm.branch_id = ${param_idx}"
            params.append(b_uuid)
            param_idx += 1
        except ValueError:
            pass
            
    if sector_id:
        try:
            s_uuid = uuid.UUID(sector_id)
            query += f" AND sm.sector_id = ${param_idx}"
            params.append(s_uuid)
            param_idx += 1
        except ValueError:
            pass
            
    if location_code:
        query += f" AND l.location_code ILIKE ${param_idx}"
        params.append(f"%{location_code.strip()}%")
        param_idx += 1
        
    if movement_type:
        query += f" AND sm.movement_type = ${param_idx}"
        params.append(movement_type)
        param_idx += 1

    if date_from:
        t_from = time_from.strip() if time_from else "00:00"
        if len(t_from) == 5: 
            t_from += ":00"
        try:
            dt_obj = datetime.strptime(f"{date_from.strip()} {t_from}", "%Y-%m-%d %H:%M:%S")
            query += f" AND sm.created_at >= ${param_idx}"
            params.append(dt_obj)
            param_idx += 1
        except ValueError:
            pass

    if date_to:
        t_to = time_to.strip() if time_to else "23:59"
        if len(t_to) == 5: 
            t_to += ":59"
        try:
            dt_obj = datetime.strptime(f"{date_to.strip()} {t_to}", "%Y-%m-%d %H:%M:%S")
            query += f" AND sm.created_at <= ${param_idx}"
            params.append(dt_obj)
            param_idx += 1
        except ValueError:
            pass

    query += " ORDER BY sm.created_at DESC LIMIT 500"
    
    try:
        rows = await conn.fetch(query, *params)
        return [dict(r) for r in rows]
    except Exception as e:
        raise HTTPException(status_code=400, detail="Error al procesar la consulta.")

# === INVENTARIO FÍSICO / CONTEOS ===
@router.get("/api/inventory/sessions")
async def list_inventory_sessions(conn: asyncpg.Connection = Depends(get_db_connection)):
    rows = await conn.fetch("""
        SELECT s.id::text, b.name as branch_name, sec.name as sector_name, 
               s.count_type, s.status, s.created_at, s.created_by, s.assigned_operator 
        FROM inventory_sessions s 
        JOIN branches b ON s.branch_id = b.id 
        JOIN sectors sec ON s.sector_id = sec.id 
        ORDER BY s.created_at DESC
    """)
    return [dict(r) for r in rows]

@router.post("/api/inventory/sessions")
async def create_inventory_session(data: InventorySessionCreate, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    async with conn.transaction():
        active = await conn.fetchval("SELECT id FROM inventory_sessions WHERE sector_id = $1 AND status IN ('OPEN', 'REVIEW')", uuid.UUID(data.sector_id))
        if active:
            raise HTTPException(400, "Ya existe un conteo activo para este sector. Ciérrelo antes de abrir uno nuevo.")

        session_id = await conn.fetchval(
            "INSERT INTO inventory_sessions (branch_id, sector_id, count_type, created_by, assigned_operator) VALUES ($1, $2, $3, $4, $5) RETURNING id",
            uuid.UUID(data.branch_id), uuid.UUID(data.sector_id), data.count_type, admin["username"], data.assigned_operator
        )

        await conn.execute("""
            INSERT INTO inventory_snapshots (session_id, sku, location_id, lot_number, expected_quantity)
            SELECT $1, sku, location_id, lot_number, quantity 
            FROM stock_inventory 
            WHERE sector_id = $2 AND quantity > 0
        """, session_id, uuid.UUID(data.sector_id))

        await log_action(conn, admin["username"], "INVENTORY_STARTED", f"Conteo asignado a {data.assigned_operator} en sector {data.sector_id}.")
        return {"status": "success", "session_id": str(session_id)}

@router.post("/api/inventory/sessions/{session_id}/scan")
async def scan_inventory_count(session_id: str, data: InventoryCountScan, user: dict = Depends(get_current_user), conn: asyncpg.Connection = Depends(get_db_connection)):
    sess = await conn.fetchrow("SELECT id, status, sector_id FROM inventory_sessions WHERE id = $1", uuid.UUID(session_id))
    if not sess or sess["status"] != "OPEN":
        raise HTTPException(400, "La sesión de conteo no existe o no está abierta.")
    
    sku_clean = data.sku.strip().upper()
    loc_id = None
    
    if data.location_code and data.location_code.strip():
        loc = await conn.fetchrow("SELECT id FROM locations WHERE sector_id = $1 AND UPPER(location_code) = $2", sess["sector_id"], data.location_code.strip().upper())
        if not loc:
            raise HTTPException(400, f"Ubicación {data.location_code} no pertenece al sector.")
        loc_id = loc["id"]

    await conn.execute("""
        INSERT INTO inventory_counts (session_id, sku, location_id, lot_number, counted_quantity, scanned_by)
        VALUES ($1, $2, $3, $4, $5, $6)
    """, sess["id"], sku_clean, loc_id, data.lot_number, data.quantity, user["username"])
    
    return {"status": "success", "message": f"Contado {data.quantity} de {sku_clean}"}

@router.post("/api/inventory/sessions/{session_id}/finish")
async def finish_inventory_count(session_id: str, user: dict = Depends(get_current_user), conn: asyncpg.Connection = Depends(get_db_connection)):
    res = await conn.execute("UPDATE inventory_sessions SET status = 'REVIEW' WHERE id = $1 AND status = 'OPEN'", uuid.UUID(session_id))
    if res == "UPDATE 0":
        raise HTTPException(400, "No se pudo finalizar. Sesión inválida o ya cerrada.")
    return {"status": "success", "message": "Conteo enviado a revisión."}

@router.get("/api/inventory/sessions/{session_id}/review")
async def review_inventory_deltas(session_id: str, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    rows = await conn.fetch("""
        WITH snapshot_agg AS (
            SELECT sku, location_id, lot_number, SUM(expected_quantity) as expected
            FROM inventory_snapshots WHERE session_id = $1 GROUP BY sku, location_id, lot_number
        ),
        count_agg AS (
            SELECT sku, location_id, lot_number, SUM(counted_quantity) as counted
            FROM inventory_counts WHERE session_id = $1 GROUP BY sku, location_id, lot_number
        )
        SELECT 
            c.sku,
            c.location_id,
            l.location_code,
            c.lot_number,
            COALESCE(s.expected, 0) as expected_quantity,
            c.counted as counted_quantity,
            (c.counted - COALESCE(s.expected, 0)) as delta
        FROM count_agg c
        LEFT JOIN snapshot_agg s ON c.sku = s.sku 
            AND c.location_id IS NOT DISTINCT FROM s.location_id 
            AND c.lot_number IS NOT DISTINCT FROM s.lot_number
        LEFT JOIN locations l ON c.location_id = l.id
    """, uuid.UUID(session_id))
    return [dict(r) for r in rows]

@router.post("/api/inventory/sessions/{session_id}/apply")
async def apply_inventory_adjustments(session_id: str, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    async with conn.transaction():
        sess = await conn.fetchrow("SELECT id, status, branch_id, sector_id FROM inventory_sessions WHERE id = $1 FOR UPDATE", uuid.UUID(session_id))
        if not sess or sess["status"] != "REVIEW":
            raise HTTPException(400, "La sesión no está en estado de revisión.")

        deltas = await conn.fetch("""
            WITH snapshot_agg AS (
                SELECT sku, location_id, lot_number, SUM(expected_quantity) as expected 
                FROM inventory_snapshots WHERE session_id = $1 GROUP BY sku, location_id, lot_number
            ),
            count_agg AS (
                SELECT sku, location_id, lot_number, SUM(counted_quantity) as counted 
                FROM inventory_counts WHERE session_id = $1 GROUP BY sku, location_id, lot_number
            )
            SELECT 
                c.sku, 
                c.location_id, 
                c.lot_number, 
                (c.counted - COALESCE(s.expected, 0)) as delta
            FROM count_agg c 
            LEFT JOIN snapshot_agg s ON c.sku = s.sku 
                AND c.location_id IS NOT DISTINCT FROM s.location_id 
                AND c.lot_number IS NOT DISTINCT FROM s.lot_number
        """, sess["id"])

        for row in deltas:
            if row["delta"] != 0:
                await record_stock_movement(conn, row["sku"], sess["branch_id"], sess["sector_id"], row["location_id"], float(row["delta"]), 'AJUSTE', f"CONTEO-{session_id}", admin["username"], row["lot_number"])

        await conn.execute("UPDATE inventory_sessions SET status = 'CLOSED', closed_at = CURRENT_TIMESTAMP, closed_by = $1 WHERE id = $2", admin["username"], sess["id"])
        await log_action(conn, admin["username"], "INVENTORY_APPLIED", f"Ajustes de inventario aplicados para sesión {session_id}.")
        return {"status": "success", "message": "Ajustes de inventario aplicados correctamente al stock actual."}

@router.post("/api/inventory/spot-check")
async def spot_check_inventory(data: SpotCheckInput, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    sku_clean = data.sku.strip().upper()
    loc_id = None

    if data.location_code and data.location_code.strip():
        loc = await conn.fetchrow("SELECT id FROM locations WHERE UPPER(location_code) = $1", data.location_code.strip().upper())
        if not loc:
            raise HTTPException(400, f"Ubicación {data.location_code} no encontrada en el sistema.")
        loc_id = loc["id"]

    if loc_id:
        expected = await conn.fetchval("SELECT COALESCE(SUM(quantity), 0) FROM stock_inventory WHERE sku = $1 AND location_id = $2 AND lot_number = $3", sku_clean, loc_id, data.lot_number)
    else:
        expected = await conn.fetchval("SELECT COALESCE(SUM(quantity), 0) FROM stock_inventory WHERE sku = $1 AND location_id IS NULL AND lot_number = $2", sku_clean, data.lot_number)

    expected = float(expected or 0)
    delta = data.quantity - expected
    match = (delta == 0)

    log_detail = f"SKU: {sku_clean} | Ubic: {data.location_code or 'N/A'} | Lote: {data.lot_number or '-'} | Esperado: {expected} | Contado: {data.quantity} | Delta: {delta}"
    await log_action(conn, admin["username"], "SPOT_CHECK_AUDIT", log_detail)

    return {
        "status": "success",
        "expected": expected,
        "counted": data.quantity,
        "delta": delta,
        "match": match
    }

# === PICKING & PACKING ===
@router.get("/api/picking/orders")
async def get_picking_mailbox(user: dict = Depends(get_current_user), conn: asyncpg.Connection = Depends(get_db_connection)):
    rows = await conn.fetch("""
        SELECT d.id, d.document_number, d.status, COALESCE(c.company_name, 'Consumidor Final') as company_name
        FROM documents d 
        LEFT JOIN entities c ON d.customer_id = c.id 
        WHERE d.status IN ('PENDING', 'IN_PROGRESS') 
        ORDER BY d.created_at ASC
    """)
    
    result = []
    for r in rows:
        doc = dict(r)
        stats = await conn.fetchrow("""
            SELECT COUNT(*) as total_items,
                   COALESCE(SUM(quantity_picked), 0)::float as picked_items,
                   COALESCE(SUM(quantity_requested), 0)::float as requested_items
            FROM document_lines WHERE document_id = $1
        """, r["id"])
        
        doc["total_items"] = stats["total_items"]
        doc["picked_items"] = stats["picked_items"]
        doc["requested_items"] = stats["requested_items"]
        result.append(doc)
        
    return result

@router.get("/api/picking/orders/{document_number}")
async def get_picking_order_details(document_number: str, user: dict = Depends(get_current_user), conn: asyncpg.Connection = Depends(get_db_connection)):
    doc = await conn.fetchrow("SELECT id, document_number, status FROM documents WHERE UPPER(document_number) = $1", document_number.strip().upper())
    if not doc: raise HTTPException(404, "Pedido no encontrado")
    
    lines = await conn.fetch("""
        SELECT dl.id::text, dl.sku, dl.quantity_requested::float, dl.quantity_picked::float,
               COALESCE(i.description, dl.sku) as description, dl.serial_numbers
        FROM document_lines dl 
        LEFT JOIN items i ON UPPER(dl.sku) = UPPER(i.sku)
        WHERE dl.document_id = $1 
        ORDER BY dl.sku ASC
    """, doc["id"])
    
    result_lines = []
    for l in lines:
        ldict = dict(l)
        locs = await conn.fetch("""
            SELECT l.location_code, si.quantity::float 
            FROM stock_inventory si 
            JOIN locations l ON si.location_id = l.id 
            WHERE UPPER(si.sku) = $1 AND si.quantity > 0
        """, l["sku"].strip().upper())
        
        if locs:
            ldict["suggested_locations"] = " | ".join([f"{loc['location_code']} ({loc['quantity']})" for loc in locs])
            ldict["sort_key"] = locs[0]['location_code']
        else:
            ldict["suggested_locations"] = "Sin ubicación asignada o stock agotado"
            ldict["sort_key"] = "ZZZZZ"
            
        result_lines.append(ldict)

    # FASE 3: Enrutamiento Óptimo
    routing_enabled = await conn.fetchval("SELECT value FROM system_settings WHERE key = 'enable_optimal_routing'") == "true"
    if routing_enabled:
        result_lines.sort(key=lambda x: (x["sort_key"], x["sku"]))
    
    return {"document": dict(doc), "lines": result_lines}

@router.post("/api/picking/orders/{document_number}/scan")
async def scan_picking_item(document_number: str, data: PickScanInput, user: dict = Depends(get_current_user), conn: asyncpg.Connection = Depends(get_db_connection)):
    async with conn.transaction():
        doc = await conn.fetchrow("SELECT id, status FROM documents WHERE UPPER(document_number) = $1 FOR UPDATE", document_number.strip().upper())
        if not doc:
            raise HTTPException(404, "Pedido no encontrado.")
        if doc["status"] == "COMPLETED": 
            raise HTTPException(400, "El pedido ya se encuentra completado.")
        
        sku_clean = data.sku.strip().upper()
        line = await conn.fetchrow("SELECT id, quantity_requested::float, quantity_picked::float FROM document_lines WHERE document_id = $1 AND UPPER(sku) = $2", doc["id"], sku_clean)
        if not line: 
            raise HTTPException(400, f"El SKU '{sku_clean}' no pertenece a este pedido.")
        
        needed = line["quantity_requested"] - line["quantity_picked"]
        if needed <= 0:
            raise HTTPException(400, f"El SKU '{sku_clean}' ya fue recolectado totalmente.")

        # Validación SNT
        snt_enabled = await conn.fetchval("SELECT value FROM system_settings WHERE key = 'enable_serial_tracking'")
        if snt_enabled == "true" and data.serial_numbers and len(data.serial_numbers) > 0:
            if len(data.serial_numbers) != int(data.quantity):
                raise HTTPException(400, f"La cantidad de números de serie ({len(data.serial_numbers)}) debe coincidir con la cantidad ({data.quantity}).")

        loc_id = None
        branch_id = None
        sector_id = None

        if data.location_code and data.location_code.strip() and data.location_code.strip().upper() not in ["GENERAL", "SIN_UBICACION", "N/A"]:
            loc = await conn.fetchrow("SELECT l.id, l.sector_id, s.branch_id FROM locations l JOIN sectors s ON l.sector_id = s.id WHERE UPPER(l.location_code) = $1", data.location_code.strip().upper())
            if loc:
                loc_id = loc["id"]
                sector_id = loc["sector_id"]
                branch_id = loc["branch_id"]

        if not loc_id:
            stock_entry = await conn.fetchrow("SELECT branch_id, sector_id, location_id FROM stock_inventory WHERE UPPER(sku) = $1 AND quantity > 0 LIMIT 1", sku_clean)
            if stock_entry:
                branch_id = stock_entry["branch_id"]
                sector_id = stock_entry["sector_id"]
                loc_id = stock_entry["location_id"]
            else:
                default_branch = await conn.fetchval("SELECT id FROM branches LIMIT 1")
                default_sector = await conn.fetchval("SELECT id FROM sectors LIMIT 1")
                branch_id = default_branch
                sector_id = default_sector

        allow_neg = await conn.fetchval("SELECT value FROM system_settings WHERE key = 'allow_negative_stock'")
        if allow_neg != "true" and loc_id:
            avail = await conn.fetchval("SELECT COALESCE(SUM(quantity), 0) FROM stock_inventory WHERE branch_id = $1 AND sector_id = $2 AND UPPER(sku) = $3 AND location_id = $4", branch_id, sector_id, sku_clean, loc_id)
            if float(avail or 0) < data.quantity: 
                raise HTTPException(400, f"Stock insuficiente en la ubicación (Disponible: {avail}).")

        await conn.execute("""
            UPDATE document_lines 
            SET quantity_picked = quantity_picked + $1,
                serial_numbers = COALESCE(serial_numbers, '[]'::jsonb) || $2::jsonb 
            WHERE id = $3
        """, data.quantity, json.dumps(data.serial_numbers or []), line["id"])
        
        if branch_id and sector_id:
            await record_stock_movement(conn, sku_clean, branch_id, sector_id, loc_id, -data.quantity, 'OUT_PICKING', document_number.strip().upper(), user.get("username"), serial_numbers=data.serial_numbers)
        
        await conn.execute("UPDATE documents SET status = 'IN_PROGRESS' WHERE id = $1 AND status = 'PENDING'", doc["id"])
        
        pending = await conn.fetchval("SELECT COUNT(*) FROM document_lines WHERE document_id = $1 AND quantity_picked < quantity_requested", doc["id"])
        
        auto_complete = await conn.fetchval("SELECT value FROM system_settings WHERE key = 'auto_complete_picking'") or "true"
        order_completed = (pending == 0)
        if order_completed and auto_complete == "true": 
            await conn.execute("UPDATE documents SET status = 'COMPLETED' WHERE id = $1", doc["id"])
        
        return {
            "status": "success", 
            "message": f"Extraído {data.quantity} un de {sku_clean}", 
            "order_completed": order_completed,
            "remaining_lines": pending
        }

# === FASE 3: PICKING POR OLAS (WAVE PICKING) ===
@router.get("/api/picking/waves/pending")
async def get_wave_picking_pending(limit: int = 5, user: dict = Depends(get_current_user), conn: asyncpg.Connection = Depends(get_db_connection)):
    enabled = await conn.fetchval("SELECT value FROM system_settings WHERE key = 'enable_wave_picking'")
    if enabled != "true": 
        raise HTTPException(400, "Picking por Olas inactivo en la Configuración Enterprise.")
        
    orders = await conn.fetch("SELECT id, document_number FROM documents WHERE status IN ('PENDING', 'IN_PROGRESS') AND document_type = 'PICKING' ORDER BY created_at ASC LIMIT $1", limit)
    if not orders: 
        raise HTTPException(400, "No hay pedidos pendientes para agrupar.")
        
    order_ids = [o["id"] for o in orders]
    order_nums = [o["document_number"] for o in orders]
    
    lines = await conn.fetch("""
        SELECT dl.sku, MAX(i.description) as description, 
               SUM(dl.quantity_requested) as quantity_requested, 
               SUM(dl.quantity_picked) as quantity_picked
        FROM document_lines dl
        LEFT JOIN items i ON UPPER(dl.sku) = UPPER(i.sku)
        WHERE dl.document_id = ANY($1::uuid[])
        GROUP BY dl.sku
    """, order_ids)
    
    routing_enabled = await conn.fetchval("SELECT value FROM system_settings WHERE key = 'enable_optimal_routing'") == "true"
    
    result_lines = []
    for l in lines:
        ldict = dict(l)
        locs = await conn.fetch("SELECT l.location_code, si.quantity::float FROM stock_inventory si JOIN locations l ON si.location_id = l.id WHERE UPPER(si.sku) = $1 AND si.quantity > 0", l["sku"].strip().upper())
        
        if locs:
            ldict["suggested_locations"] = " | ".join([f"{loc['location_code']} ({loc['quantity']})" for loc in locs])
            ldict["sort_key"] = locs[0]["location_code"]
        else:
            ldict["suggested_locations"] = "Sin ubicación asignada o stock agotado"
            ldict["sort_key"] = "ZZZZZ"
            
        result_lines.append(ldict)
        
    if routing_enabled:
        result_lines.sort(key=lambda x: (x["sort_key"], x["sku"]))
        
    return {"order_numbers": order_nums, "lines": result_lines}

@router.post("/api/picking/waves/scan")
async def scan_wave_picking_item(data: WavePickScanInput, user: dict = Depends(get_current_user), conn: asyncpg.Connection = Depends(get_db_connection)):
    async with conn.transaction():
        sku_clean = data.sku.strip().upper()
        qty_to_distribute = float(data.quantity)
        
        snt_enabled = await conn.fetchval("SELECT value FROM system_settings WHERE key = 'enable_serial_tracking'")
        if snt_enabled == "true" and data.serial_numbers and len(data.serial_numbers) > 0:
            if len(data.serial_numbers) != int(data.quantity):
                raise HTTPException(400, "Descuadre en trazabilidad. La cantidad de números de serie debe coincidir con la cantidad física extraída.")
        
        docs = await conn.fetch("SELECT id, document_number, status FROM documents WHERE document_number = ANY($1::text[]) FOR UPDATE", data.order_numbers)
        if not docs: 
            raise HTTPException(404, "Pedidos de la ola no encontrados.")
        
        total_needed = 0
        lines_to_update = []
        
        for doc in docs:
            line = await conn.fetchrow("SELECT id, quantity_requested, quantity_picked FROM document_lines WHERE document_id = $1 AND UPPER(sku) = $2", doc["id"], sku_clean)
            if line:
                needed = float(line["quantity_requested"]) - float(line["quantity_picked"])
                if needed > 0:
                    lines_to_update.append({"line_id": line["id"], "doc_id": doc["id"], "doc_number": doc["document_number"], "needed": needed})
                    total_needed += needed
        
        if qty_to_distribute > total_needed:
            raise HTTPException(400, f"La cantidad escaneada supera lo solicitado en esta Ola. Restante requerido: {total_needed}")
            
        serial_idx = 0
        serials_list = data.serial_numbers or []
        
        for lu in lines_to_update:
            if qty_to_distribute <= 0: break
            
            apply_qty = min(lu["needed"], qty_to_distribute)
            apply_serials = serials_list[serial_idx : serial_idx + int(apply_qty)]
            serial_idx += int(apply_qty)
            
            await conn.execute("""
                UPDATE document_lines 
                SET quantity_picked = quantity_picked + $1,
                    serial_numbers = COALESCE(serial_numbers, '[]'::jsonb) || $2::jsonb
                WHERE id = $3
            """, apply_qty, json.dumps(apply_serials), lu["line_id"])
            
            qty_to_distribute -= apply_qty
            await conn.execute("UPDATE documents SET status = 'IN_PROGRESS' WHERE id = $1 AND status = 'PENDING'", lu["doc_id"])
        
        loc_id = None
        branch_id = None
        sector_id = None
        
        if data.location_code and data.location_code.strip() and data.location_code.strip().upper() not in ["GENERAL", "SIN_UBICACION", "N/A"]:
            loc = await conn.fetchrow("SELECT l.id, l.sector_id, s.branch_id FROM locations l JOIN sectors s ON l.sector_id = s.id WHERE UPPER(l.location_code) = $1", data.location_code.strip().upper())
            if loc:
                loc_id = loc["id"]
                sector_id = loc["sector_id"]
                branch_id = loc["branch_id"]

        if not loc_id:
            stock_entry = await conn.fetchrow("SELECT branch_id, sector_id, location_id FROM stock_inventory WHERE UPPER(sku) = $1 AND quantity > 0 LIMIT 1", sku_clean)
            if stock_entry:
                branch_id = stock_entry["branch_id"]
                sector_id = stock_entry["sector_id"]
                loc_id = stock_entry["location_id"]
            else:
                default_branch = await conn.fetchval("SELECT id FROM branches LIMIT 1")
                default_sector = await conn.fetchval("SELECT id FROM sectors LIMIT 1")
                branch_id = default_branch
                sector_id = default_sector

        allow_neg = await conn.fetchval("SELECT value FROM system_settings WHERE key = 'allow_negative_stock'")
        if allow_neg != "true" and loc_id:
            avail = await conn.fetchval("SELECT COALESCE(SUM(quantity), 0) FROM stock_inventory WHERE branch_id = $1 AND sector_id = $2 AND UPPER(sku) = $3 AND location_id = $4", branch_id, sector_id, sku_clean, loc_id)
            if float(avail or 0) < data.quantity: 
                raise HTTPException(400, f"Stock insuficiente en la ubicación (Disponible: {avail}).")

        if branch_id and sector_id:
            await record_stock_movement(conn, sku_clean, branch_id, sector_id, loc_id, -data.quantity, 'OUT_PICKING', "WAVE-PICK", user.get("username"), serial_numbers=data.serial_numbers)

        auto_complete = await conn.fetchval("SELECT value FROM system_settings WHERE key = 'auto_complete_picking'") or "true"
        wave_completed = True
        
        for doc in docs:
            pending = await conn.fetchval("SELECT COUNT(*) FROM document_lines WHERE document_id = $1 AND quantity_picked < quantity_requested", doc["id"])
            if pending == 0 and auto_complete == "true":
                await conn.execute("UPDATE documents SET status = 'COMPLETED' WHERE id = $1", doc["id"])
            if pending > 0:
                wave_completed = False
        
        return {
            "status": "success", 
            "message": f"Consolidado {data.quantity} un de {sku_clean} en Ola.", 
            "wave_completed": wave_completed
        }

@router.get("/api/packing/orders")
async def get_packing_orders(user: dict = Depends(get_current_user), conn: asyncpg.Connection = Depends(get_db_connection)):
    return [dict(r) for r in await conn.fetch("SELECT d.document_number, d.status, COALESCE(c.company_name, 'Sin Cliente') as company_name FROM documents d LEFT JOIN entities c ON d.customer_id = c.id WHERE d.status = 'COMPLETED' ORDER BY d.created_at ASC")]

@router.get("/api/packing/orders/{document_number}")
async def get_packing_order_details(document_number: str, user: dict = Depends(get_current_user), conn: asyncpg.Connection = Depends(get_db_connection)):
    doc = await conn.fetchrow("SELECT d.id, d.document_number, COALESCE(c.company_name, 'Sin cliente') as company_name, COALESCE(a.full_address, 'Sin dirección') as address FROM documents d LEFT JOIN entities c ON d.customer_id = c.id LEFT JOIN entity_addresses a ON d.customer_address_id = a.id WHERE UPPER(d.document_number) = $1", document_number.strip().upper())
    if not doc: raise HTTPException(404, "Pedido no encontrado")
    
    totals = await conn.fetchrow("""
        SELECT COALESCE(SUM(dl.quantity_requested * COALESCE(i.weight, 0)), 0)::float as calc_weight, 
               COALESCE(SUM(dl.quantity_requested * COALESCE(i.volume, 0)), 0)::float as calc_volume 
        FROM document_lines dl JOIN items i ON UPPER(dl.sku) = UPPER(i.sku) WHERE dl.document_id = $1
    """, doc["id"])
    return {"document": dict(doc), "totals": dict(totals)}

@router.post("/api/packing/orders/{document_number}/pack")
async def pack_order_and_dispatch(document_number: str, data: PackOrderInput, user: dict = Depends(get_current_user), conn: asyncpg.Connection = Depends(get_db_connection)):
    async with conn.transaction():
        doc = await conn.fetchrow("SELECT d.id, d.status, d.document_number, d.channel_origin, COALESCE(c.company_name, 'Consumidor Final') as client_name, COALESCE(a.full_address, 'A coordinar') as delivery_address FROM documents d LEFT JOIN entities c ON d.customer_id = c.id LEFT JOIN entity_addresses a ON d.customer_address_id = a.id WHERE UPPER(d.document_number) = $1 FOR UPDATE", document_number.strip().upper())
        if not doc: raise HTTPException(404, "Pedido no encontrado.")
        if doc["status"] == "DISPATCHED": raise HTTPException(400, "El pedido ya fue despachado.")
        if doc["status"] != "COMPLETED": raise HTTPException(400, "El pedido aún no está pickeado completamente.")

        totals = await conn.fetchrow("""
            SELECT COALESCE(SUM(dl.quantity_requested * COALESCE(i.weight, 0)), 0)::float as calc_weight, 
                   COALESCE(SUM(dl.quantity_requested * COALESCE(i.volume, 0)), 0)::float as calc_volume 
            FROM document_lines dl JOIN items i ON UPPER(dl.sku) = UPPER(i.sku) WHERE dl.document_id = $1
        """, doc["id"])

        await conn.execute("UPDATE documents SET status = 'DISPATCHED' WHERE id = $1", doc["id"])
        await log_action(conn, user.get("username"), "PACKING_DISPATCH", f"Empacó y despachó {document_number} ({data.boxes} bultos)")

        template = await conn.fetchval("SELECT value FROM system_settings WHERE key = 'zpl_order_template'")
        if template:
            zpl = template.replace("{order_number}", doc["document_number"])\
                          .replace("{client_name}", doc["client_name"])\
                          .replace("{delivery_address}", doc["delivery_address"])
            default_queue = await conn.fetchval("SELECT print_queue_code FROM sectors WHERE uses_locations = FALSE LIMIT 1") or "PRINT-SEC-01"
            await queue_zpl_print_job(conn, default_queue, zpl)

        dispatch_payload = {
            "event": "order.dispatched",
            "order_number": doc["document_number"],
            "channel_origin": doc["channel_origin"],
            "client_name": doc["client_name"],
            "delivery_address": doc["delivery_address"],
            "boxes": data.boxes,
            "total_weight_kg": float(totals["calc_weight"]),
            "total_volume_m3": float(totals["calc_volume"]),
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        await dispatch_event_to_channels(conn, "OUTBOUND_DESPACHO", dispatch_payload)
        return {"status": "success", "message": "Pedido despachado e impreso exitosamente."}

# === RECEPCIÓN DE REMITOS Y COMPRAS ===
@router.get("/api/reception/remitos")
@router.get("/api/reception/orders")
async def get_reception_remitos(user: dict = Depends(get_current_user), conn: asyncpg.Connection = Depends(get_db_connection)):
    rows = await conn.fetch("""
        SELECT pr.id::text as id, pr.remito_number, pr.status, pr.created_at, 
               COALESCE(e.company_name, 'Sin Proveedor') as supplier_name,
               COALESCE(b.name, 'Sucursal') as branch_name, COALESCE(sec.name, 'Sector') as sector_name
        FROM purchase_remitos pr
        LEFT JOIN entities e ON pr.supplier_id = e.id
        LEFT JOIN branches b ON pr.branch_id = b.id
        LEFT JOIN sectors sec ON pr.sector_id = sec.id
        WHERE pr.status IN ('PENDING', 'PENDING_CONTROL', 'IN_PROGRESS')
        ORDER BY pr.created_at ASC
    """)
    return [dict(r) for r in rows]

@router.get("/api/reception/remitos/{remito_number}")
@router.get("/api/reception/orders/{remito_number}")
async def get_reception_remito_details(remito_number: str, user: dict = Depends(get_current_user), conn: asyncpg.Connection = Depends(get_db_connection)):
    rem = await conn.fetchrow("""
        SELECT pr.id, pr.remito_number, pr.status, pr.branch_id, pr.sector_id, COALESCE(e.company_name, 'Sin Proveedor') as supplier_name 
        FROM purchase_remitos pr LEFT JOIN entities e ON pr.supplier_id = e.id WHERE UPPER(pr.remito_number) = $1
    """, remito_number.strip().upper())
    if not rem: raise HTTPException(404, "Remito no encontrado")
    lines = await conn.fetch("""
        SELECT prl.id::text as id, prl.sku, prl.quantity_sent::float as quantity_sent, prl.quantity_received::float as quantity_received, l.location_code, prl.serial_numbers
        FROM purchase_remito_lines prl LEFT JOIN locations l ON prl.location_id = l.id WHERE prl.purchase_remito_id = $1 ORDER BY prl.sku ASC
    """, rem["id"])
    return {"remito": dict(rem), "lines": [dict(l) for l in lines]}

@router.post("/api/reception/remitos/{remito_number}/scan")
@router.post("/api/reception/orders/{remito_number}/scan")
async def scan_reception_item(remito_number: str, data: MobileRemitoScanInput, user: dict = Depends(get_current_user), conn: asyncpg.Connection = Depends(get_db_connection)):
    async with conn.transaction():
        rem = await conn.fetchrow("SELECT id, status, branch_id, sector_id FROM purchase_remitos WHERE UPPER(remito_number) = $1 FOR UPDATE", remito_number.strip().upper())
        if not rem: raise HTTPException(404, "Remito no encontrado")
        if rem["status"] == "COMPLETED": raise HTTPException(400, "Remito ya controlado completamente.")
        sku_clean = data.sku.strip().upper()
        line = await conn.fetchrow("SELECT id, quantity_sent, quantity_received FROM purchase_remito_lines WHERE purchase_remito_id = $1 AND UPPER(sku) = $2", rem["id"], sku_clean)
        if not line: raise HTTPException(400, "SKU no pertenece al remito.")
        
        loc_id = None
        if data.location_code and data.location_code.strip():
            loc = await conn.fetchrow("SELECT id FROM locations WHERE UPPER(location_code) = $1", data.location_code.strip().upper())
            if loc: loc_id = loc["id"]

        await conn.execute("""
            UPDATE purchase_remito_lines 
            SET quantity_received = quantity_received + $1,
                serial_numbers = COALESCE(serial_numbers, '[]'::jsonb) || $2::jsonb 
            WHERE id = $3
        """, data.quantity, json.dumps(data.serial_numbers or []), line["id"])
        
        await record_stock_movement(conn, sku_clean, rem["branch_id"], rem["sector_id"], loc_id, data.quantity, 'IN_RECEPTION', remito_number.strip().upper(), user.get("username"), serial_numbers=data.serial_numbers)
        await conn.execute("UPDATE purchase_remitos SET status = 'IN_PROGRESS' WHERE id = $1 AND status IN ('PENDING', 'PENDING_CONTROL')", rem["id"])
        
        pending = await conn.fetchval("SELECT COUNT(*) FROM purchase_remito_lines WHERE purchase_remito_id = $1 AND quantity_received < quantity_sent", rem["id"])
        if pending == 0: await conn.execute("UPDATE purchase_remitos SET status = 'COMPLETED' WHERE id = $1", rem["id"])
        
        return {"status": "success", "message": f"Ingresado {data.quantity} un de {sku_clean}", "remito_completed": pending == 0}

# === DEVOLUCIONES DE CLIENTES (RMA) ===
@router.get("/api/admin/returns/next-number")
async def get_next_return_number(user: dict = Depends(get_current_user), conn: asyncpg.Connection = Depends(get_db_connection)):
    prefix = await conn.fetchval("SELECT value FROM system_settings WHERE key = 'return_number_prefix'") or "DEV-"
    val = await conn.fetchval("SELECT return_number FROM customer_returns ORDER BY created_at DESC LIMIT 1")
    if val:
        digits = re.findall(r'\d+', val)
        if digits:
            next_num = f"{prefix}{int(digits[-1]) + 1:06d}"
        else:
            next_num = f"{prefix}000001"
    else:
        next_num = f"{prefix}000001"
    return {"next_number": next_num}

@router.get("/api/admin/returns/customers")
async def get_return_customers(user: dict = Depends(get_current_user), conn: asyncpg.Connection = Depends(get_db_connection)):
    rows = await conn.fetch("""
        SELECT DISTINCT e.id::text, e.tax_id, e.company_name
        FROM entities e
        JOIN documents d ON d.customer_id = e.id
        WHERE e.is_customer = TRUE AND d.status IN ('COMPLETED', 'DISPATCHED')
        ORDER BY e.company_name ASC
    """)
    return [dict(r) for r in rows]

@router.get("/api/admin/returns/orders-by-customer/{customer_id}")
async def get_return_orders_by_customer(customer_id: str, user: dict = Depends(get_current_user), conn: asyncpg.Connection = Depends(get_db_connection)):
    rows = await conn.fetch("""
        SELECT d.id::text, d.document_number, d.created_at, d.status
        FROM documents d
        WHERE d.customer_id = $1 AND d.status IN ('COMPLETED', 'DISPATCHED')
        ORDER BY d.created_at DESC
    """, uuid.UUID(customer_id))
    return [dict(r) for r in rows]

@router.get("/api/admin/returns/order-details/{document_id}")
async def get_return_order_details(document_id: str, user: dict = Depends(get_current_user), conn: asyncpg.Connection = Depends(get_db_connection)):
    doc = await conn.fetchrow("SELECT id, document_number, customer_id FROM documents WHERE id = $1", uuid.UUID(document_id))
    if not doc: raise HTTPException(404, "Pedido no encontrado")
    
    lines = await conn.fetch("""
        SELECT dl.id::text, dl.sku, COALESCE(i.description, dl.sku) as description, 
               dl.quantity_requested::float, dl.quantity_picked::float, dl.serial_numbers
        FROM document_lines dl
        LEFT JOIN items i ON UPPER(dl.sku) = UPPER(i.sku)
        WHERE dl.document_id = $1
        ORDER BY dl.sku ASC
    """, doc["id"])
    return {"document": dict(doc), "lines": [dict(l) for l in lines]}

@router.post("/api/admin/returns")
async def create_customer_return(
    data: CustomerReturnCreateInput, 
    x_idempotency_key: Optional[str] = Header(None),
    user: dict = Depends(get_current_user), 
    conn: asyncpg.Connection = Depends(get_db_connection)
):
    cached_resp = await check_idempotency(conn, x_idempotency_key, "/api/admin/returns")
    if cached_resp:
        return cached_resp[0]

    async with conn.transaction():
        ret_num = data.return_number.strip().upper()
        existing = await conn.fetchval("SELECT id FROM customer_returns WHERE UPPER(return_number) = $1", ret_num)
        if existing:
            raise HTTPException(400, "El número de devolución ya fue registrado.")

        target_branch_id = None
        target_sector_id = None

        if user.get("branch_id"):
            try: target_branch_id = uuid.UUID(user["branch_id"])
            except Exception: pass
        if user.get("sector_id"):
            try: target_sector_id = uuid.UUID(user["sector_id"])
            except Exception: pass

        if not target_branch_id and data.branch_id:
            try: target_branch_id = uuid.UUID(data.branch_id)
            except Exception: pass
        if not target_sector_id and data.sector_id:
            try: target_sector_id = uuid.UUID(data.sector_id)
            except Exception: pass

        if not target_branch_id:
            target_branch_id = await conn.fetchval("SELECT id FROM branches LIMIT 1")
        if not target_sector_id:
            target_sector_id = await conn.fetchval("SELECT id FROM sectors LIMIT 1")

        if not target_branch_id or not target_sector_id:
            raise HTTPException(400, "Debe configurar al menos una Sucursal y Sector de destino.")

        return_id = await conn.fetchval("""
            INSERT INTO customer_returns (return_number, customer_id, document_id, branch_id, sector_id, created_by)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING id
        """, ret_num, uuid.UUID(data.customer_id), uuid.UUID(data.document_id), target_branch_id, target_sector_id, user["username"])

        items_processed = 0
        for line in data.lines:
            if line.quantity > 0:
                sku_clean = line.sku.strip().upper()
                cond_clean = line.condition.strip().upper() if line.condition else "OPERATIVO"
                
                loc_id = None
                if line.location_code and line.location_code.strip():
                    loc = await conn.fetchrow("SELECT id FROM locations WHERE sector_id = $1 AND UPPER(location_code) = $2", target_sector_id, line.location_code.strip().upper())
                    if loc: loc_id = loc["id"]

                await conn.execute("""
                    INSERT INTO customer_return_lines (return_id, sku, quantity, condition, location_id, serial_numbers)
                    VALUES ($1, $2, $3, $4, $5, $6::jsonb)
                """, return_id, sku_clean, line.quantity, cond_clean, loc_id, json.dumps(line.serial_numbers or []))

                await record_stock_movement(
                    conn, sku_clean, target_branch_id, target_sector_id, loc_id, 
                    line.quantity, 'IN_RETURN', ret_num, user["username"], condition=cond_clean,
                    serial_numbers=line.serial_numbers
                )
                items_processed += 1

        if items_processed == 0:
            raise HTTPException(400, "Debe ingresar una cantidad mayor a 0 para al menos un artículo devuelto.")

        await log_action(conn, user["username"], "RETURN_CREATED", f"Devolución {ret_num} procesada para el pedido {data.document_id}.")
        res_data = {"status": "success", "message": f"Devolución {ret_num} registrada exitosamente."}
        
        await save_idempotency(conn, x_idempotency_key, "/api/admin/returns", res_data)
        return res_data

@router.get("/api/admin/returns")
async def list_customer_returns(search: str = "", limit: int = 50, user: dict = Depends(get_current_user), conn: asyncpg.Connection = Depends(get_db_connection)):
    rows = await conn.fetch("""
        SELECT cr.id::text as id, cr.return_number, cr.created_at, cr.created_by,
               COALESCE(e.company_name, 'Cliente') as customer_name,
               COALESCE(d.document_number, 'N/A') as document_number,
               b.name as branch_name, sec.name as sector_name
        FROM customer_returns cr
        LEFT JOIN entities e ON cr.customer_id = e.id
        LEFT JOIN documents d ON cr.document_id = d.id
        LEFT JOIN branches b ON cr.branch_id = b.id
        LEFT JOIN sectors sec ON cr.sector_id = sec.id
        WHERE cr.return_number ILIKE $1 OR e.company_name ILIKE $1 OR d.document_number ILIKE $1
        ORDER BY cr.created_at DESC LIMIT $2
    """, f"%{search}%", limit)
    return [dict(r) for r in rows]

# === TRASPASOS ===
@router.get("/api/admin/transfer-orders/next-number")
async def get_next_transfer_number(admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    val = await conn.fetchval("""
        SELECT transfer_number FROM transfer_orders 
        ORDER BY created_at DESC LIMIT 1
    """)
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
                if loc: 
                    orig_loc_id = loc["id"]
                else:
                    raise HTTPException(400, f"Ubicación Origen '{line.origin_location_code}' no existe en el sector.")

            dest_loc_id = None
            if line.destination_location_code and line.destination_location_code.strip():
                loc = await conn.fetchrow("SELECT id FROM locations WHERE sector_id = $1 AND UPPER(location_code) = $2", uuid.UUID(data.destination_sector_id), line.destination_location_code.strip().upper())
                if loc: 
                    dest_loc_id = loc["id"]
                else:
                    raise HTTPException(400, f"Ubicación Destino '{line.destination_location_code}' no existe en el sector.")

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

# === CONSULTAS LISTADOS ADMIN ===
@router.get("/api/admin/purchase-orders")
async def list_admin_purchase_orders(search: str = "", limit: int = 50, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    rows = await conn.fetch("SELECT po.id::text as id, po.order_number, po.status, po.created_at, COALESCE(e.company_name, 'Sin Proveedor') as supplier_name FROM purchase_orders po LEFT JOIN entities e ON po.supplier_id = e.id WHERE po.order_number ILIKE $1 ORDER BY po.created_at DESC LIMIT $2", f"%{search}%", limit)
    return [dict(r) for r in rows]

@router.get("/api/admin/purchase-remitos")
async def list_admin_purchase_remitos(search: str = "", limit: int = 50, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    rows = await conn.fetch("SELECT pr.id::text as id, pr.remito_number, pr.status, pr.created_at, COALESCE(e.company_name, 'Sin Proveedor') as supplier_name, b.name as branch_name, sec.name as sector_name FROM purchase_remitos pr LEFT JOIN entities e ON pr.supplier_id = e.id LEFT JOIN branches b ON pr.branch_id = b.id LEFT JOIN sectors sec ON pr.sector_id = sec.id WHERE pr.remito_number ILIKE $1 ORDER BY pr.created_at DESC LIMIT $2", f"%{search}%", limit)
    return [dict(r) for r in rows]

@router.get("/api/admin/purchase-invoices")
async def list_admin_purchase_invoices(search: str = "", limit: int = 50, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    rows = await conn.fetch("SELECT pi.id::text as id, pi.invoice_number, pi.invoice_type, pi.created_at, COALESCE(e.company_name, 'Sin Proveedor') as supplier_name FROM purchase_invoices pi LEFT JOIN entities e ON pi.supplier_id = e.id WHERE pi.invoice_number ILIKE $1 ORDER BY pi.created_at DESC LIMIT $2", f"%{search}%", limit)
    return [dict(r) for r in rows]

@router.get("/api/admin/transfer-orders")
async def list_admin_transfer_orders(search: str = "", limit: int = 50, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    rows = await conn.fetch("SELECT t.id::text as id, t.transfer_number, t.status, t.created_at, COALESCE(ob.name, 'N/A') as origin_branch, COALESCE(db.name, 'N/A') as destination_branch, COALESCE(os.name, 'N/A') as origin_sector, COALESCE(ds.name, 'N/A') as destination_sector FROM transfer_orders t LEFT JOIN branches ob ON t.origin_branch_id = ob.id LEFT JOIN branches db ON t.destination_branch_id = db.id LEFT JOIN sectors os ON t.origin_sector_id = os.id LEFT JOIN sectors ds ON t.destination_sector_id = ds.id WHERE t.transfer_number ILIKE $1 ORDER BY t.created_at DESC LIMIT $2", f"%{search}%", limit)
    return [dict(r) for r in rows]

@router.get("/api/admin/logs")
async def list_admin_logs(admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    rows = await conn.fetch("SELECT created_at, username, action, details FROM audit_logs ORDER BY created_at DESC LIMIT 100")
    return [dict(r) for r in rows]

@router.get("/api/admin/documents")
async def list_admin_documents(admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    rows = await conn.fetch("""
        SELECT d.document_number, COALESCE(e.company_name, 'Consumidor Final') as company_name, 
               d.status, 
               COALESCE((SELECT SUM(quantity_picked) * 100.0 / NULLIF(SUM(quantity_requested), 0) 
                         FROM document_lines WHERE document_id = d.id), 0)::int as progress_pct
        FROM documents d 
        LEFT JOIN entities e ON d.customer_id = e.id 
        ORDER BY d.created_at DESC LIMIT 100
    """)
    return [dict(r) for r in rows]

@router.get("/api/admin/sales-orders/next-number")
async def get_next_order_number(admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    val = await conn.fetchval("SELECT document_number FROM documents WHERE document_number ~ '^[0-9]+$' ORDER BY document_number::bigint DESC LIMIT 1")
    next_num = str(int(val) + 1).zfill(6) if val else "000001"
    return {"next_number": next_num}

@router.post("/api/admin/sales-orders")
async def create_manual_sales_order(
    data: ManualOrderInput, 
    x_idempotency_key: Optional[str] = Header(None),
    admin: dict = Depends(require_admin), 
    conn: asyncpg.Connection = Depends(get_db_connection)
):
    cached_resp = await check_idempotency(conn, x_idempotency_key, "/api/admin/sales-orders")
    if cached_resp:
        return cached_resp[0]

    async with conn.transaction():
        ent = await conn.fetchrow("SELECT id FROM entities WHERE tax_id = $1", data.customer_tax_id)
        ent_id = ent["id"] if ent else None
        
        doc_id = await conn.fetchval(
            "INSERT INTO documents (document_number, customer_id, status, channel_origin) VALUES ($1, $2, 'PENDING', 'MANUAL') RETURNING id",
            data.document_number, ent_id
        )
        
        for line in data.lines:
            await conn.execute(
                "INSERT INTO document_lines (document_id, sku, quantity_requested, quantity_picked, serial_numbers) VALUES ($1, $2, $3, 0, $4::jsonb)",
                doc_id, line.sku.strip().upper(), line.quantity, json.dumps(line.serial_numbers or [])
            )
        
        await log_action(conn, admin.get("username"), "ORDER_CREATED", f"Pedido manual {data.document_number} creado.")
        res_data = {"status": "success", "message": "Pedido creado correctamente."}
        
        await save_idempotency(conn, x_idempotency_key, "/api/admin/sales-orders", res_data)
        return res_data

@router.post("/api/admin/sales-orders/{document_number}/print-label")
async def reprint_order_label(document_number: str, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    doc = await conn.fetchrow("SELECT d.document_number, COALESCE(c.company_name, 'Consumidor Final') as client_name, COALESCE(a.full_address, 'A coordinar') as delivery_address FROM documents d LEFT JOIN entities c ON d.customer_id = c.id LEFT JOIN entity_addresses a ON d.customer_address_id = a.id WHERE d.document_number = $1", document_number.strip().upper())
    if not doc: raise HTTPException(404, "Pedido no encontrado.")
    template = await conn.fetchval("SELECT value FROM system_settings WHERE key = 'zpl_order_template'")
    if template:
        zpl = template.replace("{order_number}", doc["document_number"]).replace("{client_name}", doc["client_name"]).replace("{delivery_address}", doc["delivery_address"])
        default_queue = await conn.fetchval("SELECT print_queue_code FROM sectors WHERE uses_locations = FALSE LIMIT 1") or "PRINT-SEC-01"
        await queue_zpl_print_job(conn, default_queue, zpl)
        return {"status": "success", "message": "Etiqueta re-enviada a impresión."}
    raise HTTPException(400, "Plantilla ZPL no configurada.")