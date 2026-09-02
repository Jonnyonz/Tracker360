// === NÚCLEO DE LÓGICA FRONTEND (admin-core.js) ===

let cachedUsers = [], cachedBranches = [], cachedSectors = [], cachedEntities = [], cachedLocations = [], suppliersCache = [];
let cachedAddressesCurrentEntity = [];
let currentItemPage = 1, totalItemPages = 1, currentItemLimit = 50, currentItemSort = 'sku', currentItemOrder = 'ASC', currentItemSearchSKU = '', currentItemSearchDesc = '';
let AppConfig = {};

let cachedOrdersList = [];
let cachedLogsList = [];

// ESTADO GLOBAL DEL MODO AYUDA CONTEXTUAL (VANILLA JS SOBERANO)
let isHelpModeActive = false;

function escapeHTML(str) {
    if (str === null || str === undefined) return '';
    return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function showToast(msg, type = 'success') {
    const container = document.getElementById('toast-container');
    if(!container) return;
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.innerHTML = `<span>${escapeHTML(msg)}</span><span style="cursor:pointer; font-weight:bold; margin-left:10px;" onclick="this.parentElement.remove()">&times;</span>`;
    container.appendChild(toast);
    setTimeout(() => { if(toast.parentElement) toast.remove(); }, 3500);
}

function setFormVal(id, val) {
    const el = document.getElementById(id);
    if (el && val !== undefined && val !== null) {
        el.value = val;
    }
}

function getFormVal(id) {
    const el = document.getElementById(id);
    return el ? el.value.trim() : '';
}

window.addEventListener('online', () => { const b = document.getElementById('net-banner'); if(b) b.style.display = 'none'; });
window.addEventListener('offline', () => { const b = document.getElementById('net-banner'); if(b) b.style.display = 'block'; });

// === MOTOR NATIVO DE MODO AYUDA CONTEXTUAL ===

function toggleHelpMode() {
    isHelpModeActive = !isHelpModeActive;
    const badge = document.getElementById('help-mode-badge');
    const popover = document.getElementById('help-popover');
    
    if (isHelpModeActive) {
        document.body.classList.add('help-mode-active');
        if (badge) { badge.textContent = 'ON'; badge.className = 'badge badge-success'; }
        showToast('Modo Ayuda ACTIVADO. Pase el cursor sobre los elementos resaltados.', 'info');
    } else {
        document.body.classList.remove('help-mode-active');
        if (badge) { badge.textContent = 'OFF'; badge.className = 'badge badge-neutral'; }
        if (popover) popover.style.display = 'none';
        showToast('Modo Ayuda DESACTIVADO.', 'neutral');
    }
}

function initHelpModeListeners() {
    const popover = document.getElementById('help-popover');
    const popTitle = document.getElementById('help-popover-title');
    const popBody = document.getElementById('help-popover-body');
    const popRule = document.getElementById('help-popover-rule');

    document.addEventListener('mouseover', (e) => {
        if (!isHelpModeActive) return;
        const target = e.target.closest('[data-help]');
        if (!target || !popover) return;

        const rawData = target.getAttribute('data-help') || '';
        const parts = rawData.split('|');
        
        const title = parts[0] || 'Ayuda Contextual';
        const body = parts[1] || '';
        const rule = parts[2] || '';

        if (popTitle) popTitle.textContent = title;
        if (popBody) popBody.textContent = body;
        if (popRule) {
            if (rule) {
                popRule.textContent = 'Regla de Negocio: ' + rule;
                popRule.style.display = 'block';
            } else {
                popRule.style.display = 'none';
            }
        }

        const rect = target.getBoundingClientRect();
        popover.style.display = 'block';
        
        let top = rect.bottom + 8;
        let left = rect.left;

        if (top + popover.offsetHeight > window.innerHeight) {
            top = rect.top - popover.offsetHeight - 8;
        }
        if (left + popover.offsetWidth > window.innerWidth) {
            left = window.innerWidth - popover.offsetWidth - 16;
        }
        if (left < 10) left = 10;

        popover.style.top = `${Math.max(10, top)}px`;
        popover.style.left = `${left}px`;
    });

    document.addEventListener('mouseout', (e) => {
        if (!isHelpModeActive || !popover) return;
        const target = e.target.closest('[data-help]');
        if (target) {
            popover.style.display = 'none';
        }
    });
}

function switchView(secId, btnElement = null) {
    document.querySelectorAll('.view-section, .rail-btn, .rail-sub-btn').forEach(e => e.classList.remove('active'));
    
    const sec = document.getElementById(secId);
    if (sec) sec.classList.add('active');
    
    if (btnElement) {
        btnElement.classList.add('active');
        if (btnElement.classList.contains('rail-sub-btn')) {
            document.getElementById('btn-acc-reports')?.classList.add('active');
        }
    } else {
        const b = document.querySelector(`[onclick*="${secId}"]`);
        if(b) {
            b.classList.add('active');
            if (b.classList.contains('rail-sub-btn')) {
                document.getElementById('btn-acc-reports')?.classList.add('active');
                document.getElementById('acc-reports')?.classList.add('open');
            }
        }
    }

    if(secId === 'section-dashboard') loadDashboard();
    if(secId === 'section-users' && typeof loadUsers === 'function') loadUsers();
    if(secId === 'section-entities' && typeof loadEntities === 'function') loadEntities();
    if(secId === 'section-warehouse' && typeof loadWarehouseData === 'function') loadWarehouseData();
    if(secId === 'section-items' && typeof loadItems === 'function') loadItems();
    if(secId === 'section-purchases' && typeof loadPurchaseSelectors === 'function') { loadPurchaseSelectors(); loadAllPurchaseHistories(); }
    if(secId === 'section-orders' && typeof loadOrders === 'function') loadOrders();
    if(secId === 'section-inventory' && typeof loadInventorySessions === 'function') loadInventorySessions();
    if(secId === 'section-logs') loadLogs();
    if(secId === 'section-settings' && typeof loadSettings === 'function') { loadSettings(); if(typeof loadIntegrationChannels === 'function') loadIntegrationChannels(); }
    
    if(secId === 'section-kardex' && typeof window.loadKardexSelectors === 'function') window.loadKardexSelectors();
    if(secId === 'section-rep-stock' && typeof window.loadReportStockSelectors === 'function') window.loadReportStockSelectors();
    if(secId === 'section-rep-remitos' && typeof window.loadReportRemitosSelectors === 'function') window.loadReportRemitosSelectors();
    if(secId === 'section-rep-invoices' && typeof window.loadReportInvoicesSelectors === 'function') window.loadReportInvoicesSelectors();
    if(secId === 'section-rep-po' && typeof window.loadReportPOSelectors === 'function') window.loadReportPOSelectors();
}

function navigateToSubTab(sectionId, tabId) { switchView(sectionId); if(typeof switchPurchaseTab === 'function') switchPurchaseTab(tabId); }
function openModal(id) { const m = document.getElementById(id); if(m) m.style.display = 'flex'; }
function closeModal(id) { const m = document.getElementById(id); if(m) m.style.display = 'none'; }

async function fetchAPI(url, options = {}) {
    try {
        const r = await fetch(url, options);
        if (r.status === 401) { window.location.href = '/index.html'; return null; }
        if(!r.ok) {
            const err = await r.json().catch(()=>({}));
            const msg = err.detail || r.statusText || 'Error en el servidor';
            if(options.method && options.method !== 'GET') { showToast('Error: ' + msg, 'error'); }
            return null;
        }
        return await r.json();
    } catch(e) {
        const banner = document.getElementById('net-banner');
        if (banner) banner.style.display = 'block';
        if(options.method && options.method !== 'GET') { showToast('Falla de conexión con el servidor', 'error'); }
        return null;
    }
}

async function loadDashboard() {
    try {
        const data = await fetchAPI('/api/admin/dashboard');
        if (!data) return;
        const bodyOrders = document.getElementById('dash-orders-body');
        if (bodyOrders) {
            bodyOrders.innerHTML = '';
            const pending = data.pending_orders || [];
            if (pending.length === 0) {
                bodyOrders.innerHTML = '<tr><td colspan="3" style="color:var(--text-muted); text-align:center;">No hay pedidos pendientes.</td></tr>';
            } else {
                pending.forEach(o => {
                    bodyOrders.innerHTML += `<tr><td style="font-weight:bold;">${escapeHTML(o.document_number)}</td><td>${escapeHTML(o.company_name)}</td><td><span class="badge badge-warning">${escapeHTML(o.status)}</span></td></tr>`;
                });
            }
        }
    } catch (err) {
        console.error("Error en loadDashboard:", err);
    }
}

async function loadLogs() { 
    const body = document.getElementById('table-logs-body');
    if(body) body.innerHTML = '<tr><td colspan="4" style="text-align:center; padding:2rem; color:var(--accent); font-weight:bold;">Cargando auditoría...</td></tr>';
    
    const logs = await fetchAPI('/api/admin/logs'); 
    if(logs) { 
        cachedLogsList = logs;
        filterLogs();
    } else if(body) {
        body.innerHTML = '<tr><td colspan="4" style="text-align:center; padding:2rem; color:var(--danger);">Error al cargar los registros de auditoría.</td></tr>';
    }
}

function filterLogs() {
    const body = document.getElementById('table-logs-body');
    if(!body) return;
    
    const query = document.getElementById('search-log-text')?.value.toLowerCase().trim() || '';

    const filtered = cachedLogsList.filter(l => {
        const matchText = !query || l.username.toLowerCase().includes(query) || l.action.toLowerCase().includes(query) || (l.details && l.details.toLowerCase().includes(query));
        return matchText;
    });

    if (filtered.length === 0) {
        body.innerHTML = '<tr><td colspan="4" style="text-align:center; padding:2rem; color:var(--text-muted); font-weight:bold;">No hay registros de auditoría.</td></tr>';
        return;
    }

    body.innerHTML = filtered.map(l => {
        return `<tr>
            <td><small class="text-muted" style="font-weight:600;">${new Date(l.created_at).toLocaleString()}</small></td>
            <td style="font-weight:bold; color:var(--accent);">${escapeHTML(l.username)}</td>
            <td><span class="badge badge-neutral">${escapeHTML(l.action)}</span></td>
            <td><small>${escapeHTML(l.details || '-')}</small></td>
        </tr>`;
    }).join('');
}

document.addEventListener('DOMContentLoaded', () => {
    const btnLogout = document.getElementById('btnLogout');
    if (btnLogout) {
        btnLogout.addEventListener('click', async () => {
            await fetch('/api/auth/logout', { method: 'POST' });
            window.location.href = '/index.html';
        });
    }
    initHelpModeListeners();
});

window.onload = async () => {
    if (typeof loadSettings === 'function') {
        try { await loadSettings(); } catch (err) { console.error("Error en loadSettings:", err); }
    }
    try { await loadDashboard(); } catch (err) { console.error("Error en loadDashboard:", err); }
};

window.escapeHTML = escapeHTML;
window.showToast = showToast;
window.setFormVal = setFormVal;
window.getFormVal = getFormVal;
window.switchView = switchView;
window.navigateToSubTab = navigateToSubTab;
window.openModal = openModal;
window.closeModal = closeModal;
window.fetchAPI = fetchAPI;
window.toggleHelpMode = toggleHelpMode;