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

# 3. Generar archivo .env si no existe
NUEVA=0
if [ ! -f .env ]; then
    NUEVA=1
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
# Ver .env.example para la descripcion de cada variable. Lo de HTTPS (TRACKER360_*, CADDY_*, COMPOSE_PROFILES)
# lo mantiene install.sh.
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

# 3b. HTTPS con Caddy (servicio "caddy" del compose, perfil https). La sesion usa una cookie Secure: sin HTTPS no
#     se puede ingresar desde otra PC. Con dominio, Caddy saca el certificado solo; sin dominio usa la IP del
#     servidor con su CA local. Si el 443 ya esta en uso (otra app en este servidor), usa el 8443, 9443 o 10443. Se desactiva
#     con TRACKER360_HTTPS=no (queda como antes: http en el puerto de la API). Detras de un proxy que ya tiene el 443
#     (Nginx Proxy Manager, Traefik...): TRACKER360_HTTPS=proxy TRACKER360_DOMAIN=wms.suempresa.com, sin Caddy
#     propio; el proxy da el HTTPS y reenvia a http://<este servidor>:API_PORT (TRACKER360_BIND cambia donde
#     escucha, 0.0.0.0 por defecto; TRACKER360_PROXY_IP, la IP del proxy si esta en otro equipo).
leer() { grep -E "^$1=" .env 2>/dev/null | tail -n1 | cut -d= -f2- | tr -d '\r'; }
poner() { if grep -qE "^$1=" .env; then sed -i "s#^$1=.*#$1=$2#" .env; else printf '%s=%s\n' "$1" "$2" >> .env; fi; }
HTTPS="${TRACKER360_HTTPS:-$(leer TRACKER360_HTTPS)}"; HTTPS="${HTTPS:-si}"
NO_SE_PUDO=0
CADDY_CORRIENDO=0
if docker compose ps --status running --services 2>/dev/null | grep -qx caddy; then CADDY_CORRIENDO=1; fi
if [ "$HTTPS" = "proxy" ]; then
    DOMAIN="${DOMAIN:-${TRACKER360_DOMAIN:-$(leer TRACKER360_DOMAIN)}}"
    if [ -z "$DOMAIN" ]; then
        PREVIO="$(leer ALLOWED_ORIGINS | cut -d, -f1)"
        if [[ "$PREVIO" == https://* ]]; then DOMAIN="$(echo "$PREVIO" | sed -e 's#^https://##' -e 's#[:/].*$##')"; fi
    fi
    if [ -z "$DOMAIN" ]; then
        echo "ERROR: detras de un proxy hace falta el dominio publico de Tracker360:"
        echo "  TRACKER360_HTTPS=proxy TRACKER360_DOMAIN=wms.suempresa.com ./install.sh"
        exit 1
    fi
fi
if [ "$HTTPS" = "si" ]; then
    DOMAIN="${DOMAIN:-${TRACKER360_DOMAIN:-$(leer TRACKER360_DOMAIN)}}"
    if [ -z "$DOMAIN" ] && ! grep -qE '^TRACKER360_DOMAIN=' .env; then
        # Instalaciones anteriores guardaban el dominio solo en ALLOWED_ORIGINS (https://dominio).
        PREVIO="$(leer ALLOWED_ORIGINS | cut -d, -f1)"
        HOST_PREVIO="$(echo "$PREVIO" | sed -e 's#^https://##' -e 's#[:/].*$##')"
        if [[ "$PREVIO" == https://* ]] && [ "$HOST_PREVIO" != "localhost" ] && ! [[ "$HOST_PREVIO" =~ ^[0-9.]+$ ]]; then
            DOMAIN="$HOST_PREVIO"
        fi
    fi
    IP="${TRACKER360_IP:-$(leer TRACKER360_IP)}"
    if [ -z "$DOMAIN" ] && [ -z "$IP" ]; then
        IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
        # Sin "hostname -I" (por ejemplo en Alpine): la IP de salida segun la tabla de rutas.
        if [ -z "$IP" ]; then IP="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for (i = 1; i < NF; i++) if ($i == "src") print $(i + 1)}')"; fi
    fi
    if [ -z "$DOMAIN" ] && ! [[ "$IP" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        echo "AVISO: no se pudo saber la IP del servidor; no se configura HTTPS (indicarla con TRACKER360_IP=192.168.1.10)."
        HTTPS="no"; NO_SE_PUDO=1
    fi
fi
if [ "$HTTPS" = "si" ]; then
    # Un puerto esta "ocupado" si lo usa otro programa (el Caddy de esta instalacion no cuenta).
    ocupado() { [ "$CADDY_CORRIENDO" = "0" ] && command -v ss > /dev/null && [ -n "$(ss -ltnH "( sport = :$1 )" 2>/dev/null)" ]; }
    PUERTO_HTTPS="${TRACKER360_HTTPS_PORT:-$(leer CADDY_HTTPS_PORT)}"
    if [ -z "$PUERTO_HTTPS" ]; then
        for P in 443 8443 9443 10443; do
            PUERTO_HTTPS="$P"
            if ! ocupado "$P"; then break; fi
        done
    fi
    if ocupado "$PUERTO_HTTPS"; then
        echo "AVISO: el puerto $PUERTO_HTTPS ya esta en uso; no se configura HTTPS (elegir otro con TRACKER360_HTTPS_PORT=...)."
        HTTPS="no"; NO_SE_PUDO=1
    fi
fi
if [ "$HTTPS" = "si" ]; then
    # Puerto 80: solo si HTTPS va en el 443 y el 80 esta libre (redirige http -> https). Si no, el 80 de Caddy
    # queda en un puerto local al azar y sin redireccion.
    if grep -qE '^CADDY_HTTP_PORT=' .env; then
        PUERTO_HTTP="$(leer CADDY_HTTP_PORT)"; BIND_HTTP="$(leer CADDY_HTTP_BIND)"
    elif [ "$PUERTO_HTTPS" = "443" ] && ! ocupado 80; then
        PUERTO_HTTP=80; BIND_HTTP=0.0.0.0
    else
        PUERTO_HTTP=""; BIND_HTTP=127.0.0.1
    fi
    if [ -n "$DOMAIN" ]; then HOST="$DOMAIN"; else HOST="$IP"; fi
    SITIO="https://$HOST"
    if [ "$PUERTO_HTTPS" != "443" ]; then SITIO="$SITIO:$PUERTO_HTTPS"; fi
    mkdir -p caddy
    {
        # Opciones globales. default_sni: por IP el navegador no manda el nombre del sitio (SNI) y Caddy, dentro del
        # contenedor, no ve la IP del servidor: sin esto no sabe que certificado dar y corta la conexion.
        GLOBALES=""
        if [ "$PUERTO_HTTP" != "80" ] || [ "$PUERTO_HTTPS" != "443" ]; then GLOBALES="${GLOBALES}	auto_https disable_redirects
"; fi
        if [ -z "$DOMAIN" ]; then GLOBALES="${GLOBALES}	default_sni $IP
"; fi
        if [ -n "$GLOBALES" ]; then printf '{\n%s}\n\n' "$GLOBALES"; fi
        echo "# Generado por install.sh: se vuelve a escribir en cada corrida (no editar a mano)."
        if [ -n "$DOMAIN" ]; then
            printf '%s {\n\treverse_proxy api:8000\n}\n' "$DOMAIN"
        else
            printf 'https://%s, https://localhost {\n\ttls internal\n\treverse_proxy api:8000\n}\n' "$IP"
        fi
    } > caddy/Caddyfile
    poner TRACKER360_HTTPS si
    poner TRACKER360_DOMAIN "$DOMAIN"
    poner TRACKER360_IP "$IP"
    poner COMPOSE_PROFILES https
    poner CADDY_HTTPS_PORT "$PUERTO_HTTPS"
    poner CADDY_HTTP_PORT "$PUERTO_HTTP"
    poner CADDY_HTTP_BIND "$BIND_HTTP"
    ORIGENES="$(leer ALLOWED_ORIGINS)"
    case ",$ORIGENES," in *",$SITIO,"*) ;; *) poner ALLOWED_ORIGINS "${ORIGENES:+$ORIGENES,}$SITIO";; esac
    # Instalacion nueva: la API solo en el propio servidor (se entra por Caddy). Las existentes no se cambian.
    if [ "$NUEVA" = "1" ]; then poner API_BIND 127.0.0.1; fi
elif [ "$HTTPS" = "proxy" ]; then
    # Detras de un proxy que ya tiene el 443: sin Caddy propio. La API escucha en todas las interfaces para que el
    # proxy llegue (aunque sea un contenedor) y el navegador entra por https://DOMAIN (la cookie Secure funciona).
    SITIO="https://$DOMAIN"
    if [ -n "${TRACKER360_BIND:-}" ]; then poner API_BIND "$TRACKER360_BIND"
    elif [ "$(leer TRACKER360_HTTPS)" != "proxy" ]; then poner API_BIND 0.0.0.0; fi
    if [ -n "${TRACKER360_PROXY_IP:-}" ]; then
        CONFIABLES="$(leer TRUSTED_PROXIES)"; CONFIABLES="${CONFIABLES:-127.0.0.1/32,::1/128,172.16.0.0/12}"
        case ",$CONFIABLES," in *",$TRACKER360_PROXY_IP,"*) ;; *) poner TRUSTED_PROXIES "$CONFIABLES,$TRACKER360_PROXY_IP";; esac
    fi
    poner TRACKER360_HTTPS proxy
    poner TRACKER360_DOMAIN "$DOMAIN"
    poner COMPOSE_PROFILES ""
    ORIGENES="$(leer ALLOWED_ORIGINS)"
    case ",$ORIGENES," in *",$SITIO,"*) ;; *) poner ALLOWED_ORIGINS "${ORIGENES:+$ORIGENES,}$SITIO";; esac
    if [ "$CADDY_CORRIENDO" = "1" ]; then docker compose --profile https stop caddy > /dev/null 2>&1 || true; fi
