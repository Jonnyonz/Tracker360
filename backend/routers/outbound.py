from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timezone
import asyncpg, uuid, json

try:
    from backend.database import get_db_connection, get_current_user, require_admin, record_stock_movement, log_action, dispatch_event_to_channels, queue_zpl_print_job, check_idempotency, save_idempotency
except ImportError:
    from database import get_db_connection, get_current_user, require_admin, record_stock_movement, log_action, dispatch_event_to_channels, queue_zpl_print_job, check_idempotency, save_idempotency

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
    document_number: str
    customer_tax_id: str
    customer_name: Optional[str] = None
    address_label: str = "Principal"
    lines: List[ManualOrderLine]

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

    routing_enabled = await conn.fetchval("SELECT value FROM system_settings WHERE key = 'enable_optimal_routing'") == "true"
    if routing_enabled:
        result_lines.sort(key=lambda x: (x["sort_key"], x["sku"]))
    
    return {"document": dict(doc), "lines": result_lines}

@router.post("/api/picking/orders/{document_number}/scan")
async def scan_picking_item(document_number: str, data: PickScanInput, user: dict = Depends(get_current_user), conn: asyncpg.Connection = Depends(get_db_connection)):
    async with conn.transaction():
        doc = await conn.fetchrow("SELECT id, status FROM documents WHERE UPPER(document_number) = $1 FOR UPDATE", document_number.strip().upper())
        if not doc: raise HTTPException(404, "Pedido no encontrado.")
        if doc["status"] == "COMPLETED": raise HTTPException(400, "El pedido ya se encuentra completado.")
        
        sku_clean = data.sku.strip().upper()
        line = await conn.fetchrow("SELECT id, quantity_requested::float, quantity_picked::float FROM document_lines WHERE document_id = $1 AND UPPER(sku) = $2", doc["id"], sku_clean)
        if not line: raise HTTPException(400, f"El SKU '{sku_clean}' no pertenece a este pedido.")
        
        needed = line["quantity_requested"] - line["quantity_picked"]
        if needed <= 0: raise HTTPException(400, f"El SKU '{sku_clean}' ya fue recolectado totalmente.")

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
            if float(avail or 0) < data.quantity: raise HTTPException(400, f"Stock insuficiente en la ubicación (Disponible: {avail}).")

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
        
        return {"status": "success", "message": f"Extraído {data.quantity} un de {sku_clean}", "order_completed": order_completed, "remaining_lines": pending}

@router.get("/api/picking/waves/pending")
async def get_wave_picking_pending(limit: int = 5, user: dict = Depends(get_current_user), conn: asyncpg.Connection = Depends(get_db_connection)):
    enabled = await conn.fetchval("SELECT value FROM system_settings WHERE key = 'enable_wave_picking'")
    if enabled != "true": raise HTTPException(400, "Picking por Olas inactivo en la Configuración Enterprise.")
        
    orders = await conn.fetch("SELECT id, document_number FROM documents WHERE status IN ('PENDING', 'IN_PROGRESS') AND document_type = 'PICKING' ORDER BY created_at ASC LIMIT $1", limit)
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
        locs = await conn.fetch("SELECT l.location_code, si.quantity::float FROM stock_inventory si JOIN locations l ON si.location_id = l.id WHERE UPPER(si.sku) = $1 AND si.quantity > 0", l["sku"].strip().upper())
        
        if locs:
            ldict["suggested_locations"] = " | ".join([f"{loc['location_code']} ({loc['quantity']})" for loc in locs])
            ldict["sort_key"] = locs[0]["location_code"]
        else:
            ldict["suggested_locations"] = "Sin ubicación asignada o stock agotado"
            ldict["sort_key"] = "ZZZZZ"
            
        result_lines.append(ldict)
        
    if routing_enabled: result_lines.sort(key=lambda x: (x["sort_key"], x["sku"]))
        
    return {"order_numbers": order_nums, "lines": result_lines}

@router.post("/api/picking/waves/scan")
async def scan_wave_picking_item(data: WavePickScanInput, user: dict = Depends(get_current_user), conn: asyncpg.Connection = Depends(get_db_connection)):
    async with conn.transaction():
        sku_clean = data.sku.strip().upper()
        qty_to_distribute = float(data.quantity)
        
        snt_enabled = await conn.fetchval("SELECT value FROM system_settings WHERE key = 'enable_serial_tracking'")
        if snt_enabled == "true" and data.serial_numbers and len(data.serial_numbers) > 0:
            if len(data.serial_numbers) != int(data.quantity): raise HTTPException(400, "Descuadre en trazabilidad. La cantidad de números de serie debe coincidir con la cantidad física extraída.")
        
        docs = await conn.fetch("SELECT id, document_number, status FROM documents WHERE document_number = ANY($1::text[]) FOR UPDATE", data.order_numbers)
        if not docs: raise HTTPException(404, "Pedidos de la ola no encontrados.")
        
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
            if float(avail or 0) < data.quantity: raise HTTPException(400, f"Stock insuficiente en la ubicación (Disponible: {avail}).")

        if branch_id and sector_id:
            await record_stock_movement(conn, sku_clean, branch_id, sector_id, loc_id, -data.quantity, 'OUT_PICKING', "WAVE-PICK", user.get("username"), serial_numbers=data.serial_numbers)

        auto_complete = await conn.fetchval("SELECT value FROM system_settings WHERE key = 'auto_complete_picking'") or "true"
        wave_completed = True
        
        for doc in docs:
            pending = await conn.fetchval("SELECT COUNT(*) FROM document_lines WHERE document_id = $1 AND quantity_picked < quantity_requested", doc["id"])
            if pending == 0 and auto_complete == "true":
                await conn.execute("UPDATE documents SET status = 'COMPLETED' WHERE id = $1", doc["id"])
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
        doc = await conn.fetchrow("SELECT d.id, d.status, d.document_number, d.channel_origin, COALESCE(c.company_name, 'Consumidor Final') as client_name, COALESCE(a.full_address, 'A coordinar') as delivery_address FROM documents d LEFT JOIN entities c ON d.customer_id = c.id LEFT JOIN entity_addresses a ON d.customer_address_id = a.id WHERE UPPER(d.document_number) = $1 FOR UPDATE", document_number.strip().upper())
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
        await log_action(conn, user.get("username"), "PACKING_DISPATCH", f"Empacó y despachó {document_number} ({data.boxes} bultos)")

        template = await conn.fetchval("SELECT value FROM system_settings WHERE key = 'zpl_order_template'")
        if template:
            zpl = template.replace("{order_number}", doc["document_number"]).replace("{client_name}", doc["client_name"]).replace("{delivery_address}", doc["delivery_address"])
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
            "packed_boxes_detail": [item.dict() for item in data.packed_items] if data.packed_items else [],
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        await dispatch_event_to_channels(conn, "OUTBOUND_DESPACHO", dispatch_payload)
        return {"status": "success", "message": "Pedido verificado, empacado y despachado exitosamente."}