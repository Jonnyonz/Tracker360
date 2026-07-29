import os, asyncio, uuid, secrets, json, urllib.request
from decimal import Decimal
from datetime import datetime, timedelta, timezone
import jwt, asyncpg
from fastapi import HTTPException, Header, Request, Depends
from passlib.context import CryptContext
from typing import Optional, Dict

# === SEGURIDAD Y CONFIGURACIÓN ===
SECRET_KEY = os.getenv("SECRET_KEY", secrets.token_hex(32))
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 480

pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")
def verify_password(p, h): return pwd_context.verify(p, h)
def get_password_hash(p): return pwd_context.hash(p)

def create_access_token(data: dict):
    to_encode = data.copy()
    to_encode.update({"exp": datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

# === PROTECCIÓN ANTI-FUERZA BRUTA ===
FAILED_LOGINS: Dict[str, Dict] = {}

def check_rate_limit(ip: str):
    now = datetime.now(timezone.utc)
    if ip in FAILED_LOGINS:
        info = FAILED_LOGINS[ip]
        if info["blocked_until"] and now < info["blocked_until"]:
            time_left = int((info["blocked_until"] - now).total_seconds() / 60) + 1
            raise HTTPException(status_code=429, detail=f"Demasiados intentos fallidos. Bloqueado por {time_left} min.")
        elif info["blocked_until"] and now >= info["blocked_until"]:
            FAILED_LOGINS[ip] = {"count": 0, "blocked_until": None}

def record_failed_login(ip: str):
    now = datetime.now(timezone.utc)
    info = FAILED_LOGINS.get(ip, {"count": 0, "blocked_until": None})
    info["count"] += 1
    if info["count"] >= 5:
        info["blocked_until"] = now + timedelta(minutes=15)
    FAILED_LOGINS[ip] = info

def reset_failed_login(ip: str):
    if ip in FAILED_LOGINS:
        del FAILED_LOGINS[ip]

# === AUXILIARES DE NEGOCIO ===
def build_full_address(street: Optional[str], number: Optional[str], zip_code: Optional[str], city_neighborhood: Optional[str], fallback: Optional[str] = "") -> str:
    parts = []
    st_num = f"{street or ''} {number or ''}".strip()
    if st_num: parts.append(st_num)
    if city_neighborhood and city_neighborhood.strip(): parts.append(city_neighborhood.strip())
    if zip_code and zip_code.strip(): parts.append(f"CP {zip_code.strip()}")
    composed = ", ".join(parts)
    return composed if composed else (fallback or "Dirección no especificada")

class DB:
    pool: Optional[asyncpg.Pool] = None

async def get_db_connection():
    if DB.pool is None: raise HTTPException(status_code=503, detail="Servicio de base de datos no disponible.")
    async with DB.pool.acquire() as conn: yield conn

async def log_action(conn: asyncpg.Connection, username: str, action: str, details: str, ip_address: str = "127.0.0.1"):
    try: await conn.execute("INSERT INTO audit_logs (username, action, details) VALUES ($1, $2, $3)", username, action, f"[{ip_address}] {details}")
    except Exception: pass

def send_webhook_sync(url: str, payload: dict, api_key: str = ""):
    headers = {'Content-Type': 'application/json'}
    if api_key and api_key.strip():
        headers['Authorization'] = f"Bearer {api_key.strip()}"
    req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'), headers=headers, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=5) as response: return response.status
    except Exception: return None

async def dispatch_event_to_channels(conn: asyncpg.Connection, event_type: str, payload: dict):
    channels = await conn.fetch("SELECT target_url, api_key FROM integration_channels WHERE channel_type = $1 AND is_active = TRUE", event_type)
    for ch in channels:
        if ch["target_url"] and ch["target_url"].startswith("http"):
            asyncio.create_task(asyncio.to_thread(send_webhook_sync, ch["target_url"], payload, ch["api_key"] or ""))

async def record_stock_movement(conn: asyncpg.Connection, sku: str, branch_id: uuid.UUID, sector_id: uuid.UUID, location_id: Optional[uuid.UUID], quantity: float, movement_type: str, ref_doc: str, username: str):
    await conn.execute("INSERT INTO stock_movements (sku, branch_id, sector_id, location_id, quantity, movement_type, reference_document, username) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)", sku.upper(), branch_id, sector_id, location_id, quantity, movement_type, ref_doc, username)
    if location_id:
        await conn.execute("INSERT INTO stock_inventory (branch_id, sector_id, location_id, sku, quantity) VALUES ($1, $2, $3, $4, $5) ON CONFLICT (branch_id, sector_id, location_id, sku) DO UPDATE SET quantity = stock_inventory.quantity + EXCLUDED.quantity, updated_at = CURRENT_TIMESTAMP", branch_id, sector_id, location_id, sku.upper(), quantity)
    else:
        await conn.execute("INSERT INTO stock_inventory (branch_id, sector_id, location_id, sku, quantity) VALUES ($1, $2, NULL, $3, $4) ON CONFLICT (branch_id, sector_id, sku) WHERE location_id IS NULL DO UPDATE SET quantity = stock_inventory.quantity + EXCLUDED.quantity, updated_at = CURRENT_TIMESTAMP", branch_id, sector_id, sku.upper(), quantity)

    total_qty = await conn.fetchval("SELECT COALESCE(SUM(quantity), 0) FROM stock_inventory WHERE UPPER(sku) = $1", sku.upper())
    stock_payload = { "event": "stock.updated", "sku": sku.upper(), "available_quantity": float(total_qty), "timestamp": datetime.now(timezone.utc).isoformat() }
    await dispatch_event_to_channels(conn, "OUTBOUND_STOCK", stock_payload)

async def queue_zpl_print_job(conn: asyncpg.Connection, queue_code: str, zpl_content: str):
    await conn.execute("INSERT INTO print_jobs (queue_code, zpl_content, status) VALUES ($1, $2, 'PENDING')", queue_code.strip().upper(), zpl_content)

# === AUTENTICACIÓN Y MIDDLEWARE INYECCIÓN DE DEPENDENCIAS ===
async def get_current_user(request: Request, conn: asyncpg.Connection = Depends(get_db_connection)):
    token = request.cookies.get("access_token")
    if not token or not token.startswith("Bearer "): raise HTTPException(status_code=401, detail="Sesión expirada.")
    try:
        payload = jwt.decode(token.split(" ")[1], SECRET_KEY, algorithms=[ALGORITHM])
        user = await conn.fetchrow("SELECT id, username, role, is_active FROM users WHERE username = $1", payload.get("sub"))
        if not user or not user["is_active"]: raise HTTPException(status_code=401, detail="Usuario desactivado.")
        return dict(user)
    except jwt.PyJWTError: raise HTTPException(status_code=401, detail="Sesión inválida.")

async def require_admin(current_user: dict = Depends(get_current_user)):
    if current_user.get("role") != "ADMIN": raise HTTPException(status_code=403, detail="Permisos insuficientes.")
    return current_user

async def verify_system_api_key(x_api_key: Optional[str] = Header(None), conn: asyncpg.Connection = Depends(get_db_connection)):
    if not x_api_key: raise HTTPException(status_code=401, detail="Cabecera X-API-Key requerida.")
    valid_key = await conn.fetchval("SELECT value FROM system_settings WHERE key = 'tracker360_api_key'")
    if not valid_key or x_api_key.strip() != valid_key.strip(): raise HTTPException(status_code=403, detail="Clave API de Tracker360 inválida.")
    return True

# === INICIALIZACIÓN DE TABLAS (DDL) ===
async def init_db_schema():
    for attempt in range(10):
        try:
            DB.pool = await asyncpg.create_pool(
                user=os.getenv("POSTGRES_USER", "tracker_admin"),
                password=os.getenv("POSTGRES_PASSWORD", "tracker_secure_pass_2026"),
                database=os.getenv("POSTGRES_DB", "tracker360_db"),
                host="db", port=5432, min_size=1, max_size=20
            )
            if DB.pool is not None: break
        except Exception: await asyncio.sleep(1.0)

    if DB.pool is not None:
        try:
            async with DB.pool.acquire() as conn:
                ddl_statements = [
                    "CREATE TABLE IF NOT EXISTS system_settings (key VARCHAR(100) PRIMARY KEY, value TEXT);",
                    "CREATE TABLE IF NOT EXISTS users (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), username VARCHAR(50) UNIQUE NOT NULL, full_name VARCHAR(100) NOT NULL, password_hash TEXT NOT NULL, role VARCHAR(20) NOT NULL DEFAULT 'PREPARADOR', is_active BOOLEAN DEFAULT TRUE, created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP);",
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS email VARCHAR(150);",
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS branch_id VARCHAR(100);",
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS sector_id VARCHAR(100);",
                    "CREATE TABLE IF NOT EXISTS branches (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), code VARCHAR(50) UNIQUE NOT NULL, name VARCHAR(150) NOT NULL, is_active BOOLEAN DEFAULT TRUE, created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP);",
                    "CREATE TABLE IF NOT EXISTS entities (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), tax_id VARCHAR(50) UNIQUE NOT NULL, company_name VARCHAR(150) NOT NULL, is_customer BOOLEAN DEFAULT TRUE, is_supplier BOOLEAN DEFAULT FALSE, is_active BOOLEAN DEFAULT TRUE, created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP);",
                    "CREATE TABLE IF NOT EXISTS entity_addresses (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), entity_id UUID REFERENCES entities(id) ON DELETE CASCADE, address_label VARCHAR(100) NOT NULL, full_address TEXT NOT NULL, created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP);",
                    "ALTER TABLE entity_addresses ADD COLUMN IF NOT EXISTS street VARCHAR(150);",
                    "ALTER TABLE entity_addresses ADD COLUMN IF NOT EXISTS number VARCHAR(50);",
                    "ALTER TABLE entity_addresses ADD COLUMN IF NOT EXISTS zip_code VARCHAR(20);",
                    "ALTER TABLE entity_addresses ADD COLUMN IF NOT EXISTS city_neighborhood VARCHAR(150);",
                    "ALTER TABLE entity_addresses ADD COLUMN IF NOT EXISTS is_default BOOLEAN DEFAULT FALSE;",
                    "CREATE TABLE IF NOT EXISTS items (sku VARCHAR(100) PRIMARY KEY, description TEXT NOT NULL, category VARCHAR(100), length FLOAT DEFAULT 0, width FLOAT DEFAULT 0, height FLOAT DEFAULT 0, weight FLOAT DEFAULT 0, volume FLOAT DEFAULT 0, is_active BOOLEAN DEFAULT TRUE, created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP);",
                    "ALTER TABLE items ADD COLUMN IF NOT EXISTS length FLOAT DEFAULT 0;",
                    "ALTER TABLE items ADD COLUMN IF NOT EXISTS width FLOAT DEFAULT 0;",
                    "ALTER TABLE items ADD COLUMN IF NOT EXISTS height FLOAT DEFAULT 0;",
                    "ALTER TABLE items ADD COLUMN IF NOT EXISTS weight FLOAT DEFAULT 0;",
                    "ALTER TABLE items ADD COLUMN IF NOT EXISTS volume FLOAT DEFAULT 0;",
                    "CREATE TABLE IF NOT EXISTS sectors (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), branch_id UUID REFERENCES branches(id) ON DELETE CASCADE, name VARCHAR(100) UNIQUE NOT NULL, print_queue_code VARCHAR(50) UNIQUE NOT NULL, uses_locations BOOLEAN DEFAULT FALSE, is_active BOOLEAN DEFAULT TRUE, created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP);",
                    "CREATE TABLE IF NOT EXISTS locations (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), sector_id UUID REFERENCES sectors(id) ON DELETE CASCADE, location_code VARCHAR(100) NOT NULL, description VARCHAR(255), is_active BOOLEAN DEFAULT TRUE);",
                    "CREATE TABLE IF NOT EXISTS item_locations (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), item_sku VARCHAR(100) NOT NULL, location_id UUID REFERENCES locations(id) ON DELETE CASCADE, created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP, UNIQUE(item_sku, location_id));",
                    "CREATE TABLE IF NOT EXISTS documents (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), document_number VARCHAR(50) UNIQUE NOT NULL, document_type VARCHAR(20) DEFAULT 'PICKING', channel_origin VARCHAR(50) DEFAULT 'INTERNAL', status VARCHAR(20) DEFAULT 'PENDING', label_printed BOOLEAN DEFAULT FALSE, created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP);",
                    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS channel_origin VARCHAR(50) DEFAULT 'INTERNAL';",
                    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS customer_id UUID;",
                    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS customer_address_id UUID;",
                    "ALTER TABLE documents DROP CONSTRAINT IF EXISTS documents_customer_id_fkey;",
                    "ALTER TABLE documents ADD CONSTRAINT documents_customer_id_fkey FOREIGN KEY (customer_id) REFERENCES entities(id) ON DELETE SET NULL;",
                    "ALTER TABLE documents DROP CONSTRAINT IF EXISTS documents_customer_address_id_fkey;",
                    "ALTER TABLE documents ADD CONSTRAINT documents_customer_address_id_fkey FOREIGN KEY (customer_address_id) REFERENCES entity_addresses(id) ON DELETE SET NULL;",
                    "CREATE TABLE IF NOT EXISTS document_lines (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), document_id UUID REFERENCES documents(id) ON DELETE CASCADE, sku VARCHAR(100) NOT NULL, quantity_requested NUMERIC NOT NULL, quantity_picked NUMERIC DEFAULT 0);",
                    "CREATE TABLE IF NOT EXISTS audit_logs (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), username VARCHAR(50) NOT NULL, action VARCHAR(50) NOT NULL, details TEXT, created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP);",
                    "CREATE TABLE IF NOT EXISTS print_jobs (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), queue_code VARCHAR(50) NOT NULL, zpl_content TEXT NOT NULL, status VARCHAR(20) DEFAULT 'PENDING', created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP);",
                    "CREATE TABLE IF NOT EXISTS stock_inventory (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), branch_id UUID REFERENCES branches(id) ON DELETE CASCADE, sector_id UUID REFERENCES sectors(id) ON DELETE CASCADE, location_id UUID REFERENCES locations(id) ON DELETE CASCADE, sku VARCHAR(100) NOT NULL, quantity NUMERIC DEFAULT 0, updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP);",
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_stock_loc_sku ON stock_inventory (branch_id, sector_id, location_id, sku) WHERE location_id IS NOT NULL;",
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_stock_noloc_sku ON stock_inventory (branch_id, sector_id, sku) WHERE location_id IS NULL;",
                    "CREATE TABLE IF NOT EXISTS stock_movements (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), sku VARCHAR(100) NOT NULL, branch_id UUID REFERENCES branches(id), sector_id UUID REFERENCES sectors(id), location_id UUID REFERENCES locations(id), quantity NUMERIC NOT NULL, movement_type VARCHAR(50) NOT NULL, reference_document VARCHAR(100), username VARCHAR(50) NOT NULL, created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP);",
                    "CREATE TABLE IF NOT EXISTS purchase_orders (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), order_number VARCHAR(50) UNIQUE NOT NULL, supplier_id UUID REFERENCES entities(id), status VARCHAR(20) DEFAULT 'PENDING', created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP);",
                    "CREATE TABLE IF NOT EXISTS purchase_order_lines (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), purchase_order_id UUID REFERENCES purchase_orders(id) ON DELETE CASCADE, sku VARCHAR(100) NOT NULL, quantity_ordered NUMERIC NOT NULL, quantity_received NUMERIC DEFAULT 0);",
                    "CREATE TABLE IF NOT EXISTS purchase_remitos (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), remito_number VARCHAR(50) UNIQUE NOT NULL, supplier_id UUID REFERENCES entities(id), purchase_order_id UUID REFERENCES purchase_orders(id), branch_id UUID REFERENCES branches(id), sector_id UUID REFERENCES sectors(id), status VARCHAR(20) DEFAULT 'PENDING_CONTROL', created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP);",
                    "CREATE TABLE IF NOT EXISTS purchase_remito_lines (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), purchase_remito_id UUID REFERENCES purchase_remitos(id) ON DELETE CASCADE, sku VARCHAR(100) NOT NULL, quantity_sent NUMERIC NOT NULL, quantity_received NUMERIC DEFAULT 0, location_id UUID REFERENCES locations(id));",
                    "CREATE TABLE IF NOT EXISTS purchase_invoices (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), invoice_number VARCHAR(50) UNIQUE NOT NULL, supplier_id UUID REFERENCES entities(id), invoice_type VARCHAR(10) DEFAULT 'A', created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP);",
                    "CREATE TABLE IF NOT EXISTS purchase_invoice_lines (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), purchase_invoice_id UUID REFERENCES purchase_invoices(id) ON DELETE CASCADE, sku VARCHAR(100) NOT NULL, quantity NUMERIC NOT NULL, unit_price NUMERIC DEFAULT 0);",
                    "CREATE TABLE IF NOT EXISTS purchase_invoice_remitos (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), purchase_invoice_id UUID REFERENCES purchase_invoices(id) ON DELETE CASCADE, purchase_remito_id UUID REFERENCES purchase_remitos(id) ON DELETE CASCADE);",
                    "CREATE TABLE IF NOT EXISTS purchase_invoice_orders (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), purchase_invoice_id UUID REFERENCES purchase_invoices(id) ON DELETE CASCADE, purchase_order_id UUID REFERENCES purchase_orders(id) ON DELETE CASCADE);",
                    "CREATE TABLE IF NOT EXISTS transfer_orders (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), transfer_number VARCHAR(50) UNIQUE NOT NULL, origin_branch_id UUID REFERENCES branches(id), origin_sector_id UUID REFERENCES sectors(id), destination_branch_id UUID REFERENCES branches(id), destination_sector_id UUID REFERENCES sectors(id), status VARCHAR(20) DEFAULT 'PENDING_CONTROL', created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP);",
                    "CREATE TABLE IF NOT EXISTS transfer_order_lines (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), transfer_order_id UUID REFERENCES transfer_orders(id) ON DELETE CASCADE, sku VARCHAR(100) NOT NULL, quantity_sent NUMERIC NOT NULL, quantity_received NUMERIC DEFAULT 0, origin_location_id UUID REFERENCES locations(id), destination_location_id UUID REFERENCES locations(id));",
                    "CREATE TABLE IF NOT EXISTS integration_channels (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), name VARCHAR(100) NOT NULL, channel_type VARCHAR(50) NOT NULL, target_url TEXT NOT NULL, api_key TEXT, is_active BOOLEAN DEFAULT TRUE, created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP);",
                    "INSERT INTO system_settings (key, value) VALUES ('allow_multiproduct_locations', 'false') ON CONFLICT (key) DO NOTHING;",
                    "INSERT INTO system_settings (key, value) VALUES ('require_mobile_reception', 'false') ON CONFLICT (key) DO NOTHING;",
                    "INSERT INTO system_settings (key, value) VALUES ('enable_item_dimensions', 'false') ON CONFLICT (key) DO NOTHING;",
                    "INSERT INTO system_settings (key, value) VALUES ('app_name', 'Tracker360') ON CONFLICT (key) DO NOTHING;",
                    "INSERT INTO system_settings (key, value) VALUES ('zpl_order_width', '100') ON CONFLICT (key) DO NOTHING;",
                    "INSERT INTO system_settings (key, value) VALUES ('zpl_order_height', '150') ON CONFLICT (key) DO NOTHING;",
                    "INSERT INTO system_settings (key, value) VALUES ('zpl_order_template', '^XA^FO50,50^A0N,40,40^FDPEDIDO: {order_number}^FS^FO50,110^A0N,30,30^FDCLIENTE: {client_name}^FS^FO50,170^A0N,25,25^FDDIR: {delivery_address}^FS^FO50,230^BY3^BCN,100,Y,N,N^FD{order_number}^FS^XZ') ON CONFLICT (key) DO NOTHING;",
                    "INSERT INTO system_settings (key, value) VALUES ('zpl_item_template', '^XA^FO50,30^A0N,30,30^FD{description}^FS^FO50,70^A0N,25,25^FDSKU: {sku}^FS^FO50,110^BY2^BCN,80,Y,N,N^FD{sku}^FS^XZ') ON CONFLICT (key) DO NOTHING;"
                ]

                for stmt in ddl_statements:
                    try: await conn.execute(stmt)
                    except Exception: pass

                user_count = await conn.fetchval("SELECT COUNT(*) FROM users")
                if user_count == 0:
                    init_pass = os.getenv("INITIAL_ADMIN_PASSWORD", "admin360")
                    hashed_pass = get_password_hash(init_pass)
                    await conn.execute(
                        "INSERT INTO users (username, full_name, password_hash, role) VALUES ($1, $2, $3, 'ADMIN')",
                        "admin", "Administrador Inicial", hashed_pass
                    )

                sys_key = await conn.fetchval("SELECT value FROM system_settings WHERE key = 'tracker360_api_key'")
                if not sys_key:
                    new_key = f"trk_live_{secrets.token_hex(24)}"
                    await conn.execute("INSERT INTO system_settings (key, value) VALUES ('tracker360_api_key', $1) ON CONFLICT (key) DO NOTHING", new_key)

                branch_count = await conn.fetchval("SELECT COUNT(*) FROM branches")
                if branch_count == 0:
                    default_branch_id = await conn.fetchval("INSERT INTO branches (code, name) VALUES ('SUC-01', 'Sucursal Central') RETURNING id")
                    await conn.execute("UPDATE sectors SET branch_id = $1 WHERE branch_id IS NULL", default_branch_id)
        except Exception: pass
