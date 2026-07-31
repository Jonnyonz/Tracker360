from fastapi import APIRouter, Depends, HTTPException
from typing import Optional
import asyncpg, uuid

try:
    from backend.database import get_db_connection, require_admin
except ImportError:
    from database import get_db_connection, require_admin

router = APIRouter(tags=["Reports"])

@router.get("/api/admin/reports/stock")
async def report_stock(
    sku: Optional[str] = None,
    branch_id: Optional[str] = None,
    sector_id: Optional[str] = None,
    admin: dict = Depends(require_admin), 
    conn: asyncpg.Connection = Depends(get_db_connection)
):
    # Consulta robusta con cálculos logísticos (peso y volumen)
    query = """
        SELECT si.sku, COALESCE(i.description, 'Sin descripción') as description,
               b.name as branch_name, sec.name as sector_name, 
               COALESCE(l.location_code, 'Sin ubicación') as location_code, 
               si.quantity::float as quantity,
               (COALESCE(i.weight, 0) * si.quantity)::float as total_weight_kg,
               (COALESCE(i.volume, 0) * si.quantity)::float as total_volume_m3,
               si.updated_at
        FROM stock_inventory si
        LEFT JOIN items i ON si.sku = i.sku
        LEFT JOIN branches b ON si.branch_id = b.id
        LEFT JOIN sectors sec ON si.sector_id = sec.id
        LEFT JOIN locations l ON si.location_id = l.id
        WHERE si.quantity > 0
    """
    params = []
    param_idx = 1

    # Constructor dinámico de filtros (Parameter Binding)
    if sku:
        query += f" AND (si.sku ILIKE ${param_idx} OR i.description ILIKE ${param_idx})"
        params.append(f"%{sku.strip()}%")
        param_idx += 1
    
    if branch_id:
        try:
            b_uuid = uuid.UUID(branch_id)
            query += f" AND si.branch_id = ${param_idx}"
            params.append(b_uuid)
            param_idx += 1
        except ValueError:
            pass
            
    if sector_id:
        try:
            s_uuid = uuid.UUID(sector_id)
            query += f" AND si.sector_id = ${param_idx}"
            params.append(s_uuid)
            param_idx += 1
        except ValueError:
            pass

    query += " ORDER BY b.name ASC, sec.name ASC, si.sku ASC"
    
    try:
        rows = await conn.fetch(query, *params)
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"Error procesando Reporte de Stock: {e}")
        raise HTTPException(status_code=400, detail="Error al generar el reporte de stock.")