else
    # "no" queda guardado solo si lo eligio el usuario; si no se pudo (puertos o IP), se reintenta la proxima vez.
    if [ "${NO_SE_PUDO:-0}" != "1" ]; then poner TRACKER360_HTTPS no; fi
    poner COMPOSE_PROFILES ""
    if [ "$CADDY_CORRIENDO" = "1" ]; then docker compose --profile https stop caddy > /dev/null 2>&1 || true; fi
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

# 6. HTTPS: esperar el certificado (Caddy lo saca unos segundos despues de arrancar) y, sin dominio, copiar el
#    certificado raiz de la CA local para instalarlo en las PCs y celulares.
HTTPS_OK=0
if [ "$HTTPS" = "si" ] && command -v curl > /dev/null; then
    for _ in $(seq 1 30); do
        if curl -fsSk --max-time 5 --resolve "$HOST:$PUERTO_HTTPS:127.0.0.1" "https://$HOST:$PUERTO_HTTPS/api/auth/setup/status" > /dev/null 2>&1; then
            HTTPS_OK=1; break
        fi
        sleep 2
    done
    if [ -z "$DOMAIN" ]; then
        docker compose cp caddy:/data/caddy/pki/authorities/local/root.crt caddy/ca-local.crt > /dev/null 2>&1 || true
    fi
