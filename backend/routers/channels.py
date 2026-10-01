from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel
from typing import List, Optional
import asyncpg, re, secrets
import logging

from backend.database import (get_db_connection, require_admin, log_action, get_client_ip, hash_system_api_key, parse_uuid,
                              numero_correlativo, require_valid_quantity)
from backend.routers.outbound import cancelar_pedido

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


class PedidoLinea(BaseModel):
    sku: str
    quantity: float


class PedidoComprador(BaseModel):
    name: Optional[str] = None
    tax_id: Optional[str] = None
    address: Optional[str] = None


class PedidoEnvio(BaseModel):
    type: Optional[str] = None          # tipo de logistica del canal (en ML: cross_docking, self_service, drop_off, fulfillment...)
    shipment_ref: Optional[str] = None  # id del envio en el canal


class PedidoCanal(BaseModel):
    external_ref: str                   # numero de la venta en el canal
    account: Optional[str] = None       # cuenta del canal (un cliente puede tener varias)
    buyer: Optional[PedidoComprador] = None
    shipping: Optional[PedidoEnvio] = None
    lines: List[PedidoLinea]


# Envios que no pasan por el deposito de Tracker (el stock esta en el del marketplace): se resuelven
# aparte (paso 5 de la integracion).
ENVIOS_FUERA_DEL_DEPOSITO = {"FULFILLMENT"}


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


def _ref(valor: str) -> str:
    ref = (valor or "").strip()
    if not ref or len(ref) > 100:
        raise HTTPException(400, "Referencia externa inválida (obligatoria, hasta 100 caracteres).")
    return ref


async def _pedido_del_canal(conn: asyncpg.Connection, canal: dict, ref: str):
    doc = await conn.fetchrow("""
        SELECT id, document_number, status, external_ref, external_account, shipping_type, shipment_ref, created_at
        FROM documents WHERE sales_channel_id = $1 AND external_ref = $2
    """, canal["id"], ref)
    if not doc:
        raise HTTPException(404, "Pedido no encontrado en este canal.")
    return doc


async def _pedido_dict(conn: asyncpg.Connection, doc) -> dict:
    lineas = await conn.fetch("""SELECT UPPER(sku) AS sku, quantity_requested::float AS pedido, quantity_picked::float AS pickeado
                                 FROM document_lines WHERE document_id = $1 ORDER BY sku""", doc["id"])
    return {"document_number": doc["document_number"], "external_ref": doc["external_ref"], "account": doc["external_account"],
            "status": doc["status"], "shipping_type": doc["shipping_type"], "shipment_ref": doc["shipment_ref"],
            "created_at": doc["created_at"],
            "lines": [{"sku": l["sku"], "quantity": l["pedido"], "picked": l["pickeado"]} for l in lineas]}


@router.post("/api/v1/channel/orders")
async def crear_pedido_del_canal(data: PedidoCanal, request: Request, canal: dict = Depends(require_sales_channel),
                                 conn: asyncpg.Connection = Depends(get_db_connection)):
    """Alta de un pedido desde el canal. Idempotente por (canal, external_ref): si ya existe, devuelve el
    existente con created=false. Los SKU tienen que existir en Tracker tal cual (son los mismos que en el
    canal). El numero de Tracker es el correlativo."""
    ref = _ref(data.external_ref)
    envio = data.shipping or PedidoEnvio()
    tipo_envio = (envio.type or "").strip().upper() or None
    if tipo_envio in ENVIOS_FUERA_DEL_DEPOSITO:
        raise HTTPException(422, "Los envíos Full salen del depósito del marketplace: todavía no se cargan en Tracker.")
    if not data.lines:
        raise HTTPException(400, "El pedido no tiene artículos.")
    cantidades = {}
    for l in data.lines:
        require_valid_quantity(l.quantity)
        sku = l.sku.strip().upper()
        if not sku:
            raise HTTPException(400, "Hay un artículo sin SKU.")
        cantidades[sku] = cantidades.get(sku, 0) + l.quantity

    async with conn.transaction():
        # Dos altas simultaneas del mismo pedido (reintento del canal) se ordenan por este lock.
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"tracker360_pedido_canal_{canal['id']}_{ref}")
        existente = await conn.fetchrow("SELECT id, document_number, status, external_ref, external_account, shipping_type, shipment_ref, created_at FROM documents WHERE sales_channel_id = $1 AND external_ref = $2", canal["id"], ref)
        if existente:
            return {**await _pedido_dict(conn, existente), "created": False}
        conocidos = {r["sku"] for r in await conn.fetch("SELECT UPPER(sku) AS sku FROM items WHERE UPPER(sku) = ANY($1::text[])", list(cantidades))}
        faltan = sorted(set(cantidades) - conocidos)
        if faltan:
            raise HTTPException(422, "SKU inexistente en Tracker: " + ", ".join(faltan) + ". Tienen que ser los mismos que en el canal.")
        comprador = data.buyer or PedidoComprador()
        cliente = None
        if comprador.tax_id and comprador.tax_id.strip():
            cliente = await conn.fetchval("SELECT id FROM entities WHERE tax_id = $1", comprador.tax_id.strip())
        numero, _ = await numero_correlativo(conn, "PEDIDO")
        doc_id = await conn.fetchval("""
            INSERT INTO documents (document_number, customer_id, status, channel_origin, sales_channel_id, external_ref, external_account,
                                   shipping_type, shipment_ref, buyer_name, buyer_address)
            VALUES ($1, $2, 'PENDING', $3, $4, $5, $6, $7, $8, $9, $10) RETURNING id
        """, numero, cliente, canal["code"], canal["id"], ref, (data.account or "").strip() or None, tipo_envio,
             (envio.shipment_ref or "").strip() or None, (comprador.name or "").strip()[:200] or None, (comprador.address or "").strip() or None)
        for sku, cant in cantidades.items():
            await conn.execute("INSERT INTO document_lines (document_id, sku, quantity_requested, quantity_picked, serial_numbers) VALUES ($1, $2, $3, 0, '[]'::jsonb)",
                               doc_id, sku, cant)
        await log_action(conn, f"canal:{canal['code']}", "ORDER_CREATED", f"Pedido {numero} creado desde el canal {canal['code']} (ref {ref}).", get_client_ip(request))
        doc = await conn.fetchrow("SELECT id, document_number, status, external_ref, external_account, shipping_type, shipment_ref, created_at FROM documents WHERE id = $1", doc_id)
        return {**await _pedido_dict(conn, doc), "created": True}


