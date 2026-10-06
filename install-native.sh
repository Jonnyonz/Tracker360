#!/bin/bash
# ==============================================================================
# Instalador nativo (sin Docker) de Tracker360
# ==============================================================================
# Para Debian 12/13 y Ubuntu 24.04 (apt, Python 3.11 o mas nuevo). Correr como root desde la raiz de un
# clon del repositorio:
#
#   sudo ./install-native.sh                                     (pregunta el dominio la primera vez)
#   sudo TRACKER360_DOMAIN=wms.cliente.com ./install-native.sh   con el dominio publico del cliente
#
# Queda asi:
#   /opt/tracker360/src/                 clon de git del que se actualiza (lo usa tracker360-actualizar)
#   /opt/tracker360/releases/<version>/  codigo + su propio venv (una carpeta por version; version = commit)
#   /opt/tracker360/current              enlace a la version en uso (el actualizador lo cambia)
#   /etc/tracker360/tracker360.env       configuracion y secretos (root:tracker360, 0640)
#   servicio systemd "tracker360"        uvicorn en 0.0.0.0:8001 (http), un worker
#   base "tracker360_db" y rol "tracker360" propios en el PostgreSQL del servidor
#   /usr/local/sbin/tracker360-actualizar  actualizador (respaldo, chequeo y vuelta atras)
#
# El HTTPS lo da Nginx Proxy Manager (ya instalado en el servidor, con el 80/443): un Proxy Host del dominio
# hacia http://<IP del servidor>:8001. La sesion usa cookies Secure: sin HTTPS no se puede ingresar desde otra PC.
#
# Idempotente: se puede volver a correr. Los secretos ya generados no se pisan. Sin compilador: las
# dependencias se instalan solo con paquetes binarios (wheels) verificando los hashes de requirements.txt;
# si hay una carpeta wheelhouse/ al lado, se usa esa (instalacion sin internet).
#
# Es independiente de la instalacion con Docker (install.sh): no se pueden usar las dos en el mismo puerto.
#
# Variables opcionales:
#   TRACKER360_DOMAIN=wms.cliente.com  dominio publico (el del Proxy Host; queda en ALLOWED_ORIGINS)
#   TRACKER360_PORT=8001               puerto del servicio (al que reenvia Nginx Proxy Manager)
#   TRACKER360_BIND=0.0.0.0            interfaz donde escucha (0.0.0.0: llega NPM aunque sea un contenedor)
#   TRACKER360_PROXY_IP=192.168.1.20   IP de Nginx Proxy Manager si esta en otro equipo (TRUSTED_PROXIES)
#   TRACKER360_REPO_URL=...            repositorio del que se actualiza (por defecto, el origin de este clon)
# ==============================================================================

set -euo pipefail

APP_NAME="tracker360"
APP_USER="tracker360"
BASE_DIR="${TRACKER360_DIR:-/opt/tracker360}"
RELEASES="$BASE_DIR/releases"
SRC="$BASE_DIR/src"
ENV_DIR="/etc/$APP_NAME"
ENV_FILE="$ENV_DIR/$APP_NAME.env"
SERVICE="$APP_NAME"
DB_NAME="tracker360_db"
DB_USER="tracker360"
ACTUALIZADOR="/usr/local/sbin/tracker360-actualizar"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd /   # psql como postgres no puede entrar a la carpeta desde la que se corre (por ejemplo /root)

valor_env() { [ -f "$ENV_FILE" ] && sed -n "s/^$1=//p" "$ENV_FILE" | tail -n1 || true; }

echo "=================================================="
echo "Instalador nativo de Tracker360"
echo "=================================================="

# 1. Privilegios, sistema y ubicacion
if [ "$EUID" -ne 0 ]; then
  echo "Error: correr como root (sudo ./install-native.sh)." >&2
  exit 1
fi
if [ ! -f /etc/debian_version ]; then
  echo "Error: este instalador es para Debian/Ubuntu (apt)." >&2
  exit 1
