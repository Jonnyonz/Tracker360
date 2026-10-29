// Hoja A4 del pedido (GET /api/admin/sales-orders/<numero>/hoja): abre el dialogo de impresion al cargar y con el boton.
'use strict';
function imprimirPagina() { window.print(); }
window.imprimirPagina = imprimirPagina;
window.addEventListener('load', () => { setTimeout(() => window.print(), 150); });
