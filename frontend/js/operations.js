// === MÓDULO DE OPERACIONES, STOCK, REPORTES Y CONFIGURACIÓN ===

async function legacy_loadDashboardSummary() {
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

async function legacy_loadAdminStock() {
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

async function legacy_loadAdminKardex() {
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

async function loadOperationsSettings() {
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

// =========================================================================================
// === NUEVA LÓGICA DE KARDEX (TRAZA DE ARTÍCULOS) MÁXIMA PERFORMANCE Y ESTABILIDAD ======
// =========================================================================================

let kardexAutocompleteTimer = null;

function setupKardexAutocomplete() {
    const input = document.getElementById('kardex-filter-sku');
    if (!input || input.hasAttribute('data-autocomplete-setup')) return;
    input.setAttribute('data-autocomplete-setup', 'true');
    
    // Contenedor seguro para posicionamiento absoluto (Vanilla JS)
    const wrapper = document.createElement('div');
    wrapper.style.position = 'relative';
    wrapper.style.display = 'flex';
    wrapper.style.width = '100%';
    input.parentNode.insertBefore(wrapper, input);
    wrapper.appendChild(input);
    
    // Caja flotante de sugerencias
    const list = document.createElement('div');
    list.style.position = 'absolute';
    list.style.top = '100%';
    list.style.left = '0';
    list.style.right = '0';
    list.style.backgroundColor = 'var(--card-bg)';
    list.style.border = '1px solid var(--border-color)';
    list.style.borderRadius = '0 0 6px 6px';
    list.style.zIndex = '1000';
    list.style.maxHeight = '280px';
    list.style.overflowY = 'auto';
    list.style.boxShadow = '0 10px 15px -3px rgba(0,0,0,0.1)';
    list.style.display = 'none';
    wrapper.appendChild(list);

    // Debouncer nativo para proteger la Base de Datos
    input.addEventListener('input', (e) => {
        clearTimeout(kardexAutocompleteTimer);
        const query = e.target.value.trim();
        
        if (query.length < 2) {
            list.style.display = 'none';
            return;
        }
        
        kardexAutocompleteTimer = setTimeout(async () => {
            try {
                // Filtramos cruzado (sku y descripción) limitando estrictamente a 10
                const data = await fetchAPI(`/api/admin/items?page=1&limit=10&sku=${encodeURIComponent(query)}&description=${encodeURIComponent(query)}&sort_by=sku&sort_order=ASC`);
                
                if (!data || !data.items || data.items.length === 0) {
                    list.innerHTML = '<div style="padding:12px; color:var(--text-muted); font-size:0.85rem; text-align:center; font-weight:bold;">Sin coincidencias</div>';
                    list.style.display = 'block';
                    return;
                }
                
                list.innerHTML = data.items.map((item, idx) => `
                    <div class="autocomplete-suggestion" data-idx="${idx}" style="padding:10px 12px; border-bottom:1px solid var(--border-color); cursor:pointer; font-size:0.85rem; transition: background 0.2s;" 
                         onmouseover="this.style.backgroundColor='#F0F9FF'" 
                         onmouseout="this.style.backgroundColor='transparent'">
                        <strong style="color:var(--primary-blue); font-size:0.95rem;">${escapeHTML(item.sku)}</strong><br>
                        <span style="color:var(--text-muted);">${escapeHTML(item.description)}</span>
                    </div>
                `).join('');
                
                // Inyectar evento click sin chocar con scopes
                Array.from(list.children).forEach((child) => {
                    child.addEventListener('click', function() {
                        const idx = this.getAttribute('data-idx');
                        if (idx !== null && data.items[idx]) {
                            input.value = data.items[idx].sku;
                            list.style.display = 'none';
                        }
                    });
                });
                
                list.style.display = 'block';
            } catch (err) {
                list.style.display = 'none';
            }
        }, 350); // 350ms de pausa de seguridad
    });

    // Cierre al hacer clic fuera del control
    document.addEventListener('click', (e) => {
        if (e.target !== input && !wrapper.contains(e.target)) {
            list.style.display = 'none';
        }
    });
}

function loadKardexSelectors() {
    setupKardexAutocomplete();
    
    // Inyectar Sucursales (Cacheadas desde el Admin-Init/Warehouse)
    const branchSelect = document.getElementById('kardex-filter-branch');
    if (branchSelect && typeof cachedBranches !== 'undefined') {
        branchSelect.innerHTML = '<option value="">-- Todas --</option>' + 
            cachedBranches.map(b => `<option value="${b.id}">${escapeHTML(b.name)}</option>`).join('');
    }
    onKardexBranchChange();
    
    // UX: Fechas autocompletadas (Desde: 1 mes atrás, Hasta: Hoy)
    const dateFrom = document.getElementById('kardex-filter-date-from');
    const dateTo = document.getElementById('kardex-filter-date-to');
    if (dateFrom && dateTo && !dateFrom.value) {
        const today = new Date();
        dateTo.value = today.toISOString().split('T')[0];
        const lastMonth = new Date(today);
        lastMonth.setMonth(lastMonth.getMonth() - 1);
        dateFrom.value = lastMonth.toISOString().split('T')[0];
    }
}

function onKardexBranchChange() {
    const branchId = document.getElementById('kardex-filter-branch')?.value;
    const sectorSelect = document.getElementById('kardex-filter-sector');
    
    if (!sectorSelect || typeof cachedSectors === 'undefined') return;
    
    sectorSelect.innerHTML = '<option value="">-- Todos --</option>';
    cachedSectors.forEach(s => {
        // Mostrar si no hay sucursal elegida, o si coincide exactamente
        if (!branchId || String(s.branch_id) === String(branchId)) {
            sectorSelect.innerHTML += `<option value="${s.id}">${escapeHTML(s.name)}</option>`;
        }
    });
}

async function loadKardexFiltered(event) {
    if (event) event.preventDefault();
    
    const tbody = document.getElementById('table-kardex-body');
    if (!tbody) return;
    
    const btn = event ? event.target.querySelector('button[type="submit"]') : null;
    if (btn) { btn.disabled = true; btn.textContent = "Generando..."; }
    
    tbody.innerHTML = '<tr><td colspan="9" style="text-align:center; padding:2rem;"><div style="color:var(--primary-blue); font-weight:bold;">Generando reporte en base de datos...</div></td></tr>';
    
    // Recolección de Filtros
    const sku = document.getElementById('kardex-filter-sku').value.trim();
    const branchId = document.getElementById('kardex-filter-branch').value;
    const sectorId = document.getElementById('kardex-filter-sector').value;
    const locCode = document.getElementById('kardex-filter-location').value.trim();
    const dateFrom = document.getElementById('kardex-filter-date-from').value;
    const dateTo = document.getElementById('kardex-filter-date-to').value;
    const timeFrom = document.getElementById('kardex-filter-time-from').value;
    const timeTo = document.getElementById('kardex-filter-time-to').value;
    const mType = document.getElementById('kardex-filter-type').value;

    const params = new URLSearchParams();
    if (sku) params.append('sku', sku);
    if (branchId) params.append('branch_id', branchId);
    if (sectorId) params.append('sector_id', sectorId);
    if (locCode) params.append('location_code', locCode);
    if (dateFrom) params.append('date_from', dateFrom);
    if (timeFrom) params.append('time_from', timeFrom);
    if (dateTo) params.append('date_to', dateTo);
    if (timeTo) params.append('time_to', timeTo);
    if (mType) params.append('movement_type', mType);

    try {
        const rows = await fetchAPI(`/api/admin/stock/kardex?${params.toString()}`);
        
        if (!rows || rows.length === 0) {
            tbody.innerHTML = '<tr><td colspan="9" style="text-align:center; color:var(--error-red); padding:2rem; font-weight:bold;">No se encontraron movimientos para los filtros seleccionados.</td></tr>';
        } else {
            tbody.innerHTML = rows.map(r => `
                <tr>
                    <td><small class="text-muted" style="font-weight:600;">${new Date(r.created_at).toLocaleString()}</small></td>
                    <td class="fw-bold" style="color:var(--primary-blue); font-weight:bold;"><code>${escapeHTML(r.sku)}</code></td>
                    <td><small>${escapeHTML(r.description)}</small></td>
                    <td><span class="badge ${r.quantity > 0 ? 'badge-info' : 'badge-warning'}">${escapeHTML(r.movement_type)}</span></td>
                    <td><small>${escapeHTML(r.branch_name || '')} > ${escapeHTML(r.sector_name || '')}</small></td>
                    <td style="font-weight:bold;">${escapeHTML(r.location_code || '-')}</td>
                    <td style="font-weight:bold; color:var(--success-green); font-size:1rem;">${r.quantity > 0 ? '+' + r.quantity : ''}</td>
                    <td style="font-weight:bold; color:var(--error-red); font-size:1rem;">${r.quantity < 0 ? r.quantity : ''}</td>
                    <td><small>${escapeHTML(r.username)}</small></td>
                </tr>
            `).join('');
        }
    } catch (e) {
        tbody.innerHTML = '<tr><td colspan="9" style="text-align:center; color:var(--error-red); padding:2rem;">Fallo de conexión o error al generar el reporte de traza.</td></tr>';
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = "Generar Reporte de Traza"; }
    }
}