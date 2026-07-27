# 📦 Tracker360 WMS — Sistema de Gestión de Depósitos Multicanal

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![Python](https://img.shields.io/badge/Backend-FastAPI%20%7C%20Python-009688.svg)](https://fastapi.tiangolo.com/)
[![PostgreSQL](https://img.shields.io/badge/Database-PostgreSQL-336791.svg)](https://www.postgresql.org/)
[![Docker](https://img.shields.io/badge/Deployment-Docker%20Compose-2496ED.svg)](https://www.docker.com/)

**Tracker360** es un sistema WMS (*Warehouse Management System*) liviano, plástico y de alto rendimiento diseñado para la gestión integral de inventarios, recepción de compras, armado de pedidos (*Picking* y *Packing*) y despacho multicanal.

Está construido bajo una **arquitectura desacoplada Máquina a Máquina (Service-to-Service)**, permitiendo integrarse fácilmente con e-commerce (MercadoLibre, WooCommerce, Tienda Nube), plataformas ERP y empresas de fletes y logística.

---

## ⚡ Instalación Rápida en 1 Comando

Para desplegar Tracker360 en cualquier servidor Linux con Docker en menos de 1 minuto, ejecuta el siguiente comando en tu terminal:

```bash
curl -fsSL [https://raw.githubusercontent.com/Jonnyonz/Tracker360/main/install.sh](https://raw.githubusercontent.com/Jonnyonz/Tracker360/main/install.sh) | bash
