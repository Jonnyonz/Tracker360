// === MÓDULO DE OPERACIONES, STOCK, REPORTES E INVENTARIOS CÍCLICOS ===

let isFefoEnabled = false; // Variable global del sistema para Lotes/Vencimientos

// =========================================================================================
// === CAMBIO DE SUBPESTAÑAS EN COMPRAS / TRASPASOS ========================================
// =========================================================================================

function switchPurchaseTab(tabId, btn) {
    document.querySelectorAll('.purchase-tab').forEach(t => t.style.display = 'none');
    document.querySelectorAll('.purchase-subtab-btn').forEach(b => b.classList.remove('active'));
    
    const target = document.getElementById(tabId);
    if (target) target.style.display = 'block';
    if (btn) btn.classList.add('active');

    if (tabId === 'tab-transfer') {
        loadNextTransferNumber();
        loadTransferSelectors();
        loadTransferData();
    }
}

// =========================================================================================
// === AUTOGENERACIÓN Y CORRELATIVO DE NUMERO DE TRASPASO (ODT) ============================
// =========================================================================================

async function loadNextTransferNumber() {
    const trInput = document.getElementById('tr-num');
    if (!trInput) return;
    
    trInput.readOnly = true;
    trInput.style.backgroundColor = '#F3F4F6';
    trInput.style.fontWeight = 'bold';
    trInput.style.color = 'var(--primary-blue)';
    trInput.value = 'Cargando...';

    try {
        const data = await fetchAPI('/api/admin/transfer-orders/next-number');
        if (data && data.next_number) {
            trInput.value = data.next_number;
        } else {
            trInput.value = 'TR-000001';
        }
    } catch (e) {
        console.error("Error al obtener número de traspaso:", e);
        trInput.value = 'TR-000001';
    }
}

function loadTransferSelectors() {
    const origBranch = document.getElementById('tr-orig-branch');
    const destBranch = document.getElementById('tr-dest-branch');
    if (!origBranch || !destBranch) return;

    origBranch.innerHTML = '<option value="">-- Seleccionar --</option>';
    destBranch.innerHTML = '<option value="">-- Seleccionar --</option>';

    if (typeof cachedBranches !== 'undefined' && cachedBranches.length > 0) {
        cachedBranches.forEach(b => {
            origBranch.innerHTML += `<option value="${b.id}">${escapeHTML(b.name)}</option>`;
            destBranch.innerHTML += `<option value="${b.id}">${escapeHTML(b.name)}</option>`;
        });
    }
}

function onTrOrigBranchChange() {
    const branchId = document.getElementById('tr-orig-branch').value;
    const sectorSelect = document.getElementById('tr-orig-sector');
    if (!sectorSelect) return;
    
    sectorSelect.innerHTML = '<option value="">-- Seleccionar --</option>';
    if (typeof cachedSectors !== 'undefined') {
        cachedSectors.filter(s => s.branch_id === branchId).forEach(s => {
            sectorSelect.innerHTML += `<option value="${s.id}">${escapeHTML(s.name)}</option>`;
        });
    }
}

function onTrDestBranchChange() {
    const branchId = document.getElementById('tr-dest-branch').value;
    const sectorSelect = document.getElementById('tr-dest-sector');
    if (!sectorSelect) return;
    
    sectorSelect.innerHTML = '<option value="">-- Seleccionar --</option>';
    if (typeof cachedSectors !== 'undefined') {
        cachedSectors.filter(s => s.branch_id === branchId).forEach(s => {
            sectorSelect.innerHTML += `<option value="${s.id}">${escapeHTML(s.name)}</option>`;
        });
    }
}

async function saveTransfer(event) {
    if (event) event.preventDefault();
    
    const num = document.getElementById('tr-num').value.trim();
    const origBranch = document.getElementById('tr-orig-branch').value;
    const origSector = document.getElementById('tr-orig-sector').value;
    const destBranch = document.getElementById('tr-dest-branch').value;
    const destSector = document.getElementById('tr-dest-sector').value;

    if (!origBranch || !origSector || !destBranch || !destSector) {
        alert("Por favor complete sucursales y sectores de origen y destino.");
        return;
    }

    const rows = document.querySelectorAll('#tr-lines .dynamic-row');
    const lines = [];

    rows.forEach(r => {
        const sku = r.querySelector('.tr-sku')?.value.trim();
        const qty = parseFloat(r.querySelector('.tr-qty')?.value);
        const origLoc = r.querySelector('.tr-orig-loc')?.value.trim();
        const destLoc = r.querySelector('.tr-dest-loc')?.value.trim();
        const lot = r.querySelector('.tr-lot')?.value.trim();

        if (sku && !isNaN(qty) && qty > 0) {
            lines.push({
                sku: sku.toUpperCase(),
                quantity: qty,
                origin_location_code: origLoc || null,
                destination_location_code: destLoc || null,
                lot_number: lot || ""
            });
        }
    });

    if (lines.length === 0) {
        alert("Ingrese al menos un artículo válido.");
        return;
    }

    const payload = {
        transfer_number: num,
        origin_branch_id: origBranch,
        origin_sector_id: origSector,
        destination_branch_id: destBranch,
        destination_sector_id: destSector,
        lines: lines
    };

    const btn = event.target.querySelector('button[type="submit"]');
    if (btn) { btn.disabled = true; btn.textContent = 'Procesando...'; }

    try {
        await fetchAPI('/api/admin/transfer-orders', { method: 'POST', body: payload });
        showToast("Orden de Traspaso (ODT) generada correctamente.", "success");
        
        document.getElementById('form-transfer').reset();
        document.getElementById('tr-lines').innerHTML = `
            <div class="dynamic-row">
                <input type="text" placeholder="SKU" class="tr-sku" style="flex:2;" required>
                <input type="number" placeholder="Cant" class="tr-qty" style="flex:1;" min="0.01" step="0.01" required>
                <input type="text" placeholder="Origen" class="tr-orig-loc" style="flex:1;">
                <input type="text" placeholder="Destino" class="tr-dest-loc" style="flex:1;">
                <input type="text" placeholder="Lote / Vto" class="tr-lot lot-input" style="flex:1; display:${isFefoEnabled ? 'block' : 'none'};">
                <button type="button" onclick="this.parentElement.remove()" style="background:var(--error-red); color:white; border:none; padding:4px 8px; border-radius:4px; font-weight:bold; cursor:pointer;">X</button>
            </div>
        `;
        
        await loadNextTransferNumber();
        loadTransferData();
    } catch (e) {
        console.error(e);
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = 'Procesar Traspaso'; }
    }
}

