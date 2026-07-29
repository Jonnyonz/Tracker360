import os, asyncio, csv, uuid, secrets, json, urllib.request
from decimal import Decimal
from io import StringIO
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import jwt, asyncpg
from fastapi import FastAPI, Depends, HTTPException, status, Response, Request, UploadFile, File, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from passlib.context import CryptContext
from typing import List, Optional, Dict

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

# === PROTECCIÓN ANTI-FUERZA BRUTA (IN-MEMORY RATE LIMITER) ===
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

async def dispatch_event_to_channels(conn: asyncpg.Connection, event_type: str, payload: dict):
    channels = await conn.fetch("SELECT target_url, api_key FROM integration_channels WHERE channel_type = $1 AND is_active = TRUE", event_type)
    for ch in channels:
        if ch["target_url"] and ch["target_url"].startswith("http"):
            asyncio.create_task(asyncio.to_thread(send_webhook_sync, ch["target_url"], payload, ch["api_key"] or ""))

def send_webhook_sync(url: str, payload: dict, api_key: str = ""):
    headers = {'Content-Type': 'application/json'}
    if api_key and api_key.strip():
        headers['Authorization'] = f"Bearer {api_key.strip()}"
    req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'), headers=headers, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=5) as response: return response.status
    except Exception: return None

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

@asynccontextmanager
async def lifespan(app: FastAPI):
    for attempt in range(10):
        try:
            DB.pool = await asyncpg.create_pool(user=os.getenv("POSTGRES_USER", "tracker_admin"), password=os.getenv("POSTGRES_PASSWORD", "tracker_secure_pass_2026"), database=os.getenv("POSTGRES_DB", "tracker360_db"), host="db", port=5432, min_size=1, max_size=20)
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
    yield
    if DB.pool is not None: await DB.pool.close()

app = FastAPI(title="Tracker360 API", version="1.0", lifespan=lifespan)

# === MIDDLEWARE CABECERAS DE SEGURIDAD HTTP ===
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response

# === MIDDLEWARE CORS HARDENED ===
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https?://.*",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# === MODELOS PYDANTIC ===
class LoginRequest(BaseModel): username: str; password: str
class UserCreate(BaseModel): username: str; full_name: str; password: str; role: str = "PREPARADOR"; email: Optional[str] = None; branch_id: Optional[str] = None; sector_id: Optional[str] = None
class UserUpdate(BaseModel): full_name: Optional[str] = None; role: Optional[str] = None; email: Optional[str] = None; branch_id: Optional[str] = None; sector_id: Optional[str] = None; is_active: Optional[bool] = None; password: Optional[str] = None

class AddressCreate(BaseModel):
    address_label: str
    street: Optional[str] = None
    number: Optional[str] = None
    zip_code: Optional[str] = None
    city_neighborhood: Optional[str] = None
    full_address: Optional[str] = None
    is_default: Optional[bool] = False

class EntityCreate(BaseModel):
    tax_id: str
    company_name: str
    is_customer: bool = True
    is_supplier: bool = False
    initial_address: Optional[AddressCreate] = None

class EntityUpdate(BaseModel):
    company_name: Optional[str] = None
    tax_id: Optional[str] = None
    is_customer: Optional[bool] = None
    is_supplier: Optional[bool] = None
    is_active: Optional[bool] = None

class EntityAddressInput(BaseModel):
    entity_id: Optional[str] = None
    address_label: str
    street: Optional[str] = None
    number: Optional[str] = None
    zip_code: Optional[str] = None
    city_neighborhood: Optional[str] = None
    full_address: Optional[str] = None
    is_default: Optional[bool] = False

class EntityAddressUpdate(BaseModel):
    address_label: Optional[str] = None
    street: Optional[str] = None
    number: Optional[str] = None
    zip_code: Optional[str] = None
    city_neighborhood: Optional[str] = None
    full_address: Optional[str] = None
    is_default: Optional[bool] = None

class OrderLineInput(BaseModel): sku: str; quantity: float
class OrderCreateInput(BaseModel): document_number: str; document_type: str = "PICKING"; channel_origin: str = "EXTERNAL_API"; customer_tax_id: str; address_label: str; lines: List[OrderLineInput]

class SalesOrderLineInput(BaseModel):
    sku: str
    quantity: float

class SalesOrderCreateInput(BaseModel):
    document_number: Optional[str] = None
    customer_tax_id: Optional[str] = None
    customer_cuit: Optional[str] = None
    cuit_dni: Optional[str] = None
    customer_name: Optional[str] = None
    address_label: Optional[str] = "Principal"
    lines: List[SalesOrderLineInput]

class SettingsUpdate(BaseModel): app_name: Optional[str] = None; primary_color: Optional[str] = None; company_cuit: Optional[str] = None; zebra_ip: Optional[str] = None; allow_multiproduct_locations: Optional[str] = None; require_mobile_reception: Optional[str] = None; enable_item_dimensions: Optional[str] = None; zpl_item_width: Optional[str] = None; zpl_item_height: Optional[str] = None; zpl_item_template: Optional[str] = None; zpl_order_width: Optional[str] = None; zpl_order_height: Optional[str] = None; zpl_order_template: Optional[str] = None; tracker360_api_key: Optional[str] = None
class IntegrationChannelCreate(BaseModel): name: str; channel_type: str; target_url: str; api_key: Optional[str] = None

class BatchItemPrintLine(BaseModel):
    sku: str
    quantity: int

