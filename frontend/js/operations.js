// === MÓDULO DE OPERACIONES, STOCK, REPORTES Y CONFIGURACIÓN ===

async function loadDashboardSummary() {
    try {
        const data = await fetchAPI('/api/admin/dashboard');
        
        const pendingDiv = document.getElementById('dashPendingOrders');
        if (pendingDiv) {
            pendingDiv.innerHTML = data.pending_orders && data.pending_orders.length ? 
                data.pending_orders.map(o => `<li class="list-group-item d-flex justify-content-between align-items-center"><div><strong>${o.document_number}</strong><br><small class="text-muted">${o.company_name}</small></div><span class="badge bg-warning text-dark">${o.status}</span></li>`).join('') :
                '<li class="list-group-item text-muted small">Sin pedidos pendientes</li>';
        }

        const transfersDiv = document.getElementById('dashActiveTransfers');
        if (transfersDiv) {
            transfersDiv.innerHTML = data.active_transfers && data.active_transfers.length ?
                data.active_transfers.map(t => `<li class="list-group-item d-flex justify-content-between align-items-center"><div><strong>${t.transfer_number}</strong><br><small class="text-muted">${t.origin_branch} ➔ ${t.destination_branch}</small></div></li>`).join('') :
                '<li class="list-group-item text-muted small">Sin traspasos activos</li>';
        }

        const logsDiv = document.getElementById('dashLatestLogs');
        if (logsDiv) {
            logsDiv.innerHTML = data.latest_logs && data.latest_logs.length ?
                data.latest_logs.map(l => `<li class="list-group-item small"><strong>${l.username}</strong>: ${l.action} <span class="text-muted float-end">${new Date(l.created_at).toLocaleTimeString()}</span></li>`).join('') :
                '<li class="list-group-item text-muted small">Sin actividad reciente</li>';
        }
    } catch (e) {}
}

async function loadAdminStock() {
    const tbody = document.getElementById('stockTableBody');
    if (!tbody) return;
    tbody.innerHTML = '<tr><td colspan="6" class="text-center py-3"><div class="spinner-border spinner-border-sm"></div> Cargando stock...</td></tr>';

    try {
        const rows = await fetchAPI('/api/admin/stock');
        if (!rows || rows.length === 0) {
            tbody.innerHTML = '<tr><td colspan="6" class="text-center text-muted">Sin existencias registradas.</td></tr>';
            return;
        }

        tbody.innerHTML = rows.map(r => `
            <tr>
                <td class="fw-bold"><code>${r.sku}</code></td>
                <td>${r.description || '-'}</td>
                <td>${r.branch_name || '-'} / ${r.sector_name || '-'}</td>
                <td><span class="badge bg-light text-dark border">${r.location_code}</span></td>
                <td class="fw-bold text-success">${r.quantity} un</td>
                <td><small class="text-muted">${new Date(r.updated_at).toLocaleDateString()}</small></td>
            </tr>
        `).join('');
    } catch (e) {
        tbody.innerHTML = '<tr><td colspan="6" class="text-center text-danger">Error al cargar stock.</td></tr>';
    }
}

async function loadAdminKardex() {
    const tbody = document.getElementById('kardexTableBody');
    if (!tbody) return;
    tbody.innerHTML = '<tr><td colspan="7" class="text-center py-3"><div class="spinner-border spinner-border-sm"></div> Cargando historial...</td></tr>';

    try {
        const rows = await fetchAPI('/api/admin/stock/kardex');
        if (!rows || rows.length === 0) {
            tbody.innerHTML = '<tr><td colspan="7" class="text-center text-muted">Sin movimientos registrados.</td></tr>';
            return;
        }

        tbody.innerHTML = rows.map(r => `
            <tr>
                <td><small class="text-muted">${new Date(r.created_at).toLocaleString()}</small></td>
                <td class="fw-bold"><code>${r.sku}</code></td>
                <td><span class="badge ${r.quantity > 0 ? 'bg-success' : 'bg-danger'}">${r.movement_type}</span></td>
                <td class="fw-bold ${r.quantity > 0 ? 'text-success' : 'text-danger'}">${r.quantity > 0 ? '+' : ''}${r.quantity}</td>
                <td><small>${r.branch_name || ''} - ${r.location_code || 'Sin loc'}</small></td>
                <td><code>${r.reference_document || '-'}</code></td>
                <td><small>${r.username}</small></td>
            </tr>
        `).join('');
    } catch (e) {
        tbody.innerHTML = '<tr><td colspan="7" class="text-center text-danger">Error al cargar kardex.</td></tr>';
    }
}

async function loadSettings() {
    try {
        const settings = await fetchAPI('/api/settings');
        for (const [key, value] of Object.entries(settings)) {
            const input = document.getElementById(`setting_${key}`);
            if (input) {
                if (input.type === 'checkbox') {
                    input.checked = value === 'true';
                } else {
                    input.value = value;
                }
            }
        }
    } catch (e) {}
}

async function saveSettingsForm(event) {
    if (event) event.preventDefault();
    const inputs = document.querySelectorAll('[id^="setting_"]');
    const payload = {};

    inputs.forEach(input => {
        const key = input.id.replace('setting_', '');
        payload[key] = input.type === 'checkbox' ? (input.checked ? 'true' : 'false') : input.value.trim();
    });

    try {
        await fetchAPI('/api/admin/settings', { method: 'PUT', body: payload });
        showToast("Configuración del sistema guardada.", "success");
    } catch (e) {}
}

async function generateApiKey() {
    if (!confirm("¿Generar una nueva API Key? La anterior dejará de funcionar.")) return;
    try {
        const res = await fetchAPI('/api/admin/settings/generate-key', { method: 'POST' });
        const input = document.getElementById('setting_tracker360_api_key');
        if (input) input.value = res.new_key;
        showToast("Nueva API Key generada.", "success");
    } catch (e) {}
}

async function loadIntegrations() {
    const tbody = document.getElementById('integrationsTableBody');
    if (!tbody) return;

    try {
        const channels = await fetchAPI('/api/admin/integrations');
        tbody.innerHTML = channels.map(c => `
            <tr>
                <td class="fw-bold">${c.name}</td>
                <td><span class="badge bg-secondary">${c.channel_type}</span></td>
                <td><small class="text-truncate d-inline-block" style="max-width: 200px;">${c.target_url}</small></td>
                <td class="text-end">
                    <button class="btn btn-sm btn-outline-danger" onclick="deleteIntegration('${c.id}')">🗑️ Eliminar</button>
                </td>
            </tr>
        `).join('');
    } catch (e) {}
}

async function saveIntegrationForm(event) {
    if (event) event.preventDefault();
    const payload = {
        name: document.getElementById('intName').value.trim(),
        channel_type: document.getElementById('intType').value,
        target_url: document.getElementById('intTargetUrl').value.trim(),
        api_key: document.getElementById('intApiKey').value.trim() || null
    };

    try {
        await fetchAPI('/api/admin/integrations', { method: 'POST', body: payload });
        showToast("Canal de integración agregado.", "success");
        document.getElementById('integrationForm').reset();
        loadIntegrations();
    } catch (e) {}
}

async function deleteIntegration(id) {
    if (!confirm("¿Eliminar esta integración?")) return;
    try {
        await fetchAPI(`/api/admin/integrations/${id}`, { method: 'DELETE' });
        showToast("Integración eliminada.", "success");
        loadIntegrations();
    } catch (e) {}
}
