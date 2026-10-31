# Ajustes de stock: un documento (AJ-000001) con sucursal, sector, concepto y una linea por articulo con la
# cantidad firmada (+ suma, - resta). Al registrarlo, cada linea es un movimiento 'AJUSTE' con el numero como
# referencia (el mismo tipo que usa el cierre de un conteo). El ADMIN crea; el SUPERVISOR consulta.
import math
from typing import List, Optional

import asyncpg
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from backend.database import (get_db_connection, require_admin, require_supervisor, record_stock_movement, log_action,
                              check_idempotency, save_idempotency, parse_uuid, numero_correlativo, siguiente_numero, MAX_CANTIDAD)
from backend.filtros import Filtros

router = APIRouter(tags=["Stock Adjustments"])
URL = "/api/admin/stock-adjustments"


class AdjustmentLineInput(BaseModel):
    sku: str
    quantity: float                       # firmada: positiva suma, negativa resta; nunca 0
    location_code: Optional[str] = None   # obligatoria si el sector trabaja con ubicaciones
    lot_number: Optional[str] = ""


class AdjustmentCreateInput(BaseModel):
    branch_id: str
    sector_id: str
    reason: str
    notes: Optional[str] = ""
    lines: List[AdjustmentLineInput]


async def conceptos(conn: asyncpg.Connection) -> List[str]:
    """Conceptos de ajuste configurados (Configuracion, uno por linea), sin repetidos ni vacios."""
    from backend.routers.settings import DEFAULT_SETTINGS
    texto = (await conn.fetchval("SELECT value FROM system_settings WHERE key = 'stock_adjustment_reasons'") or "").strip()
    texto = texto or DEFAULT_SETTINGS["stock_adjustment_reasons"]   # textarea vacio en Configuracion: vuelve la lista inicial
    vistos, lista = set(), []
    for linea in texto.splitlines():
        c = linea.strip()
        if c and c.upper() not in vistos:
            vistos.add(c.upper())
            lista.append(c)
    return lista


