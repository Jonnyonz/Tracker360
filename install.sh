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
    fi
fi

# 3. Generar archivo .env si no existe
if [ ! -f .env ]; then
    # Postgres solo aplica POSTGRES_PASSWORD la primera vez que inicializa su volumen de datos:
    # un .env nuevo no sirve contra un volumen de una instalacion anterior. Antes se borraba ese
    # volumen sin preguntar, y quien habia movido o perdido el .env perdia toda la base. Ahora,
    # si el volumen existe, no se toca nada salvo que se pida explicitamente.
    # Nombre del proyecto como lo calcula docker compose (no se puede usar "docker compose config":
    # falla justamente porque falta el .env que pide el env_file del compose).
    PROJECT="${COMPOSE_PROJECT_NAME:-$(basename "$PWD" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9_-')}"
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

    # Dominio publico del cliente: se puede pasar como TRACKER360_DOMAIN=... o se pregunta.
    DOMAIN="${TRACKER360_DOMAIN:-}"
    if [ -z "$DOMAIN" ] && [ -r /dev/tty ]; then
        read -r -p "Dominio publico de este servidor (ej. wms.suempresa.com, Enter para omitir): " DOMAIN < /dev/tty || DOMAIN=""
    fi
    if [ -n "$DOMAIN" ]; then
        ORIGINS_LINE="ALLOWED_ORIGINS=https://${DOMAIN}"
    else
        ORIGINS_LINE="# ALLOWED_ORIGINS=https://wms.suempresa.com"
    fi

    cat <<EOF > .env
# Ver .env.example para la descripcion de cada variable.
POSTGRES_USER=tracker_admin
POSTGRES_PASSWORD=${DB_PASS}
POSTGRES_DB=tracker360_db
SECRET_KEY=${SECRET_KEY}
SETUP_TOKEN=${SETUP_TOKEN}
# Dominio(s) del frontend permitidos por CORS (separados por coma).
${ORIGINS_LINE}
EOF
    echo "Archivo .env generado con contrasenas seguras."
else
    echo "Se detecto un archivo .env existente. Manteniendo configuracion."
fi

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

# 5. Construir y levantar contenedores con Docker Compose
echo "Desplegando servicios con Docker Compose..."
docker compose up -d --build

echo ""
echo "=================================================="
echo "Tracker360 se instalo e inicio correctamente"
echo "=================================================="
echo "Puedes acceder desde tu navegador en:"
if [ -n "${DOMAIN:-}" ]; then
    echo "https://${DOMAIN}"
else
    API_PORT_SHOWN=$(grep -E '^API_PORT=' .env 2>/dev/null | cut -d= -f2)
    echo "http://localhost:${API_PORT_SHOWN:-8001} o http://$(hostname -I | awk '{print $1}'):${API_PORT_SHOWN:-8001}"
fi
if [ -n "$SETUP_TOKEN" ]; then
    echo ""
    echo "Token de configuracion inicial: $SETUP_TOKEN"
    echo "Entra a la URL de arriba: te va a pedir este token para crear el usuario administrador."
    echo "Se usa una sola vez (despues de crear el admin, deja de servir)."
fi
echo "=================================================="
