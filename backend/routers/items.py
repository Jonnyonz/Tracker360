from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel
from typing import Optional, List
import asyncpg, uuid, csv
from io import StringIO

try:
    from backend.database import get_db_connection, require_admin, queue_zpl_print_job
except ImportError:
    from database import get_db_connection, require_admin, queue_zpl_print_job

router = APIRouter(tags=["Items"])

class ItemUpdate(BaseModel):
    description: Optional[str] = None
    category: Optional[str] = None
    length: Optional[float] = 0.0
    width: Optional[float] = 0.0
    height: Optional[float] = 0.0
    weight: Optional[float] = 0.0
    volume: Optional[float] = 0.0

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
async def list_items(sku: str = "", description: str = "", page: int = 1, limit: int = 50, sort_by: str = "sku", sort_order: str = "ASC", admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    offset = (page - 1) * limit
    allowed_cols = {"sku": "i.sku", "description": "i.description", "category": "i.category"}
    col = allowed_cols.get(sort_by, "i.sku")
    order = "DESC" if sort_order.upper() == "DESC" else "ASC"
    
    total_count = await conn.fetchval("SELECT COUNT(*) FROM items WHERE sku ILIKE $1 AND description ILIKE $2", f"%{sku}%", f"%{description}%")
    q = f"""
        SELECT i.sku, i.description, i.category, i.length, i.width, i.height, i.weight, i.volume, 
               COALESCE((SELECT string_agg(l.location_code, ', ') FROM item_locations il JOIN locations l ON il.location_id = l.id WHERE il.item_sku = i.sku), 'Sin asignación') as locations_summary 
        FROM items i 
        WHERE i.sku ILIKE $1 AND i.description ILIKE $2 
        ORDER BY {col} {order} 
        LIMIT $3 OFFSET $4
    """
    rows = await conn.fetch(q, f"%{sku}%", f"%{description}%", limit, offset)
    total_pages = (total_count + limit - 1) // limit if total_count > 0 else 1
    return {
        "items": [dict(r) for r in rows],
        "total_count": total_count,
        "page": page,
        "limit": limit,
        "total_pages": total_pages
    }

@router.put("/api/admin/items/{sku}")
async def update_item(sku: str, data: ItemUpdate, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    calculated_vol = (data.length * data.width * data.height) / 1000000.0 if (data.length and data.width and data.height) else (data.volume or 0.0)
    await conn.execute("""
        UPDATE items 
        SET description = $1, category = $2, length = COALESCE($3, length), width = COALESCE($4, width), height = COALESCE($5, height), weight = COALESCE($6, weight), volume = $7 
        WHERE UPPER(sku) = $8
    """, data.description, data.category, data.length, data.width, data.height, data.weight, calculated_vol, sku.upper())
    return {"status": "success", "message": "Ficha de artículo actualizada."}

@router.post("/api/admin/items/batch-print-labels")
async def batch_print_item_labels(data: BatchItemPrintInput, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    if not data.items or len(data.items) == 0:
        raise HTTPException(400, "Debe agregar al menos un artículo para imprimir.")

    template = await conn.fetchval("SELECT value FROM system_settings WHERE key = 'zpl_item_template'")
    if not template:
        template = "^XA^FO50,30^A0N,30,30^FD{description}^FS^FO50,70^A0N,25,25^FDSKU: {sku}^FS^FO50,110^BY2^BCN,80,Y,N,N^FD{sku}^FS^XZ"

    total_queued = 0
    async with conn.transaction():
        for line in data.items:
            sku_clean = line.sku.strip().upper()
            qty = max(1, line.quantity)
            item = await conn.fetchrow("SELECT sku, description FROM items WHERE UPPER(sku) = $1", sku_clean)
            desc = item["description"] if item else f"Art. {sku_clean}"

            zpl = template.replace("{sku}", sku_clean).replace("{description}", desc)
            for _ in range(qty):
                await queue_zpl_print_job(conn, data.queue_code, zpl)
                total_queued += 1

    return {"status": "success", "message": f"Se enviaron {total_queued} etiquetas a la cola {data.queue_code}."}

@router.get("/api/admin/items/{sku}/locations")
async def get_item_locations(sku: str, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
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
    await conn.execute("DELETE FROM item_locations WHERE id = $1", uuid.UUID(assignment_id))
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
