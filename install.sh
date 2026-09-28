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
    echo "Descargando codigo fuente desde ${REPO_URL}..."
    git clone "${REPO_URL}" tracker360
    cd tracker360
fi

# 3. Generar archivo .env si no existe
if [ ! -f .env ]; then
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

    # Postgres solo aplica POSTGRES_PASSWORD la primera vez que inicializa su volumen de datos.
    # Si quedo un volumen de una instalacion anterior con otra contrasena, la app nunca podria
    # autenticarse. Como aca se acaba de generar un .env nuevo (instalacion desde cero), nos
    # aseguramos de que no sobreviva un volumen viejo con credenciales que ya no coinciden.
    echo "Verificando que no quede un volumen de base de datos de una instalacion anterior..."
    docker compose down -v > /dev/null 2>&1 || true
else
    echo "Se detecto un archivo .env existente. Manteniendo configuracion."
fi

# 4. Construir y levantar contenedores con Docker Compose
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
