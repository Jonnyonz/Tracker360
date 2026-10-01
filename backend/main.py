from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi import Depends
from jztech_core.security_headers import SecurityHeadersMiddleware
from jztech_core.logging_setup import configure_logging, install_generic_error_handler

# Logs en JSON por stdout (docker logs / journald).
configure_logging()
import os
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse
import logging

logger = logging.getLogger(__name__)

from backend.database import init_db_schema, outbox_en_segundo_plano, DB, session_user, get_client_ip, get_request_scheme, is_private_ip, require_admin
from backend.routers import auth, users, entities, items, warehouse, settings, printing, inbound, outbound, internal, inventory, dashboard, reports, rfid, updater, notes, channels

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db_schema()
    envios = asyncio.create_task(outbox_en_segundo_plano())  # webhooks despues del commit (S8)
    yield
    envios.cancel()
    if DB.pool is not None:
        await DB.pool.close()

# La documentacion interactiva expone el mapa completo de la API: solo con sesion de ADMIN (abajo).
app = FastAPI(title="Tracker360 API", version="3.0 Enterprise", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
# Un error no previsto se loguea completo en el servidor y el cliente recibe un mensaje generico en
# "detail" (el campo que lee el frontend), nunca el texto de la excepcion.
install_generic_error_handler(app, "tracker360", field="detail")

# CSP: solo el propio sitio y el boton de Google Sign-In. Sin JavaScript inline: los handlers van en
# atributos data-on-* que ejecuta js/acciones.js. En style-src 'unsafe-inline' sigue (atributos style).
CONTENT_SECURITY_POLICY = "; ".join([
    "default-src 'self'",
    "script-src 'self' https://accounts.google.com/gsi/client",
    "style-src 'self' 'unsafe-inline' https://accounts.google.com/gsi/style",
    "frame-src https://accounts.google.com/gsi/",
    "connect-src 'self' https://accounts.google.com/gsi/",
    "img-src 'self' data: blob:",
    "media-src 'self' blob:",
    "font-src 'self' data:",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
])
DOCS_PATHS = {"/docs", "/docs/oauth2-redirect", "/redoc"}

# === MIDDLEWARE SEGURIDAD BANCARIA (HTTPS & HEADERS) ===
@app.middleware("http")
async def security_middleware(request: Request, call_next):
    # IP y protocolo reales: X-Forwarded-* solo se aceptan desde TRUSTED_PROXIES.
    client_ip = get_client_ip(request)
    scheme = get_request_scheme(request)

    if not is_private_ip(client_ip) and scheme != "https":
        return Response(content="Acceso denegado. Se requiere conexión HTTPS segura.", status_code=403)

    response = await call_next(request)
    # X-Frame-Options, X-Content-Type-Options, Referrer-Policy y Permissions-Policy los pone
    # jztech_core (SecurityHeadersMiddleware, abajo). HSTS y CSP quedan aca: HSTS se manda siempre
    # (detras de Caddy la peticion llega por http) y la CSP excluye /docs y /redoc.
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    # /docs y /redoc cargan Swagger/ReDoc desde un CDN: se excluyen de la CSP.
    if request.url.path not in DOCS_PATHS:
        response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY

    return response

# Cabeceras comunes de JZTech. La camara queda permitida para el propio sitio (el celular escanea con
# ella); geolocalizacion y microfono no se usan.
app.add_middleware(SecurityHeadersMiddleware, csp=None, hsts=False,
                   permissions_policy="geolocation=(), microphone=(), camera=(self)")

# === MIDDLEWARE CORS HARDENED ===
# Solo los origenes de ALLOWED_ORIGINS (lista separada por comas en el .env) pueden llamar a la API
# desde otro dominio. Sin la variable no se habilita CORS: el frontend propio (mismo origen) sigue
# funcionando y ningun sitio externo puede usar la sesion del usuario.
_allowed_origins = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "").split(",") if o.strip()]
if _allowed_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
else:
    logger.warning("[Tracker360] ALLOWED_ORIGINS no esta configurada: CORS deshabilitado (solo mismo origen). "
          "Definila en el .env si otro dominio necesita llamar a la API.")

# === REGISTRO DE ROUTERS MODULARES ENTERPRISE ===
@app.get("/openapi.json", include_in_schema=False)
async def openapi_schema(admin: dict = Depends(require_admin)):
    return app.openapi()

