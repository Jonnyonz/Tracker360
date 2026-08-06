let currentTargetInputId = null, videoStream = null, cameraInterval = null;
let AppConfig = {};

// PROTECCIÓN XSS
function escapeHTML(str) {
    if (str === null || str === undefined) return '';
    return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function showToast(msg, type = 'success') {
    const container = document.getElementById('toast-container'); if(!container) return;
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.innerHTML = `<span>${escapeHTML(msg)}</span><span style="cursor:pointer; font-weight:bold; margin-left:10px;" onclick="this.parentElement.remove()">&times;</span>`;
    container.appendChild(toast);
    setTimeout(() => { if(toast.parentElement) toast.remove(); }, 3500);
}

window.addEventListener('online', () => document.getElementById('net-banner').style.display = 'none');
window.addEventListener('offline', () => document.getElementById('net-banner').style.display = 'block');

// VERIFICACIÓN DE ROL SEGURA PARA AJUSTAR LA CUADRÍCULA INFERIOR
async function checkRoleAndShowDesktopBtn() {
    try {
        const r = await fetch('/api/admin/dashboard', { method: 'GET', headers: {'Content-Type': 'application/json'} });
        if (r.ok) {
            document.getElementById('btn-switch-desktop').style.display = 'block';
            document.getElementById('bottom-actions').style.gridTemplateColumns = '1fr 1fr';
        }
    } catch(e) {}
}

window.onload = async () => {
    checkRoleAndShowDesktopBtn();
    const data = await reqAPI('/api/settings');
    if(data) {
        AppConfig = data;
        if (AppConfig.enable_lots_expiration === 'true') {
            document.querySelectorAll('.lot-input').forEach(el => el.style.display = 'block');
        }
    }
    if(!navigator.onLine) document.getElementById('net-banner').style.display = 'block';
};

function handleScannerEnter(e, nextFieldId = null, formIdToSubmit = null) {
    if (e.key === 'Enter') {
        e.preventDefault();
        if (nextFieldId) {
            const nextField = document.getElementById(nextFieldId);
            if (nextField) { nextField.focus(); nextField.select(); }
        } else if (formIdToSubmit) {
            document.getElementById(formIdToSubmit).requestSubmit();
        }
    }
}

function goHome() { 
    stopCameraScanner(); 
    document.querySelectorAll('.view').forEach(v => v.classList.remove('active')); 
    document.getElementById('view-home').classList.add('active'); 
    document.getElementById('btn-back').style.display = 'none'; 
    document.getElementById('header-title').textContent = 'TERMINAL OPERATIVA';
}

function openView(id, cb = null) { 
    stopCameraScanner(); 
    document.querySelectorAll('.view').forEach(v => v.classList.remove('active')); 
    document.getElementById(id).classList.add('active'); 
    document.getElementById('btn-back').style.display = 'inline'; 
    if(cb) cb(); 
}

async function reqAPI(url, method = 'GET', body = null) {
    try {
        const r = await fetch(url, { method, headers: {'Content-Type': 'application/json'}, body: body ? JSON.stringify(body) : null });
        if (r.status === 401) return window.location.href = '/index.html';
        const d = await r.json().catch(() => ({}));
        if(!r.ok) { 
            const errMsg = d.detail || r.statusText || 'Error en el servidor';
            if(method !== 'GET') showToast(errMsg, 'error'); 
            return null; 
        }
        return d;
    } catch(e) { 
        if(method !== 'GET') showToast('Falla de red con el servidor', 'error'); 
        document.getElementById('net-banner').style.display = 'block';
        return null; 
    }
}

// CÁMARA
async function startCameraScanner(inputId) {
    currentTargetInputId = inputId; document.getElementById('camera-modal').style.display = 'flex';
    videoStream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { exact: "environment" } } }).catch(() => navigator.mediaDevices.getUserMedia({ video: true }));
    document.getElementById('camera-video').srcObject = videoStream;
    if ('BarcodeDetector' in window) {
        const bd = new BarcodeDetector();
        cameraInterval = setInterval(async () => {
            const codes = await bd.detect(document.getElementById('camera-video')).catch(()=>[]);
            if (codes.length > 0) { 
                if (navigator.vibrate) navigator.vibrate([100]); 
                document.getElementById(currentTargetInputId).value = codes[0].rawValue; 
                stopCameraScanner(); 
            }
        }, 250);
    } else {
        showToast("Tu navegador no soporta cámara.", "warning");
        stopCameraScanner();
    }
}
function stopCameraScanner() { clearInterval(cameraInterval); if(videoStream) videoStream.getTracks().forEach(t => t.stop()); document.getElementById('camera-modal').style.display = 'none'; }

