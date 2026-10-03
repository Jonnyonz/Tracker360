from fastapi import APIRouter, Depends, HTTPException, Header, Request
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timezone
import asyncpg, uuid, json, math

from backend.filtros import Filtros
from backend.database import get_db_connection, get_current_user, get_client_ip, require_admin, require_supervisor, record_stock_movement, log_action, dispatch_event_to_channels, queue_zpl_print_job, numero_correlativo, siguiente_numero, check_idempotency, save_idempotency, require_valid_quantity, emitir_stock_a_canales, emitir_estado_a_canal

router = APIRouter(tags=["Outbound & Dispatch"])

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

class PackedItem(BaseModel):
    sku: str
    quantity: float
    box_number: int

class PackOrderInput(BaseModel):
    boxes: int
    packed_items: Optional[List[PackedItem]] = []

class ManualOrderLine(BaseModel):
    sku: str
    quantity: float
    serial_numbers: Optional[List[str]] = []

class ManualOrderInput(BaseModel):
    document_number: Optional[str] = None  # se ignora: el numero lo asigna el sistema
    customer_tax_id: str
    customer_name: Optional[str] = None
    address_label: str = "Principal"
    lines: List[ManualOrderLine]

@router.get("/api/admin/documents")
async def list_admin_documents(number: str = "", customer: str = "", sku: str = "", status: str = "", channel: str = "",
                               account: str = "", shipping: str = "", urgent: str = "", date_from: str = "", date_to: str = "",
                               limit: int = 100, admin: dict = Depends(require_supervisor), conn: asyncpg.Connection = Depends(get_db_connection)):
    """Pedidos, del mas nuevo al mas viejo (sin filtros, los ultimos 100). Filtros opcionales: number (pedido o
    venta del canal), customer (cliente o comprador), sku, status, channel (codigo o nombre), account (cuenta del
    canal), shipping (tipo de envio), urgent SI, fechas AAAA-MM-DD. limit de 1 a 500."""
    f = (Filtros().texto(number, "d.document_number", "d.external_ref").texto(customer, "e.company_name", "d.buyer_name")
         .igual(status, "d.status", ("PENDING", "IN_PROGRESS", "COMPLETED", "DISPATCHED", "CANCELLED", "FULL"))
         .texto(channel, "sc.code", "sc.name").texto(account, "d.external_account").texto(shipping, "d.shipping_type")
         .fechas(date_from, date_to, "d.created_at"))
    if sku.strip():
        f.condiciones.append(f"EXISTS (SELECT 1 FROM document_lines dl WHERE dl.document_id = d.id AND dl.sku ILIKE {f._param('%' + sku.strip() + '%')})")
    if urgent.strip().upper() == "SI":
        f.condiciones.append("COALESCE(d.priority, 0) > 0")
    tope = f.limite(limit)
    rows = await conn.fetch(f"""
        SELECT d.document_number, COALESCE(e.company_name, d.buyer_name, 'Consumidor Final') as company_name,
               d.status,
               COALESCE((SELECT SUM(quantity_picked) * 100.0 / NULLIF(SUM(quantity_requested), 0)
                         FROM document_lines WHERE document_id = d.id), 0)::int as progress_pct,
               d.created_at, d.external_ref, d.external_account, d.shipping_type, COALESCE(d.priority, 0) > 0 as urgent,
               sc.code as channel_code
        FROM documents d
        LEFT JOIN entities e ON d.customer_id = e.id
        LEFT JOIN sales_channels sc ON d.sales_channel_id = sc.id
        WHERE {f.where()}
        ORDER BY d.created_at DESC LIMIT {tope}
    """, *f.args)
    return [dict(r) for r in rows]

@router.get("/api/admin/documents/{document_number}/participants")
async def get_document_participants(document_number: str, user: dict = Depends(require_supervisor), conn: asyncpg.Connection = Depends(get_db_connection)):
    # Quienes intervinieron en un pedido: pickeo por pedido (kardex) y eventos de auditoria
    # (alta, despacho). Para supervisores y admins.
    num = document_number.strip().upper()
    doc = await conn.fetchrow("SELECT document_number, status, created_at FROM documents WHERE document_number = $1", num)
    if not doc:
        raise HTTPException(status_code=404, detail="Pedido no encontrado.")
    picking = await conn.fetch("""
        SELECT username, COUNT(*) AS lecturas, SUM(-quantity) AS unidades, MIN(created_at) AS desde, MAX(created_at) AS hasta
        FROM stock_movements WHERE reference_document = $1 AND movement_type = 'OUT_PICKING'
        GROUP BY username ORDER BY MIN(created_at)
    """, num)
    eventos = await conn.fetch("""
        SELECT username, action, regexp_replace(details, '^[[][^]]*[]] ', '') AS details, created_at
        FROM audit_logs WHERE position($1 in details) > 0 AND action <> 'UNAUTHORIZED_ACCESS' ORDER BY created_at
    """, num)
    return {
        "document_number": doc["document_number"], "status": doc["status"], "created_at": doc["created_at"],
        "picking": [dict(r) for r in picking],
        "eventos": [dict(r) for r in eventos],
        # Pickeos por ola anteriores a que se registraran por pedido quedaron como WAVE-PICK.
        "nota": "Los pickeos por ola hechos antes de esta version quedaron sin numero de pedido y no aparecen aca.",
    }

