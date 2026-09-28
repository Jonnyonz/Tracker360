from fastapi.openapi.docs import get_redoc_html
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse
import jwt

try:
    from backend.database import init_db_schema, DB, SECRET_KEY, ALGORITHM, get_client_ip, get_request_scheme, is_private_ip
    from backend.routers import auth, users, entities, items, warehouse, settings, printing, inbound, outbound, internal, inventory, dashboard, reports, rfid, updater
except ImportError:
    from database import init_db_schema, DB, SECRET_KEY, ALGORITHM, get_client_ip, get_request_scheme, is_private_ip
    from routers import auth, users, entities, items, warehouse, settings, printing, inbound, outbound, internal, inventory, dashboard, reports, rfid, updater

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db_schema()
    yield
    if DB.pool is not None:
        await DB.pool.close()

app = FastAPI(title="Tracker360 API", version="3.0 Enterprise", lifespan=lifespan)

# CSP: solo el propio sitio y el boton de Google Sign-In. 'unsafe-inline' sigue siendo necesario
# mientras el frontend use handlers onclick en linea.
CONTENT_SECURITY_POLICY = "; ".join([
    "default-src 'self'",
    "script-src 'self' 'unsafe-inline' https://accounts.google.com/gsi/client",
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
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    # /docs y /redoc cargan Swagger/ReDoc desde un CDN: se excluyen de la CSP.
    if request.url.path not in DOCS_PATHS:
        response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY

    return response

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
    print("[Tracker360] ALLOWED_ORIGINS no esta configurada: CORS deshabilitado (solo mismo origen). "
          "Definila en el .env si otro dominio necesita llamar a la API.")

# === REGISTRO DE ROUTERS MODULARES ENTERPRISE ===
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

# === ENDPOINTS EXPLICITOS DE FAVICON ===
@app.get("/favicon.png", include_in_schema=False)
@app.get("/favicon.ico", include_in_schema=False)
async def get_favicon():
    favicon_path = "frontend/favicon.png"
    if os.path.exists(favicon_path):
        return FileResponse(path=favicon_path, media_type="image/png")
    raise HTTPException(status_code=404, detail="Favicon no encontrado")

# === ENDPOINT DIRECTO PARA AGENTE DE IMPRESIÓN ===
@app.get("/downloads/tracker360-agent.zip")
@app.get("/api/download-agent")
async def download_agent_file():
    paths = ["downloads/tracker360-agent.zip", "frontend/downloads/tracker360-agent.zip"]
    for p in paths:
        if os.path.exists(p):
            return FileResponse(path=p, filename="tracker360-agent.zip", media_type="application/zip")
    raise HTTPException(status_code=404, detail="Archivo agente no encontrado")

# === RUTAS INTELIGENTES DE ENRUTAMIENTO (SWITCH DE VISTAS) ===
def get_user_role_from_cookie(request: Request) -> str:
    token = request.cookies.get("access_token")
    if not token or not token.startswith("Bearer "):
        return None
    try:
        payload = jwt.decode(token.split(" ")[1], SECRET_KEY, algorithms=[ALGORITHM])
        return payload.get("role")
    except jwt.PyJWTError:
        return None

@app.get("/")
@app.get("/index.html")
async def serve_root(request: Request):
    role = get_user_role_from_cookie(request)
    if not role:
        return FileResponse("frontend/index.html")
    
    if role in ["ADMIN", "SUPERVISOR"]:
        return RedirectResponse(url="/admin", status_code=303)
    else:
        return RedirectResponse(url="/mobile", status_code=303)

@app.get("/admin")
@app.get("/admin.html")
async def serve_admin(request: Request):
    role = get_user_role_from_cookie(request)
    if not role:
        return RedirectResponse(url="/index.html", status_code=303)
    
    if role not in ["ADMIN", "SUPERVISOR"]:
        return RedirectResponse(url="/mobile", status_code=303)
        
    return FileResponse("frontend/admin.html")

@app.get("/mobile")
@app.get("/preparador.html")
async def serve_mobile(request: Request):
    role = get_user_role_from_cookie(request)
    if not role:
        return RedirectResponse(url="/index.html", status_code=303)
    
    return FileResponse("frontend/preparador.html")

# === ARCHIVOS ESTÁTICOS AL FINAL ABSOLUTO ===
os.makedirs("downloads", exist_ok=True)
os.makedirs("frontend", exist_ok=True)
app.mount("/downloads", StaticFiles(directory="downloads"), name="downloads")
app.mount("/", StaticFiles(directory="frontend", html=False), name="frontend")