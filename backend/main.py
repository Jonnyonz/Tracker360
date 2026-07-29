import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

try:
    from backend.database import init_db_schema, DB
    from backend.routers import auth, users, entities, items, warehouse, settings, printing, operations
except ImportError:
    from database import init_db_schema, DB
    from routers import auth, users, entities, items, warehouse, settings, printing, operations

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

# === ARCHIVOS ESTÁTICOS AL FINAL ABSOLUTO ===
os.makedirs("downloads", exist_ok=True)
os.makedirs("frontend", exist_ok=True)
app.mount("/downloads", StaticFiles(directory="downloads"), name="downloads")
app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
