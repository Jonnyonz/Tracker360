from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timezone
import asyncpg, uuid

try:
    from backend.database import (
        get_db_connection, get_current_user, require_admin,
        record_stock_movement, log_action, dispatch_event_to_channels, queue_zpl_print_job
    )
except ImportError:
    from database import (
        get_db_connection, get_current_user, require_admin,
        record_stock_movement, log_action, dispatch_event_to_channels, queue_zpl_print_job
    )

router = APIRouter(tags=["Operations & Logistics"])

class PickScanInput(BaseModel):
    sku: str
    quantity: float
    location_code: str

class PackOrderInput(BaseModel):
    boxes: int

class MobileRemitoScanInput(BaseModel):
    remito_number: str
    sku: str
    quantity: float
    location_code: Optional[str] = None

class MobileTransferScanInput(BaseModel):
    transfer_number: str
    sku: str
    quantity: float
    destination_location_code: Optional[str] = None

class ManualOrderLine(BaseModel):
    sku: str
    quantity: float

class ManualOrderInput(BaseModel):
    document_number: str
    customer_tax_id: str
    customer_name: Optional[str] = None
    address_label: str = "Principal"
    lines: List[ManualOrderLine]

# Modelos del Motor de Inventario Cíclico
class InventorySessionCreate(BaseModel):
    branch_id: str
    sector_id: str
    count_type: str = "HOT"
    assigned_operator: str  # NUEVO CAMPO

class InventoryCountScan(BaseModel):
    sku: str
    quantity: float
    location_code: Optional[str] = None
    lot_number: str = ""

# Modelos para Auditoría Rápida (Spot Check)
class SpotCheckInput(BaseModel):
    sku: str
    quantity: float
    location_code: Optional[str] = None
    lot_number: str = ""

