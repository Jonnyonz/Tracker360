// === MÓDULO DE OPERACIONES, STOCK, REPORTES E INTEGRACIONES (DESKTOP / ADMIN) ===

// Caché local y aislado exclusiva para este módulo (Cero colisiones)
let opsSectorsCache = [];
let opsOrdersCache = [];

let packingCurrentOrder = null;
let packingExpectedItems = {};
let packingScannedItems = {};
let packingBoxes = [];
let currentBoxIndex = 1;

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

// === GESTIÓN DE PEDIDOS Y FILTROS ===

async function loadOrders() {
    const tbody = document.getElementById('table-orders-body');
    if(!tbody) return;
    tbody.innerHTML = '<tr><td colspan="5" style="text-align:center; padding:2rem; color:var(--text-muted);">Cargando pedidos...</td></tr>';
    
    try {
        const res = await fetchAPI('/api/admin/documents');
        if(!res || res.length === 0) {
            opsOrdersCache = [];
            tbody.innerHTML = '<tr><td colspan="5" style="text-align:center; color:var(--text-muted);">No hay pedidos registrados.</td></tr>';
            return;
        }
        opsOrdersCache = res;
        filterOrders();
    } catch (e) {
        tbody.innerHTML = `<tr><td colspan="5" style="text-align:center; color:var(--danger);">Error: ${escapeHTML(e.message)}</td></tr>`;
    }
}

function filterOrders() {
    const tbody = document.getElementById('table-orders-body');
    if(!tbody) return;
    
    const searchText = (document.getElementById('search-order-text')?.value || '').toLowerCase();
    const statusFilter = document.getElementById('search-order-status')?.value || 'ALL';

    const filtered = opsOrdersCache.filter(o => {
        const matchText = o.document_number.toLowerCase().includes(searchText) || 
                          (o.company_name && o.company_name.toLowerCase().includes(searchText));
        const matchStatus = statusFilter === 'ALL' || o.status === statusFilter;
        return matchText && matchStatus;
    });

    if (filtered.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" style="text-align:center; color:var(--text-muted);">No hay pedidos que coincidan con la búsqueda.</td></tr>';
        return;
    }

    tbody.innerHTML = filtered.map(o => {
        let badgeClass = 'badge-neutral';
        if (o.status === 'PENDING') badgeClass = 'badge-warning';
        if (o.status === 'IN_PROGRESS') badgeClass = 'badge-info';
        if (o.status === 'COMPLETED') badgeClass = 'badge-success';
        
        let actionBtn = `<span style="color:var(--text-muted); font-size:0.8rem;">Sin Acción</span>`;
        if (o.status === 'COMPLETED') {
            actionBtn = `<button class="btn-submit" style="padding:4px 10px; font-size:0.75rem; background:var(--accent);" onclick="openPackingStation('${escapeHTML(o.document_number)}', '${escapeHTML(o.company_name)}')">Empacar (Verificar)</button>`;
        } else if (o.status === 'PENDING' || o.status === 'IN_PROGRESS') {
            actionBtn = `<span style="color:var(--text-secondary); font-size:0.8rem;">En Picking</span>`;
        } else if (o.status === 'DISPATCHED') {
            actionBtn = `<button class="btn-secondary" style="padding:4px 10px; font-size:0.75rem;" onclick="reprintOrderLabel('${escapeHTML(o.document_number)}')">Re-imprimir</button>`;
        }

        return `<tr>
            <td style="font-weight:bold; color:var(--accent); font-family:monospace;">${escapeHTML(o.document_number)}</td>
            <td>${escapeHTML(o.company_name)}</td>
            <td><span class="badge ${badgeClass}">${escapeHTML(o.status)}</span></td>
            <td>
                <div style="width:100%; background:var(--border); border-radius:4px; height:8px; overflow:hidden;">
                    <div style="width:${o.progress_pct}%; background:${o.progress_pct === 100 ? 'var(--success)' : 'var(--accent)'}; height:100%;"></div>
                </div>
                <small style="display:block; text-align:right; margin-top:2px; font-weight:bold; color:var(--text-secondary);">${o.progress_pct}%</small>
            </td>
            <td style="text-align:right;">${actionBtn}</td>
        </tr>`;
    }).join('');
}

function openManualOrderModal() {
    document.getElementById('form-manual-order').reset();
    document.getElementById('manual-order-lines').innerHTML = '';
    addDynamicLineManualOrder();
    
    fetchAPI('/api/admin/sales-orders/next-number').then(res => {
        if(res && res.next_number) document.getElementById('manual-doc-num').value = res.next_number;
    });
    
    openModal('modal-manual-order');
}

function addDynamicLineManualOrder() {
    document.getElementById('manual-order-lines').innerHTML += `
        <div class="dynamic-row">
            <input type="text" placeholder="SKU" class="manual-sku font-mono" style="flex:2;" required>
            <input type="number" placeholder="Cantidad" class="manual-qty" style="flex:1;" min="0.01" step="0.01" required>
            <input type="text" placeholder="Lote" class="manual-lot lot-input" style="flex:1; display:none;">
            <button type="button" onclick="this.parentElement.remove()" class="btn-danger">X</button>
        </div>`;
}

