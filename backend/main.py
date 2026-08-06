from fastapi.openapi.docs import get_redoc_html
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse
import jwt

try:
    from backend.database import init_db_schema, DB, SECRET_KEY, ALGORITHM
    from backend.routers import auth, users, entities, items, warehouse, settings, printing, operations, reports
except ImportError:
    from database import init_db_schema, DB, SECRET_KEY, ALGORITHM
    from routers import auth, users, entities, items, warehouse, settings, printing, operations, reports

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db_schema()
    yield
    if DB.pool is not None:
        await DB.pool.close()

app = FastAPI(title="Tracker360 API", version="2.0 Modular", lifespan=lifespan)

# === MIDDLEWARE SEGURIDAD BANCARIA (HTTPS & HEADERS) ===
@app.middleware("http")
async def security_middleware(request: Request, call_next):
    # 1. Forzar HTTPS (ignora redes locales/internas para no romper desarrollo/pruebas)
    client_ip = request.client.host if request.client else ""
    forwarded_proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    
    is_local = client_ip.startswith(("127.", "192.168.", "10.", "172.16.")) or client_ip == "::1"
    if not is_local and forwarded_proto != "https":
        return Response(content="Acceso denegado. Se requiere conexión HTTPS segura.", status_code=403)

    # 2. Inyectar cabeceras contra ataques de inyección y sniffing
    response = await call_next(request)
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    
    return response

# === MIDDLEWARE CORS HARDENED ===
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https?://.*",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# === REGISTRO DE ROUTERS MODULARES ===
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(entities.router)
app.include_router(items.router)
app.include_router(warehouse.router)
app.include_router(settings.router)
app.include_router(printing.router)
app.include_router(operations.router)
app.include_router(reports.router)

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
    
    # Todos (incluyendo Admins y Supervisores) pueden acceder a la vista móvil
    return FileResponse("frontend/preparador.html")


# === ARCHIVOS ESTÁTICOS AL FINAL ABSOLUTO ===
os.makedirs("downloads", exist_ok=True)
os.makedirs("frontend", exist_ok=True)
app.mount("/downloads", StaticFiles(directory="downloads"), name="downloads")

# Excluimos html principal de StaticFiles porque ya las manejamos arriba de forma inteligente
app.mount("/", StaticFiles(directory="frontend", html=False), name="frontend")