fi
if [ ! -f "$SCRIPT_DIR/backend/main.py" ] || [ ! -f "$SCRIPT_DIR/backend/requirements.txt" ]; then
  echo "Error: correr el script desde la raiz del repo (faltan backend/main.py o backend/requirements.txt)." >&2
  exit 1
fi

# Configuracion: lo pedido, lo de la instalacion anterior o el valor por defecto.
APP_PORT="${TRACKER360_PORT:-$(valor_env APP_PORT)}"; APP_PORT="${APP_PORT:-8001}"
DOMAIN="${TRACKER360_DOMAIN:-$(valor_env TRACKER360_DOMAIN)}"
if [ -z "$DOMAIN" ] && [ ! -f "$ENV_FILE" ] && [ -r /dev/tty ]; then
  read -r -p "Dominio publico de este servidor (ej. wms.suempresa.com, Enter para omitir): " DOMAIN < /dev/tty || DOMAIN=""
fi
DOMAIN="${DOMAIN#http://}"; DOMAIN="${DOMAIN#https://}"; DOMAIN="${DOMAIN%%/*}"
# Antes escuchaba solo en 127.0.0.1 (se entraba por Caddy): ahora en todas las interfaces para que llegue NPM.
APP_BIND="${TRACKER360_BIND:-$(valor_env TRACKER360_BIND)}"; APP_BIND="${APP_BIND:-0.0.0.0}"
if [ -n "$DOMAIN" ] && ! [[ "$DOMAIN" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$ ]]; then
  echo "Error: dominio invalido: $DOMAIN" >&2
  exit 1
fi
if ! [[ "$APP_PORT" =~ ^[0-9]+$ ]]; then
  echo "Error: puerto invalido: $APP_PORT" >&2
  exit 1
fi
# Proxies de confianza: loopback y redes de Docker (NPM en un contenedor de este servidor), mas la IP de NPM si
# esta en otro equipo. Las instalaciones de antes tenian solo loopback (Caddy en el mismo servidor).
CONFIABLES="$(valor_env TRUSTED_PROXIES)"
if [ -z "$CONFIABLES" ] || [ "$CONFIABLES" = "127.0.0.1/32,::1/128" ]; then CONFIABLES="127.0.0.1/32,::1/128,172.16.0.0/12"; fi
if [ -n "${TRACKER360_PROXY_IP:-}" ]; then
  case ",$CONFIABLES," in *",$TRACKER360_PROXY_IP,"*) ;; *) CONFIABLES="$CONFIABLES,$TRACKER360_PROXY_IP" ;; esac
fi
if [ -n "$DOMAIN" ]; then ORIGENES="https://$DOMAIN"; else ORIGENES=""; fi
case "$APP_BIND" in 0.0.0.0|127.0.0.1) LOCAL=127.0.0.1 ;; *) LOCAL="$APP_BIND" ;; esac

# 2. Paquetes del sistema (sin compilador ni cabeceras de Python)
echo "Instalando paquetes del sistema..."
apt-get update -qq
apt-get install -y -qq python3 python3-venv postgresql postgresql-client openssl curl rsync git ca-certificates \
  iproute2 > /dev/null
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
  echo "Error: hace falta Python 3.11 o mas nuevo (este sistema tiene $(python3 --version 2>&1))." >&2
  echo "Sistemas soportados: Debian 12, Debian 13, Ubuntu 24.04." >&2
  exit 1
fi
# IP del servidor para el Proxy Host de Nginx Proxy Manager (o la interfaz elegida con TRACKER360_BIND).
if [ "$LOCAL" != "127.0.0.1" ]; then
  IP="$LOCAL"
else
  IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
  if [ -z "$IP" ]; then IP="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for (i = 1; i < NF; i++) if ($i == "src") print $(i + 1)}')"; fi
  IP="${IP:-<IP de este servidor>}"
fi

