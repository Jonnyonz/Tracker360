from fastapi import APIRouter, Depends, HTTPException, Response, Request
from pydantic import BaseModel
import asyncpg, json, urllib.request, urllib.parse, asyncio, secrets, os, jwt, uuid
from datetime import datetime, timezone
from typing import Optional
from jztech_core.setup_flow import verify_setup_token
import logging

logger = logging.getLogger(__name__)

try:
    from backend.database import (
        get_db_connection, check_rate_limit, record_failed_login,
        reset_failed_login, verify_password, get_password_hash, create_access_token, log_action, needs_rehash,
        get_current_user, get_client_ip, invalidate_user_sessions, SECRET_KEY, ALGORITHM
    )
except ImportError:
    from database import (
        get_db_connection, check_rate_limit, record_failed_login,
        reset_failed_login, verify_password, get_password_hash, create_access_token, log_action, needs_rehash,
        get_current_user, get_client_ip, invalidate_user_sessions, SECRET_KEY, ALGORITHM
    )

router = APIRouter(prefix="/api/auth", tags=["Auth"])

SETUP_TOKEN = os.getenv("SETUP_TOKEN", "")

class LoginRequest(BaseModel):
    username: str
    password: str

class GoogleVerifyRequest(BaseModel):
    id_token: str

class SetupAdminRequest(BaseModel):
    token: str
    username: str
    full_name: str
    password: str

@router.get("/setup/status")
async def setup_status(conn: asyncpg.Connection = Depends(get_db_connection)):
    count = await conn.fetchval("SELECT COUNT(*) FROM users")
    return {"needs_setup": (count or 0) == 0}

@router.post("/setup/admin")
async def setup_admin(data: SetupAdminRequest, request: Request, response: Response, conn: asyncpg.Connection = Depends(get_db_connection)):
    client_ip = get_client_ip(request)

    if not verify_setup_token(SETUP_TOKEN, data.token):
        raise HTTPException(status_code=403, detail="Token de instalación inválido.")

    count = await conn.fetchval("SELECT COUNT(*) FROM users")
    if (count or 0) > 0:
        raise HTTPException(status_code=403, detail="La configuración inicial ya fue completada.")

    username = data.username.strip().lower()
    full_name = data.full_name.strip()
    password = data.password
    if not username or not full_name:
        raise HTTPException(status_code=400, detail="Complete todos los campos.")
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="La contraseña debe tener al menos 8 caracteres.")

    hashed_pass = get_password_hash(password)
    # Dos altas simultaneas podian pasar el chequeo de arriba con la tabla vacia y crear dos
    # administradores. Con el lock, la segunda espera a que la primera termine y ve su usuario.
    async with conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext('tracker360_setup_admin'))")
        count = await conn.fetchval("SELECT COUNT(*) FROM users")
        if (count or 0) > 0:
            raise HTTPException(status_code=403, detail="La configuración inicial ya fue completada.")
        user = await conn.fetchrow(
            "INSERT INTO users (username, full_name, password_hash, role, is_active) VALUES ($1, $2, $3, 'ADMIN', TRUE) RETURNING id, username, role, token_version",
            username, full_name, hashed_pass
        )
        await log_action(conn, username, "SETUP_ADMIN_CREATED", "Usuario administrador inicial creado desde la pantalla de configuración", client_ip)

    token = create_access_token({"sub": user["username"], "role": user["role"], "id": str(user["id"]), "tv": user["token_version"]})
    response.set_cookie(key="access_token", value=f"Bearer {token}", httponly=True, secure=True, samesite="strict", max_age=14400)
    return {"message": "Exito", "role": user["role"]}

@router.get("/google/config")
async def get_google_config(conn: asyncpg.Connection = Depends(get_db_connection)):
    enabled = await conn.fetchval("SELECT value FROM system_settings WHERE key = 'enable_google_sso'") or "false"
    client_id = await conn.fetchval("SELECT value FROM system_settings WHERE key = 'google_client_id'") or ""
    return {"enabled": enabled.lower() == "true", "client_id": client_id}

GOOGLE_ISSUERS = {"accounts.google.com", "https://accounts.google.com"}

def verify_google_token_sync(id_token: str) -> dict:
    url = "https://oauth2.googleapis.com/tokeninfo?" + urllib.parse.urlencode({"id_token": id_token})
    req = urllib.request.Request(url, headers={'User-Agent': 'Tracker360'})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                return json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        logger.warning(f"Error verificando token de Google: {e!r}")
    return {}

def validate_google_claims(data: dict, client_id: str) -> Optional[str]:
    """Devuelve el motivo del rechazo, o None si el token es valido para esta instalacion."""
    if not data or "email" not in data:
        return "token invalido"
    if not client_id or not secrets.compare_digest(str(data.get("aud", "")), client_id):
        return "aud no coincide con google_client_id"
    if data.get("iss") not in GOOGLE_ISSUERS:
        return "emisor no es Google"
    try:
        if int(data.get("exp", 0)) <= int(datetime.now(timezone.utc).timestamp()):
            return "token expirado"
    except (TypeError, ValueError):
        return "exp invalido"
    if str(data.get("email_verified", "")).lower() != "true":
        return "email no verificado"
    return None

