#!/bin/bash
set -e

echo "=================================================="
echo "  Instalador Automatico de Tracker360 WMS         "
echo "=================================================="

# 1. Verificar dependencias del sistema
if ! command -v docker &> /dev/null; then
    echo "ERROR: Docker no esta instalado en este servidor."
    echo "Por favor, instala Docker antes de continuar."
    exit 1
fi

if ! command -v git &> /dev/null; then
    echo "ERROR: Git no esta instalado."
    echo "Instalalo ejecutando: sudo apt update && sudo apt install git -y"
    exit 1
fi

# 2. Si no existen los archivos del proyecto, clonar el repositorio
#    (se puede apuntar a un fork propio con TRACKER360_REPO_URL=...)
REPO_URL="${TRACKER360_REPO_URL:-https://github.com/Jonnyonz/Tracker360.git}"
if [ ! -f "docker-compose.yml" ]; then
    if [ -f "tracker360/docker-compose.yml" ]; then
        # Instalado antes con "curl ... | bash" desde esta carpeta: se actualiza esa instalacion.
        cd tracker360
    else
        echo "Descargando codigo fuente desde ${REPO_URL}..."
        git clone "${REPO_URL}" tracker360
        cd tracker360
    fi
fi

# 2b. Actualizar: si ya es un repositorio, traer la version publicada antes de reconstruir (antes volver a
#     correr el instalador reconstruia la misma version). Solo avanza (--ff-only): con cambios locales o sin
#     conexion se detiene sin tocar nada. TRACKER360_NO_UPDATE=1 reconstruye la version que ya esta.
#     safe.directory: el repo suele ser de root (instalado con sudo) y git rechaza usarlo desde otro usuario.
if [ -d .git ] && [ "${TRACKER360_NO_UPDATE:-}" != "1" ]; then
    GIT="git -c safe.directory=$PWD"
    ANTES=$($GIT rev-parse --short HEAD)
    echo "Buscando actualizaciones..."
    if ! $GIT pull --ff-only --quiet; then
        echo "ERROR: no se pudo traer la version nueva (cambios locales en $PWD o sin conexion)."
        echo "No se toco nada: la version instalada sigue funcionando ($ANTES)."
        exit 1
    fi
    DESPUES=$($GIT rev-parse --short HEAD)
    if [ "$ANTES" = "$DESPUES" ]; then
        echo "Ya esta en la ultima version ($DESPUES)."
    else
        echo "Codigo actualizado: $ANTES -> $DESPUES (los cambios estan en CHANGELOG.md)."
        # bash sigue leyendo el install.sh que arranco: se relanza el recien bajado para que el resto de la
        # instalacion sea el de la version nueva.
        exec env TRACKER360_NO_UPDATE=1 bash ./install.sh "$@"
    fi
fi

# Nombre del proyecto como lo calcula docker compose (no se puede usar "docker compose config": falla si falta
# el .env que pide el env_file del compose).
PROJECT="${COMPOSE_PROJECT_NAME:-$(basename "$PWD" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9_-')}"

# 3. Generar archivo .env si no existe
NUEVA=0
if [ ! -f .env ]; then
    NUEVA=1
    # Postgres solo aplica POSTGRES_PASSWORD la primera vez que inicializa su volumen de datos:
    # un .env nuevo no sirve contra un volumen de una instalacion anterior. Antes se borraba ese
    # volumen sin preguntar, y quien habia movido o perdido el .env perdia toda la base. Ahora,
    # si el volumen existe, no se toca nada salvo que se pida explicitamente.
    VOLUME="${PROJECT}_tracker360_pgdata"
    if [ -n "$PROJECT" ] && docker volume inspect "$VOLUME" > /dev/null 2>&1; then
        if [ "${TRACKER360_RESET_DB:-}" = "1" ]; then
            echo "TRACKER360_RESET_DB=1: se BORRA la base de datos de la instalacion anterior (volumen $VOLUME)."
            docker compose down -v > /dev/null 2>&1 || true
        else
            echo "ERROR: hay una base de datos de una instalacion anterior (volumen $VOLUME) pero no hay .env."
            echo "No se borro nada. Opciones:"
            echo "  - Restaurar el .env de esa instalacion en esta carpeta y volver a correr ./install.sh"
            echo "  - Empezar de cero BORRANDO esos datos: TRACKER360_RESET_DB=1 ./install.sh"
            exit 1
        fi
    fi

    echo "Configurando variables de entorno y claves de seguridad (.env)..."
    DB_PASS=$(openssl rand -hex 16 2>/dev/null || tr -dc 'a-zA-Z0-9' < /dev/urandom | head -c 24)
    SECRET_KEY=$(openssl rand -hex 32 2>/dev/null || tr -dc 'a-zA-Z0-9' < /dev/urandom | head -c 48)
    SETUP_TOKEN=$(openssl rand -hex 12 2>/dev/null || tr -dc 'a-zA-Z0-9' < /dev/urandom | head -c 24)

    # Dominio publico del cliente: TRACKER360_DOMAIN=... o se pregunta.
    DOMAIN="${TRACKER360_DOMAIN:-}"
    if [ -z "$DOMAIN" ] && [ -r /dev/tty ]; then
        read -r -p "Dominio publico de este servidor (ej. wms.suempresa.com, Enter para omitir): " DOMAIN < /dev/tty || DOMAIN=""
    fi

    cat <<EOF > .env
