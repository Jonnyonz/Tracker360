from fastapi import APIRouter, Depends, HTTPException
from typing import Optional
from datetime import datetime
import asyncpg, uuid

try:
    from backend.database import get_db_connection, require_admin
except ImportError:
    from database import get_db_connection, require_admin

router = APIRouter(tags=["Reports"])

# === 1. REPORTE DE STOCK CONSOLIDADO ===
@router.get("/api/admin/reports/stock")
async def report_stock(
    sku: Optional[str] = None,
    branch_id: Optional[str] = None,
    sector_id: Optional[str] = None,
    include_zero: str = "false",
    include_negative: str = "false",
    admin: dict = Depends(require_admin), 
    conn: asyncpg.Connection = Depends(get_db_connection)
):
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
        WHERE 1=1
    """
    params = []
    param_idx = 1

    inc_zero = include_zero.lower() == "true"
    inc_neg = include_negative.lower() == "true"

    if not inc_zero and not inc_neg:
        query += " AND si.quantity > 0"
    elif inc_zero and not inc_neg:
        query += " AND si.quantity >= 0"
    elif not inc_zero and inc_neg:
        query += " AND (si.quantity > 0 OR si.quantity < 0)"

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

# === 2. REPORTE DE PEDIDOS DE VENTA (SALIDAS) ===
@router.get("/api/admin/reports/orders")
async def report_orders(
    document_number: Optional[str] = None,
    customer: Optional[str] = None,
    sku: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    related_only: str = "false",
    admin: dict = Depends(require_admin), 
    conn: asyncpg.Connection = Depends(get_db_connection)
):
    query = """
        SELECT d.document_number, d.created_at, d.status,
               COALESCE(e.company_name, 'Consumidor Final') as customer_name,
               COALESCE(e.tax_id, 'N/A') as customer_tax_id,
               COALESCE(d.channel_origin, 'NO VINCULADO') as related_document,
               COALESCE((SELECT SUM(quantity_picked) * 100.0 / NULLIF(SUM(quantity_requested), 0) 
                         FROM document_lines WHERE document_id = d.id), 0)::int as progress_pct
        FROM documents d
        LEFT JOIN entities e ON d.customer_id = e.id
        WHERE 1=1
    """
    params = []
    param_idx = 1

    if document_number:
        query += f" AND d.document_number ILIKE ${param_idx}"
        params.append(f"%{document_number.strip()}%")
        param_idx += 1

    if customer:
        query += f" AND (e.company_name ILIKE ${param_idx} OR e.tax_id ILIKE ${param_idx})"
        params.append(f"%{customer.strip()}%")
        param_idx += 1

    if status:
        query += f" AND d.status = ${param_idx}"
        params.append(status.strip().upper())
        param_idx += 1

    # Filtro Dinámico: Solo vinculados (excluimos creación MANUAL sin origen)
    if related_only.lower() == "true":
        query += " AND d.channel_origin IS NOT NULL AND d.channel_origin != 'MANUAL'"

    # Subconsulta EXISTS para filtrar por SKU contenido sin duplicar las filas de cabecera
    if sku:
        query += f" AND EXISTS (SELECT 1 FROM document_lines dl WHERE dl.document_id = d.id AND dl.sku ILIKE ${param_idx})"
        params.append(f"%{sku.strip()}%")
        param_idx += 1

    # Motor de Fechas (Estabilidad Brutal)
    if date_from:
        try:
            dt_from = datetime.strptime(f"{date_from.strip()} 00:00:00", "%Y-%m-%d %H:%M:%S")
            query += f" AND d.created_at >= ${param_idx}"
            params.append(dt_from)
            param_idx += 1
        except ValueError:
            pass

    if date_to:
        try:
            dt_to = datetime.strptime(f"{date_to.strip()} 23:59:59", "%Y-%m-%d %H:%M:%S")
            query += f" AND d.created_at <= ${param_idx}"
            params.append(dt_to)
            param_idx += 1
        except ValueError:
            pass

    query += " ORDER BY d.created_at DESC"
    
    try:
        rows = await conn.fetch(query, *params)
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"Error procesando Reporte de Pedidos: {e}")
        raise HTTPException(status_code=400, detail="Error al generar el reporte de pedidos.")