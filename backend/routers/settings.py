from fastapi import APIRouter, Depends, HTTPException, Request
from backend.database import get_db_connection, send_webhook_sync, get_current_user, require_admin, log_action, get_client_ip, hash_system_api_key
import asyncpg, secrets, json, uuid, asyncio, re
import logging

logger = logging.getLogger(__name__)

router = APIRouter()

# Claves cuyo valor nunca se devuelve en claro por GET.
SECRET_KEYS = {"tracker360_api_key", "api_key", "google_client_secret"}
SECRET_MASK = "********"
VALID_KEY_RE = re.compile(r"^[a-z0-9_]{1,64}$")
GENERATE_KEY_ALIASES = {"generate-key", "generate-api-key", "api-key"}

def _mask(key: str, value):
    if key in SECRET_KEYS and value is not None and str(value) != "":
        return SECRET_MASK
    return value

def _validate_key(key: str) -> str:
    k = str(key).strip()
    if not VALID_KEY_RE.match(k):
        raise HTTPException(status_code=400, detail="Clave de configuracion invalida.")
    return k

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
    "enable_api_idempotency": "false",
    "enable_serial_tracking": "false",
    "enable_putaway_suggestions": "false",
    "enable_replenishment": "false",
    "enable_wave_picking": "false",
    "enable_optimal_routing": "false",
    "enable_packing_station": "false",
    "enable_labor_management": "false",
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
    # Se genera la clave en claro, se muestra una sola vez y en la DB se guarda solo su hash.
    new_key = secrets.token_hex(24)
    hashed = hash_system_api_key(new_key)
    await conn.execute("""
        INSERT INTO system_settings (key, value)
        VALUES ('tracker360_api_key', $1)
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
    """, hashed)
    await conn.execute("""
        INSERT INTO system_settings (key, value)
        VALUES ('api_key', $1)
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
    """, hashed)
    return {"status": "ok", "new_key": new_key, "api_key": new_key, "value": new_key}

