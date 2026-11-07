// === PEDIDOS: bandeja lista + detalle (design_handoff_tracker360, fase 2) ===
// Reemplaza el pintado en tabla de operations.js (loadOrders/filterOrders) por la bandeja: mismas busquedas
// (buscarPedidos, atajoPedidos, busquedaPedidos) y mismos endpoints; el detalle usa /api/picking/orders/{n},
// /api/admin/documents/{n}/participants y las observaciones de renderObservaciones. Sin endpoints nuevos.
'use strict';

const PED_TABS = [['PENDIENTE', 'Pendientes', o => o.status === 'PENDING'], ['PICKING', 'En picking', o => o.status === 'IN_PROGRESS'],
    ['COMPLETO', 'Completos', o => o.status === 'COMPLETED'], ['DESPACHADO', 'Despachados', o => o.status === 'DISPATCHED'],
    ['FULL', 'Full', o => o.status === 'FULL'], ['CANCELADO', 'Cancelados', o => o.status === 'CANCELLED']];
let pedTab = 'PENDIENTE', pedSel = null, pedPendienteMostrar = null;

async function loadOrders() {
    const lista = document.getElementById('ped-lista');
    if (!lista) return;
    if (!busquedaPedidos) {
        opsOrdersCache = [];
        resumenPedidos('');
        renderBandejaPedidos('Buscá un pedido con los filtros o tocá un atajo.');
        return;
    }
    lista.innerHTML = '<div class="vacio">Buscando…</div>';
    try {
        const res = await fetchAPI('/api/admin/documents?' + busquedaPedidos.toString());
        opsOrdersCache = res || [];
        resumenPedidos(opsOrdersCache.length >= PEDIDOS_POR_BUSQUEDA
            ? `Se muestran los ${PEDIDOS_POR_BUSQUEDA} más recientes: afiná la búsqueda para ver el resto.`
            : `${opsOrdersCache.length} pedido${opsOrdersCache.length === 1 ? '' : 's'}.`);
        // Al entrar desde Inicio o Ctrl+K con un numero, se abre ese pedido en su bandeja.
        if (pedPendienteMostrar) {
            const o = opsOrdersCache.find(x => x.document_number === pedPendienteMostrar);
            if (o) { pedTab = (PED_TABS.find(t => t[2](o)) || PED_TABS[0])[0]; pedSel = o.document_number; }
            pedPendienteMostrar = null;
        } else if (!opsOrdersCache.some(o => PED_TABS.find(t => t[0] === pedTab)[2](o))) {
            const primera = PED_TABS.find(t => opsOrdersCache.some(t[2]));
            if (primera) pedTab = primera[0];
        }
        renderBandejaPedidos();
    } catch (e) {
        lista.innerHTML = `<div class="vacio" style="color:var(--dg);">Error: ${escapeHTML(e.message)}</div>`;
    }
}
function filterOrders() { renderBandejaPedidos(); }

function renderBandejaPedidos(vacioTexto) {
    const tabs = document.getElementById('ped-tabs'), lista = document.getElementById('ped-lista');
    if (!tabs || !lista) return;
    tabs.innerHTML = PED_TABS.map(([id, l, f]) => `<button type="button" class="${pedTab === id ? 'on' : ''}" data-on-click="pedBandeja(${jsArg(id)})">${l}<span>${opsOrdersCache.filter(f).length}</span></button>`).join('');
    const filtro = PED_TABS.find(t => t[0] === pedTab)[2];
    const filas = opsOrdersCache.filter(filtro).sort(ordenPedidos);
    if (!filas.find(o => o.document_number === pedSel)) pedSel = filas.length ? filas[0].document_number : null;
    lista.innerHTML = filas.length ? filas.map(o => {
        const c = canalPedido(o), pct = Number(o.progress_pct || 0), flex = c.l === 'FLEX' || o.urgent;
        return `<button type="button" class="band-row ${o.document_number === pedSel ? 'on' : ''}" data-on-click="pedElegir(${jsArg(o.document_number)})">
            <span class="bar" style="background:${flex ? 'var(--dg)' : 'transparent'}"></span>
            <span class="l1"><span class="g"><b class="mono">${escapeHTML(o.document_number)}</b><span class="badge ${c.cls}">${c.l}</span>${o.urgent && c.l !== 'FLEX' ? '<span class="badge badge-danger">Urgente</span>' : ''}</span><small class="mono muted">${haceTexto(o.created_at)}</small></span>
            <span class="l2">${escapeHTML(o.company_name || '')}${o.external_ref ? ' · venta <span class="mono">' + escapeHTML(o.external_ref) + '</span>' : ''}</span>
            <span class="l3"><span class="pbar"><i style="width:${pct}%; background:${pct >= 100 ? 'var(--ok)' : 'var(--ac)'}"></i></span><b class="mono">${pct}%</b></span>
        </button>`;
    }).join('') : `<div class="vacio">${escapeHTML(vacioTexto || (opsOrdersCache.length ? 'No hay pedidos en esta bandeja.' : 'No hay pedidos que coincidan con la búsqueda.'))}</div>`;
    renderDetallePedido(filas.find(o => o.document_number === pedSel));
}
function pedBandeja(id) { pedTab = id; pedSel = null; renderBandejaPedidos(); }
function pedElegir(num) { pedSel = num; renderBandejaPedidos(); }
function mostrarPedidoBuscado(num) {
    pedPendienteMostrar = num;
    document.getElementById('filtros-pedidos')?.reset();
    document.getElementById('f-ped-numero').value = num;
    buscarPedidos();
}
function alternarFiltrosPedidos() {
    const c = document.getElementById('ped-filtros'); c.hidden = !c.hidden;
    document.getElementById('btn-ped-filtros')?.classList.toggle('on', !c.hidden);
}

