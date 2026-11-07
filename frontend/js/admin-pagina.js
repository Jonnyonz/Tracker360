// Script de admin.html (antes inline en la pagina; la CSP ya no permite scripts inline).
function toggleSidebar() {
    const sidebar = document.getElementById('sidebar');
    const overlay = document.getElementById('sidebar-overlay');
    if(sidebar && overlay) {
        sidebar.classList.toggle('open');
        overlay.classList.toggle('open');
    }
}

// Acordeon del menu lateral (acc-<nombre>, icon-acc-<nombre>, btn-acc-<nombre>).
// Abrir un desplegable del menu cierra los demas: el menu entra en la pantalla sin barra de desplazamiento.
function cerrarOtrosAcordeones(nombre) {
    document.querySelectorAll('.nav-rail .rail-accordion.open').forEach(acc => {
        const otro = acc.id.replace(/^acc-/, '');
        if (otro === nombre) return;
        acc.classList.remove('open');
        const icon = document.getElementById(`icon-acc-${otro}`);
        if (icon) icon.textContent = '▼';
    });
}

function alternarAcordeon(nombre) {
    const acc = document.getElementById(`acc-${nombre}`);
    if (!acc) return;
    const abrir = !acc.classList.contains('open');
    if (abrir) cerrarOtrosAcordeones(nombre);
    acc.classList.toggle('open', abrir);
    const icon = document.getElementById(`icon-acc-${nombre}`);
    if (icon) icon.textContent = abrir ? '▲' : '▼';
    document.getElementById(`btn-acc-${nombre}`)?.classList.toggle('active', abrir);
}

function toggleReportsAccordion() {
    const acc = document.getElementById('acc-reports');
    const icon = document.getElementById('icon-acc-reports');
    const btn = document.getElementById('btn-acc-reports');

    if (acc.classList.contains('open')) {
        acc.classList.remove('open');
        if (icon) icon.textContent = '▼';
        if (btn) btn.classList.remove('active');
    } else {
        cerrarOtrosAcordeones('reports');
        acc.classList.add('open');
        if (icon) icon.textContent = '▲';
        if (btn) btn.classList.add('active');
    }
}

function toggleTheme() {
    const html = document.documentElement;
    const currentTheme = html.getAttribute('data-theme');
    const newTheme = currentTheme === 'dark' ? 'light' : 'dark';
    html.setAttribute('data-theme', newTheme);
    localStorage.setItem('jztech-theme', newTheme);
}

document.addEventListener('DOMContentLoaded', () => {
    const savedTheme = localStorage.getItem('jztech-theme') || 'light';
    document.documentElement.setAttribute('data-theme', savedTheme);

    document.querySelectorAll('.rail-btn, .rail-sub-btn').forEach(btn => {
        if(btn.id === 'btn-toggle-help' || btn.id === 'btn-acc-reports' || btn.getAttribute('data-on-click')?.includes('toggleTheme')) return;

        btn.addEventListener('click', () => {
            document.querySelectorAll('.rail-btn, .rail-sub-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');

            if (btn.classList.contains('rail-sub-btn')) {
                document.getElementById('btn-acc-reports')?.classList.add('active');
            }

            if (window.innerWidth <= 900) {
                const sidebar = document.getElementById('sidebar');
                const overlay = document.getElementById('sidebar-overlay');
                if(sidebar && overlay) {
                    sidebar.classList.remove('open');
                    overlay.classList.remove('open');
                }
            }
        });
    });
});

// === Rediseño del panel (design_handoff_tracker360): cabecera por seccion, subpestañas, pie de la barra y Ctrl+K ===
const GRUPO_SECCION = {
    'section-dashboard': 'Operación', 'section-orders': 'Operación', 'section-purchases': 'Operación', 'section-ajustes': 'Operación', 'section-inventory': 'Operación',
    'section-items': 'Catálogo', 'section-warehouse': 'Catálogo', 'section-entities': 'Catálogo',
    'section-canal-publicaciones': 'Tiendas online',
    'section-kardex': 'Análisis · Reportes', 'section-rep-stock': 'Análisis · Reportes', 'section-rep-orders': 'Análisis · Reportes', 'section-rep-remitos': 'Análisis · Reportes',
    'section-rep-diffs': 'Análisis · Reportes', 'section-rep-invoices': 'Análisis · Reportes', 'section-rep-po': 'Análisis · Reportes', 'section-logs': 'Análisis',
    'section-settings': 'Sistema · Configuración', 'section-users': 'Sistema · Configuración', 'section-soporte': 'Sistema · Configuración',
};
const REPORTES_TABS = [['section-rep-stock', 'Stock'], ['section-kardex', 'Traza de Artículos'], ['section-rep-orders', 'Pedidos'], ['section-rep-remitos', 'Remitos de Compra'],
    ['section-rep-diffs', 'Diferencias de Recepción'], ['section-rep-invoices', 'Facturas de Compra'], ['section-rep-po', 'Órdenes de Compra']];

