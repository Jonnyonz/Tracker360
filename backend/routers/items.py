import uuid
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel
from typing import Optional, List
import asyncpg, csv
from io import StringIO
import logging

logger = logging.getLogger(__name__)

from backend.database import get_db_connection, require_admin, require_supervisor, queue_zpl_print_job, parse_uuid
from backend.routers.printing import zpl_etiqueta_articulo

router = APIRouter(tags=["Items"])

class ComboComponentLine(BaseModel):
    component_sku: str
    quantity: float

class ItemUpdate(BaseModel):
    description: Optional[str] = None
    category: Optional[str] = None
    length: Optional[float] = 0.0
    width: Optional[float] = 0.0
    height: Optional[float] = 0.0
    weight: Optional[float] = 0.0
    volume: Optional[float] = 0.0
    is_combo: Optional[bool] = False
    components: Optional[List[ComboComponentLine]] = None

# Tope de etiquetas por pedido de impresion: una cantidad sin limite podia encolar millones de
# trabajos de una sola vez.
MAX_ETIQUETAS_POR_PEDIDO = 5000

class BatchItemPrintLine(BaseModel):
    sku: str
    quantity: int

class BatchItemPrintInput(BaseModel):
    queue_code: str
    items: List[BatchItemPrintLine]

class ItemLocationInput(BaseModel):
    sku: str
    location_code: str

@router.get("/api/admin/items")
async def list_items(sku: str = "", description: str = "", category: str = "", location: str = "", combo: str = "", stock: str = "",
                     page: int = 1, limit: int = 50, sort_by: str = "sku", sort_order: str = "ASC", admin: dict = Depends(require_supervisor), conn: asyncpg.Connection = Depends(get_db_connection)):
    """Filtros opcionales: sku, description, category y location (codigo de ubicacion asignada) por texto;
    combo SI | NO; stock CON (> 0) | SIN (= 0) | NEGATIVO (< 0). page desde 1, limit de 1 a 200."""
    page = max(1, int(page))
    limit = max(1, min(int(limit), 200))
    offset = (page - 1) * limit
    allowed_cols = {"sku": "i.sku", "description": "i.description", "category": "i.category", "total_stock": "total_stock"}
    col = allowed_cols.get(sort_by, "i.sku")
    order = "DESC" if sort_order.upper() == "DESC" else "ASC"

    stock_sql = "COALESCE((SELECT SUM(si.quantity) FROM stock_inventory si WHERE si.sku = i.sku), 0)"
    filtros, args = ["i.sku ILIKE $1", "i.description ILIKE $2"], [f"%{sku}%", f"%{description}%"]
    if category.strip():
        args.append(f"%{category.strip()}%")
        filtros.append(f"COALESCE(i.category, '') ILIKE ${len(args)}")
    if location.strip():
        args.append(f"%{location.strip()}%")
        filtros.append(f"""EXISTS (SELECT 1 FROM item_locations il2 JOIN locations l2 ON il2.location_id = l2.id
                           WHERE il2.item_sku = i.sku AND l2.location_code ILIKE ${len(args)})""")
    tipo = combo.strip().upper()
    if tipo:
        if tipo not in ("SI", "NO"):
            raise HTTPException(status_code=400, detail="combo inválido: SI o NO.")
        filtros.append("COALESCE(i.is_combo, FALSE)" if tipo == "SI" else "NOT COALESCE(i.is_combo, FALSE)")
    con_stock = stock.strip().upper()
    if con_stock:
        condicion = {"CON": "> 0", "SIN": "= 0", "NEGATIVO": "< 0"}.get(con_stock)
        if not condicion:
            raise HTTPException(status_code=400, detail="stock inválido: CON, SIN o NEGATIVO.")
        filtros.append(f"{stock_sql} {condicion}")
    where = " AND ".join(filtros)

    total_count = await conn.fetchval(f"SELECT COUNT(*) FROM items i WHERE {where}", *args)

    q = f"""
        SELECT i.sku, i.description, i.category, i.length, i.width, i.height, i.weight, i.volume, COALESCE(i.is_combo, FALSE) as is_combo,
               COALESCE((SELECT string_agg(l.location_code, ', ') FROM item_locations il JOIN locations l ON il.location_id = l.id WHERE il.item_sku = i.sku), 'Sin asignación') as locations_summary,
               {stock_sql}::float as total_stock
        FROM items i
        WHERE {where}
        ORDER BY {col} {order}
        LIMIT ${len(args) + 1} OFFSET ${len(args) + 2}
    """
    rows = await conn.fetch(q, *args, limit, offset)
    total_pages = (total_count + limit - 1) // limit if total_count > 0 else 1
    return {
        "items": [dict(r) for r in rows],
        "total_count": total_count,
        "page": page,
        "limit": limit,
        "total_pages": total_pages
    }