async function renderDetallePedido(o) {
    const box = document.getElementById('ped-det');
    if (!box) return;
    if (!o) { box.innerHTML = '<div class="vacio" style="margin:auto;">Elegí un pedido de la lista.</div>'; return; }
    const c = canalPedido(o), e = estadoPedidoBadge(o), pct = Number(o.progress_pct || 0);
    const sup = window.ROL_ACTUAL === 'SUPERVISOR';
    const envio = ENVIO_PEDIDO_LABEL[String(o.shipping_type || '').toUpperCase()] || o.shipping_type || '';
    const creado = o.created_at ? new Date(o.created_at).toLocaleString('es-AR', { dateStyle: 'short', timeStyle: 'short', hour12: false }) : '—';
    const num = o.document_number;
    // Footer: Cancelar (admin, no despachados/cancelados) · Hoja A4 · Imprimir etiqueta · Empacar y despachar (solo COMPLETO sin empacar).
    const puedeEmpacar = o.status === 'COMPLETED' && !o.packed;
    const esperando = o.status === 'COMPLETED' && o.packed;
    let foot = '';
    if (!sup && o.status !== 'DISPATCHED' && o.status !== 'CANCELLED') foot += `<button type="button" class="btn-secondary" style="color:var(--dg);" data-on-click="abrirCancelacion(${jsArg(num)})">Cancelar pedido</button>`;
    foot += `<span class="sp"></span><button type="button" class="btn-secondary" data-on-click="imprimirHojaPedido(${jsArg(num)})">Hoja A4</button>`;
    if (!sup && (esperando || o.status === 'DISPATCHED')) foot += `<button type="button" class="btn-secondary" data-on-click="reprintOrderLabel(${jsArg(num)})">Imprimir etiqueta</button>`;
    if (!sup && o.status !== 'DISPATCHED' && o.status !== 'CANCELLED' && o.status !== 'FULL') foot += `<button type="button" class="btn-submit" ${puedeEmpacar ? '' : 'disabled title="Se habilita con el picking completo"'} data-on-click="openPackingStation(${jsArg(num)}, ${jsArg(o.company_name)})">${esperando ? 'Empacado · espera la carga' : 'Empacar y despachar'}</button>`;
    box.innerHTML = `<div class="det-h"><div class="min0"><div class="g"><span class="num mono">${escapeHTML(num)}</span><span class="badge ${e.cls}">${escapeHTML(e.l)}</span><span class="badge ${c.cls}">${c.l}</span>${o.urgent ? '<span class="badge badge-danger">Urgente</span>' : ''}</div><div class="sub">${escapeHTML(o.company_name || '')} · creado ${escapeHTML(creado)}${o.channel_code ? ' · ' + escapeHTML(o.channel_code) : ''}${o.external_ref ? ' · venta <span class="mono">' + escapeHTML(o.external_ref) + '</span>' : ''}${envio ? ' · ' + escapeHTML(envio) : ''}</div></div>
        <div class="avance"><div class="cap">Avance de picking</div><div class="mono" id="ped-det-pct">${pct}%</div><div class="pbar" style="height:6px; margin-top:8px;"><i style="width:${pct}%; background:${pct >= 100 ? 'var(--ok)' : 'var(--ac)'}"></i></div></div></div>
        <div class="det-b"><div class="scrollx"><table class="tbl-lineas"><thead><tr><th>SKU</th><th>Descripción</th><th>Ubicación</th><th style="text-align:right;">Pedido</th><th style="text-align:right;">Pickeado</th><th>Estado</th></tr></thead><tbody id="ped-det-lineas"><tr><td colspan="6" class="busqueda-vacia">Cargando líneas…</td></tr></tbody></table></div>
        <div class="det-cols"><div><div class="cap" style="margin-bottom:8px;">Participantes</div><div id="ped-det-part"><div class="muted" style="font-size:13px;">Cargando…</div></div></div><div><div class="cap" style="margin-bottom:8px;">Observaciones</div><div id="ped-det-obs"></div></div></div></div>
        <div class="det-f">${foot}</div>`;
    cargarDetallePedido(num);
}