function prepararCabeceras() {
    document.querySelectorAll('.view-section').forEach(sec => {
        const header = sec.querySelector(':scope > .content-header');
        const h2 = header && header.querySelector('h2');
        if (!h2) return;
        let caja = h2.parentElement;
        if (caja === header) {
            caja = document.createElement('div');
            header.insertBefore(caja, h2);
            caja.appendChild(h2);
        }
        caja.classList.add('ph-title');
        const eyebrow = document.createElement('div');
        eyebrow.className = 'eyebrow';
        const grupo = GRUPO_SECCION[sec.id] || 'Operación';
        eyebrow.innerHTML = (sec.id === 'section-dashboard' ? '<span class="dot"></span>' : '') + escapeHTML(grupo);
        caja.insertBefore(eyebrow, h2);
        // Reportes: subpestañas que replican los sub-items del menu.
        if (REPORTES_TABS.some(([id]) => id === sec.id)) {
            const tabs = document.createElement('div');
            tabs.className = 'subtabs';
            tabs.innerHTML = REPORTES_TABS.map(([id, l]) => `<button type="button" class="${id === sec.id ? 'active' : ''}" data-on-click="switchView('${id}')">${l}</button>`).join('');
            header.insertAdjacentElement('afterend', tabs);
        }
    });
    // Inicio: "En vivo · <fecha>" como el handoff.
    const dash = document.querySelector('#section-dashboard .eyebrow');
    if (dash) dash.innerHTML = '<span class="dot"></span>En vivo · ' + escapeHTML(new Date().toLocaleDateString('es-AR', { weekday: 'long', day: 'numeric', month: 'long' }));
}

function marcarSubtabCompras(tabId) {
    document.querySelectorAll('#compras-subtabs button').forEach(b => b.classList.toggle('active', b.getAttribute('data-tab') === tabId));
}
// Al abrir un modulo de compras desde el menu o desde Inicio, la subpestaña queda marcada.
if (typeof abrirCompras === 'function') {
    const _abrirCompras = abrirCompras;
    window.abrirCompras = async function (tabId, btn, abrirAlta) { const r = await _abrirCompras(tabId, btn, abrirAlta); marcarSubtabCompras(tabId); return r; };
}

async function pintarUsuarioRail() {
    try {
        const r = await fetch('/api/auth/me', { credentials: 'include' });
        if (!r.ok) return;
        const u = await r.json();
        const nombre = u.full_name || u.username || '';
        document.getElementById('rail-user-nombre').textContent = nombre;
        document.getElementById('rail-user-rol').textContent = u.role || '';
        document.getElementById('rail-user-ini').textContent = nombre.split(/[\s._-]+/).filter(Boolean).map(w => w[0]).slice(0, 2).join('').toUpperCase() || '?';
        const h2 = document.querySelector('#section-dashboard .content-header h2');
        if (h2) { const h = new Date().getHours(); h2.textContent = (h < 13 ? 'Buen día' : h < 20 ? 'Buenas tardes' : 'Buenas noches') + ', ' + nombre; }
    } catch (e) { /* sin sesion: el fetchAPI de las secciones redirige al login */ }
}

// Nombre de la empresa bajo el logo (viene de la configuracion, si esta cargada).
function pintarEmpresaRail(nombre) {
    const el = document.getElementById('rail-empresa');
    if (el && nombre) el.textContent = 'WMS · ' + nombre;
}

