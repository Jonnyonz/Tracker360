// === INICIO: tablero en vivo (design_handoff_tracker360, fase 2) ===
// Todo sale de endpoints que ya existen: /api/admin/dashboard (KPIs, traspasos, eventos, ranking),
// /api/admin/documents (ultimos pedidos: pendientes, para empacar, urgentes), /api/admin/purchase-remitos
// con diferencias y /api/inventory/sessions en revision. No hay endpoints nuevos.
'use strict';

const ACCIONES_CON_ERROR = new Set(['LOGIN_FAILED', 'UNAUTHORIZED_ACCESS', 'SECURITY_ALERT', 'API_INTRUSION', 'GOOGLE_LOGIN_BLOCKED',
    'GOOGLE_LOGIN_REJECTED', 'CHANNEL_AUTH_FAILED', 'SCANNER_AUTH_FAILED']);

// Canal de un pedido para el badge: FLEX (envio self_service), FULL, JZBILL, ML u otro canal, MANUAL.
function canalPedido(o) {
    const envio = String(o.shipping_type || '').toUpperCase();
    const canal = String(o.channel_code || '').toUpperCase();
    if (o.status === 'FULL' || envio === 'FULFILLMENT') return { l: 'FULL', cls: 'badge-success' };
    if (envio === 'SELF_SERVICE') return { l: 'FLEX', cls: 'badge-danger' };
    if (!canal) return { l: envio === 'MOSTRADOR' ? 'MOSTRADOR' : 'MANUAL', cls: 'badge-neutral' };
    if (canal.startsWith('JZBILL')) return { l: 'JZBILL', cls: 'badge-info' };
    if (canal.startsWith('ML') || canal.includes('MERCADO')) return { l: 'ML', cls: 'badge-warning' };
    return { l: canal.slice(0, 10), cls: 'badge-warning' };
}
function estadoPedidoBadge(o) {
    const esperando = o.status === 'COMPLETED' && o.packed;
    const cls = { PENDING: 'badge-warning', IN_PROGRESS: 'badge-info', COMPLETED: 'badge-success', DISPATCHED: 'badge-neutral', CANCELLED: 'badge-danger', FULL: 'badge-success' }[o.status] || 'badge-neutral';
    return { l: esperando ? 'Esperando carga' : (ESTADO_PEDIDO_LABEL[o.status] || o.status), cls: esperando ? 'badge-info' : cls };
}
function haceTexto(iso) {
    if (!iso) return '';
    const t = new Date(iso); if (isNaN(t)) return '';
    const m = Math.round((Date.now() - t) / 60000);
    if (m < 1) return 'ahora'; if (m < 60) return m + ' min'; if (m < 60 * 24) return Math.round(m / 60) + ' h'; if (m < 60 * 48) return 'ayer'; return Math.round(m / 60 / 24) + ' d';
}
function ordenPedidos(a, b) {
    // Los FLEX (urgentes) primero; despues los mas viejos.
    const fa = (a.urgent || String(a.shipping_type || '').toUpperCase() === 'SELF_SERVICE') ? 0 : 1;
    const fb = (b.urgent || String(b.shipping_type || '').toUpperCase() === 'SELF_SERVICE') ? 0 : 1;
    return fa - fb || String(a.created_at || '').localeCompare(String(b.created_at || ''));
}

let inicioPedidosCache = [];

