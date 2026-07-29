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
    check_rate_limit(client_ip)
    
    user = await conn.fetchrow("SELECT id, username, password_hash, role, is_active FROM users WHERE username = $1", credentials.username.strip().lower())
    if not user or not user["is_active"] or not verify_password(credentials.password, user["password_hash"]):
        record_failed_login(client_ip)
        await log_action(conn, credentials.username.strip().lower(), "LOGIN_FAILED", "Intento fallido", client_ip)
        raise HTTPException(status_code=401, detail="Credenciales incorrectas.")
    
    reset_failed_login(client_ip)
    token = create_access_token({"sub": user["username"], "role": user["role"], "id": str(user["id"])})
    response.set_cookie(key="access_token", value=f"Bearer {token}", httponly=True, secure=False, samesite="lax", max_age=28800)
    await log_action(conn, user["username"], "LOGIN_SUCCESS", "Inicio de sesión", client_ip)
    return {"message": "Éxito", "role": user["role"]}

@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie("access_token")
    return {"message": "Éxito"}