# El puerto local tiene que estar libre (por ejemplo, ocupado por la instalacion con Docker).
OCUPANTE="$(ss -ltnpH "( sport = :$APP_PORT )" 2>/dev/null || true)"
if [ -n "$OCUPANTE" ] && ! systemctl is-active --quiet "$SERVICE" 2>/dev/null; then
  echo "Error: el puerto $APP_PORT ya esta en uso:" >&2
  echo "$OCUPANTE" >&2
  echo "Si es Tracker360 con Docker (install.sh), no se pueden usar las dos instalaciones en el mismo puerto." >&2
  echo "Usar otro puerto con TRACKER360_PORT=8002 o detener la de Docker (docker compose down)." >&2
  exit 1
fi

# 3. Usuario de sistema sin login (no es dueno del codigo: solo lo lee)
if ! id "$APP_USER" &> /dev/null; then
  echo "Creando usuario de sistema $APP_USER..."
  useradd --system --no-create-home --home-dir "$BASE_DIR" --shell /usr/sbin/nologin "$APP_USER"
fi

# 4. Version (commit de git) y clon del que se actualiza
GIT="git -c safe.directory=*"
if $GIT -C "$SCRIPT_DIR" rev-parse --git-dir > /dev/null 2>&1; then
  VERSION="$($GIT -C "$SCRIPT_DIR" rev-parse --short=12 HEAD)"
  if [ -n "$($GIT -C "$SCRIPT_DIR" status --porcelain --untracked-files=no)" ]; then
    VERSION="$VERSION-local"   # con cambios sin commit: se instala igual, pero se marca
  fi
  REPO_URL="${TRACKER360_REPO_URL:-$($GIT -C "$SCRIPT_DIR" remote get-url origin 2>/dev/null || true)}"
else
  VERSION="local-$(date +%Y%m%d%H%M%S)"
  REPO_URL="${TRACKER360_REPO_URL:-}"
fi
REPO_URL="${REPO_URL:-$(valor_env TRACKER360_REPO_URL)}"
REPO_URL="${REPO_URL:-https://github.com/Jonnyonz/Tracker360.git}"
echo "Version a instalar: $VERSION"
mkdir -p "$BASE_DIR"
if [ ! -d "$SRC/.git" ]; then
  echo "Clonando $REPO_URL en $SRC (de ahi se actualiza)..."
  if ! $GIT clone --quiet "$REPO_URL" "$SRC"; then
    echo "Aviso: no se pudo clonar $REPO_URL; tracker360-actualizar no va a funcionar hasta que exista $SRC." >&2
  fi
fi

# 5. Codigo y entorno virtual de esta version
DEST="$RELEASES/$VERSION"
echo "Instalando la version $VERSION en $DEST..."
mkdir -p "$RELEASES"
rsync -a --delete --exclude '.git' --exclude 'venv' --exclude '.venv' --exclude 'wheelhouse' --exclude 'backups' \
  --exclude '__pycache__' --exclude '.env' --exclude '.serena' "$SCRIPT_DIR"/ "$DEST"/
echo "$VERSION" > "$DEST/.tracker360-version"
if [ ! -x "$DEST/venv/bin/python" ]; then
  python3 -m venv "$DEST/venv"
fi
PIP_ORIGEN=()
if [ -d "$SCRIPT_DIR/wheelhouse" ]; then
  echo "Usando wheelhouse/ (sin internet)."
  PIP_ORIGEN=(--no-index --find-links "$SCRIPT_DIR/wheelhouse")
fi
"$DEST/venv/bin/pip" install --quiet --disable-pip-version-check --require-hashes --only-binary=:all: \
  "${PIP_ORIGEN[@]}" -r "$DEST/backend/requirements.txt"
chown -R root:root "$DEST"
chmod -R a+rX,go-w "$DEST"

