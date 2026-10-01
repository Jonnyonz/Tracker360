import os, asyncio, uuid, secrets, json, urllib.request, urllib.error, urllib.parse, ipaddress, socket, hashlib, math
from decimal import Decimal
from datetime import datetime, timedelta, timezone
import asyncpg
from jztech_core.net import real_ip
from fastapi import HTTPException, Header, Request, Depends
from typing import Optional, Dict, List
from jztech_core.passwords import hash_password, needs_rehash, verify_password as _verify_password
from jztech_core import sessions as core_sessions
from jztech_core.csrf import enforce_csrf, generate_csrf_token
from jztech_core.migrations import apply_migrations
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

# === SEGURIDAD Y CONFIGURACIÓN ===
SECRET_KEY = os.getenv("SECRET_KEY", "")
if not SECRET_KEY:
    logger.warning("[Tracker360] SECRET_KEY no esta configurada en el .env: se usa una clave temporal y las sesiones se cierran en cada reinicio.")
    SECRET_KEY = secrets.token_hex(32)
ACCESS_TOKEN_EXPIRE_MINUTES = 240  # Fallback en caso de no leer la DB

# Hash de claves: jztech_core.passwords (Argon2id, parametros OWASP). Los hashes Argon2 que dejo
# passlib tienen el mismo formato y se verifican igual; en el login se rehashean (needs_rehash).
def verify_password(p, h):
    return bool(h) and isinstance(h, str) and _verify_password(p, h)

def get_password_hash(p):
    return hash_password(p)

# === SESIONES (jztech_core.sessions) ===
# Token opaco al azar en la cookie; en la base solo queda su hash. Se revoca borrando la fila.
SESSION_TTL = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
SESSION_TTL_MIN, SESSION_TTL_MAX = timedelta(minutes=5), timedelta(days=7)

async def session_ttl(conn: asyncpg.Connection) -> timedelta:
    """Duracion de una sesion nueva: el ajuste session_timeout_minutes (Configuracion), entre 5
    minutos y 7 dias. Si falta o no es un numero, 240 minutos."""
    raw = await conn.fetchval("SELECT value FROM system_settings WHERE key = 'session_timeout_minutes'")
    try:
        ttl = timedelta(minutes=int(str(raw).strip()))
    except (TypeError, ValueError):
        return SESSION_TTL
    return min(max(ttl, SESSION_TTL_MIN), SESSION_TTL_MAX)
LEGACY_COOKIE = "access_token"  # cookie del JWT anterior: se borra al iniciar o cerrar sesion
# CSRF de doble envio: la cookie csrf_token (legible por el JS de la pagina) se repite en el header
# X-CSRF-Token en cada POST/PUT/PATCH/DELETE. Es un HMAC con la propia sesion como clave: otro sitio
# no puede leerlo ni calcularlo, y no depende de SECRET_KEY (sigue valiendo despues de un reinicio).
CSRF_COOKIE = "csrf_token"
_CSRF_SUJETO = "tracker360-csrf"

def csrf_token_de(session_token: str) -> str:
    return generate_csrf_token(session_token, _CSRF_SUJETO)

class _ConexionComoPool:
    """jztech_core.sessions pide un pool; asi usa la conexion que ya tiene el request."""
    def __init__(self, conn):
        self._conn = conn
    def acquire(self):
        return self
    async def __aenter__(self):
        return self._conn
    async def __aexit__(self, *exc):
        return False

async def start_session(conn: asyncpg.Connection, response, user_id) -> None:
    await conn.execute("DELETE FROM jztech_sessions WHERE expires_at <= now()")
    ttl = await session_ttl(conn)
    token = await core_sessions.create_session(_ConexionComoPool(conn), str(user_id), ttl)
    core_sessions.set_session_cookie(response, token, ttl)
    response.set_cookie(key=CSRF_COOKIE, value=csrf_token_de(token), httponly=False, secure=True,
                        samesite="strict", max_age=int(ttl.total_seconds()))
    response.delete_cookie(LEGACY_COOKIE, secure=True, httponly=True, samesite="strict")