# Ver .env.example para la descripcion de cada variable. TRACKER360_DOMAIN, ALLOWED_ORIGINS, API_BIND y
# TRUSTED_PROXIES los mantiene install.sh.
POSTGRES_USER=tracker_admin
POSTGRES_PASSWORD=${DB_PASS}
POSTGRES_DB=tracker360_db
SECRET_KEY=${SECRET_KEY}
SETUP_TOKEN=${SETUP_TOKEN}
# Dominio(s) del frontend permitidos por CORS (separados por coma).
# ALLOWED_ORIGINS=https://wms.suempresa.com
EOF
    echo "Archivo .env generado con contrasenas seguras."
else
    echo "Se detecto un archivo .env existente. Manteniendo configuracion."
fi

# 3b. HTTPS: lo da Nginx Proxy Manager (u otro proxy con el 80/443 del servidor), que reenvia a la API por http
#     en API_PORT. La sesion usa una cookie Secure: sin HTTPS no se puede ingresar desde otra PC.
#     TRACKER360_DOMAIN: dominio publico (queda en ALLOWED_ORIGINS). TRACKER360_BIND: interfaz donde escucha la
#     API (0.0.0.0 por defecto, para que el proxy llegue aunque sea un contenedor u otro equipo).
#     TRACKER360_PROXY_IP: IP del proxy si esta en otro equipo (se suma a TRUSTED_PROXIES).
leer() { grep -E "^$1=" .env 2>/dev/null | tail -n1 | cut -d= -f2- | tr -d '\r'; }
poner() { if grep -qE "^$1=" .env; then sed -i "s#^$1=.*#$1=$2#" .env; else printf '%s=%s\n' "$1" "$2" >> .env; fi; }
if [ -n "${TRACKER360_HTTPS:-}" ]; then
    echo "Aviso: TRACKER360_HTTPS ya no se usa (ya no hay Caddy); se ignora."
fi
DOMAIN="${DOMAIN:-${TRACKER360_DOMAIN:-$(leer TRACKER360_DOMAIN)}}"
if [ -z "$DOMAIN" ] && ! grep -qE '^TRACKER360_DOMAIN=' .env; then
    # Instalaciones anteriores guardaban el dominio solo en ALLOWED_ORIGINS (https://dominio).
    PREVIO="$(leer ALLOWED_ORIGINS | cut -d, -f1)"
    HOST_PREVIO="$(echo "$PREVIO" | sed -e 's#^https://##' -e 's#[:/].*$##')"
    if [[ "$PREVIO" == https://* ]] && [ "$HOST_PREVIO" != "localhost" ] && ! [[ "$HOST_PREVIO" =~ ^[0-9.]+$ ]]; then
        DOMAIN="$HOST_PREVIO"
    fi
fi
# Por si se escribio con https:// adelante o una barra al final.
DOMAIN="${DOMAIN#http://}"; DOMAIN="${DOMAIN#https://}"; DOMAIN="${DOMAIN%%/*}"
if [ -n "$DOMAIN" ] && ! [[ "$DOMAIN" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$ ]]; then
    echo "ERROR: dominio invalido: $DOMAIN"
    exit 1
fi

# Instalacion anterior con Caddy propio (versiones previas de este instalador): la API escuchaba solo en
# 127.0.0.1 porque se entraba por Caddy. Ahora tiene que poder llegar el proxy.
CON_CADDY=0
if grep -qE '^(CADDY_[A-Z_]*|TRACKER360_HTTPS)=' .env; then CON_CADDY=1; fi
if [ -n "${TRACKER360_BIND:-}" ]; then
    poner API_BIND "$TRACKER360_BIND"
elif ! grep -qE '^API_BIND=' .env || { [ "$CON_CADDY" = "1" ] && [ "$(leer API_BIND)" = "127.0.0.1" ]; }; then
    poner API_BIND 0.0.0.0
fi
if [ -n "${TRACKER360_PROXY_IP:-}" ]; then
    CONFIABLES="$(leer TRUSTED_PROXIES)"; CONFIABLES="${CONFIABLES:-127.0.0.1/32,::1/128,172.16.0.0/12}"
    case ",$CONFIABLES," in *",$TRACKER360_PROXY_IP,"*) ;; *) CONFIABLES="$CONFIABLES,$TRACKER360_PROXY_IP";; esac
    poner TRUSTED_PROXIES "$CONFIABLES"
fi
poner TRACKER360_DOMAIN "$DOMAIN"
if [ -n "$DOMAIN" ]; then
    SITIO="https://$DOMAIN"
    ORIGENES="$(leer ALLOWED_ORIGINS)"
    case ",$ORIGENES," in *",$SITIO,"*) ;; *) poner ALLOWED_ORIGINS "${ORIGENES:+$ORIGENES,}$SITIO";; esac