// === 1. PICKING ===
async function loadPicking() {
    const list = document.getElementById('picking-list'); list.innerHTML = 'Consultando...';
    const data = await reqAPI('/api/picking/orders');
    if(!data) return;
    list.innerHTML = data.length ? data.map(o => `
        <div class="list-item" onclick="openPickingScan('${escapeHTML(o.document_number)}')">
            <strong>${escapeHTML(o.document_number)}</strong>
            <p>${escapeHTML(o.company_name)}</p>
        </div>
    `).join('') : 'Sin pedidos pendientes.';
}

async function openPickingScan(num) {
    document.getElementById('form-picking').reset(); 
    document.getElementById('pick-number').value = num; 
    document.getElementById('pick-title').textContent = num;
    openView('view-picking-scan');
    const d = await reqAPI('/api/picking/orders/' + encodeURIComponent(num));
    if(d) {
        document.getElementById('pick-details').innerHTML = d.lines.map(l => `
            <div style="border-bottom:1px solid #ddd; padding:5px 0;">
                <strong>${escapeHTML(l.sku)}</strong><br>
                Progreso: ${l.quantity_picked} / ${l.quantity_requested}<br>
                <small style="color:var(--success)">Sugerencias: ${escapeHTML(l.suggested_locations)}</small>
            </div>
        `).join('');
        setTimeout(() => document.getElementById('pick-loc').focus(), 100);
    }
}

async function scanPicking(e) {
    e.preventDefault();
    const num = document.getElementById('pick-number').value;
    const payload = { 
        location_code: document.getElementById('pick-loc').value.trim(), 
        sku: document.getElementById('pick-sku').value.trim(), 
        quantity: parseFloat(document.getElementById('pick-qty').value) 
    };
    const r = await reqAPI(`/api/picking/orders/${encodeURIComponent(num)}/scan`, 'POST', payload);
    if(r) { 
        showToast(r.message); 
        document.getElementById('pick-sku').value = ''; 
        r.order_completed ? goHome() : openPickingScan(num); 
    }
}

// === 2. PACKING ===
async function loadPacking() {
    const list = document.getElementById('packing-list'); list.innerHTML = 'Buscando pedidos...';
    const data = await reqAPI('/api/packing/orders');
    if(!data) return;
    list.innerHTML = data.length ? data.map(o => `
        <div class="list-item" style="border-left: 4px solid var(--success);" onclick="openPackingForm('${escapeHTML(o.document_number)}')">
            <strong>${escapeHTML(o.document_number)}</strong>
            <p>Destino: ${escapeHTML(o.company_name)}</p>
        </div>
    `).join('') : 'No hay pedidos esperando empaque.';
}

async function openPackingForm(num) {
    document.getElementById('form-packing').reset(); 
    document.getElementById('pack-number').value = num; 
    document.getElementById('pack-title').textContent = "EMPAQUE: " + num;
    
    const metricsDiv = document.getElementById('pack-metrics-summary');
    metricsDiv.style.display = 'none';
    openView('view-packing-form');
    
    const d = await reqAPI('/api/packing/orders/' + encodeURIComponent(num));
    if(d) {
        document.getElementById('pack-details').innerHTML = `Cliente: ${escapeHTML(d.document.company_name)}<br><small style="color:var(--text-muted)">Dir: ${escapeHTML(d.document.address)}</small>`;
        if (AppConfig.enable_item_dimensions === 'true') {
            metricsDiv.innerHTML = `Peso Calculado: ${d.totals.calc_weight} Kg | Vol: ${d.totals.calc_volume} m³`;
            metricsDiv.style.display = 'block';
        }
        setTimeout(() => document.getElementById('pack-boxes').focus(), 100);
    }
}

