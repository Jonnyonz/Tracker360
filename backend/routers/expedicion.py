"""Expedicion desde el Escaner Flex (1.4.0). Con el ajuste "Despacho desde Escaner Flex", empacar imprime la
etiqueta y deja el pedido esperando la carga; el Escaner consulta cada paquete por su numero de envio (el
shipment_ref del canal, el mismo de la etiqueta de Mercado Libre) y, al cerrar la ola, informa la carga: recien
ahi el pedido pasa a DISPATCHED y se avisa al canal. Clave propia (tex_...), distinta de las de los canales."""
from datetime import datetime, timezone
from typing import Optional
import re
import secrets

import asyncpg
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from backend.database import (get_db_connection, require_admin, log_action, get_client_ip, hash_system_api_key,
                              emitir_estado_a_canal, dispatch_event_to_channels)

router = APIRouter(tags=["Expedicion"])

CLAVE_PREFIJO = "tex_"
AJUSTE = "dispatch_from_scanner"          # 'true': empacar no despacha, despacha el Escaner
CLAVE = "expedicion_api_key"              # hash de la clave (nunca en claro)
ULTIMO_USO = "expedicion_last_used_at"
_RE_ENVIO = re.compile(r"[A-Za-z0-9-]{1,64}")

# Etapa del pedido vista desde la carga. Solo EMPACADO se puede despachar; DESPACHADO responde ok (reintento).
MOTIVOS = {
    "EN_PREPARACION": "Todavía no está pickeado en Tracker.",
    "PARA_EMPACAR": "Todavía no se empacó en Tracker.",
    "CANCELADO": "El pedido está cancelado en Tracker.",
    "FULL": "Es un envío Full: sale del depósito del marketplace.",
}


class ActivacionExpedicion(BaseModel):
    enabled: bool


class DespachoInput(BaseModel):
    wave: Optional[str] = Field(default=None, max_length=40)       # ola del Escaner
    plate: Optional[str] = Field(default=None, max_length=20)      # patente
    carrier: Optional[str] = Field(default=None, max_length=80)    # transportista
    user: Optional[str] = Field(default=None, max_length=80)       # quien cargo


async def _ajustes(conn: asyncpg.Connection) -> dict:
    filas = await conn.fetch("SELECT key, value FROM system_settings WHERE key = ANY($1::text[])", [AJUSTE, CLAVE, ULTIMO_USO])
    return {f["key"]: f["value"] for f in filas}


async def _guardar(conn: asyncpg.Connection, clave: str, valor: str) -> None:
    await conn.execute("""INSERT INTO system_settings (key, value) VALUES ($1, $2)
                          ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value""", clave, valor)


async def require_expedicion(request: Request, authorization: Optional[str] = Header(None), x_api_key: Optional[str] = Header(None),
                             conn: asyncpg.Connection = Depends(get_db_connection)) -> None:
    clave = ""
    if authorization and authorization.lower().startswith("bearer "):
        clave = authorization[7:].strip()
    elif x_api_key:
        clave = x_api_key.strip()
    if not clave.startswith(CLAVE_PREFIJO):
        raise HTTPException(401, "Clave de expedición requerida.")
    aj = await _ajustes(conn)
    guardada = aj.get(CLAVE) or ""
    if not guardada or not secrets.compare_digest(hash_system_api_key(clave), guardada):
        await log_action(conn, "SYSTEM", "SCANNER_AUTH_FAILED", "Clave de expedición inválida", get_client_ip(request))
        raise HTTPException(401, "Clave de expedición inválida.")
    if aj.get(AJUSTE) != "true":
        raise HTTPException(409, "El despacho desde el Escáner Flex está desactivado en Tracker (Configuración).")
    await _guardar(conn, ULTIMO_USO, datetime.now(timezone.utc).isoformat())


