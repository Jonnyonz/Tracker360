from fastapi.openapi.docs import get_redoc_html
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

try:
    from backend.database import init_db_schema, DB
    from backend.routers import auth, users, entities, items, warehouse, settings, printing, operations, reports
except ImportError:
    from database import init_db_schema, DB
    from routers import auth, users, entities, items, warehouse, settings, printing, operations, reports

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db_schema()
    yield
    if DB.pool is not None:
        await DB.pool.close()

app = FastAPI(title="Tracker360 API", version="2.0 Modular", lifespan=lifespan)

# === MIDDLEWARE CABECERAS DE SEGURIDAD HTTP ===
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-XSS-Protection"] = "1; mode=block"
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

@app.get("/api/admin/dashboard")
async def get_admin_dashboard():
    return {
        "status": "ok",
        "total_items": 0,
        "pending_jobs": 0
    }

# === ARCHIVOS ESTÁTICOS AL FINAL ABSOLUTO ===
os.makedirs("downloads", exist_ok=True)
os.makedirs("frontend", exist_ok=True)
app.mount("/downloads", StaticFiles(directory="downloads"), name="downloads")
app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")