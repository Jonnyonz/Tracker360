// === COMPRAS Y RECEPCION: un modulo por tipo, desde el acordeon del menu (como Reportes) ===
// Cada historial no lista nada hasta buscar (filtros propios de cada tipo); el alta se abre con "Nuevo".
// OC, remitos, traspasos y devoluciones se ven en bandeja lista + detalle (BANDEJAS, mas abajo); facturas, en tabla.

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

// === BANDEJAS: lista + detalle (design_handoff_tracker360, fase 3) ===
// Ordenes de compra, remitos, traspasos y devoluciones comparten el armado: BANDEJAS dice de donde sale la lista,
// como se pinta cada fila, las pestañas por estado y como se arma el detalle. Mismos endpoints y mismos filtros
// que las tablas de antes: no se lista nada hasta buscar o tocar un atajo. Las devoluciones no tienen endpoint
// de detalle: se ven los datos de cabecera y las observaciones, sin articulos.
const fechaCortaCompras = v => v ? new Date(v).toLocaleDateString('es-AR', { day: '2-digit', month: '2-digit' }) : '';
const fechaHoraCompras = v => v ? new Date(v).toLocaleString('es-AR', { dateStyle: 'short', timeStyle: 'short', hour12: false }) : '—';
const lugarCompras = (sucursal, sector) => [sucursal, sector].filter(Boolean).join(' · ');

const BANDEJAS = {
    po: {
        url: '/api/admin/purchase-orders', vacio: 'No hay órdenes de compra que coincidan con la búsqueda.',
        clave: o => o.order_number, num: o => o.order_number, titulo: o => o.supplier_name,
        sub: o => o.branch_name ? `Entrega en ${o.branch_name}` : '',
        estado: o => [PO_STATUS_LABEL[o.status] || o.status, { PENDING: 'badge-info', IN_PROGRESS: 'badge-warning' }[o.status] || 'badge-neutral'],
        tabs: [['Pendientes', ['PENDING']], ['Parciales', ['IN_PROGRESS']], ['Completadas', ['COMPLETED']]],
        detalle: detalleOC,
    },
    remito: {
        url: '/api/admin/purchase-remitos', vacio: 'No hay remitos que coincidan con la búsqueda.',
        clave: o => o.id, num: o => o.remito_number, titulo: o => o.supplier_name,   // el numero se puede repetir entre proveedores
        sub: o => [o.purchase_order_number || 'Sueltos', lugarCompras(o.branch_name, o.sector_name)].filter(Boolean).join(' · '),
        estado: o => [REM_STATUS_LABEL[o.status] || o.status, { COMPLETED: 'badge-success', COMPLETED_DIFF: 'badge-danger' }[o.status] || 'badge-warning'],
        tabs: [['Por controlar', ['PENDING', 'PENDING_CONTROL', 'IN_PROGRESS']], ['Ingresados', ['COMPLETED']], ['Con diferencias', ['COMPLETED_DIFF']]],
        detalle: detalleRemito,
    },
    transfer: {
        url: '/api/admin/transfer-orders', vacio: 'No hay traspasos que coincidan con la búsqueda.',
        clave: o => o.transfer_number, num: o => o.transfer_number,
        titulo: o => `${lugarCompras(o.origin_branch, o.origin_sector)} → ${lugarCompras(o.destination_branch, o.destination_sector)}`,
        sub: () => '',
        estado: o => [TR_STATUS_LABEL[o.status] || o.status, { COMPLETED: 'badge-success', IN_PROGRESS: 'badge-info' }[o.status] || 'badge-neutral'],
        tabs: [['Pendientes', ['PENDING', 'PENDING_CONTROL']], ['En curso', ['IN_PROGRESS']], ['Completados', ['COMPLETED']]],
        detalle: detalleTraspaso,
    },
    return: {
        url: '/api/admin/returns', vacio: 'No hay devoluciones que coincidan con la búsqueda.',
        clave: o => o.return_number, num: o => o.return_number, titulo: o => `${o.document_number} · ${o.customer_name}`,
        sub: o => lugarCompras(o.branch_name, o.sector_name),
        estado: () => ['Registrada', 'badge-success'],
        tabs: [],
        detalle: async o => ({
            datos: [['Pedido', o.document_number], ['Cliente', o.customer_name], ['Ingresó en', lugarCompras(o.branch_name, o.sector_name)],
                    ['Registró', `${o.created_by || '—'} · ${fechaHoraCompras(o.created_at)}`]],
            obs: ['DEVOLUCION', o.return_number],
        }),
    },
};
const bandejas = {};   // tipo -> { filas, tab (0 = Todos), sel (clave del elegido) }
function estadoBandeja(tipo) { return bandejas[tipo] || (bandejas[tipo] = { filas: [], tab: 0, sel: null }); }