fi
# Claves de Caddy que ya no se usan (COMPOSE_PROFILES=https levantaba su contenedor).
sed -i -E -e '/^(CADDY_[A-Z_]*|TRACKER360_HTTPS|TRACKER360_IP)=/d' -e '/^COMPOSE_PROFILES=(https)?[[:space:]]*$/d' \
    -e 's/^(# Ver \.env\.example para la descripcion de cada variable\.) Lo de HTTPS .*/\1 TRACKER360_DOMAIN, API_BIND y TRUSTED_PROXIES los mantiene install.sh./' \
    -e '/^# lo mantiene install\.sh\.[[:space:]]*$/d' .env

# 4. Copia de la base antes de reconstruir (al arrancar, la API aplica las migraciones nuevas).
if docker compose ps --status running --services 2>/dev/null | grep -qx db; then
    mkdir -p backups
    COPIA="backups/antes_de_actualizar_$(date +%Y-%m-%d_%H%M%S).sql"
    # < /dev/null: con "curl ... | bash" el script llega por stdin y exec se comeria el resto del script.
    if docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' < /dev/null > "$COPIA"; then
        echo "Copia de la base de datos: $PWD/$COPIA"
    else
        rm -f "$COPIA"
        echo "ERROR: no se pudo copiar la base de datos; no se actualizo nada."
        exit 1
    fi
fi

# 4b. Caddy de versiones anteriores: se saca su contenedor (libera el 80/443 para Nginx Proxy Manager) y lo que
#     generaba el instalador. Sus volumenes (certificados) no se borran solos: se avisa al final.
SE_SACO_CADDY=0
if docker ps -a --format '{{.Names}}' 2>/dev/null | grep -qx tracker360_caddy; then
    echo "Sacando el contenedor de Caddy de la version anterior (tracker360_caddy)..."
    docker rm -f tracker360_caddy > /dev/null
    SE_SACO_CADDY=1
fi
if [ -d caddy ]; then
    rm -f caddy/Caddyfile caddy/ca-local.crt
    rmdir caddy 2> /dev/null || true
fi
VOLUMENES_CADDY="$(docker volume ls -q 2>/dev/null | grep -xE "${PROJECT}_tracker360_caddy_(data|config)" | tr '\n' ' ' || true)"

# 5. Construir y levantar contenedores con Docker Compose (--remove-orphans: saca servicios que ya no estan)
echo "Desplegando servicios con Docker Compose..."
docker compose up -d --build --remove-orphans

# 6. Esperar a que la API responda en su puerto (es lo que va a consultar el proxy).
API_PORT_SHOWN="$(leer API_PORT)"; API_PORT_SHOWN="${API_PORT_SHOWN:-8001}"
BIND="$(leer API_BIND)"
case "$BIND" in ""|0.0.0.0|127.0.0.1) LOCAL=127.0.0.1 ;; *) LOCAL="$BIND" ;; esac
API_OK=0
if command -v curl > /dev/null; then
    for _ in $(seq 1 45); do
        if curl -fsS --max-time 5 "http://$LOCAL:$API_PORT_SHOWN/api/auth/setup/status" > /dev/null 2>&1; then API_OK=1; break; fi
        sleep 2
    done
fi
# IP del servidor para el proxy (o la interfaz elegida con TRACKER360_BIND).
if [ "$LOCAL" != "127.0.0.1" ]; then
    IP_LOCAL="$LOCAL"
else
    IP_LOCAL="$(hostname -I 2>/dev/null | awk '{print $1}')"
    # Sin "hostname -I" (por ejemplo en Alpine): la IP de salida segun la tabla de rutas.
    if [ -z "$IP_LOCAL" ]; then IP_LOCAL="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for (i = 1; i < NF; i++) if ($i == "src") print $(i + 1)}')"; fi
    IP_LOCAL="${IP_LOCAL:-<IP de este servidor>}"
fi

echo ""
echo "=================================================="
echo "Tracker360 se instalo e inicio correctamente"
echo "=================================================="
echo "Escuchando en: http://$IP_LOCAL:$API_PORT_SHOWN"
if [ -n "$DOMAIN" ]; then
    echo "Direccion publica: $SITIO (tiene que llegar a http://$IP_LOCAL:$API_PORT_SHOWN)"
fi
if [ "$API_OK" != "1" ]; then
    echo "ATENCION: la API todavia no responde en el puerto $API_PORT_SHOWN. Ver: docker compose logs api"
fi
if [ -n "$SETUP_TOKEN" ]; then
    echo ""
    echo "Token de configuracion inicial: $SETUP_TOKEN"
    echo "Entra a la URL de arriba: te va a pedir este token para crear el usuario administrador."
    echo "Se usa una sola vez (despues de crear el admin, deja de servir)."
fi
if [ "$SE_SACO_CADDY" = "1" ]; then
    echo ""
    echo "Se saco el Caddy de la version anterior (contenedor tracker360_caddy): los puertos 80 y 443 quedaron libres."
fi
if [ -n "$VOLUMENES_CADDY" ]; then
    echo "Quedaron los volumenes del Caddy anterior (sus certificados). Para borrarlos: docker volume rm $VOLUMENES_CADDY"
fi
echo "=================================================="
