from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
import asyncpg, uuid, secrets

try:
    from backend.database import get_db_connection, require_admin, log_action
except ImportError:
    from database import get_db_connection, require_admin, log_action

router = APIRouter(tags=["Settings & Dashboard"])

class SettingsUpdate(BaseModel):
    app_name: Optional[str] = None
    company_cuit: Optional[str] = None
    zebra_ip: Optional[str] = None
    enable_stock_management: Optional[str] = None
    allow_negative_stock: Optional[str] = None
    enable_committed_stock: Optional[str] = None
    require_mobile_reception: Optional[str] = None
    allow_multiproduct_locations: Optional[str] = None
    enable_item_dimensions: Optional[str] = None
    zpl_item_width: Optional[str] = None
    zpl_item_height: Optional[str] = None
    zpl_item_template: Optional[str] = None
    zpl_order_width: Optional[str] = None
    zpl_order_height: Optional[str] = None
    zpl_order_template: Optional[str] = None

class IntegrationChannelCreate(BaseModel):
    name: str
    channel_type: str
    target_url: str
    api_key: Optional[str] = None

@router.get("/api/settings")
async def get_system_settings(conn: asyncpg.Connection = Depends(get_db_connection)):
    rows = await conn.fetch("SELECT key, value FROM system_settings")
    res = {}
    for r in rows:
        res[r["key"]] = r["value"]
    return res

@router.put("/api/admin/settings")
async def update_settings(data: SettingsUpdate, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    items = data.dict(exclude_unset=True)
    async with conn.transaction():
        for k, v in items.items():
            if v is not None:
                await conn.execute("INSERT INTO system_settings (key, value) VALUES ($1, $2) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", k, str(v))
    return {"status": "success", "message": "Configuración actualizada."}

@router.post("/api/admin/settings/generate-key")
async def generate_new_api_key(admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    new_key = f"trk_live_{secrets.token_hex(24)}"
    await conn.execute("INSERT INTO system_settings (key, value) VALUES ('tracker360_api_key', $1) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", new_key)
    return {"status": "success", "new_key": new_key}

@router.get("/api/admin/integrations")
async def list_integration_channels(admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    rows = await conn.fetch("SELECT id, name, channel_type, target_url, is_active, created_at FROM integration_channels ORDER BY created_at DESC")
    return [dict(r) for r in rows]

@router.post("/api/admin/integrations")
async def create_integration_channel(data: IntegrationChannelCreate, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    await conn.execute("INSERT INTO integration_channels (name, channel_type, target_url, api_key) VALUES ($1, $2, $3, $4)", data.name.strip(), data.channel_type.strip(), data.target_url.strip(), data.api_key)
    return {"status": "success"}

@router.delete("/api/admin/integrations/{channel_id}")
async def delete_integration_channel(channel_id: str, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    await conn.execute("DELETE FROM integration_channels WHERE id = $1", uuid.UUID(channel_id))
    return {"status": "success"}

@router.get("/api/admin/logs")
async def list_logs(admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    return [dict(l) for l in await conn.fetch("SELECT username, action, details, created_at FROM audit_logs ORDER BY created_at DESC LIMIT 100")]

@router.get("/api/admin/dashboard")
async def get_dashboard_summary(admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    pending_orders = await conn.fetch("SELECT d.document_number, d.status, COALESCE(e.company_name, 'Consumidor Final') as company_name FROM documents d LEFT JOIN entities e ON d.customer_id = e.id WHERE d.status IN ('PENDING', 'IN_PROGRESS') ORDER BY d.created_at DESC LIMIT 5")
    active_transfers = await conn.fetch("SELECT t.transfer_number, COALESCE(ob.name, 'Origen') as origin_branch, COALESCE(db.name, 'Destino') as destination_branch FROM transfer_orders t LEFT JOIN branches ob ON t.origin_branch_id = ob.id LEFT JOIN branches db ON t.destination_branch_id = db.id WHERE t.status IN ('PENDING', 'PENDING_CONTROL', 'IN_PROGRESS') ORDER BY t.created_at DESC LIMIT 5")
    latest_logs = await conn.fetch("SELECT username, action, created_at FROM audit_logs ORDER BY created_at DESC LIMIT 5")
    
    return {
        "pending_orders": [dict(r) for r in pending_orders],
        "active_transfers": [dict(r) for r in active_transfers],
        "latest_logs": [dict(r) for r in latest_logs]
    }
