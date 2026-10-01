// === INICIALIZACIÓN DEL PANEL DE ADMINISTRACIÓN Y DASHBOARD ===

async function loadDashboardSummary() {
    try {
        const data = await fetchAPI('/api/admin/dashboard');
        if(!data) return;

        // 1. Pedidos
        const tOrders = document.getElementById('dash-orders-body');
        if(tOrders) {
            tOrders.innerHTML = data.pending_orders.length ? data.pending_orders.map(o => `<tr><td class="font-mono" style="color:var(--accent); font-weight:bold;">${escapeHTML(o.document_number)}</td><td>${escapeHTML(o.company_name)}</td><td><span class="badge badge-warning">${escapeHTML(o.status)}</span></td></tr>`).join('') : '<tr><td colspan="3" style="text-align:center; color:var(--text-muted);">Sin pedidos pendientes</td></tr>';
        }

        // 2. Traspasos
        const tTransfers = document.getElementById('dash-transfers-body');
        if(tTransfers) {
            tTransfers.innerHTML = data.active_transfers.length ? data.active_transfers.map(t => `<tr><td class="font-mono" style="color:var(--accent); font-weight:bold;">${escapeHTML(t.transfer_number)}</td><td>${escapeHTML(t.origin_branch)}</td><td>${escapeHTML(t.destination_branch)}</td></tr>`).join('') : '<tr><td colspan="3" style="text-align:center; color:var(--text-muted);">Sin traspasos activos</td></tr>';
        }

        // 3. Logs
        const tLogs = document.getElementById('dash-logs-body');
        if(tLogs) {
            tLogs.innerHTML = data.latest_logs.length ? data.latest_logs.map(l => `<tr><td>${new Date(l.created_at).toLocaleDateString()}</td><td>${escapeHTML(l.username)}</td><td><span class="badge badge-neutral">${escapeHTML(l.action)}</span></td></tr>`).join('') : '<tr><td colspan="3" style="text-align:center; color:var(--text-muted);">Sin registros</td></tr>';
        }

        // 4. KPIs y Leaderboard (FASE 5)
        const kpiUnits = document.getElementById('kpi-units-today');
        const kpiCycle = document.getElementById('kpi-cycle-time');
        if(kpiUnits) kpiUnits.textContent = data.kpis?.units_today || 0;
        if(kpiCycle) kpiCycle.textContent = (data.kpis?.avg_cycle_hours || 0).toFixed(1) + ' hrs';

        const tLeader = document.getElementById('dash-leaderboard-body');
        if(tLeader) {
            tLeader.innerHTML = (data.leaderboard && data.leaderboard.length) 
                ? data.leaderboard.map((l, i) => `<tr><td style="font-weight:bold; color:var(--text-primary);"><span style="color:var(--warning);">#${i+1}</span> ${escapeHTML(l.username)}</td><td style="text-align:center;">${escapeHTML(l.picking_lines)}</td><td style="text-align:right; font-weight:bold; color:var(--success);">${escapeHTML(l.picked_units)}</td></tr>`).join('')
                : '<tr><td colspan="3" style="text-align:center; color:var(--text-muted);">Sin actividad en los últimos 7 días.</td></tr>';
        }
    } catch (e) { console.error("Error cargando dashboard:", e); }
}

// Muestra u oculta los campos de lote en todos los formularios segun la configuracion.
async function aplicarAjusteLotes() {
    try {
        const cfg = await fetchAPI('/api/settings');
        document.body.classList.toggle('con-lotes', cfg && cfg.enable_lots_expiration === 'true');
    } catch (e) { /* sin configuracion: quedan ocultos */ }
}
window.aplicarAjusteLotes = aplicarAjusteLotes;

document.addEventListener('DOMContentLoaded', async () => {
    aplicarAjusteLotes();
    // Usuarios, configuracion e integraciones son solo del admin (ver aplicarModoSupervisor).
    const supervisor = await esSupervisor();
    loadDashboardSummary();
    if (!supervisor && typeof loadUsers === 'function') loadUsers();
    if (typeof loadEntities === 'function') loadEntities();
    if (typeof loadItems === 'function') loadItems(1);
    if (typeof loadBranches === 'function') loadBranches();
    if (typeof loadSectors === 'function') loadSectors();
    if (typeof loadLocations === 'function') loadLocations();
    if (typeof loadAdminStock === 'function') loadAdminStock();
    if (typeof loadAdminKardex === 'function') loadAdminKardex();
    if (!supervisor && typeof loadSettings === 'function') loadSettings();
    if (!supervisor && typeof loadIntegrations === 'function') loadIntegrations();
});