from fastapi import APIRouter, Depends, HTTPException, Request
from backend.database import get_db_connection
import asyncpg, secrets

router = APIRouter()

DEFAULT_ITEM_ZPL = """^XA
^PW304
^LL160
^LS0
^FO20,25^A0N,22,22^FD{{SKU}}^FS
^FO20,65^A0N,16,14^FD{{DESC}}^FS
^FO195,15^BQN,2,3^FDLA,{{SKU}}^FS
^XZ"""

DEFAULT_ORDER_ZPL = """^XA
^PW608
^LL380
^LS0
^FO30,30^A0N,30,30^FDTRACKER360 - ENVIO^FS
^FO30,80^A0N,24,24^FDORDEN: {{ORDER_NUM}}^FS
^FO30,120^A0N,20,20^FDDESTINO: {{DESTINATION}}^FS
^FO380,60^BQN,2,5^FDLA,{{ORDER_NUM}}^FS
^XZ"""

DEFAULT_LOCATION_ZPL = """^XA
^PW400
^LL200
^LS0
^FO30,25^A0N,28,28^FDUBICACION: {{LOCATION_CODE}}^FS
^FO30,65^A0N,20,18^FD{{BRANCH}} - {{SECTOR}}^FS
^FO30,105^BY3,2.0,60^BCN,70,Y,N,N^FD{{LOCATION_CODE}}^FS
^XZ"""

DEFAULT_SETTINGS = {
    "app_name": "Tracker360",
    "company_cuit": "30-00000000-0",
    "enable_stock_management": "true",
    "allow_negative_stock": "false",
    "enable_committed_stock": "true",
    "require_mobile_reception": "false",
    "allow_multiproduct_locations": "false",
    "enable_item_dimensions": "false",
    "enable_lots_expiration": "false",
    "session_timeout_minutes": "240",
    "max_login_attempts": "5",
    "lockout_time_minutes": "15",
    "enable_google_sso": "false",
    "google_client_id": "",
    "google_client_secret": "",
    "google_allowed_domain": "",
    "transfer_number_prefix": "TR-",
    "sales_order_prefix": "PED-",
    "correlative_zeros_pad": "6",
    "auto_complete_picking": "true",
    "default_print_queue": "PRINT-SEC-01",
    "default_inventory_count_type": "HOT",
    "zpl_item_width": "38",
    "zpl_item_height": "20",
    "zpl_item_template": DEFAULT_ITEM_ZPL,
    "zpl_template": DEFAULT_ITEM_ZPL,
    "zpl_order_width": "100",
    "zpl_order_height": "150",
    "zpl_order_template": DEFAULT_ORDER_ZPL,
    "zpl_location_width": "50",
    "zpl_location_height": "25",
    "zpl_location_template": DEFAULT_LOCATION_ZPL,
    "tracker360_api_key": "",
    "api_key": ""
}

async def _gen_key_db(conn: asyncpg.Connection):
    new_key = secrets.token_hex(24)
    await conn.execute("""
        INSERT INTO system_settings (key, value)
        VALUES ('tracker360_api_key', $1)
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
    """, new_key)
    await conn.execute("""
        INSERT INTO system_settings (key, value)
        VALUES ('api_key', $1)
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
    """, new_key)
    return {"status": "ok", "new_key": new_key, "api_key": new_key, "value": new_key}

@router.post("/api/admin/settings/generate-key")
@router.post("/api/settings/generate-key")
@router.get("/api/admin/settings/generate-key")
@router.get("/api/settings/generate-key")
async def generate_key_exact_endpoint(conn: asyncpg.Connection = Depends(get_db_connection)):
    return await _gen_key_db(conn)

