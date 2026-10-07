from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel
from typing import Optional
import asyncpg, uuid, csv
from io import StringIO

from backend.database import get_db_connection, require_admin, require_supervisor, build_full_address, parse_uuid
from backend.planillas import PlanillaInvalida, leer_planilla, valor

router = APIRouter(tags=["Warehouse"])

class BranchCreate(BaseModel):
    code: str
    name: str
    street: str = ""
    number: str = ""
    zip_code: str = ""
    city: str = ""

class BranchUpdate(BaseModel):
    name: str
    street: str = ""
    number: str = ""
    zip_code: str = ""
    city: str = ""

def _branch_row(r) -> dict:
    d = dict(r)
    campos = (d.get("street"), d.get("number"), d.get("zip_code"), d.get("city"))
    d["full_address"] = build_full_address(*campos) if any(c and c.strip() for c in campos) else ""
    return d

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
async def list_branches(admin: dict = Depends(require_supervisor), conn: asyncpg.Connection = Depends(get_db_connection)):
    rows = await conn.fetch("SELECT id, code, name, is_active, COALESCE(street, '') AS street, COALESCE(number, '') AS number, COALESCE(zip_code, '') AS zip_code, COALESCE(city, '') AS city FROM branches ORDER BY name ASC")
    return [_branch_row(r) for r in rows]

@router.post("/api/admin/branches")
async def create_branch(data: BranchCreate, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    await conn.execute("INSERT INTO branches (code, name, street, number, zip_code, city) VALUES ($1, $2, $3, $4, $5, $6)",
                       data.code.strip().upper(), data.name.strip(), data.street.strip(), data.number.strip(), data.zip_code.strip(), data.city.strip())
    return {"status": "success"}

@router.put("/api/admin/branches/{branch_id}")
async def update_branch(branch_id: str, data: BranchUpdate, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    # El codigo no se edita: identifica a la sucursal en importaciones y reportes.
    try:
        bid = uuid.UUID(branch_id)
    except ValueError:
        raise HTTPException(400, "Sucursal inválida.")
    if not data.name.strip(): raise HTTPException(400, "El nombre de la sucursal es obligatorio.")
    res = await conn.execute("UPDATE branches SET name = $1, street = $2, number = $3, zip_code = $4, city = $5 WHERE id = $6",
                             data.name.strip(), data.street.strip(), data.number.strip(), data.zip_code.strip(), data.city.strip(), bid)
    if res == "UPDATE 0": raise HTTPException(404, "Sucursal no encontrada.")
    return {"status": "success"}

@router.get("/api/admin/sectors")
async def list_sectors(admin: dict = Depends(require_supervisor), conn: asyncpg.Connection = Depends(get_db_connection)):
    return [dict(r) for r in await conn.fetch("SELECT s.id, s.name, s.print_queue_code, s.uses_locations, s.branch_id, b.name as branch_name FROM sectors s LEFT JOIN branches b ON s.branch_id = b.id ORDER BY s.name ASC")]

@router.post("/api/admin/sectors")
async def create_sector(data: SectorCreate, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    await conn.execute("INSERT INTO sectors (name, print_queue_code, uses_locations, branch_id) VALUES ($1, $2, $3, $4)", data.name.strip(), data.print_queue_code.strip().upper(), data.uses_locations, parse_uuid(data.branch_id))
    return {"status": "success"}

@router.get("/api/admin/locations")
async def list_all_locations(admin: dict = Depends(require_supervisor), conn: asyncpg.Connection = Depends(get_db_connection)):
    return [dict(l) for l in await conn.fetch("SELECT l.id, l.location_code, l.description, s.name as sector_name, b.name as branch_name FROM locations l JOIN sectors s ON l.sector_id = s.id LEFT JOIN branches b ON s.branch_id = b.id ORDER BY l.location_code ASC")]

@router.post("/api/admin/locations")
async def create_location_direct(data: LocationCreate, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    sector_id, codigo = parse_uuid(data.sector_id), data.location_code.strip().upper()
    if not codigo:
        raise HTTPException(400, "El código de la ubicación es obligatorio.")
    if await conn.fetchval("SELECT 1 FROM locations WHERE sector_id = $1 AND UPPER(location_code) = $2", sector_id, codigo):
        raise HTTPException(409, f"La ubicación {codigo} ya existe en ese sector.")
    await conn.execute("INSERT INTO locations (sector_id, location_code, description) VALUES ($1, $2, $3)", sector_id, codigo, (data.description or "").strip())
    return {"status": "success"}

@router.post("/api/admin/sectors/{sector_id}/locations/import")
async def import_locations_csv(sector_id: str, file: UploadFile = File(...), admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    try:
        filas = leer_planilla(file.filename or "", await file.read())
    except PlanillaInvalida as e:
        raise HTTPException(400, str(e))
    count = repetidas = 0
    async with conn.transaction():
        for row in filas:
            code = valor(row, "ubicacion", "ubicación", "location_code", "codigo")
            desc = valor(row, "descripcion", "description")
            if code and code.strip():
                codigo = code.strip().upper()
                if await conn.fetchval("SELECT 1 FROM locations WHERE sector_id = $1 AND UPPER(location_code) = $2", parse_uuid(sector_id), codigo):
                    repetidas += 1
                    continue
                await conn.execute("INSERT INTO locations (sector_id, location_code, description) VALUES ($1, $2, $3)", parse_uuid(sector_id), codigo, desc.strip())
                count += 1
    extra = f" {repetidas} ya existían y no se duplicaron." if repetidas else ""
    return {"status": "success", "message": f"Se importaron {count} ubicaciones al sector.{extra}"}
