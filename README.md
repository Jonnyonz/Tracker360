# Tracker360 WMS

Sistema de gestión de depósitos (WMS) liviano: sucursales, sectores y ubicaciones, maestro de
artículos, stock con kardex, recepción de mercadería, armado y despacho de pedidos (picking y
packing) con etiquetas ZPL, traspasos internos, conteos cíclicos y devoluciones. Incluye una
vista móvil para operarios con lectura de códigos por cámara y un agente de impresión para
Windows. Parte de **JZTech Suite**. Software libre, pensado para correr en una PC de oficina
común.

- [Funcionalidades](#funcionalidades)
- [Detalle técnico](#detalle-técnico)
- [Instalación rápida con Docker](#instalación-rápida-con-docker)
- [Instalación local (sin Docker)](#instalación-local-sin-docker)
- [Acceso desde la red (HTTPS)](#acceso-desde-la-red-https)
- [Configuración](#configuración)
- [Agente de impresión](#agente-de-impresión)
- [Operación](#operación)
- [Tests](#tests)
- [Estado y limitaciones conocidas](#estado-y-limitaciones-conocidas)
- [Contribuir y licencia](#contribuir-y-licencia)

---

## Funcionalidades

| Rol | Pantalla | Qué puede hacer |
|---|---|---|
| `ADMIN` | `/admin` (panel) | Todo: usuarios, sucursales/sectores/ubicaciones, artículos (combos, ubicaciones fijas, importación CSV), stock y kardex, pedidos de venta, traspasos, conteos, reportes, configuración, agentes de impresión, logs de webhooks. |
| `PREPARADOR` | `/mobile` (colectora o celular) | Recepción de remitos, picking y packing de pedidos, traspasos, conteos asignados. Lectura de códigos con la cámara (QR, Code 128, EAN-13, Code 39, DataMatrix). |
| `SUPERVISOR` | `/admin` (consulta) | Consulta todo lo operativo sin modificarlo: dashboard, artículos, stock y traza, reportes, depósitos, clientes y proveedores, compras, traspasos. Controla los pedidos: estado, avance y quiénes participaron (quién pickeó y cuántas unidades, quién lo creó y lo despachó). También opera conteos y autoriza agentes de impresión. No ve usuarios, configuración ni logs, y no crea, edita ni cancela nada (sí puede agregar observaciones a los documentos). |

Flujos principales:

- **Recepción:** escaneo de un remito de proveedor contra lo esperado; el stock entra a la
  ubicación sugerida (putaway).
- **Salida:** pedido de venta → picking (por pedido u olas) → packing → despacho. Al despachar
  se imprime la etiqueta y se avisa por webhook.
- **Cancelación (solo admin):** total o parcial, antes del despacho. Lo ya pickeado de la parte
  cancelada vuelve al stock exactamente como estaba antes de prepararlo (misma ubicación, lote,
  condición y números de serie), con el movimiento `Retroceso de PDV ID:<pedido>` en la traza.
- **Interno:** traspasos entre sectores (ODT) con sugerencias de reposición; conteos cíclicos
  por sector con revisión y ajuste; control puntual de stock.
- **Observaciones:** cada pedido, traspaso, remito, orden de compra y devolución lleva sus
  observaciones, visibles dentro del documento. Las escribe quien puede ver el documento o el
  propio sistema (marcadas como "Sistema"); no se editan ni se borran.
- **Integración:** webhooks salientes de stock y despacho (`OUTBOUND_STOCK`,
  `OUTBOUND_DESPACHO`), con historial y reintento. Operaciones con clave de idempotencia
  (`X-Idempotency-Key`) donde importa no duplicar.

La documentación interactiva de la API queda en `/docs` (Swagger) y `/redoc`.

---

## Detalle técnico

### Stack

| Componente | Versión |
|---|---|
| Python | 3.11 (imagen `python:3.11-slim`) |
| FastAPI / Uvicorn | 0.109.2 / 0.27.0 |
| asyncpg | 0.29.0 (SQL directo, sin ORM) |
| Hash de claves | passlib 1.7.4 + argon2-cffi 23.1.0 (Argon2) |
| Sesiones | PyJWT 2.8.0 |
| PostgreSQL | 15 (imagen `postgres:15-alpine`) |

### Estructura

```
Tracker360/
├── backend/
│   ├── main.py              # App: middlewares, rutas de vistas por rol, estáticos
│   ├── database.py          # Seguridad, pool, esquema, movimientos de stock, webhooks
│   ├── routers/             # auth, users, entities, items, warehouse, settings, printing,
│   │                        # inbound, outbound, internal, inventory, dashboard, reports,
│   │                        # rfid, updater, notes
│   ├── test_architecture.py
│   ├── requirements.txt
│   └── Dockerfile           # se construye desde la raíz del repo
├── frontend/                # index.html (login/setup), admin.html, preparador.html,
│                            # agent-auth.html, js/, css/ (sin build, lo sirve la API)
├── agent/                   # Agente de impresión para Windows (código fuente)
├── docker-compose.yml
├── install.sh
└── .env.example
```

### Modelo de datos

El esquema se crea y actualiza solo al arrancar (`CREATE/ALTER ... IF NOT EXISTS` en
`database.py`), junto con la configuración por defecto, la sucursal `SUC-01` y una clave API
inicial. Grupos de tablas:

| Área | Tablas |
|---|---|
| Usuarios y seguridad | `users`, `auth_rate_limits`, `audit_logs`, `api_idempotency_keys`, `inbound_api_keys` |
| Depósito | `branches`, `sectors` (cada uno con su cola de impresión), `locations` |
| Artículos | `items`, `item_combos`, `item_locations`, `item_serials`, `rfid_tags` |
| Stock | `stock_inventory` (por sucursal/sector/ubicación/SKU/lote/condición), `stock_movements` (kardex) |
| Salida | `documents`, `document_lines` |
| Compras | `purchase_orders`, `purchase_remitos`, `purchase_invoices` y sus líneas y vínculos |
| Interno | `transfer_orders`, `transfer_order_lines`, `inventory_sessions`, `inventory_snapshots`, `inventory_counts` |
| Devoluciones | `customer_returns`, `customer_return_lines` |
| Integración e impresión | `system_settings`, `integration_channels`, `webhook_logs`, `print_jobs`, `print_agent_auth_codes`, `print_agent_tokens` |
| Observaciones | `document_notes` (tipo y id del documento; solo se agregan) |
| Otros | `entities`, `entity_addresses` (clientes y proveedores) |

### Seguridad

- **Sesión:** JWT firmado con `SECRET_KEY` en una cookie `HttpOnly` + `Secure` +
  `SameSite=Strict` (4 horas). Cada usuario tiene un `token_version`: el logout, el cambio de
  clave o la desactivación lo incrementan e invalidan todas sus sesiones.
- **Claves con Argon2.** Las claves API y los tokens del agente se guardan hasheados.
- **HTTPS obligatorio** fuera de la red local: la app rechaza (403) toda conexión que no sea
  HTTPS salvo desde IPs privadas o loopback. `X-Forwarded-For`/`X-Forwarded-Proto` solo se
  aceptan de los proxies listados en `TRUSTED_PROXIES`.
- **Fuerza bruta:** límite de intentos de login por IP (configurable: 5 intentos, 15 minutos).
- **Cabeceras:** CSP, `X-Frame-Options: DENY`, `X-Content-Type-Options`, HSTS,
  `Referrer-Policy`.
- **Webhooks con protección anti-SSRF:** no pueden apuntar a direcciones internas (se valida
  también en cada redirección) salvo `WEBHOOK_ALLOW_PRIVATE=true`.
- **Configuración inicial con token:** el primer administrador solo se crea con el
  `SETUP_TOKEN`, y una sola vez.
- **Google SSO opcional** (Google Identity Services): se verifica el token contra Google
  (audiencia, emisor, vencimiento, email verificado y dominio permitido). Una cuenta nueva entra
  inactiva hasta que un admin la aprueba.
- **Contenedor endurecido:** usuario no root, filesystem de solo lectura, `no-new-privileges`
  y límites de memoria.

---

## Instalación rápida con Docker

Requisitos: Linux con Docker y el plugin `docker compose`, `git` y `openssl`.

### Opción A: instalador

```bash
git clone https://github.com/Jonnyonz/Tracker360.git
cd Tracker360
./install.sh
```

(También funciona sin clonar antes: `curl -fsSL https://raw.githubusercontent.com/Jonnyonz/Tracker360/main/install.sh | bash`
clona el repo en `./tracker360`.)

El instalador genera el `.env` con clave de base, `SECRET_KEY` y `SETUP_TOKEN` aleatorios,
pregunta el dominio (para `ALLOWED_ORIGINS`), levanta los contenedores y muestra la URL y el
`SETUP_TOKEN`.

Se puede volver a correr para actualizar: si ya hay un `.env`, lo respeta. Si no hay `.env`
pero sí quedó la base de una instalación anterior, **se detiene sin borrar nada** y explica las
opciones: restaurar el `.env` anterior, o empezar de cero borrando esos datos con
`TRACKER360_RESET_DB=1 ./install.sh`.

### Opción B: a mano

```bash
git clone https://github.com/Jonnyonz/Tracker360.git
cd Tracker360
cp .env.example .env
# Completar en .env: POSTGRES_PASSWORD (openssl rand -hex 16), SECRET_KEY (openssl rand -hex 32),
# SETUP_TOKEN (openssl rand -hex 24) y ALLOWED_ORIGINS con la URL con la que se va a entrar.
docker compose up -d --build
```

### Primer ingreso

1. Abrir `http://localhost:8001` en el propio servidor (o la URL HTTPS si ya hay proxy).
2. La pantalla detecta que no hay usuarios y pide el `SETUP_TOKEN`.
3. Crear el administrador (clave de al menos 8 caracteres). El token deja de servir después.
4. En el panel: cargar sucursales, sectores, ubicaciones, artículos (se pueden importar por
   CSV) y usuarios.

---

## Instalación local (sin Docker)

Para desarrollo o servidores sin Docker. Probado en Debian 12 (Python 3.11, PostgreSQL 15).
La app se ejecuta desde la **raíz** del repo (`backend.main:app`).

```bash
sudo apt install -y python3 python3-venv postgresql git openssl

# Base de datos (reemplazar CLAVE: openssl rand -hex 16). El esquema lo crea la app sola.
sudo -u postgres psql -c "CREATE USER tracker_admin WITH PASSWORD 'CLAVE';"
sudo -u postgres psql -c "CREATE DATABASE tracker360_db OWNER tracker_admin;"

git clone https://github.com/Jonnyonz/Tracker360.git
cd Tracker360
python3 -m venv .venv
. .venv/bin/activate
pip install -r backend/requirements.txt

# La app no lee el .env sola: exportar las variables (o usar un EnvironmentFile de systemd)
export POSTGRES_HOST=127.0.0.1 POSTGRES_USER=tracker_admin POSTGRES_PASSWORD='CLAVE' POSTGRES_DB=tracker360_db
export SECRET_KEY=$(openssl rand -hex 32)
export SETUP_TOKEN=$(openssl rand -hex 24); echo "SETUP_TOKEN: $SETUP_TOKEN"

uvicorn backend.main:app --host 127.0.0.1 --port 8001 --no-proxy-headers
```

Guardar `SECRET_KEY` en un lugar fijo: si cambia en cada arranque, todas las sesiones se
cierran.

---

## Acceso desde la red (HTTPS)

Desde otras PCs, colectoras o celulares hace falta HTTPS: la cookie de sesión es `Secure`
(por `http://` el navegador la descarta fuera de `localhost`), la cámara del celular solo se
habilita en sitios seguros, y la app rechaza conexiones sin HTTPS que no vengan de la red local.
Con [Caddy](https://caddyserver.com/) en el mismo servidor:

```
# /etc/caddy/Caddyfile
wms.miempresa.com {
    reverse_proxy 127.0.0.1:8001
}
```

Y en el `.env`: `API_BIND=127.0.0.1` (la API solo escucha en el propio servidor) y
`ALLOWED_ORIGINS=https://wms.miempresa.com`. En una red interna sin dominio público se puede
usar `tls internal` (CA local, que hay que instalar en cada equipo).

---

## Configuración

### Variables de entorno (`.env`)

**Obligatorias**

| Variable | Default | Para qué sirve |
|---|---|---|
| `POSTGRES_USER` / `POSTGRES_DB` | `tracker_admin` / `tracker360_db` | Usuario y nombre de la base. |
| `POSTGRES_PASSWORD` | — | Clave de la base (`openssl rand -hex 16`). Postgres la toma solo al crear el volumen. |
| `SECRET_KEY` | aleatoria por arranque | Firma de las sesiones (`openssl rand -hex 32`). Sin ella, las sesiones se cierran en cada reinicio. |
| `SETUP_TOKEN` | vacío | Crea el primer admin, una sola vez (`openssl rand -hex 24`). Vacío = no se puede crear. |

**Red y dominio**

| Variable | Default | Para qué sirve |
|---|---|---|
| `ALLOWED_ORIGINS` | vacío | Orígenes permitidos por CORS, separados por coma. Vacío = solo el mismo origen. |
| `TRUSTED_PROXIES` | `127.0.0.1/32,::1/128,172.16.0.0/12` | Proxies de confianza: solo de ellos se aceptan `X-Forwarded-For` / `X-Forwarded-Proto`. |
| `API_BIND` / `API_PORT` | `0.0.0.0` / `8001` | Dónde publica Docker la API. Con proxy: `API_BIND=127.0.0.1`. |
| `DB_PORT` | `5433` | Puerto de Postgres, solo en `127.0.0.1`, para mantenimiento. |

**Recursos**

| Variable | Default | Para qué sirve |
|---|---|---|
| `API_MEM_LIMIT` / `DB_MEM_LIMIT` | `384m` / `512m` | Límite de memoria de cada contenedor. |
| `DB_POOL_MAX` | `20` | Conexiones máximas a la base. Bajar en equipos con poca RAM. |
| `POSTGRES_HOST` / `POSTGRES_PORT` | `db` / `5432` | Postgres visto desde la API (en instalación local: `127.0.0.1`). |
| `TZ` | `America/Argentina/Buenos_Aires` | Zona horaria de los contenedores. |

**Integraciones**

| Variable | Default | Para qué sirve |
|---|---|---|
| `UPDATER_GITHUB_REPO` | `Jonnyonz/Tracker360` | Repo donde el panel busca versiones nuevas y desde donde se descarga el agente. |
| `AGENT_DOWNLOAD_URL` | Releases de ese repo | URL directa del `.exe` del agente, si se publica en otro lado. |
| `WEBHOOK_ALLOW_PRIVATE` | `false` | `true` solo si un webhook tiene que postear a un servicio de la red interna. |

### Configuración desde el panel

En **Configuración** del panel se editan los parámetros guardados en `system_settings`, entre
ellos: nombre de la aplicación y CUIT de la empresa, intentos de login y minutos de bloqueo,
plantillas ZPL y tamaños de etiqueta (artículo, ubicación y pedido), cola de impresión por
defecto, prefijos y numeración de documentos, opciones de operación (picking por olas,
estación de packing, sugerencias de ubicación y reposición, números de serie) y Google SSO
(Client ID y dominio permitido). Cada sector tiene además su propia cola de impresión.

---

## Agente de impresión

Programa para la PC de Windows que tiene la impresora de etiquetas (Zebra u otra compatible
con ZPL):

1. Descargarlo desde el panel (o desde `/api/download-agent`, que redirige a la última
   versión en GitHub Releases).
2. Al abrirlo pide la URL del servidor, la cola (sector) y la impresora (las detecta solo).
3. Se autoriza con el login del navegador (flujo PKCE: se abre la pantalla de consentimiento
   y un admin o supervisor aprueba) o con una clave API.
4. Queda consultando los trabajos de su cola cada 3 segundos y los imprime en RAW.

Los agentes autorizados se ven y se revocan desde el panel. El código fuente está en `agent/`
(`compilar.bat` genera el `.exe` con PyInstaller). Si en Windows se define
`TRACKER360_SERVER_URL`, el agente la propone como URL del servidor.

---

## Operación

```bash
docker compose ps
docker compose logs -f api

# Actualizar (el esquema se actualiza solo al arrancar)
git pull
docker compose up -d --build

# Backup y restauración
docker compose exec -T db pg_dump -U tracker_admin tracker360_db > backup_$(date +%F).sql
docker compose exec -T db psql -U tracker_admin -d tracker360_db < backup_AAAA-MM-DD.sql

docker compose down        # detener (los datos quedan en el volumen tracker360_pgdata)
```

El panel avisa si hay una versión nueva publicada (Configuración → Actualizaciones), pero la
actualización se hace a mano en el servidor.

---

## Tests

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r backend/requirements.txt
pip install pytest "httpx==0.27.2"     # httpx 0.28 no es compatible con esta versión de Starlette
python -m pytest backend/test_architecture.py
```

Verifican que todos los routers queden registrados y que el frontend se sirva. No necesitan
base de datos.

---

## Estado y limitaciones conocidas

Para no prometer lo que no está:

- **No hay conectores de e-commerce** (MercadoLibre, WooCommerce, etc.): la integración son
  webhooks salientes. Los canales de webhook y las claves API de entrada todavía no se pueden
  administrar desde el panel.
- Algunos formularios del panel (órdenes de compra, remitos, facturas de compra, devoluciones
  de cliente, alta de canales) todavía no tienen su acción implementada. Hoy la recepción
  trabaja sobre remitos ya cargados en la base.
- Los pickeos por ola hechos antes de esta versión quedaron sin número de pedido: no aparecen
  en los participantes y no se pueden revertir automáticamente al cancelar (la cancelación lo
  avisa y hay que devolverlos con un ajuste de stock).
- Hay opciones de configuración que se guardan pero todavía no cambian el comportamiento
  (por ejemplo, el tiempo de sesión: hoy es fijo en 4 horas).
- No hay token CSRF: la protección es `SameSite=Strict` en la cookie de sesión.

---

## Cambios

Lo que cambia en cada actualización está en `CHANGELOG.md`.

---

## Contribuir y licencia

Las contribuciones son bienvenidas: ver `CONTRIBUTING.md`. Cada commit tiene que llevar `Signed-off-by`
(`git commit -s`, Developer Certificate of Origin), ser un único cambio probado y pasar los
tests. Sin emojis en la interfaz: íconos solo en SVG.

Licencia: **AGPLv3** (GNU Affero General Public License v3). Ver `LICENSE`. Si ofrecés una versión modificada como servicio en red, tenés que publicar su código fuente.
