#!/bin/bash
# ==============================================================================
# Instalador nativo (sin Docker) de Tracker360
# ==============================================================================
# Para Debian 12/13 y Ubuntu 24.04 (apt, Python 3.11 o mas nuevo). Correr como root desde la raiz de un
# clon del repositorio:
#
#   sudo ./install-native.sh                                   red interna: HTTPS por la IP del servidor
#   sudo TRACKER360_DOMAIN=wms.cliente.com ./install-native.sh   dominio publico: certificado automatico
#
# Queda asi:
#   /opt/tracker360/src/                 clon de git del que se actualiza (lo usa tracker360-actualizar)
#   /opt/tracker360/releases/<version>/  codigo + su propio venv (una carpeta por version; version = commit)
#   /opt/tracker360/current              enlace a la version en uso (el actualizador lo cambia)
#   /etc/tracker360/tracker360.env       configuracion y secretos (root:tracker360, 0640)
#   servicio systemd "tracker360"        uvicorn en 127.0.0.1:8001, un worker
#   base "tracker360_db" y rol "tracker360" propios en el PostgreSQL del servidor
#   Caddy delante con HTTPS (la sesion usa cookies Secure: sin HTTPS no se puede ingresar desde otra PC)
#   /usr/local/sbin/tracker360-actualizar  actualizador (respaldo, chequeo y vuelta atras)
#
# Idempotente: se puede volver a correr. Los secretos ya generados no se pisan. Sin compilador: las
# dependencias se instalan solo con paquetes binarios (wheels) verificando los hashes de requirements.txt;
# si hay una carpeta wheelhouse/ al lado, se usa esa (instalacion sin internet).
#
# Es independiente de la instalacion con Docker (install.sh): no se pueden usar las dos en el mismo puerto.
#
# Variables opcionales:
#   TRACKER360_DOMAIN=wms.cliente.com  dominio publico (Caddy saca el certificado solo; tiene que apuntar aca)
#   TRACKER360_IP=192.168.1.10         sin dominio: IP para el certificado local (por defecto, la primera IP)
#   TRACKER360_CADDY=0                 no instalar ni tocar Caddy (si el servidor ya usa otro proxy HTTPS)
#   TRACKER360_PORT=8001               puerto local del servicio
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
APP_BIND="127.0.0.1"
CADDY="${TRACKER360_CADDY:-1}"
ACTUALIZADOR="/usr/local/sbin/tracker360-actualizar"
CA_LOCAL="/var/lib/caddy/.local/share/caddy/pki/authorities/local/root.crt"

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
IP="${TRACKER360_IP:-$(valor_env TRACKER360_IP)}"
if [ -n "$DOMAIN" ] && ! [[ "$DOMAIN" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$ ]]; then
  echo "Error: dominio invalido: $DOMAIN" >&2
  exit 1
fi
if ! [[ "$APP_PORT" =~ ^[0-9]+$ ]]; then
  echo "Error: puerto invalido: $APP_PORT" >&2
  exit 1
fi

# 2. Paquetes del sistema (sin compilador ni cabeceras de Python)
echo "Instalando paquetes del sistema..."
apt-get update -qq
apt-get install -y -qq python3 python3-venv postgresql postgresql-client openssl curl rsync git ca-certificates \
  gnupg iproute2 > /dev/null
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
  echo "Error: hace falta Python 3.11 o mas nuevo (este sistema tiene $(python3 --version 2>&1))." >&2
  echo "Sistemas soportados: Debian 12, Debian 13, Ubuntu 24.04." >&2
  exit 1
fi
if [ -z "$DOMAIN" ] && [ -z "$IP" ]; then
  IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
fi
if [ -z "$DOMAIN" ] && ! [[ "$IP" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "Error: no se pudo saber la IP del servidor. Indicarla con TRACKER360_IP=192.168.1.10 (o usar TRACKER360_DOMAIN)." >&2
  exit 1
fi
if [ -n "$DOMAIN" ]; then SITIO="https://$DOMAIN"; else SITIO="https://$IP"; fi

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
  ADICIONALES="$(grep -Ev '^(#|$|POSTGRES_|SETUP_TOKEN=|SECRET_KEY=|TRUSTED_PROXIES=|ALLOWED_ORIGINS=|APP_PORT=|TRACKER360_(DOMAIN|IP|INSTALACION|REPO_URL)=|PYTHONDONTWRITEBYTECODE=|TZ=)' "$ENV_FILE" || true)"
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
TRUSTED_PROXIES=127.0.0.1/32,::1/128
ALLOWED_ORIGINS=$SITIO
APP_PORT=$APP_PORT
TRACKER360_DOMAIN=$DOMAIN
TRACKER360_IP=$IP
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
  if curl -fsS --max-time 5 "http://$APP_BIND:$APP_PORT/api/auth/setup/status" > /dev/null 2>&1; then
    OK=1
    break
  fi
  sleep 2
done
if [ "$OK" != "1" ]; then
  echo "Error: el servicio no responde en http://$APP_BIND:$APP_PORT." >&2
  echo "Ver el detalle con: journalctl -u $SERVICE -n 50 --no-pager" >&2
  exit 1
fi
echo "Servicio en marcha (version $VERSION)."

# 9. Actualizador
install -m 0755 "$DEST/tools/tracker360-actualizar" "$ACTUALIZADOR"

# 10. Proxy HTTPS (Caddy). Si el 443 lo usa otro programa, no se toca nada. Si el 80 lo usa otro programa
#     (por ejemplo Apache), Caddy se configura igual solo en el 443, sin redireccion desde http.
if [ -n "$DOMAIN" ]; then
  BLOQUE="$DOMAIN {
    reverse_proxy $APP_BIND:$APP_PORT
}"
else
  BLOQUE="https://$IP {
    tls internal
    reverse_proxy $APP_BIND:$APP_PORT
}"
fi
SIN_REDIRECCION=0
if [ "$CADDY" = "1" ]; then
  EN_443="$(ss -ltnpH '( sport = :443 )' 2>/dev/null | grep -v '"caddy"' || true)"
  EN_80="$(ss -ltnpH '( sport = :80 )' 2>/dev/null | grep -v '"caddy"' || true)"
  if [ -n "$EN_443" ]; then
    echo "Aviso: el puerto 443 lo usa otro programa; no se configura Caddy." >&2
    echo "$EN_443" >&2
    CADDY="0"
  elif [ -n "$EN_80" ]; then
    echo "Aviso: el puerto 80 lo usa otro programa; Caddy atiende solo HTTPS (443), sin redireccion desde http." >&2
    SIN_REDIRECCION=1
  fi
