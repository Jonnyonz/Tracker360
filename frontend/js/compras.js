// === COMPRAS Y RECEPCION: un modulo por tipo, desde el acordeon del menu (como Reportes) ===
// Cada historial no lista nada hasta buscar (filtros propios de cada tipo); el alta se abre con "Nuevo".
// Los historiales de OC, remitos y traspasos los pinta operations.js; facturas y devoluciones, este archivo.

const MODULOS_COMPRAS = {
    'tab-po':       { tipo: 'po',       titulo: 'Órdenes de compra',        nuevo: '+ Nueva orden de compra' },
    'tab-remito':   { tipo: 'remito',   titulo: 'Remitos de entrada',       nuevo: '+ Nuevo remito' },
    'tab-invoice':  { tipo: 'invoice',  titulo: 'Facturas de compra',       nuevo: null },   // alta en preparacion
    'tab-transfer': { tipo: 'transfer', titulo: 'Traspasos',                nuevo: '+ Nuevo traspaso' },
    'tab-return':   { tipo: 'return',   titulo: 'Devoluciones de clientes', nuevo: null },   // alta en preparacion
};
const COMPRAS_POR_BUSQUEDA = 200;
const busquedasCompras = {};       // tipo -> parametros de la ultima busqueda
let comprasTabActual = 'tab-po';

function cargarHistorialCompras(tipo) {
    const cargadores = { po: loadPOData, remito: loadRemitoData, invoice: loadInvoiceData, transfer: loadTransferData, return: loadReturnData };
    if (cargadores[tipo]) cargadores[tipo]();
}

function buscarCompras(event, tipo) {
    if (event) event.preventDefault();
    const params = new URLSearchParams({ limit: COMPRAS_POR_BUSQUEDA });
    document.querySelectorAll(`#filtros-${tipo} [name]`).forEach(el => {
        const valor = (el.value || '').trim();
        if (valor) params.set(el.name, valor);
    });
    busquedasCompras[tipo] = params;
    cargarHistorialCompras(tipo);
}

function limpiarCompras(tipo) {
    document.getElementById(`filtros-${tipo}`)?.reset();
    delete busquedasCompras[tipo];
    cargarHistorialCompras(tipo);
}

function resumenCompras(tipo, cantidad) {
    const p = document.getElementById(`resumen-${tipo}`);
    if (!p) return;
    if (cantidad === null) { p.hidden = true; return; }
    p.textContent = cantidad >= COMPRAS_POR_BUSQUEDA
        ? `Se muestran los primeros ${COMPRAS_POR_BUSQUEDA}: afiná la búsqueda.`
        : `${cantidad} resultado${cantidad === 1 ? '' : 's'}.`;
    p.hidden = false;
}

// URL de la ultima busqueda del tipo, o null (con el aviso en la tabla) si todavia no se busco.
function urlBusquedaCompras(tipo, base, tbodyId, columnas) {
    resumenCompras(tipo, null);
    const params = busquedasCompras[tipo];
    if (!params) {
        const tbody = document.getElementById(tbodyId);
        if (tbody) tbody.innerHTML = `<tr><td colspan="${columnas}" class="busqueda-vacia">Completá los filtros y tocá Buscar.</td></tr>`;
        return null;
    }
    return `${base}?${params.toString()}`;
}

function fechaCompras(valor) {
    return valor ? new Date(valor).toLocaleDateString('es-AR') : '-';
}

async function loadInvoiceData() {
    const tbody = document.getElementById('table-invoice-body');
    if (!tbody) return;
    const url = urlBusquedaCompras('invoice', '/api/admin/purchase-invoices', 'table-invoice-body', 4);
    if (!url) return;
    try {
        const rows = await fetchAPI(url) || [];
        resumenCompras('invoice', rows.length);
        tbody.innerHTML = rows.length === 0
            ? '<tr><td colspan="4" class="busqueda-vacia">No hay facturas que coincidan con la búsqueda.</td></tr>'
            : rows.map(f => `<tr>
                <td style="color:var(--accent); font-weight:bold;">${escapeHTML(f.invoice_number)}</td>
                <td>${escapeHTML(f.invoice_type || '-')}</td>
                <td>${escapeHTML(f.supplier_name)}</td>
                <td><small>${escapeHTML(fechaCompras(f.created_at))}</small></td>
            </tr>`).join('');
    } catch (e) {
        tbody.innerHTML = '<tr><td colspan="4" style="text-align:center; color:var(--danger);">Error al cargar el historial.</td></tr>';
    }
}

async function loadReturnData() {
    const tbody = document.getElementById('table-returns-body');
    if (!tbody) return;
    const url = urlBusquedaCompras('return', '/api/admin/returns', 'table-returns-body', 5);
    if (!url) return;
    try {
        const rows = await fetchAPI(url) || [];
        resumenCompras('return', rows.length);
        tbody.innerHTML = rows.length === 0
            ? '<tr><td colspan="5" class="busqueda-vacia">No hay devoluciones que coincidan con la búsqueda.</td></tr>'
            : rows.map(d => `<tr>
                <td style="color:var(--accent); font-weight:bold;">${escapeHTML(d.return_number)}</td>
                <td>${escapeHTML(d.customer_name)}</td>
                <td class="font-mono">${escapeHTML(d.document_number)}</td>
                <td><small>${escapeHTML(fechaCompras(d.created_at))}</small></td>
                <td>${escapeHTML(d.created_by || '-')}</td>
            </tr>`).join('');
    } catch (e) {
        tbody.innerHTML = '<tr><td colspan="5" style="text-align:center; color:var(--danger);">Error al cargar el historial.</td></tr>';
    }
}

// Abre el modulo de un tipo. abrirAlta muestra el formulario de alta (si el tipo lo tiene).
async function abrirCompras(tabId, btn, abrirAlta) {
    const modulo = MODULOS_COMPRAS[tabId] || MODULOS_COMPRAS['tab-po'];
    const sub = (btn && btn.classList && btn.classList.contains('rail-sub-btn'))
        ? btn : document.querySelector(`#acc-compras .rail-sub-btn[data-on-click*="${tabId}"]`);
    switchView('section-purchases', sub);
    document.getElementById('acc-compras')?.classList.add('open');
    const icono = document.getElementById('icon-acc-compras');
    if (icono) icono.textContent = '▲';
    comprasTabActual = tabId;
    switchPurchaseTab(tabId);
    document.getElementById('compras-titulo').textContent = modulo.titulo;
    // El supervisor solo consulta: sin "Nuevo" ni formulario de alta.
    const puedeCrear = Boolean(modulo.nuevo) && !(await esSupervisor());
    const nuevo = document.getElementById('btn-compras-nuevo');
    if (nuevo) {
        nuevo.textContent = modulo.nuevo || '';
        nuevo.style.display = puedeCrear ? '' : 'none';
    }
    mostrarAltaCompras(Boolean(abrirAlta) && puedeCrear);
    if (modulo.tipo === 'invoice' || modulo.tipo === 'return') cargarHistorialCompras(modulo.tipo);
}

function mostrarAltaCompras(ver) {
    const tarjeta = document.querySelector(`#${comprasTabActual} .compras-alta`);
    if (!tarjeta) return;
    tarjeta.hidden = !ver;
    if (ver) tarjeta.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function alternarAltaCompras() {
    const tarjeta = document.querySelector(`#${comprasTabActual} .compras-alta`);
    mostrarAltaCompras(tarjeta ? tarjeta.hidden : false);
}
