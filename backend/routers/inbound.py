from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel
from typing import Optional, List
import asyncpg, uuid, re, json

try:
    from backend.database import get_db_connection, get_current_user, require_admin, require_supervisor, record_stock_movement, log_action, check_idempotency, save_idempotency, require_valid_quantity, build_full_address
except ImportError:
    from database import get_db_connection, get_current_user, require_admin, require_supervisor, record_stock_movement, log_action, check_idempotency, save_idempotency, require_valid_quantity, build_full_address

router = APIRouter(tags=["Inbound & Receptions"])

class MobileRemitoScanInput(BaseModel):
    remito_number: str
    sku: str
    quantity: float
    location_code: Optional[str] = None
    serial_numbers: Optional[List[str]] = []

class PurchaseOrderLineInput(BaseModel):
    sku: str
    quantity: float

class PurchaseOrderCreateInput(BaseModel):
    order_number: str
    supplier_id: str
    branch_id: str
    lines: List[PurchaseOrderLineInput]

class RemitoItemInput(BaseModel):
    sku: str
    quantity: float
    location_code: Optional[str] = None
    lot_number: str = ""

class RemitoOrderLineInput(BaseModel):
    purchase_order_line_id: str
    quantity: float
    location_code: Optional[str] = None
    lot_number: str = ""

class PurchaseRemitoCreateInput(BaseModel):
    remito_number: str
    supplier_id: str
    branch_id: str
    sector_id: str
    items: List[RemitoItemInput] = []             # seccion "Articulos": sueltos, sin OC
    order_lines: List[RemitoOrderLineInput] = []  # seccion "Ordenes de compra"

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

@router.get("/api/admin/purchase-orders")
async def list_admin_purchase_orders(search: str = "", limit: int = 50, admin: dict = Depends(require_supervisor), conn: asyncpg.Connection = Depends(get_db_connection)):
    rows = await conn.fetch("SELECT po.id::text as id, po.order_number, po.status, po.created_at, COALESCE(e.company_name, 'Sin Proveedor') as supplier_name FROM purchase_orders po LEFT JOIN entities e ON po.supplier_id = e.id WHERE po.order_number ILIKE $1 ORDER BY po.created_at DESC LIMIT $2", f"%{search}%", limit)
    return [dict(r) for r in rows]