# 6. PostgreSQL: rol y base propios en el cluster del servidor
echo "Verificando PostgreSQL..."
systemctl enable --now postgresql > /dev/null
ROL_EXISTE=$(sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='$DB_USER'")
BASE_EXISTE=$(sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='$DB_NAME'")
if [ -f "$ENV_FILE" ]; then
  echo "Ya existe $ENV_FILE: se reutilizan los secretos (no se pisan)."
  DB_PASSWORD="$(valor_env POSTGRES_PASSWORD)"
  SETUP_TOKEN="$(valor_env SETUP_TOKEN)"
  SECRET_KEY="$(valor_env SECRET_KEY)"
  if [ "$ROL_EXISTE" != "1" ]; then
    echo "Error: $ENV_FILE existe pero el rol $DB_USER no existe en PostgreSQL. Revisar a mano." >&2
    exit 1
  fi
else
  if [ "$ROL_EXISTE" = "1" ]; then
    echo "Error: el rol $DB_USER ya existe en PostgreSQL pero no hay $ENV_FILE con su clave." >&2
    echo "No se genera una clave nueva porque romperia el acceso existente. Revisar a mano." >&2
    exit 1
  fi
  echo "Generando secretos..."
  DB_PASSWORD="$(openssl rand -hex 24)"
  SETUP_TOKEN="$(openssl rand -hex 24)"
  SECRET_KEY="$(openssl rand -hex 32)"
fi
if [ "$ROL_EXISTE" != "1" ]; then
  echo "Creando rol $DB_USER..."
  sudo -u postgres psql -q -v ON_ERROR_STOP=1 -c "CREATE ROLE $DB_USER LOGIN PASSWORD '$DB_PASSWORD';"
fi
if [ "$BASE_EXISTE" != "1" ]; then
  echo "Creando base $DB_NAME..."
  sudo -u postgres psql -q -v ON_ERROR_STOP=1 -c "CREATE DATABASE $DB_NAME OWNER $DB_USER;"
fi

# 7. Configuracion (se reescribe con los mismos secretos; root:tracker360 0640)
echo "Escribiendo $ENV_FILE..."
mkdir -p "$ENV_DIR"
ADICIONALES=""
if [ -f "$ENV_FILE" ]; then
  # Lo que el administrador agrego a mano (por ejemplo AGENT_DOWNLOAD_URL) se conserva.
  ADICIONALES="$(grep -Ev '^(#|$|POSTGRES_|SETUP_TOKEN=|SECRET_KEY=|TRUSTED_PROXIES=|ALLOWED_ORIGINS=|APP_PORT=|TRACKER360_(DOMAIN|BIND|IP|INSTALACION|REPO_URL)=|PYTHONDONTWRITEBYTECODE=|TZ=)' "$ENV_FILE" || true)"
fi
ZONA="$(valor_env TZ)"; ZONA="${ZONA:-${TZ:-America/Argentina/Buenos_Aires}}"
TMP_ENV="$(mktemp "$ENV_DIR/.env.XXXXXX")"
cat > "$TMP_ENV" <<EOF
# Generado por install-native.sh (se vuelve a escribir en cada instalacion; las lineas agregadas a mano
# al final se conservan). No versionar ni copiar a otro servidor tal cual.
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=5432
POSTGRES_DB=$DB_NAME
POSTGRES_USER=$DB_USER
POSTGRES_PASSWORD=$DB_PASSWORD
SECRET_KEY=$SECRET_KEY
SETUP_TOKEN=$SETUP_TOKEN
TRUSTED_PROXIES=$CONFIABLES
ALLOWED_ORIGINS=$ORIGENES
APP_PORT=$APP_PORT
TRACKER360_DOMAIN=$DOMAIN
TRACKER360_BIND=$APP_BIND
TRACKER360_INSTALACION=nativa
TRACKER360_REPO_URL=$REPO_URL
TZ=$ZONA
PYTHONDONTWRITEBYTECODE=1
EOF
if [ -n "$ADICIONALES" ]; then
  printf '%s\n' "$ADICIONALES" >> "$TMP_ENV"
fi
chown root:"$APP_USER" "$TMP_ENV"
chmod 640 "$TMP_ENV"
mv -f "$TMP_ENV" "$ENV_FILE"

# 8. Version en uso y servicio systemd
ln -sfn "$DEST" "$BASE_DIR/current.tmp"
mv -Tf "$BASE_DIR/current.tmp" "$BASE_DIR/current"

echo "Escribiendo el servicio systemd..."
cat > "/etc/systemd/system/$SERVICE.service" <<EOF
[Unit]
Description=Tracker360 (WMS)
After=network-online.target postgresql.service
Wants=network-online.target

[Service]
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$BASE_DIR/current
EnvironmentFile=$ENV_FILE
# Un solo worker: el envio de webhooks (outbox) corre en segundo plano dentro del proceso.
ExecStart=$BASE_DIR/current/venv/bin/uvicorn backend.main:app --host $APP_BIND --port $APP_PORT --workers 1 --no-proxy-headers
Restart=on-failure
RestartSec=5
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictSUIDSGID=yes
RestrictRealtime=yes
RestrictNamespaces=yes
LockPersonality=yes
SystemCallArchitectures=native
CapabilityBoundingSet=
AmbientCapabilities=
UMask=0077
MemoryMax=384M

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable "$SERVICE" > /dev/null
systemctl restart "$SERVICE"

echo "Esperando que el servicio responda..."
OK=0
for _ in $(seq 1 45); do
  if curl -fsS --max-time 5 "http://$LOCAL:$APP_PORT/api/auth/setup/status" > /dev/null 2>&1; then
    OK=1
    break
  fi
  sleep 2
done
if [ "$OK" != "1" ]; then
  echo "Error: el servicio no responde en http://$LOCAL:$APP_PORT." >&2
  echo "Ver el detalle con: journalctl -u $SERVICE -n 50 --no-pager" >&2
  exit 1
fi
echo "Servicio en marcha (version $VERSION)."

# 9. Actualizador
install -m 0755 "$DEST/tools/tracker360-actualizar" "$ACTUALIZADOR"

# 10. Caddy de versiones anteriores de este instalador: no se desinstala (puede usarlo otra app), se avisa.
CADDY_VIEJO=0
if [ -f /etc/caddy/Caddyfile ] && grep -qx '# tracker360' /etc/caddy/Caddyfile; then CADDY_VIEJO=1; fi

# 11. Resumen
ADMINS=$(sudo -u postgres psql -d "$DB_NAME" -tAc "SELECT count(*) FROM users WHERE role = 'ADMIN'" 2>/dev/null || echo 0)
echo ""
echo "================================================================="
echo "INSTALACION COMPLETADA - Tracker360 $VERSION"
echo "================================================================="
echo "Escuchando en: http://$IP:$APP_PORT"
if [ -n "$DOMAIN" ]; then
  echo "Direccion publica: https://$DOMAIN (tiene que llegar a http://$IP:$APP_PORT)"
fi
if [ "$ADMINS" = "0" ]; then
  echo ""
  echo "Token de configuracion inicial: $SETUP_TOKEN"
  echo "La pagina lo pide para crear el usuario administrador (sirve una sola vez)."
fi
if [ "$CADDY_VIEJO" = "1" ]; then
  echo ""
  echo "AVISO: quedo el Caddy que configuraba una version anterior de este instalador (no se desinstala)."
  echo "Ahora Tracker360 escucha directo en el puerto $APP_PORT; Caddy sigue ocupando el 80 y el 443."
  echo "Si ninguna otra app usa Caddy, sacarlo con:"
  echo "  sudo systemctl disable --now caddy"
  echo "  sudo apt purge caddy"
  echo "Si otra app lo sigue usando: borrar el bloque \"# tracker360\" de /etc/caddy/Caddyfile y"
  echo "  sudo systemctl reload caddy"
fi
echo ""
echo "Para actualizar mas adelante: sudo tracker360-actualizar"
echo "================================================================="