class BatchItemPrintInput(BaseModel):
    queue_code: str
    items: List[BatchItemPrintLine]

class BranchCreate(BaseModel): code: str; name: str
class BranchUpdate(BaseModel): code: Optional[str] = None; name: Optional[str] = None; is_active: Optional[bool] = None
class SectorCreate(BaseModel): name: str; print_queue_code: str; uses_locations: bool; branch_id: Optional[str] = None
class SectorUpdate(BaseModel): name: Optional[str] = None; print_queue_code: Optional[str] = None; uses_locations: Optional[bool] = None; branch_id: Optional[str] = None; is_active: Optional[bool] = None
class LocationCreate(BaseModel): sector_id: str; location_code: str; description: Optional[str] = None
class LocationUpdate(BaseModel): location_code: Optional[str] = None; description: Optional[str] = None; sector_id: Optional[str] = None; is_active: Optional[bool] = None
class POLineInput(BaseModel): sku: str; quantity_ordered: float
class POCreateInput(BaseModel): order_number: str; supplier_id: str; lines: List[POLineInput]
class RemitoLineInput(BaseModel): sku: str; quantity_sent: float; location_code: Optional[str] = None
class RemitoCreateInput(BaseModel): remito_number: str; supplier_id: str; branch_id: str; sector_id: str; purchase_order_id: Optional[str] = None; lines: List[RemitoLineInput]
class InvoiceManualLineInput(BaseModel): sku: str; quantity: float; unit_price: float
class InvoiceCreateInput(BaseModel): invoice_number: str; supplier_id: str; invoice_type: str = "A"; branch_id: Optional[str] = None; sector_id: Optional[str] = None; manual_items: List[InvoiceManualLineInput] = []; remito_ids: List[str] = []; po_ids: List[str] = []
class TransferLineInput(BaseModel): sku: str; quantity_sent: float; origin_location_code: Optional[str] = None; destination_location_code: Optional[str] = None
class TransferCreateInput(BaseModel): transfer_number: str; origin_branch_id: str; origin_sector_id: str; destination_branch_id: str; destination_sector_id: str; lines: List[TransferLineInput]

class ItemUpdate(BaseModel):
    description: Optional[str] = None
    category: Optional[str] = None
    length: Optional[float] = 0.0
    width: Optional[float] = 0.0
    height: Optional[float] = 0.0
    weight: Optional[float] = 0.0
    volume: Optional[float] = 0.0

class MobileRelocateInput(BaseModel): sku: str; origin_location_code: str; destination_location_code: str; quantity: float
class MobileRemitoScanInput(BaseModel): remito_number: str; sku: str; quantity: float; location_code: Optional[str] = None
class MobileTransferScanInput(BaseModel): transfer_number: str; sku: str; quantity: float; destination_location_code: Optional[str] = None
class PickScanInput(BaseModel): sku: str; quantity: float; location_code: str
class ItemLocationInput(BaseModel): sku: str; location_code: str
class PackOrderInput(BaseModel): boxes: int

# === AUTENTICACIÓN Y VALIDACIÓN ===
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

@app.post("/api/auth/login")
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

@app.post("/api/auth/logout")
async def logout(response: Response): response.delete_cookie("access_token"); return {"message": "Éxito"}

