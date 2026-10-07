import uuid
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File
from fastapi.responses import Response
from pydantic import BaseModel
from typing import Optional, List
import asyncpg, csv
from io import StringIO
import logging

logger = logging.getLogger(__name__)

from backend.database import get_db_connection, require_admin, require_supervisor, queue_zpl_print_job, parse_uuid, log_action, get_client_ip
from backend.planillas import PlanillaInvalida, leer_planilla, valor
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

class ItemCreate(BaseModel):
    sku: str
    description: str
    category: Optional[str] = ""
    length: Optional[float] = 0.0
    width: Optional[float] = 0.0
    height: Optional[float] = 0.0
    weight: Optional[float] = 0.0


def _medida(texto: str) -> float:
    """Numero de una planilla: acepta coma decimal ("0,35", "1.234,5"). Vacio, negativo o invalido = 0."""
    t = (texto or "").strip()
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    try:
        return max(0.0, float(t)) if t else 0.0
    except ValueError:
        return 0.0


async def _leer_archivo(file: UploadFile):
    try:
        return leer_planilla(file.filename or "", await file.read())
    except PlanillaInvalida as e:
        raise HTTPException(400, str(e))


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

@router.post("/api/admin/items")
async def create_item(data: ItemCreate, request: Request, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    """Alta de un articulo desde el panel (el import sigue para cargar muchos)."""
    sku = data.sku.strip().upper()
    descripcion = data.description.strip()
    if not sku or len(sku) > 100:
        raise HTTPException(400, "El SKU es obligatorio (hasta 100 caracteres).")
    if not descripcion:
        raise HTTPException(400, "La descripción es obligatoria.")
    if await conn.fetchval("SELECT 1 FROM items WHERE UPPER(sku) = $1", sku):
        raise HTTPException(409, f"Ya existe un artículo con el SKU {sku}.")
    medidas = [max(0.0, float(x or 0)) for x in (data.length, data.width, data.height, data.weight)]
    volumen = (medidas[0] * medidas[1] * medidas[2]) / 1000000.0
    await conn.execute("""
        INSERT INTO items (sku, description, category, length, width, height, weight, volume)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
    """, sku, descripcion, (data.category or "").strip(), *medidas, volumen)
    await log_action(conn, admin["username"], "ITEM_CREATED", f"Artículo {sku} creado.", get_client_ip(request))
    return {"status": "success", "message": f"Artículo {sku} creado.", "sku": sku}


# Plantilla para importar articulos: separador ";" (lo que usa el Excel argentino) y filas de ejemplo.
PLANTILLA_ARTICULOS = (
    "sku;descripcion;categoria;largo_cm;ancho_cm;alto_cm;peso_kg\r\n"
    "TAZA-001;Taza de ceramica blanca 350 ml;Bazar;9;9;10;0,35\r\n"
    "PLATO-024;Plato playo 24 cm;Bazar;24;24;2;0,5\r\n"
)


@router.get("/api/admin/import/items/plantilla")
async def plantilla_articulos(admin: dict = Depends(require_admin)):
    return Response(content=("\ufeff" + PLANTILLA_ARTICULOS).encode("utf-8"), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="plantilla_articulos.csv"'})


@router.post("/api/admin/import/items")
async def import_items_csv(file: UploadFile = File(...), admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    """Importa articulos desde Excel (.xlsx), texto con tabulaciones (.txt/.tsv) o CSV (; o ,). Columnas: sku (o
    codigo), descripcion (o nombre), categoria, y opcionales largo_cm, ancho_cm, alto_cm, peso_kg. Un SKU que ya
    existe se actualiza. Informa las filas que no se pudieron cargar."""
    filas = await _leer_archivo(file)
    if not filas:
        raise HTTPException(400, "La planilla está vacía o no tiene encabezados (primera fila: sku, descripcion, categoria...).")
    if not any(valor(f, "sku", "codigo", "código") for f in filas[:50]):
        raise HTTPException(400, "No se encontró la columna sku (o codigo) en la primera fila de la planilla.")
    nuevos = actualizados = 0
    rechazadas = []
    async with conn.transaction():
        for n, fila in enumerate(filas, start=2):
            sku = valor(fila, "sku", "codigo", "código").upper()
            if not sku:
                rechazadas.append(f"fila {n}: sin SKU")
                continue
            if len(sku) > 100:
                rechazadas.append(f"fila {n}: SKU de más de 100 caracteres")
                continue
            desc = valor(fila, "descripcion", "description", "nombre") or sku
            cat = valor(fila, "categoria", "category", "rubro")
            medidas = [_medida(valor(fila, *c)) for c in (("largo_cm", "largo"), ("ancho_cm", "ancho"), ("alto_cm", "alto"), ("peso_kg", "peso"))]
            existia = await conn.fetchval("SELECT 1 FROM items WHERE UPPER(sku) = $1", sku)
            await conn.execute("""
                INSERT INTO items (sku, description, category, length, width, height, weight, volume)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                ON CONFLICT (sku) DO UPDATE SET description = EXCLUDED.description, category = EXCLUDED.category,
                    length = CASE WHEN EXCLUDED.length > 0 THEN EXCLUDED.length ELSE items.length END,
                    width = CASE WHEN EXCLUDED.width > 0 THEN EXCLUDED.width ELSE items.width END,
                    height = CASE WHEN EXCLUDED.height > 0 THEN EXCLUDED.height ELSE items.height END,
                    weight = CASE WHEN EXCLUDED.weight > 0 THEN EXCLUDED.weight ELSE items.weight END,
                    volume = CASE WHEN EXCLUDED.volume > 0 THEN EXCLUDED.volume ELSE items.volume END
            """, sku, desc, cat, *medidas, (medidas[0] * medidas[1] * medidas[2]) / 1000000.0)
            if existia:
                actualizados += 1
            else:
                nuevos += 1
    mensaje = f"Artículos nuevos: {nuevos}. Actualizados: {actualizados}."
    if rechazadas:
        mensaje += f" No se cargaron {len(rechazadas)} filas ({'; '.join(rechazadas[:5])}{'...' if len(rechazadas) > 5 else ''})."
    return {"status": "success", "message": mensaje, "nuevos": nuevos, "actualizados": actualizados, "rechazadas": rechazadas}

@router.post("/api/admin/import/item-locations")
async def import_item_locations_csv(file: UploadFile = File(...), admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    """Asigna articulos a ubicaciones fijas desde Excel, texto con tabulaciones o CSV (columnas sku y ubicacion)."""
    filas = await _leer_archivo(file)
    count = 0
    rechazadas = []
    async with conn.transaction():
        for n, fila in enumerate(filas, start=2):
            sku = valor(fila, "sku", "codigo_articulo").upper()
            loc_code = valor(fila, "ubicacion", "ubicación", "location_code").upper()
            if not sku or not loc_code:
                rechazadas.append(f"fila {n}: falta sku o ubicacion")
                continue
            loc = await conn.fetchrow("SELECT id FROM locations WHERE UPPER(location_code) = $1", loc_code)
            if not loc:
                rechazadas.append(f"fila {n}: la ubicación {loc_code} no existe")
                continue
            await conn.execute("INSERT INTO item_locations (item_sku, location_id) VALUES ($1, $2) ON CONFLICT DO NOTHING", sku, loc["id"])
            count += 1
    mensaje = f"Se asignaron {count} ubicaciones."
    if rechazadas:
        mensaje += f" No se cargaron {len(rechazadas)} filas ({'; '.join(rechazadas[:5])}{'...' if len(rechazadas) > 5 else ''})."
    return {"status": "success", "message": mensaje, "rechazadas": rechazadas}

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