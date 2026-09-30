from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
import asyncpg, uuid

try:
    from backend.database import get_db_connection, get_current_user
except ImportError:
    from database import get_db_connection, get_current_user

router = APIRouter(tags=["Document Notes"])

# Observaciones de documentos: se agregan y se leen, nunca se editan ni se borran (sirven de registro).
# Tipo -> (tabla, columna del numero, solo ADMIN/SUPERVISOR). Puede agregar observaciones quien puede
# ver el documento: pedidos, traspasos y remitos los ve cualquier usuario (picking, recepcion y
# traspasos en el celular); ordenes de compra y devoluciones, solo ADMIN y SUPERVISOR.
DOC_TYPES = {
    "PEDIDO": ("documents", "document_number", False),
    "TRASPASO": ("transfer_orders", "transfer_number", False),
    "REMITO": ("purchase_remitos", "remito_number", False),
    "OC": ("purchase_orders", "order_number", True),
    "DEVOLUCION": ("customer_returns", "return_number", True),
}
MAX_NOTE_LENGTH = 2000

class NoteInput(BaseModel):
    text: str

async def _resolve_document(conn: asyncpg.Connection, user: dict, doc_type: str, number: str):
    spec = DOC_TYPES.get(doc_type.upper())
    if not spec: raise HTTPException(404, "Tipo de documento desconocido.")
    table, column, restricted = spec
    if restricted and user.get("role") not in ("ADMIN", "SUPERVISOR"):
        raise HTTPException(403, "Permisos insuficientes.")
    try:
        doc_id = await conn.fetchval(f"SELECT id FROM {table} WHERE id = $1", uuid.UUID(number.strip()))
        if not doc_id: raise HTTPException(404, "Documento no encontrado.")
        return doc_type.upper(), doc_id
    except ValueError:
        pass
    ids = await conn.fetch(f"SELECT id FROM {table} WHERE UPPER({column}) = $1", number.strip().upper())
    if not ids: raise HTTPException(404, "Documento no encontrado.")
    if len(ids) > 1: raise HTTPException(409, "Hay más de un documento con ese número: indicalo por su identificador.")
    return doc_type.upper(), ids[0]["id"]

@router.get("/api/documents/{doc_type}/{number}/notes")
async def list_document_notes(doc_type: str, number: str, user: dict = Depends(get_current_user), conn: asyncpg.Connection = Depends(get_db_connection)):
    kind, doc_id = await _resolve_document(conn, user, doc_type, number)
    rows = await conn.fetch("""
        SELECT id::text, body, source, username, created_at FROM document_notes
        WHERE doc_type = $1 AND doc_id = $2 ORDER BY created_at ASC
    """, kind, doc_id)
    return [dict(r) for r in rows]

@router.post("/api/documents/{doc_type}/{number}/notes")
async def add_document_note(doc_type: str, number: str, data: NoteInput, user: dict = Depends(get_current_user), conn: asyncpg.Connection = Depends(get_db_connection)):
    kind, doc_id = await _resolve_document(conn, user, doc_type, number)
    text = data.text.strip()
    if not text: raise HTTPException(400, "La observación está vacía.")
    if len(text) > MAX_NOTE_LENGTH: raise HTTPException(400, f"La observación no puede superar los {MAX_NOTE_LENGTH} caracteres.")
    row = await conn.fetchrow("""
        INSERT INTO document_notes (doc_type, doc_id, body, source, username) VALUES ($1, $2, $3, 'USUARIO', $4)
        RETURNING id::text, body, source, username, created_at
    """, kind, doc_id, text, user.get("username"))
    return dict(row)
