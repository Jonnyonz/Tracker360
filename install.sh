#!/bin/bash
set -e

echo "=================================================="
echo "  Instalador Automático de Tracker360 WMS     "
echo "=================================================="

# 1. Verificar si Docker está instalado
if ! command -v docker &> /dev/null; then
    echo " Error: Docker no está instalado en este servidor."
    echo "Por favor, instala Docker y Docker Compose antes de continuar."
    exit 1
fi

# 2. Generar archivo .env si no existe
if [ ! -f .env ]; then
    echo " Configurando variables de entorno y claves de seguridad (.env)..."
    DB_PASS=$(openssl rand -hex 16 2>/dev/null || tr -dc 'a-zA-Z0-9' < /dev/urandom | head -c 24)
    SECRET_KEY=$(openssl rand -hex 32 2>/dev/null || tr -dc 'a-zA-Z0-9' < /dev/urandom | head -c 48)

    cat <<EOF > .env
POSTGRES_USER=tracker_admin
POSTGRES_PASSWORD=${DB_PASS}
POSTGRES_DB=tracker360_db
SECRET_KEY=${SECRET_KEY}
EOF
    echo " Archivo .env generado con contraseñas seguras."
else
    echo " Se detectó un archivo .env existente. Manteniendo configuración."
fi

# 3. Construir y levantar contenedores
echo " Desplegando servicios con Docker Compose..."
docker compose up -d --build

echo ""
echo "=================================================="
echo " ¡Tracker360 se instaló e inició correctamente!"
echo "=================================================="
echo "Puedes acceder desde tu navegador en:"
echo "👉 http://localhost  o  http://$(hostname -I | awk '{print $1}')"
echo "=================================================="
