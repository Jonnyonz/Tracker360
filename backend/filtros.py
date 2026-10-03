# Filtros de los historiales del panel (ordenes de compra, remitos, facturas, traspasos, devoluciones).
# Arma el WHERE con parametros numerados ($1, $2...) para asyncpg; nunca concatena valores al SQL.
from datetime import datetime

from fastapi import HTTPException


class Filtros:
    def __init__(self):
        self.condiciones, self.args = [], []

    def _param(self, valor) -> str:
        self.args.append(valor)
        return f"${len(self.args)}"

    def texto(self, valor: str, *columnas: str) -> "Filtros":
        """Coincidencia parcial sin distinguir mayusculas en cualquiera de las columnas."""
        valor = (valor or "").strip()
        if valor:
            p = self._param(f"%{valor}%")
            self.condiciones.append("(" + " OR ".join(f"COALESCE({c}, '') ILIKE {p}" for c in columnas) + ")")
        return self

    def igual(self, valor: str, columna: str, validos: tuple) -> "Filtros":
        """Valor exacto (en mayusculas) de una lista cerrada; otro valor es 400."""
        valor = (valor or "").strip().upper()
        if valor:
            if valor not in validos:
                raise HTTPException(400, f"Valor inválido: {valor}. Opciones: {', '.join(validos)}.")
            self.condiciones.append(f"{columna} = {self._param(valor)}")
        return self

    def fechas(self, desde: str, hasta: str, columna: str) -> "Filtros":
        """Rango de fechas AAAA-MM-DD, ambos extremos incluidos."""
        if (desde or "").strip():
            self.condiciones.append(f"{columna} >= {self._param(_fecha(desde, '00:00:00'))}")
        if (hasta or "").strip():
            self.condiciones.append(f"{columna} <= {self._param(_fecha(hasta, '23:59:59'))}")
        return self

    def where(self) -> str:
        return " AND ".join(self.condiciones) if self.condiciones else "TRUE"

    def limite(self, limit: int, maximo: int = 500) -> str:
        return self._param(max(1, min(int(limit), maximo)))


def _fecha(valor: str, hora: str) -> datetime:
    try:
        return datetime.strptime(f"{valor.strip()} {hora}", "%Y-%m-%d %H:%M:%S")
    except ValueError:
        raise HTTPException(400, "Fecha inválida (usar AAAA-MM-DD).")
