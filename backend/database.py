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
async def check_rate_limit(ip: str, conn: asyncpg.Connection):
    now = datetime.now(timezone.utc)
    row = await conn.fetchrow("SELECT attempts, blocked_until FROM auth_rate_limits WHERE ip_address = $1", ip)
    if row:
        if row["blocked_until"] and now < row["blocked_until"]:
            time_left = int((row["blocked_until"] - now).total_seconds() / 60) + 1
            raise HTTPException(status_code=429, detail=f"Demasiados intentos fallidos. Bloqueado por {time_left} min.")
        elif row["blocked_until"] and now >= row["blocked_until"]:
            await conn.execute("UPDATE auth_rate_limits SET attempts = 0, blocked_until = NULL WHERE ip_address = $1", ip)

async def record_failed_login(ip: str, conn: asyncpg.Connection):
    now = datetime.now(timezone.utc)
    await conn.execute("""
        INSERT INTO auth_rate_limits (ip_address, attempts) VALUES ($1, 1)
        ON CONFLICT (ip_address) DO UPDATE SET attempts = auth_rate_limits.attempts + 1
    """, ip)
    
    attempts = await conn.fetchval("SELECT attempts FROM auth_rate_limits WHERE ip_address = $1", ip)
    max_attempts_str = await conn.fetchval("SELECT value FROM system_settings WHERE key = 'max_login_attempts'") or "5"
    lockout_mins_str = await conn.fetchval("SELECT value FROM system_settings WHERE key = 'lockout_time_minutes'") or "15"
    
    try: max_attempts = int(max_attempts_str)
    except ValueError: max_attempts = 5
    
    try: lockout_mins = int(lockout_mins_str)
    except ValueError: lockout_mins = 15

    if attempts >= max_attempts:
        await conn.execute("UPDATE auth_rate_limits SET blocked_until = $1 WHERE ip_address = $2", now + timedelta(minutes=lockout_mins), ip)