async function saveManualOrder(e) {
    e.preventDefault();
    const docNum = document.getElementById('manual-doc-num').value;
    const taxId = document.getElementById('manual-cust-taxid').value;
    const custName = document.getElementById('manual-cust-name').value;
    const addrLabel = document.getElementById('manual-addr-label').value;

    const rows = document.querySelectorAll('#manual-order-lines .dynamic-row');
    const lines = [];
    rows.forEach(r => {
        const sku = r.querySelector('.manual-sku').value;
        const qty = parseFloat(r.querySelector('.manual-qty').value);
        if (sku && qty > 0) {
            lines.push({ sku: sku.toUpperCase(), quantity: qty, serial_numbers: [] });
        }
    });

    if (lines.length === 0) return showToast("Añade al menos un artículo", "error");

    const payload = { document_number: docNum, customer_tax_id: taxId, customer_name: custName, address_label: addrLabel, lines: lines };

    const btn = e.target.querySelector('button[type="submit"]');
    btn.disabled = true; btn.textContent = 'Guardando...';

    try {
        const r = await fetchAPI('/api/admin/sales-orders', { method: 'POST', body: JSON.stringify(payload) });
        showToast(r.message, "success");
        closeModal('modal-manual-order');
        loadOrders();
    } catch(err) {
        showToast(err.message, "error");
    } finally {
        btn.disabled = false; btn.textContent = 'Registrar Pedido';
    }
}

async function reprintOrderLabel(docNum) {
    try {
        const r = await fetchAPI(`/api/admin/sales-orders/${encodeURIComponent(docNum)}/print-label`, { method: 'POST' });
        showToast(r.message, "success");
    } catch(err) {
        showToast(err.message, "error");
    }
}

// === FASE 4: ESTACIÓN DE EMPAQUE (PACKING STATION) ===

async function openPackingStation(documentNumber, clientName) {
    packingCurrentOrder = documentNumber;
    packingExpectedItems = {};
    packingScannedItems = {};
    packingBoxes = [];
    currentBoxIndex = 1;

    document.getElementById('pack-order-label').textContent = documentNumber;
    document.getElementById('pack-customer-label').textContent = clientName || 'Consumidor Final';
    document.getElementById('pack-current-box-label').textContent = `Caja ${currentBoxIndex}`;
    document.getElementById('pack-scan-sku').value = '';
    document.getElementById('pack-current-box-items').innerHTML = '<p style="color:var(--text-muted); font-style:italic;">Caja vacía. Escanee artículos.</p>';
    document.getElementById('btn-dispatch-packing').disabled = true;
    document.getElementById('btn-dispatch-packing').style.background = 'var(--text-muted)';
    document.getElementById('btn-dispatch-packing').textContent = 'Verificación Incompleta...';

    const tbody = document.getElementById('table-packing-lines');
    tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;">Cargando lista de picking...</td></tr>';
    openModal('modal-packing-station');

    try {
        const res = await fetchAPI(`/api/packing/orders/${encodeURIComponent(documentNumber)}`);
        if (!res || !res.lines || res.lines.length === 0) {
            tbody.innerHTML = '<tr><td colspan="5" style="text-align:center; color:var(--danger);">Error: No hay líneas pickeadas.</td></tr>';
            return;
        }

        res.lines.forEach(l => {
            const sku = l.sku.toUpperCase();
            packingExpectedItems[sku] = {
                desc: l.description,
                expected: l.quantity,
                scanned: 0
            };
        });

        renderPackingVerificationTable();
        
        setTimeout(() => {
            const inp = document.getElementById('pack-scan-sku');
            if (inp) { inp.focus(); inp.select(); }
        }, 500);

    } catch (e) {
        tbody.innerHTML = `<tr><td colspan="5" style="text-align:center; color:var(--danger);">Error: ${escapeHTML(e.message)}</td></tr>`;
    }
}