async function dispatchPacking(e) {
    e.preventDefault();
    const num = document.getElementById('pack-number').value;
    const payload = { boxes: parseInt(document.getElementById('pack-boxes').value) };
    const r = await reqAPI(`/api/packing/orders/${encodeURIComponent(num)}/pack`, 'POST', payload);
    if(r) { showToast(r.message); goHome(); }
}

// === 3. RECEPCIÓN ===
async function loadReceptions() {
    const list = document.getElementById('receptions-list'); list.innerHTML = 'Consultando remitos...';
    const data = await reqAPI('/api/reception/remitos');
    if(!data) return;
    list.innerHTML = data.length ? data.map(r => `
        <div class="list-item" style="border-left: 4px solid var(--accent-blue);" onclick="openReceptionScan('${escapeHTML(r.remito_number)}')">
            <strong>${escapeHTML(r.remito_number)}</strong>
            <p>Proveedor: ${escapeHTML(r.supplier_name)}</p>
            <p><small>${escapeHTML(r.branch_name)} > ${escapeHTML(r.sector_name)}</small> <span class="badge badge-warning">${escapeHTML(r.status)}</span></p>
        </div>
    `).join('') : 'No hay remitos pendientes de control.';
}

async function openReceptionScan(remitoNumber) {
    document.getElementById('form-reception').reset();
    document.getElementById('rec-number').value = remitoNumber;
    document.getElementById('rec-title').textContent = "REMITO: " + remitoNumber;
    openView('view-reception-scan');

    const d = await reqAPI('/api/reception/remitos/' + encodeURIComponent(remitoNumber));
    if(d && d.lines) {
        document.getElementById('rec-details').innerHTML = `
            <div style="margin-bottom:8px; font-weight:bold; color:var(--primary-blue);">Proveedor: ${escapeHTML(d.remito.supplier_name)}</div>
            ${d.lines.map(l => `
                <div style="border-bottom:1px solid #eee; padding:4px 0; font-size:0.85rem;">
                    <strong>${escapeHTML(l.sku)}</strong> - Controlado: ${l.quantity_received} / ${l.quantity_sent} un
                    ${l.location_code ? `<br><small style="color:var(--success)">Sugerencia: ${escapeHTML(l.location_code)}</small>` : ''}
                </div>
            `).join('')}
        `;
        setTimeout(() => document.getElementById('rec-sku').focus(), 100);
    }
}

async function scanReception(e) {
    e.preventDefault();
    const num = document.getElementById('rec-number').value;
    const payload = {
        remito_number: num,
        sku: document.getElementById('rec-sku').value.trim(),
        quantity: parseFloat(document.getElementById('rec-qty').value),
        location_code: document.getElementById('rec-loc').value.trim() || null
    };
    const r = await reqAPI(`/api/reception/remitos/${encodeURIComponent(num)}/scan`, 'POST', payload);
    if(r) { 
        showToast(r.message); 
        document.getElementById('rec-sku').value = '';
        r.remito_completed ? goHome() : openReceptionScan(num); 
    }
}

