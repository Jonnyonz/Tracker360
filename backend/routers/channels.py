from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from typing import List, Optional
import asyncpg, re, secrets
import logging

from backend.database import get_db_connection, require_admin, log_action, get_client_ip, hash_system_api_key, parse_uuid

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Sales Channels"])

# Canales de venta: sistemas externos (el middleware de Mercado Libre, otra tienda) que cargan pedidos
# y leen el stock por /api/v1/channel/*. Cada canal se autentica con su propia clave
# (Authorization: Bearer <clave> o X-API-Key), que se muestra una sola vez al crearla o rotarla.
MODOS_STOCK = ("DISPONIBLE", "DISPONIBLE_MENOS_COMPROMETIDO")
CLAVE_PREFIJO = "tch_"


class CanalInput(BaseModel):
    code: str
    name: str
    stock_mode: str = "DISPONIBLE"
    stock_branch_ids: Optional[List[str]] = None


class CanalUpdate(BaseModel):
    name: Optional[str] = None
    stock_mode: Optional[str] = None
    stock_branch_ids: Optional[List[str]] = None
    todas_las_sucursales: bool = False
    is_active: Optional[bool] = None


def _nueva_clave() -> str:
    return CLAVE_PREFIJO + secrets.token_urlsafe(32)


def _modo(valor: str) -> str:
    modo = (valor or "").strip().upper()
    if modo not in MODOS_STOCK:
        raise HTTPException(400, "Modo de stock inválido (DISPONIBLE o DISPONIBLE_MENOS_COMPROMETIDO).")
    return modo


async def _sucursales(conn: asyncpg.Connection, ids: Optional[List[str]]):
    if ids is None:
        return None
    uuids = [parse_uuid(x, "Sucursal inválida.") for x in ids]
    if uuids and await conn.fetchval("SELECT COUNT(*) FROM branches WHERE id = ANY($1::uuid[])", uuids) != len(set(uuids)):
        raise HTTPException(400, "Alguna sucursal no existe.")
    return uuids or None


def _canal_dict(r) -> dict:
    return {"id": str(r["id"]), "code": r["code"], "name": r["name"], "stock_mode": r["stock_mode"],
            "stock_branch_ids": [str(x) for x in (r["stock_branch_ids"] or [])] or None,
            "is_active": r["is_active"], "created_at": r["created_at"], "last_used_at": r["last_used_at"]}


# --- Autenticacion del canal ---
async def require_sales_channel(request: Request, authorization: Optional[str] = Header(None), x_api_key: Optional[str] = Header(None),
                                conn: asyncpg.Connection = Depends(get_db_connection)) -> dict:
    clave = ""
    if authorization and authorization.lower().startswith("bearer "):
        clave = authorization[7:].strip()
    elif x_api_key:
        clave = x_api_key.strip()
    if not clave.startswith(CLAVE_PREFIJO):
        raise HTTPException(401, "Clave de canal requerida.")
    canal = await conn.fetchrow("""
        UPDATE sales_channels SET last_used_at = now() WHERE api_key_hash = $1 AND is_active
        RETURNING id, code, name, stock_mode, stock_branch_ids, is_active, created_at, last_used_at
    """, hash_system_api_key(clave))
    if not canal:
        await log_action(conn, "SYSTEM", "CHANNEL_AUTH_FAILED", "Clave de canal inválida o inactiva", get_client_ip(request))
        raise HTTPException(401, "Clave de canal inválida o inactiva.")
    return dict(canal)


# --- Administracion (panel) ---
@router.get("/api/admin/sales-channels")
async def listar_canales(admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    filas = await conn.fetch("SELECT * FROM sales_channels ORDER BY created_at")
    return [_canal_dict(r) for r in filas]


@router.post("/api/admin/sales-channels")
async def crear_canal(data: CanalInput, request: Request, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    code = data.code.strip().upper()
    if not re.fullmatch(r"[A-Z0-9_-]{2,30}", code):
        raise HTTPException(400, "Código de canal inválido (2 a 30 letras, números, - o _).")
    if not data.name.strip():
        raise HTTPException(400, "El nombre del canal es obligatorio.")
    if await conn.fetchval("SELECT 1 FROM sales_channels WHERE code = $1", code):
        raise HTTPException(409, "Ya existe un canal con ese código.")
    clave = _nueva_clave()
    fila = await conn.fetchrow("""
        INSERT INTO sales_channels (code, name, api_key_hash, stock_mode, stock_branch_ids)
        VALUES ($1, $2, $3, $4, $5) RETURNING *
    """, code, data.name.strip(), hash_system_api_key(clave), _modo(data.stock_mode), await _sucursales(conn, data.stock_branch_ids))
    await log_action(conn, admin["username"], "SALES_CHANNEL_CREATED", f"Canal de venta {code} creado.", get_client_ip(request))
    return {**_canal_dict(fila), "api_key": clave, "message": "Guardá la clave ahora: no se vuelve a mostrar."}


@router.put("/api/admin/sales-channels/{channel_id}")
async def editar_canal(channel_id: str, data: CanalUpdate, request: Request, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    cid = parse_uuid(channel_id, "Canal inválido.")
    actual = await conn.fetchrow("SELECT * FROM sales_channels WHERE id = $1", cid)
    if not actual:
        raise HTTPException(404, "Canal no encontrado.")
    nombre = data.name.strip() if data.name is not None else actual["name"]
    if not nombre:
        raise HTTPException(400, "El nombre del canal es obligatorio.")
    modo = _modo(data.stock_mode) if data.stock_mode is not None else actual["stock_mode"]
    if data.todas_las_sucursales:
        sucursales = None
    elif data.stock_branch_ids is not None:
        sucursales = await _sucursales(conn, data.stock_branch_ids)
    else:
        sucursales = actual["stock_branch_ids"]
    activo = data.is_active if data.is_active is not None else actual["is_active"]
    fila = await conn.fetchrow("""
        UPDATE sales_channels SET name = $2, stock_mode = $3, stock_branch_ids = $4, is_active = $5 WHERE id = $1 RETURNING *
    """, cid, nombre, modo, sucursales, activo)
    await log_action(conn, admin["username"], "SALES_CHANNEL_UPDATED", f"Canal de venta {fila['code']} actualizado.", get_client_ip(request))
    return _canal_dict(fila)


@router.post("/api/admin/sales-channels/{channel_id}/rotate-key")
async def rotar_clave(channel_id: str, request: Request, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    cid = parse_uuid(channel_id, "Canal inválido.")
    clave = _nueva_clave()
    code = await conn.fetchval("UPDATE sales_channels SET api_key_hash = $2 WHERE id = $1 RETURNING code", cid, hash_system_api_key(clave))
    if not code:
        raise HTTPException(404, "Canal no encontrado.")
    await log_action(conn, admin["username"], "SALES_CHANNEL_KEY_ROTATED", f"Clave del canal {code} rotada.", get_client_ip(request))
    return {"api_key": clave, "message": "La clave anterior dejó de valer. Guardá la nueva: no se vuelve a mostrar."}


# --- API del canal ---
@router.get("/api/v1/channel/me")
async def canal_actual(canal: dict = Depends(require_sales_channel)):
    return _canal_dict(canal)