# === GESTIÓN DE USUARIOS ===
@app.get("/api/admin/users")
async def list_users(admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    return [dict(u) for u in await conn.fetch("SELECT u.id, u.username, u.full_name, u.role, u.is_active, u.email, u.branch_id, u.sector_id, b.name as branch_name, s.name as sector_name FROM users u LEFT JOIN branches b ON u.branch_id = b.id::text LEFT JOIN sectors s ON u.sector_id = s.id::text ORDER BY u.created_at DESC")]

@app.post("/api/admin/users")
async def create_or_update_user(data: UserCreate, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    await conn.execute("INSERT INTO users (username, full_name, password_hash, role, email, branch_id, sector_id) VALUES ($1, $2, $3, $4, $5, $6, $7) ON CONFLICT (username) DO UPDATE SET full_name = EXCLUDED.full_name, password_hash = EXCLUDED.password_hash, role = EXCLUDED.role, email = EXCLUDED.email, branch_id = EXCLUDED.branch_id, sector_id = EXCLUDED.sector_id, is_active = TRUE", data.username.strip().lower(), data.full_name.strip(), get_password_hash(data.password), data.role, data.email, data.branch_id, data.sector_id)
    return {"status": "success"}

@app.put("/api/admin/users/{username}")
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

# === AGENTE DE IMPRESIÓN REST / API ===
@app.get("/api/print-agent/queues")
async def list_print_queues(conn: asyncpg.Connection = Depends(get_db_connection)):
    rows = await conn.fetch("""
        SELECT s.print_queue_code as queue_code, s.name as sector_name, b.name as branch_name 
        FROM sectors s 
        LEFT JOIN branches b ON s.branch_id = b.id 
        ORDER BY b.name, s.name ASC
    """)
    return [dict(r) for r in rows]

@app.get("/api/print-agent/jobs")
async def get_print_jobs_for_agent(queue_code: str, authenticated: bool = Depends(verify_system_api_key), conn: asyncpg.Connection = Depends(get_db_connection)):
    rows = await conn.fetch("""
        SELECT id::text as id, zpl_content, created_at 
        FROM print_jobs 
        WHERE UPPER(queue_code) = $1 AND status = 'PENDING' 
        ORDER BY created_at ASC LIMIT 10
    """, queue_code.strip().upper())
    return [dict(r) for r in rows]

@app.post("/api/print-agent/jobs/{job_id}/ack")
async def acknowledge_print_job(job_id: str, authenticated: bool = Depends(verify_system_api_key), conn: asyncpg.Connection = Depends(get_db_connection)):
    await conn.execute("UPDATE print_jobs SET status = 'PRINTED' WHERE id = $1", uuid.UUID(job_id))
    return {"status": "success"}

# === GESTIÓN DE ETIQUETAS / ZPL DESDE FRONTEND ===
@app.post("/api/admin/sales-orders/{document_number}/print-label")
async def print_order_label_again(document_number: str, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    doc = await conn.fetchrow("""
        SELECT d.id, d.document_number, d.status, COALESCE(c.company_name, 'Consumidor Final') as client_name, COALESCE(a.full_address, 'A coordinar') as delivery_address
        FROM documents d
        LEFT JOIN entities c ON d.customer_id = c.id
        LEFT JOIN entity_addresses a ON d.customer_address_id = a.id
        WHERE UPPER(d.document_number) = $1
    """, document_number.strip().upper())
    
    if not doc: raise HTTPException(404, "Pedido no encontrado.")
    if doc["status"] not in ("COMPLETED", "DISPATCHED"):
        raise HTTPException(400, "El pedido aún no está totalmente preparado.")

    template = await conn.fetchval("SELECT value FROM system_settings WHERE key = 'zpl_order_template'")
    if not template:
        template = "^XA^FO50,50^A0N,40,40^FDPEDIDO: {order_number}^FS^FO50,110^A0N,30,30^FDCLIENTE: {client_name}^FS^FO50,170^A0N,25,25^FDDIR: {delivery_address}^FS^FO50,230^BY3^BCN,100,Y,N,N^FD{order_number}^FS^XZ"

    zpl = template.replace("{order_number}", doc["document_number"])\
                  .replace("{client_name}", doc["client_name"])\
                  .replace("{delivery_address}", doc["delivery_address"])

    default_queue = await conn.fetchval("SELECT print_queue_code FROM sectors WHERE uses_locations = FALSE LIMIT 1") or "PRINT-SEC-01"
    await queue_zpl_print_job(conn, default_queue, zpl)
    return {"status": "success", "message": f"Etiqueta enviada a la cola {default_queue}."}

@app.post("/api/admin/items/batch-print-labels")
async def batch_print_item_labels(data: BatchItemPrintInput, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    if not data.items or len(data.items) == 0:
        raise HTTPException(400, "Debe agregar al menos un artículo para imprimir.")

    template = await conn.fetchval("SELECT value FROM system_settings WHERE key = 'zpl_item_template'")
    if not template:
        template = "^XA^FO50,30^A0N,30,30^FD{description}^FS^FO50,70^A0N,25,25^FDSKU: {sku}^FS^FO50,110^BY2^BCN,80,Y,N,N^FD{sku}^FS^XZ"

    total_queued = 0
    async with conn.transaction():
        for line in data.items:
            sku_clean = line.sku.strip().upper()
            qty = max(1, line.quantity)
            item = await conn.fetchrow("SELECT sku, description FROM items WHERE UPPER(sku) = $1", sku_clean)
            desc = item["description"] if item else f"Art. {sku_clean}"

            zpl = template.replace("{sku}", sku_clean).replace("{description}", desc)
            for _ in range(qty):
                await queue_zpl_print_job(conn, data.queue_code, zpl)
                total_queued += 1

    return {"status": "success", "message": f"Se enviaron {total_queued} etiquetas a la cola {data.queue_code}."}

@app.post("/api/admin/purchase-remitos/{remito_id}/print-labels")
async def print_remito_item_labels(remito_id: str, queue_code: str = "PRINT-SEC-01", admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    lines = await conn.fetch("""
        SELECT prl.sku, prl.quantity_received, COALESCE(i.description, prl.sku) as description
        FROM purchase_remito_lines prl
        LEFT JOIN items i ON UPPER(prl.sku) = UPPER(i.sku)
        WHERE prl.purchase_remito_id = $1 AND prl.quantity_received > 0
    """, uuid.UUID(remito_id))

    if not lines or len(lines) == 0:
        raise HTTPException(400, "El remito no tiene ítems controlados/recibidos.")

    template = await conn.fetchval("SELECT value FROM system_settings WHERE key = 'zpl_item_template'")
    if not template:
        template = "^XA^FO50,30^A0N,30,30^FD{description}^FS^FO50,70^A0N,25,25^FDSKU: {sku}^FS^FO50,110^BY2^BCN,80,Y,N,N^FD{sku}^FS^XZ"

    total_queued = 0
    async with conn.transaction():
        for line in lines:
            sku_clean = line["sku"].strip().upper()
            qty = int(line["quantity_received"])
            zpl = template.replace("{sku}", sku_clean).replace("{description}", line["description"])
            for _ in range(qty):
                await queue_zpl_print_job(conn, queue_code, zpl)
                total_queued += 1

    return {"status": "success", "message": f"Enviadas {total_queued} etiquetas de artículos a la cola {queue_code}."}

# === OTROS MÓDULOS DE ADMINISTRACIÓN ===
@app.get("/api/admin/branches")
async def list_branches(admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)): return [dict(r) for r in await conn.fetch("SELECT id, code, name, is_active FROM branches ORDER BY name ASC")]
@app.post("/api/admin/branches")
async def create_branch(data: BranchCreate, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    await conn.execute("INSERT INTO branches (code, name) VALUES ($1, $2)", data.code.strip().upper(), data.name.strip()); return {"status": "success"}

@app.get("/api/admin/sectors")
async def list_sectors(admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)): return [dict(r) for r in await conn.fetch("SELECT s.id, s.name, s.print_queue_code, s.uses_locations, s.branch_id, b.name as branch_name FROM sectors s LEFT JOIN branches b ON s.branch_id = b.id ORDER BY s.name ASC")]
@app.post("/api/admin/sectors")
async def create_sector(data: SectorCreate, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    await conn.execute("INSERT INTO sectors (name, print_queue_code, uses_locations, branch_id) VALUES ($1, $2, $3, $4)", data.name.strip(), data.print_queue_code.strip().upper(), data.uses_locations, uuid.UUID(data.branch_id)); return {"status": "success"}

@app.get("/api/admin/locations")
async def list_all_locations(admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)): return [dict(l) for l in await conn.fetch("SELECT l.id, l.location_code, l.description, s.name as sector_name, b.name as branch_name FROM locations l JOIN sectors s ON l.sector_id = s.id LEFT JOIN branches b ON s.branch_id = b.id ORDER BY l.location_code ASC")]
@app.post("/api/admin/locations")
async def create_location_direct(data: LocationCreate, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    await conn.execute("INSERT INTO locations (sector_id, location_code, description) VALUES ($1, $2, $3)", uuid.UUID(data.sector_id), data.location_code.strip().upper(), data.description.strip()); return {"status": "success"}

@app.get("/api/admin/items")
async def list_items(sku: str = "", description: str = "", page: int = 1, limit: int = 50, sort_by: str = "sku", sort_order: str = "ASC", admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    offset = (page - 1) * limit
    allowed_cols = {"sku": "i.sku", "description": "i.description", "category": "i.category"}
    col = allowed_cols.get(sort_by, "i.sku")
    order = "DESC" if sort_order.upper() == "DESC" else "ASC"
    
    total_count = await conn.fetchval("SELECT COUNT(*) FROM items WHERE sku ILIKE $1 AND description ILIKE $2", f"%{sku}%", f"%{description}%")
    q = f"""
        SELECT i.sku, i.description, i.category, i.length, i.width, i.height, i.weight, i.volume, 
               COALESCE((SELECT string_agg(l.location_code, ', ') FROM item_locations il JOIN locations l ON il.location_id = l.id WHERE il.item_sku = i.sku), 'Sin asignación') as locations_summary 
        FROM items i 
        WHERE i.sku ILIKE $1 AND i.description ILIKE $2 
        ORDER BY {col} {order} 
        LIMIT $3 OFFSET $4
    """
    rows = await conn.fetch(q, f"%{sku}%", f"%{description}%", limit, offset)
    total_pages = (total_count + limit - 1) // limit if total_count > 0 else 1
    return {
        "items": [dict(r) for r in rows],
        "total_count": total_count,
        "page": page,
        "limit": limit,
        "total_pages": total_pages
    }

@app.put("/api/admin/items/{sku}")
async def update_item(sku: str, data: ItemUpdate, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    calculated_vol = (data.length * data.width * data.height) / 1000000.0 if (data.length and data.width and data.height) else (data.volume or 0.0)
    await conn.execute("""
        UPDATE items 
        SET description = $1, category = $2, length = COALESCE($3, length), width = COALESCE($4, width), height = COALESCE($5, height), weight = COALESCE($6, weight), volume = $7 
        WHERE UPPER(sku) = $8
    """, data.description, data.category, data.length, data.width, data.height, data.weight, calculated_vol, sku.upper())
    return {"status": "success", "message": "Ficha de artículo actualizada."}

@app.get("/api/admin/stock")
async def list_admin_stock(admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    rows = await conn.fetch("""
        SELECT 
            si.sku, 
            i.description, 
            b.name as branch_name, 
            sec.name as sector_name, 
            COALESCE(l.location_code, 'Sin ubicación') as location_code, 
            si.quantity::float as quantity, 
            si.updated_at
        FROM stock_inventory si
        LEFT JOIN items i ON si.sku = i.sku
        LEFT JOIN branches b ON si.branch_id = b.id
        LEFT JOIN sectors sec ON si.sector_id = sec.id
        LEFT JOIN locations l ON si.location_id = l.id
        ORDER BY si.sku ASC
    """)
    return [dict(r) for r in rows]

@app.get("/api/admin/stock/kardex")
async def list_admin_stock_kardex(admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    rows = await conn.fetch("""
        SELECT 
            sm.id::text as id, 
            sm.sku, 
            sm.movement_type, 
            sm.quantity::float as quantity, 
            sm.reference_document, 
            sm.username, 
            sm.created_at,
            b.name as branch_name, 
            sec.name as sector_name, 
            l.location_code
        FROM stock_movements sm
        LEFT JOIN branches b ON sm.branch_id = b.id
        LEFT JOIN sectors sec ON sm.sector_id = sec.id
        LEFT JOIN locations l ON sm.location_id = l.id
        ORDER BY sm.created_at DESC LIMIT 100
    """)
    return [dict(r) for r in rows]

@app.get("/api/admin/purchase-orders")
async def list_admin_purchase_orders(search: str = "", limit: int = 50, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    rows = await conn.fetch("""
        SELECT 
            po.id::text as id, 
            po.order_number, 
            po.status, 
            po.created_at, 
            COALESCE(e.company_name, 'Sin Proveedor') as supplier_name
        FROM purchase_orders po
        LEFT JOIN entities e ON po.supplier_id = e.id
        WHERE po.order_number ILIKE $1
        ORDER BY po.created_at DESC LIMIT $2
    """, f"%{search}%", limit)
    return [dict(r) for r in rows]

@app.get("/api/admin/purchase-remitos")
async def list_admin_purchase_remitos(search: str = "", limit: int = 50, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    rows = await conn.fetch("""
        SELECT 
            pr.id::text as id, 
            pr.remito_number, 
            pr.status, 
            pr.created_at, 
            COALESCE(e.company_name, 'Sin Proveedor') as supplier_name,
            b.name as branch_name,
            sec.name as sector_name
        FROM purchase_remitos pr
        LEFT JOIN entities e ON pr.supplier_id = e.id
        LEFT JOIN branches b ON pr.branch_id = b.id
        LEFT JOIN sectors sec ON pr.sector_id = sec.id
        WHERE pr.remito_number ILIKE $1
        ORDER BY pr.created_at DESC LIMIT $2
    """, f"%{search}%", limit)
    return [dict(r) for r in rows]

@app.get("/api/admin/purchase-invoices")
async def list_admin_purchase_invoices(search: str = "", limit: int = 50, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    rows = await conn.fetch("""
        SELECT 
            pi.id::text as id, 
            pi.invoice_number, 
            pi.invoice_type, 
            pi.created_at, 
            COALESCE(e.company_name, 'Sin Proveedor') as supplier_name
        FROM purchase_invoices pi
        LEFT JOIN entities e ON pi.supplier_id = e.id
        WHERE pi.invoice_number ILIKE $1
        ORDER BY pi.created_at DESC LIMIT $2
    """, f"%{search}%", limit)
    return [dict(r) for r in rows]

@app.get("/api/admin/transfer-orders")
async def list_admin_transfer_orders(search: str = "", limit: int = 50, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    rows = await conn.fetch("""
        SELECT 
            t.id::text as id, 
            t.transfer_number, 
            t.status, 
            t.created_at, 
            COALESCE(ob.name, 'N/A') as origin_branch, 
            COALESCE(db.name, 'N/A') as destination_branch,
            COALESCE(os.name, 'N/A') as origin_sector,
            COALESCE(ds.name, 'N/A') as destination_sector
        FROM transfer_orders t
        LEFT JOIN branches ob ON t.origin_branch_id = ob.id
        LEFT JOIN branches db ON t.destination_branch_id = db.id
        LEFT JOIN sectors os ON t.origin_sector_id = os.id
        LEFT JOIN sectors ds ON t.destination_sector_id = ds.id
        WHERE t.transfer_number ILIKE $1
        ORDER BY t.created_at DESC LIMIT $2
    """, f"%{search}%", limit)
    return [dict(r) for r in rows]

# === OPERATIVA MÓVIL, PICKING, PACKING, RECEPCIÓN Y TRASPASOS ===
@app.get("/api/picking/orders")
async def get_picking_mailbox(user: dict = Depends(get_current_user), conn: asyncpg.Connection = Depends(get_db_connection)):
    return [dict(r) for r in await conn.fetch("SELECT d.document_number, d.status, COALESCE(c.company_name, 'Sin cliente') as company_name FROM documents d LEFT JOIN entities c ON d.customer_id = c.id WHERE d.status IN ('PENDING', 'IN_PROGRESS') ORDER BY d.created_at ASC")]

@app.get("/api/picking/orders/{document_number}")
async def get_picking_order_details(document_number: str, user: dict = Depends(get_current_user), conn: asyncpg.Connection = Depends(get_db_connection)):
    doc = await conn.fetchrow("SELECT id, document_number, status FROM documents WHERE document_number = $1", document_number.strip().upper())
    if not doc: raise HTTPException(404, "Pedido no encontrado")
    lines = await conn.fetch("SELECT dl.id, dl.sku, dl.quantity_requested, dl.quantity_picked, COALESCE((SELECT string_agg(l.location_code || ' (' || si.quantity || ')', ' | ') FROM stock_inventory si JOIN locations l ON si.location_id = l.id WHERE si.sku = dl.sku AND si.quantity > 0), 'Sin stock') as suggested_locations FROM document_lines dl WHERE dl.document_id = $1 ORDER BY dl.sku ASC", doc["id"])
    return {"document": dict(doc), "lines": [dict(l) for l in lines]}

@app.post("/api/picking/orders/{document_number}/scan")
async def scan_picking_item(document_number: str, data: PickScanInput, user: dict = Depends(get_current_user), conn: asyncpg.Connection = Depends(get_db_connection)):
    async with conn.transaction():
        doc = await conn.fetchrow("SELECT id, status FROM documents WHERE document_number = $1 FOR UPDATE", document_number.strip().upper())
        if doc["status"] == "COMPLETED": raise HTTPException(400, "Ya completado.")
        sku_clean = data.sku.strip().upper()
        line = await conn.fetchrow("SELECT id, quantity_requested, quantity_picked FROM document_lines WHERE document_id = $1 AND sku = $2", doc["id"], sku_clean)
        if not line: raise HTTPException(400, "SKU no pertenece al pedido.")
        loc = await conn.fetchrow("SELECT l.id, l.sector_id, s.branch_id FROM locations l JOIN sectors s ON l.sector_id = s.id WHERE l.location_code = $1", data.location_code.strip().upper())
        avail = await conn.fetchval("SELECT COALESCE(SUM(quantity), 0) FROM stock_inventory WHERE branch_id = $1 AND sector_id = $2 AND sku = $3 AND location_id = $4", loc["branch_id"], loc["sector_id"], sku_clean, loc["id"])
        if avail < data.quantity: raise HTTPException(400, "Stock insuficiente en ubicación.")
        await conn.execute("UPDATE document_lines SET quantity_picked = quantity_picked + $1 WHERE id = $2", data.quantity, line["id"])
        await record_stock_movement(conn, sku_clean, loc["branch_id"], loc["sector_id"], loc["id"], -data.quantity, 'OUT_PICKING', document_number.strip().upper(), user.get("username"))
        await conn.execute("UPDATE documents SET status = 'IN_PROGRESS' WHERE id = $1 AND status = 'PENDING'", doc["id"])
        pending = await conn.fetchval("SELECT COUNT(*) FROM document_lines WHERE document_id = $1 AND quantity_picked < quantity_requested", doc["id"])
        if pending == 0: await conn.execute("UPDATE documents SET status = 'COMPLETED' WHERE id = $1", doc["id"])
        return {"status": "success", "message": f"Extraído {data.quantity} un de {sku_clean}", "order_completed": pending == 0}

@app.get("/api/packing/orders")
async def get_packing_orders(user: dict = Depends(get_current_user), conn: asyncpg.Connection = Depends(get_db_connection)):
    return [dict(r) for r in await conn.fetch("SELECT d.document_number, d.status, COALESCE(c.company_name, 'Sin Cliente') as company_name FROM documents d LEFT JOIN entities c ON d.customer_id = c.id WHERE d.status = 'COMPLETED' ORDER BY d.created_at ASC")]

@app.get("/api/packing/orders/{document_number}")
async def get_packing_order_details(document_number: str, user: dict = Depends(get_current_user), conn: asyncpg.Connection = Depends(get_db_connection)):
    doc = await conn.fetchrow("SELECT d.id, d.document_number, COALESCE(c.company_name, 'Sin cliente') as company_name, COALESCE(a.full_address, 'Sin dirección') as address FROM documents d LEFT JOIN entities c ON d.customer_id = c.id LEFT JOIN entity_addresses a ON d.customer_address_id = a.id WHERE d.document_number = $1", document_number.strip().upper())
    if not doc: raise HTTPException(404, "Pedido no encontrado")
    
    totals = await conn.fetchrow("""
        SELECT COALESCE(SUM(dl.quantity_requested * COALESCE(i.weight, 0)), 0) as calc_weight, 
               COALESCE(SUM(dl.quantity_requested * COALESCE(i.volume, 0)), 0) as calc_volume 
        FROM document_lines dl 
        JOIN items i ON dl.sku = i.sku 
        WHERE dl.document_id = $1
    """, doc["id"])
    return {"document": dict(doc), "totals": dict(totals)}

@app.post("/api/packing/orders/{document_number}/pack")
async def pack_order_and_dispatch(document_number: str, data: PackOrderInput, user: dict = Depends(get_current_user), conn: asyncpg.Connection = Depends(get_db_connection)):
    async with conn.transaction():
        doc = await conn.fetchrow("SELECT d.id, d.status, d.document_number, d.channel_origin, COALESCE(c.company_name, 'Consumidor Final') as client_name, COALESCE(a.full_address, 'A coordinar') as delivery_address FROM documents d LEFT JOIN entities c ON d.customer_id = c.id LEFT JOIN entity_addresses a ON d.customer_address_id = a.id WHERE d.document_number = $1 FOR UPDATE", document_number.strip().upper())
        if not doc: raise HTTPException(404, "Pedido no encontrado.")
        if doc["status"] == "DISPATCHED": raise HTTPException(400, "El pedido ya fue despachado.")
        if doc["status"] != "COMPLETED": raise HTTPException(400, "El pedido aún no está pickeado completamente.")

        totals = await conn.fetchrow("""
            SELECT COALESCE(SUM(dl.quantity_requested * COALESCE(i.weight, 0)), 0) as calc_weight, 
                   COALESCE(SUM(dl.quantity_requested * COALESCE(i.volume, 0)), 0) as calc_volume 
            FROM document_lines dl 
            JOIN items i ON dl.sku = i.sku 
            WHERE dl.document_id = $1
        """, doc["id"])

        await conn.execute("UPDATE documents SET status = 'DISPATCHED' WHERE id = $1", doc["id"])
        await log_action(conn, user.get("username"), "PACKING_DISPATCH", f"Empacó y despachó {document_number} ({data.boxes} bultos)")

        template = await conn.fetchval("SELECT value FROM system_settings WHERE key = 'zpl_order_template'")
        if template:
            zpl = template.replace("{order_number}", doc["document_number"])\
                          .replace("{client_name}", doc["client_name"])\
                          .replace("{delivery_address}", doc["delivery_address"])
            default_queue = await conn.fetchval("SELECT print_queue_code FROM sectors WHERE uses_locations = FALSE LIMIT 1") or "PRINT-SEC-01"
            await queue_zpl_print_job(conn, default_queue, zpl)

        dispatch_payload = {
            "event": "order.dispatched",
            "order_number": doc["document_number"],
            "channel_origin": doc["channel_origin"],
            "client_name": doc["client_name"],
            "delivery_address": doc["delivery_address"],
            "boxes": data.boxes,
            "total_weight_kg": float(totals["calc_weight"]),
            "total_volume_m3": float(totals["calc_volume"]),
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        await dispatch_event_to_channels(conn, "OUTBOUND_DESPACHO", dispatch_payload)
        return {"status": "success", "message": "Pedido despachado e impreso exitosamente."}

# === RECEPCIÓN (PREPARADOR) ===
@app.get("/api/reception/remitos")
@app.get("/api/reception/orders")
async def get_reception_remitos(user: dict = Depends(get_current_user), conn: asyncpg.Connection = Depends(get_db_connection)):
    rows = await conn.fetch("""
        SELECT 
            pr.id::text as id, 
            pr.remito_number, 
            pr.status, 
            pr.created_at, 
            COALESCE(e.company_name, 'Sin Proveedor') as supplier_name,
            COALESCE(b.name, 'Sucursal') as branch_name,
            COALESCE(sec.name, 'Sector') as sector_name
        FROM purchase_remitos pr
        LEFT JOIN entities e ON pr.supplier_id = e.id
        LEFT JOIN branches b ON pr.branch_id = b.id
        LEFT JOIN sectors sec ON pr.sector_id = sec.id
        WHERE pr.status IN ('PENDING', 'PENDING_CONTROL', 'IN_PROGRESS')
        ORDER BY pr.created_at ASC
    """)
    return [dict(r) for r in rows]

@app.get("/api/reception/remitos/{remito_number}")
@app.get("/api/reception/orders/{remito_number}")
async def get_reception_remito_details(remito_number: str, user: dict = Depends(get_current_user), conn: asyncpg.Connection = Depends(get_db_connection)):
    rem = await conn.fetchrow("""
        SELECT pr.id, pr.remito_number, pr.status, pr.branch_id, pr.sector_id, COALESCE(e.company_name, 'Sin Proveedor') as supplier_name 
        FROM purchase_remitos pr 
        LEFT JOIN entities e ON pr.supplier_id = e.id 
        WHERE UPPER(pr.remito_number) = $1
    """, remito_number.strip().upper())
    if not rem: raise HTTPException(404, "Remito no encontrado")
    lines = await conn.fetch("""
        SELECT prl.id::text as id, prl.sku, prl.quantity_sent::float as quantity_sent, prl.quantity_received::float as quantity_received, l.location_code
        FROM purchase_remito_lines prl
        LEFT JOIN locations l ON prl.location_id = l.id
        WHERE prl.purchase_remito_id = $1
        ORDER BY prl.sku ASC
    """, rem["id"])
    return {"remito": dict(rem), "lines": [dict(l) for l in lines]}

@app.post("/api/reception/remitos/{remito_number}/scan")
@app.post("/api/reception/orders/{remito_number}/scan")
async def scan_reception_item(remito_number: str, data: MobileRemitoScanInput, user: dict = Depends(get_current_user), conn: asyncpg.Connection = Depends(get_db_connection)):
    async with conn.transaction():
        rem = await conn.fetchrow("SELECT id, status, branch_id, sector_id FROM purchase_remitos WHERE UPPER(remito_number) = $1 FOR UPDATE", remito_number.strip().upper())
        if not rem: raise HTTPException(404, "Remito no encontrado")
        if rem["status"] == "COMPLETED": raise HTTPException(400, "Remito ya controlado completamente.")
        sku_clean = data.sku.strip().upper()
        line = await conn.fetchrow("SELECT id, quantity_sent, quantity_received FROM purchase_remito_lines WHERE purchase_remito_id = $1 AND UPPER(sku) = $2", rem["id"], sku_clean)
        if not line: raise HTTPException(400, "SKU no pertenece al remito.")
        
        loc_id = None
        if data.location_code and data.location_code.strip():
            loc = await conn.fetchrow("SELECT id FROM locations WHERE UPPER(location_code) = $1", data.location_code.strip().upper())
            if loc: loc_id = loc["id"]

        await conn.execute("UPDATE purchase_remito_lines SET quantity_received = quantity_received + $1 WHERE id = $2", data.quantity, line["id"])
        await record_stock_movement(conn, sku_clean, rem["branch_id"], rem["sector_id"], loc_id, data.quantity, 'IN_RECEPTION', remito_number.strip().upper(), user.get("username"))
        await conn.execute("UPDATE purchase_remitos SET status = 'IN_PROGRESS' WHERE id = $1 AND status IN ('PENDING', 'PENDING_CONTROL')", rem["id"])
        
        pending = await conn.fetchval("SELECT COUNT(*) FROM purchase_remito_lines WHERE purchase_remito_id = $1 AND quantity_received < quantity_sent", rem["id"])
        if pending == 0:
            await conn.execute("UPDATE purchase_remitos SET status = 'COMPLETED' WHERE id = $1", rem["id"])
        
        return {"status": "success", "message": f"Ingresado {data.quantity} un de {sku_clean}", "remito_completed": pending == 0}

# === TRASPASOS (PREPARADOR) ===
@app.get("/api/transfers/orders")
@app.get("/api/transfers/pending")
async def get_transfer_orders(user: dict = Depends(get_current_user), conn: asyncpg.Connection = Depends(get_db_connection)):
    rows = await conn.fetch("""
        SELECT 
            t.id::text as id, 
            t.transfer_number, 
            t.status, 
            t.created_at, 
            COALESCE(ob.name, 'Origen') as origin_branch, 
            COALESCE(db.name, 'Destino') as destination_branch,
            COALESCE(os.name, 'Sector Origen') as origin_sector,
            COALESCE(ds.name, 'Sector Destino') as destination_sector
        FROM transfer_orders t
        LEFT JOIN branches ob ON t.origin_branch_id = ob.id
        LEFT JOIN branches db ON t.destination_branch_id = db.id
        LEFT JOIN sectors os ON t.origin_sector_id = os.id
        LEFT JOIN sectors ds ON t.destination_sector_id = ds.id
        WHERE t.status IN ('PENDING', 'PENDING_CONTROL', 'IN_PROGRESS')
        ORDER BY t.created_at ASC
    """)
    return [dict(r) for r in rows]

@app.get("/api/transfers/orders/{transfer_number}")
async def get_transfer_order_details(transfer_number: str, user: dict = Depends(get_current_user), conn: asyncpg.Connection = Depends(get_db_connection)):
    tr = await conn.fetchrow("""
        SELECT t.id, t.transfer_number, t.status, t.origin_branch_id, t.origin_sector_id, t.destination_branch_id, t.destination_sector_id,
               COALESCE(ob.name, 'Origen') as origin_branch, COALESCE(db.name, 'Destino') as destination_branch
        FROM transfer_orders t
        LEFT JOIN branches ob ON t.origin_branch_id = ob.id
        LEFT JOIN branches db ON t.destination_branch_id = db.id
        WHERE UPPER(t.transfer_number) = $1
    """, transfer_number.strip().upper())
    if not tr: raise HTTPException(404, "Traspaso no encontrado")
    lines = await conn.fetch("""
        SELECT tol.id::text as id, tol.sku, tol.quantity_sent::float as quantity_sent, tol.quantity_received::float as quantity_received,
               ol.location_code as origin_location, dl.location_code as destination_location
        FROM transfer_order_lines tol
        LEFT JOIN locations ol ON tol.origin_location_id = ol.id
        LEFT JOIN locations dl ON tol.destination_location_id = dl.id
        WHERE tol.transfer_order_id = $1
        ORDER BY tol.sku ASC
    """, tr["id"])
    return {"transfer": dict(tr), "lines": [dict(l) for l in lines]}

@app.post("/api/transfers/orders/{transfer_number}/scan")
async def scan_transfer_item(transfer_number: str, data: MobileTransferScanInput, user: dict = Depends(get_current_user), conn: asyncpg.Connection = Depends(get_db_connection)):
    async with conn.transaction():
        tr = await conn.fetchrow("SELECT id, status, origin_branch_id, origin_sector_id, destination_branch_id, destination_sector_id FROM transfer_orders WHERE UPPER(transfer_number) = $1 FOR UPDATE", transfer_number.strip().upper())
        if not tr: raise HTTPException(404, "Traspaso no encontrado")
        if tr["status"] == "COMPLETED": raise HTTPException(400, "Traspaso ya completado.")
        sku_clean = data.sku.strip().upper()
        line = await conn.fetchrow("SELECT id, quantity_sent, quantity_received, origin_location_id FROM transfer_order_lines WHERE transfer_order_id = $1 AND UPPER(sku) = $2", tr["id"], sku_clean)
        if not line: raise HTTPException(400, "SKU no pertenece al traspaso.")

        dest_loc_id = None
        if data.destination_location_code and data.destination_location_code.strip():
            loc = await conn.fetchrow("SELECT id FROM locations WHERE UPPER(location_code) = $1", data.destination_location_code.strip().upper())
            if loc: dest_loc_id = loc["id"]

        await conn.execute("UPDATE transfer_order_lines SET quantity_received = quantity_received + $1 WHERE id = $2", data.quantity, line["id"])
        await record_stock_movement(conn, sku_clean, tr["origin_branch_id"], tr["origin_sector_id"], line["origin_location_id"], -data.quantity, 'TRANSFER_OUT', transfer_number.strip().upper(), user.get("username"))
        await record_stock_movement(conn, sku_clean, tr["destination_branch_id"], tr["destination_sector_id"], dest_loc_id, data.quantity, 'TRANSFER_IN', transfer_number.strip().upper(), user.get("username"))

        await conn.execute("UPDATE transfer_orders SET status = 'IN_PROGRESS' WHERE id = $1 AND status IN ('PENDING', 'PENDING_CONTROL')", tr["id"])
        pending = await conn.fetchval("SELECT COUNT(*) FROM transfer_order_lines WHERE transfer_order_id = $1 AND quantity_received < quantity_sent", tr["id"])
        if pending == 0:
            await conn.execute("UPDATE transfer_orders SET status = 'COMPLETED' WHERE id = $1", tr["id"])

        return {"status": "success", "message": f"Transferido {data.quantity} un de {sku_clean}", "transfer_completed": pending == 0}

# === AUDITORÍA ===
@app.get("/api/admin/logs")
async def list_logs(admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)): return [dict(l) for l in await conn.fetch("SELECT username, action, details, created_at FROM audit_logs ORDER BY created_at DESC LIMIT 100")]

# === ARCHIVOS ESTÁTICOS AL FINAL ABSOLUTO ===
os.makedirs("downloads", exist_ok=True)
os.makedirs("frontend", exist_ok=True)
app.mount("/downloads", StaticFiles(directory="downloads"), name="downloads")
app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")