@router.get(URL + "/next-number")
async def next_adjustment_number(user: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    return {"number": await siguiente_numero(conn, "AJUSTE")}


@router.get(URL + "/reasons")
async def list_reasons(user: dict = Depends(require_supervisor), conn: asyncpg.Connection = Depends(get_db_connection)):
    return await conceptos(conn)


@router.get(URL)
async def list_adjustments(number: str = "", branch: str = "", sector: str = "", user: str = "", reason: str = "", sku: str = "",
                           date_from: str = "", date_to: str = "", limit: int = 200,
                           quien: dict = Depends(require_supervisor), conn: asyncpg.Connection = Depends(get_db_connection)):
    """Historial de ajustes, del mas nuevo al mas viejo. Filtros opcionales, cada uno por su dato: number, branch
    (nombre o codigo), sector, user (quien lo creo), reason, sku (algun articulo del ajuste) y fechas AAAA-MM-DD."""
    f = (Filtros().texto(number, "a.adjustment_number").texto(branch, "b.name", "b.code").texto(sector, "s.name")
         .texto(user, "a.created_by").texto(reason, "a.reason").fechas(date_from, date_to, "a.created_at"))
    if sku.strip():
        f.condiciones.append(f"EXISTS (SELECT 1 FROM stock_adjustment_lines x WHERE x.adjustment_id = a.id AND x.sku ILIKE {f._param('%' + sku.strip() + '%')})")
    tope = f.limite(limit)
    rows = await conn.fetch(f"""
        SELECT a.id::text AS id, a.adjustment_number, a.reason, a.notes, a.created_by, a.created_at,
               b.name AS branch_name, s.name AS sector_name,
               (SELECT COUNT(*) FROM stock_adjustment_lines l WHERE l.adjustment_id = a.id)::int AS lines_count,
               COALESCE((SELECT SUM(l.quantity) FROM stock_adjustment_lines l WHERE l.adjustment_id = a.id AND l.quantity > 0), 0)::float AS total_in,
               COALESCE((SELECT -SUM(l.quantity) FROM stock_adjustment_lines l WHERE l.adjustment_id = a.id AND l.quantity < 0), 0)::float AS total_out
        FROM stock_adjustments a
        JOIN branches b ON b.id = a.branch_id
        JOIN sectors s ON s.id = a.sector_id
        WHERE {f.where()}
        ORDER BY a.created_at DESC, a.adjustment_number DESC LIMIT {tope}
    """, *f.args)
    return [dict(r) for r in rows]


@router.get(URL + "/{number}")
async def get_adjustment(number: str, user: dict = Depends(require_supervisor), conn: asyncpg.Connection = Depends(get_db_connection)):
    a = await conn.fetchrow("""
        SELECT a.id::text AS id, a.adjustment_number, a.reason, a.notes, a.created_by, a.created_at,
               b.name AS branch_name, s.name AS sector_name
        FROM stock_adjustments a JOIN branches b ON b.id = a.branch_id JOIN sectors s ON s.id = a.sector_id
        WHERE UPPER(a.adjustment_number) = $1""", number.strip().upper())
    if not a:
        raise HTTPException(404, "Ajuste no encontrado.")
    lineas = await conn.fetch("""
        SELECT l.sku, COALESCE(i.description, '') AS description, l.quantity::float AS quantity, loc.location_code, l.lot_number
        FROM stock_adjustment_lines l LEFT JOIN items i ON i.sku = l.sku LEFT JOIN locations loc ON loc.id = l.location_id
        WHERE l.adjustment_id = $1::uuid ORDER BY l.sku, loc.location_code""", a["id"])
    return {**dict(a), "lines": [dict(l) for l in lineas]}


@router.post(URL)
async def create_adjustment(data: AdjustmentCreateInput, x_idempotency_key: Optional[str] = Header(None),
                            user: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    cached = await check_idempotency(conn, x_idempotency_key, URL)
    if cached:
        return cached[0]

    concepto = (data.reason or "").strip()
    validos = await conceptos(conn)
    if not concepto or concepto.upper() not in {c.upper() for c in validos}:
        raise HTTPException(400, "Elegí un concepto de ajuste de la lista (se configuran en Configuración).")
    concepto = next(c for c in validos if c.upper() == concepto.upper())
    if not data.lines:
        raise HTTPException(400, "Agregá al menos un artículo.")

    async with conn.transaction():
        branch_id = parse_uuid(data.branch_id, "Sucursal inválida.")
        sector_id = parse_uuid(data.sector_id, "Sector inválido.")
        # Un usuario con sucursal/sector fijos solo ajusta ahi (misma regla que las devoluciones).
        if user.get("branch_id") and str(user["branch_id"]) != str(branch_id):
            raise HTTPException(400, "Solo podés ajustar stock en la sucursal asignada a tu usuario.")
        if user.get("sector_id") and str(user["sector_id"]) != str(sector_id):
            raise HTTPException(400, "Solo podés ajustar stock en el sector asignado a tu usuario.")
        sector = await conn.fetchrow("SELECT id, name, uses_locations FROM sectors WHERE id = $1 AND branch_id = $2", sector_id, branch_id)
        if not sector:
            raise HTTPException(400, "El sector no pertenece a esa sucursal.")

        # Lineas: SKU existente, cantidad finita y distinta de 0, ubicacion del sector si el sector las usa.
        # Las repetidas (mismo SKU, ubicacion y lote) se suman; si quedan en 0 se rechazan.
        combinadas, codigos = {}, {}
        for n, line in enumerate(data.lines, 1):
            sku = (line.sku or "").strip().upper()
            if not sku:
                raise HTTPException(400, f"Línea {n}: falta el SKU.")
            q = float(line.quantity)
            if not math.isfinite(q) or q == 0:
                raise HTTPException(400, f"Línea {n} ({sku}): la cantidad tiene que ser distinta de 0.")
            if abs(q) > MAX_CANTIDAD:
                raise HTTPException(400, f"Línea {n} ({sku}): la cantidad es demasiado grande.")
            codigo = (line.location_code or "").strip().upper()
            loc_id = None
            if sector["uses_locations"]:
                if not codigo:
                    raise HTTPException(400, f"Línea {n} ({sku}): el sector {sector['name']} trabaja con ubicaciones, indicá una.")
                loc_id = await conn.fetchval("SELECT id FROM locations WHERE sector_id = $1 AND UPPER(location_code) = $2", sector_id, codigo)
                if not loc_id:
                    raise HTTPException(400, f"Línea {n} ({sku}): la ubicación {codigo} no existe en el sector {sector['name']}.")
                codigos[loc_id] = codigo
            elif codigo:
                raise HTTPException(400, f"Línea {n} ({sku}): el sector {sector['name']} no trabaja con ubicaciones.")
            lote = (line.lot_number or "").strip()
            clave = (sku, loc_id, lote)
            combinadas[clave] = combinadas.get(clave, 0.0) + q
        skus = sorted({c[0] for c in combinadas})
        existentes = {r["sku"] for r in await conn.fetch("SELECT UPPER(sku) AS sku FROM items WHERE UPPER(sku) = ANY($1::text[])", skus)}
        faltan = [s for s in skus if s not in existentes]
        if faltan:
            raise HTTPException(400, "Artículos inexistentes: " + ", ".join(faltan) + ".")
        lineas = [(sku, loc_id, lote, q) for (sku, loc_id, lote), q in combinadas.items()]
        if any(q == 0 for _, _, _, q in lineas):
            raise HTTPException(400, "Hay artículos cuyas líneas se cancelan entre sí (suman 0).")

        # Las restas no dejan el stock negativo salvo que la configuracion lo permita. FOR UPDATE: otra operacion
        # sobre la misma fila espera y ve lo que dejo esta.
        permite_negativo = (await conn.fetchval("SELECT value FROM system_settings WHERE key = 'allow_negative_stock'")) == "true"
        for sku, loc_id, lote, q in lineas:
            if q >= 0 or permite_negativo:
                continue
            disponible = await conn.fetchval("""
                SELECT quantity FROM stock_inventory
                WHERE branch_id = $1 AND sector_id = $2 AND location_id IS NOT DISTINCT FROM $3 AND UPPER(sku) = $4
                  AND lot_number = $5 AND COALESCE(condition, 'OPERATIVO') = 'OPERATIVO' FOR UPDATE""",
                branch_id, sector_id, loc_id, sku, lote)
            disponible = float(disponible or 0)
            if disponible + q < 0:
                donde = f" en {codigos[loc_id]}" if loc_id else ""
                raise HTTPException(400, f"Stock insuficiente de {sku}{donde} (disponible {disponible:g}, se quieren restar {-q:g}).")

        numero, _ = await numero_correlativo(conn, "AJUSTE")
        adj_id = await conn.fetchval("""
            INSERT INTO stock_adjustments (adjustment_number, branch_id, sector_id, reason, notes, created_by)
            VALUES ($1, $2, $3, $4, $5, $6) RETURNING id""", numero, branch_id, sector_id, concepto, (data.notes or "").strip(), user["username"])
        for sku, loc_id, lote, q in lineas:
            await conn.execute("INSERT INTO stock_adjustment_lines (adjustment_id, sku, quantity, location_id, lot_number) VALUES ($1, $2, $3, $4, $5)",
                               adj_id, sku, q, loc_id, lote)
            await record_stock_movement(conn, sku, branch_id, sector_id, loc_id, q, 'AJUSTE', numero, user["username"], lot_number=lote)

        entradas = sum(q for *_, q in lineas if q > 0)
        salidas = -sum(q for *_, q in lineas if q < 0)
        await log_action(conn, user["username"], "STOCK_ADJUSTMENT_CREATED",
                         f"Ajuste de stock {numero} ({concepto}) en {sector['name']}: {len(lineas)} artículo(s), +{entradas:g} / -{salidas:g}.")
        res = {"status": "success", "message": f"Ajuste {numero} registrado.", "adjustment_number": numero,
               "lines": len(lineas), "total_in": entradas, "total_out": salidas}
        await save_idempotency(conn, x_idempotency_key, URL, res)
        return res