function processPackingScan(e) {
    e.preventDefault();
    const input = document.getElementById('pack-scan-sku');
    let sku = input.value.trim().toUpperCase();
    if (!sku) return;

    // Si el usuario usa pistola con sufijo de cantidad ej. "MOUSE-01*2"
    let qty = 1;
    if (sku.includes('*')) {
        const parts = sku.split('*');
        sku = parts[0];
        qty = parseFloat(parts[1]) || 1;
    }

    input.value = '';

    if (!packingExpectedItems[sku]) {
        if (typeof playErrorTone === 'function') playErrorTone();
        showToast(`CUIDADO: El SKU ${sku} no pertenece a este pedido.`, 'error');
        return;
    }

    const item = packingExpectedItems[sku];
    if (item.scanned + qty > item.expected) {
        if (typeof playErrorTone === 'function') playErrorTone();
        showToast(`ALERTA: Intenta empacar ${item.scanned + qty} de ${item.expected} permitidos para ${sku}.`, 'error');
        return;
    }

    // Registro global
    item.scanned += qty;

    // Registro por caja actual
    const currentBoxKey = `box_${currentBoxIndex}`;
    if (!packingScannedItems[currentBoxKey]) packingScannedItems[currentBoxKey] = {};
    if (!packingScannedItems[currentBoxKey][sku]) packingScannedItems[currentBoxKey][sku] = 0;
    packingScannedItems[currentBoxKey][sku] += qty;

    if (typeof playSuccessChime === 'function') playSuccessChime();
    renderPackingVerificationTable();
    renderCurrentBoxItems();
    checkPackingCompletion();
}

function renderPackingVerificationTable() {
    const tbody = document.getElementById('table-packing-lines');
    let html = '';
    
    for (const [sku, data] of Object.entries(packingExpectedItems)) {
        const isComplete = data.scanned === data.expected;
        const colorClass = isComplete ? 'color:var(--success);' : (data.scanned > 0 ? 'color:var(--warning);' : 'color:var(--text-primary);');
        const bgClass = isComplete ? 'background:rgba(0, 200, 83, 0.05);' : '';
        const badge = isComplete ? '<span class="badge badge-success">OK</span>' : '<span class="badge badge-neutral">Falta</span>';

        html += `
            <tr style="${bgClass}">
                <td style="font-family:monospace; font-weight:bold; ${colorClass}">${escapeHTML(sku)}</td>
                <td style="font-size:0.85rem;">${escapeHTML(data.desc)}</td>
                <td style="text-align:center; font-weight:bold;">${data.expected}</td>
                <td style="text-align:center; font-weight:bold; ${colorClass}">${data.scanned}</td>
                <td style="text-align:right;">${badge}</td>
            </tr>
        `;
    }
    tbody.innerHTML = html;
}

function renderCurrentBoxItems() {
    const container = document.getElementById('pack-current-box-items');
    const currentBoxKey = `box_${currentBoxIndex}`;
    const items = packingScannedItems[currentBoxKey];

    if (!items || Object.keys(items).length === 0) {
        container.innerHTML = '<p style="color:var(--text-muted); font-style:italic;">Caja vacía. Escanee artículos.</p>';
        return;
    }

    let html = '<ul style="list-style:none; padding:0; margin:0; display:flex; flex-direction:column; gap:6px;">';
    for (const [sku, qty] of Object.entries(items)) {
        html += `
            <li style="display:flex; justify-content:space-between; border-bottom:1px solid var(--border); padding-bottom:4px;">
                <span class="font-mono" style="color:var(--accent); font-weight:bold;">${escapeHTML(sku)}</span>
                <span style="font-weight:bold;">x${qty}</span>
            </li>
        `;
    }
    html += '</ul>';
    container.innerHTML = html;
}

function closeCurrentBox() {
    const currentBoxKey = `box_${currentBoxIndex}`;
    const items = packingScannedItems[currentBoxKey];

    if (!items || Object.keys(items).length === 0) {
        showToast("No puede sellar una caja vacía.", "warning");
        return;
    }

    packingBoxes.push({
        box_number: currentBoxIndex,
        items: { ...items }
    });

    currentBoxIndex++;
    document.getElementById('pack-current-box-label').textContent = `Caja ${currentBoxIndex}`;
    document.getElementById('pack-current-box-items').innerHTML = '<p style="color:var(--text-muted); font-style:italic;">Caja vacía. Escanee artículos.</p>';
    showToast(`Caja ${currentBoxIndex - 1} sellada.`, "info");
    
    document.getElementById('pack-scan-sku').focus();
}

function checkPackingCompletion() {
    let allComplete = true;
    for (const data of Object.values(packingExpectedItems)) {
        if (data.scanned < data.expected) {
            allComplete = false;
            break;
        }
    }

    const btn = document.getElementById('btn-dispatch-packing');
    if (allComplete) {
        btn.disabled = false;
        btn.style.background = 'var(--success)';
        btn.innerHTML = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"></polyline></svg> Despachar e Imprimir Etiquetas';
        showToast("Verificación 100% completada. Listo para despachar.", "success");
    } else {
        btn.disabled = true;
        btn.style.background = 'var(--text-muted)';
        btn.textContent = 'Verificación Incompleta...';
    }
}

