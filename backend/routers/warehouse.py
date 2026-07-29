from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel
from typing import Optional
import asyncpg, uuid, csv
from io import StringIO

try:
    from backend.database import get_db_connection, require_admin
except ImportError:
    from database import get_db_connection, require_admin

router = APIRouter(tags=["Warehouse"])

class BranchCreate(BaseModel):
    code: str
    name: str

class SectorCreate(BaseModel):
    name: str
    print_queue_code: str
    uses_locations: bool
    branch_id: Optional[str] = None

class LocationCreate(BaseModel):
    sector_id: str
    location_code: str
    description: Optional[str] = None

@router.get("/api/admin/branches")
async def list_branches(admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    return [dict(r) for r in await conn.fetch("SELECT id, code, name, is_active FROM branches ORDER BY name ASC")]

@router.post("/api/admin/branches")
async def create_branch(data: BranchCreate, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    await conn.execute("INSERT INTO branches (code, name) VALUES ($1, $2)", data.code.strip().upper(), data.name.strip())
    return {"status": "success"}

@router.get("/api/admin/sectors")
async def list_sectors(admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    return [dict(r) for r in await conn.fetch("SELECT s.id, s.name, s.print_queue_code, s.uses_locations, s.branch_id, b.name as branch_name FROM sectors s LEFT JOIN branches b ON s.branch_id = b.id ORDER BY s.name ASC")]

@router.post("/api/admin/sectors")
async def create_sector(data: SectorCreate, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    await conn.execute("INSERT INTO sectors (name, print_queue_code, uses_locations, branch_id) VALUES ($1, $2, $3, $4)", data.name.strip(), data.print_queue_code.strip().upper(), data.uses_locations, uuid.UUID(data.branch_id))
    return {"status": "success"}

@router.get("/api/admin/locations")
async def list_all_locations(admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    return [dict(l) for l in await conn.fetch("SELECT l.id, l.location_code, l.description, s.name as sector_name, b.name as branch_name FROM locations l JOIN sectors s ON l.sector_id = s.id LEFT JOIN branches b ON s.branch_id = b.id ORDER BY l.location_code ASC")]

@router.post("/api/admin/locations")
async def create_location_direct(data: LocationCreate, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    await conn.execute("INSERT INTO locations (sector_id, location_code, description) VALUES ($1, $2, $3)", uuid.UUID(data.sector_id), data.location_code.strip().upper(), data.description.strip())
    return {"status": "success"}

@router.post("/api/admin/sectors/{sector_id}/locations/import")
async def import_locations_csv(sector_id: str, file: UploadFile = File(...), admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    content = await file.read()
    text = content.decode('utf-8-sig', errors='ignore')
    reader = csv.DictReader(StringIO(text))
    count = 0
    async with conn.transaction():
        for row in reader:
            code = row.get("ubicacion") or row.get("location_code") or row.get("codigo")
            desc = row.get("descripcion") or row.get("description") or ""
            if code and code.strip():
                await conn.execute("INSERT INTO locations (sector_id, location_code, description) VALUES ($1, $2, $3) ON CONFLICT DO NOTHING", uuid.UUID(sector_id), code.strip().upper(), desc.strip())
                count += 1
    return {"status": "success", "message": f"Se importaron {count} ubicaciones al sector."}