@router.get("/api/settings")
@router.get("/api/admin/settings")
async def get_all_settings(conn: asyncpg.Connection = Depends(get_db_connection)):
    rows = await conn.fetch("SELECT key, value FROM system_settings")
    db_res = {r["key"]: r["value"] for r in rows}
    
    res = DEFAULT_SETTINGS.copy()
    
    for k, v in db_res.items():
        if v is not None and str(v).strip() != "":
            res[k] = str(v)
            
    key_val = db_res.get("tracker360_api_key") or db_res.get("api_key") or res.get("tracker360_api_key") or ""
    res["tracker360_api_key"] = key_val
    res["api_key"] = key_val

    item_tpl = db_res.get("zpl_item_template") or db_res.get("zpl_template")
    if not item_tpl or not str(item_tpl).strip():
        item_tpl = DEFAULT_ITEM_ZPL

    order_tpl = db_res.get("zpl_order_template")
    if not order_tpl or not str(order_tpl).strip():
        order_tpl = DEFAULT_ORDER_ZPL

    loc_tpl = db_res.get("zpl_location_template")
    if not loc_tpl or not str(loc_tpl).strip():
        loc_tpl = DEFAULT_LOCATION_ZPL

    res["zpl_item_template"] = str(item_tpl)
    res["zpl_template"] = str(item_tpl)
    res["zpl_order_template"] = str(order_tpl)
    res["zpl_location_template"] = str(loc_tpl)
    return res

@router.post("/api/settings")
@router.put("/api/settings")
@router.post("/api/admin/settings")
@router.put("/api/admin/settings")
async def save_bulk_settings(request: Request, conn: asyncpg.Connection = Depends(get_db_connection)):
    try:
        data = await request.json()
        if isinstance(data, dict):
            for k, v in data.items():
                if k is not None:
                    val_str = str(v) if v is not None else ""
                    
                    if k in ["zpl_item_template", "zpl_template"] and not val_str.strip():
                        val_str = DEFAULT_ITEM_ZPL
                    elif k == "zpl_order_template" and not val_str.strip():
                        val_str = DEFAULT_ORDER_ZPL
                    elif k == "zpl_location_template" and not val_str.strip():
                        val_str = DEFAULT_LOCATION_ZPL
                    elif k == "zpl_item_width" and not val_str.strip():
                        val_str = "38"
                    elif k == "zpl_item_height" and not val_str.strip():
                        val_str = "20"
                    elif k == "zpl_order_width" and not val_str.strip():
                        val_str = "100"
                    elif k == "zpl_order_height" and not val_str.strip():
                        val_str = "150"
                    elif k == "zpl_location_width" and not val_str.strip():
                        val_str = "50"
                    elif k == "zpl_location_height" and not val_str.strip():
                        val_str = "25"

                    await conn.execute("""
                        INSERT INTO system_settings (key, value)
                        VALUES ($1, $2)
                        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
                    """, str(k).strip(), val_str)
                    
                    if k == "zpl_item_template":
                        await conn.execute("""
                            INSERT INTO system_settings (key, value)
                            VALUES ('zpl_template', $1)
                            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
                        """, val_str)

        return {"status": "ok", "message": "Configuración guardada exitosamente"}
    except Exception as exc:
        print(f"[SAVE BULK SETTINGS ERROR]: {exc}")
        raise HTTPException(status_code=500, detail=f"Error guardando configuración: {str(exc)}")

@router.get("/api/settings/{key}")
@router.get("/api/admin/settings/{key}")
async def get_setting_by_key(key: str, conn: asyncpg.Connection = Depends(get_db_connection)):
    if key.strip().lower() in ["generate-key", "generate-api-key", "api-key"]:
        return await _gen_key_db(conn)
    row = await conn.fetchrow("SELECT value FROM system_settings WHERE key = $1", key.strip())
    val = row["value"] if row else DEFAULT_SETTINGS.get(key.strip(), "")
    return {"key": key, "value": val}

@router.post("/api/settings/{key}")
@router.post("/api/admin/settings/{key}")
@router.put("/api/settings/{key}")
@router.put("/api/admin/settings/{key}")
async def update_setting_by_key(key: str, request: Request, conn: asyncpg.Connection = Depends(get_db_connection)):
    if key.strip().lower() in ["generate-key", "generate-api-key", "api-key"]:
        return await _gen_key_db(conn)
    body_val = ""
    try:
        data = await request.json()
        if isinstance(data, dict):
            body_val = str(data.get("value", data.get("val", "")))
        elif isinstance(data, str):
            body_val = data
    except Exception:
        pass
    await conn.execute("""
        INSERT INTO system_settings (key, value)
        VALUES ($1, $2)
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
    """, key.strip(), body_val)
    return {"status": "ok", "key": key, "value": body_val}