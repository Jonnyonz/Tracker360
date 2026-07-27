import os, asyncio, csv, uuid
from io import StringIO
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import jwt, asyncpg
from fastapi import FastAPI, Depends, HTTPException, status, Response, Request, UploadFile, File
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from passlib.context import CryptContext
from typing import List, Optional

SECRET_KEY = "clave-super-secreta-tracker360"
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

class DB:
    pool: Optional[asyncpg.Pool] = None

async def get_db_connection():
    if DB.pool is None:
        raise HTTPException(
            status_code=status.HTTP_53_SERVICE_UNAVAILABLE,
            detail="Servicio de base de datos no disponible. Reintente en unos segundos."
        )
    async with DB.pool.acquire() as conn:
        yield conn

async def log_action(conn: asyncpg.Connection, username: str, action: str, details: str):
    try:
        await conn.execute(
            "INSERT INTO audit_logs (username, action, details) VALUES ($1, $2, $3)",
            username, action, details
        )
    except Exception:
        pass

async def record_stock_movement(conn: asyncpg.Connection, sku: str, branch_id: uuid.UUID, sector_id: uuid.UUID, location_id: Optional[uuid.UUID], quantity: float, movement_type: str, ref_doc: str, username: str):
    await conn.execute("""
        INSERT INTO stock_movements (sku, branch_id, sector_id, location_id, quantity, movement_type, reference_document, username)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
    """, sku.upper(), branch_id, sector_id, location_id, quantity, movement_type, ref_doc, username)

    if location_id:
        await conn.execute("""
            INSERT INTO stock_inventory (branch_id, sector_id, location_id, sku, quantity)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (branch_id, sector_id, location_id, sku)
            DO UPDATE SET quantity = stock_inventory.quantity + EXCLUDED.quantity, updated_at = CURRENT_TIMESTAMP
        """, branch_id, sector_id, location_id, sku.upper(), quantity)
    else:
        await conn.execute("""
            INSERT INTO stock_inventory (branch_id, sector_id, location_id, sku, quantity)
            VALUES ($1, $2, NULL, $3, $4)
            ON CONFLICT (branch_id, sector_id, sku) WHERE location_id IS NULL
            DO UPDATE SET quantity = stock_inventory.quantity + EXCLUDED.quantity, updated_at = CURRENT_TIMESTAMP
        """, branch_id, sector_id, sku.upper(), quantity)

@asynccontextmanager
async def lifespan(app: FastAPI):
    for attempt in range(10):
        try:
            DB.pool = await asyncpg.create_pool(
                user=os.getenv("POSTGRES_USER", "tracker_admin"),
                password=os.getenv("POSTGRES_PASSWORD", "tracker_secure_pass_2026"),
                database=os.getenv("POSTGRES_DB", "tracker360_db"),
                host="db",
                port=5432,
                min_size=1,
                max_size=20
            )
            if DB.pool is not None:
                break
        except Exception:
            await asyncio.sleep(1.0)

    if DB.pool is not None:
        try:
            async with DB.pool.acquire() as conn:
                ddl_statements = [
                    "CREATE TABLE IF NOT EXISTS system_settings (key VARCHAR(100) PRIMARY KEY, value TEXT);",
                    "CREATE TABLE IF NOT EXISTS users (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), username VARCHAR(50) UNIQUE NOT NULL, full_name VARCHAR(100) NOT NULL, password_hash TEXT NOT NULL, role VARCHAR(20) NOT NULL DEFAULT 'PREPARADOR', is_active BOOLEAN DEFAULT TRUE, created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP);",
                    "CREATE TABLE IF NOT EXISTS branches (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), code VARCHAR(50) UNIQUE NOT NULL, name VARCHAR(150) NOT NULL, is_active BOOLEAN DEFAULT TRUE, created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP);",
                    "CREATE TABLE IF NOT EXISTS entities (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), tax_id VARCHAR(50) UNIQUE NOT NULL, company_name VARCHAR(150) NOT NULL, is_customer BOOLEAN DEFAULT TRUE, is_supplier BOOLEAN DEFAULT FALSE, is_active BOOLEAN DEFAULT TRUE, created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP);",
                    "CREATE TABLE IF NOT EXISTS entity_addresses (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), entity_id UUID REFERENCES entities(id) ON DELETE CASCADE, address_label VARCHAR(100) NOT NULL, full_address TEXT NOT NULL, created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP);",
                    "CREATE TABLE IF NOT EXISTS items (sku VARCHAR(100) PRIMARY KEY, description TEXT NOT NULL, category VARCHAR(100), is_active BOOLEAN DEFAULT TRUE, created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP);",
                    "CREATE TABLE IF NOT EXISTS sectors (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), name VARCHAR(100) UNIQUE NOT NULL, print_queue_code VARCHAR(50) UNIQUE NOT NULL, uses_locations BOOLEAN DEFAULT FALSE, is_active BOOLEAN DEFAULT TRUE, created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP);",
                    "ALTER TABLE sectors ADD COLUMN IF NOT EXISTS branch_id UUID REFERENCES branches(id) ON DELETE CASCADE;",
                    "CREATE TABLE IF NOT EXISTS locations (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), location_code VARCHAR(100) NOT NULL, description VARCHAR(255), is_active BOOLEAN DEFAULT TRUE);",
                    "ALTER TABLE locations ADD COLUMN IF NOT EXISTS sector_id UUID REFERENCES sectors(id) ON DELETE CASCADE;",
                    "CREATE TABLE IF NOT EXISTS item_locations (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), item_sku VARCHAR(100) NOT NULL, location_id UUID REFERENCES locations(id) ON DELETE CASCADE, created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP, UNIQUE(item_sku, location_id));",
                    "CREATE TABLE IF NOT EXISTS documents (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), document_number VARCHAR(50) UNIQUE NOT NULL, document_type VARCHAR(20) DEFAULT 'PICKING', status VARCHAR(20) DEFAULT 'PENDING', label_printed BOOLEAN DEFAULT FALSE, customer_id UUID REFERENCES entities(id), customer_address_id UUID REFERENCES entity_addresses(id), created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP);",
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
                    "INSERT INTO system_settings (key, value) VALUES ('allow_multiproduct_locations', 'false') ON CONFLICT (key) DO NOTHING;",
                    "INSERT INTO system_settings (key, value) VALUES ('require_mobile_reception', 'false') ON CONFLICT (key) DO NOTHING;",
                    "INSERT INTO system_settings (key, value) VALUES ('enable_item_dimensions', 'false') ON CONFLICT (key) DO NOTHING;",
                    "INSERT INTO system_settings (key, value) VALUES ('app_name', 'Tracker360') ON CONFLICT (key) DO NOTHING;",
                    "INSERT INTO system_settings (key, value) VALUES ('primary_color', '#1E3A8A') ON CONFLICT (key) DO NOTHING;",
                    "ALTER TABLE items ADD COLUMN IF NOT EXISTS weight FLOAT;",
                    "ALTER TABLE items ADD COLUMN IF NOT EXISTS length FLOAT;",
                    "ALTER TABLE items ADD COLUMN IF NOT EXISTS width FLOAT;",
                    "ALTER TABLE items ADD COLUMN IF NOT EXISTS height FLOAT;",
                    "ALTER TABLE items ADD COLUMN IF NOT EXISTS volume FLOAT;"
                ]
                for stmt in ddl_statements:
                    try: await conn.execute(stmt)
                    except Exception: pass
                branch_count = await conn.fetchval("SELECT COUNT(*) FROM branches")
                if branch_count == 0:
                    default_branch_id = await conn.fetchval("INSERT INTO branches (code, name) VALUES ('SUC-01', 'Sucursal Central') RETURNING id")
                    await conn.execute("UPDATE sectors SET branch_id = $1 WHERE branch_id IS NULL", default_branch_id)
        except Exception:
            pass
    yield
    if DB.pool is not None:
        await DB.pool.close()

app = FastAPI(title="Tracker360 API", version="1.0", lifespan=lifespan)