@router.post("/api/admin/settings/generate-key")
@router.post("/api/settings/generate-key")
async def generate_key_exact_endpoint(request: Request, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    client_ip = get_client_ip(request)
    await log_action(conn, admin["username"], "API_KEY_ROTATED", "Clave API maestra regenerada", client_ip)
    return await _gen_key_db(conn)

@router.get("/api/settings")
@router.get("/api/admin/settings")
async def get_all_settings(user: dict = Depends(get_current_user), conn: asyncpg.Connection = Depends(get_db_connection)):
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
    for k in SECRET_KEYS:
        if k in res:
            res[k] = _mask(k, res[k])
    return res

@router.post("/api/settings")
@router.put("/api/settings")
@router.post("/api/admin/settings")
@router.put("/api/admin/settings")
async def save_bulk_settings(request: Request, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Cuerpo JSON invalido.")
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Se esperaba un objeto JSON.")
    for k in data.keys():
        if k is not None:
            _validate_key(k)
    try:
        if isinstance(data, dict):
            for k, v in data.items():
                if k is not None:
                    k = str(k).strip()
                    val_str = str(v) if v is not None else ""
                    # El formulario reenvia el secreto enmascarado: no pisar el valor real.
                    if k in SECRET_KEYS and val_str == SECRET_MASK:
                        continue

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
                    """, k, val_str)

                    if k == "zpl_item_template":
                        await conn.execute("""
                            INSERT INTO system_settings (key, value)
                            VALUES ('zpl_template', $1)
                            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
                        """, val_str)

        client_ip = get_client_ip(request)
        await log_action(conn, admin["username"], "SETTINGS_UPDATED", f"Configuracion modificada: {', '.join(sorted(str(k) for k in data.keys()))}", client_ip)
        return {"status": "ok", "message": "Configuración guardada exitosamente"}
    except Exception as exc:
        logger.exception(f"[SAVE BULK SETTINGS ERROR]: {exc!r}")
        raise HTTPException(status_code=500, detail="Error guardando configuración.")

@router.get("/api/settings/{key}")
@router.get("/api/admin/settings/{key}")
async def get_setting_by_key(key: str, user: dict = Depends(get_current_user), conn: asyncpg.Connection = Depends(get_db_connection)):
    # Generar clave tiene efecto: solo por POST, nunca por GET.
    if key.strip().lower() in GENERATE_KEY_ALIASES:
        raise HTTPException(status_code=405, detail="Use POST para generar la clave.")
    k = _validate_key(key)
    row = await conn.fetchrow("SELECT value FROM system_settings WHERE key = $1", k)
    val = row["value"] if row else DEFAULT_SETTINGS.get(k, "")
    return {"key": key, "value": _mask(k, val)}

@router.post("/api/settings/{key}")
@router.post("/api/admin/settings/{key}")
@router.put("/api/settings/{key}")
@router.put("/api/admin/settings/{key}")
async def update_setting_by_key(key: str, request: Request, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    client_ip = get_client_ip(request)
    if key.strip().lower() in GENERATE_KEY_ALIASES:
        await log_action(conn, admin["username"], "API_KEY_ROTATED", "Clave API maestra regenerada", client_ip)
        return await _gen_key_db(conn)
    k = _validate_key(key)
    body_val = ""
    raw = await request.body()
    if raw.strip():
        try:
            data = json.loads(raw)
        except ValueError:
            raise HTTPException(status_code=400, detail="Cuerpo JSON invalido.")
        if isinstance(data, dict):
            body_val = str(data.get("value", data.get("val", "")))
        elif isinstance(data, str):
            body_val = data
    if k in SECRET_KEYS and body_val == SECRET_MASK:
        return {"status": "ok", "key": key, "value": SECRET_MASK}
    await conn.execute("""
        INSERT INTO system_settings (key, value)
        VALUES ($1, $2)
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
    """, k, body_val)
    await log_action(conn, admin["username"], "SETTINGS_UPDATED", f"Configuracion modificada: {k}", client_ip)
    return {"status": "ok", "key": key, "value": _mask(k, body_val)}

# === AUDITORÍA Y RE-INTENTOS DE WEBHOOKS ===
@router.get("/api/admin/webhooks/logs")
async def get_webhook_logs(limit: int = 50, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    limit = max(1, min(limit, 500))
    rows = await conn.fetch("""
        SELECT id, channel_id, channel_name, event_type, target_url, payload::text, response_status, response_body, error_message, status, created_at
        FROM webhook_logs ORDER BY created_at DESC LIMIT $1
    """, limit)
    return [dict(r) for r in rows]

@router.post("/api/admin/webhooks/retry/{log_id}")
async def retry_webhook_log(log_id: str, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    try:
        log_uuid = uuid.UUID(log_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="ID de log inválido.")
    
    log_row = await conn.fetchrow("SELECT * FROM webhook_logs WHERE id = $1", log_uuid)
    if not log_row:
        raise HTTPException(status_code=404, detail="Registro de webhook no encontrado.")
    
    api_key = ""
    if log_row["channel_id"]:
        api_key = await conn.fetchval("SELECT api_key FROM integration_channels WHERE id = $1", log_row["channel_id"]) or ""
    
    payload = json.loads(log_row["payload"]) if isinstance(log_row["payload"], str) else dict(log_row["payload"])
    
    status, body, err = await asyncio.to_thread(send_webhook_sync, log_row["target_url"], payload, api_key)
    new_status = "SUCCESS" if (status and 200 <= status < 300) else "FAILED"
    
    await conn.execute("""
        UPDATE webhook_logs SET response_status = $1, response_body = $2, error_message = $3, status = $4, created_at = CURRENT_TIMESTAMP
        WHERE id = $5
    """, status, body, err, new_status, log_uuid)
    
    return {"status": "ok", "response_status": status, "webhook_status": new_status, "error_message": err}