def _parse_uuid(value: str, message: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except ValueError:
        raise HTTPException(400, message)

@router.get("/api/admin/purchase-orders/next-number")
async def get_next_purchase_order_number(admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    val = await conn.fetchval("SELECT order_number FROM purchase_orders ORDER BY created_at DESC LIMIT 1")
    digits = re.findall(r'\d+', val or "")
    return {"next_number": f"OC-{(int(digits[-1]) + 1) if digits else 1:06d}"}

@router.post("/api/admin/purchase-orders")
async def create_purchase_order(data: PurchaseOrderCreateInput, x_idempotency_key: Optional[str] = Header(None), admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    cached_resp = await check_idempotency(conn, x_idempotency_key, "/api/admin/purchase-orders")
    if cached_resp:
        return cached_resp[0]

    number = data.order_number.strip().upper()
    if not number: raise HTTPException(400, "El número de orden es obligatorio.")
    supplier_id = _parse_uuid(data.supplier_id, "Proveedor inválido.")
    branch_id = _parse_uuid(data.branch_id, "Sucursal inválida.")
    if not data.lines: raise HTTPException(400, "La orden debe tener al menos un artículo.")

    async with conn.transaction():
        if await conn.fetchval("SELECT 1 FROM purchase_orders WHERE UPPER(order_number) = $1", number):
            raise HTTPException(400, "El número de orden ya existe.")
        if not await conn.fetchval("SELECT 1 FROM entities WHERE id = $1 AND is_supplier = TRUE", supplier_id):
            raise HTTPException(400, "El proveedor no existe o no está marcado como proveedor.")
        if not await conn.fetchval("SELECT 1 FROM branches WHERE id = $1", branch_id):
            raise HTTPException(400, "La sucursal de recepción no existe.")

        vistos = set()
        for line in data.lines:
            sku = line.sku.strip().upper()
            require_valid_quantity(line.quantity)
            if sku in vistos: raise HTTPException(400, f"El SKU '{sku}' está repetido en la orden.")
            vistos.add(sku)
            if not await conn.fetchval("SELECT 1 FROM items WHERE UPPER(sku) = $1", sku):
                raise HTTPException(400, f"El SKU '{sku}' no existe en el maestro de artículos.")

        po_id = await conn.fetchval(
            "INSERT INTO purchase_orders (order_number, supplier_id, branch_id, status, created_by) VALUES ($1, $2, $3, 'PENDING', $4) RETURNING id",
            number, supplier_id, branch_id, admin["username"])
        for line in data.lines:
            await conn.execute("INSERT INTO purchase_order_lines (purchase_order_id, sku, quantity_ordered, quantity_received) VALUES ($1, $2, $3, 0)",
                               po_id, line.sku.strip().upper(), line.quantity)

        await log_action(conn, admin["username"], "PURCHASE_ORDER_CREATED", f"Orden de compra {number} creada.")
        res_data = {"status": "success", "message": "Orden de compra registrada correctamente.", "order_number": number}
        await save_idempotency(conn, x_idempotency_key, "/api/admin/purchase-orders", res_data)
        return res_data

# OC con algo pendiente de un proveedor: la seccion "Ordenes de compra" del remito.
@router.get("/api/admin/purchase-orders/pending")
async def list_pending_purchase_orders(supplier_id: str, admin: dict = Depends(require_supervisor), conn: asyncpg.Connection = Depends(get_db_connection)):
    sup = _parse_uuid(supplier_id, "Proveedor inválido.")
    rows = await conn.fetch("""
        SELECT po.order_number, po.created_at, po.status, pol.id::text AS line_id, pol.sku, COALESCE(i.description, pol.sku) AS description,
               pol.quantity_ordered::float AS ordered, pol.quantity_received::float AS received,
               (pol.quantity_ordered - pol.quantity_received)::float AS pending
        FROM purchase_orders po
        JOIN purchase_order_lines pol ON pol.purchase_order_id = po.id
        LEFT JOIN items i ON UPPER(i.sku) = UPPER(pol.sku)
        WHERE po.supplier_id = $1 AND po.status IN ('PENDING', 'IN_PROGRESS') AND pol.quantity_received < pol.quantity_ordered
        ORDER BY po.created_at ASC, pol.sku ASC
    """, sup)
    orders = {}
    for r in rows:
        o = orders.setdefault(r["order_number"], {"order_number": r["order_number"], "created_at": r["created_at"], "status": r["status"], "lines": []})
        o["lines"].append({k: r[k] for k in ("line_id", "sku", "description", "ordered", "received", "pending")})
    return list(orders.values())

@router.get("/api/admin/purchase-orders/{order_number}")
async def get_purchase_order(order_number: str, admin: dict = Depends(require_supervisor), conn: asyncpg.Connection = Depends(get_db_connection)):
    po = await conn.fetchrow("""
        SELECT po.id, po.order_number, po.status, po.created_at, po.created_by,
               COALESCE(e.company_name, 'Sin Proveedor') AS supplier_name, COALESCE(e.tax_id, '') AS supplier_tax_id,
               b.code AS branch_code, b.name AS branch_name, b.street, b.number, b.zip_code, b.city
        FROM purchase_orders po LEFT JOIN entities e ON po.supplier_id = e.id LEFT JOIN branches b ON po.branch_id = b.id
        WHERE UPPER(po.order_number) = $1
    """, order_number.strip().upper())
    if not po: raise HTTPException(404, "Orden de compra no encontrada.")
    lines = await conn.fetch("""
        SELECT pol.sku, COALESCE(i.description, pol.sku) AS description, pol.quantity_ordered::float AS ordered,
               pol.quantity_received::float AS received, GREATEST(pol.quantity_ordered - pol.quantity_received, 0)::float AS pending
        FROM purchase_order_lines pol LEFT JOIN items i ON UPPER(i.sku) = UPPER(pol.sku)
        WHERE pol.purchase_order_id = $1 ORDER BY pol.sku ASC
    """, po["id"])
    campos = (po["street"], po["number"], po["zip_code"], po["city"])
    header = {k: po[k] for k in ("order_number", "status", "created_at", "created_by", "supplier_name", "supplier_tax_id", "branch_code", "branch_name")}
    header["delivery_address"] = build_full_address(*campos) if any(c and c.strip() for c in campos) else ""
    return {"order": header, "lines": [dict(l) for l in lines]}

@router.get("/api/admin/purchase-remitos")
async def list_admin_purchase_remitos(search: str = "", limit: int = 50, admin: dict = Depends(require_supervisor), conn: asyncpg.Connection = Depends(get_db_connection)):
    rows = await conn.fetch("SELECT pr.id::text as id, pr.remito_number, pr.status, pr.created_at, COALESCE(e.company_name, 'Sin Proveedor') as supplier_name, b.name as branch_name, sec.name as sector_name FROM purchase_remitos pr LEFT JOIN entities e ON pr.supplier_id = e.id LEFT JOIN branches b ON pr.branch_id = b.id LEFT JOIN sectors sec ON pr.sector_id = sec.id WHERE pr.remito_number ILIKE $1 ORDER BY pr.created_at DESC LIMIT $2", f"%{search}%", limit)
    return [dict(r) for r in rows]

async def _sector_location(conn: asyncpg.Connection, sector_id: uuid.UUID, code: Optional[str]):
    if not code or not code.strip(): return None
    loc = await conn.fetchval("SELECT id FROM locations WHERE sector_id = $1 AND UPPER(location_code) = $2", sector_id, code.strip().upper())
    if not loc: raise HTTPException(400, f"La ubicación '{code.strip().upper()}' no existe en el sector de ingreso.")
    return loc

# Registrar el remito lo cierra: lo tomado de cada OC se suma a lo recibido de la OC en ese momento.
# El stock no se mueve aca: entra con el control del deposito (escaneo de recepcion).
@router.post("/api/admin/purchase-remitos")
async def create_purchase_remito(data: PurchaseRemitoCreateInput, x_idempotency_key: Optional[str] = Header(None), admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    cached_resp = await check_idempotency(conn, x_idempotency_key, "/api/admin/purchase-remitos")
    if cached_resp:
        return cached_resp[0]

    number = data.remito_number.strip().upper()
    if not number: raise HTTPException(400, "El número de remito es obligatorio.")
    supplier_id = _parse_uuid(data.supplier_id, "Proveedor inválido.")
    branch_id = _parse_uuid(data.branch_id, "Sucursal inválida.")
    sector_id = _parse_uuid(data.sector_id, "Sector inválido.")
    if not data.items and not data.order_lines: raise HTTPException(400, "El remito debe tener al menos un artículo.")

    async with conn.transaction():
        if await conn.fetchval("SELECT 1 FROM purchase_remitos WHERE UPPER(remito_number) = $1", number):
            raise HTTPException(400, "El número de remito ya existe.")
        if not await conn.fetchval("SELECT 1 FROM entities WHERE id = $1 AND is_supplier = TRUE", supplier_id):
            raise HTTPException(400, "El proveedor no existe o no está marcado como proveedor.")
        if not await conn.fetchval("SELECT 1 FROM sectors WHERE id = $1 AND branch_id = $2", sector_id, branch_id):
            raise HTTPException(400, "El sector de ingreso no pertenece a la sucursal elegida.")

        filas = []  # (sku, cantidad, ubicacion, lote, linea de OC)
        for it in data.items:
            sku = it.sku.strip().upper()
            require_valid_quantity(it.quantity)
            if not await conn.fetchval("SELECT 1 FROM items WHERE UPPER(sku) = $1", sku):
                raise HTTPException(400, f"El SKU '{sku}' no existe en el maestro de artículos.")
            filas.append((sku, it.quantity, await _sector_location(conn, sector_id, it.location_code), it.lot_number.strip(), None))

        vistas = set()
        ordenes = set()
        for ol in data.order_lines:
            line_id = _parse_uuid(ol.purchase_order_line_id, "Línea de orden de compra inválida.")
            require_valid_quantity(ol.quantity)
            if line_id in vistas: raise HTTPException(400, "Una línea de orden de compra está repetida en el remito.")
            vistas.add(line_id)
            pol = await conn.fetchrow("""
                SELECT pol.id, UPPER(pol.sku) AS sku, pol.quantity_ordered::float AS ordered, pol.quantity_received::float AS received,
                       po.id AS po_id, po.order_number, po.supplier_id, po.status
                FROM purchase_order_lines pol JOIN purchase_orders po ON po.id = pol.purchase_order_id
                WHERE pol.id = $1 FOR UPDATE OF pol, po
            """, line_id)
            if not pol: raise HTTPException(400, "Línea de orden de compra inexistente.")
            if pol["supplier_id"] != supplier_id: raise HTTPException(400, f"La orden {pol['order_number']} es de otro proveedor.")
            if pol["status"] not in ("PENDING", "IN_PROGRESS"): raise HTTPException(400, f"La orden {pol['order_number']} no está pendiente.")
            pendiente = pol["ordered"] - pol["received"]
            if ol.quantity > pendiente:
                raise HTTPException(400, f"La orden {pol['order_number']} solo tiene {pendiente:g} pendientes de {pol['sku']}: el excedente se carga en Artículos.")
            ordenes.add(pol["po_id"])
            filas.append((pol["sku"], ol.quantity, await _sector_location(conn, sector_id, ol.location_code), ol.lot_number.strip(), line_id))

        remito_id = await conn.fetchval(
            "INSERT INTO purchase_remitos (remito_number, supplier_id, branch_id, sector_id, status, created_by) VALUES ($1, $2, $3, $4, 'PENDING_CONTROL', $5) RETURNING id",
            number, supplier_id, branch_id, sector_id, admin["username"])
        for sku, qty, loc_id, lot, line_id in filas:
            await conn.execute("INSERT INTO purchase_remito_lines (purchase_remito_id, sku, quantity_sent, quantity_received, location_id, lot_number, purchase_order_line_id) VALUES ($1, $2, $3, 0, $4, $5, $6)",
                               remito_id, sku, qty, loc_id, lot, line_id)
            if line_id:
                await conn.execute("UPDATE purchase_order_lines SET quantity_received = quantity_received + $1 WHERE id = $2", qty, line_id)
        for po_id in ordenes:
            abiertas = await conn.fetchval("SELECT COUNT(*) FROM purchase_order_lines WHERE purchase_order_id = $1 AND quantity_received < quantity_ordered", po_id)
            await conn.execute("UPDATE purchase_orders SET status = $1 WHERE id = $2", "COMPLETED" if abiertas == 0 else "IN_PROGRESS", po_id)

        await log_action(conn, admin["username"], "PURCHASE_REMITO_CREATED", f"Remito de compra {number} registrado ({len(filas)} líneas, {len(ordenes)} OC).")
        res_data = {"status": "success", "message": "Remito registrado.", "remito_number": number}
        await save_idempotency(conn, x_idempotency_key, "/api/admin/purchase-remitos", res_data)
        return res_data

@router.get("/api/admin/purchase-remitos/{remito_number}")
async def get_purchase_remito(remito_number: str, admin: dict = Depends(require_supervisor), conn: asyncpg.Connection = Depends(get_db_connection)):
    rem = await conn.fetchrow("""
        SELECT pr.id, pr.remito_number, pr.status, pr.created_at, pr.created_by,
               COALESCE(e.company_name, 'Sin Proveedor') AS supplier_name, COALESCE(e.tax_id, '') AS supplier_tax_id,
               b.name AS branch_name, sec.name AS sector_name
        FROM purchase_remitos pr LEFT JOIN entities e ON pr.supplier_id = e.id
        LEFT JOIN branches b ON pr.branch_id = b.id LEFT JOIN sectors sec ON pr.sector_id = sec.id
        WHERE UPPER(pr.remito_number) = $1
    """, remito_number.strip().upper())
    if not rem: raise HTTPException(404, "Remito no encontrado.")
    lines = await conn.fetch("""
        SELECT prl.sku, COALESCE(i.description, prl.sku) AS description, prl.quantity_sent::float AS sent,
               prl.quantity_received::float AS controlled, l.location_code, COALESCE(prl.lot_number, '') AS lot_number,
               po.order_number
        FROM purchase_remito_lines prl
        LEFT JOIN items i ON UPPER(i.sku) = UPPER(prl.sku)
        LEFT JOIN locations l ON prl.location_id = l.id
        LEFT JOIN purchase_order_lines pol ON prl.purchase_order_line_id = pol.id
        LEFT JOIN purchase_orders po ON pol.purchase_order_id = po.id
        WHERE prl.purchase_remito_id = $1 ORDER BY po.order_number NULLS FIRST, prl.sku
    """, rem["id"])
    header = {k: rem[k] for k in ("remito_number", "status", "created_at", "created_by", "supplier_name", "supplier_tax_id", "branch_name", "sector_name")}
    return {"remito": header, "lines": [dict(l) for l in lines]}

@router.get("/api/admin/purchase-invoices")
async def list_admin_purchase_invoices(search: str = "", limit: int = 50, admin: dict = Depends(require_supervisor), conn: asyncpg.Connection = Depends(get_db_connection)):
    rows = await conn.fetch("SELECT pi.id::text as id, pi.invoice_number, pi.invoice_type, pi.created_at, COALESCE(e.company_name, 'Sin Proveedor') as supplier_name FROM purchase_invoices pi LEFT JOIN entities e ON pi.supplier_id = e.id WHERE pi.invoice_number ILIKE $1 ORDER BY pi.created_at DESC LIMIT $2", f"%{search}%", limit)
    return [dict(r) for r in rows]

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
        require_valid_quantity(data.quantity)
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

@router.get("/api/admin/returns/next-number")
async def get_next_return_number(user: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
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
async def get_return_customers(user: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    rows = await conn.fetch("""
        SELECT DISTINCT e.id::text, e.tax_id, e.company_name
        FROM entities e
        JOIN documents d ON d.customer_id = e.id
        WHERE e.is_customer = TRUE AND d.status IN ('COMPLETED', 'DISPATCHED')
        ORDER BY e.company_name ASC
    """)
    return [dict(r) for r in rows]

@router.get("/api/admin/returns/orders-by-customer/{customer_id}")
async def get_return_orders_by_customer(customer_id: str, user: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    rows = await conn.fetch("""
        SELECT d.id::text, d.document_number, d.created_at, d.status
        FROM documents d
        WHERE d.customer_id = $1 AND d.status IN ('COMPLETED', 'DISPATCHED')
        ORDER BY d.created_at DESC
    """, uuid.UUID(customer_id))
    return [dict(r) for r in rows]

@router.get("/api/admin/returns/order-details/{document_id}")
async def get_return_order_details(document_id: str, user: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
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
    user: dict = Depends(require_admin), 
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
            # Una linea en 0 se sigue salteando (articulo no devuelto), como antes.
            require_valid_quantity(line.quantity, allow_zero=True)
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
async def list_customer_returns(search: str = "", limit: int = 50, user: dict = Depends(require_supervisor), conn: asyncpg.Connection = Depends(get_db_connection)):
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