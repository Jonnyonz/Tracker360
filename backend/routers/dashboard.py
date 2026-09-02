from fastapi import APIRouter, Depends
import asyncpg

try:
    from backend.database import get_db_connection, require_admin
except ImportError:
    from database import get_db_connection, require_admin

router = APIRouter(tags=["Dashboard & Logs"])

@router.get("/api/admin/dashboard")
async def get_admin_dashboard_op(admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    # 1. Bloque Core del Dashboard (A prueba de fallos)
    pending_orders = await conn.fetch("""
        SELECT d.document_number, COALESCE(e.company_name, 'Consumidor Final') as company_name, d.status 
        FROM documents d 
        LEFT JOIN entities e ON d.customer_id = e.id 
        WHERE d.status IN ('PENDING', 'IN_PROGRESS') 
        ORDER BY d.created_at ASC LIMIT 5
    """)
    
    active_transfers = await conn.fetch("""
        SELECT t.transfer_number, COALESCE(ob.name, 'N/A') as origin_branch, COALESCE(db.name, 'N/A') as destination_branch
        FROM transfer_orders t
        LEFT JOIN branches ob ON t.origin_branch_id = ob.id
        LEFT JOIN branches db ON t.destination_branch_id = db.id
        WHERE t.status IN ('PENDING', 'PENDING_CONTROL', 'IN_PROGRESS')
        ORDER BY t.created_at ASC LIMIT 5
    """)
    
    latest_logs = await conn.fetch("""
        SELECT created_at, username, action 
        FROM audit_logs 
        ORDER BY created_at DESC LIMIT 5
    """)

    # 2. Bloque KPIs de Productividad (Fase 5) - Aislado para no tumbar la app
    units_today = 0.0
    avg_cycle = 0.0
    leaderboard = []

    try:
        units_today = await conn.fetchval("""
            SELECT COALESCE(SUM(ABS(quantity)), 0)
            FROM stock_movements
            WHERE movement_type = 'OUT_PICKING' AND DATE(created_at) = CURRENT_DATE
        """)
    except Exception as e:
        print(f"[KPI Error] units_today falló: {e}")

    try:
        # Simplificamos la consulta por si la base de datos no tiene 'updated_at'
        avg_cycle = await conn.fetchval("""
            SELECT COALESCE(AVG(EXTRACT(EPOCH FROM (updated_at - created_at))/3600), 0)
            FROM documents 
            WHERE status IN ('COMPLETED', 'DISPATCHED') AND updated_at IS NOT NULL
        """)
    except Exception as e:
        print(f"[KPI Error] avg_cycle falló (Posible falta de columna updated_at): {e}")

    try:
        leaderboard_rows = await conn.fetch("""
            SELECT username, COUNT(*) as picking_lines, COALESCE(SUM(ABS(quantity)), 0) as picked_units
            FROM stock_movements
            WHERE movement_type = 'OUT_PICKING' AND created_at >= CURRENT_DATE - INTERVAL '7 days'
            GROUP BY username
            ORDER BY picked_units DESC
            LIMIT 5
        """)
        leaderboard = [dict(r) for r in leaderboard_rows]
    except Exception as e:
        print(f"[KPI Error] leaderboard falló: {e}")
    
    return {
        "status": "ok",
        "pending_orders": [dict(r) for r in pending_orders],
        "active_transfers": [dict(r) for r in active_transfers],
        "latest_logs": [dict(r) for r in latest_logs],
        "kpis": {
            "units_today": float(units_today or 0),
            "avg_cycle_hours": float(avg_cycle or 0)
        },
        "leaderboard": leaderboard
    }

@router.get("/api/admin/integrations")
async def get_admin_integrations_op(admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    rows = await conn.fetch("""
        SELECT id::text, name, channel_type, target_url, is_active, created_at
        FROM integration_channels
        ORDER BY created_at DESC
    """)
    return [dict(r) for r in rows]

@router.get("/api/admin/logs")
async def list_admin_logs(admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    rows = await conn.fetch("SELECT created_at, username, action, details FROM audit_logs ORDER BY created_at DESC LIMIT 100")
    return [dict(r) for r in rows]