# === MODELOS PYDANTIC ===
class LoginRequest(BaseModel): username: str; password: str
class UserCreate(BaseModel): username: str; full_name: str; password: str; role: str = "PREPARADOR"; email: Optional[str] = None; branch_id: Optional[str] = None; sector_id: Optional[str] = None
class UserUpdate(BaseModel): full_name: Optional[str] = None; role: Optional[str] = None; email: Optional[str] = None; branch_id: Optional[str] = None; sector_id: Optional[str] = None; is_active: Optional[bool] = None; password: Optional[str] = None
class AddressCreate(BaseModel): address_label: str; full_address: str
class EntityCreate(BaseModel): tax_id: str; company_name: str; is_customer: bool = True; is_supplier: bool = False; initial_address: Optional[AddressCreate] = None
class EntityUpdate(BaseModel): company_name: Optional[str] = None; tax_id: Optional[str] = None; is_customer: Optional[bool] = None; is_supplier: Optional[bool] = None; is_active: Optional[bool] = None
class OrderLineInput(BaseModel): sku: str; quantity: float
class OrderCreateInput(BaseModel): document_number: str; document_type: str = "PICKING"; customer_tax_id: str; address_label: str; lines: List[OrderLineInput]
class SettingsUpdate(BaseModel): app_name: Optional[str] = None; primary_color: Optional[str] = None; company_cuit: Optional[str] = None; zebra_ip: Optional[str] = None; enable_item_dimensions: Optional[str] = None; allow_multiproduct_locations: Optional[str] = None; require_mobile_reception: Optional[str] = None; zpl_item_width: Optional[str] = None; zpl_item_height: Optional[str] = None; zpl_item_template: Optional[str] = None; zpl_order_width: Optional[str] = None; zpl_order_height: Optional[str] = None; zpl_order_template: Optional[str] = None
class ScanPayload(BaseModel): document_number: str; sku: str
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
class ItemUpdate(BaseModel): description: Optional[str] = None; category: Optional[str] = None; weight: Optional[float] = None; length: Optional[float] = None; width: Optional[float] = None; height: Optional[float] = None; volume: Optional[float] = None

class MobileRelocateInput(BaseModel): sku: str; origin_location_code: str; destination_location_code: str; quantity: float
class MobileRemitoScanInput(BaseModel): remito_number: str; sku: str; quantity: float; location_code: Optional[str] = None
class MobileTransferScanInput(BaseModel): transfer_number: str; sku: str; quantity: float; destination_location_code: Optional[str] = None
class PickScanInput(BaseModel): sku: str; quantity: float; location_code: str
class ItemLocationInput(BaseModel): sku: str; location_code: str

# === AUTENTICACIÓN ===
async def get_current_user(request: Request):
    token = request.cookies.get("access_token")
    if not token or not token.startswith("Bearer "): raise HTTPException(status_code=401, detail="Sesión expirada o no iniciada.")
    try: return jwt.decode(token.split(" ")[1], SECRET_KEY, algorithms=[ALGORITHM])
    except: raise HTTPException(status_code=401, detail="Sesión inválida.")

async def require_admin(current_user: dict = Depends(get_current_user)):
    if current_user.get("role") != "ADMIN": raise HTTPException(status_code=403, detail="Permisos insuficientes.")
    return current_user