# === STOCK Y KARDEX ===
@router.get("/api/admin/stock")
async def list_admin_stock(admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    rows = await conn.fetch("""
        SELECT si.sku, i.description, b.name as branch_name, sec.name as sector_name, 
               COALESCE(l.location_code, 'Sin ubicación') as location_code, 
               si.quantity::float as quantity, si.updated_at
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
               sm.movement_type, sm.quantity::float as quantity, 
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
        print(f"Error procesando Kardex: {e}")
        raise HTTPException(status_code=400, detail="Error al procesar la consulta. Verifique los filtros aplicados.")

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
        # Validar si ya hay un conteo abierto para ese sector
        active = await conn.fetchval("SELECT id FROM inventory_sessions WHERE sector_id = $1 AND status IN ('OPEN', 'REVIEW')", uuid.UUID(data.sector_id))
        if active:
            raise HTTPException(400, "Ya existe un conteo activo para este sector. Ciérrelo antes de abrir uno nuevo.")

        # Crear Sesión
        session_id = await conn.fetchval(
            "INSERT INTO inventory_sessions (branch_id, sector_id, count_type, created_by, assigned_operator) VALUES ($1, $2, $3, $4, $5) RETURNING id",
            uuid.UUID(data.branch_id), uuid.UUID(data.sector_id), data.count_type, admin["username"], data.assigned_operator
        )

        # Tomar la 'FOTO' del sector (Snapshot ultra liviano)
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
    # El operario avisa que terminó de escanear. Pasa a revisión del Admin.
    res = await conn.execute("UPDATE inventory_sessions SET status = 'REVIEW' WHERE id = $1 AND status = 'OPEN'", uuid.UUID(session_id))
    if res == "UPDATE 0":
        raise HTTPException(400, "No se pudo finalizar. Sesión inválida o ya cerrada.")
    return {"status": "success", "message": "Conteo enviado a revisión."}

@router.get("/api/inventory/sessions/{session_id}/review")
async def review_inventory_deltas(session_id: str, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    # Motor de Deltas: Cruza la Foto (Snapshot) con los Conteos agrupados. Costo de procesamiento: casi 0.
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
            COALESCE(s.sku, c.sku) as sku,
            COALESCE(s.location_id, c.location_id) as location_id,
            l.location_code,
            COALESCE(s.lot_number, c.lot_number) as lot_number,
            COALESCE(s.expected, 0) as expected_quantity,
            COALESCE(c.counted, 0) as counted_quantity,
            (COALESCE(c.counted, 0) - COALESCE(s.expected, 0)) as delta
        FROM snapshot_agg s
        FULL OUTER JOIN count_agg c ON s.sku = c.sku AND s.location_id IS NOT DISTINCT FROM c.location_id AND s.lot_number IS NOT DISTINCT FROM c.lot_number
        LEFT JOIN locations l ON COALESCE(s.location_id, c.location_id) = l.id
    """, uuid.UUID(session_id))
    return [dict(r) for r in rows]

@router.post("/api/inventory/sessions/{session_id}/apply")
async def apply_inventory_adjustments(session_id: str, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    async with conn.transaction():
        sess = await conn.fetchrow("SELECT id, status, branch_id, sector_id FROM inventory_sessions WHERE id = $1 FOR UPDATE", uuid.UUID(session_id))
        if not sess or sess["status"] != "REVIEW":
            raise HTTPException(400, "La sesión no está en estado de revisión.")

        # Traer los deltas calculados
        deltas = await conn.fetch("""
            WITH snapshot_agg AS (
                SELECT sku, location_id, lot_number, SUM(expected_quantity) as expected FROM inventory_snapshots WHERE session_id = $1 GROUP BY sku, location_id, lot_number
            ),
            count_agg AS (
                SELECT sku, location_id, lot_number, SUM(counted_quantity) as counted FROM inventory_counts WHERE session_id = $1 GROUP BY sku, location_id, lot_number
            )
            SELECT COALESCE(s.sku, c.sku) as sku, COALESCE(s.location_id, c.location_id) as location_id, COALESCE(s.lot_number, c.lot_number) as lot_number, (COALESCE(c.counted, 0) - COALESCE(s.expected, 0)) as delta
            FROM snapshot_agg s FULL OUTER JOIN count_agg c ON s.sku = c.sku AND s.location_id IS NOT DISTINCT FROM c.location_id AND s.lot_number IS NOT DISTINCT FROM c.lot_number
        """, sess["id"])

        # Aplicar el ajuste usando la lógica matemática de sumarle el Delta al stock actual.
        for row in deltas:
            if row["delta"] != 0:
                await record_stock_movement(conn, row["sku"], sess["branch_id"], sess["sector_id"], row["location_id"], float(row["delta"]), 'AJUSTE', f"CONTEO-{session_id}", admin["username"], row["lot_number"])

        # Cerrar Sesión
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
    return [dict(r) for r in await conn.fetch("SELECT d.document_number, d.status, COALESCE(c.company_name, 'Sin cliente') as company_name FROM documents d LEFT JOIN entities c ON d.customer_id = c.id WHERE d.status IN ('PENDING', 'IN_PROGRESS') ORDER BY d.created_at ASC")]

@router.get("/api/picking/orders/{document_number}")
async def get_picking_order_details(document_number: str, user: dict = Depends(get_current_user), conn: asyncpg.Connection = Depends(get_db_connection)):
    doc = await conn.fetchrow("SELECT id, document_number, status FROM documents WHERE document_number = $1", document_number.strip().upper())
    if not doc: raise HTTPException(404, "Pedido no encontrado")
    lines = await conn.fetch("SELECT dl.id, dl.sku, dl.quantity_requested, dl.quantity_picked, COALESCE((SELECT string_agg(l.location_code || ' (' || si.quantity || ')', ' | ') FROM stock_inventory si JOIN locations l ON si.location_id = l.id WHERE si.sku = dl.sku AND si.quantity > 0), 'Sin stock') as suggested_locations FROM document_lines dl WHERE dl.document_id = $1 ORDER BY dl.sku ASC", doc["id"])
    return {"document": dict(doc), "lines": [dict(l) for l in lines]}

@router.post("/api/picking/orders/{document_number}/scan")
async def scan_picking_item(document_number: str, data: PickScanInput, user: dict = Depends(get_current_user), conn: asyncpg.Connection = Depends(get_db_connection)):
    async with conn.transaction():
        doc = await conn.fetchrow("SELECT id, status FROM documents WHERE document_number = $1 FOR UPDATE", document_number.strip().upper())
        if doc["status"] == "COMPLETED": raise HTTPException(400, "Ya completado.")
        sku_clean = data.sku.strip().upper()
        line = await conn.fetchrow("SELECT id, quantity_requested, quantity_picked FROM document_lines WHERE document_id = $1 AND sku = $2", doc["id"], sku_clean)
        if not line: raise HTTPException(400, "SKU no pertenece al pedido.")
        loc = await conn.fetchrow("SELECT l.id, l.sector_id, s.branch_id FROM locations l JOIN sectors s ON l.sector_id = s.id WHERE l.location_code = $1", data.location_code.strip().upper())
        avail = await conn.fetchval("SELECT COALESCE(SUM(quantity), 0) FROM stock_inventory WHERE branch_id = $1 AND sector_id = $2 AND sku = $3 AND location_id = $4", loc["branch_id"], loc["sector_id"], sku_clean, loc["id"])
        if avail < data.quantity: raise HTTPException(400, "Stock insuficiente en ubicación.")
        await conn.execute("UPDATE document_lines SET quantity_picked = quantity_picked + $1 WHERE id = $2", data.quantity, line["id"])
        await record_stock_movement(conn, sku_clean, loc["branch_id"], loc["sector_id"], loc["id"], -data.quantity, 'OUT_PICKING', document_number.strip().upper(), user.get("username"))
        await conn.execute("UPDATE documents SET status = 'IN_PROGRESS' WHERE id = $1 AND status = 'PENDING'", doc["id"])
        pending = await conn.fetchval("SELECT COUNT(*) FROM document_lines WHERE document_id = $1 AND quantity_picked < quantity_requested", doc["id"])
        if pending == 0: await conn.execute("UPDATE documents SET status = 'COMPLETED' WHERE id = $1", doc["id"])
        return {"status": "success", "message": f"Extraído {data.quantity} un de {sku_clean}", "order_completed": pending == 0}

@router.get("/api/packing/orders")
async def get_packing_orders(user: dict = Depends(get_current_user), conn: asyncpg.Connection = Depends(get_db_connection)):
    return [dict(r) for r in await conn.fetch("SELECT d.document_number, d.status, COALESCE(c.company_name, 'Sin Cliente') as company_name FROM documents d LEFT JOIN entities c ON d.customer_id = c.id WHERE d.status = 'COMPLETED' ORDER BY d.created_at ASC")]

@router.get("/api/packing/orders/{document_number}")
async def get_packing_order_details(document_number: str, user: dict = Depends(get_current_user), conn: asyncpg.Connection = Depends(get_db_connection)):
    doc = await conn.fetchrow("SELECT d.id, d.document_number, COALESCE(c.company_name, 'Sin cliente') as company_name, COALESCE(a.full_address, 'Sin dirección') as address FROM documents d LEFT JOIN entities c ON d.customer_id = c.id LEFT JOIN entity_addresses a ON d.customer_address_id = a.id WHERE d.document_number = $1", document_number.strip().upper())
    if not doc: raise HTTPException(404, "Pedido no encontrado")
    
    totals = await conn.fetchrow("""
        SELECT COALESCE(SUM(dl.quantity_requested * COALESCE(i.weight, 0)), 0) as calc_weight, 
               COALESCE(SUM(dl.quantity_requested * COALESCE(i.volume, 0)), 0) as calc_volume 
        FROM document_lines dl JOIN items i ON dl.sku = i.sku WHERE dl.document_id = $1
    """, doc["id"])
    return {"document": dict(doc), "totals": dict(totals)}

@router.post("/api/packing/orders/{document_number}/pack")
async def pack_order_and_dispatch(document_number: str, data: PackOrderInput, user: dict = Depends(get_current_user), conn: asyncpg.Connection = Depends(get_db_connection)):
    async with conn.transaction():
        doc = await conn.fetchrow("SELECT d.id, d.status, d.document_number, d.channel_origin, COALESCE(c.company_name, 'Consumidor Final') as client_name, COALESCE(a.full_address, 'A coordinar') as delivery_address FROM documents d LEFT JOIN entities c ON d.customer_id = c.id LEFT JOIN entity_addresses a ON d.customer_address_id = a.id WHERE d.document_number = $1 FOR UPDATE", document_number.strip().upper())
        if not doc: raise HTTPException(404, "Pedido no encontrado.")
        if doc["status"] == "DISPATCHED": raise HTTPException(400, "El pedido ya fue despachado.")
        if doc["status"] != "COMPLETED": raise HTTPException(400, "El pedido aún no está pickeado completamente.")

        totals = await conn.fetchrow("""
            SELECT COALESCE(SUM(dl.quantity_requested * COALESCE(i.weight, 0)), 0) as calc_weight, 
                   COALESCE(SUM(dl.quantity_requested * COALESCE(i.volume, 0)), 0) as calc_volume 
            FROM document_lines dl JOIN items i ON dl.sku = i.sku WHERE dl.document_id = $1
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
        SELECT prl.id::text as id, prl.sku, prl.quantity_sent::float as quantity_sent, prl.quantity_received::float as quantity_received, l.location_code
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

        await conn.execute("UPDATE purchase_remito_lines SET quantity_received = quantity_received + $1 WHERE id = $2", data.quantity, line["id"])
        await record_stock_movement(conn, sku_clean, rem["branch_id"], rem["sector_id"], loc_id, data.quantity, 'IN_RECEPTION', remito_number.strip().upper(), user.get("username"))
        await conn.execute("UPDATE purchase_remitos SET status = 'IN_PROGRESS' WHERE id = $1 AND status IN ('PENDING', 'PENDING_CONTROL')", rem["id"])
        
        pending = await conn.fetchval("SELECT COUNT(*) FROM purchase_remito_lines WHERE purchase_remito_id = $1 AND quantity_received < quantity_sent", rem["id"])
        if pending == 0: await conn.execute("UPDATE purchase_remitos SET status = 'COMPLETED' WHERE id = $1", rem["id"])
        
        return {"status": "success", "message": f"Ingresado {data.quantity} un de {sku_clean}", "remito_completed": pending == 0}

# === TRASPASOS ===
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
               ol.location_code as origin_location, dl.location_code as destination_location
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

        await conn.execute("UPDATE transfer_order_lines SET quantity_received = quantity_received + $1 WHERE id = $2", data.quantity, line["id"])
        await record_stock_movement(conn, sku_clean, tr["origin_branch_id"], tr["origin_sector_id"], line["origin_location_id"], -data.quantity, 'TRANSFER_OUT', transfer_number.strip().upper(), user.get("username"))
        await record_stock_movement(conn, sku_clean, tr["destination_branch_id"], tr["destination_sector_id"], dest_loc_id, data.quantity, 'TRANSFER_IN', transfer_number.strip().upper(), user.get("username"))

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

@router.get("/api/admin/integrations")
async def get_admin_integrations_op(admin: dict = Depends(require_admin)):
    return []

@router.get("/api/admin/dashboard")
async def get_admin_dashboard_op(admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    # 1. Pedidos Pendientes (Top 5)
    pending_orders = await conn.fetch("""
        SELECT d.document_number, COALESCE(e.company_name, 'Consumidor Final') as company_name, d.status 
        FROM documents d 
        LEFT JOIN entities e ON d.customer_id = e.id 
        WHERE d.status IN ('PENDING', 'IN_PROGRESS') 
        ORDER BY d.created_at ASC LIMIT 5
    """)
    
    # 2. Traspasos Activos (Top 5)
    active_transfers = await conn.fetch("""
        SELECT t.transfer_number, COALESCE(ob.name, 'N/A') as origin_branch, COALESCE(db.name, 'N/A') as destination_branch
        FROM transfer_orders t
        LEFT JOIN branches ob ON t.origin_branch_id = ob.id
        LEFT JOIN branches db ON t.destination_branch_id = db.id
        WHERE t.status IN ('PENDING', 'PENDING_CONTROL', 'IN_PROGRESS')
        ORDER BY t.created_at ASC LIMIT 5
    """)
    
    # 3. Auditoría Reciente (Top 5)
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

# === MÓDULOS DE PEDIDOS Y AUDITORÍA ===
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
async def create_manual_sales_order(data: ManualOrderInput, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    async with conn.transaction():
        ent = await conn.fetchrow("SELECT id FROM entities WHERE tax_id = $1", data.customer_tax_id)
        ent_id = ent["id"] if ent else None
        
        doc_id = await conn.fetchval(
            "INSERT INTO documents (document_number, customer_id, status, channel_origin) VALUES ($1, $2, 'PENDING', 'MANUAL') RETURNING id",
            data.document_number, ent_id
        )
        
        for line in data.lines:
            await conn.execute(
                "INSERT INTO document_lines (document_id, sku, quantity_requested, quantity_picked) VALUES ($1, $2, $3, 0)",
                doc_id, line.sku.strip().upper(), line.quantity
            )
        
        await log_action(conn, admin.get("username"), "ORDER_CREATED", f"Pedido manual {data.document_number} creado.")
        return {"status": "success", "message": "Pedido creado correctamente."}

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