async function cargarDetallePedido(num) {
    const [det, part] = await Promise.all([
        fetchAPI(`/api/picking/orders/${encodeURIComponent(num)}`).catch(e => ({ error: e.message })),
        fetchAPI(`/api/admin/documents/${encodeURIComponent(num)}/participants`).catch(e => ({ error: e.message })),
    ]);
    if (pedSel !== num) return;   // el usuario ya eligio otro
    const tb = document.getElementById('ped-det-lineas');
    if (tb) {
        if (det.error) tb.innerHTML = `<tr><td colspan="6" class="busqueda-vacia" style="color:var(--dg);">${escapeHTML(det.error)}</td></tr>`;
        else tb.innerHTML = (det.lines || []).map(l => {
            const ped = Number(l.quantity_requested || 0), pick = Number(l.quantity_picked || 0);
            const est = pick >= ped && ped > 0 ? ['Completa', 'badge-success'] : pick > 0 ? ['Parcial', 'badge-warning'] : ['Pendiente', 'badge-neutral'];
            const ubi = String(l.suggested_locations || '').split(' | ')[0].replace(/ \(.*\)$/, '');
            return `<tr><td class="mono"><b>${escapeHTML(l.sku)}</b></td><td>${escapeHTML(l.description || '')}</td><td class="mono sec">${escapeHTML(ubi)}</td><td class="mono" style="text-align:right;">${ped}</td><td class="mono" style="text-align:right; color:${pick >= ped ? 'var(--okt)' : 'var(--tp)'}">${pick}</td><td><span class="badge ${est[1]}">${est[0]}</span></td></tr>`;
        }).join('') || '<tr><td colspan="6" class="busqueda-vacia">Sin líneas.</td></tr>';
    }
    const pb = document.getElementById('ped-det-part');
    if (pb) {
        if (part.error) pb.innerHTML = `<div class="muted" style="font-size:13px;">${escapeHTML(part.error)}</div>`;
        else {
            const fecha = v => v ? new Date(v).toLocaleString('es-AR', { dateStyle: 'short', timeStyle: 'short', hour12: false }) : '';
            const ev = part.eventos || [];
            const creo = ev.find(x => x.action === 'ORDER_CREATED'), desp = ev.find(x => x.action === 'PACKING_DISPATCH' || x.action === 'SCANNER_DISPATCH'), emp = ev.find(x => x.action === 'PACKING_DONE');
            const filas = [];
            filas.push(['Creó', creo ? `${escapeHTML(creo.username)} · <span class="mono">${escapeHTML(fecha(creo.created_at))}</span>` : (part.created_at ? `<span class="mono">${escapeHTML(fecha(part.created_at))}</span>` : '—')]);
            (part.picking || []).forEach(p => filas.push(['Pickeó', `${escapeHTML(p.username)} · ${escapeHTML(String(p.unidades))} un. en ${escapeHTML(String(p.lecturas))} lecturas<br><small class="mono muted">${escapeHTML(fecha(p.desde))} → ${escapeHTML(fecha(p.hasta))}</small>`]));
            if (!(part.picking || []).length) filas.push(['Pickeó', '<span class="muted">Todavía nadie</span>']);
            if (emp) filas.push(['Empacó', `${escapeHTML(emp.username)} · <span class="mono">${escapeHTML(fecha(emp.created_at))}</span>`]);
            filas.push(['Despachó', desp ? `${escapeHTML(desp.username)} · <span class="mono">${escapeHTML(fecha(desp.created_at))}</span>` : '<span class="muted">Pendiente</span>']);
            pb.innerHTML = filas.map(([k, v]) => `<div class="kv"><span>${k}</span><b>${v}</b></div>`).join('')
                + (ev.length > 3 ? `<button type="button" class="btn-link" style="margin-top:8px;" data-on-click="verParticipantes(${jsArg(num)})">Ver todos los eventos (${ev.length})</button>` : '');
        }
    }
    const ob = document.getElementById('ped-det-obs');
    if (ob && typeof renderObservaciones === 'function') renderObservaciones(ob, 'PEDIDO', num);
}

window.loadOrders = loadOrders; window.filterOrders = filterOrders; window.renderBandejaPedidos = renderBandejaPedidos;
window.pedBandeja = pedBandeja; window.pedElegir = pedElegir; window.mostrarPedidoBuscado = mostrarPedidoBuscado; window.alternarFiltrosPedidos = alternarFiltrosPedidos;