@router.get("/api/v1/channel/orders/{external_ref}")
async def ver_pedido_del_canal(external_ref: str, canal: dict = Depends(require_sales_channel), conn: asyncpg.Connection = Depends(get_db_connection)):
    return await _pedido_dict(conn, await _pedido_del_canal(conn, canal, _ref(external_ref)))


@router.post("/api/v1/channel/orders/{external_ref}/cancel")
async def cancelar_pedido_del_canal(external_ref: str, request: Request, canal: dict = Depends(require_sales_channel),
                                    conn: asyncpg.Connection = Depends(get_db_connection)):
    """Cancelacion total desde el canal (la venta se cancelo en el marketplace). Lo pickeado vuelve al
    stock. Un pedido ya despachado no se puede cancelar (400)."""
    doc = await _pedido_del_canal(conn, canal, _ref(external_ref))
    if doc["status"] == "CANCELLED":
        return {**await _pedido_dict(conn, doc), "devuelto": []}
    r = await cancelar_pedido(conn, doc["document_number"], None, f"canal:{canal['code']}", get_client_ip(request))
    doc = await _pedido_del_canal(conn, canal, doc["external_ref"])
    return {**await _pedido_dict(conn, doc), "devuelto": r["devuelto"]}


async def stock_para_canal(conn: asyncpg.Connection, canal: dict, skus: Optional[List[str]] = None,
                           despues: str = "", limite: int = 500) -> list:
    """Stock por SKU tal como lo ve el canal. fisico = stock OPERATIVO de las sucursales del canal
    (todas si no eligio); comprometido = lo pedido y todavia no pickeado en pedidos abiertos (PENDING o
    IN_PROGRESS; lo pickeado ya salio del stock). disponible = fisico, o fisico - comprometido si el
    canal trabaja con stock comprometido; nunca negativo. Con skus devuelve esos (los que existen);
    sin skus, todos los articulos en orden de SKU desde 'despues'."""
    if skus is not None:
        filtro, args = "UPPER(i.sku) = ANY($1::text[])", [[s.strip().upper() for s in skus if s.strip()]]
    else:
        filtro, args = "UPPER(i.sku) > $1", [despues.strip().upper()]
    args += [canal["stock_branch_ids"], limite]
    filas = await conn.fetch(f"""
        SELECT UPPER(i.sku) AS sku,
               COALESCE((SELECT SUM(si.quantity) FROM stock_inventory si
                         WHERE UPPER(si.sku) = UPPER(i.sku) AND COALESCE(si.condition, 'OPERATIVO') = 'OPERATIVO'
                           AND ($2::uuid[] IS NULL OR si.branch_id = ANY($2::uuid[]))), 0)::float AS fisico,
               COALESCE((SELECT SUM(dl.quantity_requested - dl.quantity_picked) FROM document_lines dl
                         JOIN documents d ON d.id = dl.document_id
                         WHERE UPPER(dl.sku) = UPPER(i.sku) AND d.status IN ('PENDING', 'IN_PROGRESS')
                           AND dl.quantity_requested > dl.quantity_picked), 0)::float AS comprometido
        FROM items i
        WHERE {filtro}
        ORDER BY UPPER(i.sku)
        LIMIT $3
    """, *args)
    resta = canal["stock_mode"] == "DISPONIBLE_MENOS_COMPROMETIDO"
    return [{"sku": f["sku"], "physical": f["fisico"], "committed": f["comprometido"],
             "available": max(0.0, f["fisico"] - (f["comprometido"] if resta else 0.0))} for f in filas]


@router.get("/api/v1/channel/stock")
async def stock_del_canal(skus: Optional[str] = Query(None, description="SKU separados por coma"),
                          after: str = "", limit: int = 500,
                          canal: dict = Depends(require_sales_channel), conn: asyncpg.Connection = Depends(get_db_connection)):
    """Stock disponible para el canal segun su configuracion. ?skus=A,B para algunos, o paginado por SKU
    (?after=<ultimo sku>&limit=500). Un SKU que no existe en Tracker no aparece."""
    limite = max(1, min(int(limit), 1000))
    lista = [s for s in skus.split(",")] if skus is not None else None
    if lista is not None and len(lista) > 1000:
        raise HTTPException(400, "Hasta 1000 SKU por consulta.")
    filas = await stock_para_canal(conn, canal, lista, after, limite)
    return {"stock_mode": canal["stock_mode"], "items": filas,
            "next_after": filas[-1]["sku"] if lista is None and len(filas) == limite else None}