@router.post("/login")
async def login(request: Request, response: Response, credentials: LoginRequest, conn: asyncpg.Connection = Depends(get_db_connection)):
    client_ip = get_client_ip(request)
    
    await check_rate_limit(client_ip, conn)
    
    user = await conn.fetchrow("SELECT id, username, password_hash, role, is_active, token_version FROM users WHERE LOWER(username) = $1 OR LOWER(email) = $1", credentials.username.strip().lower())

    if not user or not user["is_active"] or not verify_password(credentials.password, user["password_hash"]):
        await record_failed_login(client_ip, conn)
        username_attempt = credentials.username.strip().lower() if credentials.username else "UNKNOWN"
        await log_action(conn, username_attempt, "LOGIN_FAILED", "Intento de acceso fallido", client_ip)
        raise HTTPException(status_code=401, detail="Credenciales incorrectas o cuenta no aprobada.")

    await reset_failed_login(client_ip, conn)
    if needs_rehash(user["password_hash"]):
        await conn.execute("UPDATE users SET password_hash = $1 WHERE id = $2", get_password_hash(credentials.password), user["id"])
    token = create_access_token({"sub": user["username"], "role": user["role"], "id": str(user["id"]), "tv": user["token_version"]})
    
    response.set_cookie(
        key="access_token", 
        value=f"Bearer {token}", 
        httponly=True, 
        secure=True, 
        samesite="strict", 
        max_age=14400 
    )
    
    await log_action(conn, user["username"], "LOGIN_SUCCESS", "Inicio de sesion", client_ip)
    return {"message": "Exito", "role": user["role"]}

@router.post("/google/verify")
async def verify_google_login(request: Request, response: Response, body: GoogleVerifyRequest, conn: asyncpg.Connection = Depends(get_db_connection)):
    client_ip = get_client_ip(request)
    
    enabled = await conn.fetchval("SELECT value FROM system_settings WHERE key = 'enable_google_sso'") or "false"
    if enabled.lower() != "true":
        raise HTTPException(status_code=400, detail="El inicio de sesion con Google no esta habilitado.")

    client_id = (await conn.fetchval("SELECT value FROM system_settings WHERE key = 'google_client_id'") or "").strip()
    google_data = await asyncio.to_thread(verify_google_token_sync, body.id_token)
    reject_reason = validate_google_claims(google_data, client_id)
    if reject_reason:
        await log_action(conn, str(google_data.get("email", "UNKNOWN"))[:50], "GOOGLE_LOGIN_REJECTED", f"Token de Google rechazado: {reject_reason}", client_ip)
        raise HTTPException(status_code=401, detail="Token de Google invalido o expirado.")

    email = google_data.get("email", "").strip().lower()
    full_name = google_data.get("name", email).strip()

    allowed_domain = await conn.fetchval("SELECT value FROM system_settings WHERE key = 'google_allowed_domain'") or ""
    if allowed_domain and allowed_domain.strip():
        req_domain = allowed_domain.strip().lower()
        if not email.endswith(f"@{req_domain}"):
            await log_action(conn, email, "GOOGLE_LOGIN_BLOCKED", f"Dominio no autorizado: {email}", client_ip)
            raise HTTPException(status_code=403, detail=f"Solo se permiten correos del dominio @{req_domain}.")

    user = await conn.fetchrow("SELECT id, username, email, role, is_active, token_version FROM users WHERE LOWER(email) = $1 OR LOWER(username) = $1", email)

    # Mismo mensaje para cuenta nueva y cuenta ya pendiente: no revela si el email ya existe
    # (evita enumeracion de cuentas por parte de alguien del dominio permitido).
    PENDING_MSG = "Tu solicitud de acceso quedó registrada. Un administrador debe aprobarla antes de que puedas ingresar."

    if not user:
        random_pass = secrets.token_urlsafe(24)
        hashed_pass = get_password_hash(random_pass)

        await conn.execute("""
            INSERT INTO users (username, email, full_name, password_hash, role, is_active)
            VALUES ($1, $2, $3, $4, 'PREPARADOR', FALSE)
            ON CONFLICT (username) DO NOTHING
        """, email, email, full_name, hashed_pass)

        await log_action(conn, email, "USER_REGISTERED_GOOGLE_PENDING", f"Solicitud de acceso registrada via Google para {full_name}", client_ip)
        raise HTTPException(status_code=403, detail=PENDING_MSG)

    if not user["is_active"]:
        await log_action(conn, user["username"], "GOOGLE_LOGIN_PENDING", "Intento de ingreso con cuenta pendiente de aprobacion", client_ip)
        raise HTTPException(status_code=403, detail=PENDING_MSG)

    await reset_failed_login(client_ip, conn)
    token = create_access_token({"sub": user["username"], "role": user["role"], "id": str(user["id"]), "tv": user["token_version"]})

    response.set_cookie(
        key="access_token", 
        value=f"Bearer {token}", 
        httponly=True, 
        secure=True, 
        samesite="strict", 
        max_age=14400 
    )

    await log_action(conn, user["username"], "GOOGLE_LOGIN_SUCCESS", "Inicio de sesion via Google SSO", client_ip)
    return {"message": "Exito", "role": user["role"]}

@router.get("/me")
async def me(user: dict = Depends(get_current_user)):
    return {"username": user["username"], "role": user["role"]}

@router.post("/logout")
async def logout(request: Request, response: Response, conn: asyncpg.Connection = Depends(get_db_connection)):
    # Ademas de borrar la cookie, invalida el token del lado servidor (por si fue copiado).
    token = request.cookies.get("access_token")
    if token and token.startswith("Bearer "):
        try:
            payload = jwt.decode(token.split(" ")[1], SECRET_KEY, algorithms=[ALGORITHM])
            user_id = payload.get("id")
            if user_id:
                await invalidate_user_sessions(conn, uuid.UUID(user_id))
        except (jwt.PyJWTError, ValueError):
            pass
    response.delete_cookie("access_token", secure=True, httponly=True, samesite="strict")
    return {"message": "Exito"}