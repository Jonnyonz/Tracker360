import os, json, urllib.request
from fastapi import APIRouter, Depends

try:
    from backend.database import require_admin
except ImportError:
    from database import require_admin

router = APIRouter(prefix="/api/admin/updater", tags=["System Updater"])

# Solo informa si hay una version publicada mas nueva. La actualizacion se aplica en el
# servidor (git pull + docker compose up -d --build), nunca descargando codigo en caliente.
CURRENT_VERSION = "2.0.0"
GITHUB_REPO = os.getenv("UPDATER_GITHUB_REPO", "Jonnyonz/Tracker360")

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
    req = urllib.request.Request(url, headers={"User-Agent": "Tracker360-UpdateCheck"})
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
        tag_name = data.get("tag_name", "v0.0.0")
        return {
            "current_version": CURRENT_VERSION,
            "latest_version": tag_name.lstrip("v"),
            "tag_name": tag_name,
            "update_available": parse_version(tag_name) > parse_version(CURRENT_VERSION),
            "release_name": data.get("name") or tag_name,
            "release_notes": data.get("body") or "Sin notas de versión proporcionadas.",
            "published_at": data.get("published_at"),
        }
    except Exception as e:
        print(f"[UPDATE CHECK ERROR] {GITHUB_REPO}: {e!r}")
        return {
            "current_version": CURRENT_VERSION,
            "latest_version": CURRENT_VERSION,
            "update_available": False,
            "error": "No se pudo consultar las versiones publicadas.",
        }