// === 4. TRASPASOS ===
async function loadTransfers() {
    const list = document.getElementById('transfers-list'); list.innerHTML = 'Consultando traspasos...';
    const data = await reqAPI('/api/transfers/orders');
    if(!data) return;
    list.innerHTML = data.length ? data.map(t => `
        <div class="list-item" style="border-left: 4px solid var(--warning);" onclick="openTransferScan('${escapeHTML(t.transfer_number)}')">
            <strong>${escapeHTML(t.transfer_number)}</strong>
            <p>Origen: ${escapeHTML(t.origin_branch)} (${escapeHTML(t.origin_sector)})</p>
            <p>Destino: ${escapeHTML(t.destination_branch)} (${escapeHTML(t.destination_sector)})</p>
            <p><span class="badge badge-warning">${escapeHTML(t.status)}</span></p>
        </div>
    `).join('') : 'No hay órdenes de traspaso activas.';
}

async function openTransferScan(transferNumber) {
    document.getElementById('form-transfer-scan').reset();
    document.getElementById('tr-number').value = transferNumber;
    document.getElementById('tr-title').textContent = "TRASPASO: " + transferNumber;
    openView('view-transfer-scan');

    const d = await reqAPI('/api/transfers/orders/' + encodeURIComponent(transferNumber));
    if(d && d.lines) {
        document.getElementById('tr-details').innerHTML = `
            <div style="margin-bottom:8px; font-weight:bold; color:var(--primary-blue);">${escapeHTML(d.transfer.origin_branch)} ➔ ${escapeHTML(d.transfer.destination_branch)}</div>
            ${d.lines.map(l => `
                <div style="border-bottom:1px solid #eee; padding:4px 0; font-size:0.85rem;">
                    <strong>${escapeHTML(l.sku)}</strong> - Transferido: ${l.quantity_received} / ${l.quantity_sent} un
                    <br><small style="color:var(--text-muted)">Origen: ${escapeHTML(l.origin_location || 'General')} ➔ Destino: ${escapeHTML(l.destination_location || 'General')}</small>
                </div>
            `).join('')}
        `;
        setTimeout(() => document.getElementById('tr-sku').focus(), 100);
    }
}

async function scanTransfer(e) {
    e.preventDefault();
    const num = document.getElementById('tr-number').value;
    const payload = {
        transfer_number: num,
        sku: document.getElementById('tr-sku').value.trim(),
        quantity: parseFloat(document.getElementById('tr-qty').value),
        destination_location_code: document.getElementById('tr-dest-loc').value.trim() || null
    };
    const r = await reqAPI(`/api/transfers/orders/${encodeURIComponent(num)}/scan`, 'POST', payload);
    if(r) { 
        showToast(r.message); 
        document.getElementById('tr-sku').value = '';
        r.transfer_completed ? goHome() : openTransferScan(num); 
    }
}

// === 5. INVENTARIO (CONTEOS CÍCLICOS) ===
async function loadInventory() {
    const list = document.getElementById('inventory-list'); list.innerHTML = 'Consultando conteos pendientes...';
    const data = await reqAPI('/api/inventory/sessions');
    if(!data) return;
    
    const openSessions = data.filter(s => s.status === 'OPEN');
    
    list.innerHTML = openSessions.length ? openSessions.map(s => `
        <div class="list-item" style="border-left: 4px solid #8B5CF6;" onclick="openInventoryScan('${escapeHTML(s.id)}')">
            <strong>${escapeHTML(s.sector_name)}</strong>
            <p>Sucursal: ${escapeHTML(s.branch_name)}</p>
            <p>Modalidad: ${s.count_type === 'HOT' ? 'En Caliente' : 'En Frío'}</p>
        </div>
    `).join('') : 'No hay conteos pendientes asignados.';
}

function openInventoryScan(sessionId) {
    document.getElementById('form-inventory-scan').reset();
    document.getElementById('inv-session-id').value = sessionId;
    document.getElementById('inv-session-id-label').textContent = sessionId.split('-')[0].toUpperCase();
    openView('view-inventory-scan');
    setTimeout(() => document.getElementById('inv-loc').focus(), 100);
}

