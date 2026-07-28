import os, asyncio, csv, uuid, secrets, json, urllib.request
from io import StringIO
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import jwt, asyncpg
from fastapi import FastAPI, Depends, HTTPException, status, Response, Request, UploadFile, File, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from passlib.context import CryptContext
from typing import List, Optional

# === SEGURIDAD Y CONFIGURACIÓN ===
SECRET_KEY = os.getenv("SECRET_KEY", secrets.token_hex(32))
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 480
MAX_FILE_SIZE = 2 * 1024 * 1024

pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")
def verify_password(p, h): return pwd_context.verify(p, h)
def get_password_hash(p): return pwd_context.hash(p)

def create_access_token(data: dict):
    to_encode = data.copy()
    to_encode.update({"exp": datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def sanitize_csv_value(val: str) -> str:
    val = val.strip()
    return f"'{val}" if val.startswith(('=', '+', '-', '@')) else val

def sanitize_zpl_input(val: str) -> str:
    return str(val).replace("^", "").replace("~", "").strip()

def send_webhook_sync(url: str, payload: dict, api_key: str = ""):
    headers = {'Content-Type': 'application/json'}
    if api_key and api_key.strip():
        headers['Authorization'] = f"Bearer {api_key.strip()}"
    req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'), headers=headers, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=5) as response: return response.status
    except Exception: return None

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

async def record_stock_movement(conn: asyncpg.Connection, sku: str, branch_id: uuid.UUID, sector_id: uuid.UUID, location_id: Optional[uuid.UUID], quantity: float, movement_type: str, ref_doc: str, username: str):
    await conn.execute("INSERT INTO stock_movements (sku, branch_id, sector_id, location_id, quantity, movement_type, reference_document, username) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)", sku.upper(), branch_id, sector_id, location_id, quantity, movement_type, ref_doc, username)
    if location_id:
        await conn.execute("INSERT INTO stock_inventory (branch_id, sector_id, location_id, sku, quantity) VALUES ($1, $2, $3, $4, $5) ON CONFLICT (branch_id, sector_id, location_id, sku) DO UPDATE SET quantity = stock_inventory.quantity + EXCLUDED.quantity, updated_at = CURRENT_TIMESTAMP", branch_id, sector_id, location_id, sku.upper(), quantity)
    else:
        await conn.execute("INSERT INTO stock_inventory (branch_id, sector_id, location_id, sku, quantity) VALUES ($1, $2, NULL, $3, $4) ON CONFLICT (branch_id, sector_id, sku) WHERE location_id IS NULL DO UPDATE SET quantity = stock_inventory.quantity + EXCLUDED.quantity, updated_at = CURRENT_TIMESTAMP", branch_id, sector_id, sku.upper(), quantity)

    total_qty = await conn.fetchval("SELECT COALESCE(SUM(quantity), 0) FROM stock_inventory WHERE UPPER(sku) = $1", sku.upper())
    stock_payload = { "event": "stock.updated", "sku": sku.upper(), "available_quantity": float(total_qty), "timestamp": datetime.now(timezone.utc).isoformat() }
    await dispatch_event_to_channels(conn, "OUTBOUND_STOCK", stock_payload)

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
                    "CREATE TABLE IF NOT EXISTS branches (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), code VARCHAR(50) UNIQUE NOT NULL, name VARCHAR(150) NOT NULL, is_active BOOLEAN DEFAULT TRUE, created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP);",
                    "CREATE TABLE IF NOT EXISTS entities (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), tax_id VARCHAR(50) UNIQUE NOT NULL, company_name VARCHAR(150) NOT NULL, is_customer BOOLEAN DEFAULT TRUE, is_supplier BOOLEAN DEFAULT FALSE, is_active BOOLEAN DEFAULT TRUE, created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP);",
                    "CREATE TABLE IF NOT EXISTS entity_addresses (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), entity_id UUID REFERENCES entities(id) ON DELETE CASCADE, address_label VARCHAR(100) NOT NULL, full_address TEXT NOT NULL, created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP);",
                    "CREATE TABLE IF NOT EXISTS items (sku VARCHAR(100) PRIMARY KEY, description TEXT NOT NULL, category VARCHAR(100), length FLOAT DEFAULT 0, width FLOAT DEFAULT 0, height FLOAT DEFAULT 0, weight FLOAT DEFAULT 0, volume FLOAT DEFAULT 0, is_active BOOLEAN DEFAULT TRUE, created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP);",
                    "ALTER TABLE items ADD COLUMN IF NOT EXISTS length FLOAT DEFAULT 0;",
                    "ALTER TABLE items ADD COLUMN IF NOT EXISTS width FLOAT DEFAULT 0;",
                    "ALTER TABLE items ADD COLUMN IF NOT EXISTS height FLOAT DEFAULT 0;",
                    "ALTER TABLE items ADD COLUMN IF NOT EXISTS weight FLOAT DEFAULT 0;",
                    "ALTER TABLE items ADD COLUMN IF NOT EXISTS volume FLOAT DEFAULT 0;",
                    "CREATE TABLE IF NOT EXISTS sectors (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), branch_id UUID REFERENCES branches(id) ON DELETE CASCADE, name VARCHAR(100) UNIQUE NOT NULL, print_queue_code VARCHAR(50) UNIQUE NOT NULL, uses_locations BOOLEAN DEFAULT FALSE, is_active BOOLEAN DEFAULT TRUE, created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP);",
                    "CREATE TABLE IF NOT EXISTS locations (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), sector_id UUID REFERENCES sectors(id) ON DELETE CASCADE, location_code VARCHAR(100) NOT NULL, description VARCHAR(255), is_active BOOLEAN DEFAULT TRUE);",
                    "CREATE TABLE IF NOT EXISTS item_locations (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), item_sku VARCHAR(100) NOT NULL, location_id UUID REFERENCES locations(id) ON DELETE CASCADE, created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP, UNIQUE(item_sku, location_id));",
                    "CREATE TABLE IF NOT EXISTS documents (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), document_number VARCHAR(50) UNIQUE NOT NULL, document_type VARCHAR(20) DEFAULT 'PICKING', channel_origin VARCHAR(50) DEFAULT 'INTERNAL', status VARCHAR(20) DEFAULT 'PENDING', label_printed BOOLEAN DEFAULT FALSE, customer_id UUID REFERENCES entities(id), customer_address_id UUID REFERENCES entity_addresses(id), created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP);",
                    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS channel_origin VARCHAR(50) DEFAULT 'INTERNAL';",
                    "CREATE TABLE IF NOT EXISTS document_lines (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), document_id UUID REFERENCES documents(id) ON DELETE CASCADE, sku VARCHAR(100) NOT NULL, quantity_requested NUMERIC NOT NULL, quantity_picked NUMERIC DEFAULT 0);",
                    "CREATE TABLE IF NOT EXISTS audit_logs (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), username VARCHAR(50) NOT NULL, action VARCHAR(50) NOT NULL, details TEXT, created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP);",
                    "CREATE TABLE IF NOT EXISTS item_print_jobs (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), job_code VARCHAR(50) UNIQUE NOT NULL, zpl_content TEXT NOT NULL, label_printed BOOLEAN DEFAULT FALSE, created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP);",
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
                    "INSERT INTO system_settings (key, value) VALUES ('zpl_order_template', '^XA^FO50,50^A0N,40,40^FDPEDIDO: {order_number}^FS^FO50,110^A0N,30,30^FDCLIENTE: {client_name}^FS^XZ') ON CONFLICT (key) DO NOTHING;"
                ]
		# --- INICIO: CREACIÓN AUTOMÁTICA DEL USUARIO ADMINISTRADOR ---
                try:
                    user_count = await conn.fetchval("SELECT COUNT(*) FROM users")
                    if user_count == 0:
                        init_pass = os.getenv("INITIAL_ADMIN_PASSWORD", "admin360")
                        hashed_pass = get_password_hash(init_pass) # Asegúrate de que esta función coincida con la tuya
                        await conn.execute(
                            "INSERT INTO users (username, full_name, password_hash, role) VALUES ($1, $2, $3, 'ADMIN')",
                            "admin", "Administrador Inicial", hashed_pass
                        )
                        print("Usuario admin inicial creado con éxito.")
                except Exception as e:
                    print(f"No se pudo crear el usuario inicial: {e}")
                # --- FIN: CREACIÓN AUTOMÁTICA ---
                for stmt in ddl_statements:
                    try: await conn.execute(stmt)
                    except Exception: pass

                # CREACIÓN AUTOMÁTICA DEL USUARIO ADMINISTRADOR INICIAL
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

# === MIDDLEWARE CORS ===
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# === MODELOS PYDANTIC ===
class LoginRequest(BaseModel): username: str; password: str
class UserCreate(BaseModel): username: str; full_name: str; password: str; role: str = "PREPARADOR"; email: Optional[str] = None; branch_id: Optional[str] = None; sector_id: Optional[str] = None
class UserUpdate(BaseModel): full_name: Optional[str] = None; role: Optional[str] = None; email: Optional[str] = None; branch_id: Optional[str] = None; sector_id: Optional[str] = None; is_active: Optional[bool] = None; password: Optional[str] = None
class AddressCreate(BaseModel): address_label: str; full_address: str
class EntityCreate(BaseModel): tax_id: str; company_name: str; is_customer: bool = True; is_supplier: bool = False; initial_address: Optional[AddressCreate] = None
class EntityUpdate(BaseModel): company_name: Optional[str] = None; tax_id: Optional[str] = None; is_customer: Optional[bool] = None; is_supplier: Optional[bool] = None; is_active: Optional[bool] = None
class OrderLineInput(BaseModel): sku: str; quantity: float
class OrderCreateInput(BaseModel): document_number: str; document_type: str = "PICKING"; channel_origin: str = "EXTERNAL_API"; customer_tax_id: str; address_label: str; lines: List[OrderLineInput]
class SettingsUpdate(BaseModel): app_name: Optional[str] = None; primary_color: Optional[str] = None; company_cuit: Optional[str] = None; zebra_ip: Optional[str] = None; allow_multiproduct_locations: Optional[str] = None; require_mobile_reception: Optional[str] = None; enable_item_dimensions: Optional[str] = None; zpl_item_width: Optional[str] = None; zpl_item_height: Optional[str] = None; zpl_item_template: Optional[str] = None; zpl_order_width: Optional[str] = None; zpl_order_height: Optional[str] = None; zpl_order_template: Optional[str] = None; tracker360_api_key: Optional[str] = None
class IntegrationChannelCreate(BaseModel): name: str; channel_type: str; target_url: str; api_key: Optional[str] = None
class BatchItemPrintInput(BaseModel): sku: str; quantity: int
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
    user = await conn.fetchrow("SELECT id, username, password_hash, role, is_active FROM users WHERE username = $1", credentials.username.strip().lower())
    if not user or not user["is_active"] or not verify_password(credentials.password, user["password_hash"]):
        await log_action(conn, credentials.username.strip().lower(), "LOGIN_FAILED", "Intento fallido", client_ip)
        raise HTTPException(status_code=401, detail="Credenciales incorrectas.")
    token = create_access_token({"sub": user["username"], "role": user["role"], "id": str(user["id"])})
    response.set_cookie(key="access_token", value=f"Bearer {token}", httponly=True, secure=False, samesite="lax", max_age=28800)
    await log_action(conn, user["username"], "LOGIN_SUCCESS", "Inicio de sesión", client_ip)
    return {"message": "Éxito", "role": user["role"]}

@app.post("/api/auth/logout")
async def logout(response: Response): response.delete_cookie("access_token"); return {"message": "Éxito"}

# === MÚLTIPLES CANALES DE INTEGRACIÓN ===
@app.get("/api/admin/integrations")
async def list_integration_channels(admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    return [dict(r) for r in await conn.fetch("SELECT id, name, channel_type, target_url, api_key, is_active, created_at FROM integration_channels ORDER BY name ASC")]

@app.post("/api/admin/integrations")
async def create_integration_channel(data: IntegrationChannelCreate, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    await conn.execute("INSERT INTO integration_channels (name, channel_type, target_url, api_key) VALUES ($1, $2, $3, $4)", data.name.strip(), data.channel_type.strip(), data.target_url.strip(), data.api_key.strip() if data.api_key else None)
    return {"status": "success", "message": "Canal de integración configurado."}

@app.delete("/api/admin/integrations/{channel_id}")
async def delete_integration_channel(channel_id: str, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    await conn.execute("DELETE FROM integration_channels WHERE id = $1", uuid.UUID(channel_id))
    return {"status": "success", "message": "Canal eliminado."}

# === INGESTA EXTERNA IDEMPOTENTE ===
@app.post("/api/v1/external/orders")
async def create_external_order(data: OrderCreateInput, authenticated: bool = Depends(verify_system_api_key), conn: asyncpg.Connection = Depends(get_db_connection)):
    if not data.lines or len(data.lines) == 0:
        raise HTTPException(status_code=400, detail="El pedido debe contener al menos un artículo en 'lines'.")

    clean_doc = data.document_number.strip().upper()

    async with conn.transaction():
        existing_doc = await conn.fetchval("SELECT id FROM documents WHERE UPPER(document_number) = $1", clean_doc)
        if existing_doc:
            raise HTTPException(status_code=400, detail=f"El pedido {clean_doc} ya fue registrado previamente en el depósito.")

        customer = await conn.fetchrow("SELECT id FROM entities WHERE tax_id = $1", data.customer_tax_id.strip())
        if not customer:
            cust_id = await conn.fetchval("INSERT INTO entities (tax_id, company_name, is_customer) VALUES ($1, $2, TRUE) RETURNING id", data.customer_tax_id.strip(), f"Cliente {data.customer_tax_id.strip()}")
        else:
            cust_id = customer["id"]

        addr_id = await conn.fetchval("SELECT id FROM entity_addresses WHERE entity_id = $1 AND address_label = $2", cust_id, data.address_label.strip())
        if not addr_id:
            addr_id = await conn.fetchval("INSERT INTO entity_addresses (entity_id, address_label, full_address) VALUES ($1, $2, $3) RETURNING id", cust_id, data.address_label.strip(), data.address_label.strip())

        doc_id = await conn.fetchval("INSERT INTO documents (document_number, document_type, channel_origin, customer_id, customer_address_id, status) VALUES ($1, $2, $3, $4, $5, 'PENDING') RETURNING id", clean_doc, data.document_type, data.channel_origin.strip().upper(), cust_id, addr_id)

        for line in data.lines:
            await conn.execute("INSERT INTO document_lines (document_id, sku, quantity_requested) VALUES ($1, $2, $3)", doc_id, line.sku.strip().upper(), line.quantity)

        await log_action(conn, "SYSTEM_API", "EXTERNAL_ORDER_IMPORT", f"Ingresó pedido {clean_doc} desde canal {data.channel_origin.upper()}")
        return {"status": "success", "message": f"Pedido {clean_doc} ingresado al circuito de preparación."}

@app.get("/api/v1/external/stock/{sku}")
async def get_external_stock_balance(sku: str, authenticated: bool = Depends(verify_system_api_key), conn: asyncpg.Connection = Depends(get_db_connection)):
    total = await conn.fetchval("SELECT COALESCE(SUM(quantity), 0) FROM stock_inventory WHERE UPPER(sku) = $1", sku.strip().upper())
    return {"sku": sku.strip().upper(), "available_quantity": float(total)}

# === DASHBOARD Y SETTINGS ===
@app.get("/api/admin/dashboard")
async def get_dashboard_summary(admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    pending_orders = [dict(r) for r in await conn.fetch("SELECT d.document_number, d.status, COALESCE(c.company_name, 'Sin cliente') as company_name FROM documents d LEFT JOIN entities c ON d.customer_id = c.id WHERE d.status IN ('PENDING', 'IN_PROGRESS', 'COMPLETED') ORDER BY d.created_at DESC LIMIT 5")]
    active_transfers = [dict(r) for r in await conn.fetch("SELECT t.transfer_number, t.status, COALESCE(ob.name, 'N/A') as origin_branch, COALESCE(db.name, 'N/A') as destination_branch FROM transfer_orders t LEFT JOIN branches ob ON t.origin_branch_id = ob.id LEFT JOIN branches db ON t.destination_branch_id = db.id ORDER BY t.created_at DESC LIMIT 5")]
    latest_logs = [dict(r) for r in await conn.fetch("SELECT username, action, details, created_at FROM audit_logs ORDER BY created_at DESC LIMIT 5")]
    return { "pending_orders": pending_orders, "active_transfers": active_transfers, "latest_logs": latest_logs }

@app.get("/api/settings")
async def get_settings(conn: asyncpg.Connection = Depends(get_db_connection)): return {row['key']: row['value'] for row in await conn.fetch("SELECT key, value FROM system_settings")}

@app.put("/api/admin/settings")
async def update_settings(data: SettingsUpdate, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    async with conn.transaction():
        for key, val in data.model_dump(exclude_unset=True).items():
            if val is not None: await conn.execute("INSERT INTO system_settings (key, value) VALUES ($1, $2) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", key, str(val))
    return {"status": "success", "message": "Configuración guardada correctamente."}

@app.post("/api/admin/settings/generate-key")
async def generate_new_system_api_key(admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    new_key = f"trk_live_{secrets.token_hex(24)}"
    await conn.execute("INSERT INTO system_settings (key, value) VALUES ('tracker360_api_key', $1) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", new_key)
    return {"status": "success", "new_key": new_key}

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
async def list_items(sku: str = "", description: str = "", limit: int = 50, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    q = "SELECT i.sku, i.description, i.category, i.length, i.width, i.height, i.weight, i.volume, COALESCE((SELECT string_agg(l.location_code, ', ') FROM item_locations il JOIN locations l ON il.location_id = l.id WHERE il.item_sku = i.sku), 'Sin asignación') as locations_summary FROM items i WHERE i.sku ILIKE $1 AND i.description ILIKE $2 ORDER BY i.sku ASC LIMIT $3"
    return { "items": [dict(r) for r in await conn.fetch(q, f"%{sku}%", f"%{description}%", limit)] }

@app.put("/api/admin/items/{sku}")
async def update_item(sku: str, data: ItemUpdate, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    calculated_vol = (data.length * data.width * data.height) / 1000000.0 if (data.length and data.width and data.height) else (data.volume or 0.0)
    await conn.execute("""
        UPDATE items 
        SET description = $1, category = $2, length = COALESCE($3, length), width = COALESCE($4, width), height = COALESCE($5, height), weight = COALESCE($6, weight), volume = $7 
        WHERE UPPER(sku) = $8
    """, data.description, data.category, data.length, data.width, data.height, data.weight, calculated_vol, sku.upper())
    return {"status": "success", "message": "Ficha de artículo actualizada."}

@app.get("/api/admin/entities")
async def list_entities(admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)): 
    return [dict(r) for r in await conn.fetch("SELECT e.id, e.tax_id, e.company_name, e.is_customer, e.is_supplier, e.is_active, COALESCE(json_agg(json_build_object('id', a.id, 'label', a.address_label, 'address', a.full_address)) FILTER (WHERE a.id IS NOT NULL), '[]') as addresses FROM entities e LEFT JOIN entity_addresses a ON e.id = a.entity_id GROUP BY e.id ORDER BY e.company_name ASC")]
@app.post("/api/admin/entities")
async def create_entity(data: EntityCreate, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    ent_id = await conn.fetchval("INSERT INTO entities (tax_id, company_name, is_customer, is_supplier) VALUES ($1, $2, $3, $4) RETURNING id", data.tax_id.strip(), data.company_name.strip(), data.is_customer, data.is_supplier)
    if data.initial_address: await conn.execute("INSERT INTO entity_addresses (entity_id, address_label, full_address) VALUES ($1, $2, $3)", ent_id, data.initial_address.address_label, data.initial_address.full_address)
    return {"status": "success"}

# === OPERATIVA MÓVIL, PICKING Y PACKING ===
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
        return {"status": "success", "message": "Pedido despachado exitosamente."}

# === USUARIOS Y LOGS ===
@app.get("/api/admin/users")
async def list_users(admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)): return [dict(u) for u in await conn.fetch("SELECT u.id, u.username, u.full_name, u.role, u.is_active, u.email, u.branch_id, u.sector_id, b.name as branch_name, s.name as sector_name FROM users u LEFT JOIN branches b ON u.branch_id = b.id::text LEFT JOIN sectors s ON u.sector_id = s.id::text ORDER BY u.created_at DESC")]
@app.post("/api/admin/users")
async def create_or_update_user(data: UserCreate, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)): await conn.execute("INSERT INTO users (username, full_name, password_hash, role, email, branch_id, sector_id) VALUES ($1, $2, $3, $4, $5, $6, $7) ON CONFLICT (username) DO UPDATE SET full_name = EXCLUDED.full_name, password_hash = EXCLUDED.password_hash, role = EXCLUDED.role, email = EXCLUDED.email, branch_id = EXCLUDED.branch_id, sector_id = EXCLUDED.sector_id, is_active = TRUE", data.username.strip().lower(), data.full_name.strip(), get_password_hash(data.password), data.role, data.email, data.branch_id, data.sector_id); return {"status": "success"}

@app.get("/api/admin/logs")
async def list_logs(admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)): return [dict(l) for l in await conn.fetch("SELECT username, action, details, created_at FROM audit_logs ORDER BY created_at DESC LIMIT 100")]

app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")