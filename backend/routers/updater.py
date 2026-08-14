import os, sys, json, shutil, zipfile, urllib.request
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
import asyncpg

try:
    from backend.database import get_db_connection, require_admin, log_action, init_db_schema
except ImportError:
    from database import get_db_connection, require_admin, log_action, init_db_schema

router = APIRouter(prefix="/api/admin/updater", tags=["System Updater"])

CURRENT_VERSION = "2.0.0"
GITHUB_REPO = "Jonnyonz/Tracker360"

def parse_version(v_str: str):
    clean = v_str.lower().lstrip("v").strip()
    parts = []
    for part in clean.split("."):
        try:
            parts.append(int(part))
        except ValueError:
            parts.append(0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])

@router.get("/check")
async def check_for_updates(admin: dict = Depends(require_admin)):
    url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
    req = urllib.request.Request(url, headers={"User-Agent": "Tracker360-InAppUpdater"})
    
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            if response.status == 200:
                data = json.loads(response.read().decode("utf-8"))
                tag_name = data.get("tag_name", "v0.0.0")
                latest_ver_clean = tag_name.lstrip("v")
                
                update_available = parse_version(tag_name) > parse_version(CURRENT_VERSION)
                zip_url = data.get("zipball_url") or f"https://github.com/{GITHUB_REPO}/archive/refs/tags/{tag_name}.zip"
                
                return {
                    "current_version": CURRENT_VERSION,
                    "latest_version": latest_ver_clean,
                    "tag_name": tag_name,
                    "update_available": update_available,
                    "release_name": data.get("name") or tag_name,
                    "release_notes": data.get("body") or "Sin notas de versión proporcionadas.",
                    "published_at": data.get("published_at"),
                    "download_url": zip_url
                }
    except Exception as e:
        return {
            "current_version": CURRENT_VERSION,
            "latest_version": CURRENT_VERSION,
            "update_available": False,
            "error": f"No se pudo consultar GitHub API: {str(e)}"
        }

@router.post("/apply")
async def apply_update(admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_db_connection)):
    check_res = await check_for_updates(admin=admin)
    if not check_res.get("update_available"):
        raise HTTPException(status_code=400, detail="El sistema ya se encuentra en la versión más reciente.")
    
    download_url = check_res.get("download_url")
    target_tag = check_res.get("tag_name")
    
    if not download_url:
        raise HTTPException(status_code=400, detail="No se encontró URL de descarga en la publicación de GitHub.")
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = os.path.join("backups", f"backup_v{CURRENT_VERSION}_{timestamp}")
    tmp_zip = os.path.join("backups", f"update_{target_tag}.zip")
    extract_tmp = os.path.join("backups", f"tmp_extract_{timestamp}")

    os.makedirs("backups", exist_ok=True)
    
    try:
        # 1. Copia de Respaldo Preventiva (Atomic Backup)
        os.makedirs(backup_dir, exist_ok=True)
        if os.path.exists("backend"):
            shutil.copytree("backend", os.path.join(backup_dir, "backend"), dirs_exist_ok=True)
        if os.path.exists("frontend"):
            shutil.copytree("frontend", os.path.join(backup_dir, "frontend"), dirs_exist_ok=True)
        if os.path.exists("main.py"):
            shutil.copy2("main.py", os.path.join(backup_dir, "main.py"))

        # 2. Descargar ZIP desde GitHub Release
        req = urllib.request.Request(download_url, headers={"User-Agent": "Tracker360-InAppUpdater"})
        with urllib.request.urlopen(req, timeout=60) as resp, open(tmp_zip, "wb") as out_file:
            shutil.copyfileobj(resp, out_file)

        # 3. Descomprimir en directorio temporal
        with zipfile.ZipFile(tmp_zip, "r") as zip_ref:
            zip_ref.extractall(extract_tmp)

        subdirs = [os.path.join(extract_tmp, d) for d in os.listdir(extract_tmp) if os.path.isdir(os.path.join(extract_tmp, d))]
        if not subdirs:
            raise Exception("Estructura del archivo ZIP de GitHub no reconocida.")
        
        release_root = subdirs[0]

        # 4. Sobrescribir archivos del sistema
        new_backend = os.path.join(release_root, "backend")
        new_frontend = os.path.join(release_root, "frontend")
        new_main = os.path.join(release_root, "main.py")

        if os.path.exists(new_backend):
            shutil.copytree(new_backend, "backend", dirs_exist_ok=True)
        if os.path.exists(new_frontend):
            shutil.copytree(new_frontend, "frontend", dirs_exist_ok=True)
        if os.path.exists(new_main):
            shutil.copy2(new_main, "main.py")

        # Limpieza de temporales
        if os.path.exists(tmp_zip): os.remove(tmp_zip)
        if os.path.exists(extract_tmp): shutil.rmtree(extract_tmp)

        # 5. Ejecutar migraciones DDL SQL
        await init_db_schema()

        # 6. Registrar en auditoría
        await log_action(conn, admin["username"], "SYSTEM_UPDATED", f"Sistema actualizado exitosamente a la versión {target_tag}.")

        return {
            "status": "success",
            "message": f"¡Tracker360 actualizado con éxito a la versión {target_tag}! Se creó una copia de respaldo en '{backup_dir}'.",
            "new_version": target_tag
        }

    except Exception as e:
        # Rollback de emergencia si falla la sobrescritura
        if os.path.exists(backup_dir):
            try:
                if os.path.exists(os.path.join(backup_dir, "backend")):
                    shutil.copytree(os.path.join(backup_dir, "backend"), "backend", dirs_exist_ok=True)
                if os.path.exists(os.path.join(backup_dir, "frontend")):
                    shutil.copytree(os.path.join(backup_dir, "frontend"), "frontend", dirs_exist_ok=True)
                if os.path.exists(os.path.join(backup_dir, "main.py")):
                    shutil.copy2(os.path.join(backup_dir, "main.py"), "main.py")
            except Exception:
                pass
        
        if os.path.exists(tmp_zip): os.remove(tmp_zip)
        if os.path.exists(extract_tmp): shutil.rmtree(extract_tmp)

        print(f"[UPDATER ERROR]: {e}")
        raise HTTPException(status_code=500, detail=f"Fallo al aplicar la actualización: {str(e)}. Se restauró la versión previa.")