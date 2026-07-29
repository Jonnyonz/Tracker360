from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional, List
import asyncpg, uuid

try:
    from backend.database import get_db_connection, require_admin, build_full_address
except ImportError:
    from database import get_db_connection, require_admin, build_full_address

router = APIRouter(tags=["Entities"])

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

@router.get("/api/admin/entities")
async def list_entities(admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    rows = await conn.fetch("""
        SELECT e.id, e.tax_id, e.company_name, e.is_customer, e.is_supplier, e.is_active,
               COALESCE(json_agg(json_build_object(
                   'id', a.id,
                   'label', a.address_label,
                   'address_label', a.address_label,
                   'address', a.full_address,
                   'full_address', a.full_address,
                   'street', a.street,
                   'number', a.number,
                   'zip_code', a.zip_code,
                   'city_neighborhood', a.city_neighborhood,
                   'is_default', a.is_default
               )) FILTER (WHERE a.id IS NOT NULL), '[]'::json) as addresses
        FROM entities e
        LEFT JOIN entity_addresses a ON e.id = a.entity_id
        GROUP BY e.id
        ORDER BY e.company_name ASC
    """)
    return [dict(r) for r in rows]

@router.post("/api/admin/entities")
async def create_entity(data: EntityCreate, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    async with conn.transaction():
        ent_id = await conn.fetchval(
            "INSERT INTO entities (tax_id, company_name, is_customer, is_supplier) VALUES ($1, $2, $3, $4) RETURNING id",
            data.tax_id.strip(), data.company_name.strip(), data.is_customer, data.is_supplier
        )
        if data.initial_address:
            addr = data.initial_address
            composed = build_full_address(addr.street, addr.number, addr.zip_code, addr.city_neighborhood, addr.full_address)
            await conn.execute(
                "INSERT INTO entity_addresses (entity_id, address_label, full_address, street, number, zip_code, city_neighborhood, is_default) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
                ent_id, addr.address_label.strip(), composed, addr.street, addr.number, addr.zip_code, addr.city_neighborhood, addr.is_default
            )
    return {"status": "success"}

@router.put("/api/admin/entities/{entity_id}")
async def update_entity(entity_id: str, data: EntityUpdate, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    await conn.execute("""
        UPDATE entities 
        SET company_name = COALESCE($1, company_name),
            tax_id = COALESCE($2, tax_id),
            is_customer = COALESCE($3, is_customer),
            is_supplier = COALESCE($4, is_supplier),
            is_active = COALESCE($5, is_active)
        WHERE id = $6
    """, data.company_name, data.tax_id, data.is_customer, data.is_supplier, data.is_active, uuid.UUID(entity_id))
    return {"status": "success"}

@router.get("/api/admin/entities/{entity_id}/addresses")
async def get_entity_addresses(entity_id: str, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    rows = await conn.fetch("SELECT id, address_label, full_address, street, number, zip_code, city_neighborhood, is_default FROM entity_addresses WHERE entity_id = $1 ORDER BY is_default DESC, created_at ASC", uuid.UUID(entity_id))
    return [dict(r) for r in rows]

@router.post("/api/admin/entities/{entity_id}/addresses")
async def add_entity_address(entity_id: str, data: EntityAddressInput, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    async with conn.transaction():
        if data.is_default:
            await conn.execute("UPDATE entity_addresses SET is_default = FALSE WHERE entity_id = $1", uuid.UUID(entity_id))
        composed = build_full_address(data.street, data.number, data.zip_code, data.city_neighborhood, data.full_address)
        await conn.execute(
            "INSERT INTO entity_addresses (entity_id, address_label, full_address, street, number, zip_code, city_neighborhood, is_default) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
            uuid.UUID(entity_id), data.address_label.strip(), composed, data.street, data.number, data.zip_code, data.city_neighborhood, data.is_default
        )
    return {"status": "success"}

@router.put("/api/admin/addresses/{address_id}")
async def update_address(address_id: str, data: EntityAddressInput, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    async with conn.transaction():
        if data.is_default and data.entity_id:
            await conn.execute("UPDATE entity_addresses SET is_default = FALSE WHERE entity_id = $1", uuid.UUID(data.entity_id))
        composed = build_full_address(data.street, data.number, data.zip_code, data.city_neighborhood, data.full_address)
        await conn.execute("""
            UPDATE entity_addresses 
            SET address_label = $1, full_address = $2, street = $3, number = $4, zip_code = $5, city_neighborhood = $6, is_default = $7
            WHERE id = $8
        """, data.address_label.strip(), composed, data.street, data.number, data.zip_code, data.city_neighborhood, data.is_default, uuid.UUID(address_id))
    return {"status": "success"}

@router.delete("/api/admin/addresses/{address_id}")
async def delete_address(address_id: str, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    await conn.execute("DELETE FROM entity_addresses WHERE id = $1", uuid.UUID(address_id))
    return {"status": "success"}

@router.get("/api/admin/suppliers/{supplier_id}/remitos")
async def get_supplier_remitos(supplier_id: str, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    rows = await conn.fetch("""
        SELECT pr.id, pr.remito_number, pr.status, b.name as branch_name, sec.name as sector_name 
        FROM purchase_remitos pr
        LEFT JOIN branches b ON pr.branch_id = b.id
        LEFT JOIN sectors sec ON pr.sector_id = sec.id
        WHERE pr.supplier_id = $1
        ORDER BY pr.created_at DESC
    """, uuid.UUID(supplier_id))
    return [dict(r) for r in rows]