fi
# Detras de un proxy: esperar a que la API responda en su puerto (es lo que va a consultar el proxy).
PROXY_OK=0
if [ "$HTTPS" = "proxy" ] && command -v curl > /dev/null; then
    PUERTO_API="$(leer API_PORT)"; PUERTO_API="${PUERTO_API:-8001}"
    for _ in $(seq 1 45); do
        if curl -fsS --max-time 5 "http://127.0.0.1:$PUERTO_API/api/auth/setup/status" > /dev/null 2>&1; then PROXY_OK=1; break; fi
        sleep 2
    done
fi

echo ""
echo "=================================================="
echo "Tracker360 se instalo e inicio correctamente"
echo "=================================================="
echo "Puedes acceder desde tu navegador en:"
API_PORT_SHOWN=$(grep -E '^API_PORT=' .env 2>/dev/null | cut -d= -f2)
if [ "$HTTPS" = "si" ] || [ "$HTTPS" = "proxy" ]; then
    echo "$SITIO"
else
    echo "http://localhost:${API_PORT_SHOWN:-8001} o http://$(hostname -I | awk '{print $1}'):${API_PORT_SHOWN:-8001}"
fi
if [ -n "$SETUP_TOKEN" ]; then
    echo ""
    echo "Token de configuracion inicial: $SETUP_TOKEN"
    echo "Entra a la URL de arriba: te va a pedir este token para crear el usuario administrador."
    echo "Se usa una sola vez (despues de crear el admin, deja de servir)."
