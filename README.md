# Tracker360 WMS — Sistema de Gestión de Depósitos Multicanal

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![Python](https://img.shields.io/badge/Backend-FastAPI%20%7C%20Python-009688.svg)](https://fastapi.tiangolo.com/)
[![PostgreSQL](https://img.shields.io/badge/Database-PostgreSQL-336791.svg)](https://www.postgresql.org/)
[![Docker](https://img.shields.io/badge/Deployment-Docker%20Compose-2496ED.svg)](https://www.docker.com/)

**Tracker360** es un sistema WMS (*Warehouse Management System*) liviano, plástico y de alto rendimiento diseñado para la gestión integral de inventarios, recepción de compras, armado de pedidos (*Picking* y *Packing*) y despacho multicanal.

Está construido bajo una **arquitectura desacoplada Máquina a Máquina (Service-to-Service)**, permitiendo integrarse fácilmente con e-commerce (MercadoLibre, WooCommerce, Tienda Nube), plataformas ERP y empresas de fletes y logística.

---

## Instalación Rápida en 1 Comando

Para desplegar Tracker360 en cualquier servidor Linux con Docker en menos de 1 minuto, ejecuta el siguiente comando en tu terminal:

```bash
curl -fsSL https://raw.githubusercontent.com/Jonnyonz/Tracker360/main/install.sh | bash
```

El instalador genera el `.env` con claves aleatorias y pregunta el dominio del servidor. Copiá `.env.example` como referencia; cada variable está explicada abajo.

Para actualizar un servidor ya instalado:

```bash
git pull && docker compose up -d --build
```

## Configuración (`.env`)

Todo lo específico de cada cliente (URLs, claves, puertos) se toma del `.env` y no está fijo en el código. El archivo `.env.example` trae la lista completa con valores de ejemplo.

### Obligatorias

| Variable | Descripción |
|---|---|
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | Credenciales de PostgreSQL. `install.sh` genera la contraseña al azar. |
| `SECRET_KEY` | Firma de las sesiones. Generar con `openssl rand -hex 32`. Si falta, se usa una clave temporal y las sesiones se cierran en cada reinicio. |
| `SETUP_TOKEN` | Token de un solo uso para crear el primer administrador. Generar con `openssl rand -hex 12`. |

### Dominio y red

| Variable | Por defecto | Descripción |
|---|---|---|
| `ALLOWED_ORIGINS` | (vacío) | Dominios del frontend permitidos por CORS, separados por coma. Sin esta variable, solo se acepta el mismo origen (ningún sitio externo puede llamar a la API). |
| `TRUSTED_PROXIES` | `127.0.0.1/32,::1/128,172.16.0.0/12` | Proxies inversos de confianza (IPs o redes). Solo desde ellos se aceptan `X-Forwarded-For` / `X-Forwarded-Proto` para la IP real y el chequeo de HTTPS. |
| `API_BIND` | `0.0.0.0` | Interfaz donde Docker publica la API. Detrás de un proxy inverso, poner `127.0.0.1`. |
| `API_PORT` | `8001` | Puerto público de la API. |
| `DB_PORT` | `5433` | Puerto local (solo `127.0.0.1`) de PostgreSQL para mantenimiento. |

### Recursos e integraciones

| Variable | Por defecto | Descripción |
|---|---|---|
| `API_MEM_LIMIT` / `DB_MEM_LIMIT` | `384m` / `512m` | Límite de memoria de cada contenedor. Bajarlos en equipos con poca RAM. |
| `DB_POOL_MAX` | `20` | Conexiones máximas del pool a la base. |
| `POSTGRES_HOST` / `POSTGRES_PORT` | `db` / `5432` | Servidor y puerto de PostgreSQL vistos desde la API. |
| `UPDATER_GITHUB_REPO` | `Jonnyonz/Tracker360` | Repositorio (`usuario/repo`) donde el aviso de actualizaciones busca versiones. |
| `WEBHOOK_ALLOW_PRIVATE` | `false` | Por defecto los webhooks no pueden apuntar a direcciones internas (protección anti-SSRF). Poner `true` solo en instalaciones on-premise que necesiten postear a un servicio de la red interna. |
| `TZ` | `America/Argentina/Buenos_Aires` | Zona horaria de los contenedores. |

### Agente de impresión (en la PC del cliente)

| Variable | Descripción |
|---|---|
| `TRACKER360_SERVER_URL` | Opcional. Si se define en Windows, el agente la sugiere como URL del servidor al configurarse. |

## Actualizaciones

El panel (Configuración → Actualizaciones del Sistema) avisa si hay una versión más nueva publicada, pero **no** actualiza solo: se hace en el servidor con `git pull && docker compose up -d --build`. El repositorio a consultar se define con `UPDATER_GITHUB_REPO`.

## Licencia

GNU General Public License v3.0. Ver el archivo `LICENSE`.
