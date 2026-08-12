from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional, List
import asyncpg

try:
    from backend.database import get_db_connection, get_current_user, require_admin, log_action
except ImportError:
    from database import get_db_connection, get_current_user, require_admin, log_action

router = APIRouter(prefix="/api/rfid", tags=["RFID Operations"])

class RFIDTagMapping(BaseModel):
    epc: str
    sku: str

class RFIDBulkMappingInput(BaseModel):
    mappings: List[RFIDTagMapping]

class RFIDBulkScanInput(BaseModel):
    epcs: List[str]
    location_code: Optional[str] = None

# === MAESTRO Y VINCULACIÓN DE ETIQUETAS RFID ===

@router.get("/tags")
async def list_rfid_tags(sku: str = "", page: int = 1, limit: int = 50, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    offset = (page - 1) * limit
    total_count = await conn.fetchval("SELECT COUNT(*) FROM rfid_tags WHERE sku ILIKE $1", f"%{sku}%")
    
    rows = await conn.fetch("""
        SELECT r.epc, r.sku, i.description, r.created_at, r.created_by
        FROM rfid_tags r
        LEFT JOIN items i ON UPPER(r.sku) = UPPER(i.sku)
        WHERE r.sku ILIKE $1
        ORDER BY r.created_at DESC
        LIMIT $2 OFFSET $3
    """, f"%{sku}%", limit, offset)
    
    return {
        "tags": [dict(r) for r in rows],
        "total_count": total_count,
        "page": page,
        "limit": limit
    }

@router.post("/tags")
async def create_or_update_rfid_mapping(data: RFIDTagMapping, user: dict = Depends(get_current_user), conn: asyncpg.Connection = Depends(get_db_connection)):
    clean_epc = data.epc.strip().upper()
    clean_sku = data.sku.strip().upper()

    item_exists = await conn.fetchval("SELECT COUNT(*) FROM items WHERE UPPER(sku) = $1", clean_sku)
    if not item_exists:
        raise HTTPException(status_code=404, detail=f"El SKU '{clean_sku}' no existe en el maestro de artículos.")

    await conn.execute("""
        INSERT INTO rfid_tags (epc, sku, created_by)
        VALUES ($1, $2, $3)
        ON CONFLICT (epc) DO UPDATE SET sku = EXCLUDED.sku, created_by = EXCLUDED.created_by
    """, clean_epc, clean_sku, user["username"])

    return {"status": "success", "message": f"Tag EPC '{clean_epc}' vinculado al SKU '{clean_sku}'."}

@router.get("/tags/{epc}")
async def resolve_rfid_tag(epc: str, user: dict = Depends(get_current_user), conn: asyncpg.Connection = Depends(get_db_connection)):
    clean_epc = epc.strip().upper()
    tag = await conn.fetchrow("""
        SELECT r.epc, r.sku, i.description, i.category,
               COALESCE((SELECT SUM(quantity) FROM stock_inventory WHERE UPPER(sku) = UPPER(r.sku)), 0)::float as total_stock
        FROM rfid_tags r
        LEFT JOIN items i ON UPPER(r.sku) = UPPER(i.sku)
        WHERE UPPER(r.epc) = $1
    """, clean_epc)

    if not tag:
        raise HTTPException(status_code=404, detail=f"El chip RFID EPC '{clean_epc}' no se encuentra registrado en el sistema.")

    return dict(tag)

@router.delete("/tags/{epc}")
async def delete_rfid_mapping(epc: str, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    clean_epc = epc.strip().upper()
    res = await conn.execute("DELETE FROM rfid_tags WHERE UPPER(epc) = $1", clean_epc)
    if res == "DELETE 0":
        raise HTTPException(status_code=404, detail="Etiqueta RFID no encontrada.")
    return {"status": "success", "message": "Enlace RFID eliminado."}

# === MOTOR DE ABSORCIÓN MASIVA RFID (BULK SCAN) ===

@router.post("/bulk-scan")
async def process_rfid_bulk_scan(data: RFIDBulkScanInput, user: dict = Depends(get_current_user), conn: asyncpg.Connection = Depends(get_db_connection)):
    if not data.epcs:
        raise HTTPException(status_code=400, detail="Debe enviar al menos un código EPC para procesar.")

    clean_epcs = list(set([e.strip().upper() for e in data.epcs if e and e.strip()]))
    
    rows = await conn.fetch("""
        SELECT r.epc, r.sku, i.description
        FROM rfid_tags r
        LEFT JOIN items i ON UPPER(r.sku) = UPPER(i.sku)
        WHERE UPPER(r.epc) = ANY($1::text[])
    """, clean_epcs)

    resolved_map = {r["epc"]: dict(r) for r in rows}
    
    counts_by_sku = {}
    unknown_epcs = []

    for epc in clean_epcs:
        if epc in resolved_map:
            sku = resolved_map[epc]["sku"]
            desc = resolved_map[epc]["description"] or sku
            if sku not in counts_by_sku:
                counts_by_sku[sku] = {"sku": sku, "description": desc, "detected_units": 0, "epcs": []}
            counts_by_sku[sku]["detected_units"] += 1
            counts_by_sku[sku]["epcs"].append(epc)
        else:
            unknown_epcs.append(epc)

    summary_list = list(counts_by_sku.values())

    return {
        "status": "success",
        "total_tags_read": len(clean_epcs),
        "resolved_skus_count": len(summary_list),
        "unknown_epcs_count": len(unknown_epcs),
        "summary_by_sku": summary_list,
        "unknown_epcs": unknown_epcs
    }