async function loadTransferData(search = "", limit = 50) {
    const tbody = document.getElementById('table-transfers-body');
    if (!tbody) return;

    try {
        const rows = await fetchAPI(`/api/admin/transfer-orders?search=${encodeURIComponent(search)}&limit=${limit}`);
        if (!rows || rows.length === 0) {
            tbody.innerHTML = '<tr><td colspan="4" style="text-align:center; color:var(--text-muted); padding:1rem;">Sin traspasos registrados.</td></tr>';
            return;
        }

        tbody.innerHTML = rows.map(r => `
            <tr>
                <td style="color:var(--primary-blue); font-weight:bold;">${escapeHTML(r.transfer_number)}</td>
                <td><small>${escapeHTML(r.origin_branch)} (${escapeHTML(r.origin_sector)})</small></td>
                <td><small>${escapeHTML(r.destination_branch)} (${escapeHTML(r.destination_sector)})</small></td>
                <td><span class="badge badge-warning">${escapeHTML(r.status)}</span></td>
            </tr>
        `).join('');
    } catch (e) {
        tbody.innerHTML = '<tr><td colspan="4" style="text-align:center; color:var(--error-red);">Error al cargar historial.</td></tr>';
    }
}

// =========================================================================================
// === GENERACIÓN Y COPIA SEGURA DE CLAVE API (MODAL DE VISTA ÚNICA) =======================
// =========================================================================================

async function generateApiKey() {
    if (!confirm("ATENCIÓN: ¿Desea generar una nueva Clave API Maestra? La clave anterior dejará de funcionar de inmediato.")) return;
    
    try {
        const res = await fetchAPI('/api/admin/settings/generate-key', { method: 'POST' });
        if (res && (res.new_key || res.api_key || res.key)) {
            const keyVal = res.new_key || res.api_key || res.key;
            const keyInput = document.getElementById('display-generated-api-key');
            if (keyInput) keyInput.value = keyVal;
            if (typeof openModal === 'function') openModal('modal-show-api-key');
        } else {
            showToast("Clave API generada correctamente.", "success");
        }
    } catch (e) {
        showToast("Error al generar clave API: " + e.message, "error");
    }
}

function copyApiKeyToClipboard() {
    const keyInput = document.getElementById('display-generated-api-key');
    if (!keyInput || !keyInput.value) return;
    
    if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(keyInput.value).then(() => {
            showToast("Clave API copiada al portapapeles.", "success");
        }).catch(() => {
            fallbackCopyText(keyInput);
        });
    } else {
        fallbackCopyText(keyInput);
    }
}

function fallbackCopyText(inputEl) {
    inputEl.select();
    inputEl.setSelectionRange(0, 99999);
    try {
        document.execCommand('copy');
        showToast("Clave API copiada al portapapeles.", "success");
    } catch (err) {
        showToast("No se pudo copiar automáticamente. Seleccione y copie manualmente.", "error");
    }
}

// =========================================================================================
// === FUNCIONES LEGACY (DASHBOARD E INTEGRACIONES) =========================================
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
    loadNextTransferNumber();
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

async function loadIntegrations() {
    const tbody = document.getElementById('integrationsTableBody');
    if (!tbody) return;

    try {
        const channels = await fetchAPI('/api/admin/integrations');
        if (!channels || channels.length === 0) {
            tbody.innerHTML = '<tr><td colspan="4" style="text-align:center; padding:1rem; color:var(--text-muted);">Sin conexiones configuradas.</td></tr>';
            return;
        }
        tbody.innerHTML = channels.map(c => `
            <tr>
                <td class="fw-bold">${escapeHTML(c.name)}</td>
                <td><span class="badge badge-neutral">${escapeHTML(c.channel_type)}</span></td>
                <td><small class="text-truncate d-inline-block" style="max-width: 250px;">${escapeHTML(c.target_url)}</small></td>
                <td style="text-align:right;">
                    <button class="btn-secondary" style="color:var(--error-red); border-color:var(--error-red); padding:3px 8px; font-size:0.75rem;" onclick="deleteIntegration('${c.id}')">Eliminar</button>
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

// Exponer funciones globales
window.loadNextTransferNumber = loadNextTransferNumber;
window.switchPurchaseTab = switchPurchaseTab;
window.onTrOrigBranchChange = onTrOrigBranchChange;
window.onTrDestBranchChange = onTrDestBranchChange;
window.saveTransfer = saveTransfer;
window.generateApiKey = generateApiKey;
window.copyApiKeyToClipboard = copyApiKeyToClipboard;