async def end_session(conn: asyncpg.Connection, request: Request, response) -> None:
    """Cierra solo la sesion de este dispositivo."""
    token = request.cookies.get(core_sessions.SESSION_COOKIE_NAME)
    if token:
        await core_sessions.revoke_session(_ConexionComoPool(conn), token)
    core_sessions.clear_session_cookie(response)
    response.delete_cookie(CSRF_COOKIE, secure=True, samesite="strict")
    response.delete_cookie(LEGACY_COOKIE, secure=True, httponly=True, samesite="strict")

async def session_user(conn: asyncpg.Connection, request: Request):
    """Usuario de la cookie de sesion (o None si no hay sesion valida). No valida is_active."""
    token = request.cookies.get(core_sessions.SESSION_COOKIE_NAME)
    if not token:
        return None
    user_id = await core_sessions.verify_session(_ConexionComoPool(conn), token)
    if not user_id:
        return None
    try:
        uid = uuid.UUID(user_id)
    except ValueError:
        return None
    return await conn.fetchrow("SELECT id, username, role, branch_id, sector_id, is_active, token_version FROM users WHERE id = $1", uid)

# === CLAVE API DEL SISTEMA: HASH EN REPOSO ===
# La clave del sistema se guarda hasheada (nunca en claro): un backup o una lectura de la DB
# ya no expone la clave real. El valor en claro solo se muestra una vez, al generarla.
SYSTEM_KEY_PREFIX = "sha256:"

def hash_system_api_key(raw: str) -> str:
    return SYSTEM_KEY_PREFIX + hashlib.sha256(raw.strip().encode("utf-8")).hexdigest()

def verify_system_key_value(raw: str, stored: str) -> bool:
    if not raw or not stored:
        return False
    if stored.startswith(SYSTEM_KEY_PREFIX):
        return secrets.compare_digest(hash_system_api_key(raw), stored)
    # Valor heredado en claro (antes de la migracion): comparacion directa.
    return secrets.compare_digest(raw.strip(), stored.strip())

async def invalidate_user_sessions(conn: asyncpg.Connection, user_id) -> None:
    """Cierra todas las sesiones del usuario (cambio de clave, desactivacion o baja)."""
    await core_sessions.revoke_all_sessions_for_user(_ConexionComoPool(conn), str(user_id))
    await conn.execute("UPDATE users SET token_version = token_version + 1 WHERE id = $1", user_id)

# === IP REAL DEL CLIENTE (DETRAS DE PROXY INVERSO) ===
# Solo se confia en X-Forwarded-For / X-Forwarded-Proto si la conexion viene de un proxy listado.
# Por defecto: loopback y redes internas de Docker (proxy en el mismo host).
def _parse_networks(raw: str):
    nets = []
    for part in raw.split(","):
        part = part.strip()
        if not part: continue
        try: nets.append(ipaddress.ip_network(part, strict=False))
        except ValueError: print(f"[Tracker360] TRUSTED_PROXIES: valor invalido ignorado: {part}")
    return nets

TRUSTED_PROXIES = _parse_networks(os.getenv("TRUSTED_PROXIES", "127.0.0.1/32,::1/128,172.16.0.0/12"))

def _is_trusted_proxy(ip: str) -> bool:
    try: addr = ipaddress.ip_address(ip)
    except ValueError: return False
    return any(addr in net for net in TRUSTED_PROXIES)

def _from_trusted_proxy(request: Request) -> bool:
    peer = request.client.host if request.client is not None else ""
    return _is_trusted_proxy(peer)

def get_client_ip(request: Request) -> str:
    # jztech_core.net.real_ip: X-Forwarded-For solo desde TRUSTED_PROXIES, recorrido de derecha a izquierda.
    if request.client is None:
        return "Unknown"
    return real_ip(request, TRUSTED_PROXIES)