# --- Configuracion (panel) ---
@router.get("/api/admin/expedicion")
async def ver_expedicion(admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    aj = await _ajustes(conn)
    return {"enabled": aj.get(AJUSTE) == "true", "has_key": bool(aj.get(CLAVE)), "last_used_at": aj.get(ULTIMO_USO) or None}


@router.put("/api/admin/expedicion")
async def activar_expedicion(data: ActivacionExpedicion, request: Request, admin: dict = Depends(require_admin),
                             conn: asyncpg.Connection = Depends(get_db_connection)):
    await _guardar(conn, AJUSTE, "true" if data.enabled else "false")
    await log_action(conn, admin["username"], "SCANNER_DISPATCH_ENABLED" if data.enabled else "SCANNER_DISPATCH_DISABLED",
                     "Despacho desde el Escáner Flex " + ("activado" if data.enabled else "desactivado") + ".", get_client_ip(request))
    return await ver_expedicion(admin, conn)


@router.post("/api/admin/expedicion/rotate-key")
async def rotar_clave_expedicion(request: Request, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    clave = CLAVE_PREFIJO + secrets.token_urlsafe(32)
    await _guardar(conn, CLAVE, hash_system_api_key(clave))
    await log_action(conn, admin["username"], "SCANNER_KEY_ROTATED", "Clave de expedición (Escáner Flex) generada.", get_client_ip(request))
    return {"api_key": clave, "message": "Guardá la clave ahora: no se vuelve a mostrar. La anterior dejó de valer."}


# --- API para el Escaner ---
def _envio(valor: str) -> str:
    envio = (valor or "").strip()
    if not _RE_ENVIO.fullmatch(envio):
        raise HTTPException(400, "Número de envío inválido.")
    return envio


async def _pedido_por_envio(conn: asyncpg.Connection, envio: str, bloquear: bool = False):
    # Si hubiera mas de un pedido con el mismo envio (uno cancelado y otro nuevo), vale el vigente mas nuevo.
    return await conn.fetchrow(f"""
        SELECT d.id, d.document_number, d.status, d.packed_at, d.dispatched_at, d.dispatch_ref, d.external_ref,
               d.external_account, d.shipping_type, COALESCE(d.priority, 0) > 0 AS urgent, d.channel_origin,
               d.channel_label_zpl, d.shipment_ref,
               COALESCE(c.company_name, d.buyer_name, 'Consumidor Final') AS client_name,
               COALESCE(a.full_address, d.buyer_address, 'A coordinar') AS delivery_address,
               (SELECT COALESCE(SUM(quantity_requested), 0) FROM document_lines WHERE document_id = d.id)::float AS units
        FROM documents d LEFT JOIN entities c ON d.customer_id = c.id LEFT JOIN entity_addresses a ON d.customer_address_id = a.id
        WHERE d.shipment_ref = $1
        ORDER BY (d.status <> 'CANCELLED') DESC, d.created_at DESC LIMIT 1
        {"FOR UPDATE OF d" if bloquear else ""}
    """, envio)


def _etapa(doc) -> str:
    estado = doc["status"]
    if estado == "DISPATCHED":
        return "DESPACHADO"
    if estado == "CANCELLED":
        return "CANCELADO"
    if estado == "FULL":
        return "FULL"
    if estado == "COMPLETED":
        return "EMPACADO" if doc["packed_at"] else "PARA_EMPACAR"
    return "EN_PREPARACION"


def _pedido_dict(doc) -> dict:
    etapa = _etapa(doc)
    return {"document_number": doc["document_number"], "status": doc["status"], "stage": etapa,
            "ready_to_load": etapa in ("EMPACADO", "DESPACHADO"), "message": MOTIVOS.get(etapa),
            "shipment_ref": doc["shipment_ref"], "external_ref": doc["external_ref"], "account": doc["external_account"],
            "channel": doc["channel_origin"], "shipping_type": doc["shipping_type"], "urgent": doc["urgent"],
            "buyer": doc["client_name"], "units": doc["units"], "packed_at": doc["packed_at"],
            "dispatched_at": doc["dispatched_at"], "dispatch_ref": doc["dispatch_ref"]}


@router.get("/api/v1/expedicion/envios/{shipment_ref}")
async def ver_envio(shipment_ref: str, _: None = Depends(require_expedicion), conn: asyncpg.Connection = Depends(get_db_connection)):
    """Pedido de Tracker de un numero de envio y si se puede cargar (stage EMPACADO o DESPACHADO). 404 si no existe."""
    doc = await _pedido_por_envio(conn, _envio(shipment_ref))
    if not doc:
        raise HTTPException(404, "No hay un pedido con ese número de envío en Tracker.")
    return _pedido_dict(doc)


@router.post("/api/v1/expedicion/envios/{shipment_ref}/despacho")
async def despachar_envio(shipment_ref: str, data: DespachoInput, request: Request, _: None = Depends(require_expedicion),
                          conn: asyncpg.Connection = Depends(get_db_connection)):
    """El paquete salio cargado en una ola: el pedido pasa a DISPATCHED y se avisa al canal. Repetirlo devuelve
    already=true sin cambiar nada (reintentos). 409 con el motivo si no esta empacado, esta cancelado o es Full."""
    envio = _envio(shipment_ref)
    async with conn.transaction():
        doc = await _pedido_por_envio(conn, envio, bloquear=True)
        if not doc:
            raise HTTPException(404, "No hay un pedido con ese número de envío en Tracker.")
        etapa = _etapa(doc)
        if etapa == "DESPACHADO":
            return {**_pedido_dict(doc), "already": True}
        if etapa != "EMPACADO":
            raise HTTPException(409, MOTIVOS[etapa])
        partes = [f"Ola {data.wave.strip()}" if data.wave and data.wave.strip() else "",
                  (data.plate or "").strip(), (data.carrier or "").strip(), (data.user or "").strip()]
        referencia = " · ".join(p for p in partes if p)[:200] or "Escáner Flex"
        await conn.execute("UPDATE documents SET status = 'DISPATCHED', dispatched_at = now(), dispatch_ref = $2 WHERE id = $1",
                           doc["id"], referencia)
        await emitir_estado_a_canal(conn, doc["id"])
        await log_action(conn, "escaner", "SCANNER_DISPATCH",
                         f"Despachó {doc['document_number']} (envío {envio}) desde el Escáner Flex: {referencia}", get_client_ip(request))
        await dispatch_event_to_channels(conn, "OUTBOUND_DESPACHO", {
            "event": "order.dispatched", "order_number": doc["document_number"], "channel_origin": doc["channel_origin"],
            "client_name": doc["client_name"], "delivery_address": doc["delivery_address"], "dispatch_ref": referencia,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        doc = await _pedido_por_envio(conn, envio)
    return {**_pedido_dict(doc), "already": False}