// ---- Buscador Ctrl+K: modulos, acciones y busqueda rapida de SKU o pedido con las funciones de cada seccion ----
let cmdIdx = 0, cmdVisibles = [];
const CMD_ICONOS = {
    modulo: '<rect x="3" y="3" width="7" height="9" rx="1"></rect><rect x="14" y="3" width="7" height="5" rx="1"></rect><rect x="14" y="12" width="7" height="9" rx="1"></rect><rect x="3" y="16" width="7" height="5" rx="1"></rect>',
    accion: '<line x1="12" y1="5" x2="12" y2="19"></line><line x1="5" y1="12" x2="19" y2="12"></line>',
    sku: '<path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z"></path><line x1="7" y1="7" x2="7.01" y2="7"></line>',
    pedido: '<path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"></path><polyline points="3.27 6.96 12 12.01 20.73 6.96"></polyline><line x1="12" y1="22.08" x2="12" y2="12"></line>',
};
function cmdItems(q) {
    const its = [];
    // Modulos: los botones visibles del menu (el supervisor no ve los que aplicarModoSupervisor oculto).
    document.querySelectorAll('.rail-nav .rail-btn, .rail-nav .rail-sub-btn').forEach(b => {
        if (b.style.display === 'none' || b.id.startsWith('btn-acc-')) return;
        const padre = b.closest('.rail-accordion') ? b.closest('.rail-item').querySelector('.rail-btn .rail-label')?.textContent.trim() : null;
        const label = (b.querySelector('.rail-label')?.textContent || b.textContent).trim();
        its.push({ l: 'Ir a ' + (padre ? padre + ' › ' : '') + label, k: 'MÓDULO', ic: 'modulo', go: () => b.click() });
    });
    const esSup = window.ROL_ACTUAL === 'SUPERVISOR';
    if (!esSup) {
        its.push({ l: 'Nuevo pedido de venta', k: 'ACCIÓN', ic: 'accion', go: () => { switchView('section-orders'); openManualOrderModal(); } });
        its.push({ l: 'Nuevo artículo', k: 'ACCIÓN', ic: 'accion', go: () => { switchView('section-items'); openModal('modal-nuevo-articulo'); } });
        its.push({ l: 'Nuevo remito de entrada', k: 'ACCIÓN', ic: 'accion', go: () => abrirCompras('tab-remito', null, true) });
        its.push({ l: 'Nueva orden de compra', k: 'ACCIÓN', ic: 'accion', go: () => abrirCompras('tab-po', null, true) });
        its.push({ l: 'Nuevo traspaso (ODT)', k: 'ACCIÓN', ic: 'accion', go: () => abrirCompras('tab-transfer', null, true) });
        its.push({ l: 'Crear ajuste de stock', k: 'ACCIÓN', ic: 'accion', go: () => { switchView('section-ajustes'); if (typeof nuevoAjuste === 'function') nuevoAjuste(); } });
        its.push({ l: 'Nueva sesión de conteo', k: 'ACCIÓN', ic: 'accion', go: () => { switchView('section-inventory'); openCreateInventoryModal(); } });
    }
    its.push({ l: 'Control puntual de stock', k: 'ACCIÓN', ic: 'accion', go: () => { switchView('section-inventory'); openSpotCheckModal(); } });
    const lista = its.filter(c => !q || c.l.toLowerCase().includes(q));
    if (q) {
        lista.push({ l: 'Buscar artículo «' + q + '»', k: 'SKU', ic: 'sku', mono: true, go: () => { switchView('section-items'); const f = document.getElementById('form-buscar-articulos'); if (f) f.reset(); document.getElementById('search-sku').value = q; buscarArticulos(); } });
        lista.push({ l: 'Buscar pedido «' + q + '»', k: 'PEDIDO', ic: 'pedido', mono: true, go: () => { switchView('section-orders'); const f = document.getElementById('filtros-pedidos'); if (f) f.reset(); document.getElementById('f-ped-numero').value = q; buscarPedidos(); } });
    }
    return lista;
}
function abrirCmd() {
    document.getElementById('cmd').classList.add('open');
    const q = document.getElementById('cmd-q'); q.value = ''; cmdIdx = 0; renderCmd();
    setTimeout(() => q.focus(), 30);
    if (window.innerWidth <= 900) { document.getElementById('sidebar')?.classList.remove('open'); document.getElementById('sidebar-overlay')?.classList.remove('open'); }
}
function cerrarCmd() { document.getElementById('cmd').classList.remove('open'); }
function cerrarCmdSiFondo(ev) { if (ev.target && ev.target.id === 'cmd') cerrarCmd(); }
function renderCmd() {
    const q = (document.getElementById('cmd-q').value || '').trim().toLowerCase();
    cmdVisibles = cmdItems(q).slice(0, q ? 14 : 10);
    if (cmdIdx >= cmdVisibles.length) cmdIdx = 0;
    document.getElementById('cmd-list').innerHTML = cmdVisibles.length
        ? cmdVisibles.map((c, i) => `<button type="button" class="cmd-it ${i === cmdIdx ? 'on' : ''}" data-on-click="cmdIr(${i})"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${CMD_ICONOS[c.ic]}</svg><span class="t ${c.mono ? 'mono' : ''}">${escapeHTML(c.l)}</span><span class="k">${c.k}</span></button>`).join('')
        : '<div class="cmd-vacio">Sin resultados.</div>';
}
function cmdIr(i) { const c = cmdVisibles[i]; if (!c) return; cerrarCmd(); c.go(); }
function cmdTecla(ev) {
    if (ev.key === 'ArrowDown') { ev.preventDefault(); cmdIdx = Math.min(cmdVisibles.length - 1, cmdIdx + 1); renderCmd(); }
    else if (ev.key === 'ArrowUp') { ev.preventDefault(); cmdIdx = Math.max(0, cmdIdx - 1); renderCmd(); }
    else if (ev.key === 'Enter') { ev.preventDefault(); cmdIr(cmdIdx); }
}
document.addEventListener('keydown', (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); abrirCmd(); }
    if (e.key === 'Escape') cerrarCmd();
});

document.addEventListener('DOMContentLoaded', () => {
    prepararCabeceras();
    marcarSubtabCompras('tab-po');
    pintarUsuarioRail();
});
window.abrirCmd = abrirCmd; window.cerrarCmd = cerrarCmd; window.cerrarCmdSiFondo = cerrarCmdSiFondo; window.renderCmd = renderCmd; window.cmdIr = cmdIr; window.cmdTecla = cmdTecla;
window.marcarSubtabCompras = marcarSubtabCompras; window.pintarEmpresaRail = pintarEmpresaRail;
