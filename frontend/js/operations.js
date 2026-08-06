// === MÓDULO DE OPERACIONES, STOCK, REPORTES E INVENTARIOS CÍCLICOS ===

let isFefoEnabled = false; // Variable global del sistema para Lotes/Vencimientos

// =========================================================================================
// === FUNCIONES LEGACY (DASHBOARD E INTEGRACIONES) QUE HABÍAN SIDO OMITIDAS ===========
// =========================================================================================

async function legacy_loadDashboardSummary() {
    try {
        const data = await fetchAPI('/api/admin/dashboard');
        
        const pendingBody = document.getElementById('dash-orders-body');
        if (pendingBody) {
            pendingBody.innerHTML = data.pending_orders && data.pending_orders.length ? 
                data.pending_orders.map(o => `
                    <tr>
                        <td style="color:var(--primary-blue); font-weight:bold;">${escapeHTML(o.document_number)}</td>
                        <td><small>${escapeHTML(o.company_name)}</small></td>
                        <td><span class="badge ${o.status === 'PENDING' ? 'badge-warning' : 'badge-info'}">${o.status}</span></td>
                    </tr>
                `).join('') :
                '<tr><td colspan="3" style="text-align:center; color:var(--text-muted); padding:1rem;">No hay pedidos pendientes.</td></tr>';
        }

        const transfersBody = document.getElementById('dash-transfers-body');
        if (transfersBody) {
            transfersBody.innerHTML = data.active_transfers && data.active_transfers.length ?
                data.active_transfers.map(t => `
                    <tr>
                        <td style="color:var(--primary-blue); font-weight:bold;">${escapeHTML(t.transfer_number)}</td>
                        <td><small>${escapeHTML(t.origin_branch)}</small></td>
                        <td><small>${escapeHTML(t.destination_branch)}</small></td>
                    </tr>
                `).join('') :
                '<tr><td colspan="3" style="text-align:center; color:var(--text-muted); padding:1rem;">No hay traspasos activos.</td></tr>';
        }

        const logsBody = document.getElementById('dash-logs-body');
        if (logsBody) {
            logsBody.innerHTML = data.latest_logs && data.latest_logs.length ?
                data.latest_logs.map(l => `
                    <tr>
                        <td><small style="font-weight:600; color:var(--text-muted);">${new Date(l.created_at).toLocaleString()}</small></td>
                        <td style="font-weight:bold;">${escapeHTML(l.username)}</td>
                        <td><span class="badge badge-neutral">${escapeHTML(l.action)}</span></td>
                    </tr>
                `).join('') :
                '<tr><td colspan="3" style="text-align:center; color:var(--text-muted); padding:1rem;">Sin actividad reciente.</td></tr>';
        }
    } catch (e) {
        console.error("Error al cargar el dashboard:", e);
    }
}

document.addEventListener('DOMContentLoaded', () => {
    legacy_loadDashboardSummary();
});

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
                <td><span class="badge ${r.quantity > 0 ? 'badge-info' : 'badge-warning'}">${r.movement_type}</span></td>
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