async function renderInicio() {
    if (!document.getElementById('ini-kpis')) return;
    const esAdmin = window.ROL_ACTUAL !== 'SUPERVISOR';
    const [dash, pedidos, remitosDif, conteos] = await Promise.all([
        fetchAPI('/api/admin/dashboard').catch(() => null),
        fetchAPI('/api/admin/documents?limit=500').catch(() => []),
        esAdmin ? fetchAPI('/api/admin/purchase-remitos?status=COMPLETED_DIFF&limit=10').catch(() => []) : Promise.resolve([]),
        fetchAPI('/api/inventory/sessions?status=REVIEW').catch(() => []),
    ]);
    inicioPedidosCache = pedidos || [];
    const hoy = fechaLocalHoy();
    const pend = inicioPedidosCache.filter(o => o.status === 'PENDING' || o.status === 'IN_PROGRESS');
    const empacar = inicioPedidosCache.filter(o => o.status === 'COMPLETED' && !o.packed);
    const urgentes = pend.filter(o => o.urgent);
    const deHoy = inicioPedidosCache.filter(o => String(o.created_at || '').slice(0, 10) === hoy);
    const kpis = (dash && dash.kpis) || {};
    const traspasos = (dash && dash.active_transfers) || [];
    const cnt = document.getElementById('nav-cnt-pedidos');
    if (cnt) cnt.textContent = pend.length ? String(pend.length) : '';

    // KPIs
    document.getElementById('ini-kpis').innerHTML = [
        { l: 'Pedidos pendientes', v: pend.length, of: '', s: urgentes.length ? `${urgentes.length} urgente${urgentes.length === 1 ? '' : 's'} · ${empacar.length} para empacar` : `${empacar.length} para empacar · ${deHoy.length} cargados hoy`, c: pend.length ? 'var(--ac)' : 'var(--okt)', go: "irAPedidos('pendientes')" },
        { l: 'Unidades pickeadas hoy', v: Number(kpis.units_today || 0).toLocaleString('es-AR'), of: 'un.', s: 'Lecturas de picking del día', c: 'var(--tp)', go: "switchView('section-rep-orders')" },
        { l: 'Ciclo carga → despacho', v: Number(kpis.avg_cycle_hours || 0).toLocaleString('es-AR', { minimumFractionDigits: 1, maximumFractionDigits: 1 }), of: 'h', s: 'Promedio de los últimos 30 días', c: 'var(--tp)', go: "switchView('section-rep-orders')" },
        { l: 'Traspasos activos', v: traspasos.length >= 5 ? '5+' : traspasos.length, of: '', s: traspasos.length ? 'Pendientes de control en destino' : 'Sin traspasos en curso', c: 'var(--tp)', go: "abrirCompras('tab-transfer')" },
    ].map(k => `<button type="button" class="kpi" data-on-click=${'"' + k.go + '"'}><span class="l">${k.l}</span><span class="v"><b style="color:${k.c}">${k.v}</b><span>${k.of}</span></span><span class="s">${k.s}</span></button>`).join('');

    // Pedidos pendientes (Flex primero)
    const lista = pend.slice().sort(ordenPedidos).slice(0, 6);
    document.getElementById('ini-pedidos').innerHTML = lista.length ? lista.map(o => {
        const c = canalPedido(o), e = estadoPedidoBadge(o), pct = Number(o.progress_pct || 0);
        return `<button type="button" class="ini-ped" data-on-click="abrirPedidoDesdeInicio(${jsArg(o.document_number)})">
            <span class="min0"><span class="l1"><b class="mono">${escapeHTML(o.document_number)}</b><span class="badge ${c.cls}">${c.l}</span>${o.urgent ? '<span class="badge badge-danger">Urgente</span>' : ''}</span><span class="cli">${escapeHTML(o.company_name || '')}</span></span>
            <span class="prog"><span class="pl"><span class="badge ${e.cls}" style="padding:2px 5px;">${escapeHTML(e.l)}</span><b class="mono">${pct}%</b></span><span class="pbar"><i style="width:${pct}%; background:${pct >= 100 ? 'var(--ok)' : 'var(--ac)'}"></i></span></span>
        </button>`;
    }).join('') : '<div class="vacio">Sin pedidos pendientes.</div>';

    // Productividad 7 dias
    const lb = (dash && dash.leaderboard) || [];
    const max = Math.max(1, ...lb.map(r => Number(r.picked_units || 0)));
    document.getElementById('ini-ranking').innerHTML = lb.length ? lb.map((r, i) => `<div class="rk"><span class="mono muted">${String(i + 1).padStart(2, '0')}</span><span class="u">${escapeHTML(r.username)}</span><span class="pbar" style="height:10px;"><i style="width:${Number(r.picked_units) / max * 100}%; background:${i === 0 ? 'var(--ac)' : 'var(--tm)'}"></i></span><span class="n"><b class="mono">${Number(r.picked_units).toLocaleString('es-AR')}</b><small>${escapeHTML(String(r.picking_lines))} líneas</small></span></div>`).join('')
        : '<div class="vacio">Sin actividad en los últimos 7 días.</div>';

    // Requiere tu atencion
    const at = [];
    if (urgentes.length) at.push({ cls: 'badge-danger', ic: 'alert', t: `${urgentes.length} pedido${urgentes.length === 1 ? '' : 's'} Flex sin preparar`, d: urgentes.slice(0, 3).map(o => o.document_number).join(', '), cta: 'Ver', go: "irAPedidos('urgentes')" });
    if (empacar.length) at.push({ cls: 'badge-success', ic: 'box', t: `${empacar.length} pedido${empacar.length === 1 ? ' listo' : 's listos'} para empacar`, d: 'Picking completo, falta despachar', cta: 'Empacar', go: "irAPedidos('empacar')" });
    (remitosDif || []).slice(0, 3).forEach(r => at.push({ cls: 'badge-warning', ic: 'alert', t: `Remito ${r.remito_number || r.number || ''} con diferencias`, d: `${r.supplier_name || r.supplier || ''}`.trim() || 'Controlado con faltantes o sobrantes', cta: 'Revisar', go: r.remito_number ? `mostrarCompraBuscada('tab-remito', ${jsArg(r.remito_number)})` : "abrirCompras('tab-remito')" }));
    (conteos || []).slice(0, 3).forEach(s => at.push({ cls: 'badge-info', ic: 'count', t: `Conteo de ${s.sector_name || ''} para revisar`, d: `${s.branch_name || ''} · ${s.count_type === 'COLD' ? 'en frío' : 'en caliente'} · ${s.assigned_operator || s.created_by || ''}`, cta: 'Revisar y aplicar', go: "switchView('section-inventory')" }));
    const ICO = {
        alert: '<path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"></path><line x1="12" y1="9" x2="12" y2="13"></line><line x1="12" y1="17" x2="12.01" y2="17"></line>',
        box: '<path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"></path>',
        count: '<path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"></path><rect x="8" y="2" width="8" height="4" rx="1"></rect><polyline points="9 14 11 16 15 12"></polyline>',
    };
    document.getElementById('ini-at-n').textContent = at.length;
    document.getElementById('ini-atencion').innerHTML = at.length ? at.map(a => `<div class="at-row"><span class="ic badge ${a.cls}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">${ICO[a.ic]}</svg></span><div class="tx"><b>${escapeHTML(a.t)}</b><small>${escapeHTML(a.d)}</small></div><button type="button" class="btn-secondary btn-sm" data-on-click=${'"' + a.go + '"'}>${a.cta}</button></div>`).join('')
        : '<div class="vacio">Todo en orden por ahora.</div>';

    // Traspasos activos
    document.getElementById('ini-traspasos').innerHTML = traspasos.length ? traspasos.map(t => `<div class="tr-row"><b class="mono">${escapeHTML(t.transfer_number)}</b><span class="od">${escapeHTML(t.origin_branch)}<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"><line x1="5" y1="12" x2="19" y2="12"></line><polyline points="12 5 19 12 12 19"></polyline></svg>${escapeHTML(t.destination_branch)}</span><button type="button" class="btn-link" data-on-click="mostrarCompraBuscada('tab-transfer', ${jsArg(t.transfer_number)})">Ver</button></div>`).join('')
        : '<div class="vacio">Sin traspasos activos.</div>';

    // Eventos del sistema
    const logs = (dash && dash.latest_logs) || [];
    document.getElementById('ini-eventos').innerHTML = logs.length ? logs.map(l => { const err = ACCIONES_CON_ERROR.has(l.action); return `<div class="ev-row"><span class="mono muted">${escapeHTML(new Date(l.created_at).toLocaleTimeString('es-AR', { hour: '2-digit', minute: '2-digit', hour12: false }))}</span><span class="min0"><b>${escapeHTML(accionES(l.action))}</b><small>${escapeHTML(l.username)}</small></span><span class="badge ${err ? 'badge-danger' : 'badge-success'}">${err ? 'ERROR' : 'OK'}</span></div>`; }).join('')
        : '<div class="vacio">Sin eventos.</div>';
}

function irAPedidos(atajo) {
    switchView('section-orders');
    if (atajo === 'pendientes') { document.getElementById('filtros-pedidos')?.reset(); if (typeof pedBandeja === 'function') pedBandeja('PENDIENTE'); buscarPedidos(); }
    else if (typeof atajoPedidos === 'function') atajoPedidos(atajo);
}
function abrirPedidoDesdeInicio(num) {
    switchView('section-orders');
    if (typeof mostrarPedidoBuscado === 'function') mostrarPedidoBuscado(num);
}

window.renderInicio = renderInicio; window.irAPedidos = irAPedidos; window.abrirPedidoDesdeInicio = abrirPedidoDesdeInicio;
window.canalPedido = canalPedido; window.estadoPedidoBadge = estadoPedidoBadge; window.haceTexto = haceTexto; window.ordenPedidos = ordenPedidos;