@router.get("/api/admin/items/{sku}/combo")
async def get_item_combo_components(sku: str, admin: dict = Depends(require_supervisor), conn: asyncpg.Connection = Depends(get_db_connection)):
    clean_sku = sku.strip().upper()
    item = await conn.fetchrow("SELECT sku, description, COALESCE(is_combo, FALSE) as is_combo FROM items WHERE UPPER(sku) = $1", clean_sku)
    if not item:
        raise HTTPException(status_code=404, detail="Artículo no encontrado.")
    
    rows = await conn.fetch("""
        SELECT ic.id::text, ic.component_sku, i.description as component_description, ic.quantity::float as quantity
        FROM item_combos ic
        JOIN items i ON UPPER(ic.component_sku) = UPPER(i.sku)
        WHERE UPPER(ic.combo_sku) = $1
        ORDER BY ic.component_sku ASC
    """, clean_sku)
    
    return {
        "combo_sku": item["sku"],
        "description": item["description"],
        "is_combo": item["is_combo"],
        "components": [dict(r) for r in rows]
    }

@router.get("/api/admin/items/{sku}/stock-breakdown")
async def get_item_stock_breakdown(sku: str, admin: dict = Depends(require_supervisor), conn: asyncpg.Connection = Depends(get_db_connection)):
    rows = await conn.fetch("""
        SELECT b.name as branch_name, sec.name as sector_name, 
               COALESCE(l.location_code, 'Ubicación General') as location_code, 
               si.quantity::float as quantity
        FROM stock_inventory si
        LEFT JOIN branches b ON si.branch_id = b.id
        LEFT JOIN sectors sec ON si.sector_id = sec.id
        LEFT JOIN locations l ON si.location_id = l.id
        WHERE UPPER(si.sku) = $1 AND si.quantity > 0
        ORDER BY b.name ASC, sec.name ASC, l.location_code ASC
    """, sku.strip().upper())
    return [dict(r) for r in rows]