fi
echo ""
echo "------------------------------ AVISO HTTPS ------------------------------"
if [ "$HTTPS" = "si" ]; then
    echo "Se configuro HTTPS con Caddy (contenedor tracker360_caddy): $SITIO"
    if [ "$HTTPS_OK" != "1" ]; then
        echo "ATENCION: el HTTPS todavia no responde. Ver: docker compose logs caddy"
    fi
    if [ -n "$DOMAIN" ]; then
        echo "- El certificado lo saca Caddy solo: el dominio $DOMAIN tiene que apuntar a este servidor y"
        echo "  los puertos 80 y 443 tienen que llegar desde internet."
    else
        echo "- Sin dominio, el certificado es de la CA local de Caddy: el navegador avisa que la conexion no es"
        echo "  privada hasta que se instala en cada PC, celular o colectora el certificado raiz:"
        echo "  $PWD/caddy/ca-local.crt (o aceptar la excepcion del navegador para probar)."
        echo "- Para usar un dominio: TRACKER360_DOMAIN=wms.suempresa.com ./install.sh"
    fi
    if [ "$PUERTO_HTTPS" != "443" ]; then
        echo "- El puerto 443 lo usa otro programa de este servidor: HTTPS quedo en el $PUERTO_HTTPS."
    fi
    echo "- Para no usar HTTPS (solo http en el puerto ${API_PORT_SHOWN:-8001}): TRACKER360_HTTPS=no ./install.sh"
elif [ "$HTTPS" = "proxy" ]; then
    IP_LOCAL="$(hostname -I 2>/dev/null | awk '{print $1}')"
    if [ -z "$IP_LOCAL" ]; then IP_LOCAL="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for (i = 1; i < NF; i++) if ($i == "src") print $(i + 1)}')"; fi
    echo "Sin Caddy propio: el HTTPS lo da el proxy de este servidor (por ejemplo Nginx Proxy Manager)."
    if [ "$PROXY_OK" != "1" ]; then echo "ATENCION: la API todavia no responde en el puerto ${API_PORT_SHOWN:-8001}. Ver: docker compose logs api"; fi
    echo "- En el proxy: $DOMAIN -> http://${IP_LOCAL:-<IP de este servidor>}:${API_PORT_SHOWN:-8001} (esquema http),"
    echo "  con certificado SSL (Let's Encrypt), Force SSL y soporte de WebSockets."
    echo "- El puerto ${API_PORT_SHOWN:-8001} queda abierto por http en la red local: conviene que el firewall solo"
    echo "  deje entrar al proxy. Si el proxy esta en OTRO equipo: TRACKER360_PROXY_IP=<su IP> ./install.sh"
else
    echo "HTTPS desactivado: se entra por http en el puerto ${API_PORT_SHOWN:-8001}."
    echo "Desde otra PC no se puede iniciar sesion por http (la cookie de sesion es Secure)."
    echo "Para activarlo: TRACKER360_HTTPS=si ./install.sh"
fi
echo "=================================================="