async function submitPackingStation() {
    // Si hay items en la caja actual que no se selló, la cerramos automáticamente.
    const currentBoxKey = `box_${currentBoxIndex}`;
    if (packingScannedItems[currentBoxKey] && Object.keys(packingScannedItems[currentBoxKey]).length > 0) {
        closeCurrentBox();
    }

    const payload = {
        boxes: packingBoxes.length,
        packed_items: []
    };

    packingBoxes.forEach(box => {
        for (const [sku, qty] of Object.entries(box.items)) {
            payload.packed_items.push({
                sku: sku,
                quantity: qty,
                box_number: box.box_number
            });
        }
    });

    const btn = document.getElementById('btn-dispatch-packing');
    btn.disabled = true;
    btn.textContent = 'Procesando Despacho...';

    try {
        const res = await fetchAPI(`/api/packing/orders/${encodeURIComponent(packingCurrentOrder)}/pack`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload)
        });
        
        showToast(res.message, "success");
        closeModal('modal-packing-station');
        loadOrders();
    } catch (e) {
        showToast(e.message, "error");
        btn.disabled = false;
        btn.innerHTML = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"></polyline></svg> Reintentar Despacho';
    }
}

// === PUTAWAY Y REPLENISHMENT FASE 2 ===
async function fetchPutawaySuggestion(sku, inputElement) {
    if (!sku || !inputElement) return;
    try {
        const res = await fetchAPI(`/api/admin/putaway/${encodeURIComponent(sku)}`);
        if (res && res.suggested_location) {
            inputElement.value = res.suggested_location;
            if (res.type === 'FIXED_LOCATION') {
                inputElement.style.border = '2px solid var(--accent)';
                if (typeof showToast === 'function') showToast(`Ubicación fija sugerida: ${res.suggested_location}`, 'info');
            } else if (res.type === 'EXISTING_STOCK') {
                inputElement.style.border = '2px solid var(--warning)';
                if (typeof showToast === 'function') showToast(`Sugerencia (Agrupación de stock): ${res.suggested_location}`, 'warning');
            }
        }
    } catch (e) { console.error("Error cargando sugerencia Putaway", e); }
}

async function loadReplenishmentSuggestions() {
    const tbody = document.getElementById('table-replenishment-body');
    if (!tbody) return;
    tbody.innerHTML = '<tr><td colspan="7" style="text-align:center; padding:1.5rem; color:var(--text-muted);">Buscando sugerencias...</td></tr>';
    
    try {
        const res = await fetchAPI('/api/admin/replenishment-suggestions');
        if (res.status === 'disabled') {
            tbody.innerHTML = '<tr><td colspan="7" style="text-align:center; padding:1.5rem; color:var(--text-muted);">El módulo de Reabastecimiento Automático está desactivado en Configuración.</td></tr>';
            return;
        }
        if (!res.suggestions || res.suggestions.length === 0) {
            tbody.innerHTML = '<tr><td colspan="7" style="text-align:center; padding:1.5rem; color:var(--success);">Las áreas de picking están completamente abastecidas.</td></tr>';
            return;
        }
        
        tbody.innerHTML = res.suggestions.map(s => `
            <tr>
                <td style="font-weight:bold; color:var(--accent);">${escapeHTML(s.sku)}</td>
                <td>${escapeHTML(s.description)}</td>
                <td style="color:var(--danger); font-weight:bold;">${s.stock_picking}</td>
                <td style="color:var(--success); font-weight:bold;">${s.stock_pulmon}</td>
                <td><code class="font-mono">${escapeHTML(s.origin_location || 'N/A')}</code></td>
                <td><code class="font-mono">${escapeHTML(s.destination_location)}</code></td>
                <td style="text-align:right;">
                    <button class="btn-submit" style="padding:4px 8px; font-size:0.75rem;" onclick="createReplenishmentTransfer('${escapeHTML(s.sku)}', '${escapeHTML(s.origin_location || '')}', '${escapeHTML(s.destination_location)}', ${s.stock_pulmon})">Crear ODT</button>
                </td>
            </tr>
        `).join('');
    } catch (e) {
        tbody.innerHTML = '<tr><td colspan="7" style="text-align:center; padding:1.5rem; color:var(--danger);">Error al cargar sugerencias.</td></tr>';
    }
}

async function createReplenishmentTransfer(sku, origLoc, destLoc, availableQty) {
    if (typeof switchView === 'function') switchView('section-purchases');
    switchPurchaseTab('tab-transfer');
    document.getElementById('form-transfer').reset();
    await loadNextTransferNumber();
    
    await loadTransferSelectors();
    
    document.getElementById('tr-lines').innerHTML = '';
    addDynamicLineTransfer();
    
    setTimeout(() => {
        const firstRow = document.querySelector('#tr-lines .dynamic-row');
        if (firstRow) {
            firstRow.querySelector('.tr-sku').value = sku;
            firstRow.querySelector('.tr-qty').value = availableQty > 10 ? 10 : availableQty;
            firstRow.querySelector('.tr-orig-loc').value = origLoc;
            firstRow.querySelector('.tr-dest-loc').value = destLoc;
        }
        if (typeof showToast === 'function') showToast('Formulario ODT autocompletado para reabastecimiento.', 'info');
    }, 300);
}

// === INVENTARIO FÍSICO ===
function openSpotCheckModal() {
    document.getElementById('form-spot-check').reset();
    document.getElementById('spot-check-result').style.display = 'none';
    openModal('modal-spot-check');
}