async function cargarBandeja(tipo) {
    const st = estadoBandeja(tipo), lista = document.getElementById(`band-lista-${tipo}`);
    if (!lista) return;
    resumenCompras(tipo, null);
    if (!busquedasCompras[tipo]) { st.filas = []; pintarBandeja(tipo, 'Buscá con los filtros o tocá un atajo.'); return; }
    lista.innerHTML = '<div class="vacio">Buscando…</div>';
    try {
        st.filas = await fetchAPI(`${BANDEJAS[tipo].url}?${busquedasCompras[tipo].toString()}`) || [];
        resumenCompras(tipo, st.filas.length);
        pintarBandeja(tipo);
    } catch (e) {
        lista.innerHTML = `<div class="vacio" style="color:var(--dg);">Error: ${escapeHTML(e.message)}</div>`;
    }
}
function loadPOData() { return cargarBandeja('po'); }
function loadRemitoData() { return cargarBandeja('remito'); }
function loadTransferData() { return cargarBandeja('transfer'); }
function loadReturnData() { return cargarBandeja('return'); }

function pintarBandeja(tipo, vacioTexto) {
    const B = BANDEJAS[tipo], st = estadoBandeja(tipo);
    const tabs = [['Todos', null], ...B.tabs], enTab = t => o => !t[1] || t[1].includes(o.status);
    if (!tabs[st.tab]) st.tab = 0;
    document.getElementById(`band-tabs-${tipo}`).innerHTML = tabs.map((t, i) =>
        `<button type="button" class="${st.tab === i ? 'on' : ''}" data-on-click="bandejaTab(${jsArg(tipo)}, ${i})">${t[0]}<span>${st.filas.filter(enTab(t)).length}</span></button>`).join('');
    const filas = st.filas.filter(enTab(tabs[st.tab]));
    if (!filas.some(o => B.clave(o) === st.sel)) st.sel = filas.length ? B.clave(filas[0]) : null;
    document.getElementById(`band-lista-${tipo}`).innerHTML = filas.length ? filas.map(o => {
        const [el, ecls] = B.estado(o), k = B.clave(o);
        return `<button type="button" class="band-row ${k === st.sel ? 'on' : ''}" data-on-click="bandejaElegir(${jsArg(tipo)}, ${jsArg(k)})">
            <span class="bar"></span>
            <span class="l1"><b class="mono">${escapeHTML(B.num(o))}</b><small class="mono muted">${fechaCortaCompras(o.created_at)}</small></span>
            <span class="lt">${escapeHTML(B.titulo(o))}</span>
            <span class="l4"><span>${escapeHTML(B.sub(o))}</span><span class="badge ${ecls}">${escapeHTML(el)}</span></span>
        </button>`;
    }).join('') : `<div class="vacio">${escapeHTML(vacioTexto || (st.filas.length ? 'No hay documentos en esta pestaña.' : B.vacio))}</div>`;
    pintarDetalleCompras(tipo, filas.find(o => B.clave(o) === st.sel));
}
function bandejaTab(tipo, i) { const st = estadoBandeja(tipo); st.tab = i; st.sel = null; pintarBandeja(tipo); }
function bandejaElegir(tipo, clave) { estadoBandeja(tipo).sel = clave; pintarBandeja(tipo); }

// Atajo: busca lo cargado desde hace `dias` dias (0 = hoy), sin otros filtros.
function atajoCompras(tipo, dias) {
    document.getElementById(`filtros-${tipo}`)?.reset();
    const d = new Date(Date.now() - dias * 86400000);
    const desde = document.querySelector(`#filtros-${tipo} [name="date_from"]`);
    if (desde) desde.value = new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
    buscarCompras(null, tipo);
}
function alternarFiltrosCompras(tipo) {
    const c = document.getElementById(`caja-filtros-${tipo}`); c.hidden = !c.hidden;
    document.getElementById(`btn-filtros-${tipo}`)?.classList.toggle('on', !c.hidden);
}

