from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel
from typing import Optional, List
import asyncpg, uuid, re, json

from backend.database import get_db_connection, get_current_user, require_admin, require_supervisor, record_stock_movement, log_action, check_idempotency, save_idempotency, require_valid_quantity, build_full_address, add_system_note, parse_uuid, numero_correlativo, siguiente_numero

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
    order_number: Optional[str] = None  # se ignora: el numero lo asigna el sistema
    supplier_id: str
    branch_id: str
    lines: List[PurchaseOrderLineInput]

class UnexpectedItemInput(BaseModel):
    sku: str
    quantity: float
    location_code: Optional[str] = None
    lot_number: str = ""

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
    return_number: Optional[str] = None  # se ignora: el numero lo asigna el sistema
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
    return {"next_number": await siguiente_numero(conn, "OC")}

@router.post("/api/admin/purchase-orders")
async def create_purchase_order(data: PurchaseOrderCreateInput, x_idempotency_key: Optional[str] = Header(None), admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    cached_resp = await check_idempotency(conn, x_idempotency_key, "/api/admin/purchase-orders")
    if cached_resp:
        return cached_resp[0]

    supplier_id = _parse_uuid(data.supplier_id, "Proveedor inválido.")
    branch_id = _parse_uuid(data.branch_id, "Sucursal inválida.")
    if not data.lines: raise HTTPException(400, "La orden debe tener al menos un artículo.")

    async with conn.transaction():
        number, aviso = await numero_correlativo(conn, "OC", data.order_number)
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
        res_data = {"status": "success", "message": f"Orden de compra {number} registrada correctamente." + (f" {aviso}" if aviso else ""),
                    "order_number": number}
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

# Un remito se identifica por su id interno. El numero solo alcanza si no se repite entre proveedores.
async def _resolve_remito_id(conn: asyncpg.Connection, ref: str) -> uuid.UUID:
    ref = ref.strip()
    try:
        rid = uuid.UUID(ref)
    except ValueError:
        rid = None
    if rid:
        if not await conn.fetchval("SELECT 1 FROM purchase_remitos WHERE id = $1", rid): raise HTTPException(404, "Remito no encontrado.")
        return rid
    rows = await conn.fetch("SELECT id FROM purchase_remitos WHERE UPPER(remito_number) = $1", ref.upper())
    if not rows: raise HTTPException(404, "Remito no encontrado.")
    if len(rows) > 1: raise HTTPException(409, "Hay más de un remito con ese número (de distintos proveedores): elegilo desde la lista.")
    return rows[0]["id"]

async def _sector_location(conn: asyncpg.Connection, sector_id: uuid.UUID, code: Optional[str]):
    if not code or not code.strip(): return None
    loc = await conn.fetchval("SELECT id FROM locations WHERE sector_id = $1 AND UPPER(location_code) = $2", sector_id, code.strip().upper())
    if not loc: raise HTTPException(400, f"La ubicación '{code.strip().upper()}' no existe en el sector de ingreso.")
    return loc

# Registrar el remito lo cierra: lo tomado de cada OC se suma a lo recibido de la OC en ese momento.
# Stock: con "segundo control de stock en remitos" (require_mobile_reception) entra con el control del
# deposito (escaneo de recepcion); sin el, entra aca mismo. El modo se fija al registrar cada remito.
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
        if await conn.fetchval("SELECT 1 FROM purchase_remitos WHERE supplier_id = $1 AND UPPER(remito_number) = $2", supplier_id, number):
            raise HTTPException(400, "Este proveedor ya tiene un remito con ese número.")
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

        segundo_control = (await conn.fetchval("SELECT value FROM system_settings WHERE key = 'require_mobile_reception'")) == "true"
        remito_id = await conn.fetchval(
            "INSERT INTO purchase_remitos (remito_number, supplier_id, branch_id, sector_id, status, created_by) VALUES ($1, $2, $3, $4, $5, $6) RETURNING id",
            number, supplier_id, branch_id, sector_id, "PENDING_CONTROL" if segundo_control else "COMPLETED", admin["username"])
        for sku, qty, loc_id, lot, line_id in filas:
            await conn.execute("INSERT INTO purchase_remito_lines (purchase_remito_id, sku, quantity_sent, quantity_received, location_id, lot_number, purchase_order_line_id) VALUES ($1, $2, $3, $4, $5, $6, $7)",
                               remito_id, sku, qty, 0 if segundo_control else qty, loc_id, lot, line_id)
            if not segundo_control:
                await record_stock_movement(conn, sku, branch_id, sector_id, loc_id, qty, 'IN_RECEPTION', number, admin["username"], lot_number=lot)
            if line_id:
                await conn.execute("UPDATE purchase_order_lines SET quantity_received = quantity_received + $1 WHERE id = $2", qty, line_id)
        for po_id in ordenes:
            abiertas = await conn.fetchval("SELECT COUNT(*) FROM purchase_order_lines WHERE purchase_order_id = $1 AND quantity_received < quantity_ordered", po_id)
            await conn.execute("UPDATE purchase_orders SET status = $1 WHERE id = $2", "COMPLETED" if abiertas == 0 else "IN_PROGRESS", po_id)

        await log_action(conn, admin["username"], "PURCHASE_REMITO_CREATED", f"Remito de compra {number} registrado ({len(filas)} líneas, {len(ordenes)} OC, {'pendiente de control' if segundo_control else 'stock ingresado'}).")
        res_data = {"status": "success", "message": "Remito registrado.", "remito_number": number, "id": str(remito_id), "stock_ingresado": not segundo_control}
        await save_idempotency(conn, x_idempotency_key, "/api/admin/purchase-remitos", res_data)
        return res_data

@router.get("/api/admin/purchase-remitos/{remito_number}")
async def get_purchase_remito(remito_number: str, admin: dict = Depends(require_supervisor), conn: asyncpg.Connection = Depends(get_db_connection)):
    rem = await conn.fetchrow("""
        SELECT pr.id::text AS id, pr.remito_number, pr.status, pr.created_at, pr.created_by,
               COALESCE(e.company_name, 'Sin Proveedor') AS supplier_name, COALESCE(e.tax_id, '') AS supplier_tax_id,
               b.name AS branch_name, sec.name AS sector_name
        FROM purchase_remitos pr LEFT JOIN entities e ON pr.supplier_id = e.id
        LEFT JOIN branches b ON pr.branch_id = b.id LEFT JOIN sectors sec ON pr.sector_id = sec.id
        WHERE pr.id = $1
    """, await _resolve_remito_id(conn, remito_number))
    lines = await conn.fetch("""
        SELECT prl.sku, COALESCE(i.description, prl.sku) AS description, prl.quantity_sent::float AS sent,
               prl.quantity_received::float AS controlled, l.location_code, COALESCE(prl.lot_number, '') AS lot_number,
               po.order_number, prl.added_in_control
        FROM purchase_remito_lines prl
        LEFT JOIN items i ON UPPER(i.sku) = UPPER(prl.sku)
        LEFT JOIN locations l ON prl.location_id = l.id
        LEFT JOIN purchase_order_lines pol ON prl.purchase_order_line_id = pol.id
        LEFT JOIN purchase_orders po ON pol.purchase_order_id = po.id
        WHERE prl.purchase_remito_id = $1::uuid ORDER BY po.order_number NULLS FIRST, prl.sku
    """, rem["id"])
    header = {k: rem[k] for k in ("id", "remito_number", "status", "created_at", "created_by", "supplier_name", "supplier_tax_id", "branch_name", "sector_name")}
    exceptions = await conn.fetch("""
        SELECT x.id::text AS id, x.sku, COALESCE(i.description, x.sku) AS description, x.quantity::float AS quantity, l.location_code,
               x.status, x.reported_by, x.resolved_by, x.created_at, x.resolved_at
        FROM reception_exceptions x LEFT JOIN items i ON UPPER(i.sku) = UPPER(x.sku) LEFT JOIN locations l ON x.location_id = l.id
        WHERE x.remito_id = $1::uuid ORDER BY x.created_at
    """, rem["id"])
    return {"remito": header, "lines": [dict(l) for l in lines], "exceptions": [dict(x) for x in exceptions]}

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
        FROM purchase_remitos pr LEFT JOIN entities e ON pr.supplier_id = e.id WHERE pr.id = $1
    """, await _resolve_remito_id(conn, remito_number))
    # Control ciego: el operario ve que articulos trae el remito, donde van y cuanto lleva escaneado,
    # nunca cuanto dice el remito (asi cuenta lo que llego en vez de confirmar el papel).
    lines = await conn.fetch("""
        SELECT UPPER(prl.sku) AS sku, COALESCE(MAX(i.description), UPPER(prl.sku)) AS description,
               MAX(l.location_code) AS location_code, SUM(prl.quantity_received)::float AS scanned
        FROM purchase_remito_lines prl LEFT JOIN locations l ON prl.location_id = l.id LEFT JOIN items i ON UPPER(i.sku) = UPPER(prl.sku)
        WHERE prl.purchase_remito_id = $1 GROUP BY UPPER(prl.sku) ORDER BY UPPER(prl.sku)
    """, rem["id"])
    unexpected = await conn.fetch("""
        SELECT x.sku, x.quantity::float AS quantity, x.status FROM reception_exceptions x WHERE x.remito_id = $1 ORDER BY x.created_at
    """, rem["id"])
    remito = {k: rem[k] for k in ("id", "remito_number", "status", "supplier_name")}
    return {"remito": remito, "lines": [dict(l) for l in lines], "unexpected": [dict(u) for u in unexpected], "blind": True}

@router.post("/api/reception/remitos/{remito_number}/scan")
@router.post("/api/reception/orders/{remito_number}/scan")
async def scan_reception_item(remito_number: str, data: MobileRemitoScanInput, user: dict = Depends(get_current_user), conn: asyncpg.Connection = Depends(get_db_connection)):
    async with conn.transaction():
        rem = await conn.fetchrow("SELECT id, remito_number, status, branch_id, sector_id FROM purchase_remitos WHERE id = $1 FOR UPDATE", await _resolve_remito_id(conn, remito_number))
        if not rem: raise HTTPException(404, "Remito no encontrado")
        if rem["status"] in ("COMPLETED", "COMPLETED_DIFF"): raise HTTPException(400, "El control de este remito ya está finalizado.")
        require_valid_quantity(data.quantity)
        sku_clean = data.sku.strip().upper()
        # Un mismo SKU puede venir en varias lineas (suelto y contra una o mas OC): el escaneo llena lo
        # remitido y lo que sobra va a una linea suelta "agregada en el control" (nunca contra una OC).
        lines = await conn.fetch("""
            SELECT id, quantity_sent::float AS sent, quantity_received::float AS received, location_id, COALESCE(lot_number, '') AS lot_number
            FROM purchase_remito_lines WHERE purchase_remito_id = $1 AND UPPER(sku) = $2 AND NOT added_in_control
            ORDER BY (quantity_received < quantity_sent) DESC, id
        """, rem["id"], sku_clean)
        if not lines: raise HTTPException(400, f"El artículo {sku_clean} no figura en este remito. Si llegó, registralo como no esperado (queda en cuarentena).")

        loc_id = None
        if data.location_code and data.location_code.strip():
            loc = await conn.fetchrow("SELECT id FROM locations WHERE UPPER(location_code) = $1", data.location_code.strip().upper())
            if loc: loc_id = loc["id"]

        restante = float(data.quantity)
        repartos = []
        for ln in lines:
            if restante <= 0: break
            toma = min(restante, max(ln["sent"] - ln["received"], 0))
            if toma <= 0: continue
            repartos.append((ln["id"], ln["location_id"], ln["lot_number"], toma))
            restante -= toma
        excedente = restante
        if excedente > 0:
            extra = await conn.fetchrow("SELECT id, location_id, COALESCE(lot_number, '') AS lot_number FROM purchase_remito_lines WHERE purchase_remito_id = $1 AND UPPER(sku) = $2 AND added_in_control", rem["id"], sku_clean)
            if not extra:
                extra = await conn.fetchrow("""
                    INSERT INTO purchase_remito_lines (purchase_remito_id, sku, quantity_sent, quantity_received, location_id, lot_number, added_in_control)
                    VALUES ($1, $2, 0, 0, $3, $4, TRUE) RETURNING id, location_id, COALESCE(lot_number, '') AS lot_number
                """, rem["id"], sku_clean, loc_id or lines[0]["location_id"], lines[0]["lot_number"])
            repartos.append((extra["id"], extra["location_id"], extra["lot_number"], excedente))

        serials = list(data.serial_numbers or [])
        for line_id, line_loc, lot, toma in repartos:
            sn = serials[:int(toma)] if serials else []
            serials = serials[int(toma):] if serials else []
            await conn.execute("""
                UPDATE purchase_remito_lines
                SET quantity_received = quantity_received + $1,
                    serial_numbers = COALESCE(serial_numbers, '[]'::jsonb) || $2::jsonb
                WHERE id = $3
            """, toma, json.dumps(sn), line_id)
            await record_stock_movement(conn, sku_clean, rem["branch_id"], rem["sector_id"], loc_id or line_loc, toma, 'IN_RECEPTION', rem["remito_number"], user.get("username"), lot_number=lot, serial_numbers=sn)
        await conn.execute("UPDATE purchase_remitos SET status = 'IN_PROGRESS' WHERE id = $1 AND status IN ('PENDING', 'PENDING_CONTROL')", rem["id"])

        aviso = None
        if excedente > 0:
            aviso = f"Llegó más de lo que dice el remito: {excedente:g} de {sku_clean} se agregaron como artículo suelto."
            await add_system_note(conn, "REMITO", rem["id"], f"Control: {user.get('username')} recibió {excedente:g} de {sku_clean} por encima de lo remitido; se agregaron como artículo suelto.")
        # Control ciego: el remito no se cierra solo (eso le diria al operario que llego a lo esperado).
        return {"status": "success", "message": f"Ingresado {data.quantity:g} un de {sku_clean}", "warning": aviso, "remito_completed": False}

async def _remito_en_control(conn: asyncpg.Connection, ref: str):
    rem = await conn.fetchrow("SELECT id, remito_number, status, branch_id, sector_id FROM purchase_remitos WHERE id = $1 FOR UPDATE", await _resolve_remito_id(conn, ref))
    if rem["status"] not in ("PENDING", "PENDING_CONTROL", "IN_PROGRESS"): raise HTTPException(400, "El control de este remito ya está finalizado.")
    return rem

# Articulo que no figura en el remito: entra a cuarentena (en el deposito, pero no disponible) hasta que
# ADMIN o SUPERVISOR lo apruebe (pasa a disponible y se suma al remito) o lo rechace (se devuelve).
@router.post("/api/reception/remitos/{remito_number}/unexpected")
async def report_unexpected_item(remito_number: str, data: UnexpectedItemInput, user: dict = Depends(get_current_user), conn: asyncpg.Connection = Depends(get_db_connection)):
    async with conn.transaction():
        rem = await _remito_en_control(conn, remito_number)
        require_valid_quantity(data.quantity)
        sku = data.sku.strip().upper()
        if not await conn.fetchval("SELECT 1 FROM items WHERE UPPER(sku) = $1", sku):
            raise HTTPException(400, f"El artículo {sku} no existe en el maestro: dejá una observación en el remito.")
        if await conn.fetchval("SELECT 1 FROM purchase_remito_lines WHERE purchase_remito_id = $1 AND UPPER(sku) = $2", rem["id"], sku):
            raise HTTPException(400, f"El artículo {sku} figura en el remito: escanealo normalmente.")
        loc_id = None
        if data.location_code and data.location_code.strip():
            loc_id = await conn.fetchval("SELECT id FROM locations WHERE UPPER(location_code) = $1", data.location_code.strip().upper())
            if not loc_id: raise HTTPException(400, f"La ubicación {data.location_code.strip().upper()} no existe.")
        await conn.execute("INSERT INTO reception_exceptions (remito_id, sku, quantity, location_id, lot_number, reported_by) VALUES ($1, $2, $3, $4, $5, $6)",
                           rem["id"], sku, data.quantity, loc_id, data.lot_number.strip(), user.get("username"))
        await record_stock_movement(conn, sku, rem["branch_id"], rem["sector_id"], loc_id, data.quantity, 'IN_QUARANTINE', rem["remito_number"], user.get("username"), lot_number=data.lot_number.strip(), condition="CUARENTENA")
        await conn.execute("UPDATE purchase_remitos SET status = 'IN_PROGRESS' WHERE id = $1 AND status IN ('PENDING', 'PENDING_CONTROL')", rem["id"])
        await add_system_note(conn, "REMITO", rem["id"], f"Control: {user.get('username')} registró {data.quantity:g} de {sku}, que no figura en el remito. Quedan en cuarentena hasta que un responsable los apruebe o rechace.")
        return {"status": "success", "message": f"{data.quantity:g} de {sku} quedan en cuarentena hasta que un responsable los apruebe."}

@router.post("/api/reception/remitos/{remito_number}/finish")
async def finish_reception_control(remito_number: str, user: dict = Depends(get_current_user), conn: asyncpg.Connection = Depends(get_db_connection)):
    async with conn.transaction():
        rem = await _remito_en_control(conn, remito_number)
        por_sku = await conn.fetch("""
            SELECT UPPER(sku) AS sku, SUM(quantity_sent)::float AS sent, SUM(quantity_received)::float AS received
            FROM purchase_remito_lines WHERE purchase_remito_id = $1 GROUP BY UPPER(sku) ORDER BY UPPER(sku)
        """, rem["id"])
        faltantes = [(r["sku"], r["sent"] - r["received"]) for r in por_sku if r["received"] < r["sent"]]
        sobrantes = [(r["sku"], r["received"] - r["sent"]) for r in por_sku if r["received"] > r["sent"]]
        cuarentena = await conn.fetchval("SELECT COUNT(*) FROM reception_exceptions WHERE remito_id = $1 AND status = 'PENDING'", rem["id"])
        con_diferencias = bool(faltantes or sobrantes or cuarentena)
        await conn.execute("UPDATE purchase_remitos SET status = $1 WHERE id = $2", "COMPLETED_DIFF" if con_diferencias else "COMPLETED", rem["id"])
        partes = [f"Control finalizado por {user.get('username')}."]
        if faltantes: partes.append("Faltantes: " + ", ".join(f"{s} {q:g}" for s, q in faltantes) + ".")
        if sobrantes: partes.append("Sobrantes: " + ", ".join(f"{s} {q:g}" for s, q in sobrantes) + ".")
        if cuarentena: partes.append(f"Artículos no esperados en cuarentena: {cuarentena}.")
        if not con_diferencias: partes.append("Sin diferencias con el remito.")
        await add_system_note(conn, "REMITO", rem["id"], " ".join(partes))
        return {"status": "COMPLETED_DIFF" if con_diferencias else "COMPLETED",
                "faltantes": [{"sku": s, "quantity": q} for s, q in faltantes],
                "sobrantes": [{"sku": s, "quantity": q} for s, q in sobrantes],
                "cuarentena": cuarentena}

async def _resolver_excepcion(conn: asyncpg.Connection, exception_id: str, user: dict, aprobar: bool):
    x = await conn.fetchrow("""
        SELECT x.id, x.sku, x.quantity::float AS quantity, x.location_id, COALESCE(x.lot_number, '') AS lot_number, x.status,
               pr.id AS remito_id, pr.remito_number, pr.branch_id, pr.sector_id
        FROM reception_exceptions x JOIN purchase_remitos pr ON pr.id = x.remito_id WHERE x.id = $1 FOR UPDATE OF x
    """, _parse_uuid(exception_id, "Excepción inválida."))
    if not x: raise HTTPException(404, "Excepción no encontrada.")
    if x["status"] != "PENDING": raise HTTPException(400, "Esta excepción ya fue resuelta.")
    quien = user.get("username")
    # Sale de cuarentena en los dos casos; si se aprueba, entra como disponible y se suma al remito.
    await record_stock_movement(conn, x["sku"], x["branch_id"], x["sector_id"], x["location_id"], -x["quantity"], 'QUARANTINE_RELEASE' if aprobar else 'QUARANTINE_REJECT', x["remito_number"], quien, lot_number=x["lot_number"], condition="CUARENTENA")
    if aprobar:
        await record_stock_movement(conn, x["sku"], x["branch_id"], x["sector_id"], x["location_id"], x["quantity"], 'QUARANTINE_RELEASE', x["remito_number"], quien, lot_number=x["lot_number"])
        await conn.execute("INSERT INTO purchase_remito_lines (purchase_remito_id, sku, quantity_sent, quantity_received, location_id, lot_number, added_in_control) VALUES ($1, $2, 0, $3, $4, $5, TRUE)",
                           x["remito_id"], x["sku"], x["quantity"], x["location_id"], x["lot_number"])
        texto = f"{quien} aprobó {x['quantity']:g} de {x['sku']} no esperados: pasan a stock disponible y se suman al remito como artículo suelto."
    else:
        texto = f"{quien} rechazó {x['quantity']:g} de {x['sku']} no esperados: salen de la cuarentena para devolverse al proveedor."
    await conn.execute("UPDATE reception_exceptions SET status = $1, resolved_by = $2, resolved_at = CURRENT_TIMESTAMP WHERE id = $3", "APPROVED" if aprobar else "REJECTED", quien, x["id"])
    await add_system_note(conn, "REMITO", x["remito_id"], texto)
    await log_action(conn, quien, "RECEPTION_EXCEPTION_APPROVED" if aprobar else "RECEPTION_EXCEPTION_REJECTED", f"Remito {x['remito_number']}: {texto}")
    return {"status": "success", "message": texto}

@router.post("/api/admin/reception-exceptions/{exception_id}/approve")
async def approve_reception_exception(exception_id: str, admin: dict = Depends(require_supervisor), conn: asyncpg.Connection = Depends(get_db_connection)):
    async with conn.transaction():
        return await _resolver_excepcion(conn, exception_id, admin, True)

@router.post("/api/admin/reception-exceptions/{exception_id}/reject")
async def reject_reception_exception(exception_id: str, admin: dict = Depends(require_supervisor), conn: asyncpg.Connection = Depends(get_db_connection)):
    async with conn.transaction():
        return await _resolver_excepcion(conn, exception_id, admin, False)

@router.get("/api/admin/returns/next-number")
async def get_next_return_number(user: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    return {"next_number": await siguiente_numero(conn, "DEVOLUCION")}

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
    """, parse_uuid(customer_id))
    return [dict(r) for r in rows]

