from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel
from datetime import datetime
from typing import List, Optional
import asyncpg, json, re, secrets
import logging

from backend.database import (get_db_connection, require_admin, log_action, get_client_ip, hash_system_api_key, parse_uuid,
                              numero_correlativo, require_valid_quantity, emitir_stock_a_canales)
from backend.routers.outbound import cancelar_pedido, imprimir_etiqueta_pedido

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
    urgent: bool = False                # el canal pide prepararlo primero (en ML: Flex, se entrega en el dia)
    lines: List[PedidoLinea]


# Envios que no pasan por el deposito de Tracker (Full: el stock esta en el del marketplace). El pedido se
# registra con estado FULL, solo informativo: no descuenta ni compromete stock, no entra al picking, al
# empaque ni a devoluciones. Se puede cancelar.
ENVIOS_FUERA_DEL_DEPOSITO = {"FULFILLMENT"}


class EtiquetaCanal(BaseModel):
    zpl: str


ETIQUETA_MAX_BYTES = 256 * 1024


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
    # orders_7d: pedidos que cargo el canal en los ultimos 7 dias (el panel muestra primero los mas activos).
    filas = await conn.fetch("""
        SELECT c.*, (SELECT COUNT(*) FROM documents d WHERE d.sales_channel_id = c.id
                     AND d.created_at > now() - interval '7 days') AS orders_7d
        FROM sales_channels c ORDER BY c.created_at
    """)
    return [{**_canal_dict(r), "orders_7d": r["orders_7d"]} for r in filas]