def get_request_scheme(request: Request) -> str:
    if _from_trusted_proxy(request):
        proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
        if proto in ("http", "https"):
            return proto
    return request.url.scheme

def is_private_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return addr.is_private or addr.is_loopback

class DB:
    pool: Optional[asyncpg.Pool] = None

async def get_db_connection():
    if DB.pool is None: raise HTTPException(status_code=503, detail="Servicio de base de datos no disponible.")
    async with DB.pool.acquire() as conn: yield conn

async def log_action(conn: asyncpg.Connection, username: str, action: str, details: str, ip_address: str = "127.0.0.1"):
    try: await conn.execute("INSERT INTO audit_logs (username, action, details) VALUES ($1, $2, $3)", username, action, f"[{ip_address}] {details}")
    except Exception as e: print(f"[AUDIT LOG ERROR] {action} ({username}): {e!r}")

# === PROTECCIÓN ANTI-FUERZA BRUTA DINÁMICA ===
# Limite de intentos de login. La clave es IP + usuario: en un deposito todos salen por la misma IP, y
# con una clave solo por IP un usuario equivocado bloqueaba a todos y cualquier login correcto desde
# esa IP borraba el contador (con una cuenta propia se podian probar claves sin limite contra otra).
def login_limit_key(ip: str, user_ref: str) -> str:
    return f"{ip}|{user_ref}"[:255]

async def _login_limits(conn: asyncpg.Connection):
    max_attempts_str = await conn.fetchval("SELECT value FROM system_settings WHERE key = 'max_login_attempts'") or "5"
    lockout_mins_str = await conn.fetchval("SELECT value FROM system_settings WHERE key = 'lockout_time_minutes'") or "15"
    try: max_attempts = int(max_attempts_str)
    except ValueError: max_attempts = 5
    try: lockout_mins = int(lockout_mins_str)
    except ValueError: lockout_mins = 15
    return max_attempts, lockout_mins

async def reserve_login_attempt(key: str, conn: asyncpg.Connection) -> None:
    """Cuenta el intento ANTES de verificar la clave, en una sola sentencia: intentos simultaneos no
    pueden pasar todos el control. 429 si la clave esta bloqueada o se pasa del maximo."""
    max_attempts, lockout_mins = await _login_limits(conn)
    row = await conn.fetchrow("""
        INSERT INTO auth_rate_limits (ip_address, attempts) VALUES ($1, 1)
        ON CONFLICT (ip_address) DO UPDATE SET
            attempts = CASE WHEN auth_rate_limits.blocked_until <= now() THEN 1 ELSE auth_rate_limits.attempts + 1 END,
            blocked_until = CASE WHEN auth_rate_limits.blocked_until <= now() THEN NULL ELSE auth_rate_limits.blocked_until END
        RETURNING attempts, blocked_until
    """, key)
    now = datetime.now(timezone.utc)
    blocked_until = row["blocked_until"]
    if not blocked_until and row["attempts"] > max_attempts:
        blocked_until = now + timedelta(minutes=lockout_mins)
        await conn.execute("UPDATE auth_rate_limits SET blocked_until = $2 WHERE ip_address = $1 AND blocked_until IS NULL", key, blocked_until)
    if blocked_until and now < blocked_until:
        time_left = int((blocked_until - now).total_seconds() / 60) + 1
        raise HTTPException(status_code=429, detail=f"Demasiados intentos fallidos. Bloqueado por {time_left} min.")

async def reset_failed_login(key: str, conn: asyncpg.Connection):
    await conn.execute("DELETE FROM auth_rate_limits WHERE ip_address = $1", key)

