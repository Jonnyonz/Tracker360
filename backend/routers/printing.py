from fastapi import APIRouter, Depends, HTTPException, Header, Request
from backend.database import get_db_connection, get_current_user, require_admin, require_supervisor, verify_system_api_key, log_action, get_client_ip
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, timedelta, timezone
import asyncpg, uuid, secrets, hashlib, base64, re

router = APIRouter()

class PrintJobItem(BaseModel):
    sku: str
    quantity: int = 1

class PrintJobRequest(BaseModel):
    queue_code: str = "RECEPCION"
    skus: list[str] = []
    items: list[PrintJobItem] = []

class AgentAuthorizeRequest(BaseModel):
    challenge: str
    agent_name: str = ""

class AgentTokenRequest(BaseModel):
    code: str
    code_verifier: str

AGENT_AUTH_ROLES = {"ADMIN", "SUPERVISOR"}
AUTH_CODE_TTL = timedelta(minutes=5)
PKCE_RE = re.compile(r"^[A-Za-z0-9_-]{43,128}$")

def _sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()

def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")

async def verify_print_agent(request: Request, authorization: Optional[str] = Header(None), x_api_key: Optional[str] = Header(None), conn: asyncpg.Connection = Depends(get_db_connection)):
    # Agente autorizado desde el navegador: Authorization: Bearer <token>
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:].strip()
        # Si el usuario que lo autorizo se desactiva, el agente deja de funcionar.
        row = await conn.fetchrow("""
            SELECT t.id, t.agent_name FROM print_agent_tokens t
            JOIN users u ON u.username = t.created_by
            WHERE t.token_hash = $1 AND t.is_active = TRUE AND u.is_active = TRUE
        """, _sha256_hex(token))
        if row:
            await conn.execute("UPDATE print_agent_tokens SET last_used_at = NOW() WHERE id = $1", row["id"])
            return row["agent_name"]
        client_ip = get_client_ip(request)
        await log_action(conn, "SYSTEM", "API_INTRUSION", "Agente de impresion con token invalido o revocado", client_ip)
        raise HTTPException(status_code=401, detail="Token de agente invalido o revocado.")
    # Agentes existentes configurados con la clave API del sistema.
    return await verify_system_api_key(request, x_api_key, conn)

# === LOGIN DEL AGENTE POR NAVEGADOR ===
@router.post("/api/print-agent/authorize")
async def authorize_agent(req: AgentAuthorizeRequest, request: Request, user: dict = Depends(get_current_user), conn: asyncpg.Connection = Depends(get_db_connection)):
    client_ip = get_client_ip(request)
    if user.get("role") not in AGENT_AUTH_ROLES:
        await log_action(conn, user.get("username", "Unknown"), "UNAUTHORIZED_ACCESS", "Intento de autorizar agente de impresion sin permisos", client_ip)
        raise HTTPException(status_code=403, detail="Solo un administrador o supervisor puede autorizar agentes de impresion.")
    if not PKCE_RE.match(req.challenge):
        raise HTTPException(status_code=400, detail="Solicitud de autorizacion invalida.")
    agent_name = (req.agent_name or "").strip()[:100] or "Agente de impresion"
    code = secrets.token_urlsafe(32)
    await conn.execute("""
        INSERT INTO print_agent_auth_codes (code_hash, challenge, agent_name, username, expires_at)
        VALUES ($1, $2, $3, $4, $5)
    """, _sha256_hex(code), req.challenge, agent_name, user["username"], datetime.now(timezone.utc) + AUTH_CODE_TTL)
    await conn.execute("DELETE FROM print_agent_auth_codes WHERE expires_at < NOW() OR used = TRUE")
    return {"code": code}

@router.post("/api/print-agent/token")
async def exchange_agent_token(req: AgentTokenRequest, request: Request, conn: asyncpg.Connection = Depends(get_db_connection)):
    client_ip = get_client_ip(request)
    if not PKCE_RE.match(req.code_verifier):
        raise HTTPException(status_code=400, detail="Codigo de autorizacion invalido o expirado.")
    # El codigo se consume en el primer intento, sea valido o no el verificador.
    row = await conn.fetchrow("""
        UPDATE print_agent_auth_codes SET used = TRUE
        WHERE code_hash = $1 AND used = FALSE AND expires_at > NOW()
        RETURNING challenge, agent_name, username
    """, _sha256_hex(req.code))
    if not row or not secrets.compare_digest(row["challenge"], _pkce_challenge(req.code_verifier)):
        await log_action(conn, "SYSTEM", "API_INTRUSION", "Canje de codigo de agente de impresion invalido", client_ip)
        raise HTTPException(status_code=400, detail="Codigo de autorizacion invalido o expirado.")
    token = secrets.token_urlsafe(32)
    await conn.execute("""
        INSERT INTO print_agent_tokens (token_hash, agent_name, created_by) VALUES ($1, $2, $3)
    """, _sha256_hex(token), row["agent_name"], row["username"])
    await log_action(conn, row["username"], "PRINT_AGENT_AUTHORIZED", f"Agente de impresion autorizado: {row['agent_name']}", client_ip)
    return {"token": token, "agent_name": row["agent_name"], "authorized_by": row["username"]}

