// === MÓDULO DE OPERACIONES, STOCK, REPORTES E INTEGRACIONES (DESKTOP / ADMIN) ===

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

// Generación y copia de Clave API
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
        showToast("No se pudo copiar automáticamente.", "error");
    }
}

window.loadNextTransferNumber = loadNextTransferNumber;
window.switchPurchaseTab = switchPurchaseTab;
window.onTrOrigBranchChange = onTrOrigBranchChange;
window.onTrDestBranchChange = onTrDestBranchChange;
window.saveTransfer = saveTransfer;
window.generateApiKey = generateApiKey;
window.copyApiKeyToClipboard = copyApiKeyToClipboard;