async function scanInventoryCount(e) {
    e.preventDefault();
    const sessionId = document.getElementById('inv-session-id').value;
    const skuInput = document.getElementById('inv-sku');
    const lotInput = document.getElementById('inv-lot');
    
    const payload = {
        sku: skuInput.value.trim(),
        quantity: parseFloat(document.getElementById('inv-qty').value),
        location_code: document.getElementById('inv-loc').value.trim() || null,
        lot_number: AppConfig.enable_lots_expiration === 'true' ? (lotInput ? lotInput.value.trim() : "") : ""
    };
    
    const r = await reqAPI(`/api/inventory/sessions/${sessionId}/scan`, 'POST', payload);
    if(r) { 
        showToast(r.message); 
        skuInput.value = '';
        document.getElementById('inv-qty').value = '';
        if(lotInput) lotInput.value = '';
        skuInput.focus();
    }
}

async function finishInventorySession() {
    const sessionId = document.getElementById('inv-session-id').value;
    if (!confirm("¿Está seguro de finalizar el escaneo? El conteo pasará a revisión del supervisor.")) return;
    
    const r = await reqAPI(`/api/inventory/sessions/${sessionId}/finish`, 'POST');
    if(r) {
        showToast(r.message);
        goHome();
    }
}

// === 6. SPOT CHECK (AUDITORÍA RÁPIDA) ===
function openSpotCheck() {
    document.getElementById('form-spot-check').reset();
    document.getElementById('spot-check-result').style.display = 'none';
    openView('view-spot-check');
    setTimeout(() => document.getElementById('spot-check-loc').focus(), 100);
}

async function runSpotCheck(e) {
    e.preventDefault();
    const skuInput = document.getElementById('spot-check-sku');
    const lotInput = document.getElementById('spot-check-lot');
    
    const payload = {
        sku: skuInput.value.trim(),
        quantity: parseFloat(document.getElementById('spot-check-qty').value),
        location_code: document.getElementById('spot-check-loc').value.trim() || null,
        lot_number: AppConfig.enable_lots_expiration === 'true' ? (lotInput ? lotInput.value.trim() : "") : ""
    };
    
    const resultDiv = document.getElementById('spot-check-result');

    const res = await reqAPI('/api/inventory/spot-check', 'POST', payload);
    if(res) {
        resultDiv.style.display = 'block';
        if (res.match) {
            resultDiv.style.backgroundColor = '#D1FAE5';
            resultDiv.style.border = '2px solid #059669';
            resultDiv.innerHTML = `
                <h2 style="color:#065F46; margin:0; font-size:1.5rem; margin-bottom:8px;">COINCIDENCIA EXACTA</h2>
                <p style="color:#065F46; font-size:1rem; margin:0;">El stock físico es correcto.</p>
                <p style="color:#047857; font-size:0.9rem; margin-top:4px;">Stock Confirmado: <strong>${res.expected} un</strong></p>
            `;
        } else {
            resultDiv.style.backgroundColor = '#FEE2E2';
            resultDiv.style.border = '2px solid #DC2626';
            let sign = res.delta > 0 ? '+' : '';
            let actionText = res.delta > 0 ? 'Sobrante' : 'Faltante';
            resultDiv.innerHTML = `
                <h2 style="color:#B91C1C; margin:0; font-size:1.5rem; margin-bottom:8px;">DIFERENCIA DETECTADA</h2>
                <div style="display:flex; justify-content:center; gap:20px; color:#991B1B; font-size:1rem; margin-top:10px;">
                    <div><strong>Esperado:</strong><br>${res.expected}</div>
                    <div><strong>Contado:</strong><br>${res.counted}</div>
                </div>
                <div style="background:#FEF2F2; border:1px solid #F87171; border-radius:6px; padding:8px; margin-top:12px;">
                    <span style="font-size:1rem; font-weight:bold; color:#DC2626;">${actionText}: ${sign}${res.delta}</span>
                </div>
            `;
        }

        // Limpiar inputs críticos para continuar escaneando rápido
        skuInput.value = '';
        document.getElementById('spot-check-qty').value = '';
        skuInput.focus();
    }
}

async function logout() { await reqAPI('/api/auth/logout', 'POST'); window.location.href = '/index.html'; }