from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
import asyncpg

try:
    from backend.database import get_db_connection, require_admin, get_password_hash, log_action
except ImportError:
    from database import get_db_connection, require_admin, get_password_hash, log_action

router = APIRouter(prefix="/api/admin/users", tags=["Users"])

class UserCreate(BaseModel):
    username: str
    full_name: str
    password: str
    role: str = "PREPARADOR"
    email: Optional[str] = None
    branch_id: Optional[str] = None
    sector_id: Optional[str] = None

class UserUpdate(BaseModel):
    full_name: Optional[str] = None
    role: Optional[str] = None
    email: Optional[str] = None
    branch_id: Optional[str] = None
    sector_id: Optional[str] = None
    is_active: Optional[bool] = None
    password: Optional[str] = None

@router.get("")
async def list_users(admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    return [dict(u) for u in await conn.fetch("SELECT u.id, u.username, u.full_name, u.role, u.is_active, u.email, u.branch_id, u.sector_id, b.name as branch_name, s.name as sector_name FROM users u LEFT JOIN branches b ON u.branch_id = b.id::text LEFT JOIN sectors s ON u.sector_id = s.id::text ORDER BY u.created_at DESC")]

@router.post("")
async def create_or_update_user(data: UserCreate, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    await conn.execute("INSERT INTO users (username, full_name, password_hash, role, email, branch_id, sector_id) VALUES ($1, $2, $3, $4, $5, $6, $7) ON CONFLICT (username) DO UPDATE SET full_name = EXCLUDED.full_name, password_hash = EXCLUDED.password_hash, role = EXCLUDED.role, email = EXCLUDED.email, branch_id = EXCLUDED.branch_id, sector_id = EXCLUDED.sector_id, is_active = TRUE", data.username.strip().lower(), data.full_name.strip(), get_password_hash(data.password), data.role, data.email, data.branch_id, data.sector_id)
    return {"status": "success"}

@router.put("/{username}")
async def update_user(username: str, data: UserUpdate, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    clean_user = username.strip().lower()
    user = await conn.fetchrow("SELECT id FROM users WHERE username = $1", clean_user)
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")

    updates = []
    params = []
    idx = 1

    if data.full_name is not None:
        updates.append(f"full_name = ${idx}")
        params.append(data.full_name.strip())
        idx += 1
    if data.role is not None:
        updates.append(f"role = ${idx}")
        params.append(data.role)
        idx += 1
    if data.email is not None:
        updates.append(f"email = ${idx}")
        params.append(data.email.strip() if data.email else None)
        idx += 1
    if data.branch_id is not None:
        updates.append(f"branch_id = ${idx}")
        params.append(data.branch_id if data.branch_id else None)
        idx += 1
    if data.sector_id is not None:
        updates.append(f"sector_id = ${idx}")
        params.append(data.sector_id if data.sector_id else None)
        idx += 1
    if data.is_active is not None:
        updates.append(f"is_active = ${idx}")
        params.append(data.is_active)
        idx += 1
    if data.password and data.password.strip():
        updates.append(f"password_hash = ${idx}")
        params.append(get_password_hash(data.password.strip()))
        idx += 1

    if updates:
        query = f"UPDATE users SET {', '.join(updates)} WHERE username = ${idx}"
        params.append(clean_user)
        await conn.execute(query, *params)
        await log_action(conn, admin.get("username", "admin"), "USER_UPDATE", f"Actualizó usuario {clean_user}")

    return {"status": "success", "message": "Usuario actualizado correctamente."}