# === AUXILIARES DE IDEMPOTENCIA Y SERIALES (WMS ENTERPRISE) ===
async def check_idempotency(conn: asyncpg.Connection, idempotency_key: Optional[str], endpoint: str):
    if not idempotency_key or not idempotency_key.strip():
        return None
    enabled = await conn.fetchval("SELECT value FROM system_settings WHERE key = 'enable_api_idempotency'")
    if enabled != "true":
        return None
        
    row = await conn.fetchrow(
        "SELECT response_body, status_code FROM api_idempotency_keys WHERE idempotency_key = $1 AND endpoint = $2",
        idempotency_key.strip(), endpoint
    )
    if row:
        return json.loads(row["response_body"]), row["status_code"]
    return None

async def save_idempotency(conn: asyncpg.Connection, idempotency_key: Optional[str], endpoint: str, response_data: dict, status_code: int = 200):
    if not idempotency_key or not idempotency_key.strip():
        return
    enabled = await conn.fetchval("SELECT value FROM system_settings WHERE key = 'enable_api_idempotency'")
    if enabled != "true":
        return
    try:
        await conn.execute(
            "INSERT INTO api_idempotency_keys (idempotency_key, endpoint, response_body, status_code) VALUES ($1, $2, $3::jsonb, $4) ON CONFLICT (idempotency_key) DO NOTHING",
            idempotency_key.strip(), endpoint, json.dumps(response_data), status_code
        )
    except Exception as e:
        logger.exception(f"[IDEMPOTENCY SAVE ERROR] {endpoint}: {e!r}")

# === AUXILIARES DE NEGOCIO ===
def build_full_address(street: Optional[str], number: Optional[str], zip_code: Optional[str], city_neighborhood: Optional[str], fallback: Optional[str] = "") -> str:
    parts = []
    st_num = f"{street or ''} {number or ''}".strip()
    if st_num: parts.append(st_num)
    if city_neighborhood and city_neighborhood.strip(): parts.append(city_neighborhood.strip())
    if zip_code and zip_code.strip(): parts.append(f"CP {zip_code.strip()}")
    composed = ", ".join(parts)
    return composed if composed else (fallback or "Dirección no especificada")

# === PROTECCION ANTI-SSRF PARA WEBHOOKS ===
# Los webhooks salen a una URL guardada en integration_channels. Sin control, un destino
# apuntado a la red interna (169.254.169.254 metadata del cloud, la propia DB, el panel)
# convierte el envio en un Server-Side Request Forgery. Por defecto se bloquean destinos
# no publicos; en instalaciones on-premise que necesiten postear a un servicio interno se
# puede permitir con WEBHOOK_ALLOW_PRIVATE=true.
WEBHOOK_ALLOW_PRIVATE = os.getenv("WEBHOOK_ALLOW_PRIVATE", "false").strip().lower() == "true"
WEBHOOK_MAX_REDIRECTS = 3

def _ip_is_blocked(ip_str: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip_str)
    except ValueError:
        return True
    if getattr(addr, "ipv4_mapped", None) is not None:
        return _ip_is_blocked(str(addr.ipv4_mapped))
    return (addr.is_private or addr.is_loopback or addr.is_link_local or
            addr.is_reserved or addr.is_multicast or addr.is_unspecified)

def assert_safe_webhook_url(url: str) -> None:
    """Lanza ValueError si la URL no es un destino publico http/https valido."""
    if WEBHOOK_ALLOW_PRIVATE:
        return
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        raise ValueError("URL de webhook invalida.")
    if parsed.scheme not in ("http", "https"):
        raise ValueError("El webhook solo admite http o https.")
    host = parsed.hostname
    if not host:
        raise ValueError("URL de webhook sin host.")
    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise ValueError("No se pudo resolver el host del webhook.")
    resolved = {info[4][0] for info in infos}
    if not resolved:
        raise ValueError("El host del webhook no resolvio a ninguna IP.")
    for ip in resolved:
        if _ip_is_blocked(ip):
            raise ValueError("El webhook apunta a una direccion interna no permitida.")

class _NoUnsafeRedirect(urllib.request.HTTPRedirectHandler):
    # Un 3xx puede redirigir de un host publico a uno interno: se revalida cada salto.
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        assert_safe_webhook_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)