@router.get("/api/admin/print-agents")
async def list_print_agents(admin: dict = Depends(require_supervisor), conn: asyncpg.Connection = Depends(get_db_connection)):
    rows = await conn.fetch("SELECT id, agent_name, created_by, is_active, created_at, last_used_at FROM print_agent_tokens ORDER BY created_at DESC")
    return [dict(r) for r in rows]

@router.post("/api/admin/print-agents/{agent_id}/revoke")
async def revoke_print_agent(agent_id: str, request: Request, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    try:
        agent_uuid = uuid.UUID(agent_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="ID de agente invalido.")
    row = await conn.fetchrow("UPDATE print_agent_tokens SET is_active = FALSE WHERE id = $1 RETURNING agent_name", agent_uuid)
    if not row:
        raise HTTPException(status_code=404, detail="Agente no encontrado.")
    client_ip = get_client_ip(request)
    await log_action(conn, admin["username"], "PRINT_AGENT_REVOKED", f"Agente de impresion revocado: {row['agent_name']}", client_ip)
    return {"status": "ok"}

# === COLA DE IMPRESION ===
@router.get("/api/print-agent/jobs")
async def get_pending_jobs(queue_code: str = "RECEPCION", agent=Depends(verify_print_agent), conn: asyncpg.Connection = Depends(get_db_connection)):
    clean_q = queue_code.strip().upper() if queue_code else "RECEPCION"
    if clean_q in ["RECEPCION", "RECEPCIÓN", "1", ""]:
        jobs = await conn.fetch("""
            SELECT id, zpl_content
            FROM print_jobs
            WHERE status = 'PENDING'
              AND (UPPER(TRIM(queue_code)) IN ('RECEPCION', 'RECEPCIÓN', '1', '') OR queue_code IS NULL)
            ORDER BY created_at ASC
        """)
    else:
        jobs = await conn.fetch("""
            SELECT id, zpl_content
            FROM print_jobs
            WHERE status = 'PENDING' AND UPPER(TRIM(queue_code)) = $1
            ORDER BY created_at ASC
        """, clean_q)

    return [{"id": str(j["id"]), "zpl": j["zpl_content"], "zpl_content": j["zpl_content"]} for j in jobs]

@router.post("/api/print-agent/jobs/{job_id}/ack")
async def ack_print_job(job_id: str, agent=Depends(verify_print_agent), conn: asyncpg.Connection = Depends(get_db_connection)):
    try:
        await conn.execute("UPDATE print_jobs SET status = 'COMPLETED' WHERE CAST(id AS TEXT) = $1", str(job_id).strip())
    except Exception as e:
        print(f"[ACK ERROR]: {e!r}")
        raise HTTPException(status_code=500, detail="Error al confirmar el trabajo de impresion.")
    return {"status": "ok", "job_id": job_id}

@router.post("/api/admin/print-jobs")
async def create_print_job(req: PrintJobRequest, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    q_code = req.queue_code.strip().upper() if req.queue_code else "RECEPCION"
    sku_list = list(req.skus)
    for it in req.items:
        if it.sku:
            sku_list.extend([it.sku] * max(1, it.quantity))

    if not sku_list:
        raise HTTPException(status_code=400, detail="Debe ingresar al menos un SKU para imprimir.")

    template_row = await conn.fetchrow("SELECT value FROM system_settings WHERE key = 'zpl_template'")
    custom_tpl = template_row["value"] if template_row and template_row["value"] else None

    inserted = 0
    for sku in sku_list:
        clean_sku = str(sku).strip().upper()
        if not clean_sku: continue

        item_row = await conn.fetchrow("SELECT description FROM items WHERE UPPER(sku) = $1 LIMIT 1", clean_sku)
        clean_desc = item_row["description"] if item_row and item_row["description"] else clean_sku
        short_desc = clean_desc[:22]

        if custom_tpl:
            zpl = custom_tpl
            for tag in ["{{SKU}}", "{{sku}}", "{SKU}", "{sku}", "{{ SKU }}"]:
                zpl = zpl.replace(tag, clean_sku)
            for tag in ["{{DESC}}", "{{desc}}", "{DESC}", "{desc}", "{{DESCRIPTION}}", "{{description}}", "{{ DESC }}"]:
                zpl = zpl.replace(tag, short_desc)
        else:
            zpl = f"^XA\n^PW304\n^LL160\n^LS0\n^FO40,25^A0N,24,24^FD{clean_sku}^FS\n^FO40,65^A0N,18,18^FD{short_desc}^FS\n^FO205,20^BQN,2,3^FDLA,{clean_sku}^FS\n^XZ"

        await conn.execute("""
            INSERT INTO print_jobs (id, queue_code, zpl_content, status, created_at)
            VALUES ($1, $2, $3, 'PENDING', NOW())
        """, str(uuid.uuid4()), q_code, zpl)
        inserted += 1

    return {"status": "ok", "jobs_created": inserted}