async def reset_failed_login(ip: str, conn: asyncpg.Connection):
    await conn.execute("DELETE FROM auth_rate_limits WHERE ip_address = $1", ip)

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
        try:
            async with DB.pool.acquire() as conn:
                ddl_statements = [
                    "CREATE TABLE IF NOT EXISTS system_settings (key VARCHAR(100) PRIMARY KEY, value TEXT);",
                    "CREATE TABLE IF NOT EXISTS users (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), username VARCHAR(50) UNIQUE NOT NULL, full_name VARCHAR(100) NOT NULL, password_hash TEXT NOT NULL, role VARCHAR(20) NOT NULL DEFAULT 'PREPARADOR', is_active BOOLEAN DEFAULT TRUE, created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP);",
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS email VARCHAR(150);",
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS token_version INTEGER NOT NULL DEFAULT 0;",
                    core_sessions.CREATE_TABLE_SQL,
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS branch_id VARCHAR(100);",
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS sector_id VARCHAR(100);",
                    "CREATE TABLE IF NOT EXISTS branches (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), code VARCHAR(50) UNIQUE NOT NULL, name VARCHAR(150) NOT NULL, is_active BOOLEAN DEFAULT TRUE, created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP);",
                    "CREATE TABLE IF NOT EXISTS entities (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), tax_id VARCHAR(50) UNIQUE NOT NULL, company_name VARCHAR(150) NOT NULL, is_customer BOOLEAN DEFAULT TRUE, is_supplier BOOLEAN DEFAULT FALSE, is_active BOOLEAN DEFAULT TRUE, created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP);",
                    "CREATE TABLE IF NOT EXISTS entity_addresses (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), entity_id UUID REFERENCES entities(id) ON DELETE CASCADE, address_label VARCHAR(100) NOT NULL, full_address TEXT NOT NULL, created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP);",
                    "ALTER TABLE entity_addresses ADD COLUMN IF NOT EXISTS street VARCHAR(150);",
                    "ALTER TABLE entity_addresses ADD COLUMN IF NOT EXISTS number VARCHAR(50);",
                    "ALTER TABLE entity_addresses ADD COLUMN IF NOT EXISTS zip_code VARCHAR(20);",
                    "ALTER TABLE entity_addresses ADD COLUMN IF NOT EXISTS city_neighborhood VARCHAR(150);",
                    "ALTER TABLE entity_addresses ADD COLUMN IF NOT EXISTS is_default BOOLEAN DEFAULT FALSE;",
                    "CREATE TABLE IF NOT EXISTS items (sku VARCHAR(100) PRIMARY KEY, description TEXT NOT NULL, category VARCHAR(100), length FLOAT DEFAULT 0, width FLOAT DEFAULT 0, height FLOAT DEFAULT 0, weight FLOAT DEFAULT 0, volume FLOAT DEFAULT 0, is_active BOOLEAN DEFAULT TRUE, created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP);",
                    "ALTER TABLE items ADD COLUMN IF NOT EXISTS is_combo BOOLEAN DEFAULT FALSE;",
                    
                    "CREATE TABLE IF NOT EXISTS item_combos (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), combo_sku VARCHAR(100) NOT NULL REFERENCES items(sku) ON DELETE CASCADE, component_sku VARCHAR(100) NOT NULL REFERENCES items(sku) ON DELETE CASCADE, quantity NUMERIC NOT NULL DEFAULT 1, created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP, UNIQUE(combo_sku, component_sku));",

                    # TABLA DDL RFID & SNT
                    "CREATE TABLE IF NOT EXISTS rfid_tags (epc VARCHAR(100) PRIMARY KEY, sku VARCHAR(100) NOT NULL REFERENCES items(sku) ON DELETE CASCADE, created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP, created_by VARCHAR(50));",
                    "CREATE INDEX IF NOT EXISTS idx_rfid_tags_sku ON rfid_tags (sku);",

                    # TABLA DDL IDEMPOTENCIA API
                    "CREATE TABLE IF NOT EXISTS api_idempotency_keys (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), idempotency_key VARCHAR(255) UNIQUE NOT NULL, endpoint VARCHAR(255) NOT NULL, response_body JSONB NOT NULL, status_code INT NOT NULL, created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP);",

                    "CREATE TABLE IF NOT EXISTS sectors (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), branch_id UUID REFERENCES branches(id) ON DELETE CASCADE, name VARCHAR(100) UNIQUE NOT NULL, print_queue_code VARCHAR(50) UNIQUE NOT NULL, uses_locations BOOLEAN DEFAULT FALSE, is_active BOOLEAN DEFAULT TRUE, created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP);",
                    "CREATE TABLE IF NOT EXISTS locations (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), sector_id UUID REFERENCES sectors(id) ON DELETE CASCADE, location_code VARCHAR(100) NOT NULL, description VARCHAR(255), is_active BOOLEAN DEFAULT TRUE);",
                    "CREATE TABLE IF NOT EXISTS item_locations (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), item_sku VARCHAR(100) NOT NULL, location_id UUID REFERENCES locations(id) ON DELETE CASCADE, created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP, UNIQUE(item_sku, location_id));",

                    # item_serials referencia branches, sectors y locations: va despues de crearlas.
                    "CREATE TABLE IF NOT EXISTS item_serials (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), sku VARCHAR(100) NOT NULL REFERENCES items(sku) ON DELETE CASCADE, serial_number VARCHAR(100) UNIQUE NOT NULL, status VARCHAR(50) DEFAULT 'IN_STOCK', branch_id UUID REFERENCES branches(id) ON DELETE SET NULL, sector_id UUID REFERENCES sectors(id) ON DELETE SET NULL, location_id UUID REFERENCES locations(id) ON DELETE SET NULL, lot_number VARCHAR(100) DEFAULT '', created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP);",
                    "CREATE INDEX IF NOT EXISTS idx_item_serials_sku_sn ON item_serials (sku, serial_number);",
                    "CREATE TABLE IF NOT EXISTS documents (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), document_number VARCHAR(50) UNIQUE NOT NULL, document_type VARCHAR(20) DEFAULT 'PICKING', channel_origin VARCHAR(50) DEFAULT 'INTERNAL', status VARCHAR(20) DEFAULT 'PENDING', label_printed BOOLEAN DEFAULT FALSE, created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP);",
                    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS channel_origin VARCHAR(50) DEFAULT 'INTERNAL';",
                    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS customer_id UUID;",
                    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS customer_address_id UUID;",
                    # Las FK se crean solo si faltan: borrarlas y recrearlas en cada arranque bloqueaba la
                    # tabla y, si fallaba el ADD (filas huerfanas), la FK quedaba borrada.
                    "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'documents_customer_id_fkey') THEN ALTER TABLE documents ADD CONSTRAINT documents_customer_id_fkey FOREIGN KEY (customer_id) REFERENCES entities(id) ON DELETE SET NULL; END IF; END $$;",
                    "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'documents_customer_address_id_fkey') THEN ALTER TABLE documents ADD CONSTRAINT documents_customer_address_id_fkey FOREIGN KEY (customer_address_id) REFERENCES entity_addresses(id) ON DELETE SET NULL; END IF; END $$;",
                    
                    "CREATE TABLE IF NOT EXISTS document_lines (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), document_id UUID REFERENCES documents(id) ON DELETE CASCADE, sku VARCHAR(100) NOT NULL, quantity_requested NUMERIC NOT NULL, quantity_picked NUMERIC DEFAULT 0);",
                    "ALTER TABLE document_lines ADD COLUMN IF NOT EXISTS serial_numbers JSONB DEFAULT '[]'::jsonb;",

                    "CREATE TABLE IF NOT EXISTS audit_logs (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), username VARCHAR(50) NOT NULL, action VARCHAR(50) NOT NULL, details TEXT, created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP);",
                    "CREATE TABLE IF NOT EXISTS print_jobs (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), queue_code VARCHAR(50) NOT NULL, zpl_content TEXT NOT NULL, status VARCHAR(20) DEFAULT 'PENDING', created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP);",
                    
                    "CREATE TABLE IF NOT EXISTS stock_inventory (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), branch_id UUID REFERENCES branches(id) ON DELETE CASCADE, sector_id UUID REFERENCES sectors(id) ON DELETE CASCADE, location_id UUID REFERENCES locations(id) ON DELETE CASCADE, sku VARCHAR(100) NOT NULL, quantity NUMERIC DEFAULT 0, updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP);",
                    "ALTER TABLE stock_inventory ADD COLUMN IF NOT EXISTS lot_number VARCHAR(100) DEFAULT '';",
                    "ALTER TABLE stock_inventory ADD COLUMN IF NOT EXISTS expiration_date DATE;",
                    "ALTER TABLE stock_inventory ADD COLUMN IF NOT EXISTS condition VARCHAR(50) DEFAULT 'OPERATIVO';",
                    
                    "DROP INDEX IF EXISTS idx_stock_loc_sku;",
                    "DROP INDEX IF EXISTS idx_stock_noloc_sku;",
                    "DROP INDEX IF EXISTS idx_stock_loc_sku_lot;",
                    "DROP INDEX IF EXISTS idx_stock_noloc_sku_lot;",
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_stock_loc_sku_lot_cond ON stock_inventory (branch_id, sector_id, location_id, sku, lot_number, condition) WHERE location_id IS NOT NULL;",
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_stock_noloc_sku_lot_cond ON stock_inventory (branch_id, sector_id, sku, lot_number, condition) WHERE location_id IS NULL;",
                    
                    "CREATE TABLE IF NOT EXISTS stock_movements (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), sku VARCHAR(100) NOT NULL, branch_id UUID REFERENCES branches(id), sector_id UUID REFERENCES sectors(id), location_id UUID REFERENCES locations(id), quantity NUMERIC NOT NULL, movement_type VARCHAR(50) NOT NULL, reference_document VARCHAR(100), username VARCHAR(50) NOT NULL, created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP);",
                    "ALTER TABLE stock_movements ADD COLUMN IF NOT EXISTS lot_number VARCHAR(100) DEFAULT '';",
                    # "Retroceso de PDV ID:<pedido>" (cancelaciones) no entra en 50 con numeros largos.
                    "ALTER TABLE stock_movements ALTER COLUMN movement_type TYPE VARCHAR(100);",
                    "ALTER TABLE stock_movements ADD COLUMN IF NOT EXISTS expiration_date DATE;",
                    "ALTER TABLE stock_movements ADD COLUMN IF NOT EXISTS condition VARCHAR(50) DEFAULT 'OPERATIVO';",
                    "ALTER TABLE stock_movements ADD COLUMN IF NOT EXISTS serial_numbers JSONB DEFAULT '[]'::jsonb;",

                    "CREATE TABLE IF NOT EXISTS customer_returns (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), return_number VARCHAR(50) UNIQUE NOT NULL, customer_id UUID REFERENCES entities(id), document_id UUID REFERENCES documents(id), branch_id UUID REFERENCES branches(id), sector_id UUID REFERENCES sectors(id), status VARCHAR(20) DEFAULT 'COMPLETED', created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP, created_by VARCHAR(50));",
                    "CREATE TABLE IF NOT EXISTS customer_return_lines (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), return_id UUID REFERENCES customer_returns(id) ON DELETE CASCADE, sku VARCHAR(100) NOT NULL, quantity NUMERIC NOT NULL, condition VARCHAR(50) DEFAULT 'OPERATIVO', location_id UUID REFERENCES locations(id));",
                    "ALTER TABLE customer_return_lines ADD COLUMN IF NOT EXISTS serial_numbers JSONB DEFAULT '[]'::jsonb;",

                    "CREATE TABLE IF NOT EXISTS purchase_orders (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), order_number VARCHAR(50) UNIQUE NOT NULL, supplier_id UUID REFERENCES entities(id), status VARCHAR(20) DEFAULT 'PENDING', created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP);",
                    "CREATE TABLE IF NOT EXISTS purchase_order_lines (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), purchase_order_id UUID REFERENCES purchase_orders(id) ON DELETE CASCADE, sku VARCHAR(100) NOT NULL, quantity_ordered NUMERIC NOT NULL, quantity_received NUMERIC DEFAULT 0);",
                    "CREATE TABLE IF NOT EXISTS purchase_remitos (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), remito_number VARCHAR(50) UNIQUE NOT NULL, supplier_id UUID REFERENCES entities(id), purchase_order_id UUID REFERENCES purchase_orders(id), branch_id UUID REFERENCES branches(id), sector_id UUID REFERENCES sectors(id), status VARCHAR(20) DEFAULT 'PENDING_CONTROL', created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP);",
                    "CREATE TABLE IF NOT EXISTS purchase_remito_lines (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), purchase_remito_id UUID REFERENCES purchase_remitos(id) ON DELETE CASCADE, sku VARCHAR(100) NOT NULL, quantity_sent NUMERIC NOT NULL, quantity_received NUMERIC DEFAULT 0, location_id UUID REFERENCES locations(id));",
                    "ALTER TABLE purchase_remito_lines ADD COLUMN IF NOT EXISTS serial_numbers JSONB DEFAULT '[]'::jsonb;",

                    "CREATE TABLE IF NOT EXISTS purchase_invoices (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), invoice_number VARCHAR(50) UNIQUE NOT NULL, supplier_id UUID REFERENCES entities(id), invoice_type VARCHAR(10) DEFAULT 'A', created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP);",
                    "CREATE TABLE IF NOT EXISTS purchase_invoice_lines (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), purchase_invoice_id UUID REFERENCES purchase_invoices(id) ON DELETE CASCADE, sku VARCHAR(100) NOT NULL, quantity NUMERIC NOT NULL, unit_price NUMERIC DEFAULT 0);",
                    "CREATE TABLE IF NOT EXISTS purchase_invoice_remitos (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), purchase_invoice_id UUID REFERENCES purchase_invoices(id) ON DELETE CASCADE, purchase_remito_id UUID REFERENCES purchase_remitos(id) ON DELETE CASCADE);",
                    "CREATE TABLE IF NOT EXISTS purchase_invoice_orders (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), purchase_invoice_id UUID REFERENCES purchase_invoices(id) ON DELETE CASCADE, purchase_order_id UUID REFERENCES purchase_orders(id) ON DELETE CASCADE);",
                    
                    "CREATE TABLE IF NOT EXISTS transfer_orders (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), transfer_number VARCHAR(50) UNIQUE NOT NULL, origin_branch_id UUID REFERENCES branches(id), origin_sector_id UUID REFERENCES sectors(id), destination_branch_id UUID REFERENCES branches(id), destination_sector_id UUID REFERENCES sectors(id), status VARCHAR(20) DEFAULT 'PENDING_CONTROL', created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP);",
                    "ALTER TABLE transfer_orders ADD COLUMN IF NOT EXISTS created_by VARCHAR(50);",
                    "CREATE TABLE IF NOT EXISTS transfer_order_lines (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), transfer_order_id UUID REFERENCES transfer_orders(id) ON DELETE CASCADE, sku VARCHAR(100) NOT NULL, quantity_sent NUMERIC NOT NULL, quantity_received NUMERIC DEFAULT 0, origin_location_id UUID REFERENCES locations(id), destination_location_id UUID REFERENCES locations(id));",
                    "ALTER TABLE transfer_order_lines ADD COLUMN IF NOT EXISTS lot_number VARCHAR(100) DEFAULT '';",
                    "ALTER TABLE transfer_order_lines ADD COLUMN IF NOT EXISTS serial_numbers JSONB DEFAULT '[]'::jsonb;",

                    "CREATE TABLE IF NOT EXISTS integration_channels (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), name VARCHAR(100) NOT NULL, channel_type VARCHAR(50) NOT NULL, target_url TEXT NOT NULL, api_key TEXT, is_active BOOLEAN DEFAULT TRUE, created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP);",

                    "CREATE TABLE IF NOT EXISTS webhook_logs (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), channel_id UUID REFERENCES integration_channels(id) ON DELETE SET NULL, channel_name VARCHAR(100) NOT NULL, event_type VARCHAR(50) NOT NULL, target_url TEXT NOT NULL, payload JSONB NOT NULL, response_status INT, response_body TEXT, error_message TEXT, status VARCHAR(20) DEFAULT 'PENDING', created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP);",

                    "CREATE TABLE IF NOT EXISTS inventory_sessions (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), branch_id UUID REFERENCES branches(id), sector_id UUID REFERENCES sectors(id), count_type VARCHAR(20) NOT NULL DEFAULT 'HOT', status VARCHAR(20) NOT NULL DEFAULT 'OPEN', created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP, created_by VARCHAR(50), closed_at TIMESTAMP WITH TIME ZONE, closed_by VARCHAR(50));",
                    "ALTER TABLE inventory_sessions ADD COLUMN IF NOT EXISTS assigned_operator VARCHAR(50);",
                    "CREATE TABLE IF NOT EXISTS inventory_snapshots (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), session_id UUID REFERENCES inventory_sessions(id) ON DELETE CASCADE, sku VARCHAR(100) NOT NULL, location_id UUID REFERENCES locations(id), lot_number VARCHAR(100) DEFAULT '', expected_quantity NUMERIC DEFAULT 0);",
                    "CREATE TABLE IF NOT EXISTS inventory_counts (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), session_id UUID REFERENCES inventory_sessions(id) ON DELETE CASCADE, sku VARCHAR(100) NOT NULL, location_id UUID REFERENCES locations(id), lot_number VARCHAR(100) DEFAULT '', counted_quantity NUMERIC NOT NULL, scanned_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP, scanned_by VARCHAR(50));",

                    "CREATE TABLE IF NOT EXISTS auth_rate_limits (ip_address VARCHAR(50) PRIMARY KEY, attempts INT DEFAULT 0, blocked_until TIMESTAMP WITH TIME ZONE);",
                    "CREATE TABLE IF NOT EXISTS inbound_api_keys (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), name VARCHAR(100) NOT NULL, api_key TEXT UNIQUE NOT NULL, is_active BOOLEAN DEFAULT TRUE, created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP);",

                    # LOGIN DEL AGENTE DE IMPRESION POR NAVEGADOR (codigo de un solo uso + PKCE, tokens guardados como hash)
                    "CREATE TABLE IF NOT EXISTS print_agent_auth_codes (code_hash VARCHAR(64) PRIMARY KEY, challenge VARCHAR(64) NOT NULL, agent_name VARCHAR(100) NOT NULL, username VARCHAR(50) NOT NULL, expires_at TIMESTAMP WITH TIME ZONE NOT NULL, used BOOLEAN DEFAULT FALSE);",
                    "CREATE TABLE IF NOT EXISTS print_agent_tokens (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), token_hash VARCHAR(64) UNIQUE NOT NULL, agent_name VARCHAR(100) NOT NULL, created_by VARCHAR(50) NOT NULL, is_active BOOLEAN DEFAULT TRUE, created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP, last_used_at TIMESTAMP WITH TIME ZONE);",

                    # CONTROL FISICO DE REMITOS: lineas agregadas en el control (excedentes y no esperados
                    # aprobados) y articulos no esperados en cuarentena hasta que ADMIN/SUPERVISOR resuelve.
                    "ALTER TABLE purchase_remito_lines ADD COLUMN IF NOT EXISTS added_in_control BOOLEAN NOT NULL DEFAULT FALSE;",
                    "CREATE TABLE IF NOT EXISTS reception_exceptions (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), remito_id UUID NOT NULL REFERENCES purchase_remitos(id) ON DELETE CASCADE, sku VARCHAR(100) NOT NULL, quantity NUMERIC NOT NULL, location_id UUID REFERENCES locations(id), lot_number VARCHAR(100) DEFAULT '', status VARCHAR(20) NOT NULL DEFAULT 'PENDING', reported_by VARCHAR(50), resolved_by VARCHAR(50), resolved_at TIMESTAMP WITH TIME ZONE, created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP);",

                    # REMITOS DE COMPRA: cada linea puede venir de una linea de OC (vacio = articulo suelto)
                    "ALTER TABLE purchase_remitos ADD COLUMN IF NOT EXISTS created_by VARCHAR(50);",
                    # El numero de remito lo pone el proveedor: es unico por proveedor, no en todo el sistema.
                    "ALTER TABLE purchase_remitos DROP CONSTRAINT IF EXISTS purchase_remitos_remito_number_key;",
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_purchase_remitos_supplier_number ON purchase_remitos (supplier_id, UPPER(remito_number));",
                    "ALTER TABLE purchase_remito_lines ADD COLUMN IF NOT EXISTS purchase_order_line_id UUID REFERENCES purchase_order_lines(id);",
                    "ALTER TABLE purchase_remito_lines ADD COLUMN IF NOT EXISTS lot_number VARCHAR(100) DEFAULT '';",

                    # ORDENES DE COMPRA: sucursal de recepcion (su direccion es la de entrega) y quien la emitio
                    "ALTER TABLE purchase_orders ADD COLUMN IF NOT EXISTS branch_id UUID REFERENCES branches(id);",
                    "ALTER TABLE purchase_orders ADD COLUMN IF NOT EXISTS created_by VARCHAR(50);",

                    # DIRECCION DE CADA SUCURSAL (la usan las ordenes de compra como direccion de recepcion)
                    "ALTER TABLE branches ADD COLUMN IF NOT EXISTS street VARCHAR(150) DEFAULT '';",
                    "ALTER TABLE branches ADD COLUMN IF NOT EXISTS number VARCHAR(20) DEFAULT '';",
                    "ALTER TABLE branches ADD COLUMN IF NOT EXISTS zip_code VARCHAR(20) DEFAULT '';",
                    "ALTER TABLE branches ADD COLUMN IF NOT EXISTS city VARCHAR(100) DEFAULT '';",

                    # OBSERVACIONES DE DOCUMENTOS (solo se agregan: no se editan ni se borran)
                    "CREATE TABLE IF NOT EXISTS document_notes (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), doc_type VARCHAR(20) NOT NULL, doc_id UUID NOT NULL, body TEXT NOT NULL, source VARCHAR(10) NOT NULL DEFAULT 'USUARIO', username VARCHAR(100), created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP);",
                    "CREATE INDEX IF NOT EXISTS idx_document_notes_doc ON document_notes (doc_type, doc_id, created_at);",

                    # INSERTS DE CONFIGURACIONES INICIALES ENTERPRISE
                    "INSERT INTO system_settings (key, value) VALUES ('allow_multiproduct_locations', 'false') ON CONFLICT (key) DO NOTHING;",
                    "INSERT INTO system_settings (key, value) VALUES ('require_mobile_reception', 'false') ON CONFLICT (key) DO NOTHING;",
                    "INSERT INTO system_settings (key, value) VALUES ('enable_item_dimensions', 'false') ON CONFLICT (key) DO NOTHING;",
                    "INSERT INTO system_settings (key, value) VALUES ('enable_lots_expiration', 'false') ON CONFLICT (key) DO NOTHING;",
                    "INSERT INTO system_settings (key, value) VALUES ('enable_stock_conditions', 'false') ON CONFLICT (key) DO NOTHING;",
                    "INSERT INTO system_settings (key, value) VALUES ('enable_quarantine_on_return', 'false') ON CONFLICT (key) DO NOTHING;",
                    "INSERT INTO system_settings (key, value) VALUES ('return_number_prefix', 'DEV-') ON CONFLICT (key) DO NOTHING;",
                    "INSERT INTO system_settings (key, value) VALUES ('app_name', 'Tracker360') ON CONFLICT (key) DO NOTHING;",
                    "INSERT INTO system_settings (key, value) VALUES ('company_cuit', '30-00000000-0') ON CONFLICT (key) DO NOTHING;",
                    "INSERT INTO system_settings (key, value) VALUES ('session_timeout_minutes', '240') ON CONFLICT (key) DO NOTHING;",
                    "INSERT INTO system_settings (key, value) VALUES ('max_login_attempts', '5') ON CONFLICT (key) DO NOTHING;",
                    "INSERT INTO system_settings (key, value) VALUES ('lockout_time_minutes', '15') ON CONFLICT (key) DO NOTHING;",
                    "INSERT INTO system_settings (key, value) VALUES ('enable_google_sso', 'false') ON CONFLICT (key) DO NOTHING;",
                    "INSERT INTO system_settings (key, value) VALUES ('google_client_id', '') ON CONFLICT (key) DO NOTHING;",
                    "INSERT INTO system_settings (key, value) VALUES ('google_client_secret', '') ON CONFLICT (key) DO NOTHING;",
                    "INSERT INTO system_settings (key, value) VALUES ('google_allowed_domain', '') ON CONFLICT (key) DO NOTHING;",
                    "INSERT INTO system_settings (key, value) VALUES ('transfer_number_prefix', 'TR-') ON CONFLICT (key) DO NOTHING;",
                    "INSERT INTO system_settings (key, value) VALUES ('sales_order_prefix', 'PED-') ON CONFLICT (key) DO NOTHING;",
                    "INSERT INTO system_settings (key, value) VALUES ('correlative_zeros_pad', '6') ON CONFLICT (key) DO NOTHING;",
                    "INSERT INTO system_settings (key, value) VALUES ('auto_complete_picking', 'true') ON CONFLICT (key) DO NOTHING;",
                    "INSERT INTO system_settings (key, value) VALUES ('default_print_queue', 'PRINT-SEC-01') ON CONFLICT (key) DO NOTHING;",
                    "INSERT INTO system_settings (key, value) VALUES ('default_inventory_count_type', 'HOT') ON CONFLICT (key) DO NOTHING;",
                    "INSERT INTO system_settings (key, value) VALUES ('enable_api_idempotency', 'false') ON CONFLICT (key) DO NOTHING;",
                    "INSERT INTO system_settings (key, value) VALUES ('enable_serial_tracking', 'false') ON CONFLICT (key) DO NOTHING;",
                    "INSERT INTO system_settings (key, value) VALUES ('enable_putaway_suggestions', 'false') ON CONFLICT (key) DO NOTHING;",
                    "INSERT INTO system_settings (key, value) VALUES ('enable_replenishment', 'false') ON CONFLICT (key) DO NOTHING;",
                    "INSERT INTO system_settings (key, value) VALUES ('enable_wave_picking', 'false') ON CONFLICT (key) DO NOTHING;",
                    "INSERT INTO system_settings (key, value) VALUES ('enable_optimal_routing', 'false') ON CONFLICT (key) DO NOTHING;",
                    "INSERT INTO system_settings (key, value) VALUES ('enable_packing_station', 'false') ON CONFLICT (key) DO NOTHING;",
                    "INSERT INTO system_settings (key, value) VALUES ('enable_labor_management', 'false') ON CONFLICT (key) DO NOTHING;",
                    "INSERT INTO system_settings (key, value) VALUES ('zpl_item_width', '38') ON CONFLICT (key) DO NOTHING;",
                    "INSERT INTO system_settings (key, value) VALUES ('zpl_item_height', '20') ON CONFLICT (key) DO NOTHING;",
                    "INSERT INTO system_settings (key, value) VALUES ('zpl_order_width', '100') ON CONFLICT (key) DO NOTHING;",
                    "INSERT INTO system_settings (key, value) VALUES ('zpl_order_height', '150') ON CONFLICT (key) DO NOTHING;",
                    "INSERT INTO system_settings (key, value) VALUES ('zpl_location_width', '50') ON CONFLICT (key) DO NOTHING;",
                    "INSERT INTO system_settings (key, value) VALUES ('zpl_location_height', '25') ON CONFLICT (key) DO NOTHING;",
                    "INSERT INTO system_settings (key, value) VALUES ('zpl_order_template', '^XA^FO50,50^A0N,40,40^FDPEDIDO: {{ORDER_NUM}}^FS^FO50,110^A0N,30,30^FDCLIENTE: {{DESTINATION}}^FS^FO50,170^BY3^BCN,100,Y,N,N^FD{{ORDER_NUM}}^FS^XZ') ON CONFLICT (key) DO NOTHING;",
                    "INSERT INTO system_settings (key, value) VALUES ('zpl_item_template', '^XA^FO50,30^A0N,30,30^FD{{DESC}}^FS^FO50,70^A0N,25,25^FDSKU: {{SKU}}^FS^FO50,110^BY2^BCN,80,Y,N,N^FD{{SKU}}^FS^XZ') ON CONFLICT (key) DO NOTHING;",
                    "INSERT INTO system_settings (key, value) VALUES ('zpl_location_template', '^XA^FO30,25^A0N,28,28^FDUBICACION: {{LOCATION_CODE}}^FS^FO30,65^A0N,20,18^FD{{BRANCH}} - {{SECTOR}}^FS^FO30,105^BY3,2.0,60^BCN,70,Y,N,N^FD{{LOCATION_CODE}}^FS^XZ') ON CONFLICT (key) DO NOTHING;"
                ]

                ddl_errors = 0
                for stmt in ddl_statements:
                    try: await conn.execute(stmt)
                    except Exception as e:
                        ddl_errors += 1
                        logger.error(f"[DB DDL ERROR] {e!r} en: {stmt[:160]}")
                if ddl_errors:
                    logger.error(f"[DB] Esquema inicializado con {ddl_errors} error(es) de DDL. Revisar los mensajes anteriores.")

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