_webhook_opener = urllib.request.build_opener(_NoUnsafeRedirect())

def send_webhook_sync(url: str, payload: dict, api_key: str = ""):
    try:
        assert_safe_webhook_url(url)
    except ValueError as e:
        logger.warning(f"[WEBHOOK BLOQUEADO] {url!r}: {e}")
        return None, "", "Destino de webhook no permitido."
    headers = {'Content-Type': 'application/json'}
    if api_key and api_key.strip():
        headers['Authorization'] = f"Bearer {api_key.strip()}"
    req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'), headers=headers, method='POST')
    try:
        with _webhook_opener.open(req, timeout=5) as response:
            body = response.read().decode('utf-8', errors='ignore')
            return response.status, body, None
    # El error se guarda en webhook_logs y se le muestra al admin: nunca el texto de la excepcion
    # (puede traer hosts, IPs o puertos internos); el detalle queda en el log del servidor.
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='ignore') if e.fp else ""
        return e.code, body, f"El destino respondió HTTP {e.code}."
    except Exception as e:
        logger.warning(f"[WEBHOOK ERROR] {url!r}: {e!r}")
        return None, "", "No se pudo conectar con el destino del webhook."

async def execute_and_log_webhook(channel_id: Optional[uuid.UUID], channel_name: str, event_type: str, target_url: str, payload: dict, api_key: str = ""):
    if DB.pool is None: return
    status, body, err = await asyncio.to_thread(send_webhook_sync, target_url, payload, api_key)
    log_status = "SUCCESS" if (status and 200 <= status < 300) else "FAILED"
    try:
        async with DB.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO webhook_logs (channel_id, channel_name, event_type, target_url, payload, response_status, response_body, error_message, status)
                VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8, $9)
            """, channel_id, channel_name, event_type, target_url, json.dumps(payload), status, body, err, log_status)
    except Exception as e: print(f"[WEBHOOK LOG ERROR] {channel_name} {event_type}: {e!r}")

async def dispatch_event_to_channels(conn: asyncpg.Connection, event_type: str, payload: dict):
    channels = await conn.fetch("SELECT id, name, target_url, api_key FROM integration_channels WHERE channel_type = $1 AND is_active = TRUE", event_type)
    for ch in channels:
        if ch["target_url"] and ch["target_url"].startswith("http"):
            asyncio.create_task(execute_and_log_webhook(ch["id"], ch["name"], event_type, ch["target_url"], payload, ch["api_key"] or ""))

def parse_uuid(value, mensaje: str = "Identificador inválido.") -> uuid.UUID:
    # Identificadores que vienen del usuario: un valor mal formado (o faltante) es un 400, no un 500.
    try:
        return uuid.UUID(str(value)) if value is not None else uuid.UUID("")
    except ValueError:
        raise HTTPException(400, mensaje)

def require_valid_quantity(quantity: float, allow_zero: bool = False) -> None:
    # Una cantidad negativa invierte el movimiento (una recepcion resta stock, un traspaso lo
    # devuelve al origen) y NaN/infinito llegan a la base (NUMERIC acepta 'Infinity').
    if not math.isfinite(quantity) or quantity < 0 or (quantity == 0 and not allow_zero):
        raise HTTPException(400, "La cantidad no puede ser negativa." if allow_zero else "La cantidad debe ser mayor a cero.")

async def add_system_note(conn: asyncpg.Connection, doc_type: str, doc_id: uuid.UUID, body: str):
    # Observacion automatica (la escribe el sistema, no un usuario). doc_type: ver DOC_TYPES en routers/notes.py.
    await conn.execute("INSERT INTO document_notes (doc_type, doc_id, body, source, username) VALUES ($1, $2, $3, 'SISTEMA', 'SISTEMA')", doc_type, doc_id, body)

async def record_stock_movement(conn: asyncpg.Connection, sku: str, branch_id: uuid.UUID, sector_id: uuid.UUID, location_id: Optional[uuid.UUID], quantity: float, movement_type: str, ref_doc: str, username: str, lot_number: str = "", expiration_date = None, condition: str = "OPERATIVO", serial_numbers: Optional[List[str]] = None):
    cond_clean = condition.strip().upper() if condition else "OPERATIVO"
    # NULL nunca coincide en un indice unico (dos NULL son distintos): sin esto, cada movimiento
    # sin lote crearia una fila de stock nueva en vez de sumar a la existente.
    lot_number = lot_number or ""
    serials_json = json.dumps(serial_numbers) if serial_numbers else "[]"
    
    await conn.execute("""
        INSERT INTO stock_movements (sku, branch_id, sector_id, location_id, quantity, movement_type, reference_document, username, lot_number, expiration_date, condition, serial_numbers) 
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12::jsonb)
    """, sku.upper(), branch_id, sector_id, location_id, quantity, movement_type, ref_doc, username, lot_number, expiration_date, cond_clean, serials_json)
    
    if location_id:
        await conn.execute("""
            INSERT INTO stock_inventory (branch_id, sector_id, location_id, sku, lot_number, expiration_date, quantity, condition) 
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8) 
            ON CONFLICT (branch_id, sector_id, location_id, sku, lot_number, condition) WHERE location_id IS NOT NULL
            DO UPDATE SET quantity = stock_inventory.quantity + EXCLUDED.quantity, updated_at = CURRENT_TIMESTAMP
        """, branch_id, sector_id, location_id, sku.upper(), lot_number, expiration_date, quantity, cond_clean)
    else:
        await conn.execute("""
            INSERT INTO stock_inventory (branch_id, sector_id, location_id, sku, lot_number, expiration_date, quantity, condition) 
            VALUES ($1, $2, NULL, $3, $4, $5, $6, $7) 
            ON CONFLICT (branch_id, sector_id, sku, lot_number, condition) WHERE location_id IS NULL 
            DO UPDATE SET quantity = stock_inventory.quantity + EXCLUDED.quantity, updated_at = CURRENT_TIMESTAMP
        """, branch_id, sector_id, sku.upper(), lot_number, expiration_date, quantity, cond_clean)

    # Procesar Números de Serie Unitorios si aplican
    if serial_numbers and len(serial_numbers) > 0:
        if quantity > 0:
            for sn in serial_numbers:
                sn_clean = sn.strip().upper()
                if sn_clean:
                    await conn.execute("""
                        INSERT INTO item_serials (sku, serial_number, status, branch_id, sector_id, location_id, lot_number)
                        VALUES ($1, $2, 'IN_STOCK', $3, $4, $5, $6)
                        ON CONFLICT (serial_number) DO UPDATE SET 
                            status = 'IN_STOCK', branch_id = EXCLUDED.branch_id, sector_id = EXCLUDED.sector_id, 
                            location_id = EXCLUDED.location_id, updated_at = CURRENT_TIMESTAMP
                    """, sku.upper(), sn_clean, branch_id, sector_id, location_id, lot_number)
        elif quantity < 0:
            status_target = 'DISPATCHED' if 'OUT' in movement_type else 'TRANSFERRED'
            for sn in serial_numbers:
                sn_clean = sn.strip().upper()
                if sn_clean:
                    await conn.execute("""
                        UPDATE item_serials SET status = $1, location_id = NULL, updated_at = CURRENT_TIMESTAMP
                        WHERE UPPER(sku) = $2 AND UPPER(serial_number) = $3
                    """, status_target, sku.upper(), sn_clean)

    # Disponible = solo stock OPERATIVO (lo que esta en cuarentena u otra condicion no se puede vender).
    total_qty = await conn.fetchval("SELECT COALESCE(SUM(quantity), 0) FROM stock_inventory WHERE UPPER(sku) = $1 AND COALESCE(condition, 'OPERATIVO') = 'OPERATIVO'", sku.upper())
    stock_payload = { "event": "stock.updated", "sku": sku.upper(), "available_quantity": float(total_qty), "timestamp": datetime.now(timezone.utc).isoformat() }
    await dispatch_event_to_channels(conn, "OUTBOUND_STOCK", stock_payload)

async def queue_zpl_print_job(conn: asyncpg.Connection, queue_code: str, zpl_content: str):
    await conn.execute("INSERT INTO print_jobs (queue_code, zpl_content, status) VALUES ($1, $2, 'PENDING')", queue_code.strip().upper(), zpl_content)

# === AUTENTICACIÓN BLINDADA ===
async def get_current_user(request: Request, conn: asyncpg.Connection = Depends(get_db_connection)):
    user = await session_user(conn, request)
    if not user:
        raise HTTPException(status_code=401, detail="Sesión expirada.")
    if not user["is_active"]:
        client_ip = get_client_ip(request)
        await log_action(conn, user["username"], "SECURITY_ALERT", f"Usuario desactivado intentó operar desde {client_ip}", client_ip)
        raise HTTPException(status_code=401, detail="Usuario desactivado.")
    await enforce_csrf(request, request.cookies[core_sessions.SESSION_COOKIE_NAME], _CSRF_SUJETO)
    return dict(user)

async def require_admin(request: Request, current_user: dict = Depends(get_current_user), conn: asyncpg.Connection = Depends(get_db_connection)):
    if current_user.get("role") != "ADMIN": 
        client_ip = get_client_ip(request)
        await log_action(conn, current_user.get("username", "Unknown"), "UNAUTHORIZED_ACCESS", f"Intento de escalar privilegios a ruta de Administrador", client_ip)
        raise HTTPException(status_code=403, detail="Permisos insuficientes.")
    return current_user

async def require_supervisor(request: Request, current_user: dict = Depends(get_current_user), conn: asyncpg.Connection = Depends(get_db_connection)):
    # ADMIN o SUPERVISOR. El supervisor solo controla pedidos: su estado y quienes participaron.
    if current_user.get("role") not in ("ADMIN", "SUPERVISOR"):
        client_ip = get_client_ip(request)
        await log_action(conn, current_user.get("username", "Unknown"), "UNAUTHORIZED_ACCESS", f"Intento de acceder a control de pedidos: {request.url.path}", client_ip)
        raise HTTPException(status_code=403, detail="Permisos insuficientes.")
    return current_user

async def verify_system_api_key(request: Request, x_api_key: Optional[str] = Header(None), conn: asyncpg.Connection = Depends(get_db_connection)):
    client_ip = get_client_ip(request)
    
    if not x_api_key: 
        await log_action(conn, "SYSTEM", "API_INTRUSION", f"Acceso a API denegado (Falta Cabecera)", client_ip)
        raise HTTPException(status_code=401, detail="Cabecera X-API-Key requerida.")
    
    valid_key = await conn.fetchval("SELECT value FROM system_settings WHERE key = 'tracker360_api_key'")
    if valid_key and verify_system_key_value(x_api_key, valid_key):
        return True
        
    # Las claves por-canal tambien se guardan hasheadas; se aceptan tanto el hash (ya migradas)
    # como el valor en claro (recien insertadas por SQL, aun sin migrar).
    valid_channel = await conn.fetchval(
        "SELECT name FROM inbound_api_keys WHERE api_key = ANY($1::text[]) AND is_active = TRUE",
        [hash_system_api_key(x_api_key), x_api_key.strip()])
    if valid_channel:
        return valid_channel
        
    await log_action(conn, "SYSTEM", "API_INTRUSION", f"Intento de acceso a API con clave inválida", client_ip)
    raise HTTPException(status_code=403, detail="Clave API inválida.")

# === INICIALIZACIÓN DE TABLAS (DDL) ===
MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
async def init_db_schema():
    for attempt in range(10):
        try:
            DB.pool = await asyncpg.create_pool(
                user=os.getenv("POSTGRES_USER", "tracker_admin"),
                password=os.getenv("POSTGRES_PASSWORD", secrets.token_hex(24)),
                database=os.getenv("POSTGRES_DB", "tracker360_db"),
                host=os.getenv("POSTGRES_HOST", "db"),
                port=int(os.getenv("POSTGRES_PORT", "5432")),
                min_size=1, max_size=int(os.getenv("DB_POOL_MAX", "20"))
            )
            if DB.pool is not None: break
        except Exception as e:
            logger.warning(f"[DB] Intento {attempt + 1}/10 de conexion a PostgreSQL fallido: {e!r}")
            await asyncio.sleep(1.0)

    if DB.pool is None:
        logger.error("[DB] No se pudo conectar a PostgreSQL: la API respondera 503 hasta reiniciar el servicio.")

    if DB.pool is not None:
        # Esquema: migraciones versionadas (backend/migrations/NNNN_*.sql, jztech_core.migrations).
        # Cada archivo corre una sola vez y en su propia transaccion; si uno falla se corta el
        # arranque con el error en el log (la base queda en la version anterior, no a medias).
        async with DB.pool.acquire() as conn:
            aplicadas = await apply_migrations(_ConexionComoPool(conn), MIGRATIONS_DIR)
        if aplicadas:
            logger.info(f"[DB] Migraciones aplicadas: {aplicadas}")
        try:
            async with DB.pool.acquire() as conn:
                # El primer usuario administrador ya no se auto-crea acá: si la tabla users está vacía,
                # el frontend muestra la pantalla de configuración inicial (POST /api/auth/setup/admin),
                # que exige el SETUP_TOKEN generado por install.sh.

                sys_key = await conn.fetchval("SELECT value FROM system_settings WHERE key = 'tracker360_api_key'")
                if not sys_key:
                    # Clave inicial: se guarda hasheada. Es un marcador; el admin genera una real
                    # (que se muestra una sola vez) desde el panel para poder usarla.
                    new_key = f"trk_live_{secrets.token_hex(24)}"
                    hashed = hash_system_api_key(new_key)
                    await conn.execute("INSERT INTO system_settings (key, value) VALUES ('tracker360_api_key', $1) ON CONFLICT (key) DO NOTHING", hashed)
                    await conn.execute("INSERT INTO system_settings (key, value) VALUES ('api_key', $1) ON CONFLICT (key) DO NOTHING", hashed)
                elif not sys_key.startswith(SYSTEM_KEY_PREFIX):
                    # Migracion: instalacion previa con la clave en claro -> se reemplaza por su hash.
                    hashed = hash_system_api_key(sys_key)
                    await conn.execute("UPDATE system_settings SET value = $1 WHERE key IN ('tracker360_api_key', 'api_key')", hashed)
                    logger.info("[DB] Clave API del sistema migrada a hash en reposo.")

                # Migracion de las claves por-canal en claro a hash.
                legacy_inbound = await conn.fetch("SELECT id, api_key FROM inbound_api_keys WHERE api_key NOT LIKE $1", SYSTEM_KEY_PREFIX + "%")
                for row in legacy_inbound:
                    await conn.execute("UPDATE inbound_api_keys SET api_key = $1 WHERE id = $2", hash_system_api_key(row["api_key"]), row["id"])
                if legacy_inbound:
                    logger.info(f"[DB] {len(legacy_inbound)} clave(s) de canal migrada(s) a hash en reposo.")

                branch_count = await conn.fetchval("SELECT COUNT(*) FROM branches")
                if branch_count == 0:
                    default_branch_id = await conn.fetchval("INSERT INTO branches (code, name) VALUES ('SUC-01', 'Sucursal Central') RETURNING id")
                    await conn.execute("UPDATE sectors SET branch_id = $1 WHERE branch_id IS NULL", default_branch_id)
        except Exception as e:
            logger.exception(f"[DB] Error inicializando el esquema: {e!r}")