fi
if [ "$CADDY" = "1" ]; then
  CADDYFILE="/etc/caddy/Caddyfile"
  MARCA="# Gestionado por los instaladores nativos de JZTech"
  GLOBAL=""
  [ "$SIN_REDIRECCION" = "1" ] && GLOBAL="{
	auto_https disable_redirects
}
"
  mkdir -p /etc/caddy
  # Mismo criterio que los otros instaladores de JZTech: el Caddyfile de ejemplo compite por el puerto 80.
  # Se escribe antes de instalar Caddy para que arranque con este y no con el de ejemplo.
  if [ ! -f "$CADDYFILE" ] || ! grep -q "$MARCA" "$CADDYFILE"; then
    printf '%s%s\n%s\n' "$GLOBAL" "$MARCA (install-native.sh)." "# Cada app agrega su propio bloque de sitio abajo." > "$CADDYFILE"
  elif [ -n "$GLOBAL" ] && ! grep -q "auto_https disable_redirects" "$CADDYFILE"; then
    { printf '%s' "$GLOBAL"; cat "$CADDYFILE"; } > "$CADDYFILE.tmp" && mv -f "$CADDYFILE.tmp" "$CADDYFILE"
  fi
  PRIMERA="$(printf '%s\n' "$BLOQUE" | head -n1)"
  if ! grep -qxF "$PRIMERA" "$CADDYFILE"; then
    printf '\n# tracker360\n%s\n' "$BLOQUE" >> "$CADDYFILE"
  fi
  if ! command -v caddy &> /dev/null; then
    echo "Instalando Caddy..."
    if ! DEBIAN_FRONTEND=noninteractive apt-get install -y -qq -o Dpkg::Options::=--force-confold caddy > /dev/null 2>&1; then
      curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
        | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
      curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
      apt-get update -qq
      DEBIAN_FRONTEND=noninteractive apt-get install -y -qq -o Dpkg::Options::=--force-confold caddy > /dev/null
    fi
  fi
  if caddy validate --config "$CADDYFILE" --adapter caddyfile > /dev/null 2>&1; then
    systemctl enable --now caddy > /dev/null
    systemctl reload caddy 2> /dev/null || systemctl restart caddy
    # Caddy saca el certificado unos segundos despues de arrancar: se espera a que el HTTPS responda antes
    # de dar la direccion (con dominio publico, el certificado automatico puede tardar mas).
    if [ -n "$DOMAIN" ]; then
      PRUEBA=(--resolve "$DOMAIN:443:127.0.0.1" "https://$DOMAIN/api/auth/setup/status")
    else
      PRUEBA=("https://$IP/api/auth/setup/status")
    fi
    HTTPS_OK=0
    for _ in $(seq 1 30); do
      if curl -fsSk --max-time 5 "${PRUEBA[@]}" > /dev/null 2>&1; then HTTPS_OK=1; break; fi
      sleep 2
    done
    if [ "$HTTPS_OK" != "1" ]; then
      echo "Aviso: el HTTPS todavia no responde en $SITIO. Con dominio, revisar que apunte a este servidor;" >&2
      echo "el detalle esta en: journalctl -u caddy -n 50 --no-pager" >&2
    fi
  else
    echo "Aviso: el Caddyfile no valida; no se recargo Caddy. Revisar $CADDYFILE." >&2
  fi
fi

# 11. Resumen
ADMINS=$(sudo -u postgres psql -d "$DB_NAME" -tAc "SELECT count(*) FROM users WHERE role = 'ADMIN'" 2>/dev/null || echo 0)
echo ""
echo "================================================================="
echo "INSTALACION COMPLETADA - Tracker360 $VERSION"
echo "================================================================="
echo "Entrar desde el navegador: $SITIO"
if [ "$CADDY" != "1" ]; then
  echo "Caddy no se configuro. Agregar al proxy HTTPS del servidor el equivalente a:"
  echo "$BLOQUE"
elif [ -z "$DOMAIN" ]; then
  echo "Certificado de la CA local de Caddy: el navegador avisa que la conexion no es privada hasta que se"
  echo "instala en cada PC, colectora o celular el certificado raiz: $CA_LOCAL"
fi
if [ "$ADMINS" = "0" ]; then
  echo ""
  echo "Token de configuracion inicial: $SETUP_TOKEN"
  echo "La pagina lo pide para crear el usuario administrador (sirve una sola vez)."
fi
echo ""
echo "Para actualizar mas adelante: sudo tracker360-actualizar"
echo "================================================================="