# === DASHBOARD Y CONFIGURACIÓN ===
@app.get("/api/admin/dashboard")
async def get_dashboard_summary(admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    pending_orders = [dict(r) for r in await conn.fetch("SELECT d.document_number, d.status, COALESCE(c.company_name, 'Sin cliente') as company_name FROM documents d LEFT JOIN entities c ON d.customer_id = c.id WHERE d.status IN ('PENDING', 'IN_PROGRESS') ORDER BY d.created_at DESC LIMIT 5")]
    active_transfers = [dict(r) for r in await conn.fetch("SELECT t.transfer_number, t.status, COALESCE(ob.name, 'N/A') as origin_branch, COALESCE(db.name, 'N/A') as destination_branch FROM transfer_orders t LEFT JOIN branches ob ON t.origin_branch_id = ob.id LEFT JOIN branches db ON t.destination_branch_id = db.id ORDER BY t.created_at DESC LIMIT 5")]
    latest_logs = [dict(r) for r in await conn.fetch("SELECT username, action, details, created_at FROM audit_logs ORDER BY created_at DESC LIMIT 5")]
    return { "pending_orders": pending_orders, "active_transfers": active_transfers, "latest_logs": latest_logs }

@app.get("/api/settings")
async def get_settings(conn: asyncpg.Connection = Depends(get_db_connection)):
    rows = await conn.fetch("SELECT key, value FROM system_settings")
    return {row['key']: row['value'] for row in rows}

@app.put("/api/admin/settings")
async def update_settings(data: SettingsUpdate, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    async with conn.transaction():
        for key, val in data.model_dump(exclude_unset=True).items():
            if val is not None:
                await conn.execute("INSERT INTO system_settings (key, value) VALUES ($1, $2) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", key, str(val))
    return {"status": "success", "message": "Configuración guardada correctamente."}

# === ESTRUCTURA DE DEPÓSITOS ===
@app.get("/api/admin/branches")
async def list_branches(admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)): return [dict(r) for r in await conn.fetch("SELECT id, code, name, is_active FROM branches ORDER BY name ASC")]

@app.post("/api/admin/branches")
async def create_branch(data: BranchCreate, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    try: await conn.execute("INSERT INTO branches (code, name) VALUES ($1, $2)", data.code.strip().upper(), data.name.strip()); return {"status": "success", "message": "Sucursal registrada."}
    except Exception: raise HTTPException(status_code=400, detail="El código de sucursal ya existe.")

@app.put("/api/admin/branches/{branch_id}")
async def update_branch(branch_id: str, data: BranchUpdate, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    updates, params, idx = [], [uuid.UUID(branch_id)], 2
    if data.code is not None: updates.append(f"code = ${idx}"); params.append(data.code.strip().upper()); idx += 1
    if data.name is not None: updates.append(f"name = ${idx}"); params.append(data.name.strip()); idx += 1
    if data.is_active is not None: updates.append(f"is_active = ${idx}"); params.append(data.is_active); idx += 1
    if updates: await conn.execute(f"UPDATE branches SET {', '.join(updates)} WHERE id = $1", *params)
    return {"status": "success"}

@app.get("/api/admin/sectors")
async def list_sectors(admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)): return [dict(r) for r in await conn.fetch("SELECT s.id, s.name, s.print_queue_code, s.uses_locations, s.is_active, s.branch_id, COALESCE(b.name, 'Sin sucursal') as branch_name FROM sectors s LEFT JOIN branches b ON s.branch_id = b.id ORDER BY s.name ASC")]

@app.post("/api/admin/sectors")
async def create_sector(data: SectorCreate, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    try: branch_uuid = uuid.UUID(data.branch_id) if data.branch_id else await conn.fetchval("SELECT id FROM branches LIMIT 1"); await conn.execute("INSERT INTO sectors (name, print_queue_code, uses_locations, branch_id) VALUES ($1, $2, $3, $4)", data.name.strip(), data.print_queue_code.strip().upper(), data.uses_locations, branch_uuid); return {"status": "success", "message": "Sector registrado."}
    except Exception: raise HTTPException(status_code=400, detail="El sector o el código de impresión ya existen.")

@app.put("/api/admin/sectors/{sector_id}")
async def update_sector(sector_id: str, data: SectorUpdate, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    updates, params, idx = [], [uuid.UUID(sector_id)], 2
    if data.name is not None: updates.append(f"name = ${idx}"); params.append(data.name.strip()); idx += 1
    if data.print_queue_code is not None: updates.append(f"print_queue_code = ${idx}"); params.append(data.print_queue_code.strip().upper()); idx += 1
    if data.uses_locations is not None: updates.append(f"uses_locations = ${idx}"); params.append(data.uses_locations); idx += 1
    if data.branch_id is not None: updates.append(f"branch_id = ${idx}"); params.append(uuid.UUID(data.branch_id)); idx += 1
    if data.is_active is not None: updates.append(f"is_active = ${idx}"); params.append(data.is_active); idx += 1
    if updates: await conn.execute(f"UPDATE sectors SET {', '.join(updates)} WHERE id = $1", *params)
    return {"status": "success"}

@app.get("/api/admin/locations")
async def list_all_locations(admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)): return [dict(l) for l in await conn.fetch("SELECT l.id, l.location_code, l.description, l.is_active, l.sector_id, s.name as sector_name, s.branch_id, COALESCE(b.name, 'Sin sucursal') as branch_name FROM locations l JOIN sectors s ON l.sector_id = s.id LEFT JOIN branches b ON s.branch_id = b.id ORDER BY b.name ASC, s.name ASC, l.location_code ASC")]

@app.post("/api/admin/locations")
async def create_location_direct(data: LocationCreate, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    try: await conn.execute("INSERT INTO locations (sector_id, location_code, description) VALUES ($1, $2, $3)", uuid.UUID(data.sector_id), data.location_code.strip().upper(), data.description.strip() if data.description and data.description.strip() else None); return {"status": "success", "message": "Ubicación registrada."}
    except Exception: raise HTTPException(status_code=400, detail="El código de ubicación ya existe en este sector.")

@app.put("/api/admin/locations/{location_id}")
async def update_location(location_id: str, data: LocationUpdate, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    updates, params, idx = [], [uuid.UUID(location_id)], 2
    if data.location_code is not None: updates.append(f"location_code = ${idx}"); params.append(data.location_code.strip().upper()); idx += 1
    if data.description is not None: updates.append(f"description = ${idx}"); params.append(data.description.strip() if data.description and data.description.strip() else None); idx += 1
    if data.sector_id is not None: updates.append(f"sector_id = ${idx}"); params.append(uuid.UUID(data.sector_id)); idx += 1
    if data.is_active is not None: updates.append(f"is_active = ${idx}"); params.append(data.is_active); idx += 1
    if updates: await conn.execute(f"UPDATE locations SET {', '.join(updates)} WHERE id = $1", *params)
    return {"status": "success"}

# === ASIGNACIÓN DE UBICACIONES A ARTÍCULOS ===
@app.get("/api/admin/items/{sku}/locations")
async def get_item_locations(sku: str, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    rows = await conn.fetch("""
        SELECT il.id as assignment_id, l.id as location_id, l.location_code, l.description, s.name as sector_name, b.name as branch_name
        FROM item_locations il
        JOIN locations l ON il.location_id = l.id
        JOIN sectors s ON l.sector_id = s.id
        LEFT JOIN branches b ON s.branch_id = b.id
        WHERE UPPER(il.item_sku) = $1
        ORDER BY l.location_code ASC
    """, sku.strip().upper())
    return [dict(r) for r in rows]

@app.post("/api/admin/item-locations")
async def assign_item_location(data: ItemLocationInput, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    sku_clean, loc_clean = data.sku.strip().upper(), data.location_code.strip().upper()
    item_exists = await conn.fetchval("SELECT sku FROM items WHERE UPPER(sku) = $1", sku_clean)
    if not item_exists: raise HTTPException(400, f"El SKU '{sku_clean}' no existe.")
    loc = await conn.fetchrow("SELECT id FROM locations WHERE UPPER(location_code) = $1", loc_clean)
    if not loc: raise HTTPException(404, f"La ubicación '{loc_clean}' no existe.")
    await conn.execute("INSERT INTO item_locations (item_sku, location_id) VALUES ($1, $2) ON CONFLICT (item_sku, location_id) DO NOTHING", sku_clean, loc["id"])
    return {"status": "success", "message": f"Ubicación '{loc_clean}' asignada a {sku_clean}."}

@app.delete("/api/admin/item-locations/{assignment_id}")
async def remove_item_location(assignment_id: str, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    await conn.execute("DELETE FROM item_locations WHERE id = $1", uuid.UUID(assignment_id))
    return {"status": "success", "message": "Asignación de ubicación removida."}

# === CLIENTES Y PROVEEDORES (ENTIDADES) CON EDICIÓN Y GESTIÓN MULTIDIRECCIÓN ===
@app.get("/api/admin/entities")
async def list_entities(admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)): 
    return [dict(r) for r in await conn.fetch("SELECT e.id, e.tax_id, e.company_name, e.is_customer, e.is_supplier, e.is_active, COALESCE(json_agg(json_build_object('id', a.id, 'label', a.address_label, 'address', a.full_address)) FILTER (WHERE a.id IS NOT NULL), '[]') as addresses FROM entities e LEFT JOIN entity_addresses a ON e.id = a.entity_id GROUP BY e.id ORDER BY e.company_name ASC")]

@app.post("/api/admin/entities")
async def create_entity(data: EntityCreate, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    async with conn.transaction():
        ent_id = await conn.fetchval("INSERT INTO entities (tax_id, company_name, is_customer, is_supplier) VALUES ($1, $2, $3, $4) ON CONFLICT (tax_id) DO UPDATE SET company_name = EXCLUDED.company_name, is_customer = EXCLUDED.is_customer, is_supplier = EXCLUDED.is_supplier RETURNING id", data.tax_id.strip(), data.company_name.strip(), data.is_customer, data.is_supplier)
        if data.initial_address and data.initial_address.full_address.strip():
            label = data.initial_address.address_label.strip() if data.initial_address.address_label else "Principal"
            await conn.execute("INSERT INTO entity_addresses (entity_id, address_label, full_address) VALUES ($1, $2, $3)", ent_id, label, data.initial_address.full_address.strip())
    return {"status": "success", "message": "Entidad registrada correctamente."}

@app.put("/api/admin/entities/{entity_id}")
async def update_entity(entity_id: str, data: EntityUpdate, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    ent = await conn.fetchrow("SELECT id FROM entities WHERE id = $1", uuid.UUID(entity_id))
    if not ent: raise HTTPException(status_code=404, detail="Entidad no encontrada")
    updates, params, idx = [], [uuid.UUID(entity_id)], 2
    if data.company_name is not None: updates.append(f"company_name = ${idx}"); params.append(data.company_name.strip()); idx += 1
    if data.tax_id is not None: updates.append(f"tax_id = ${idx}"); params.append(data.tax_id.strip()); idx += 1
    if data.is_customer is not None: updates.append(f"is_customer = ${idx}"); params.append(data.is_customer); idx += 1
    if data.is_supplier is not None: updates.append(f"is_supplier = ${idx}"); params.append(data.is_supplier); idx += 1
    if data.is_active is not None: updates.append(f"is_active = ${idx}"); params.append(data.is_active); idx += 1
    if updates: await conn.execute(f"UPDATE entities SET {', '.join(updates)} WHERE id = $1", *params)
    return {"status": "success", "message": "Entidad actualizada correctamente."}

@app.get("/api/admin/entities/{entity_id}/addresses")
async def get_entity_addresses(entity_id: str, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    rows = await conn.fetch("SELECT id, address_label, full_address, created_at FROM entity_addresses WHERE entity_id = $1 ORDER BY created_at ASC", uuid.UUID(entity_id))
    return [dict(r) for r in rows]

@app.post("/api/admin/entities/{entity_id}/addresses")
async def add_entity_address(entity_id: str, data: AddressCreate, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    if not data.full_address or not data.full_address.strip():
        raise HTTPException(status_code=400, detail="Debe especificar la dirección completa.")
    label = data.address_label.strip() if data.address_label and data.address_label.strip() else "Sucursal"
    await conn.execute("INSERT INTO entity_addresses (entity_id, address_label, full_address) VALUES ($1, $2, $3)", uuid.UUID(entity_id), label, data.full_address.strip())
    return {"status": "success", "message": "Dirección registrada correctamente."}

@app.delete("/api/admin/entities/addresses/{address_id}")
async def delete_entity_address(address_id: str, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    await conn.execute("DELETE FROM entity_addresses WHERE id = $1", uuid.UUID(address_id))
    return {"status": "success", "message": "Dirección eliminada correctamente."}

# === COMPRAS, REMITOS Y FACTURAS ===
@app.get("/api/admin/suppliers/{supplier_id}/remitos")
async def list_supplier_pending_remitos(supplier_id: str, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    rows = await conn.fetch("""
        SELECT r.id, r.remito_number, r.status, r.created_at, COALESCE(b.name, 'Sin Sucursal') as branch_name, COALESCE(s.name, 'Sin Sector') as sector_name
        FROM purchase_remitos r
        LEFT JOIN branches b ON r.branch_id = b.id
        LEFT JOIN sectors s ON r.sector_id = s.id
        WHERE r.supplier_id = $1
        ORDER BY r.created_at DESC
    """, uuid.UUID(supplier_id))
    return [dict(r) for r in rows]

@app.get("/api/admin/purchase-orders")
async def list_purchase_orders(search: Optional[str] = None, limit: int = 5, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    if search and search.strip():
        where = " WHERE UPPER(po.order_number) LIKE $1 OR UPPER(e.company_name) LIKE $1 "
        params = [f"%{search.strip().upper()}%", limit]
        q = f"SELECT po.id, po.order_number, po.status, po.created_at, COALESCE(e.company_name, 'Sin Proveedor') as supplier_name, COALESCE(SUM(pol.quantity_ordered), 0) as total_qty FROM purchase_orders po LEFT JOIN entities e ON po.supplier_id = e.id LEFT JOIN purchase_order_lines pol ON po.id = pol.purchase_order_id {where} GROUP BY po.id, e.company_name ORDER BY po.created_at DESC LIMIT $2"
    else:
        params = [limit]
        q = f"SELECT po.id, po.order_number, po.status, po.created_at, COALESCE(e.company_name, 'Sin Proveedor') as supplier_name, COALESCE(SUM(pol.quantity_ordered), 0) as total_qty FROM purchase_orders po LEFT JOIN entities e ON po.supplier_id = e.id LEFT JOIN purchase_order_lines pol ON po.id = pol.purchase_order_id GROUP BY po.id, e.company_name ORDER BY po.created_at DESC LIMIT $1"
    return [dict(r) for r in await conn.fetch(q, *params)]

@app.post("/api/admin/purchase-orders")
async def create_purchase_order(data: POCreateInput, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    async with conn.transaction():
        po_id = await conn.fetchval("INSERT INTO purchase_orders (order_number, supplier_id) VALUES ($1, $2) RETURNING id", data.order_number.strip().upper(), uuid.UUID(data.supplier_id))
        for line in data.lines: await conn.execute("INSERT INTO purchase_order_lines (purchase_order_id, sku, quantity_ordered) VALUES ($1, $2, $3)", po_id, line.sku.strip().upper(), line.quantity_ordered)
        await log_action(conn, admin.get("sub"), "PO_CREATE", f"Registró Orden de Compra {data.order_number}")
    return {"status": "success", "message": "Orden de compra generada."}

@app.get("/api/admin/purchase-remitos")
async def list_purchase_remitos(search: Optional[str] = None, limit: int = 5, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    if search and search.strip():
        where = " WHERE UPPER(r.remito_number) LIKE $1 OR UPPER(e.company_name) LIKE $1 "
        params = [f"%{search.strip().upper()}%", limit]
        q = f"SELECT r.id, r.remito_number, r.status, r.created_at, COALESCE(e.company_name, 'Sin Proveedor') as supplier_name, COALESCE(b.name, 'N/A') as branch_name, COALESCE(s.name, 'N/A') as sector_name FROM purchase_remitos r LEFT JOIN entities e ON r.supplier_id = e.id LEFT JOIN branches b ON r.branch_id = b.id LEFT JOIN sectors s ON r.sector_id = s.id {where} ORDER BY r.created_at DESC LIMIT $2"
    else:
        params = [limit]
        q = f"SELECT r.id, r.remito_number, r.status, r.created_at, COALESCE(e.company_name, 'Sin Proveedor') as supplier_name, COALESCE(b.name, 'N/A') as branch_name, COALESCE(s.name, 'N/A') as sector_name FROM purchase_remitos r LEFT JOIN entities e ON r.supplier_id = e.id LEFT JOIN branches b ON r.branch_id = b.id LEFT JOIN sectors s ON r.sector_id = s.id ORDER BY r.created_at DESC LIMIT $1"
    return [dict(r) for r in await conn.fetch(q, *params)]

@app.post("/api/admin/purchase-remitos")
async def create_purchase_remito(data: RemitoCreateInput, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    async with conn.transaction():
        require_mobile = await conn.fetchval("SELECT value FROM system_settings WHERE key = 'require_mobile_reception'")
        initial_status = "PENDING_CONTROL" if require_mobile == "true" else "COMPLETED"
        remito_id = await conn.fetchval("INSERT INTO purchase_remitos (remito_number, supplier_id, purchase_order_id, branch_id, sector_id, status) VALUES ($1, $2, $3, $4, $5, $6) RETURNING id", data.remito_number.strip().upper(), uuid.UUID(data.supplier_id), uuid.UUID(data.purchase_order_id) if data.purchase_order_id else None, uuid.UUID(data.branch_id), uuid.UUID(data.sector_id), initial_status)
        for line in data.lines:
            loc_id = await conn.fetchval("SELECT id FROM locations WHERE sector_id = $1 AND UPPER(location_code) = $2", uuid.UUID(data.sector_id), line.location_code.strip().upper()) if line.location_code else None
            await conn.execute("INSERT INTO purchase_remito_lines (purchase_remito_id, sku, quantity_sent, quantity_received, location_id) VALUES ($1, $2, $3, $4, $5)", remito_id, line.sku.strip().upper(), line.quantity_sent, (0 if require_mobile == "true" else line.quantity_sent), loc_id)
            if require_mobile != "true": await record_stock_movement(conn, line.sku, uuid.UUID(data.branch_id), uuid.UUID(data.sector_id), loc_id, line.quantity_sent, 'IN_REMITO', data.remito_number, admin.get("sub"))
        await log_action(conn, admin.get("sub"), "REMITO_CREATE", f"Ingresó Remito {data.remito_number} (Estado: {initial_status})")
        return {"status": "success", "message": f"Remito registrado correctamente en estado {initial_status}."}

@app.get("/api/admin/purchase-invoices")
async def list_purchase_invoices(search: Optional[str] = None, limit: int = 5, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    if search and search.strip():
        where = " WHERE UPPER(i.invoice_number) LIKE $1 OR UPPER(e.company_name) LIKE $1 "
        params = [f"%{search.strip().upper()}%", limit]
        q = f"SELECT i.id, i.invoice_number, i.invoice_type, i.created_at, COALESCE(e.company_name, 'Sin Proveedor') as supplier_name FROM purchase_invoices i LEFT JOIN entities e ON i.supplier_id = e.id {where} ORDER BY i.created_at DESC LIMIT $2"
    else:
        params = [limit]
        q = f"SELECT i.id, i.invoice_number, i.invoice_type, i.created_at, COALESCE(e.company_name, 'Sin Proveedor') as supplier_name FROM purchase_invoices i LEFT JOIN entities e ON i.supplier_id = e.id ORDER BY i.created_at DESC LIMIT $1"
    return [dict(r) for r in await conn.fetch(q, *params)]

@app.post("/api/admin/purchase-invoices")
async def create_purchase_invoice(data: InvoiceCreateInput, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    async with conn.transaction():
        inv_id = await conn.fetchval("INSERT INTO purchase_invoices (invoice_number, supplier_id, invoice_type) VALUES ($1, $2, $3) RETURNING id", data.invoice_number.strip().upper(), uuid.UUID(data.supplier_id), data.invoice_type)
        if data.manual_items:
            if not data.branch_id or not data.sector_id: raise HTTPException(status_code=400, detail="Debe especificar sucursal y sector para ingresar ítems manuales a stock.")
            for item in data.manual_items:
                await conn.execute("INSERT INTO purchase_invoice_lines (purchase_invoice_id, sku, quantity, unit_price) VALUES ($1, $2, $3, $4)", inv_id, item.sku.strip().upper(), item.quantity, item.unit_price)
                await record_stock_movement(conn, item.sku, uuid.UUID(data.branch_id), uuid.UUID(data.sector_id), None, item.quantity, 'IN_FACTURA_MANUAL', data.invoice_number, admin.get("sub"))
        for r_id in data.remito_ids: await conn.execute("INSERT INTO purchase_invoice_remitos (purchase_invoice_id, purchase_remito_id) VALUES ($1, $2)", inv_id, uuid.UUID(r_id))
        for po_id in data.po_ids: await conn.execute("INSERT INTO purchase_invoice_orders (purchase_invoice_id, purchase_order_id) VALUES ($1, $2)", inv_id, uuid.UUID(po_id))
        await log_action(conn, admin.get("sub"), "INVOICE_CREATE", f"Registró Factura de Compra {data.invoice_number}")
        return {"status": "success", "message": "Factura de Compra registrada exitosamente."}

@app.get("/api/admin/transfer-orders")
async def list_transfer_orders(search: Optional[str] = None, limit: int = 5, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    if search and search.strip():
        where = " WHERE UPPER(t.transfer_number) LIKE $1 OR UPPER(ob.name) LIKE $1 OR UPPER(db.name) LIKE $1 "
        params = [f"%{search.strip().upper()}%", limit]
        q = f"SELECT t.id, t.transfer_number, t.status, t.created_at, COALESCE(ob.name, 'N/A') as origin_branch, COALESCE(os.name, 'N/A') as origin_sector, COALESCE(db.name, 'N/A') as destination_branch, COALESCE(ds.name, 'N/A') as destination_sector FROM transfer_orders t LEFT JOIN branches ob ON t.origin_branch_id = ob.id LEFT JOIN sectors os ON t.origin_sector_id = os.id LEFT JOIN branches db ON t.destination_branch_id = db.id LEFT JOIN sectors ds ON t.destination_sector_id = ds.id {where} ORDER BY t.created_at DESC LIMIT $2"
    else:
        params = [limit]
        q = f"SELECT t.id, t.transfer_number, t.status, t.created_at, COALESCE(ob.name, 'N/A') as origin_branch, COALESCE(os.name, 'N/A') as origin_sector, COALESCE(db.name, 'N/A') as destination_branch, COALESCE(ds.name, 'N/A') as destination_sector FROM transfer_orders t LEFT JOIN branches ob ON t.origin_branch_id = ob.id LEFT JOIN sectors os ON t.origin_sector_id = os.id LEFT JOIN branches db ON t.destination_branch_id = db.id LEFT JOIN sectors ds ON t.destination_sector_id = ds.id ORDER BY t.created_at DESC LIMIT $1"
    return [dict(r) for r in await conn.fetch(q, *params)]

@app.post("/api/admin/transfer-orders")
async def create_transfer_order(data: TransferCreateInput, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    if data.origin_sector_id == data.destination_sector_id: raise HTTPException(status_code=400, detail="El sector de origen y destino deben ser diferentes.")
    async with conn.transaction():
        require_mobile = await conn.fetchval("SELECT value FROM system_settings WHERE key = 'require_mobile_reception'")
        initial_status = "PENDING_CONTROL" if require_mobile == "true" else "COMPLETED"
        transfer_id = await conn.fetchval("INSERT INTO transfer_orders (transfer_number, origin_branch_id, origin_sector_id, destination_branch_id, destination_sector_id, status) VALUES ($1, $2, $3, $4, $5, $6) RETURNING id", data.transfer_number.strip().upper(), uuid.UUID(data.origin_branch_id), uuid.UUID(data.origin_sector_id), uuid.UUID(data.destination_branch_id), uuid.UUID(data.destination_sector_id), initial_status)
        for line in data.lines:
            orig_loc_id = await conn.fetchval("SELECT id FROM locations WHERE sector_id = $1 AND UPPER(location_code) = $2", uuid.UUID(data.origin_sector_id), line.origin_location_code.strip().upper()) if line.origin_location_code else None
            dest_loc_id = await conn.fetchval("SELECT id FROM locations WHERE sector_id = $1 AND UPPER(location_code) = $2", uuid.UUID(data.destination_sector_id), line.destination_location_code.strip().upper()) if line.destination_location_code else None
            avail = await conn.fetchval("SELECT COALESCE(SUM(quantity), 0) FROM stock_inventory WHERE branch_id = $1 AND sector_id = $2 AND sku = $3 AND (location_id = $4 OR ($4 IS NULL AND location_id IS NULL))", uuid.UUID(data.origin_branch_id), uuid.UUID(data.origin_sector_id), line.sku.strip().upper(), orig_loc_id)
            if avail < line.quantity_sent: raise HTTPException(status_code=400, detail=f"Stock insuficiente para {line.sku.strip().upper()} en origen.")
            await conn.execute("INSERT INTO transfer_order_lines (transfer_order_id, sku, quantity_sent, quantity_received, origin_location_id, destination_location_id) VALUES ($1, $2, $3, $4, $5, $6)", transfer_id, line.sku.strip().upper(), line.quantity_sent, (0 if require_mobile == "true" else line.quantity_sent), orig_loc_id, dest_loc_id)
            await record_stock_movement(conn, line.sku.strip().upper(), uuid.UUID(data.origin_branch_id), uuid.UUID(data.origin_sector_id), orig_loc_id, -line.quantity_sent, 'OUT_TRANSFER', data.transfer_number, admin.get("sub"))
            if require_mobile != "true": await record_stock_movement(conn, line.sku.strip().upper(), uuid.UUID(data.destination_branch_id), uuid.UUID(data.destination_sector_id), dest_loc_id, line.quantity_sent, 'IN_TRANSFER', data.transfer_number, admin.get("sub"))
        await log_action(conn, admin.get("sub"), "TRANSFER_CREATE", f"Orden de traspaso {data.transfer_number} registrada (Estado: {initial_status})")
        return {"status": "success", "message": f"Orden de traspaso registrada en estado {initial_status}."}

# === INVENTARIO, KARDEX Y ARTÍCULOS ===
@app.get("/api/admin/stock")
async def get_stock_summary(admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)): return [dict(r) for r in await conn.fetch("SELECT i.sku, item.description, b.name as branch_name, s.name as sector_name, COALESCE(l.location_code, 'Sin Ubicación') as location_code, i.quantity, i.updated_at FROM stock_inventory i JOIN items item ON UPPER(i.sku) = UPPER(item.sku) JOIN branches b ON i.branch_id = b.id JOIN sectors s ON i.sector_id = s.id LEFT JOIN locations l ON i.location_id = l.id WHERE i.quantity > 0 ORDER BY b.name, s.name, l.location_code, i.sku")]

@app.get("/api/admin/stock/kardex")
async def get_stock_kardex(admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)): return [dict(r) for r in await conn.fetch("SELECT m.id, m.sku, b.name as branch_name, s.name as sector_name, COALESCE(l.location_code, 'N/A') as location_code, m.quantity, m.movement_type, m.reference_document, m.username, m.created_at FROM stock_movements m JOIN branches b ON m.branch_id = b.id JOIN sectors s ON m.sector_id = s.id LEFT JOIN locations l ON m.location_id = l.id ORDER BY m.created_at DESC LIMIT 200")]

@app.get("/api/admin/items")
async def list_items(sku: Optional[str] = None, description: Optional[str] = None, category: Optional[str] = None, location: Optional[str] = None, sort_by: Optional[str] = "sku", sort_order: Optional[str] = "ASC", page: int = 1, limit: int = 50, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    valid_sort_columns = { "sku": "i.sku", "description": "i.description", "category": "i.category", "weight": "i.weight", "volume": "i.volume" }
    sort_col = valid_sort_columns.get(sort_by.lower() if sort_by else "sku", "i.sku"); order = "DESC" if sort_order and sort_order.upper() == "DESC" else "ASC"; offset = (page - 1) * limit; base_from = " FROM items i "
    conditions, args, arg_idx = [], [], 1
    if sku and sku.strip(): conditions.append(f"i.sku ILIKE ${arg_idx}"); args.append(f"%{sku.strip()}%"); arg_idx += 1
    if description and description.strip(): conditions.append(f"i.description ILIKE ${arg_idx}"); args.append(f"%{description.strip()}%"); arg_idx += 1
    if category and category.strip(): conditions.append(f"i.category ILIKE ${arg_idx}"); args.append(f"%{category.strip()}%"); arg_idx += 1
    if location and location.strip(): conditions.append(f"EXISTS (SELECT 1 FROM item_locations il JOIN locations l ON il.location_id = l.id WHERE UPPER(il.item_sku) = UPPER(i.sku) AND l.location_code ILIKE ${arg_idx})"); args.append(f"%{location.strip()}%"); arg_idx += 1
    where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""
    count_query = f"SELECT COUNT(*) {base_from} {where_clause}"
    total_items = await conn.fetchval(count_query, *args) if args else await conn.fetchval(count_query)
    query = f"SELECT i.sku, i.description, i.category, i.weight, i.length, i.width, i.height, i.volume, i.is_active, COALESCE((SELECT string_agg(l.location_code, ', ') FROM item_locations il JOIN locations l ON il.location_id = l.id WHERE UPPER(il.item_sku) = UPPER(i.sku)), 'Sin asignación') as locations_summary {base_from} {where_clause} ORDER BY {sort_col} {order} LIMIT ${arg_idx} OFFSET ${arg_idx + 1}"
    args.extend([limit, offset])
    return { "items": [dict(r) for r in await conn.fetch(query, *args)], "total": total_items, "page": page, "limit": limit }

@app.post("/api/admin/items/batch-print")
async def batch_print_items(items: List[BatchItemPrintInput], admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    template = await conn.fetchval("SELECT value FROM system_settings WHERE key = 'zpl_item_template'")
    if not template: template = "^XA^FO50,50^A0N,30,30^FD{sku}^FS^FO50,90^A0N,20,20^FD{description}^FS^XZ"
    total_labels = 0
    async with conn.transaction():
        for itm in items:
            db_item = await conn.fetchrow("SELECT sku, description FROM items WHERE UPPER(sku) = $1", itm.sku.strip().upper())
            if not db_item: continue
            zpl = template.replace("{sku}", str(db_item["sku"])).replace("{{SKU}}", str(db_item["sku"])).replace("{description}", str(db_item["description"])).replace("{{DESC}}", str(db_item["description"]))
            if "^PQ" not in zpl: zpl = zpl.replace("^XZ", f"\n^PQ{itm.quantity}\n^XZ")
            await conn.execute("INSERT INTO item_print_jobs (job_code, zpl_content) VALUES ($1, $2)", f"PRN-{uuid.uuid4().hex[:8].upper()}", zpl)
            total_labels += itm.quantity
    return {"status": "success", "total_labels": total_labels}

@app.put("/api/admin/items/{sku}")
async def update_item(sku: str, data: ItemUpdate, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    item = await conn.fetchrow("SELECT sku FROM items WHERE UPPER(sku) = $1", sku.strip().upper())
    if not item: raise HTTPException(status_code=404, detail="Artículo no encontrado")
    updates, values, idx = [], [], 1
    for field, val in data.model_dump(exclude_unset=True).items(): updates.append(f"{field} = ${idx}"); values.append(val); idx += 1
    if updates: values.append(sku.strip().upper()); await conn.execute(f"UPDATE items SET {', '.join(updates)} WHERE UPPER(sku) = ${idx}", *values)
    return {"status": "success", "message": "Artículo actualizado"}

@app.post("/api/admin/import/items")
async def import_items(file: UploadFile = File(...), admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    if not file.filename.endswith('.csv'): raise HTTPException(status_code=400, detail="Utilice un archivo .csv")
    try: csv_text = (await file.read(MAX_FILE_SIZE + 1)).decode("utf-8-sig")
    except: raise HTTPException(status_code=400, detail="Codificación inválida.")
    try: f = StringIO(csv_text); dialect = csv.Sniffer().sniff(csv_text[:1024], delimiters=[',', ';', '\t']); f.seek(0); reader = csv.reader(f, dialect=dialect)
    except: f.seek(0); reader = csv.reader(f, delimiter=',')
    headers = [h.strip().lower() for h in next(reader, [])]
    idx_sku, idx_desc, idx_cat = headers.index('sku'), headers.index('descripcion'), headers.index('categoria')
    async with conn.transaction():
        for row in reader:
            if not row or len(row) <= idx_cat: continue
            await conn.execute("INSERT INTO items (sku, description, category) VALUES ($1, $2, $3) ON CONFLICT (sku) DO UPDATE SET description = EXCLUDED.description, category = EXCLUDED.category, is_active = TRUE", sanitize_csv_value(row[idx_sku])[:100].upper(), sanitize_csv_value(row[idx_desc]), sanitize_csv_value(row[idx_cat])[:100])
    return {"status": "success", "message": "Importación exitosa."}

@app.get("/api/admin/documents")
async def list_documents(admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    return [ { **dict(r), "progress_pct": round((r['total_picked'] / r['total_req'] * 100) if r['total_req'] > 0 else 0, 1) } for r in await conn.fetch("SELECT d.id, d.document_number, d.document_type, d.status, d.label_printed, d.created_at, COALESCE(c.company_name, 'Sin cliente') as company_name, COALESCE(SUM(dl.quantity_requested), 0) as total_req, COALESCE(SUM(dl.quantity_picked), 0) as total_picked FROM documents d LEFT JOIN entities c ON d.customer_id = c.id LEFT JOIN document_lines dl ON d.id = dl.document_id GROUP BY d.id, c.company_name ORDER BY d.created_at DESC") ]

# === OPERATIVA MÓVIL Y PICKING ===
@app.get("/api/picking/orders")
async def get_picking_mailbox(user: dict = Depends(get_current_user), conn: asyncpg.Connection = Depends(get_db_connection)):
    rows = await conn.fetch("SELECT d.document_number, d.status, COALESCE(c.company_name, 'Cliente Faltante') as company_name, COALESCE(a.address_label, 'N/A') as address_label FROM documents d LEFT JOIN entities c ON d.customer_id = c.id LEFT JOIN entity_addresses a ON d.customer_address_id = a.id WHERE d.status IN ('PENDING', 'IN_PROGRESS') ORDER BY d.created_at ASC")
    return [dict(r) for r in rows]

@app.get("/api/picking/orders/{document_number}")
async def get_picking_order_details(document_number: str, user: dict = Depends(get_current_user), conn: asyncpg.Connection = Depends(get_db_connection)):
    doc = await conn.fetchrow("SELECT id, document_number, status FROM documents WHERE UPPER(document_number) = UPPER($1)", document_number.strip())
    if not doc: raise HTTPException(404, "Pedido no encontrado")
    lines_raw = await conn.fetch("SELECT dl.id, dl.sku, dl.quantity_requested, dl.quantity_picked, COALESCE((SELECT string_agg(l.location_code || ' (' || si.quantity || ' un)', ' | ') FROM stock_inventory si JOIN locations l ON si.location_id = l.id WHERE UPPER(si.sku) = UPPER(dl.sku) AND si.quantity > 0), 'Sin stock ubicado') as suggested_locations FROM document_lines dl WHERE dl.document_id = $1 ORDER BY dl.sku ASC", doc["id"])
    return {"document": dict(doc), "lines": [dict(l) for l in lines_raw]}

@app.post("/api/picking/orders/{document_number}/scan")
async def scan_picking_item(document_number: str, data: PickScanInput, user: dict = Depends(get_current_user), conn: asyncpg.Connection = Depends(get_db_connection)):
    async with conn.transaction():
        doc = await conn.fetchrow("SELECT id, status FROM documents WHERE UPPER(document_number) = UPPER($1) FOR UPDATE", document_number.strip())
        if not doc: raise HTTPException(404, "Pedido no encontrado")
        if doc["status"] == "COMPLETED": raise HTTPException(400, "El pedido ya está completado.")
        sku_clean = data.sku.strip().upper()
        line = await conn.fetchrow("SELECT id, quantity_requested, quantity_picked FROM document_lines WHERE document_id = $1 AND UPPER(sku) = $2", doc["id"], sku_clean)
        if not line: raise HTTPException(400, f"El SKU {sku_clean} no pertenece a este pedido.")
        loc = await conn.fetchrow("SELECT l.id, l.sector_id, s.branch_id FROM locations l JOIN sectors s ON l.sector_id = s.id WHERE UPPER(l.location_code) = $1", data.location_code.strip().upper())
        if not loc: raise HTTPException(400, "La ubicación de extracción escaneada no existe en el sistema.")
        avail = await conn.fetchval("SELECT COALESCE(SUM(quantity), 0) FROM stock_inventory WHERE branch_id = $1 AND sector_id = $2 AND sku = $3 AND location_id = $4", loc["branch_id"], loc["sector_id"], sku_clean, loc["id"])
        if avail < data.quantity: raise HTTPException(400, f"Stock insuficiente en la ubicación {data.location_code}. Disponible: {avail}")
        new_picked = float(line["quantity_picked"]) + data.quantity
        if new_picked > float(line["quantity_requested"]): raise HTTPException(400, f"Se requieren solo {line['quantity_requested']} un.")
        await conn.execute("UPDATE document_lines SET quantity_picked = $1 WHERE id = $2", new_picked, line["id"])
        await record_stock_movement(conn, sku_clean, loc["branch_id"], loc["sector_id"], loc["id"], -data.quantity, 'OUT_PICKING', document_number.strip(), user.get("sub"))
        if doc["status"] == "PENDING": await conn.execute("UPDATE documents SET status = 'IN_PROGRESS' WHERE id = $1", doc["id"])
        pending_lines = await conn.fetchval("SELECT COUNT(*) FROM document_lines WHERE document_id = $1 AND quantity_picked < quantity_requested", doc["id"])
        if pending_lines == 0: await conn.execute("UPDATE documents SET status = 'COMPLETED' WHERE id = $1", doc["id"])
        await log_action(conn, user.get("sub"), "PICKING_SCAN", f"Pickeó {data.quantity} un. de {sku_clean} para pedido {document_number.strip()}")
        return {"status": "success", "message": f"Extraído: {sku_clean} ({data.quantity} un.)", "order_completed": pending_lines == 0}

@app.get("/api/mobile/receptions")
async def get_pending_receptions(user: dict = Depends(get_current_user), conn: asyncpg.Connection = Depends(get_db_connection)):
    rows = await conn.fetch("""
        SELECT r.id, r.remito_number, r.status,
               COALESCE(e.company_name, 'Sin Proveedor') as supplier_name,
               COALESCE(b.name, 'Sin Sucursal') as branch_name,
               COALESCE(s.name, 'Sin Sector') as sector_name,
               r.created_at
        FROM purchase_remitos r
        LEFT JOIN entities e ON r.supplier_id = e.id
        LEFT JOIN branches b ON r.branch_id = b.id
        LEFT JOIN sectors s ON r.sector_id = s.id
        WHERE r.status != 'COMPLETED'
        ORDER BY r.created_at ASC
    """)
    return [dict(r) for r in rows]

@app.get("/api/mobile/receptions/{identifier}")
async def get_mobile_reception_details(identifier: str, user: dict = Depends(get_current_user), conn: asyncpg.Connection = Depends(get_db_connection)):
    clean_id = identifier.strip()
    rem = None
    try:
        u_id = uuid.UUID(clean_id)
        rem = await conn.fetchrow("SELECT id, remito_number, status FROM purchase_remitos WHERE id = $1", u_id)
    except ValueError:
        pass

    if not rem:
        rem = await conn.fetchrow("SELECT id, remito_number, status FROM purchase_remitos WHERE UPPER(remito_number) = UPPER($1)", clean_id)

    if not rem: raise HTTPException(404, detail=f"Remito '{clean_id}' no encontrado.")
    lines = await conn.fetch("SELECT id, sku, quantity_sent, quantity_received FROM purchase_remito_lines WHERE purchase_remito_id = $1 ORDER BY sku ASC", rem["id"])
    return {"remito": dict(rem), "lines": [dict(l) for l in lines]}

@app.post("/api/mobile/receptions/scan")
async def scan_reception_item(data: MobileRemitoScanInput, user: dict = Depends(get_current_user), conn: asyncpg.Connection = Depends(get_db_connection)):
    async with conn.transaction():
        clean_id = data.remito_number.strip()
        rem = None
        try:
            u_id = uuid.UUID(clean_id)
            rem = await conn.fetchrow("SELECT r.id, r.remito_number, r.branch_id, r.sector_id, r.status FROM purchase_remitos r WHERE r.id = $1", u_id)
        except ValueError:
            pass

        if not rem:
            rem = await conn.fetchrow("SELECT r.id, r.remito_number, r.branch_id, r.sector_id, r.status FROM purchase_remitos r WHERE UPPER(r.remito_number) = UPPER($1)", clean_id)

        if not rem: raise HTTPException(status_code=404, detail="Remito no encontrado.")
        if rem["status"] == "COMPLETED": raise HTTPException(status_code=400, detail="El remito ya fue completado.")

        sku_clean = data.sku.strip().upper()
        line = await conn.fetchrow("SELECT id, quantity_sent, quantity_received FROM purchase_remito_lines WHERE purchase_remito_id = $1 AND UPPER(sku) = $2", rem["id"], sku_clean)
        if not line: raise HTTPException(status_code=400, detail=f"El SKU {sku_clean} no pertenece a este remito.")
        loc_id = await conn.fetchval("SELECT id FROM locations WHERE sector_id = $1 AND UPPER(location_code) = $2", rem["sector_id"], data.location_code.strip().upper()) if data.location_code else None
        new_received = float(line["quantity_received"]) + data.quantity
        await conn.execute("UPDATE purchase_remito_lines SET quantity_received = $1, location_id = COALESCE($2, location_id) WHERE id = $3", new_received, loc_id, line["id"])
        await record_stock_movement(conn, sku_clean, rem["branch_id"], rem["sector_id"], loc_id, data.quantity, 'IN_REMITO_MOBILE', rem["remito_number"], user.get("sub"))
        pending_lines = await conn.fetchval("SELECT COUNT(*) FROM purchase_remito_lines WHERE purchase_remito_id = $1 AND quantity_received < quantity_sent", rem["id"])
        if pending_lines == 0: await conn.execute("UPDATE purchase_remitos SET status = 'COMPLETED' WHERE id = $1", rem["id"])
        await log_action(conn, user.get("sub"), "MOBILE_RECEPTION", f"Controló {data.quantity} un. de {sku_clean} para remito {rem['remito_number']}")
        return {"status": "success", "message": f"Escaneo registrado. SKU {sku_clean} +{data.quantity}", "remito_completed": pending_lines == 0}

@app.get("/api/mobile/transfers")
async def get_pending_transfers(user: dict = Depends(get_current_user), conn: asyncpg.Connection = Depends(get_db_connection)):
    rows = await conn.fetch("""
        SELECT t.id, t.transfer_number, t.status,
               COALESCE(ob.name, 'N/A') as origin_branch,
               COALESCE(os.name, 'N/A') as origin_sector,
               COALESCE(db.name, 'N/A') as destination_branch,
               COALESCE(ds.name, 'N/A') as destination_sector,
               t.created_at
        FROM transfer_orders t
        LEFT JOIN branches ob ON t.origin_branch_id = ob.id
        LEFT JOIN sectors os ON t.origin_sector_id = os.id
        LEFT JOIN branches db ON t.destination_branch_id = db.id
        LEFT JOIN sectors ds ON t.destination_sector_id = ds.id
        WHERE t.status != 'COMPLETED'
        ORDER BY t.created_at ASC
    """)
    return [dict(r) for r in rows]

@app.get("/api/mobile/transfers/{identifier}")
async def get_mobile_transfer_details(identifier: str, user: dict = Depends(get_current_user), conn: asyncpg.Connection = Depends(get_db_connection)):
    clean_id = identifier.strip()
    tr = None
    try:
        u_id = uuid.UUID(clean_id)
        tr = await conn.fetchrow("SELECT id, transfer_number, status FROM transfer_orders WHERE id = $1", u_id)
    except ValueError:
        pass

    if not tr:
        tr = await conn.fetchrow("SELECT id, transfer_number, status FROM transfer_orders WHERE UPPER(transfer_number) = UPPER($1)", clean_id)

    if not tr: raise HTTPException(404, detail="Traspaso no encontrado")
    lines = await conn.fetch("SELECT id, sku, quantity_sent, quantity_received FROM transfer_order_lines WHERE transfer_order_id = $1 ORDER BY sku ASC", tr["id"])
    return {"transfer": dict(tr), "lines": [dict(l) for l in lines]}

@app.post("/api/mobile/transfers/scan")
async def scan_transfer_item(data: MobileTransferScanInput, user: dict = Depends(get_current_user), conn: asyncpg.Connection = Depends(get_db_connection)):
    async with conn.transaction():
        clean_id = data.transfer_number.strip()
        tr = None
        try:
            u_id = uuid.UUID(clean_id)
            tr = await conn.fetchrow("SELECT id, transfer_number, destination_branch_id, destination_sector_id, status FROM transfer_orders WHERE id = $1", u_id)
        except ValueError:
            pass

        if not tr:
            tr = await conn.fetchrow("SELECT id, transfer_number, destination_branch_id, destination_sector_id, status FROM transfer_orders WHERE UPPER(transfer_number) = UPPER($1)", clean_id)

        if not tr: raise HTTPException(status_code=404, detail="Traspaso no encontrado.")
        if tr["status"] == "COMPLETED": raise HTTPException(status_code=400, detail="El traspaso ya fue completado.")

        sku_clean = data.sku.strip().upper()
        line = await conn.fetchrow("SELECT id, quantity_sent, quantity_received FROM transfer_order_lines WHERE transfer_order_id = $1 AND UPPER(sku) = $2", tr["id"], sku_clean)
        if not line: raise HTTPException(status_code=400, detail=f"El SKU {sku_clean} no pertenece a este traspaso.")
        dest_loc_id = await conn.fetchval("SELECT id FROM locations WHERE sector_id = $1 AND UPPER(location_code) = $2", tr["destination_sector_id"], data.destination_location_code.strip().upper()) if data.destination_location_code else None
        new_received = float(line["quantity_received"]) + data.quantity
        await conn.execute("UPDATE transfer_order_lines SET quantity_received = $1, destination_location_id = COALESCE($2, destination_location_id) WHERE id = $3", new_received, dest_loc_id, line["id"])
        await record_stock_movement(conn, sku_clean, tr["destination_branch_id"], tr["destination_sector_id"], dest_loc_id, data.quantity, 'IN_TRANSFER_MOBILE', tr["transfer_number"], user.get("sub"))
        pending_lines = await conn.fetchval("SELECT COUNT(*) FROM transfer_order_lines WHERE transfer_order_id = $1 AND quantity_received < quantity_sent", tr["id"])
        if pending_lines == 0: await conn.execute("UPDATE transfer_orders SET status = 'COMPLETED' WHERE id = $1", tr["id"])
        return {"status": "success", "message": f"Escaneo de traspaso registrado para {sku_clean}", "transfer_completed": pending_lines == 0}

@app.post("/api/mobile/assign-location")
async def mobile_assign_location(data: ItemLocationInput, user: dict = Depends(get_current_user), conn: asyncpg.Connection = Depends(get_db_connection)):
    sku_clean, loc_clean = data.sku.strip().upper(), data.location_code.strip().upper()
    item = await conn.fetchrow("SELECT sku FROM items WHERE UPPER(sku) = $1", sku_clean)
    if not item: raise HTTPException(400, f"El SKU {sku_clean} no existe.")
    loc = await conn.fetchrow("SELECT id, location_code FROM locations WHERE UPPER(location_code) = $1", loc_clean)
    if not loc: raise HTTPException(400, f"La ubicación {loc_clean} no existe.")
    await conn.execute("INSERT INTO item_locations (item_sku, location_id) VALUES ($1, $2) ON CONFLICT (item_sku, location_id) DO NOTHING", sku_clean, loc["id"])
    await log_action(conn, user.get("sub"), "MOBILE_ASSIGN_LOC", f"Asignó ubicación {loc_clean} a SKU {sku_clean}")
    return {"status": "success", "message": f"Ubicación {loc_clean} asignada a {sku_clean}."}

@app.post("/api/mobile/relocate")
async def relocate_stock_mobile(data: MobileRelocateInput, user: dict = Depends(get_current_user), conn: asyncpg.Connection = Depends(get_db_connection)):
    if data.quantity <= 0: raise HTTPException(status_code=400, detail="La cantidad debe ser mayor a 0.")
    async with conn.transaction():
        sku_clean = data.sku.strip().upper()
        orig = await conn.fetchrow("SELECT l.id, l.sector_id, s.branch_id FROM locations l JOIN sectors s ON l.sector_id = s.id WHERE UPPER(l.location_code) = $1", data.origin_location_code.strip().upper())
        if not orig: raise HTTPException(400, f"Ubicación origen {data.origin_location_code} no existe.")
        dest = await conn.fetchrow("SELECT l.id, l.sector_id, s.branch_id FROM locations l JOIN sectors s ON l.sector_id = s.id WHERE UPPER(l.location_code) = $1", data.destination_location_code.strip().upper())
        if not dest: raise HTTPException(400, f"Ubicación destino {data.destination_location_code} no existe.")
        avail = await conn.fetchval("SELECT COALESCE(SUM(quantity), 0) FROM stock_inventory WHERE branch_id = $1 AND sector_id = $2 AND sku = $3 AND location_id = $4", orig["branch_id"], orig["sector_id"], sku_clean, orig["id"])
        if avail < data.quantity: raise HTTPException(status_code=400, detail=f"Stock insuficiente para {sku_clean} en origen. Disponible: {avail}")
        doc_ref = f"MOVER-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        await record_stock_movement(conn, sku_clean, orig["branch_id"], orig["sector_id"], orig["id"], -data.quantity, 'RELOCATION_OUT', doc_ref, user.get("sub"))
        await record_stock_movement(conn, sku_clean, dest["branch_id"], dest["sector_id"], dest["id"], data.quantity, 'RELOCATION_IN', doc_ref, user.get("sub"))
        await log_action(conn, user.get("sub"), "MOBILE_RELOCATE", f"Movió {data.quantity} un. de {sku_clean} de {data.origin_location_code} a {data.destination_location_code}")
        return {"status": "success", "message": f"Mercadería movida exitosamente a {data.destination_location_code}."}

@app.get("/api/mobile/lookup")
async def quick_lookup(query: str, user: dict = Depends(get_current_user), conn: asyncpg.Connection = Depends(get_db_connection)):
    q = query.strip().upper()
    item = await conn.fetchrow("SELECT sku, description, category FROM items WHERE UPPER(sku) = $1", q)
    if item:
        stock_rows = await conn.fetch("SELECT b.name as branch_name, s.name as sector_name, COALESCE(l.location_code, 'Sin Ubicación') as location_code, i.quantity FROM stock_inventory i JOIN branches b ON i.branch_id = b.id JOIN sectors s ON i.sector_id = s.id LEFT JOIN locations l ON i.location_id = l.id WHERE UPPER(i.sku) = $1 AND i.quantity > 0", q)
        return { "type": "SKU", "sku": item["sku"], "description": item["description"], "category": item["category"], "stock": [dict(r) for r in stock_rows] }
    loc = await conn.fetchrow("SELECT l.id, l.location_code, l.description, s.name as sector_name, b.name as branch_name FROM locations l JOIN sectors s ON l.sector_id = s.id JOIN branches b ON s.branch_id = b.id WHERE UPPER(l.location_code) = $1", q)
    if loc:
        stock_rows = await conn.fetch("SELECT i.sku, item.description, i.quantity FROM stock_inventory i JOIN items item ON UPPER(i.sku) = UPPER(item.sku) WHERE i.location_id = $1 AND i.quantity > 0", loc["id"])
        return { "type": "LOCATION", "location_code": loc["location_code"], "description": loc["description"], "branch_name": loc["branch_name"], "sector_name": loc["sector_name"], "items": [dict(r) for r in stock_rows] }
    return {"type": "NOT_FOUND", "message": f"No se encontró información para '{query}'."}

# === AUTENTICACIÓN Y USUARIOS ===
@app.post("/api/auth/login")
async def login(response: Response, credentials: LoginRequest, conn: asyncpg.Connection = Depends(get_db_connection)):
    user = await conn.fetchrow("SELECT id, username, password_hash, role, is_active FROM users WHERE username = $1", credentials.username.strip().lower())
    if not user or not user["is_active"] or not verify_password(credentials.password, user["password_hash"]): raise HTTPException(status_code=401, detail="Credenciales incorrectas.")
    response.set_cookie(key="access_token", value=f"Bearer {create_access_token({'sub': user['username'], 'role': user['role'], 'id': str(user['id'])})}", httponly=True, secure=False, samesite="lax", max_age=28800)
    return {"message": "Éxito", "role": user["role"]}

@app.post("/api/auth/logout")
async def logout(response: Response): response.delete_cookie("access_token"); return {"message": "Éxito"}

@app.get("/api/admin/users")
async def list_users(admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS email VARCHAR(255); ALTER TABLE users ADD COLUMN IF NOT EXISTS branch_id VARCHAR(255); ALTER TABLE users ADD COLUMN IF NOT EXISTS sector_id VARCHAR(255);")
    return [dict(u) for u in await conn.fetch("SELECT u.id, u.username, u.full_name, u.role, u.is_active, u.email, u.branch_id, u.sector_id, b.name as branch_name, s.name as sector_name FROM users u LEFT JOIN branches b ON u.branch_id = b.id::text LEFT JOIN sectors s ON u.sector_id = s.id::text ORDER BY u.created_at DESC")]

@app.post("/api/admin/users")
async def create_or_update_user(data: UserCreate, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    await conn.execute("INSERT INTO users (username, full_name, password_hash, role, email, branch_id, sector_id) VALUES ($1, $2, $3, $4, $5, $6, $7) ON CONFLICT (username) DO UPDATE SET full_name = EXCLUDED.full_name, password_hash = EXCLUDED.password_hash, role = EXCLUDED.role, email = EXCLUDED.email, branch_id = EXCLUDED.branch_id, sector_id = EXCLUDED.sector_id, is_active = TRUE", data.username.strip().lower(), data.full_name.strip(), get_password_hash(data.password), data.role, data.email, data.branch_id, data.sector_id)
    return {"status": "success"}

@app.put("/api/admin/users/{username}")
async def update_user(username: str, data: UserUpdate, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    user = await conn.fetchrow("SELECT id FROM users WHERE LOWER(username) = LOWER($1)", username)
    if not user: return {"status": "error", "message": "Usuario no encontrado"}
    updates, params, idx = [], [username.lower()], 2
    for k, v in data.model_dump(exclude_unset=True).items():
        if k == 'password' and v: updates.append(f"password_hash = ${idx}"); params.append(get_password_hash(v)); idx += 1
        elif k != 'password': updates.append(f"{k} = ${idx}"); params.append(v.strip() if isinstance(v, str) else v); idx += 1
    if updates: await conn.execute(f"UPDATE users SET {', '.join(updates)} WHERE LOWER(username) = $1", *params)
    return {"status": "success"}

@app.get("/api/admin/logs")
async def list_logs(admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)): return [dict(l) for l in await conn.fetch("SELECT username, action, details, created_at FROM audit_logs ORDER BY created_at DESC LIMIT 100")]

@app.get("/api/admin/print-queue")
async def get_print_queue(conn: asyncpg.Connection = Depends(get_db_connection)): return [dict(r) for r in await conn.fetch("SELECT id, job_code, zpl_content FROM item_print_jobs WHERE label_printed = FALSE ORDER BY created_at ASC")]

@app.put("/api/admin/print-queue/{identifier}")
async def mark_printed(identifier: str, conn: asyncpg.Connection = Depends(get_db_connection)):
    try: await conn.execute("UPDATE item_print_jobs SET label_printed = TRUE WHERE id = $1", uuid.UUID(identifier))
    except ValueError: await conn.execute("UPDATE item_print_jobs SET label_printed = TRUE WHERE job_code = $1", identifier)
    return {"status": "success"}

app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")