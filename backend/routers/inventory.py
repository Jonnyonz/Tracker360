from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
import asyncpg, uuid

try:
    from backend.database import get_db_connection, get_current_user, require_admin, log_action, record_stock_movement
except ImportError:
    from database import get_db_connection, get_current_user, require_admin, log_action, record_stock_movement

router = APIRouter(tags=["Inventory Control"])

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
    sku: Optional[str] = None, branch_id: Optional[str] = None, sector_id: Optional[str] = None,
    location_code: Optional[str] = None, date_from: Optional[str] = None, time_from: Optional[str] = None,
    date_to: Optional[str] = None, time_to: Optional[str] = None, movement_type: Optional[str] = None,
    admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)
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
            params.append(uuid.UUID(branch_id))
            query += f" AND sm.branch_id = ${param_idx}"
            param_idx += 1
        except ValueError: pass
            
    if sector_id:
        try:
            params.append(uuid.UUID(sector_id))
            query += f" AND sm.sector_id = ${param_idx}"
            param_idx += 1
        except ValueError: pass
            
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
        if len(t_from) == 5: t_from += ":00"
        try:
            dt_obj = datetime.strptime(f"{date_from.strip()} {t_from}", "%Y-%m-%d %H:%M:%S")
            query += f" AND sm.created_at >= ${param_idx}"
            params.append(dt_obj)
            param_idx += 1
        except ValueError: pass

    if date_to:
        t_to = time_to.strip() if time_to else "23:59"
        if len(t_to) == 5: t_to += ":59"
        try:
            dt_obj = datetime.strptime(f"{date_to.strip()} {t_to}", "%Y-%m-%d %H:%M:%S")
            query += f" AND sm.created_at <= ${param_idx}"
            params.append(dt_obj)
            param_idx += 1
        except ValueError: pass

    query += " ORDER BY sm.created_at DESC LIMIT 500"
    
    try:
        rows = await conn.fetch(query, *params)
        return [dict(r) for r in rows]
    except Exception as e:
        raise HTTPException(status_code=400, detail="Error al procesar la consulta.")

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