async function pintarDetalleCompras(tipo, o) {
    const box = document.getElementById(`band-det-${tipo}`);
    if (!box) return;
    if (!o) { box.innerHTML = '<div class="vacio" style="margin:auto;">Elegí un documento de la lista.</div>'; return; }
    const B = BANDEJAS[tipo], [el, ecls] = B.estado(o), k = B.clave(o);
    box.innerHTML = `<div class="det-h"><div class="min0"><div class="g"><span class="num mono">${escapeHTML(B.num(o))}</span><span class="badge ${ecls}">${escapeHTML(el)}</span></div><div class="sub">${escapeHTML(B.titulo(o))}</div></div></div>
        <div class="det-b" id="band-cuerpo-${tipo}"><div class="vacio">Cargando…</div></div>`;
    let d;
    try { d = await B.detalle(o); } catch (e) { d = { error: e.message }; }
    const cuerpo = document.getElementById(`band-cuerpo-${tipo}`);
    if (!cuerpo || estadoBandeja(tipo).sel !== k) return;   // el usuario ya eligio otro
    if (d.error) { cuerpo.innerHTML = `<div class="vacio" style="color:var(--dg);">${escapeHTML(d.error)}</div>`; return; }
    const celda = (v, extra) => `<td class="mono" style="text-align:right;${extra || ''}">${escapeHTML(String(v))}</td>`;
    const tabla = !d.lineas ? '' : `<div class="scrollx"><table class="tbl-lineas tbl-compras"><thead><tr><th>SKU</th><th>${d.col2 || 'Descripción'}</th>${d.cols.map(c => `<th style="text-align:right;">${c}</th>`).join('')}</tr></thead><tbody>${
        d.lineas.map(l => `<tr><td class="mono"><b>${escapeHTML(l.sku)}</b></td><td>${escapeHTML(l.desc || '')}${l.sub ? `<br><small class="muted">${escapeHTML(l.sub)}</small>` : ''}</td>${celda(l.a)}${celda(l.b)}${celda(l.c, ` font-weight:600; color:${l.cc || 'var(--tp)'};`)}</tr>`).join('')
        || '<tr><td colspan="5" class="busqueda-vacia">Sin líneas.</td></tr>'}</tbody></table></div>`;
    cuerpo.innerHTML = `<div class="datos">${d.datos.map(([c, v]) => `<div><div class="cap">${c}</div><b>${escapeHTML(v || '—')}</b></div>`).join('')}</div>
        ${d.avisos || ''}${tabla}
        <div><div class="cap" style="margin-bottom:8px;">Observaciones</div><div id="band-obs-${tipo}"></div></div>`;
    renderObservaciones(document.getElementById(`band-obs-${tipo}`), d.obs[0], d.obs[1]);
}

async function detalleOC(o) {
    const d = await fetchAPI(`/api/admin/purchase-orders/${encodeURIComponent(o.order_number)}`), c = d.order;
    return {
        datos: [['Proveedor', c.supplier_name], ['CUIT', c.supplier_tax_id], ['Recepción', [c.branch_name, c.delivery_address].filter(Boolean).join(' · ')],
                ['Emitida', `${fechaHoraCompras(c.created_at)} · ${c.created_by || '—'}`]],
        cols: ['Pedido', 'Recibido', 'Pendiente'],
        lineas: d.lines.map(l => ({ sku: l.sku, desc: l.description, a: l.ordered, b: l.received, c: l.pending,
                                    cc: l.pending <= 0 ? 'var(--okt)' : l.received > 0 ? 'var(--wnt)' : 'var(--tp)' })),
        obs: ['OC', o.order_number],
    };
}

