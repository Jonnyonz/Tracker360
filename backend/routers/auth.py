from fastapi import APIRouter, Depends, HTTPException, Response, Request
from pydantic import BaseModel
import asyncpg

try:
    from backend.database import (
        get_db_connection, check_rate_limit, record_failed_login,
        reset_failed_login, verify_password, create_access_token, log_action
    )
except ImportError:
    from database import (
        get_db_connection, check_rate_limit, record_failed_login,
        reset_failed_login, verify_password, create_access_token, log_action
    )

router = APIRouter(prefix="/api/auth", tags=["Auth"])

class LoginRequest(BaseModel):
    username: str
    password: str

@router.post("/login")
async def login(request: Request, response: Response, credentials: LoginRequest, conn: asyncpg.Connection = Depends(get_db_connection)):
    client_ip = request.client.host if request.client else "127.0.0.1"
    
    # 1. Comprobar Bloqueos en Base de Datos
    await check_rate_limit(client_ip, conn)
    
    user = await conn.fetchrow("SELECT id, username, password_hash, role, is_active FROM users WHERE username = $1", credentials.username.strip().lower())
    
    if not user or not user["is_active"] or not verify_password(credentials.password, user["password_hash"]):
        # 2. Registrar fallo en Base de Datos
        await record_failed_login(client_ip, conn)
        username_attempt = credentials.username.strip().lower() if credentials.username else "UNKNOWN"
        await log_action(conn, username_attempt, "LOGIN_FAILED", "Intento de acceso fallido", client_ip)
        raise HTTPException(status_code=401, detail="Credenciales incorrectas.")
    
    # 3. Limpiar contador tras éxito
    await reset_failed_login(client_ip, conn)
    token = create_access_token({"sub": user["username"], "role": user["role"], "id": str(user["id"])})
    
    # === COOKIE DE MÁXIMA SEGURIDAD (Secure, HttpOnly, Strict) ===
    response.set_cookie(
        key="access_token", 
        value=f"Bearer {token}", 
        httponly=True, 
        secure=True, 
        samesite="strict", 
        max_age=14400 
    )
    
    await log_action(conn, user["username"], "LOGIN_SUCCESS", "Inicio de sesión", client_ip)
    return {"message": "Éxito", "role": user["role"]}

@router.post("/logout")
async def logout(response: Response):
    # Destrucción segura de la cookie
    response.delete_cookie("access_token", secure=True, httponly=True, samesite="strict")
    return {"message": "Éxito"}