@router.get("/api/admin/sales-channels/{channel_id}/events")
async def ver_eventos_del_canal(channel_id: str, limit: int = 30, admin: dict = Depends(require_admin),
                                conn: asyncpg.Connection = Depends(get_db_connection)):
    """Para el panel: los ultimos pedidos que cargo el canal y los ultimos eventos que Tracker le dejo
    (stock.changed y order.status), del mas nuevo al mas viejo."""
    cid = parse_uuid(channel_id, "Canal inválido.")
    canal = await conn.fetchrow("SELECT * FROM sales_channels WHERE id = $1", cid)
    if not canal:
        raise HTTPException(404, "Canal no encontrado.")
    limite = max(1, min(int(limit), 200))
    pedidos = await conn.fetch("""
        SELECT document_number, external_ref, external_account, status, shipping_type, priority, created_at
        FROM documents WHERE sales_channel_id = $1 ORDER BY created_at DESC LIMIT $2
    """, cid, limite)
    eventos = await conn.fetch("""
        SELECT id, event_type, payload::text AS payload, created_at FROM channel_events
        WHERE sales_channel_id = $1 ORDER BY id DESC LIMIT $2
    """, cid, limite)
    return {"channel": _canal_dict(canal), "orders": [dict(p) for p in pedidos],
            "events": [{"id": e["id"], "type": e["event_type"], "payload": json.loads(e["payload"]),
                        "created_at": e["created_at"]} for e in eventos]}


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
        SELECT id, document_number, status, external_ref, external_account, shipping_type, shipment_ref, priority, created_at
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
            "urgent": doc["priority"] > 0, "created_at": doc["created_at"],
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
    externo = tipo_envio in ENVIOS_FUERA_DEL_DEPOSITO
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
        existente = await conn.fetchrow("SELECT id, document_number, status, external_ref, external_account, shipping_type, shipment_ref, priority, created_at FROM documents WHERE sales_channel_id = $1 AND external_ref = $2", canal["id"], ref)
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
                                   shipping_type, shipment_ref, buyer_name, buyer_address, priority)
            VALUES ($1, $2, $11, $3, $4, $5, $6, $7, $8, $9, $10, $12) RETURNING id
        """, numero, cliente, canal["code"], canal["id"], ref, (data.account or "").strip() or None, tipo_envio,
             (envio.shipment_ref or "").strip() or None, (comprador.name or "").strip()[:200] or None, (comprador.address or "").strip() or None,
             "FULL" if externo else "PENDING", 1 if data.urgent else 0)
        for sku, cant in cantidades.items():
            await conn.execute("INSERT INTO document_lines (document_id, sku, quantity_requested, quantity_picked, serial_numbers) VALUES ($1, $2, $3, 0, '[]'::jsonb)",
                               doc_id, sku, cant)
        if not externo:   # un pedido Full no compromete stock de Tracker
            await emitir_stock_a_canales(conn, cantidades.keys())
        await log_action(conn, f"canal:{canal['code']}", "ORDER_CREATED", f"Pedido {numero} creado desde el canal {canal['code']} (ref {ref})"
                         + (" (Full: sale del depósito del marketplace)." if externo else "."), get_client_ip(request))
        doc = await conn.fetchrow("SELECT id, document_number, status, external_ref, external_account, shipping_type, shipment_ref, priority, created_at FROM documents WHERE id = $1", doc_id)
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


@router.get("/api/v1/channel/events")
async def eventos_del_canal(after: int = 0, limit: int = 200, canal: dict = Depends(require_sales_channel),
                            conn: asyncpg.Connection = Depends(get_db_connection)):
    """Eventos del canal con id mayor a 'after', en orden. El canal guarda el ultimo id que proceso y
    pide desde ahi (entrega al menos una vez: puede ver un evento repetido si no guardo el cursor).
    stock.changed {sku}: consultar el disponible con /api/v1/channel/stock. order.status
    {external_ref, document_number, status, account, shipment_ref}. Se guardan 14 dias."""
    limite = max(1, min(int(limit), 500))
    filas = await conn.fetch("""
        SELECT id, event_type, payload::text AS payload, created_at FROM channel_events
        WHERE sales_channel_id = $1 AND id > $2 ORDER BY id LIMIT $3
    """, canal["id"], max(0, int(after)), limite)
    eventos = [{"id": f["id"], "type": f["event_type"], "payload": json.loads(f["payload"]), "created_at": f["created_at"]} for f in filas]
    return {"events": eventos, "next_after": eventos[-1]["id"] if eventos else max(0, int(after))}


@router.put("/api/v1/channel/orders/{external_ref}/label")
async def etiqueta_del_canal(external_ref: str, data: EtiquetaCanal, request: Request, canal: dict = Depends(require_sales_channel),
                             conn: asyncpg.Connection = Depends(get_db_connection)):
    """Etiqueta ZPL del canal para el pedido (por ejemplo la de envio de Mercado Libre). Se imprime al
    empacar en lugar de la de Tracker. Si el pedido ya se despacho (la etiqueta llego tarde), se imprime
    en el momento. Reemplaza a la anterior si la habia."""
    zpl = (data.zpl or "").strip()
    if not zpl or "^XA" not in zpl or "^XZ" not in zpl:
        raise HTTPException(400, "La etiqueta tiene que ser ZPL (con ^XA y ^XZ).")
    if len(zpl.encode("utf-8")) > ETIQUETA_MAX_BYTES:
        raise HTTPException(400, "La etiqueta es demasiado grande (máximo 256 KB).")
    async with conn.transaction():
        doc = await _pedido_del_canal(conn, canal, _ref(external_ref))
        await conn.execute("UPDATE documents SET channel_label_zpl = $2 WHERE id = $1", doc["id"], zpl)
        impresa = False
        if doc["status"] == "DISPATCHED":
            fila = await conn.fetchrow("SELECT d.document_number, d.channel_label_zpl, '' AS client_name, '' AS delivery_address FROM documents d WHERE d.id = $1", doc["id"])
            impresa = await imprimir_etiqueta_pedido(conn, fila)
        await log_action(conn, f"canal:{canal['code']}", "CHANNEL_LABEL", f"Etiqueta del canal para el pedido {doc['document_number']}" + (" (impresa: ya estaba despachado)." if impresa else "."), get_client_ip(request))
    return {"document_number": doc["document_number"], "printed_now": impresa}


# --- Publicaciones del canal (modulo Mercado Libre del panel) ---
class PublicacionCanal(BaseModel):
    listing_id: str                       # id de la publicacion en el canal (en ML: MLA123...)
    variation_id: Optional[str] = None    # variante, si tiene
    account: Optional[str] = None
    title: Optional[str] = None
    sku: Optional[str] = None
    status: Optional[str] = None          # estado en el canal (active, paused...)
    quantity: Optional[int] = None        # stock que tiene la publicacion en el canal
    problem: Optional[str] = None         # por que no se sincroniza (SIN_SKU, SKU_NO_EN_TRACKER, FULL, ERROR)
    detail: Optional[str] = None
    stock_sent_at: Optional[datetime] = None   # ultima vez que el canal le mando stock


class PublicacionesCanal(BaseModel):
    listings: List[PublicacionCanal]


PUBLICACIONES_MAX = 50000


def _corto(valor, largo):
    valor = (valor or "").strip() if isinstance(valor, str) else valor
    return valor[:largo] if isinstance(valor, str) and valor else None


@router.put("/api/v1/channel/listings")
async def publicaciones_del_canal(data: PublicacionesCanal, canal: dict = Depends(require_sales_channel),
                                  conn: asyncpg.Connection = Depends(get_db_connection)):
    """Lista COMPLETA de publicaciones del canal: reemplaza la anterior. Solo informativa (el panel la
    muestra junto con el stock de Tracker); no cambia stock ni pedidos."""
    if len(data.listings) > PUBLICACIONES_MAX:
        raise HTTPException(400, f"Hasta {PUBLICACIONES_MAX} publicaciones por envío.")
    filas = {}
    for p in data.listings:
        lid = _corto(p.listing_id, 40)
        if not lid:
            raise HTTPException(400, "Hay una publicación sin listing_id.")
        vid = _corto(p.variation_id, 40) or ""
        filas[(lid, vid)] = (canal["id"], lid, vid, _corto(p.account, 100), _corto(p.title, 300), _corto(p.sku, 100),
                             _corto(p.status, 40), p.quantity if p.quantity is None or p.quantity >= 0 else 0,
                             _corto((p.problem or "").upper(), 30), _corto(p.detail, 500), p.stock_sent_at)
    async with conn.transaction():
        await conn.execute("DELETE FROM channel_listings WHERE sales_channel_id = $1", canal["id"])
        if filas:
            await conn.copy_records_to_table("channel_listings", records=list(filas.values()), columns=[
                "sales_channel_id", "listing_id", "variation_id", "account", "title", "sku", "status", "quantity",
                "problem", "detail", "stock_sent_at"])
        await conn.execute("UPDATE sales_channels SET listings_synced_at = now() WHERE id = $1", canal["id"])
    return {"count": len(filas)}


@router.get("/api/admin/sales-channels/{channel_id}/listings")
async def ver_publicaciones_del_canal(channel_id: str, problem: Optional[str] = None, q: str = "", sku: str = "", listing: str = "",
                                      title: str = "", account: str = "", status: str = "", limit: int = 200, offset: int = 0,
                                      admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    """Publicaciones que informo el canal, con el disponible que Tracker le informa por SKU. problem: OK
    (las que se sincronizan), SIN_SKU, SKU_NO_EN_TRACKER, FULL o ERROR. q busca en SKU, titulo y publicacion;
    sku, listing (MLA), title, account y status filtran cada dato por separado (coincidencia parcial)."""
    cid = parse_uuid(channel_id, "Canal inválido.")
    canal = await conn.fetchrow("SELECT id, code, name, stock_mode, stock_branch_ids, listings_synced_at FROM sales_channels WHERE id = $1", cid)
    if not canal:
        raise HTTPException(404, "Canal no encontrado.")
    resumen = {r["p"]: r["n"] for r in await conn.fetch(
        "SELECT COALESCE(problem, 'OK') AS p, COUNT(*) AS n FROM channel_listings WHERE sales_channel_id = $1 GROUP BY 1", cid)}
    filtros, args = ["sales_channel_id = $1"], [cid]
    if problem:
        if problem.upper() == "OK":
            filtros.append("problem IS NULL")
        else:
            args.append(problem.strip().upper())
            filtros.append(f"problem = ${len(args)}")
    if q.strip():
        args.append(f"%{q.strip()}%")
        filtros.append(f"(sku ILIKE ${len(args)} OR title ILIKE ${len(args)} OR listing_id ILIKE ${len(args)})")
    for columna, valor in (("sku", sku), ("listing_id", listing), ("title", title), ("account", account), ("status", status)):
        if valor.strip():
            args.append(f"%{valor.strip()}%")
            filtros.append(f"COALESCE({columna}, '') ILIKE ${len(args)}")
    where = " AND ".join(filtros)
    total = await conn.fetchval(f"SELECT COUNT(*) FROM channel_listings WHERE {where}", *args)
    args += [max(1, min(int(limit), 1000)), max(0, int(offset))]
    filas = await conn.fetch(f"""
        SELECT listing_id, variation_id, account, title, sku, status, quantity, problem, detail, stock_sent_at
        FROM channel_listings WHERE {where}
        ORDER BY problem NULLS FIRST, UPPER(sku), listing_id, variation_id
        LIMIT ${len(args) - 1} OFFSET ${len(args)}
    """, *args)
    skus = sorted({f["sku"] for f in filas if f["sku"]})
    disponible = {s["sku"]: s["available"] for s in await stock_para_canal(conn, dict(canal), skus)} if skus else {}
    return {
        "channel": {"id": str(canal["id"]), "code": canal["code"], "name": canal["name"], "stock_mode": canal["stock_mode"],
                    "listings_synced_at": canal["listings_synced_at"]},
        "summary": {"total": sum(resumen.values()), "ok": resumen.get("OK", 0), "sin_sku": resumen.get("SIN_SKU", 0),
                    "sku_no_en_tracker": resumen.get("SKU_NO_EN_TRACKER", 0), "full": resumen.get("FULL", 0),
                    "error": resumen.get("ERROR", 0)},
        "total": total,
        "items": [{**dict(f), "tracker_available": disponible.get((f["sku"] or "").upper())} for f in filas],
    }

