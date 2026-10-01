from fastapi import APIRouter, Depends, HTTPException
from typing import Optional
from datetime import datetime
import asyncpg, uuid
import logging

logger = logging.getLogger(__name__)

from backend.database import get_db_connection, require_admin, require_supervisor

router = APIRouter(tags=["Reports"])

# === 1. REPORTE DE STOCK CONSOLIDADO ===
@router.get("/api/admin/reports/stock")
async def report_stock(
    sku: Optional[str] = None,
    branch_id: Optional[str] = None,
    sector_id: Optional[str] = None,
    include_zero: str = "false",
    include_negative: str = "false",
    admin: dict = Depends(require_supervisor), 
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
            raise HTTPException(400, "Filtro inválido: identificador mal formado.")
            
    if sector_id:
        try:
            s_uuid = uuid.UUID(sector_id)
            query += f" AND si.sector_id = ${param_idx}"
            params.append(s_uuid)
            param_idx += 1
        except ValueError:
            raise HTTPException(400, "Filtro inválido: identificador mal formado.")

    query += " ORDER BY b.name ASC, sec.name ASC, si.sku ASC"
    
    rows = await conn.fetch(query, *params)
    return [dict(r) for r in rows]

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
    admin: dict = Depends(require_supervisor), 
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

    if related_only.lower() == "true":
        query += " AND d.channel_origin IS NOT NULL AND d.channel_origin != 'MANUAL'"

    if sku:
        query += f" AND EXISTS (SELECT 1 FROM document_lines dl WHERE dl.document_id = d.id AND dl.sku ILIKE ${param_idx})"
        params.append(f"%{sku.strip()}%")
        param_idx += 1

    if date_from:
        try:
            dt_from = datetime.strptime(f"{date_from.strip()} 00:00:00", "%Y-%m-%d %H:%M:%S")
            query += f" AND d.created_at >= ${param_idx}"
            params.append(dt_from)
            param_idx += 1
        except ValueError:
            raise HTTPException(400, "Fecha inválida (usar AAAA-MM-DD).")

    if date_to:
        try:
            dt_to = datetime.strptime(f"{date_to.strip()} 23:59:59", "%Y-%m-%d %H:%M:%S")
            query += f" AND d.created_at <= ${param_idx}"
            params.append(dt_to)
            param_idx += 1
        except ValueError:
            raise HTTPException(400, "Fecha inválida (usar AAAA-MM-DD).")

    query += " ORDER BY d.created_at DESC"
    
    rows = await conn.fetch(query, *params)
    return [dict(r) for r in rows]

# === 3. REPORTE DE REMITOS DE COMPRA (INGRESOS) ===
@router.get("/api/admin/reports/remitos")
async def report_remitos(
    remito_number: Optional[str] = None,
    supplier_id: Optional[str] = None,
    sku: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    branch_id: Optional[str] = None,
    admin: dict = Depends(require_supervisor), 
    conn: asyncpg.Connection = Depends(get_db_connection)
):
    query = """
        SELECT pr.remito_number, pr.created_at, pr.status,
               COALESCE(e.company_name, 'Sin Proveedor') as supplier_name,
               COALESCE(e.tax_id, 'N/A') as supplier_tax_id,
               COALESCE(b.name, 'N/A') as branch_name,
               COALESCE(s.name, 'N/A') as sector_name,
               COALESCE((SELECT SUM(quantity_received) * 100.0 / NULLIF(SUM(quantity_sent), 0) 
                         FROM purchase_remito_lines WHERE purchase_remito_id = pr.id), 0)::int as progress_pct
        FROM purchase_remitos pr
        LEFT JOIN entities e ON pr.supplier_id = e.id
        LEFT JOIN branches b ON pr.branch_id = b.id
        LEFT JOIN sectors s ON pr.sector_id = s.id
        WHERE 1=1
    """
    params = []
    param_idx = 1

    if remito_number:
        query += f" AND pr.remito_number ILIKE ${param_idx}"
        params.append(f"%{remito_number.strip()}%")
        param_idx += 1

    if supplier_id:
        try:
            sup_uuid = uuid.UUID(supplier_id)
            query += f" AND pr.supplier_id = ${param_idx}"
            params.append(sup_uuid)
            param_idx += 1
        except ValueError:
            raise HTTPException(400, "Filtro inválido: identificador mal formado.")

    if status:
        query += f" AND pr.status = ${param_idx}"
        params.append(status.strip().upper())
        param_idx += 1

    if branch_id:
        try:
            b_uuid = uuid.UUID(branch_id)
            query += f" AND pr.branch_id = ${param_idx}"
            params.append(b_uuid)
            param_idx += 1
        except ValueError:
            raise HTTPException(400, "Filtro inválido: identificador mal formado.")

    if sku:
        query += f" AND EXISTS (SELECT 1 FROM purchase_remito_lines prl WHERE prl.purchase_remito_id = pr.id AND prl.sku ILIKE ${param_idx})"
        params.append(f"%{sku.strip()}%")
        param_idx += 1

    if date_from:
        try:
            dt_from = datetime.strptime(f"{date_from.strip()} 00:00:00", "%Y-%m-%d %H:%M:%S")
            query += f" AND pr.created_at >= ${param_idx}"
            params.append(dt_from)
            param_idx += 1
        except ValueError:
            raise HTTPException(400, "Fecha inválida (usar AAAA-MM-DD).")

    if date_to:
        try:
            dt_to = datetime.strptime(f"{date_to.strip()} 23:59:59", "%Y-%m-%d %H:%M:%S")
            query += f" AND pr.created_at <= ${param_idx}"
            params.append(dt_to)
            param_idx += 1
        except ValueError:
            raise HTTPException(400, "Fecha inválida (usar AAAA-MM-DD).")

    query += " ORDER BY pr.created_at DESC"
    
    rows = await conn.fetch(query, *params)
    return [dict(r) for r in rows]

# === DIFERENCIAS DE RECEPCION (control fisico de remitos) ===
# Faltantes y sobrantes por remito y articulo (solo articulos que figuraban en el remito, asi un no
# esperado aprobado no se cuenta dos veces) y los no esperados de la cuarentena con su estado.
@router.get("/api/admin/reports/reception-differences")
async def report_reception_differences(
    supplier_id: Optional[str] = None,
    branch_id: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    kind: Optional[str] = None,
    admin: dict = Depends(require_supervisor),
    conn: asyncpg.Connection = Depends(get_db_connection)
):
    filtros, params = [], []
    def uid(v, msg):
        try: return uuid.UUID(v)
        except ValueError: raise HTTPException(400, msg)
    def fecha(v, hora):
        try: return datetime.strptime(f"{v.strip()} {hora}", "%Y-%m-%d %H:%M:%S")
        except ValueError: raise HTTPException(400, "Fecha inválida (usar AAAA-MM-DD).")
    if supplier_id: params.append(uid(supplier_id, "Proveedor inválido.")); filtros.append(f"pr.supplier_id = ${len(params)}")
    if branch_id: params.append(uid(branch_id, "Sucursal inválida.")); filtros.append(f"pr.branch_id = ${len(params)}")
    if date_from: params.append(fecha(date_from, "00:00:00")); filtros.append(f"pr.created_at >= ${len(params)}")
    if date_to: params.append(fecha(date_to, "23:59:59")); filtros.append(f"pr.created_at <= ${len(params)}")
    kind = (kind or "").strip().upper()
    if kind and kind not in ("FALTANTE", "SOBRANTE", "NO_ESPERADO"): raise HTTPException(400, "Tipo inválido.")
    where = (" AND " + " AND ".join(filtros)) if filtros else ""

    rows = await conn.fetch(f"""
        WITH por_sku AS (
            SELECT prl.purchase_remito_id AS remito_id, UPPER(prl.sku) AS sku,
                   SUM(prl.quantity_sent) FILTER (WHERE NOT prl.added_in_control)::float AS sent,
                   SUM(prl.quantity_received)::float AS received
            FROM purchase_remito_lines prl GROUP BY prl.purchase_remito_id, UPPER(prl.sku)
            HAVING COUNT(*) FILTER (WHERE NOT prl.added_in_control) > 0
        ), dif AS (
            SELECT CASE WHEN ps.received < ps.sent THEN 'FALTANTE' ELSE 'SOBRANTE' END AS kind, pr.id AS remito_id,
                   ps.sku, ps.sent, ps.received, (ps.received - ps.sent) AS difference, NULL::text AS exception_status
            FROM por_sku ps JOIN purchase_remitos pr ON pr.id = ps.remito_id
            WHERE pr.status IN ('COMPLETED', 'COMPLETED_DIFF') AND ps.received <> ps.sent{where}
            UNION ALL
            SELECT 'NO_ESPERADO', pr.id, UPPER(x.sku), 0, x.quantity::float, x.quantity::float, x.status
            FROM reception_exceptions x JOIN purchase_remitos pr ON pr.id = x.remito_id
            WHERE TRUE{where}
        )
        SELECT d.kind, d.remito_id::text AS remito_id, pr.remito_number, pr.created_at, pr.status AS remito_status,
               COALESCE(e.company_name, 'Sin Proveedor') AS supplier_name, COALESCE(e.tax_id, '') AS supplier_tax_id,
               COALESCE(b.name, '') AS branch_name, d.sku, COALESCE(i.description, d.sku) AS description,
               d.sent, d.received, d.difference, d.exception_status
        FROM dif d JOIN purchase_remitos pr ON pr.id = d.remito_id
        LEFT JOIN entities e ON pr.supplier_id = e.id LEFT JOIN branches b ON pr.branch_id = b.id
        LEFT JOIN items i ON UPPER(i.sku) = d.sku
        {"WHERE d.kind = '" + kind + "'" if kind else ""}
        ORDER BY pr.created_at DESC, pr.remito_number, d.sku
    """, *params)
    return [dict(r) for r in rows]

# === 4. REPORTE DE FACTURAS DE COMPRA ===
@router.get("/api/admin/reports/invoices")
async def report_invoices(
    invoice_number: Optional[str] = None,
    supplier_id: Optional[str] = None,
    invoice_type: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    admin: dict = Depends(require_supervisor), 
    conn: asyncpg.Connection = Depends(get_db_connection)
):
    query = """
        SELECT pi.invoice_number, pi.invoice_type, pi.created_at,
               COALESCE(e.company_name, 'Sin Proveedor') as supplier_name,
               COALESCE(e.tax_id, 'N/A') as supplier_tax_id
        FROM purchase_invoices pi
        LEFT JOIN entities e ON pi.supplier_id = e.id
        WHERE 1=1
    """
    params = []
    param_idx = 1

    if invoice_number:
        query += f" AND pi.invoice_number ILIKE ${param_idx}"
        params.append(f"%{invoice_number.strip()}%")
        param_idx += 1

    if supplier_id:
        try:
            sup_uuid = uuid.UUID(supplier_id)
            query += f" AND pi.supplier_id = ${param_idx}"
            params.append(sup_uuid)
            param_idx += 1
        except ValueError:
            raise HTTPException(400, "Filtro inválido: identificador mal formado.")

    if invoice_type:
        query += f" AND pi.invoice_type = ${param_idx}"
        params.append(invoice_type.strip().upper())
        param_idx += 1

    if date_from:
        try:
            dt_from = datetime.strptime(f"{date_from.strip()} 00:00:00", "%Y-%m-%d %H:%M:%S")
            query += f" AND pi.created_at >= ${param_idx}"
            params.append(dt_from)
            param_idx += 1
        except ValueError:
            raise HTTPException(400, "Fecha inválida (usar AAAA-MM-DD).")

    if date_to:
        try:
            dt_to = datetime.strptime(f"{date_to.strip()} 23:59:59", "%Y-%m-%d %H:%M:%S")
            query += f" AND pi.created_at <= ${param_idx}"
            params.append(dt_to)
            param_idx += 1
        except ValueError:
            raise HTTPException(400, "Fecha inválida (usar AAAA-MM-DD).")

    query += " ORDER BY pi.created_at DESC"
    
    rows = await conn.fetch(query, *params)
    return [dict(r) for r in rows]

# === 5. REPORTE DE ÓRDENES DE COMPRA ===
@router.get("/api/admin/reports/purchase-orders")
async def report_purchase_orders(
    order_number: Optional[str] = None,
    supplier_id: Optional[str] = None,
    sku: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    admin: dict = Depends(require_supervisor), 
    conn: asyncpg.Connection = Depends(get_db_connection)
):
    query = """
        SELECT po.order_number, po.created_at, po.status,
               COALESCE(e.company_name, 'Sin Proveedor') as supplier_name,
               COALESCE(e.tax_id, 'N/A') as supplier_tax_id,
               (SELECT COUNT(*) FROM purchase_order_lines WHERE purchase_order_id = po.id) as total_skus,
               (SELECT COALESCE(SUM(quantity_ordered), 0) FROM purchase_order_lines WHERE purchase_order_id = po.id) as total_units
        FROM purchase_orders po
        LEFT JOIN entities e ON po.supplier_id = e.id
        WHERE 1=1
    """
    params = []
    param_idx = 1

    if order_number:
        query += f" AND po.order_number ILIKE ${param_idx}"
        params.append(f"%{order_number.strip()}%")
        param_idx += 1

    if supplier_id:
        try:
            sup_uuid = uuid.UUID(supplier_id)
            query += f" AND po.supplier_id = ${param_idx}"
            params.append(sup_uuid)
            param_idx += 1
        except ValueError:
            raise HTTPException(400, "Filtro inválido: identificador mal formado.")

    if status:
        query += f" AND po.status = ${param_idx}"
        params.append(status.strip().upper())
        param_idx += 1

    if sku:
        query += f" AND EXISTS (SELECT 1 FROM purchase_order_lines pol WHERE pol.purchase_order_id = po.id AND pol.sku ILIKE ${param_idx})"
        params.append(f"%{sku.strip()}%")
        param_idx += 1

    if date_from:
        try:
            dt_from = datetime.strptime(f"{date_from.strip()} 00:00:00", "%Y-%m-%d %H:%M:%S")
            query += f" AND po.created_at >= ${param_idx}"
            params.append(dt_from)
            param_idx += 1
        except ValueError:
            raise HTTPException(400, "Fecha inválida (usar AAAA-MM-DD).")

    if date_to:
        try:
            dt_to = datetime.strptime(f"{date_to.strip()} 23:59:59", "%Y-%m-%d %H:%M:%S")
            query += f" AND po.created_at <= ${param_idx}"
            params.append(dt_to)
            param_idx += 1
        except ValueError:
            raise HTTPException(400, "Fecha inválida (usar AAAA-MM-DD).")

    query += " ORDER BY po.created_at DESC"
    
    rows = await conn.fetch(query, *params)
    return [dict(r) for r in rows]