async function detalleRemito(o) {
    const d = await fetchAPI(`/api/admin/purchase-remitos/${encodeURIComponent(o.id)}`), r = d.remito;
    const sinControl = r.status === 'PENDING' || r.status === 'PENDING_CONTROL';
    const cerrado = r.status === 'COMPLETED' || r.status === 'COMPLETED_DIFF';   // en control, la diferencia todavia se mueve: va en gris
    const diferencia = (remitido, controlado) => {
        if (sinControl) return ['—', 'var(--tm)'];
        const n = Math.round((controlado - remitido) * 100) / 100;
        return [n > 0 ? '+' + n : String(n), !cerrado ? 'var(--tm)' : n === 0 ? 'var(--okt)' : n < 0 ? 'var(--dgt)' : 'var(--wnt)'];
    };
    const lineas = d.lines.map(l => {
        const [c, cc] = diferencia(l.sent, l.controlled);
        return { sku: l.sku, desc: l.description, a: l.sent, b: sinControl ? '—' : l.controlled, c, cc,
                 sub: [l.order_number || (l.added_in_control ? 'Agregado en el control' : 'Suelto'), l.location_code, l.lot_number && `Lote ${l.lot_number}`].filter(Boolean).join(' · ') };
    });
    // No esperados: los aprobados ya son una linea del remito; en cuarentena y rechazados se suman aca.
    const excepciones = d.exceptions || [];
    excepciones.filter(x => x.status !== 'APPROVED').forEach(x => lineas.push({
        sku: x.sku, desc: x.description, a: 0, b: x.quantity, c: '+' + x.quantity, cc: x.status === 'PENDING' ? 'var(--wnt)' : 'var(--tm)',
        sub: `No figuraba en el remito · ${EXC_STATUS_LABEL[x.status] || x.status}${x.resolved_by ? ' por ' + x.resolved_by : ''}` }));
    const ocs = [...new Set(d.lines.map(l => l.order_number).filter(Boolean))];
    return {
        datos: [['Proveedor', r.supplier_name], ['CUIT', r.supplier_tax_id], ['Ingreso', lugarCompras(r.branch_name, r.sector_name)],
                ['Órdenes de compra', ocs.join(', ') || 'Sin orden (sueltos)'], ['Registró', `${r.created_by || '—'} · ${fechaHoraCompras(r.created_at)}`]],
        avisos: excepciones.filter(x => x.status === 'PENDING').map(x => `<div class="aviso-dif">
            <span><b class="mono">${escapeHTML(String(x.quantity))} × ${escapeHTML(x.sku)}</b> (${escapeHTML(x.description)}) no figuraba en el remito y está en cuarentena${x.location_code ? ' en ' + escapeHTML(x.location_code) : ''}. Aprobar lo pasa a stock disponible y lo suma al remito; rechazar lo saca del stock para devolverlo al proveedor.</span>
            <button type="button" class="btn-secondary" style="color:var(--dg);" data-on-click="resolverNoEsperado(${jsArg(x.id)}, false, ${jsArg(r.id)}, ${jsArg(r.remito_number)})">Rechazar</button>
            <button type="button" class="btn-submit" style="background:var(--ok); border-color:var(--ok);" data-on-click="resolverNoEsperado(${jsArg(x.id)}, true, ${jsArg(r.id)}, ${jsArg(r.remito_number)})">Aprobar ingreso</button>
        </div>`).join(''),
        cols: ['Remito', 'Controlado', 'Diferencia'], lineas,
        obs: ['REMITO', r.id],
    };
}

async function detalleTraspaso(o) {
    const d = await fetchAPI(`/api/transfers/orders/${encodeURIComponent(o.transfer_number)}`);
    return {
        datos: [['Origen', lugarCompras(o.origin_branch, o.origin_sector)], ['Destino', lugarCompras(o.destination_branch, o.destination_sector)],
                ['Creado', fechaHoraCompras(o.created_at)]],
        col2: 'Ubicaciones', cols: ['Solicitado', 'Movido', 'Pendiente'],
        lineas: d.lines.map(l => {
            const pendiente = Math.max(0, Math.round((l.quantity_sent - l.quantity_received) * 100) / 100);
            return { sku: l.sku, desc: `${l.origin_location || '—'} → ${l.destination_location || '—'}`, a: l.quantity_sent, b: l.quantity_received, c: pendiente,
                     cc: pendiente <= 0 ? 'var(--okt)' : l.quantity_received > 0 ? 'var(--wnt)' : 'var(--tp)' };
        }),
        obs: ['TRASPASO', o.transfer_number],
    };
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

// Abre un documento en su bandeja buscandolo por numero (enlaces de Inicio).
async function mostrarCompraBuscada(tabId, numero) {
    await abrirCompras(tabId);
    const tipo = MODULOS_COMPRAS[tabId].tipo;
    document.getElementById(`filtros-${tipo}`)?.reset();
    const campo = document.querySelector(`#filtros-${tipo} [name="number"]`);
    if (campo) campo.value = numero;
    buscarCompras(null, tipo);
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
