from fastapi import APIRouter, Depends, HTTPException, Response, Request
from pydantic import BaseModel
import asyncpg, json, urllib.request, asyncio, secrets, os
from datetime import datetime, timezone

try:
    from backend.database import (
        get_db_connection, check_rate_limit, record_failed_login,
        reset_failed_login, verify_password, get_password_hash, create_access_token, log_action
    )
except ImportError:
    from database import (
        get_db_connection, check_rate_limit, record_failed_login,
        reset_failed_login, verify_password, get_password_hash, create_access_token, log_action
    )

router = APIRouter(prefix="/api/auth", tags=["Auth"])

class LoginRequest(BaseModel):
    username: str
    password: str

class GoogleVerifyRequest(BaseModel):
    id_token: str

@router.get("/google/config")
async def get_google_config(conn: asyncpg.Connection = Depends(get_db_connection)):
    enabled = await conn.fetchval("SELECT value FROM system_settings WHERE key = 'enable_google_sso'") or "false"
    client_id = await conn.fetchval("SELECT value FROM system_settings WHERE key = 'google_client_id'") or ""
    return {"enabled": enabled.lower() == "true", "client_id": client_id}

def verify_google_token_sync(id_token: str) -> dict:
    url = f"https://oauth2.googleapis.com/tokeninfo?id_token={id_token}"
    req = urllib.request.Request(url, headers={'User-Agent': 'Tracker360'})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                return json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        print(f"Error verificando token de Google: {e}")
    return {}

@router.post("/login")
async def login(request: Request, response: Response, credentials: LoginRequest, conn: asyncpg.Connection = Depends(get_db_connection)):
    client_ip = request.client.host if request.client else "127.0.0.1"
    
    await check_rate_limit(client_ip, conn)
    
    user = await conn.fetchrow("SELECT id, username, password_hash, role, is_active FROM users WHERE LOWER(username) = $1 OR LOWER(email) = $1", credentials.username.strip().lower())
    
    if not user or not user["is_active"] or not verify_password(credentials.password, user["password_hash"]):
        await record_failed_login(client_ip, conn)
        username_attempt = credentials.username.strip().lower() if credentials.username else "UNKNOWN"
        await log_action(conn, username_attempt, "LOGIN_FAILED", "Intento de acceso fallido", client_ip)
        raise HTTPException(status_code=401, detail="Credenciales incorrectas o cuenta no aprobada.")
    
    await reset_failed_login(client_ip, conn)
    token = create_access_token({"sub": user["username"], "role": user["role"], "id": str(user["id"])})
    
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
    client_ip = request.client.host if request.client else "127.0.0.1"
    
    enabled = await conn.fetchval("SELECT value FROM system_settings WHERE key = 'enable_google_sso'") or "false"
    if enabled.lower() != "true":
        raise HTTPException(status_code=400, detail="El inicio de sesion con Google no esta habilitado.")

    google_data = await asyncio.to_thread(verify_google_token_sync, body.id_token)
    if not google_data or "email" not in google_data:
        raise HTTPException(status_code=401, detail="Token de Google invalido o expirado.")

    email = google_data.get("email", "").strip().lower()
    full_name = google_data.get("name", email).strip()

    allowed_domain = await conn.fetchval("SELECT value FROM system_settings WHERE key = 'google_allowed_domain'") or ""
    if allowed_domain and allowed_domain.strip():
        req_domain = allowed_domain.strip().lower()
        if not email.endswith(f"@{req_domain}"):
            await log_action(conn, email, "GOOGLE_LOGIN_BLOCKED", f"Dominio no autorizado: {email}", client_ip)
            raise HTTPException(status_code=403, detail=f"Solo se permiten correos del dominio @{req_domain}.")

    user = await conn.fetchrow("SELECT id, username, email, role, is_active FROM users WHERE LOWER(email) = $1 OR LOWER(username) = $1", email)

    if not user:
        random_pass = secrets.token_urlsafe(24)
        hashed_pass = get_password_hash(random_pass)
        
        await conn.execute("""
            INSERT INTO users (username, email, full_name, password_hash, role, is_active)
            VALUES ($1, $2, $3, $4, 'PREPARADOR', FALSE)
        """, email, email, full_name, hashed_pass)

        await log_action(conn, email, "USER_REGISTERED_GOOGLE_PENDING", f"Solicitud de acceso registrada via Google para {full_name}", client_ip)
        raise HTTPException(status_code=403, detail="Tu solicitud de acceso ha sido registrada. Un administrador debe aprobar tu cuenta antes de ingresar.")

    if not user["is_active"]:
        await log_action(conn, user["username"], "GOOGLE_LOGIN_PENDING", "Intento de ingreso con cuenta pendiente de aprobacion", client_ip)
        raise HTTPException(status_code=403, detail="Tu cuenta esta registrada pero se encuentra pendiente de aprobacion por un administrador.")

    await reset_failed_login(client_ip, conn)
    token = create_access_token({"sub": user["username"], "role": user["role"], "id": str(user["id"])})

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

@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie("access_token", secure=True, httponly=True, samesite="strict")
    return {"message": "Exito"}