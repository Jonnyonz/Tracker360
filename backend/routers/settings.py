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

DEFAULT_SETTINGS = {
    "app_name": "Tracker360",
    "company_cuit": "30-00000000-0",
    "zebra_ip": "192.168.1.50",
    "enable_stock_management": "true",
    "allow_negative_stock": "false",
    "enable_committed_stock": "true",
    "require_mobile_reception": "false",
    "allow_multiproduct_locations": "false",
    "enable_item_dimensions": "false",
    "enable_lots_expiration": "false", # MODIFICACIÓN FASE 1
    "zpl_item_width": "38",
    "zpl_item_height": "20",
    "zpl_item_template": DEFAULT_ITEM_ZPL,
    "zpl_template": DEFAULT_ITEM_ZPL,
    "zpl_order_width": "100",
    "zpl_order_height": "150",
    "zpl_order_template": DEFAULT_ORDER_ZPL,
    "tracker360_api_key": "",
    "api_key": ""
}

async def _gen_key_db(conn: asyncpg.Connection):
    new_key = secrets.token_hex(24)
    await conn.execute("""
        INSERT INTO settings (key, value, updated_at)
        VALUES ('tracker360_api_key', $1, NOW())
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at
    """, new_key)
    await conn.execute("""
        INSERT INTO settings (key, value, updated_at)
        VALUES ('api_key', $1, NOW())
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at
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
    rows = await conn.fetch("SELECT key, value FROM settings")
    db_res = {r["key"]: r["value"] for r in rows}
    
    # Iniciar con el diccionario de valores por defecto
    res = DEFAULT_SETTINGS.copy()
    
    # Sobrescribir con lo guardado en la base de datos (siempre que no esté vacío)
    for k, v in db_res.items():
        if v is not None and str(v).strip() != "":
            res[k] = str(v)
            
    # Sincronización de API Key
    key_val = db_res.get("tracker360_api_key") or db_res.get("api_key") or res.get("tracker360_api_key") or ""
    res["tracker360_api_key"] = key_val
    res["api_key"] = key_val

    # Sincronización de Plantillas ZPL
    item_tpl = db_res.get("zpl_item_template") or db_res.get("zpl_template")
    if not item_tpl or not str(item_tpl).strip():
        item_tpl = DEFAULT_ITEM_ZPL

    order_tpl = db_res.get("zpl_order_template")
    if not order_tpl or not str(order_tpl).strip():
        order_tpl = DEFAULT_ORDER_ZPL

    res["zpl_item_template"] = str(item_tpl)
    res["zpl_template"] = str(item_tpl)
    res["zpl_order_template"] = str(order_tpl)
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
                    
                    # Evitar guardar textos vacíos en parámetros críticos
                    if k in ["zpl_item_template", "zpl_template"] and not val_str.strip():
                        val_str = DEFAULT_ITEM_ZPL
                    elif k == "zpl_order_template" and not val_str.strip():
                        val_str = DEFAULT_ORDER_ZPL
                    elif k == "zpl_item_width" and not val_str.strip():
                        val_str = "38"
                    elif k == "zpl_item_height" and not val_str.strip():
                        val_str = "20"
                    elif k == "zpl_order_width" and not val_str.strip():
                        val_str = "100"
                    elif k == "zpl_order_height" and not val_str.strip():
                        val_str = "150"

                    await conn.execute("""
                        INSERT INTO settings (key, value, updated_at)
                        VALUES ($1, $2, NOW())
                        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at
                    """, str(k).strip(), val_str)
                    
                    if k == "zpl_item_template":
                        await conn.execute("""
                            INSERT INTO settings (key, value, updated_at)
                            VALUES ('zpl_template', $1, NOW())
                            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at
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
    row = await conn.fetchrow("SELECT value FROM settings WHERE key = $1", key.strip())
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
        INSERT INTO settings (key, value, updated_at)
        VALUES ($1, $2, NOW())
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at
    """, key.strip(), body_val)
    return {"status": "ok", "key": key, "value": body_val}