async function generateApiKey() {
    if (!confirm("¿Generar una nueva API Key? La anterior dejará de funcionar.")) return;
    try {
        const res = await fetchAPI('/api/admin/settings/generate-key', { method: 'POST' });
        const input = document.getElementById('cfg-tracker360-api-key');
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
                <td><span class="badge badge-neutral">${c.channel_type}</span></td>
                <td><small class="text-truncate d-inline-block" style="max-width: 200px;">${c.target_url}</small></td>
                <td class="text-end">
                    <button class="btn-secondary" style="color:var(--error-red); border-color:var(--error-red);" onclick="deleteIntegration('${c.id}')">Eliminar</button>
                </td>
            </tr>
        `).join('');
    } catch (e) {}
}

async function saveNewChannel(event) {
    if (event) event.preventDefault();
    const payload = {
        name: document.getElementById('chan-name').value.trim(),
        channel_type: document.getElementById('chan-type').value,
        target_url: document.getElementById('chan-url').value.trim(),
        api_key: document.getElementById('chan-key').value.trim() || null
    };

    try {
        await fetchAPI('/api/admin/integrations', { method: 'POST', body: payload });
        showToast("Canal de integración agregado.", "success");
        closeModal('modal-add-channel');
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
// === MOTOR DE SETTINGS Y FEFO ============================================================
// =========================================================================================

async function loadOperationsSettings() {
    try {
        const settings = await fetchAPI('/api/settings');
        
        // Fase 1: Sincronizar todos los inputs de settings
        for (const [key, value] of Object.entries(settings)) {
            const input = document.getElementById(`cfg-${key.replace(/_/g, '-')}`);
            if (input) {
                if (input.tagName === 'SELECT') input.value = value;
                else if (input.type === 'checkbox') input.checked = value === 'true';
                else input.value = value;
            }
        }

        // Fase 2: Aplicar diseño dinámico FEFO a las operativas
        isFefoEnabled = (settings.enable_lots_expiration === 'true');
        const lotInputs = document.querySelectorAll('.lot-input');
        lotInputs.forEach(el => {
            if(el.tagName === 'TH' || el.tagName === 'TD') {
                el.style.display = isFefoEnabled ? 'table-cell' : 'none';
            } else {
                el.style.display = isFefoEnabled ? 'block' : 'none';
            }
        });

    } catch (e) {
        console.error("Error cargando configuración operativa: ", e);
    }
}

async function saveSettingsForm(event) {
    if (event) event.preventDefault();
    const payload = {};
    const inputs = document.querySelectorAll('form[id^="form-settings"] input, form[id^="form-settings"] select, form[id^="form-settings"] textarea');
    
    inputs.forEach(input => {
        if(!input.id.startsWith('cfg-')) return;
        const key = input.id.replace('cfg-', '').replace(/-/g, '_');
        payload[key] = input.type === 'checkbox' ? (input.checked ? 'true' : 'false') : input.value.trim();
    });

    try {
        await fetchAPI('/api/settings', { method: 'POST', body: payload });
        showToast("Configuración guardada. Actualizando interfaz...", "success");
        setTimeout(() => location.reload(), 1500); 
    } catch (e) {}
}

// =========================================================================================
// === MOTOR DE AUDITORÍA: INVENTARIO FÍSICO (CONTEOS CÍCLICOS) ============================
// =========================================================================================

async function loadInventorySessions() {
    const tbody = document.getElementById('table-inventory-sessions-body');
    if (!tbody) return;
    tbody.innerHTML = '<tr><td colspan="7" class="text-center py-3">Cargando sesiones...</td></tr>';

    try {
        const rows = await fetchAPI('/api/inventory/sessions');
        if (!rows || rows.length === 0) {
            tbody.innerHTML = '<tr><td colspan="7" class="text-center text-muted">No hay sesiones de conteo registradas.</td></tr>';
            return;
        }

        tbody.innerHTML = rows.map(r => {
            const isClosed = r.status === 'CLOSED';
            const isReview = r.status === 'REVIEW';
            
            let statusBadge = '<span class="badge badge-success" style="background:#D1FAE5; color:#065F46;">ABIERTO</span>';
            if (isReview) statusBadge = '<span class="badge badge-warning" style="background:#FEF3C7; color:#92400E;">EN REVISIÓN</span>';
            if (isClosed) statusBadge = '<span class="badge badge-neutral" style="background:#E5E7EB; color:#374151;">CERRADO</span>';

            let actionBtn = `<button class="btn-submit" style="padding:4px 10px; width:auto;" onclick="openInventoryScan('${r.id}')">Escanear (App)</button>`;
            if (isReview) actionBtn = `<button class="btn-secondary" style="background:#F59E0B; color:white; border:none; padding:4px 10px;" onclick="openInventoryReview('${r.id}')">Revisar Deltas</button>`;
            if (isClosed) actionBtn = `<span style="color:var(--text-muted); font-size:0.8rem; font-weight:bold;">Ajustes Aplicados</span>`;

            return `
            <tr>
                <td class="fw-bold" style="font-size:0.75rem;">${escapeHTML(r.branch_name)}</td>
                <td style="color:var(--primary-blue); font-weight:bold;">${escapeHTML(r.sector_name)}</td>
                <td>${r.count_type === 'HOT' ? '🔥 En Caliente' : '❄️ En Frío'}</td>
                <td>${statusBadge}</td>
                <td><small class="text-muted">${new Date(r.created_at).toLocaleString()}</small></td>
                <td><small style="font-weight:bold;">${escapeHTML(r.assigned_operator || 'No asignado')}</small></td>
                <td>${actionBtn}</td>
            </tr>
            `;
        }).join('');
    } catch (e) {
        tbody.innerHTML = '<tr><td colspan="7" class="text-center text-danger">Error al cargar conteos.</td></tr>';
    }
}

async function openCreateInventoryModal() {
    document.getElementById('form-create-inventory').reset();
    openModal('modal-create-inventory');
    
    // Cargar Sucursales
    const branchSelect = document.getElementById('inv-session-branch');
    branchSelect.innerHTML = '<option value="">-- Seleccione Sucursal --</option>';
    if (typeof cachedBranches !== 'undefined') {
        cachedBranches.forEach(b => {
            branchSelect.innerHTML += `<option value="${b.id}">${escapeHTML(b.name)}</option>`;
        });
    }
    
    document.getElementById('inv-session-sector').innerHTML = '<option value="">-- Seleccione Sector --</option>';

    // Cargar Operadores activos
    const opSelect = document.getElementById('inv-session-operator');
    opSelect.innerHTML = '<option value="">-- Cargando operadores... --</option>';
    try {
        const users = await fetchAPI('/api/admin/users');
        opSelect.innerHTML = '<option value="">-- Seleccione Operador --</option>';
        if (users && users.length > 0) {
            users.filter(u => u.is_active).forEach(u => {
                opSelect.innerHTML += `<option value="${u.username}">${escapeHTML(u.full_name)} (${u.role})</option>`;
            });
        }
    } catch (e) {
        opSelect.innerHTML = '<option value="">-- Error al cargar --</option>';
    }
}

function onInvSessionBranchChange() {
    const branchId = document.getElementById('inv-session-branch').value;
    const sectorSelect = document.getElementById('inv-session-sector');
    sectorSelect.innerHTML = '<option value="">-- Seleccione Sector --</option>';
    
    if (typeof cachedSectors !== 'undefined') {
        cachedSectors.filter(s => s.branch_id === branchId).forEach(s => {
            sectorSelect.innerHTML += `<option value="${s.id}">${escapeHTML(s.name)}</option>`;
        });
    }
}

async function saveInventorySession(event) {
    event.preventDefault();
    const payload = {
        branch_id: document.getElementById('inv-session-branch').value,
        sector_id: document.getElementById('inv-session-sector').value,
        assigned_operator: document.getElementById('inv-session-operator').value,
        count_type: document.getElementById('inv-session-type').value
    };

    try {
        const btn = event.target.querySelector('button[type="submit"]');
        btn.disabled = true; btn.textContent = 'Tomando Snapshot...';
        
        await fetchAPI('/api/inventory/sessions', { method: 'POST', body: payload });
        
        showToast("Sesión creada y foto de inventario capturada.", "success");
        closeModal('modal-create-inventory');
        loadInventorySessions();
    } catch (e) {
        showToast(e.message, "error");
    } finally {
        const btn = event.target.querySelector('button[type="submit"]');
        btn.disabled = false; btn.textContent = 'Crear Sesión y Tomar Foto (Snapshot)';
    }
}

function openInventoryScan(sessionId) {
    document.getElementById('scan-inv-session-id').value = sessionId;
    document.getElementById('scan-inv-session-id-label').textContent = sessionId.split('-')[0].toUpperCase();
    document.getElementById('form-scan-inventory').reset();
    openModal('modal-scan-inventory');
    setTimeout(() => document.getElementById('scan-inv-sku').focus(), 300);
}

async function scanInventoryCount(event) {
    event.preventDefault();
    const sessionId = document.getElementById('scan-inv-session-id').value;
    const skuInput = document.getElementById('scan-inv-sku');
    
    const payload = {
        sku: skuInput.value.trim(),
        quantity: parseFloat(document.getElementById('scan-inv-qty').value),
        location_code: document.getElementById('scan-inv-loc').value.trim() || null,
        lot_number: isFefoEnabled ? document.getElementById('scan-inv-lot').value.trim() : ""
    };

    try {
        await fetchAPI(`/api/inventory/sessions/${sessionId}/scan`, { method: 'POST', body: payload });
        showToast(`Registrado: ${payload.quantity}x ${payload.sku}`, "success");
        
        skuInput.value = '';
        document.getElementById('scan-inv-qty').value = '';
        if(isFefoEnabled) document.getElementById('scan-inv-lot').value = '';
        skuInput.focus();
    } catch (e) {
        showToast(e.message, "error");
    }
}

async function finishInventorySession() {
    const sessionId = document.getElementById('scan-inv-session-id').value;
    if (!confirm("¿Está seguro de finalizar la etapa de escaneo? El conteo pasará a revisión del Administrador.")) return;
    
    try {
        await fetchAPI(`/api/inventory/sessions/${sessionId}/finish`, { method: 'POST' });
        showToast("Conteo enviado a revisión exitosamente.", "success");
        closeModal('modal-scan-inventory');
        loadInventorySessions();
    } catch (e) {}
}

async function openInventoryReview(sessionId) {
    document.getElementById('review-inv-session-id').value = sessionId;
    const tbody = document.getElementById('table-review-inventory-body');
    tbody.innerHTML = '<tr><td colspan="6" style="text-align:center; padding:2rem;">Calculando Deltas matemáticos...</td></tr>';
    openModal('modal-review-inventory');

    try {
        const deltas = await fetchAPI(`/api/inventory/sessions/${sessionId}/review`);
        
        if (!deltas || deltas.length === 0) {
            tbody.innerHTML = '<tr><td colspan="6" style="text-align:center; padding:2rem;">No hay discrepancias. El sector está perfecto.</td></tr>';
            return;
        }

        tbody.innerHTML = deltas.map(d => {
            const deltaVal = parseFloat(d.delta);
            let color = 'var(--text-main)';
            let sign = '';
            
            if (deltaVal > 0) { color = 'var(--success-green)'; sign = '+'; }
            if (deltaVal < 0) { color = 'var(--error-red)'; }

            return `
            <tr>
                <td style="font-weight:bold; color:var(--primary-blue);">${escapeHTML(d.sku)}</td>
                <td>${escapeHTML(d.location_code || 'N/A')}</td>
                <td class="lot-input" style="display:${isFefoEnabled ? 'table-cell' : 'none'};">${escapeHTML(d.lot_number || '-')}</td>
                <td style="text-align:center; background:#F8FAFC;">${d.expected_quantity}</td>
                <td style="text-align:center; font-weight:bold;">${d.counted_quantity}</td>
                <td style="text-align:center; font-weight:900; font-size:1.1rem; color:${color};">${sign}${deltaVal}</td>
            </tr>
            `;
        }).join('');
    } catch (e) {
        tbody.innerHTML = '<tr><td colspan="6" style="text-align:center; color:var(--error-red); padding:2rem;">Error al cruzar datos.</td></tr>';
    }
}

async function applyInventoryAdjustments() {
    const sessionId = document.getElementById('review-inv-session-id').value;
    if (!confirm("ESTA ACCIÓN ES CRÍTICA.\nEl sistema inyectará los Deltas (Ajustes) al stock en tiempo real. ¿Desea proceder?")) return;
    
    try {
        await fetchAPI(`/api/inventory/sessions/${sessionId}/apply`, { method: 'POST' });
        showToast("Deltas aplicados. El inventario ha sido cuadrado.", "success");
        closeModal('modal-review-inventory');
        loadInventorySessions();
    } catch (e) {}
}

// === AUDITORÍA RÁPIDA (SPOT CHECK) ===

function openSpotCheckModal() {
    document.getElementById('form-spot-check').reset();
    document.getElementById('spot-check-result').style.display = 'none';
    openModal('modal-spot-check');
    setTimeout(() => document.getElementById('spot-check-sku').focus(), 300);
}

async function runSpotCheck(event) {
    event.preventDefault();
    const skuInput = document.getElementById('spot-check-sku');
    const qtyInput = document.getElementById('spot-check-qty');
    
    const payload = {
        sku: skuInput.value.trim(),
        quantity: parseFloat(qtyInput.value),
        location_code: document.getElementById('spot-check-loc').value.trim() || null,
        lot_number: isFefoEnabled ? document.getElementById('spot-check-lot').value.trim() : ""
    };

    const resultDiv = document.getElementById('spot-check-result');

    try {
        const res = await fetchAPI('/api/inventory/spot-check', { method: 'POST', body: payload });

        resultDiv.style.display = 'block';
        if (res.match) {
            resultDiv.style.backgroundColor = '#D1FAE5';
            resultDiv.style.border = '2px solid #059669';
            resultDiv.innerHTML = `
                <h2 style="color:#065F46; margin:0; font-size:2rem; margin-bottom:8px;">✅ ¡PERFECTO!</h2>
                <p style="color:#065F46; font-size:1.1rem; margin:0;">El stock físico coincide con el sistema.</p>
                <p style="color:#047857; font-size:0.9rem; margin-top:4px;">Stock Confirmado: <strong>${res.expected} un</strong></p>
            `;
        } else {
            resultDiv.style.backgroundColor = '#FEE2E2';
            resultDiv.style.border = '2px solid #DC2626';
            let sign = res.delta > 0 ? '+' : '';
            let actionText = res.delta > 0 ? 'Sobrante' : 'Faltante';
            resultDiv.innerHTML = `
                <h2 style="color:#B91C1C; margin:0; font-size:2rem; margin-bottom:8px;">❌ DIFERENCIA</h2>
                <div style="display:flex; justify-content:center; gap:20px; color:#991B1B; font-size:1.1rem; margin-top:10px;">
                    <div><strong>Esperado:</strong><br>${res.expected}</div>
                    <div><strong>Contado:</strong><br>${res.counted}</div>
                </div>
                <div style="background:#FEF2F2; border:1px solid #F87171; border-radius:6px; padding:8px; margin-top:12px;">
                    <span style="font-size:1.1rem; font-weight:bold; color:#DC2626;">${actionText}: ${sign}${res.delta}</span>
                </div>
            `;
        }

        // Limpiar solo los campos críticos para seguir disparando el láser
        skuInput.value = '';
        qtyInput.value = '';
        skuInput.focus();

    } catch (e) {
        showToast(e.message, "error");
    }
}

// =========================================================================================
// === KARDEX (TRAZA DE ARTÍCULOS) MÁXIMA PERFORMANCE ======================================
// =========================================================================================

let kardexAutocompleteTimer = null;

function setupKardexAutocomplete() {
    const input = document.getElementById('kardex-filter-sku');
    if (!input || input.hasAttribute('data-autocomplete-setup')) return;
    input.setAttribute('data-autocomplete-setup', 'true');
    
    const wrapper = document.createElement('div');
    wrapper.style.position = 'relative';
    wrapper.style.display = 'flex';
    wrapper.style.width = '100%';
    input.parentNode.insertBefore(wrapper, input);
    wrapper.appendChild(input);
    
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

    input.addEventListener('input', (e) => {
        clearTimeout(kardexAutocompleteTimer);
        const query = e.target.value.trim();
        
        if (query.length < 2) {
            list.style.display = 'none';
            return;
        }
        
        kardexAutocompleteTimer = setTimeout(async () => {
            try {
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
        }, 350); 
    });

    document.addEventListener('click', (e) => {
        if (e.target !== input && !wrapper.contains(e.target)) {
            list.style.display = 'none';
        }
    });
}

function loadKardexSelectors() {
    setupKardexAutocomplete();
    
    const branchSelect = document.getElementById('kardex-filter-branch');
    if (branchSelect && typeof cachedBranches !== 'undefined') {
        branchSelect.innerHTML = '<option value="">-- Todas --</option>' + 
            cachedBranches.map(b => `<option value="${b.id}">${escapeHTML(b.name)}</option>`).join('');
    }
    onKardexBranchChange();
    
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

// === FUNCIONES DINÁMICAS (Líneas de Compras, Traspasos, Pedidos) ===

function addDynamicLinePO() {
    const list = document.getElementById('po-lines');
    const div = document.createElement('div');
    div.className = 'dynamic-row';
    div.innerHTML = `
        <input type="text" placeholder="SKU" class="po-sku" style="flex:2;" required>
        <input type="number" placeholder="Cantidad" class="po-qty" style="flex:1;" min="0.01" step="0.01" required>
        <button type="button" onclick="this.parentElement.remove()" style="background:var(--error-red); color:white; border:none; padding:4px 8px; border-radius:4px; font-weight:bold; cursor:pointer;">X</button>
    `;
    list.appendChild(div);
}

function addDynamicLineRemito() {
    const list = document.getElementById('rem-lines');
    const div = document.createElement('div');
    div.className = 'dynamic-row';
    div.innerHTML = `
        <input type="text" placeholder="SKU" class="rem-sku" style="flex:2;" required>
        <input type="number" placeholder="Cant" class="rem-qty" style="flex:1;" min="0.01" step="0.01" required>
        <input type="text" placeholder="Ubicación Destino" class="rem-loc" style="flex:1;">
        <input type="text" placeholder="Lote / Vto" class="rem-lot lot-input" style="flex:1; display:${isFefoEnabled ? 'block' : 'none'};">
        <button type="button" onclick="this.parentElement.remove()" style="background:var(--error-red); color:white; border:none; padding:4px 8px; border-radius:4px; font-weight:bold; cursor:pointer;">X</button>
    `;
    list.appendChild(div);
}

function addDynamicLineInvoice() {
    const list = document.getElementById('inv-lines');
    const div = document.createElement('div');
    div.className = 'dynamic-row';
    div.innerHTML = `
        <input type="text" placeholder="SKU" class="inv-sku" style="flex:2;">
        <input type="number" placeholder="Cantidad" class="inv-qty" style="flex:1;" min="0.01" step="0.01">
        <input type="number" placeholder="Precio Unitario" class="inv-price" style="flex:1;" min="0" step="0.01">
        <input type="text" placeholder="Lote / Vto" class="inv-lot lot-input" style="flex:1; display:${isFefoEnabled ? 'block' : 'none'};">
        <button type="button" onclick="this.parentElement.remove()" style="background:var(--error-red); color:white; border:none; padding:4px 8px; border-radius:4px; font-weight:bold; cursor:pointer;">X</button>
    `;
    list.appendChild(div);
}

function addDynamicLineTransfer() {
    const list = document.getElementById('tr-lines');
    const div = document.createElement('div');
    div.className = 'dynamic-row';
    div.innerHTML = `
        <input type="text" placeholder="SKU" class="tr-sku" style="flex:2;" required>
        <input type="number" placeholder="Cant" class="tr-qty" style="flex:1;" min="0.01" step="0.01" required>
        <input type="text" placeholder="Ubic. Origen" class="tr-orig-loc" style="flex:1;">
        <input type="text" placeholder="Ubic. Destino" class="tr-dest-loc" style="flex:1;">
        <input type="text" placeholder="Lote / Vto" class="tr-lot lot-input" style="flex:1; display:${isFefoEnabled ? 'block' : 'none'};">
        <button type="button" onclick="this.parentElement.remove()" style="background:var(--error-red); color:white; border:none; padding:4px 8px; border-radius:4px; font-weight:bold; cursor:pointer;">X</button>
    `;
    list.appendChild(div);
}

function addDynamicLineManualOrder() {
    const list = document.getElementById('manual-order-lines');
    const div = document.createElement('div');
    div.className = 'dynamic-row';
    div.innerHTML = `
        <input type="text" placeholder="SKU" class="manual-sku" style="flex:2;" required>
        <input type="number" placeholder="Cantidad" class="manual-qty" style="flex:1;" min="0.01" step="0.01" required>
        <input type="text" placeholder="Lote Extracción" class="manual-lot lot-input" style="flex:1; display:${isFefoEnabled ? 'block' : 'none'};">
        <button type="button" onclick="this.parentElement.remove()" style="background:var(--error-red); color:white; border:none; padding:4px 8px; border-radius:4px; font-weight:bold; cursor:pointer;">X</button>
    `;
    list.appendChild(div);
}

// Call settings to initialize feature flags on page load
document.addEventListener('DOMContentLoaded', () => {
    loadOperationsSettings();
});