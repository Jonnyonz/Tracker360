"""Lectura de planillas para las importaciones (articulos, ubicaciones, articulos por ubicacion), sin dependencias:

- Excel nativo .xlsx: es un ZIP con XML adentro (primera hoja). No lee el .xls viejo (Excel 97-2003): se guarda
  como .xlsx desde el mismo Excel.
- Texto separado por tabulaciones (.txt / .tsv) y CSV con coma o punto y coma (el Excel argentino guarda con ";").
- Texto en UTF-8 o en la codificacion de Windows (cp1252): no se pierden las tildes.

Devuelve las filas como diccionarios con los encabezados normalizados (minusculas, sin tildes, espacios como "_"),
asi "Descripción", "DESCRIPCION" y "descripcion" son la misma columna. Con limites de tamano: un archivo armado a
proposito (ZIP que se infla, millones de filas) no tumba el servidor."""

import csv
import io
import re
import unicodedata
import zipfile
import xml.etree.ElementTree as ET
from typing import Dict, List

MAX_BYTES = 10 * 1024 * 1024            # archivo subido
MAX_DESCOMPRIMIDO = 60 * 1024 * 1024    # contenido del .xlsx ya descomprimido
MAX_FILAS = 50000

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
      "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}
REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"


class PlanillaInvalida(ValueError):
    """El archivo no se puede leer: el mensaje se puede mostrar al usuario tal cual."""


def normalizar(encabezado: str) -> str:
    sin_tildes = unicodedata.normalize("NFKD", str(encabezado or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", sin_tildes.strip().lower()).strip("_")


def _filas_a_dicts(filas: List[List[str]]) -> List[Dict[str, str]]:
    filas = [f for f in filas if any((c or "").strip() for c in f)]
    if not filas:
        return []
    encabezados = [normalizar(c) for c in filas[0]]
    if len(filas) - 1 > MAX_FILAS:
        raise PlanillaInvalida(f"La planilla tiene más de {MAX_FILAS} filas: dividila en varias.")
    salida = []
    for f in filas[1:]:
        salida.append({encabezados[i]: (f[i] if i < len(f) else "").strip() for i in range(len(encabezados)) if encabezados[i]})
    return salida


def _texto(contenido: bytes) -> str:
    try:
        return contenido.decode("utf-8-sig")
    except UnicodeDecodeError:
        return contenido.decode("cp1252", errors="replace")


def _leer_texto(contenido: bytes, nombre: str) -> List[Dict[str, str]]:
    texto = _texto(contenido)
    primera = texto.splitlines()[0] if texto.strip() else ""
    if nombre.endswith((".txt", ".tsv")) or "\t" in primera:
        separador = "\t"
    else:
        # El que mas aparece en el encabezado entre ";" y "," (Excel argentino: ";").
        separador = ";" if primera.count(";") >= primera.count(",") and ";" in primera else ","
    return _filas_a_dicts(list(csv.reader(io.StringIO(texto), delimiter=separador)))


def _xml(z: zipfile.ZipFile, ruta: str) -> ET.Element:
    datos = z.read(ruta)
    if b"<!DOCTYPE" in datos[:2048].upper():
        raise PlanillaInvalida("El archivo Excel no es válido.")
    return ET.fromstring(datos)


def _columna(ref: str) -> int:
    letras = re.match(r"[A-Z]+", ref or "")
    n = 0
    for ch in (letras.group(0) if letras else "A"):
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _numero(valor: str) -> str:
    # 12.0 -> "12" (Excel guarda los codigos numericos como numeros: el SKU 1001 no tiene que quedar "1001.0").
    try:
        f = float(valor)
        return str(int(f)) if f.is_integer() else valor
    except ValueError:
        return valor


def _leer_xlsx(contenido: bytes) -> List[Dict[str, str]]:
    try:
        z = zipfile.ZipFile(io.BytesIO(contenido))
    except zipfile.BadZipFile:
        raise PlanillaInvalida("El archivo no es un Excel .xlsx válido (si es un .xls viejo, guardalo como .xlsx).")
    with z:
        if sum(i.file_size for i in z.infolist()) > MAX_DESCOMPRIMIDO:
            raise PlanillaInvalida("El archivo Excel es demasiado grande.")
        nombres = set(z.namelist())
        if "xl/workbook.xml" not in nombres:
            raise PlanillaInvalida("El archivo no es un Excel .xlsx válido.")
        compartidos = []
        if "xl/sharedStrings.xml" in nombres:
            for si in _xml(z, "xl/sharedStrings.xml").findall("m:si", NS):
                compartidos.append("".join(t.text or "" for t in si.iter(f"{{{NS['m']}}}t")))
        # Primera hoja del libro, segun las relaciones (no siempre se llama sheet1.xml).
        libro = _xml(z, "xl/workbook.xml")
        hoja = libro.find("m:sheets/m:sheet", NS)
        ruta = "xl/worksheets/sheet1.xml"
        if hoja is not None and "xl/_rels/workbook.xml.rels" in nombres:
            rid = hoja.get(f"{{{NS['r']}}}id")
            for rel in _xml(z, "xl/_rels/workbook.xml.rels").iter(f"{REL_NS}Relationship"):
                if rel.get("Id") == rid:
                    destino = rel.get("Target", "").lstrip("/")
                    ruta = destino if destino.startswith("xl/") else "xl/" + destino
        if ruta not in nombres:
            raise PlanillaInvalida("No se encontró la primera hoja del Excel.")
        filas = []
        for fila in _xml(z, ruta).iter(f"{{{NS['m']}}}row"):
            valores = {}
            for celda in fila.findall("m:c", NS):
                tipo, v = celda.get("t"), celda.find("m:v", NS)
                if tipo == "s" and v is not None:
                    i = int(v.text or 0)
                    texto = compartidos[i] if i < len(compartidos) else ""
                elif tipo == "inlineStr":
                    texto = "".join(t.text or "" for t in celda.iter(f"{{{NS['m']}}}t"))
                elif v is not None:
                    texto = v.text or ""
                    if tipo not in ("str", "b", "e"):
                        texto = _numero(texto)
                else:
                    texto = ""
                valores[_columna(celda.get("r", ""))] = texto
            if valores:
                ancho = max(valores) + 1
                filas.append([valores.get(i, "") for i in range(ancho)])
            if len(filas) > MAX_FILAS + 1:
                raise PlanillaInvalida(f"La planilla tiene más de {MAX_FILAS} filas: dividila en varias.")
        return _filas_a_dicts(filas)


def leer_planilla(nombre: str, contenido: bytes) -> List[Dict[str, str]]:
    nombre = (nombre or "").lower()
    if len(contenido) > MAX_BYTES:
        raise PlanillaInvalida("El archivo supera los 10 MB.")
    if nombre.endswith(".xls"):
        raise PlanillaInvalida("El formato .xls (Excel 97-2003) no se lee: guardalo como .xlsx desde Excel.")
    if nombre.endswith(".xlsx") or contenido[:2] == b"PK":
        return _leer_xlsx(contenido)
    return _leer_texto(contenido, nombre)


def valor(fila: Dict[str, str], *nombres: str) -> str:
    """El primer valor no vacio entre varias columnas posibles (sku / codigo, descripcion / nombre...)."""
    for n in nombres:
        v = fila.get(normalizar(n))
        if v:
            return v
    return ""