async function runSpotCheck(e) {
    e.preventDefault();
    const payload = {
        sku: document.getElementById('spot-check-sku').value.trim(),
        quantity: parseFloat(document.getElementById('spot-check-qty').value),
        location_code: document.getElementById('spot-check-loc').value.trim() || null,
        lot_number: document.getElementById('spot-check-lot').value.trim() || ""
    };

    const btn = e.target.querySelector('button[type="submit"]');
    if (btn) { btn.disabled = true; btn.textContent = 'Verificando...'; }

    try {
        const r = await fetchAPI('/api/inventory/spot-check', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload) });
        if(r) {
            const resDiv = document.getElementById('spot-check-result');
            resDiv.style.display = 'block';
            if(r.match) {
                resDiv.style.backgroundColor = 'rgba(0, 200, 83, 0.1)';
                resDiv.style.border = '1px solid var(--success)';
                resDiv.innerHTML = `<h4 style="color:var(--success); margin:0;">ACTUALIZADO: ¡STOCK CORRECTO!</h4><p style="margin:5px 0 0 0;">Físico Contado: <strong>${r.counted}</strong> | Sistema: <strong>${r.expected}</strong></p>`;
            } else {
                resDiv.style.backgroundColor = 'rgba(213, 0, 0, 0.1)';
                resDiv.style.border = '1px solid var(--danger)';
                resDiv.innerHTML = `<h4 style="color:var(--danger); margin:0;">ALERTA: DESCUADRE DETECTADO (DELTA: ${r.delta > 0 ? '+'+r.delta : r.delta})</h4><p style="margin:5px 0 0 0;">Físico Contado: <strong>${r.counted}</strong> | Sistema Esperaba: <strong>${r.expected}</strong></p>`;
            }
        }
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = 'Verificar Stock'; }
    }
}

async function loadInventorySessions() {
    const tbody = document.getElementById('table-inventory-sessions-body');
    if(!tbody) return;
    tbody.innerHTML = '<tr><td colspan="7" style="text-align:center; color:var(--text-muted); padding:2rem;">Cargando sesiones...</td></tr>';
    
    try {
        const data = await fetchAPI('/api/inventory/sessions');
        if(!data || data.length === 0) {
            tbody.innerHTML = '<tr><td colspan="7" style="text-align:center; color:var(--text-muted); padding:2rem;">No hay sesiones de conteo registradas.</td></tr>';
            return;
        }
        
        tbody.innerHTML = data.map(s => {
            let btn = '';
            let badge = '';
            
            if(s.status === 'OPEN') {
                badge = '<span class="badge badge-warning">ABIERTO (ESCANEO)</span>';
                btn = `<button class="btn-secondary" onclick="openScanInventoryModal('${s.id}')" style="padding:4px 8px; font-size:0.75rem;">Escanear Físico</button>`;
            } else if (s.status === 'REVIEW') {
                badge = '<span class="badge badge-info">EN REVISIÓN (DELTAS)</span>';
                btn = `<button class="btn-submit" onclick="openReviewInventoryModal('${s.id}')" style="padding:4px 8px; font-size:0.75rem;">Auditar Deltas</button>`;
            } else {
                badge = '<span class="badge badge-success">CERRADO</span>';
                btn = `<span style="color:var(--text-muted); font-size:0.8rem; padding-right:8px;">Finalizado</span>`;
            }
            
            return `<tr>
                <td>${escapeHTML(s.branch_name)}</td>
                <td style="font-weight:bold; color:var(--primary-blue);">${escapeHTML(s.sector_name)}</td>
                <td><span class="badge badge-neutral">${escapeHTML(s.count_type)}</span></td>
                <td>${badge}</td>
                <td>${new Date(s.created_at).toLocaleDateString()}</td>
                <td>${escapeHTML(s.assigned_operator)}</td>
                <td style="text-align:right;">${btn}</td>
            </tr>`;
        }).join('');
    } catch (e) {
        tbody.innerHTML = '<tr><td colspan="7" style="text-align:center; color:var(--danger); padding:2rem;">Error al cargar sesiones.</td></tr>';
    }
}

