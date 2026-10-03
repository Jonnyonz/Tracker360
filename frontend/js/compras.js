// === COMPRAS Y RECEPCION: un modulo por tipo, desde el acordeon del menu (como Reportes) ===
// Cada historial no lista nada hasta buscar (filtros propios de cada tipo); el alta se abre con "Nuevo".
// Los historiales de OC, remitos y traspasos los pinta operations.js; facturas y devoluciones, este archivo.

const MODULOS_COMPRAS = {
    'tab-po':       { tipo: 'po',       titulo: 'Órdenes de compra',        nuevo: '+ Nueva orden de compra' },
    'tab-remito':   { tipo: 'remito',   titulo: 'Remitos de entrada',       nuevo: '+ Nuevo remito' },
    'tab-invoice':  { tipo: 'invoice',  titulo: 'Facturas de compra',       nuevo: null },   // alta en preparacion
    'tab-transfer': { tipo: 'transfer', titulo: 'Traspasos',                nuevo: '+ Nuevo traspaso' },
    'tab-return':   { tipo: 'return',   titulo: 'Devoluciones de clientes', nuevo: '+ Nueva devolución' },
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
    if (ver && comprasTabActual === 'tab-return') prepararDevolucion();
    if (ver) tarjeta.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function alternarAltaCompras() {
    const tarjeta = document.querySelector(`#${comprasTabActual} .compras-alta`);
    mostrarAltaCompras(tarjeta ? tarjeta.hidden : false);
}

// === DEVOLUCIONES DE CLIENTES: alta ===
// Se busca el pedido (numero, venta del canal, cliente o comprador; solo completos o despachados), se elige
// donde entra la mercadería y cuanto vuelve de cada articulo. El servidor no deja devolver mas de lo
// preparado menos lo ya devuelto; aca solo se limita el campo para avisar antes.
let devolucionPedido = null;      // detalle del pedido elegido (order-details)
let devolucionSectores = [];

const ESTADO_PEDIDO_DEVOLUCION = { COMPLETED: 'Para empacar', DISPATCHED: 'Despachado' };

async function prepararDevolucion() {
    devolucionPedido = null;
    document.getElementById('rma-buscar').value = '';
    document.getElementById('rma-resultados').innerHTML = '';
    document.getElementById('rma-pedido').hidden = true;
    document.getElementById('form-return').hidden = true;
    try {
        const [sucursales, sectores] = await Promise.all([fetchAPI('/api/admin/branches'), fetchAPI('/api/admin/sectors')]);
        devolucionSectores = sectores || [];
        // Solo sucursales con algun sector: la mercaderia entra a un sector.
        const conSectores = (sucursales || []).filter(b => devolucionSectores.some(s => String(s.branch_id) === String(b.id)));
        document.getElementById('rma-branch').innerHTML = conSectores.length
            ? conSectores.map(b => `<option value="${escapeHTML(b.id)}">${escapeHTML(b.name)}</option>`).join('')
            : '<option value="">Primero creá una sucursal con un sector (Depósitos)</option>';
        pintarSectoresDevolucion();
    } catch (e) { /* fetchAPI ya avisa */ }
}

function pintarSectoresDevolucion() {
    const sucursal = document.getElementById('rma-branch').value;
    const opciones = devolucionSectores.filter(s => String(s.branch_id) === String(sucursal))
        .map(s => `<option value="${escapeHTML(s.id)}">${escapeHTML(s.name)}</option>`).join('');
    document.getElementById('rma-sector').innerHTML = opciones || '<option value="">Esta sucursal no tiene sectores</option>';
}

async function buscarPedidoDevolucion() {
    const q = document.getElementById('rma-buscar').value.trim();
    const caja = document.getElementById('rma-resultados');
    if (!q) {
        caja.innerHTML = '<p class="canales-vacio">Escribí un número de pedido o de venta, un cliente o un comprador.</p>';
        return;
    }
    caja.innerHTML = '<p class="canales-vacio">Buscando...</p>';
    try {
        const pedidos = await fetchAPI('/api/admin/returns/orders?q=' + encodeURIComponent(q)) || [];
        caja.innerHTML = pedidos.length === 0
            ? '<p class="canales-vacio">No hay pedidos completos o despachados que coincidan.</p>'
            : pedidos.map(o => `
                <div class="canal-linea">
                    <div class="canal-linea-datos">
                        <div><strong class="font-mono">${escapeHTML(o.document_number)}</strong>
                            ${o.external_ref ? `<span class="canal-linea-codigo">${escapeHTML(o.channel_code || '')} venta ${escapeHTML(o.external_ref)}</span>` : ''}
                            <span class="badge ${o.status === 'DISPATCHED' ? 'badge-success' : 'badge-info'}">${escapeHTML(ESTADO_PEDIDO_DEVOLUCION[o.status] || o.status)}</span></div>
                        <small>${escapeHTML(o.customer_name)} · ${escapeHTML(fechaCompras(o.created_at))}</small>
                    </div>
                    <div class="canal-linea-botones"><button type="button" class="btn-secondary" style="padding:3px 8px; font-size:0.75rem;" data-on-click="elegirPedidoDevolucion(${jsArg(o.id)})">Elegir</button></div>
                </div>`).join('');
    } catch (e) {
        caja.innerHTML = '';
    }
}

async function elegirPedidoDevolucion(id) {
    let d;
    try { d = await fetchAPI(`/api/admin/returns/order-details/${encodeURIComponent(id)}`); } catch (e) { return; }
    devolucionPedido = d;
    document.getElementById('rma-resultados').innerHTML = '';
    // Una fila por SKU: lo preparado (suma de lo pickeado de sus lineas) y lo ya devuelto.
    const porSku = {};
    d.lines.forEach(l => {
        const clave = String(l.sku).toUpperCase();
        porSku[clave] = porSku[clave] || { sku: clave, description: l.description, picked: 0 };
        porSku[clave].picked += Number(l.quantity_picked) || 0;
    });
    const filas = Object.values(porSku).map(x => ({ ...x, devuelto: Number((d.returned || {})[x.sku]) || 0 }));
    const resumen = document.getElementById('rma-pedido');
    resumen.innerHTML = `Pedido <strong class="font-mono">${escapeHTML(d.document.document_number)}</strong>`
        + (d.document.external_ref ? ` (venta ${escapeHTML(d.document.external_ref)})` : '')
        + ` · ${escapeHTML(d.document.customer_name)} · ${escapeHTML(ESTADO_PEDIDO_DEVOLUCION[d.document.status] || d.document.status)}`
        + ` <button type="button" class="btn-secondary" style="padding:2px 8px; font-size:0.75rem; margin-left:8px;" data-on-click="prepararDevolucion()">Cambiar pedido</button>`;
    resumen.hidden = false;
    const quedan = filas.some(f => f.picked - f.devuelto > 0);
    document.getElementById('rma-items-body').innerHTML = filas.map(f => {
        const maximo = Math.max(0, f.picked - f.devuelto);
        return `<tr data-sku="${escapeHTML(f.sku)}">
            <td class="font-mono" style="color:var(--accent); font-weight:bold;">${escapeHTML(f.sku)}</td>
            <td>${escapeHTML(f.description)}</td>
            <td style="text-align:right;">${f.picked}</td>
            <td style="text-align:right;">${f.devuelto}</td>
            <td><input type="number" class="rma-cant" min="0" max="${maximo}" step="any" value="0" style="width:90px;" ${maximo === 0 ? 'disabled' : ''}></td>
            <td><select class="rma-cond"><option value="OPERATIVO">Operativo (vuelve a la venta)</option><option value="CUARENTENA">Cuarentena (a revisar)</option></select></td>
            <td><input type="text" class="rma-ubic" placeholder="Código" style="width:120px;" autocomplete="off"></td>
        </tr>`;
    }).join('');
    document.getElementById('form-return').hidden = false;
    document.getElementById('rma-guardar').disabled = !quedan;
    if (!quedan) showToast('Este pedido ya se devolvió entero.', 'warning');
}

async function saveCustomerReturn(event) {
    event.preventDefault();
    if (!devolucionPedido) { showToast('Elegí el pedido que se devuelve.', 'danger'); return; }
    const lineas = [...document.querySelectorAll('#rma-items-body tr[data-sku]')].map(tr => ({
        sku: tr.dataset.sku,
        quantity: Number(tr.querySelector('.rma-cant').value) || 0,
        condition: tr.querySelector('.rma-cond').value,
        location_code: tr.querySelector('.rma-ubic').value.trim() || null,
    })).filter(l => l.quantity > 0);
    if (lineas.length === 0) { showToast('Cargá cuánto vuelve de al menos un artículo.', 'danger'); return; }
    const boton = document.getElementById('rma-guardar');
    boton.disabled = true;
    try {
        const clave = (window.crypto && crypto.randomUUID) ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
        const r = await fetchAPI('/api/admin/returns', {
            method: 'POST', headers: { 'X-Idempotency-Key': clave },
            body: { document_id: String(devolucionPedido.document.id), branch_id: document.getElementById('rma-branch').value,
                    sector_id: document.getElementById('rma-sector').value, lines: lineas },
        });
        showToast(r.message || 'Devolución registrada.');
        mostrarAltaCompras(false);
        // Muestra la devolucion recien cargada en el historial.
        document.getElementById('filtros-return')?.reset();
        const filtro = document.getElementById('f-return-numero');
        if (filtro && r.return_number) filtro.value = r.return_number;
        buscarCompras(null, 'return');
    } catch (e) {
        /* fetchAPI ya mostro el motivo */
    } finally {
        boton.disabled = false;
    }
}