@router.get("/api/admin/sales-orders/next-number")
async def get_next_order_number(admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    return {"next_number": await siguiente_numero(conn, "PEDIDO")}

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
        numero, aviso = await numero_correlativo(conn, "PEDIDO", data.document_number)
        ent = await conn.fetchrow("SELECT id FROM entities WHERE tax_id = $1", data.customer_tax_id)
        ent_id = ent["id"] if ent else None
        
        doc_id = await conn.fetchval(
            "INSERT INTO documents (document_number, customer_id, status, channel_origin) VALUES ($1, $2, 'PENDING', 'MANUAL') RETURNING id",
            numero, ent_id
        )
        
        for line in data.lines:
            require_valid_quantity(line.quantity)
            await conn.execute(
                "INSERT INTO document_lines (document_id, sku, quantity_requested, quantity_picked, serial_numbers) VALUES ($1, $2, $3, 0, $4::jsonb)",
                doc_id, line.sku.strip().upper(), line.quantity, json.dumps(line.serial_numbers or [])
            )
        
        await emitir_stock_a_canales(conn, [line.sku for line in data.lines])
        await log_action(conn, admin.get("username"), "ORDER_CREATED", f"Pedido manual {numero} creado.")
        res_data = {"status": "success", "message": f"Pedido {numero} creado correctamente." + (f" {aviso}" if aviso else ""),
                    "document_number": numero}
        
        await save_idempotency(conn, x_idempotency_key, "/api/admin/sales-orders", res_data)
        return res_data

class CancelLine(BaseModel):
    sku: str
    quantity: float

class CancelOrderInput(BaseModel):
    # Sin lineas: cancelacion total. Con lineas: se cancela esa cantidad de cada SKU.
    lines: Optional[List[CancelLine]] = None

def _tipo_retroceso(num: str) -> str:
    return f"Retroceso de PDV ID:{num}"

async def cancelar_pedido(conn: asyncpg.Connection, document_number: str, lineas_cancelar: Optional[List[CancelLine]], usuario: str, ip: str) -> dict:
    """Cancelacion total (sin lineas) o parcial. Lo ya pickeado de la parte cancelada vuelve al
    stock tal como estaba antes de prepararlo: misma sucursal, sector, ubicacion, lote, condicion
    y numeros de serie, con el movimiento "Retroceso de PDV ID:<pedido>" en la traza. La usan el
    panel (solo ADMIN) y los canales de venta."""
    num = document_number.strip().upper()
    tipo = _tipo_retroceso(num)
    async with conn.transaction():
        doc = await conn.fetchrow("SELECT id, status FROM documents WHERE UPPER(document_number) = $1 FOR UPDATE", num)
        if not doc: raise HTTPException(404, "Pedido no encontrado.")
        if doc["status"] == "DISPATCHED": raise HTTPException(400, "El pedido ya fue despachado: no se puede cancelar.")
        if doc["status"] == "CANCELLED": raise HTTPException(400, "El pedido ya esta cancelado.")

        lineas = await conn.fetch("SELECT id, UPPER(sku) AS sku, quantity_requested::float AS pedido, quantity_picked::float AS pickeado FROM document_lines WHERE document_id = $1 FOR UPDATE", doc["id"])
        por_sku = {l["sku"]: l for l in lineas}
        if not lineas_cancelar:
            objetivo = {l["sku"]: l["pedido"] for l in lineas if l["pedido"] > 0}
        else:
            objetivo = {}
            for cl in lineas_cancelar:
                sku = cl.sku.strip().upper()
                if sku not in por_sku: raise HTTPException(400, f"El SKU '{sku}' no pertenece a este pedido.")
                if cl.quantity <= 0: raise HTTPException(400, "La cantidad a cancelar debe ser mayor a cero.")
                objetivo[sku] = objetivo.get(sku, 0) + cl.quantity
            for sku, cant in objetivo.items():
                if cant > por_sku[sku]["pedido"] + 1e-9:
                    raise HTTPException(400, f"No se pueden cancelar {cant:g} de '{sku}': el pedido tiene {por_sku[sku]['pedido']:g}.")

        devuelto = []
        for sku, cancelar in objetivo.items():
            linea = por_sku[sku]
            nuevo_pedido = linea["pedido"] - cancelar
            a_devolver = max(0.0, linea["pickeado"] - nuevo_pedido)
            if a_devolver > 0:
                # Stock que salio para este pedido, por ubicacion/lote/condicion, neto de retrocesos previos.
                movs = await conn.fetch("""
                    SELECT movement_type, branch_id, sector_id, location_id, COALESCE(lot_number, '') AS lote,
                           COALESCE(condition, 'OPERATIVO') AS cond, expiration_date, quantity::float AS q,
                           COALESCE(serial_numbers, '[]'::jsonb)::text AS sn, created_at
                    FROM stock_movements
                    WHERE reference_document = $1 AND UPPER(sku) = $2 AND movement_type IN ('OUT_PICKING', $3)
                    ORDER BY created_at, id
                """, num, sku, tipo)
                origenes = {}
                for m in movs:
                    clave = (m["branch_id"], m["sector_id"], m["location_id"], m["lote"], m["cond"])
                    o = origenes.setdefault(clave, {"neto": 0.0, "seriales": [], "vence": m["expiration_date"], "ultimo": None})
                    seriales = json.loads(m["sn"]) or []
                    if m["movement_type"] == "OUT_PICKING":
                        o["neto"] += -m["q"]; o["seriales"].extend(seriales); o["ultimo"] = m["created_at"]
                    else:
                        o["neto"] -= m["q"]; o["seriales"] = [s for s in o["seriales"] if s not in seriales]
                atribuible = sum(o["neto"] for o in origenes.values())
                if atribuible + 1e-9 < a_devolver:
                    raise HTTPException(409, f"{a_devolver - atribuible:g} unidades de '{sku}' se pickearon sin registrar el pedido (picking por ola de una version anterior): no se sabe de que ubicacion salieron. Cancelar menos unidades o devolverlas con un ajuste de stock.")
                restante = a_devolver
                # Primero lo pickeado mas recientemente.
                for clave, o in sorted(origenes.items(), key=lambda kv: (kv[1]["ultimo"] is not None, kv[1]["ultimo"] or 0), reverse=True):
                    if restante <= 1e-9: break
                    cant = min(o["neto"], restante)
                    if cant <= 1e-9: continue
                    seriales = o["seriales"][-int(cant):] if o["seriales"] and cant >= 1 else []
                    await record_stock_movement(conn, sku, clave[0], clave[1], clave[2], cant, tipo, num, usuario, lot_number=clave[3], expiration_date=o["vence"], condition=clave[4], serial_numbers=seriales)
                    devuelto.append({"sku": sku, "cantidad": cant, "location_id": str(clave[2]) if clave[2] else None})
                    restante -= cant
            await conn.execute("UPDATE document_lines SET quantity_requested = $1, quantity_picked = $2 WHERE id = $3", nuevo_pedido, linea["pickeado"] - a_devolver, linea["id"])

        tot = await conn.fetchrow("SELECT COALESCE(SUM(quantity_requested), 0)::float AS pedido, COALESCE(SUM(quantity_picked), 0)::float AS pickeado, COUNT(*) FILTER (WHERE quantity_picked < quantity_requested) AS pendientes FROM document_lines WHERE document_id = $1", doc["id"])
        if tot["pedido"] <= 1e-9: estado = "CANCELLED"
        elif doc["status"] == "FULL": estado = "FULL"   # Full (deposito del marketplace): nunca entra al picking
        elif tot["pendientes"] == 0: estado = "COMPLETED"
        elif tot["pickeado"] > 0: estado = "IN_PROGRESS"
        else: estado = "PENDING"
        await conn.execute("UPDATE documents SET status = $1 WHERE id = $2", estado, doc["id"])
        await emitir_stock_a_canales(conn, objetivo.keys())
        await emitir_estado_a_canal(conn, doc["id"])
        detalle = ", ".join(f"{sku} x{cant:g}" for sku, cant in objetivo.items())
        await log_action(conn, usuario, "ORDER_CANCELLED" if estado == "CANCELLED" else "ORDER_PARTIAL_CANCEL",
                         f"Pedido {num}: cancelado {detalle}. Stock devuelto: {sum(d['cantidad'] for d in devuelto):g} un.", ip)
    return {"status": estado, "devuelto": devuelto}

@router.post("/api/admin/sales-orders/{document_number}/cancel")
async def cancel_sales_order(document_number: str, data: CancelOrderInput, request: Request, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    return await cancelar_pedido(conn, document_number, data.lines, admin.get("username"), get_client_ip(request))

# Etiqueta de pedido: la plantilla por defecto (y la del editor) usa {{ORDER_NUM}} y {{DESTINATION}};
# el codigo solo reemplazaba {order_number}/{client_name}/{delivery_address} y la etiqueta salia con el
# marcador literal. Se aceptan los dos formatos. A los datos se les quitan ^ y ~ (comandos ZPL): un
# nombre de cliente con "^XZ" cortaba la etiqueta o agregaba campos.
def _zpl_pedido(template: str, doc) -> str:
    limpio = lambda v: str(v or "").replace("^", "").replace("~", "")
    numero, cliente, direccion = limpio(doc["document_number"]), limpio(doc["client_name"]), limpio(doc["delivery_address"])
    valores = {"{{ORDER_NUM}}": numero, "{{DESTINATION}}": f"{cliente} - {direccion}", "{{CLIENT}}": cliente, "{{ADDRESS}}": direccion,
               "{order_number}": numero, "{client_name}": cliente, "{delivery_address}": direccion}
    for marcador, valor in valores.items():
        template = template.replace(marcador, valor)
    return template

async def imprimir_etiqueta_pedido(conn: asyncpg.Connection, doc) -> bool:
    """Encola la etiqueta del pedido: la que mando el canal de venta (channel_label_zpl, por ejemplo la
    de envio de Mercado Libre) si la tiene; si no, la plantilla zpl_order_template. False si no hay
    ninguna. doc necesita document_number, client_name, delivery_address y channel_label_zpl."""
    zpl = doc["channel_label_zpl"]
    if not zpl:
        template = await conn.fetchval("SELECT value FROM system_settings WHERE key = 'zpl_order_template'")
        if not template:
            return False
        zpl = _zpl_pedido(template, doc)
    default_queue = await conn.fetchval("SELECT print_queue_code FROM sectors WHERE uses_locations = FALSE LIMIT 1") or "PRINT-SEC-01"
    await queue_zpl_print_job(conn, default_queue, zpl)
    return True

@router.post("/api/admin/sales-orders/{document_number}/print-label")
async def reprint_order_label(document_number: str, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    doc = await conn.fetchrow("SELECT d.document_number, d.channel_label_zpl, COALESCE(c.company_name, d.buyer_name, 'Consumidor Final') as client_name, COALESCE(a.full_address, d.buyer_address, 'A coordinar') as delivery_address FROM documents d LEFT JOIN entities c ON d.customer_id = c.id LEFT JOIN entity_addresses a ON d.customer_address_id = a.id WHERE d.document_number = $1", document_number.strip().upper())
    if not doc: raise HTTPException(404, "Pedido no encontrado.")
    if await imprimir_etiqueta_pedido(conn, doc):
        return {"status": "success", "message": "Etiqueta re-enviada a impresión."}
    raise HTTPException(400, "Plantilla ZPL no configurada.")

async def _ubicaciones_sugeridas(conn: asyncpg.Connection, sku: str):
    """Ubicaciones con stock OPERATIVO del SKU para la pantalla de picking: texto "A-01 (5) | A-02 (3)"
    y clave de orden (la primera ubicacion; sin stock va al final)."""
    locs = await conn.fetch("""
        SELECT l.location_code, si.quantity::float
        FROM stock_inventory si
        JOIN locations l ON si.location_id = l.id
        WHERE UPPER(si.sku) = $1 AND si.quantity > 0 AND COALESCE(si.condition, 'OPERATIVO') = 'OPERATIVO'
    """, sku.strip().upper())
    if not locs:
        return "Sin ubicación asignada o stock agotado", "ZZZZZ"
    return " | ".join(f"{loc['location_code']} ({loc['quantity']})" for loc in locs), locs[0]["location_code"]

@router.get("/api/picking/orders")
async def get_picking_mailbox(user: dict = Depends(get_current_user), conn: asyncpg.Connection = Depends(get_db_connection)):
    rows = await conn.fetch("""
        SELECT d.id, d.document_number, d.status, COALESCE(c.company_name, d.buyer_name, 'Consumidor Final') as company_name, d.priority, d.shipping_type
        FROM documents d 
        LEFT JOIN entities c ON d.customer_id = c.id 
        WHERE d.status IN ('PENDING', 'IN_PROGRESS') 
        ORDER BY d.priority DESC, d.created_at ASC
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
        ldict["suggested_locations"], ldict["sort_key"] = await _ubicaciones_sugeridas(conn, l["sku"])
            
        result_lines.append(ldict)

    routing_enabled = await conn.fetchval("SELECT value FROM system_settings WHERE key = 'enable_optimal_routing'") == "true"
    if routing_enabled:
        result_lines.sort(key=lambda x: (x["sort_key"], x["sku"]))
    
    return {"document": dict(doc), "lines": result_lines}

SIN_UBICACION = ("GENERAL", "SIN_UBICACION", "N/A")

# De que fila de stock sale un pickeo (por pedido o por ola). Con ubicacion, tiene que existir y
# tener el SKU; sin ubicacion, se toma cualquier fila con stock. Dentro de eso se prefiere una fila
# que alcance y la que vence antes (FEFO), y el movimiento sale de esa fila (con su lote). Sin stock
# solo se sigue si la empresa permite stock negativo, y siempre en un sector de la misma sucursal.
async def _origen_picking(conn: asyncpg.Connection, sku: str, location_code: Optional[str], cantidad: float):
    codigo = (location_code or "").strip().upper()
    loc = None
    if codigo and codigo not in SIN_UBICACION:
        loc = await conn.fetchrow("SELECT l.id, l.sector_id, s.branch_id FROM locations l JOIN sectors s ON l.sector_id = s.id WHERE UPPER(l.location_code) = $1", codigo)
        if not loc: raise HTTPException(400, f"La ubicación {codigo} no existe.")
    fila = await conn.fetchrow("""
        SELECT branch_id, sector_id, location_id, COALESCE(lot_number, '') AS lot_number, quantity::float AS quantity
        FROM stock_inventory
        WHERE UPPER(sku) = $1 AND quantity > 0 AND COALESCE(condition, 'OPERATIVO') = 'OPERATIVO'
          AND ($2::uuid IS NULL OR location_id = $2)
        ORDER BY (quantity >= $3) DESC, expiration_date NULLS LAST, quantity DESC
        LIMIT 1
    """, sku, loc["id"] if loc else None, cantidad)
    permite_negativo = (await conn.fetchval("SELECT value FROM system_settings WHERE key = 'allow_negative_stock'")) == "true"
    donde = f" en la ubicación {codigo}" if loc else ""
    if fila and (fila["quantity"] >= cantidad or permite_negativo):
        return fila["branch_id"], fila["sector_id"], fila["location_id"], fila["lot_number"]
    if not permite_negativo:
        disponible = fila["quantity"] if fila else 0
        raise HTTPException(400, f"Stock insuficiente de {sku}{donde} (Disponible: {disponible:g}).")
    if loc: return loc["branch_id"], loc["sector_id"], loc["id"], ""
    branch_id = await conn.fetchval("SELECT id FROM branches ORDER BY created_at LIMIT 1")
    sector_id = await conn.fetchval("SELECT id FROM sectors WHERE branch_id = $1 ORDER BY created_at LIMIT 1", branch_id)
    if not branch_id or not sector_id: raise HTTPException(400, "No hay una sucursal con sector configurados para registrar el movimiento.")
    return branch_id, sector_id, None, ""

@router.post("/api/picking/orders/{document_number}/scan")
async def scan_picking_item(document_number: str, data: PickScanInput, user: dict = Depends(get_current_user), conn: asyncpg.Connection = Depends(get_db_connection)):
    async with conn.transaction():
        doc = await conn.fetchrow("SELECT id, status FROM documents WHERE UPPER(document_number) = $1 FOR UPDATE", document_number.strip().upper())
        if not doc: raise HTTPException(404, "Pedido no encontrado.")
        if doc["status"] == "COMPLETED": raise HTTPException(400, "El pedido ya se encuentra completado.")
        if doc["status"] in ("CANCELLED", "DISPATCHED"): raise HTTPException(400, "El pedido esta cancelado o ya fue despachado.")
        
        sku_clean = data.sku.strip().upper()
        line = await conn.fetchrow("SELECT id, quantity_requested::float, quantity_picked::float FROM document_lines WHERE document_id = $1 AND UPPER(sku) = $2", doc["id"], sku_clean)
        if not line: raise HTTPException(400, f"El SKU '{sku_clean}' no pertenece a este pedido.")
        
        needed = line["quantity_requested"] - line["quantity_picked"]
        if needed <= 0: raise HTTPException(400, f"El SKU '{sku_clean}' ya fue recolectado totalmente.")
        if not math.isfinite(data.quantity) or data.quantity <= 0: raise HTTPException(400, "La cantidad debe ser mayor a cero.")
        if data.quantity > needed: raise HTTPException(400, f"La cantidad supera lo pendiente del SKU '{sku_clean}'. Solo faltan {needed:g}.")

        snt_enabled = await conn.fetchval("SELECT value FROM system_settings WHERE key = 'enable_serial_tracking'")
        if snt_enabled == "true" and data.serial_numbers and len(data.serial_numbers) > 0:
            if len(data.serial_numbers) != int(data.quantity):
                raise HTTPException(400, f"La cantidad de números de serie ({len(data.serial_numbers)}) debe coincidir con la cantidad ({data.quantity}).")

        branch_id, sector_id, loc_id, lot = await _origen_picking(conn, sku_clean, data.location_code, float(data.quantity))

        await conn.execute("""
            UPDATE document_lines 
            SET quantity_picked = quantity_picked + $1,
                serial_numbers = COALESCE(serial_numbers, '[]'::jsonb) || $2::jsonb 
            WHERE id = $3
        """, data.quantity, json.dumps(data.serial_numbers or []), line["id"])
        
        await record_stock_movement(conn, sku_clean, branch_id, sector_id, loc_id, -data.quantity, 'OUT_PICKING', document_number.strip().upper(), user.get("username"), lot_number=lot, serial_numbers=data.serial_numbers)
        
        if await conn.execute("UPDATE documents SET status = 'IN_PROGRESS' WHERE id = $1 AND status = 'PENDING'", doc["id"]) != "UPDATE 0":
            await emitir_estado_a_canal(conn, doc["id"])
        
        pending = await conn.fetchval("SELECT COUNT(*) FROM document_lines WHERE document_id = $1 AND quantity_picked < quantity_requested", doc["id"])
        
        auto_complete = await conn.fetchval("SELECT value FROM system_settings WHERE key = 'auto_complete_picking'") or "true"
        order_completed = (pending == 0)
        if order_completed and auto_complete == "true": 
            await conn.execute("UPDATE documents SET status = 'COMPLETED' WHERE id = $1", doc["id"])
            await emitir_estado_a_canal(conn, doc["id"])
        
        return {"status": "success", "message": f"Extraído {data.quantity} un de {sku_clean}", "order_completed": order_completed, "remaining_lines": pending}

@router.get("/api/picking/waves/pending")
async def get_wave_picking_pending(limit: int = 5, user: dict = Depends(get_current_user), conn: asyncpg.Connection = Depends(get_db_connection)):
    enabled = await conn.fetchval("SELECT value FROM system_settings WHERE key = 'enable_wave_picking'")
    if enabled != "true": raise HTTPException(400, "Picking por Olas inactivo en la Configuración Enterprise.")
        
    orders = await conn.fetch("SELECT id, document_number FROM documents WHERE status IN ('PENDING', 'IN_PROGRESS') AND document_type = 'PICKING' ORDER BY priority DESC, created_at ASC LIMIT $1", limit)
    if not orders: raise HTTPException(400, "No hay pedidos pendientes para agrupar.")
        
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
        ldict["suggested_locations"], ldict["sort_key"] = await _ubicaciones_sugeridas(conn, l["sku"])
            
        result_lines.append(ldict)
        
    if routing_enabled: result_lines.sort(key=lambda x: (x["sort_key"], x["sku"]))
        
    return {"order_numbers": order_nums, "lines": result_lines}

@router.post("/api/picking/waves/scan")
async def scan_wave_picking_item(data: WavePickScanInput, user: dict = Depends(get_current_user), conn: asyncpg.Connection = Depends(get_db_connection)):
    async with conn.transaction():
        sku_clean = data.sku.strip().upper()
        qty_to_distribute = float(data.quantity)
        if not math.isfinite(qty_to_distribute) or qty_to_distribute <= 0: raise HTTPException(400, "La cantidad debe ser mayor a cero.")
        
        snt_enabled = await conn.fetchval("SELECT value FROM system_settings WHERE key = 'enable_serial_tracking'")
        if snt_enabled == "true" and data.serial_numbers and len(data.serial_numbers) > 0:
            if len(data.serial_numbers) != int(data.quantity): raise HTTPException(400, "Descuadre en trazabilidad. La cantidad de números de serie debe coincidir con la cantidad física extraída.")
        
        numeros = [n.strip().upper() for n in data.order_numbers]
        docs = await conn.fetch("SELECT id, document_number, status FROM documents WHERE UPPER(document_number) = ANY($1::text[]) FOR UPDATE", numeros)
        if not docs: raise HTTPException(404, "Pedidos de la ola no encontrados.")
        cerrados = [d["document_number"] for d in docs if d["status"] in ("CANCELLED", "DISPATCHED")]
        if cerrados: raise HTTPException(400, f"Pedidos cancelados o ya despachados en la ola: {', '.join(cerrados)}.")
        
        total_needed = 0
        lines_to_update = []
        
        for doc in docs:
            line = await conn.fetchrow("SELECT id, quantity_requested, quantity_picked FROM document_lines WHERE document_id = $1 AND UPPER(sku) = $2", doc["id"], sku_clean)
            if line:
                needed = float(line["quantity_requested"]) - float(line["quantity_picked"])
                if needed > 0:
                    lines_to_update.append({"line_id": line["id"], "doc_id": doc["id"], "doc_number": doc["document_number"], "needed": needed})
                    total_needed += needed
        
        if qty_to_distribute > total_needed: raise HTTPException(400, f"La cantidad escaneada supera lo solicitado en esta Ola. Restante requerido: {total_needed}")
            
        serial_idx = 0
        serials_list = data.serial_numbers or []
        # Reparto por pedido: cada pedido de la ola recibe su propio movimiento de stock (con su
        # numero como referencia), para poder saber quien pickeo que y revertirlo al cancelar.
        asignaciones = []
        
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
            asignaciones.append((lu["doc_number"], apply_qty, apply_serials))
            if await conn.execute("UPDATE documents SET status = 'IN_PROGRESS' WHERE id = $1 AND status = 'PENDING'", lu["doc_id"]) != "UPDATE 0":
                await emitir_estado_a_canal(conn, lu["doc_id"])
        
        branch_id, sector_id, loc_id, lot = await _origen_picking(conn, sku_clean, data.location_code, float(data.quantity))

        for doc_number, cantidad, seriales in asignaciones:
            await record_stock_movement(conn, sku_clean, branch_id, sector_id, loc_id, -cantidad, 'OUT_PICKING', doc_number.strip().upper(), user.get("username"), lot_number=lot, serial_numbers=seriales)

        auto_complete = await conn.fetchval("SELECT value FROM system_settings WHERE key = 'auto_complete_picking'") or "true"
        wave_completed = True
        
        for doc in docs:
            pending = await conn.fetchval("SELECT COUNT(*) FROM document_lines WHERE document_id = $1 AND quantity_picked < quantity_requested", doc["id"])
            if pending == 0 and auto_complete == "true":
                await conn.execute("UPDATE documents SET status = 'COMPLETED' WHERE id = $1", doc["id"])
                await emitir_estado_a_canal(conn, doc["id"])
            if pending > 0: wave_completed = False
        
        return {"status": "success", "message": f"Consolidado {data.quantity} un de {sku_clean} en Ola.", "wave_completed": wave_completed}

@router.get("/api/packing/orders")
async def get_packing_orders(user: dict = Depends(get_current_user), conn: asyncpg.Connection = Depends(get_db_connection)):
    return [dict(r) for r in await conn.fetch("SELECT d.document_number, d.status, COALESCE(c.company_name, 'Sin Cliente') as company_name FROM documents d LEFT JOIN entities c ON d.customer_id = c.id WHERE d.status = 'COMPLETED' ORDER BY d.created_at ASC")]

@router.get("/api/packing/orders/{document_number}")
async def get_packing_order_details(document_number: str, user: dict = Depends(get_current_user), conn: asyncpg.Connection = Depends(get_db_connection)):
    doc = await conn.fetchrow("SELECT d.id, d.document_number, COALESCE(c.company_name, 'Sin cliente') as company_name, COALESCE(a.full_address, 'Sin dirección') as address FROM documents d LEFT JOIN entities c ON d.customer_id = c.id LEFT JOIN entity_addresses a ON d.customer_address_id = a.id WHERE UPPER(d.document_number) = $1", document_number.strip().upper())
    if not doc: raise HTTPException(404, "Pedido no encontrado")
    
    lines = await conn.fetch("""
        SELECT dl.sku, COALESCE(i.description, dl.sku) as description, dl.quantity_picked::float as quantity 
        FROM document_lines dl 
        LEFT JOIN items i ON UPPER(dl.sku) = UPPER(i.sku) 
        WHERE dl.document_id = $1 AND dl.quantity_picked > 0
        ORDER BY dl.sku ASC
    """, doc["id"])

    totals = await conn.fetchrow("""
        SELECT COALESCE(SUM(dl.quantity_requested * COALESCE(i.weight, 0)), 0)::float as calc_weight, 
               COALESCE(SUM(dl.quantity_requested * COALESCE(i.volume, 0)), 0)::float as calc_volume 
        FROM document_lines dl JOIN items i ON UPPER(dl.sku) = UPPER(i.sku) WHERE dl.document_id = $1
    """, doc["id"])
    
    return {"document": dict(doc), "lines": [dict(l) for l in lines], "totals": dict(totals)}

@router.post("/api/packing/orders/{document_number}/pack")
async def pack_order_and_dispatch(document_number: str, data: PackOrderInput, user: dict = Depends(get_current_user), conn: asyncpg.Connection = Depends(get_db_connection)):
    async with conn.transaction():
        doc = await conn.fetchrow("SELECT d.id, d.status, d.document_number, d.channel_origin, d.channel_label_zpl, COALESCE(c.company_name, d.buyer_name, 'Consumidor Final') as client_name, COALESCE(a.full_address, d.buyer_address, 'A coordinar') as delivery_address FROM documents d LEFT JOIN entities c ON d.customer_id = c.id LEFT JOIN entity_addresses a ON d.customer_address_id = a.id WHERE UPPER(d.document_number) = $1 FOR UPDATE OF d", document_number.strip().upper())
        if not doc: raise HTTPException(404, "Pedido no encontrado.")
        if doc["status"] == "DISPATCHED": raise HTTPException(400, "El pedido ya fue despachado.")
        if doc["status"] != "COMPLETED": raise HTTPException(400, "El pedido aún no está pickeado completamente.")

        packing_enabled = await conn.fetchval("SELECT value FROM system_settings WHERE key = 'enable_packing_station'")
        if packing_enabled == "true":
            if not data.packed_items:
                raise HTTPException(400, "La estación de empaque está activa. Debe enviar el detalle de los artículos escaneados por bulto.")
            
            db_lines = await conn.fetch("SELECT sku, quantity_picked FROM document_lines WHERE document_id = $1 AND quantity_picked > 0", doc["id"])
            expected_pack = {r["sku"].upper(): float(r["quantity_picked"]) for r in db_lines}
            
            actual_pack = {}
            for item in data.packed_items:
                sku_clean = item.sku.upper()
                actual_pack[sku_clean] = actual_pack.get(sku_clean, 0.0) + item.quantity
                
            for sku, qty in expected_pack.items():
                packed_qty = actual_pack.get(sku, 0.0)
                if packed_qty != qty:
                    raise HTTPException(400, f"Discrepancia en empaque para SKU {sku}. Pickeado: {qty}, Empacado: {packed_qty}")
                    
            for sku in actual_pack.keys():
                if sku not in expected_pack:
                    raise HTTPException(400, f"El SKU {sku} fue escaneado en empaque pero no pertenece al pedido pickeado.")

        totals = await conn.fetchrow("""
            SELECT COALESCE(SUM(dl.quantity_requested * COALESCE(i.weight, 0)), 0)::float as calc_weight, 
                   COALESCE(SUM(dl.quantity_requested * COALESCE(i.volume, 0)), 0)::float as calc_volume 
            FROM document_lines dl JOIN items i ON UPPER(dl.sku) = UPPER(i.sku) WHERE dl.document_id = $1
        """, doc["id"])

        await conn.execute("UPDATE documents SET status = 'DISPATCHED' WHERE id = $1", doc["id"])
        await emitir_estado_a_canal(conn, doc["id"])
        await log_action(conn, user.get("username"), "PACKING_DISPATCH", f"Empacó y despachó {document_number} ({data.boxes} bultos)")

        await imprimir_etiqueta_pedido(conn, doc)

        dispatch_payload = {
            "event": "order.dispatched",
            "order_number": doc["document_number"],
            "channel_origin": doc["channel_origin"],
            "client_name": doc["client_name"],
            "delivery_address": doc["delivery_address"],
            "boxes": data.boxes,
            "total_weight_kg": float(totals["calc_weight"]),
            "total_volume_m3": float(totals["calc_volume"]),
            "packed_boxes_detail": [item.dict() for item in data.packed_items] if data.packed_items else [],
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        await dispatch_event_to_channels(conn, "OUTBOUND_DESPACHO", dispatch_payload)
        return {"status": "success", "message": "Pedido verificado, empacado y despachado exitosamente."}