async function openCreateInventoryModal() {
    document.getElementById('form-create-inventory').reset();
    const bSelect = document.getElementById('inv-session-branch');
    const sSelect = document.getElementById('inv-session-sector');
    const oSelect = document.getElementById('inv-session-operator');
    
    bSelect.innerHTML = '<option value="">Cargando Sucursales...</option>';
    sSelect.innerHTML = '<option value="">-- Seleccionar Sector --</option>';
    oSelect.innerHTML = '<option value="">Cargando Operadores...</option>';
    
    openModal('modal-create-inventory');

    try {
        const [branches, sectors, users] = await Promise.all([
            fetchAPI('/api/admin/branches'),
            fetchAPI('/api/admin/sectors'),
            fetchAPI('/api/admin/users')
        ]);
        
        opsSectorsCache = sectors || [];
        
        if (branches && branches.length > 0) {
            bSelect.innerHTML = '<option value="">-- Seleccione Sucursal --</option>' + 
                branches.map(b => `<option value="${b.id}">${escapeHTML(b.name)}</option>`).join('');
        } else {
            bSelect.innerHTML = '<option value="">-- Sin Sucursales --</option>';
        }
        
        if (users && users.length > 0) {
            const ops = users.filter(u => u.role !== 'ADMIN' && u.is_active);
            oSelect.innerHTML = '<option value="">-- Seleccione Operador --</option>' + 
                ops.map(u => `<option value="${u.username}">${escapeHTML(u.full_name)} (${escapeHTML(u.username)})</option>`).join('');
        } else {
            oSelect.innerHTML = '<option value="">-- Sin Operadores Activos --</option>';
        }
    } catch (e) {
        console.error(e);
        if (typeof showToast === 'function') showToast("Error cargando dependencias para la auditoría.", "error");
    }
}

function onInvSessionBranchChange() {
    const branchId = document.getElementById('inv-session-branch').value;
    const sSelect = document.getElementById('inv-session-sector');
    sSelect.innerHTML = '<option value="">-- Seleccionar Sector --</option>';
    
    if (opsSectorsCache && opsSectorsCache.length > 0) {
        opsSectorsCache.filter(s => String(s.branch_id) === String(branchId)).forEach(s => {
            sSelect.innerHTML += `<option value="${s.id}">${escapeHTML(s.name)}</option>`;
        });
    }
}

async function saveInventorySession(e) {
    e.preventDefault();
    const payload = {
        branch_id: document.getElementById('inv-session-branch').value,
        sector_id: document.getElementById('inv-session-sector').value,
        count_type: document.getElementById('inv-session-type').value,
        assigned_operator: document.getElementById('inv-session-operator').value
    };
    const r = await fetchAPI('/api/inventory/sessions', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload) });
    if(r) {
        showToast('Sesión de inventario iniciada. Foto (Snapshot) capturada.', 'success');
        closeModal('modal-create-inventory');
        loadInventorySessions();
    }
}

function openScanInventoryModal(sessionId) {
    document.getElementById('form-scan-inventory').reset();
    document.getElementById('scan-inv-session-id').value = sessionId;
    document.getElementById('scan-inv-session-id-label').textContent = sessionId.substring(0, 8) + '...';
    openModal('modal-scan-inventory');
}

async function scanInventoryCount(e) {
    e.preventDefault();
    const sessionId = document.getElementById('scan-inv-session-id').value;
    const payload = {
        sku: document.getElementById('scan-inv-sku').value.trim(),
        quantity: parseFloat(document.getElementById('scan-inv-qty').value),
        location_code: document.getElementById('scan-inv-loc').value.trim() || null,
        lot_number: document.getElementById('scan-inv-lot').value.trim() || ""
    };
    
    const r = await fetchAPI(`/api/inventory/sessions/${sessionId}/scan`, { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload) });
    if(r) {
        showToast(r.message, 'success');
        document.getElementById('scan-inv-sku').value = '';
        document.getElementById('scan-inv-qty').value = '';
        document.getElementById('scan-inv-sku').focus();
    }
}

async function finishInventorySession() {
    const sessionId = document.getElementById('scan-inv-session-id').value;
    if(!confirm("¿Está seguro de finalizar el escaneo físico y enviar el conteo a Revisión de Deltas?")) return;
    
    const r = await fetchAPI(`/api/inventory/sessions/${sessionId}/finish`, { method: 'POST' });
    if(r) {
        showToast('Escaneo finalizado. Sesión enviada a revisión.', 'success');
        closeModal('modal-scan-inventory');
        loadInventorySessions();
    }
}

async function openReviewInventoryModal(sessionId) {
    document.getElementById('review-inv-session-id').value = sessionId;
    const tbody = document.getElementById('table-review-inventory-body');
    tbody.innerHTML = '<tr><td colspan="6" style="text-align:center; padding:2rem;">Analizando cruce de datos y calculando deltas...</td></tr>';
    openModal('modal-review-inventory');
    
    try {
        const data = await fetchAPI(`/api/inventory/sessions/${sessionId}/review`);
        if(!data || data.length === 0) {
            tbody.innerHTML = '<tr><td colspan="6" style="text-align:center; padding:2rem;">No hay discrepancias registradas en esta sesión.</td></tr>';
            return;
        }
        
        tbody.innerHTML = data.map(d => {
            const delta = parseFloat(d.delta);
            let deltaColor = 'var(--text-primary)';
            if (delta > 0) deltaColor = 'var(--success)';
            if (delta < 0) deltaColor = 'var(--danger)';
            
            return `<tr>
                <td style="font-weight:bold; color:var(--primary-blue);">${escapeHTML(d.sku)}</td>
                <td><code class="font-mono">${escapeHTML(d.location_code || 'N/A')}</code></td>
                <td class="lot-input">${escapeHTML(d.lot_number || '-')}</td>
                <td style="text-align:center;">${d.expected_quantity}</td>
                <td style="text-align:center; font-weight:bold;">${d.counted_quantity}</td>
                <td style="text-align:center; font-weight:bold; color:${deltaColor};">${delta > 0 ? '+'+delta : delta}</td>
            </tr>`;
        }).join('');
    } catch (e) {
        tbody.innerHTML = '<tr><td colspan="6" style="text-align:center; color:var(--danger); padding:2rem;">Error al calcular cruce de inventario.</td></tr>';
    }
}