@app.get("/docs", include_in_schema=False)
async def swagger_docs(admin: dict = Depends(require_admin)):
    return get_swagger_ui_html(openapi_url="/openapi.json", title="Tracker360 API")

@app.get("/redoc", include_in_schema=False)
async def redoc_docs(admin: dict = Depends(require_admin)):
    return get_redoc_html(openapi_url="/openapi.json", title="Tracker360 API")

app.include_router(auth.router)
app.include_router(users.router)
app.include_router(entities.router)
app.include_router(items.router)
app.include_router(warehouse.router)
app.include_router(settings.router)
app.include_router(printing.router)
app.include_router(inbound.router)     # Recepción de Proveedores / Remitos / Devoluciones
app.include_router(outbound.router)    # Despachos / Picking / Packing / Olas
app.include_router(internal.router)    # Traspasos (ODT) / Replenishment
app.include_router(inventory.router)   # Stock / Auditorías / Spot Check
app.include_router(dashboard.router)   # Tablero / Kpis / Logs
app.include_router(reports.router)
app.include_router(rfid.router)
app.include_router(updater.router)
app.include_router(notes.router)     # Observaciones de documentos
app.include_router(channels.router)  # Canales de venta (marketplaces): /api/v1/channel/*

# === ENDPOINTS EXPLICITOS DE FAVICON ===
@app.get("/favicon.png", include_in_schema=False)
@app.get("/favicon.ico", include_in_schema=False)
async def get_favicon():
    favicon_path = "frontend/favicon.png"
    if os.path.exists(favicon_path):
        return FileResponse(path=favicon_path, media_type="image/png")
    raise HTTPException(status_code=404, detail="Favicon no encontrado")

# === DESCARGA DEL AGENTE DE IMPRESIÓN (GitHub Releases) ===
# El agente se publica compilado en GitHub Releases, no dentro del repo. Se redirige a la
# ultima version. La URL es configurable por cliente (AGENT_DOWNLOAD_URL) o se deriva del
# repositorio (UPDATER_GITHUB_REPO), sin quedar fija en el codigo.
_AGENT_REPO = os.getenv("UPDATER_GITHUB_REPO", "Jonnyonz/Tracker360")
AGENT_DOWNLOAD_URL = os.getenv("AGENT_DOWNLOAD_URL", f"https://github.com/{_AGENT_REPO}/releases/latest/download/Tracker360_Agente.exe")

@app.get("/downloads/tracker360-agent.zip")
@app.get("/api/download-agent")
async def download_agent_file():
    return RedirectResponse(url=AGENT_DOWNLOAD_URL, status_code=307)

# === RUTAS INTELIGENTES DE ENRUTAMIENTO (SWITCH DE VISTAS) ===
async def get_user_role_from_cookie(request: Request):
    """Rol del usuario de la sesion, para elegir la pantalla. None si no hay sesion valida."""
    if DB.pool is None:
        return None
    async with DB.pool.acquire() as conn:
        user = await session_user(conn, request)
    return user["role"] if user and user["is_active"] else None

@app.get("/")
@app.get("/index.html")
async def serve_root(request: Request):
    role = await get_user_role_from_cookie(request)
    if not role:
        return FileResponse("frontend/index.html")
    
    if role in ["ADMIN", "SUPERVISOR"]:
        return RedirectResponse(url="/admin", status_code=303)
    else:
        return RedirectResponse(url="/mobile", status_code=303)

@app.get("/admin")
@app.get("/admin.html")
async def serve_admin(request: Request):
    role = await get_user_role_from_cookie(request)
    if not role:
        return RedirectResponse(url="/index.html", status_code=303)
    
    if role not in ["ADMIN", "SUPERVISOR"]:
        return RedirectResponse(url="/mobile", status_code=303)
        
    return FileResponse("frontend/admin.html")

@app.get("/mobile")
@app.get("/preparador.html")
async def serve_mobile(request: Request):
    role = await get_user_role_from_cookie(request)
    if not role:
        return RedirectResponse(url="/index.html", status_code=303)
    
    return FileResponse("frontend/preparador.html")

# === ARCHIVOS ESTÁTICOS AL FINAL ABSOLUTO ===
# (La carpeta downloads/ ya no existe: el agente se descarga de GitHub Releases, ver la
# redireccion de /downloads/tracker360-agent.zip mas arriba.)
os.makedirs("frontend", exist_ok=True)
app.mount("/", StaticFiles(directory="frontend", html=False), name="frontend")