@router.get("/api/admin/returns/order-details/{document_id}")
async def get_return_order_details(document_id: str, user: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    doc = await conn.fetchrow("SELECT id, document_number, customer_id FROM documents WHERE id = $1", parse_uuid(document_id))
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
        ret_num, aviso = await numero_correlativo(conn, "DEVOLUCION", data.return_number)
        ret_num = ret_num.upper()

        target_branch_id = None
        target_sector_id = None

        # Destino: la sucursal/sector del usuario si los tiene; si no, los que vienen en la devolucion
        # (invalidos dan 400); si no, la primera sucursal y un sector de esa misma sucursal.
        if user.get("branch_id"):
            target_branch_id = _parse_uuid(user["branch_id"], "La sucursal asignada al usuario es inválida.")
        if user.get("sector_id"):
            target_sector_id = _parse_uuid(user["sector_id"], "El sector asignado al usuario es inválido.")
        if not target_branch_id and data.branch_id:
            target_branch_id = _parse_uuid(data.branch_id, "Sucursal de destino inválida.")
        if not target_sector_id and data.sector_id:
            target_sector_id = _parse_uuid(data.sector_id, "Sector de destino inválido.")

        if not target_branch_id:
            target_branch_id = await conn.fetchval("SELECT id FROM branches ORDER BY created_at LIMIT 1")
        if not target_sector_id:
            target_sector_id = await conn.fetchval("SELECT id FROM sectors WHERE branch_id = $1 ORDER BY created_at LIMIT 1", target_branch_id)

        if not target_branch_id or not target_sector_id:
            raise HTTPException(400, "Debe configurar al menos una Sucursal y Sector de destino.")
        if not await conn.fetchval("SELECT 1 FROM sectors WHERE id = $1 AND branch_id = $2", target_sector_id, target_branch_id):
            raise HTTPException(400, "El sector de destino no pertenece a la sucursal de destino.")

        return_id = await conn.fetchval("""
            INSERT INTO customer_returns (return_number, customer_id, document_id, branch_id, sector_id, created_by)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING id
        """, ret_num, parse_uuid(data.customer_id), parse_uuid(data.document_id), target_branch_id, target_sector_id, user["username"])

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
        res_data = {"status": "success", "message": f"Devolución {ret_num} registrada exitosamente." + (f" {aviso}" if aviso else ""),
                    "return_number": ret_num}
        
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