async function applyInventoryAdjustments() {
    const sessionId = document.getElementById('review-inv-session-id').value;
    if(!confirm("ATENCIÓN: Esto inyectará movimientos de ajuste automático en el Kardex e impactará el stock activo real. ¿Desea proceder y cerrar el conteo?")) return;
    
    const r = await fetchAPI(`/api/inventory/sessions/${sessionId}/apply`, { method: 'POST' });
    if(r) {
        showToast('Ajustes aplicados al Kardex y Stock exitosamente.', 'success');
        closeModal('modal-review-inventory');
        loadInventorySessions();
    }
}

// === FUNCIONES DE RED DE MOVIMIENTOS Y TRASPASOS ===
async function loadNextTransferNumber() {
    const trInput = document.getElementById('tr-num');
    if (!trInput) return;
    
    trInput.readOnly = true;
    trInput.style.backgroundColor = '#F3F4F6';
    trInput.style.fontWeight = 'bold';
    trInput.style.color = 'var(--accent)';
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

async function loadTransferSelectors() {
    const origBranch = document.getElementById('tr-orig-branch');
    const destBranch = document.getElementById('tr-dest-branch');
    if (!origBranch || !destBranch) return;

    origBranch.innerHTML = '<option value="">Cargando...</option>';
    destBranch.innerHTML = '<option value="">Cargando...</option>';

    try {
        const [branches, sectors] = await Promise.all([
            fetchAPI('/api/admin/branches'),
            fetchAPI('/api/admin/sectors')
        ]);

        opsSectorsCache = sectors || [];

        const defaultOpt = '<option value="">-- Seleccionar --</option>';
        if (branches && branches.length > 0) {
            const branchOpts = branches.map(b => `<option value="${b.id}">${escapeHTML(b.name)}</option>`).join('');
            origBranch.innerHTML = defaultOpt + branchOpts;
            destBranch.innerHTML = defaultOpt + branchOpts;
        } else {
            origBranch.innerHTML = defaultOpt;
            destBranch.innerHTML = defaultOpt;
        }
    } catch (e) {
        console.error("Error cargando sucursales para traspaso:", e);
    }
}

function onTrOrigBranchChange() {
    const branchId = document.getElementById('tr-orig-branch').value;
    const sectorSelect = document.getElementById('tr-orig-sector');
    if (!sectorSelect) return;
    
    sectorSelect.innerHTML = '<option value="">-- Seleccionar --</option>';
    if (opsSectorsCache && opsSectorsCache.length > 0) {
        opsSectorsCache.filter(s => String(s.branch_id) === String(branchId)).forEach(s => {
            sectorSelect.innerHTML += `<option value="${s.id}">${escapeHTML(s.name)}</option>`;
        });
    }
}

function onTrDestBranchChange() {
    const branchId = document.getElementById('tr-dest-branch').value;
    const sectorSelect = document.getElementById('tr-dest-sector');
    if (!sectorSelect) return;
    
    sectorSelect.innerHTML = '<option value="">-- Seleccionar --</option>';
    if (opsSectorsCache && opsSectorsCache.length > 0) {
        opsSectorsCache.filter(s => String(s.branch_id) === String(branchId)).forEach(s => {
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
        showToast("Por favor complete sucursales y sectores de origen y destino.", "error");
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
                lot_number: lot || "",
                serial_numbers: []
            });
        }
    });

    if (lines.length === 0) {
        showToast("Ingrese al menos un artículo válido.", "error");
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
        const headers = {'Content-Type': 'application/json'};
        if (typeof AppConfig !== 'undefined' && AppConfig.enable_api_idempotency === 'true') {
            headers['X-Idempotency-Key'] = `TR-${num}-${Date.now()}`;
        }
        
        await fetchAPI('/api/admin/transfer-orders', { method: 'POST', headers: headers, body: JSON.stringify(payload) });
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
                <td style="color:var(--accent); font-weight:bold;">${escapeHTML(r.transfer_number)}</td>
                <td><small>${escapeHTML(r.origin_branch)} (${escapeHTML(r.origin_sector)})</small></td>
                <td><small>${escapeHTML(r.destination_branch)} (${escapeHTML(r.destination_sector)})</small></td>
                <td><span class="badge badge-warning">${escapeHTML(r.status)}</span></td>
            </tr>
        `).join('');
    } catch (e) {
        tbody.innerHTML = '<tr><td colspan="4" style="text-align:center; color:var(--danger);">Error al cargar historial.</td></tr>';
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

function addDynamicLinePO() { document.getElementById('po-lines').innerHTML += `<div class="dynamic-row"><input type="text" placeholder="SKU" class="po-sku font-mono" style="flex:2;" required><input type="number" placeholder="Cantidad" class="po-qty" style="flex:1;" min="0.01" step="0.01" required><button type="button" onclick="this.parentElement.remove()" class="btn-danger">X</button></div>`; }
function addDynamicLineRemito() { document.getElementById('rem-lines').innerHTML += `<div class="dynamic-row"><input type="text" placeholder="SKU" class="rem-sku font-mono" style="flex:2;" required onblur="fetchPutawaySuggestion(this.value, this.parentElement.querySelector('.rem-loc'))"><input type="number" placeholder="Cant" class="rem-qty" style="flex:1;" min="0.01" step="0.01" required><input type="text" placeholder="Ubicación" class="rem-loc font-mono" style="flex:1;"><input type="text" placeholder="Lote / Vto" class="rem-lot lot-input font-mono" style="flex:1; display:none;"><button type="button" onclick="this.parentElement.remove()" class="btn-danger">X</button></div>`; }
function addDynamicLineInvoice() { document.getElementById('inv-lines').innerHTML += `<div class="dynamic-row"><input type="text" placeholder="SKU" class="inv-sku font-mono" style="flex:2;" onblur="fetchPutawaySuggestion(this.value, this.parentElement.querySelector('.inv-loc'))"><input type="number" placeholder="Cantidad" class="inv-qty" style="flex:1;" min="0.01" step="0.01"><input type="text" placeholder="Ubicación" class="inv-loc font-mono" style="flex:1;"><input type="text" placeholder="Lote / Vto" class="inv-lot lot-input" style="flex:1; display:none;"><button type="button" onclick="this.parentElement.remove()" class="btn-danger">X</button></div>`; }
function addDynamicLineTransfer() { document.getElementById('tr-lines').innerHTML += `<div class="dynamic-row"><input type="text" placeholder="SKU" class="tr-sku font-mono" style="flex:2;" required><input type="number" placeholder="Cant" class="tr-qty" style="flex:1;" min="0.01" step="0.01" required><input type="text" placeholder="Origen" class="tr-orig-loc font-mono" style="flex:1;"><input type="text" placeholder="Destino" class="tr-dest-loc font-mono" style="flex:1;"><input type="text" placeholder="Lote / Vto" class="tr-lot lot-input" style="flex:1; display:none;"><button type="button" onclick="this.parentElement.remove()" class="btn-danger">X</button></div>`; }

window.loadNextTransferNumber = loadNextTransferNumber;
window.switchPurchaseTab = switchPurchaseTab;
window.onTrOrigBranchChange = onTrOrigBranchChange;
window.onTrDestBranchChange = onTrDestBranchChange;
window.saveTransfer = saveTransfer;
window.generateApiKey = generateApiKey;
window.copyApiKeyToClipboard = copyApiKeyToClipboard;
window.fetchPutawaySuggestion = fetchPutawaySuggestion;
window.loadReplenishmentSuggestions = loadReplenishmentSuggestions;
window.createReplenishmentTransfer = createReplenishmentTransfer;
window.addDynamicLinePO = addDynamicLinePO;
window.addDynamicLineRemito = addDynamicLineRemito;
window.addDynamicLineInvoice = addDynamicLineInvoice;
window.addDynamicLineTransfer = addDynamicLineTransfer;
window.loadOrders = loadOrders;
window.filterOrders = filterOrders;
window.openManualOrderModal = openManualOrderModal;
window.addDynamicLineManualOrder = addDynamicLineManualOrder;
window.saveManualOrder = saveManualOrder;
window.reprintOrderLabel = reprintOrderLabel;

// === VINCULACIÓN DEL MÓDULO DE INVENTARIO FÍSICO ===
window.openSpotCheckModal = openSpotCheckModal;
window.runSpotCheck = runSpotCheck;
window.loadInventorySessions = loadInventorySessions;
window.openCreateInventoryModal = openCreateInventoryModal;
window.onInvSessionBranchChange = onInvSessionBranchChange;
window.saveInventorySession = saveInventorySession;
window.openScanInventoryModal = openScanInventoryModal;
window.scanInventoryCount = scanInventoryCount;
window.finishInventorySession = finishInventorySession;
window.openReviewInventoryModal = openReviewInventoryModal;
window.applyInventoryAdjustments = applyInventoryAdjustments;
window.openPackingStation = openPackingStation;
window.processPackingScan = processPackingScan;
window.closeCurrentBox = closeCurrentBox;
window.submitPackingStation = submitPackingStation;