@router.put("/api/admin/items/{sku}")
async def update_item(sku: str, data: ItemUpdate, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    clean_sku = sku.strip().upper()
    calculated_vol = (data.length * data.width * data.height) / 1000000.0 if (data.length and data.width and data.height) else (data.volume or 0.0)
    
    async with conn.transaction():
        # 1. Actualizar los datos básicos del artículo
        await conn.execute("""
            UPDATE items 
            SET description = $1, category = $2, length = COALESCE($3, length), width = COALESCE($4, width), height = COALESCE($5, height), weight = COALESCE($6, weight), volume = $7, is_combo = COALESCE($8, is_combo) 
            WHERE UPPER(sku) = $9
        """, data.description, data.category, data.length, data.width, data.height, data.weight, calculated_vol, data.is_combo, clean_sku)
        
        # 2. Limpiar la tabla de combos (por si desmarcó el checkbox o para sobrescribir)
        await conn.execute("DELETE FROM item_combos WHERE UPPER(combo_sku) = $1", clean_sku)
        
        # 3. Insertar los componentes si es combo y se enviaron datos
        if data.is_combo and data.components:
            valid_count = 0
            for comp in data.components:
                comp_sku = comp.component_sku.strip().upper()
                if comp_sku == clean_sku:
                    continue # Evitar bucles infinitos (el combo no puede incluirse a sí mismo)
                
                comp_exists = await conn.fetchval("SELECT COUNT(*) FROM items WHERE UPPER(sku) = $1", comp_sku)
                if comp_exists and comp.quantity > 0:
                    await conn.execute("""
                        INSERT INTO item_combos (combo_sku, component_sku, quantity)
                        VALUES ($1, $2, $3)
                        ON CONFLICT (combo_sku, component_sku) DO UPDATE SET quantity = EXCLUDED.quantity
                    """, clean_sku, comp_sku, comp.quantity)
                    valid_count += 1
            
            # Si marcó "Es Combo" pero ningún componente era válido, revertimos la marca por integridad de base de datos
            if valid_count == 0:
                await conn.execute("UPDATE items SET is_combo = FALSE WHERE UPPER(sku) = $1", clean_sku)

    return {"status": "success", "message": "Ficha de artículo y configuración de combo actualizados."}

@router.get("/api/admin/items/{sku}/locations")
async def get_item_locations(sku: str, admin: dict = Depends(require_supervisor), conn: asyncpg.Connection = Depends(get_db_connection)):
    rows = await conn.fetch("""
        SELECT il.id as assignment_id, l.location_code, s.name as sector_name, b.name as branch_name
        FROM item_locations il
        JOIN locations l ON il.location_id = l.id
        JOIN sectors s ON l.sector_id = s.id
        LEFT JOIN branches b ON s.branch_id = b.id
        WHERE UPPER(il.item_sku) = $1
    """, sku.strip().upper())
    return [dict(r) for r in rows]

@router.post("/api/admin/item-locations")
async def add_item_location(data: ItemLocationInput, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    loc = await conn.fetchrow("SELECT id FROM locations WHERE UPPER(location_code) = $1", data.location_code.strip().upper())
    if not loc: raise HTTPException(404, "Ubicación no encontrada.")
    await conn.execute("INSERT INTO item_locations (item_sku, location_id) VALUES ($1, $2) ON CONFLICT DO NOTHING", data.sku.strip().upper(), loc["id"])
    return {"status": "success"}

@router.delete("/api/admin/item-locations/{assignment_id}")
async def delete_item_location(assignment_id: str, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    await conn.execute("DELETE FROM item_locations WHERE id = $1", parse_uuid(assignment_id))
    return {"status": "success"}

@router.post("/api/admin/import/items")
async def import_items_csv(file: UploadFile = File(...), admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    content = await file.read()
    text = content.decode('utf-8-sig', errors='ignore')
    reader = csv.DictReader(StringIO(text))
    count = 0
    async with conn.transaction():
        for row in reader:
            sku = row.get("sku") or row.get("SKU") or row.get("codigo")
            desc = row.get("description") or row.get("descripcion") or row.get("nombre") or sku
            cat = row.get("category") or row.get("categoria") or ""
            if sku and sku.strip():
                await conn.execute("""
                    INSERT INTO items (sku, description, category) VALUES ($1, $2, $3)
                    ON CONFLICT (sku) DO UPDATE SET description = EXCLUDED.description, category = EXCLUDED.category
                """, sku.strip().upper(), desc.strip(), cat.strip())
                count += 1
    return {"status": "success", "message": f"Se procesaron {count} artículos."}

@router.post("/api/admin/import/item-locations")
async def import_item_locations_csv(file: UploadFile = File(...), admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    content = await file.read()
    text = content.decode('utf-8-sig', errors='ignore')
    reader = csv.DictReader(StringIO(text))
    count = 0
    async with conn.transaction():
        for row in reader:
            sku = row.get("sku") or row.get("SKU")
            loc_code = row.get("ubicacion") or row.get("location_code") or row.get("codigo")
            if sku and loc_code:
                loc = await conn.fetchrow("SELECT id FROM locations WHERE UPPER(location_code) = $1", loc_code.strip().upper())
                if loc:
                    await conn.execute("INSERT INTO item_locations (item_sku, location_id) VALUES ($1, $2) ON CONFLICT DO NOTHING", sku.strip().upper(), loc["id"])
                    count += 1
    return {"status": "success", "message": f"Se asignaron {count} ubicaciones."}

@router.post("/api/admin/items/batch-print-labels")
async def batch_print_items_labels(req: dict, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
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
                    if len(skus) + max(qty, 0) > MAX_ETIQUETAS_POR_PEDIDO:
                        raise HTTPException(400, f"Máximo {MAX_ETIQUETAS_POR_PEDIDO} etiquetas por pedido de impresión.")
                    skus.extend([str(it["sku"]).strip()] * qty)
                elif isinstance(it, str):
                    skus.append(it.strip())

        if not skus:
            raise HTTPException(status_code=400, detail="Debe proporcionar al menos un SKU.")
        if len(skus) > MAX_ETIQUETAS_POR_PEDIDO:
            raise HTTPException(400, f"Máximo {MAX_ETIQUETAS_POR_PEDIDO} etiquetas por pedido de impresión.")

        template_row = await conn.fetchrow("SELECT value FROM system_settings WHERE key = 'zpl_item_template'")
        custom_tpl = template_row["value"] if template_row and template_row["value"] else None

        inserted = 0
        for sku in skus:
            clean_sku = str(sku).strip().upper()
            if not clean_sku: continue

            item_row = await conn.fetchrow("SELECT description FROM items WHERE UPPER(sku) = $1 LIMIT 1", clean_sku)
            clean_desc = item_row["description"] if item_row and item_row["description"] else clean_sku
            short_desc = clean_desc[:22]

            zpl = zpl_etiqueta_articulo(custom_tpl, clean_sku, short_desc)

            await conn.execute("""
                INSERT INTO print_jobs (id, queue_code, zpl_content, status, created_at)
                VALUES ($1, $2, $3, 'PENDING', NOW())
            """, str(uuid.uuid4()), queue_code, zpl)
            inserted += 1

        return {"status": "ok", "jobs_created": inserted, "queue_code": queue_code}

    except HTTPException as he:
        raise he
    except Exception as e:
        # El detalle queda en el log del servidor; al cliente solo un mensaje generico.
        logger.exception(f"[BATCH PRINT ERROR]: {e!r}")
        raise HTTPException(status_code=500, detail="No se pudieron generar las etiquetas.")