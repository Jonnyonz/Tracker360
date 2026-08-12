// === MÓDULO ARTÍCULOS (TRACKER360) ===
let itemsCurrentPage = 1;
let itemsCache = {};

function escapeHTML(str) {
    if (str === null || str === undefined) return '';
    return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

async function handleSearchItems(page = 1) {
    itemsCurrentPage = page;
    const tbody = document.getElementById('table-items-body');
    if (!tbody) return;

    const theadTr = document.querySelector('#section-items table thead tr');
    if (theadTr && theadTr.children.length === 4) {
        const stockTh = document.createElement('th');
        stockTh.textContent = 'Stock Total';
        theadTr.insertBefore(stockTh, theadTr.children[3]);
    }

    const skuInput = document.getElementById('search-sku');
    const descInput = document.getElementById('search-desc');

    const sku = skuInput ? skuInput.value.trim() : '';
    const desc = descInput ? descInput.value.trim() : '';

    tbody.innerHTML = '<tr><td colspan="5" style="text-align:center; padding:1.5rem; color:#6c757d;">Cargando artículos...</td></tr>';

    let url = `/api/admin/items?page=${page}&limit=15`;
    if (sku) url += `&sku=${encodeURIComponent(sku)}`;
    if (desc) url += `&description=${encodeURIComponent(desc)}`;

    try {
        const data = await fetchAPI(url);
        const items = Array.isArray(data) ? data : (data.items || data.rows || data.data || []);

        if (!items || items.length === 0) {
            tbody.innerHTML = '<tr><td colspan="5" style="text-align:center; padding:1.5rem; color:#6c757d;">No se encontraron artículos registrados.</td></tr>';
            return;
        }

        itemsCache = {};
        items.forEach(item => {
            const key = item.sku || item.code;
            if (key) itemsCache[key] = item;
        });

        tbody.innerHTML = items.map(item => {
            const itemSku = escapeHTML(item.sku || item.code || '-');
            const itemDesc = escapeHTML(item.description || item.name || '-');
            const itemLoc = escapeHTML(item.locations_summary || item.locations || item.location_code || 'Sin asignación');
            const stockTotal = item.total_stock || 0;
            const isCombo = item.is_combo;

            const stockColor = stockTotal > 0 ? 'var(--success-green)' : (stockTotal < 0 ? 'var(--error-red)' : 'var(--text-muted)');
            const comboBadge = isCombo ? `<span class="badge badge-info" style="margin-left:6px; font-size:0.7rem;">COMBO</span>` : '';

            return `
                <tr>
                    <td style="font-weight:600; color:var(--primary-blue);"><code>${itemSku}</code>${comboBadge}</td>
                    <td>${itemDesc}</td>
                    <td><span class="badge badge-neutral">${itemLoc}</span></td>
                    <td style="font-weight:bold; color:${stockColor}; font-size:1.1rem;">${stockTotal}</td>
                    <td>
                        <div style="display:flex; gap:6px;">
                            <button type="button" class="btn-secondary" style="padding:0.3rem 0.6rem; font-size:0.75rem;" onclick="openStockBreakdownModal('${itemSku}')">
                                Stock
                            </button>
                            <button type="button" class="btn-submit" style="width:auto; margin:0; padding:0.3rem 0.6rem; font-size:0.75rem;" onclick="openEditItemModal('${itemSku}')">
                                Editar
                            </button>
                        </div>
                    </td>
                </tr>
            `;
        }).join('');

    } catch (err) {
        console.error("Error al obtener artículos:", err);
        tbody.innerHTML = '<tr><td colspan="5" style="text-align:center; padding:1.5rem; color:#dc3545;">Error de conexión al cargar artículos.</td></tr>';
    }
}

// === EDICIÓN DE ARTÍCULO Y GESTIÓN DE COMBOS/DIMENSIONES ===
async function openEditItemModal(sku) {
    const modalEl = document.getElementById('modal-edit-item');
    if (!modalEl) return;

    const item = itemsCache[sku] || { sku: sku, description: '', category: '', is_combo: false };

    const titleEl = document.getElementById('edit-item-sku-label');
    if (titleEl) titleEl.textContent = sku;

    document.getElementById('edit-item-sku').value = sku;
    
    const descVal = item.description || item.name || '';
    const catVal = item.category || '';
    
    const descInp = document.getElementById('edit-item-desc');
    const catInp = document.getElementById('edit-item-cat');
    if (descInp) descInp.value = descVal === '-' ? '' : descVal;
    if (catInp) catInp.value = catVal === '-' ? '' : catVal;

    // Lógica para Dimensiones y Pesos
    const dimSection = document.getElementById('edit-item-dimensions-section');
    if (dimSection) {
        if (typeof AppConfig !== 'undefined' && AppConfig.enable_item_dimensions === 'true') {
            dimSection.style.display = 'flex';
            document.getElementById('edit-item-length').value = item.length || 0;
            document.getElementById('edit-item-width').value = item.width || 0;
            document.getElementById('edit-item-height').value = item.height || 0;
            document.getElementById('edit-item-weight').value = item.weight || 0;
        } else {
            dimSection.style.display = 'none';
        }
    }

    // Manejo del checkbox de Combo
    const isComboCheck = document.getElementById('edit-item-is-combo');
    const comboSection = document.getElementById('edit-combo-section');
    const list = document.getElementById('edit-combo-components-list');

    if (isComboCheck) {
        isComboCheck.checked = item.is_combo;
        
        if (item.is_combo) {
            comboSection.style.display = 'block';
            list.innerHTML = '<p style="text-align:center; padding:1rem; color:var(--text-muted);">Cargando componentes...</p>';
            
            try {
                const data = await fetchAPI(`/api/admin/items/${encodeURIComponent(sku)}/combo`);
                list.innerHTML = '';
                if (data && data.components && data.components.length > 0) {
                    data.components.forEach(comp => addComboComponentRow(comp.component_sku, comp.quantity));
                } else {
                    addComboComponentRow('', 1);
                }
            } catch (e) {
                list.innerHTML = '';
                addComboComponentRow('', 1);
            }
        } else {
            comboSection.style.display = 'none';
            list.innerHTML = '';
        }
    }

    if (typeof window.openModal === 'function') {
        window.openModal('modal-edit-item');
    } else {
        modalEl.style.display = 'flex';
    }
}

function toggleEditComboSection() {
    const isChecked = document.getElementById('edit-item-is-combo').checked;
    const section = document.getElementById('edit-combo-section');
    const list = document.getElementById('edit-combo-components-list');
    
    if (isChecked) {
        section.style.display = 'block';
        if (list.children.length === 0) {
            addComboComponentRow('', 1);
        }
    } else {
        section.style.display = 'none';
        list.innerHTML = '';
    }
}

function addComboComponentRow(compSku = '', qty = 1) {
    const list = document.getElementById('edit-combo-components-list');
    if (!list) return;

    const div = document.createElement('div');
    div.className = 'dynamic-row';
    div.style.marginBottom = '8px';
    div.innerHTML = `
        <input type="text" placeholder="SKU Componente" class="combo-comp-sku" value="${escapeHTML(compSku)}" style="flex:2;" required autocomplete="off">
        <input type="number" placeholder="Cantidad" class="combo-comp-qty" value="${qty}" style="flex:1;" min="0.01" step="0.01" required>
        <button type="button" onclick="this.parentElement.remove()" style="background:var(--error-red); color:white; border:none; padding:4px 10px; border-radius:4px; font-weight:bold; cursor:pointer;">X</button>
    `;
    list.appendChild(div);
}

async function saveItemEdit(event) {
    event.preventDefault();
    const sku = document.getElementById('edit-item-sku').value;
    const desc = document.getElementById('edit-item-desc').value.trim();
    const cat = document.getElementById('edit-item-cat').value.trim();
    const isCombo = document.getElementById('edit-item-is-combo').checked;

    // Valores de Dimensiones
    let length = 0, width = 0, height = 0, weight = 0;
    if (typeof AppConfig !== 'undefined' && AppConfig.enable_item_dimensions === 'true') {
        length = parseFloat(document.getElementById('edit-item-length').value) || 0;
        width = parseFloat(document.getElementById('edit-item-width').value) || 0;
        height = parseFloat(document.getElementById('edit-item-height').value) || 0;
        weight = parseFloat(document.getElementById('edit-item-weight').value) || 0;
    }

    const components = [];
    if (isCombo) {
        const rows = document.querySelectorAll('#edit-combo-components-list .dynamic-row');
        rows.forEach(r => {
            const compSku = r.querySelector('.combo-comp-sku')?.value.trim().toUpperCase();
            const qty = parseFloat(r.querySelector('.combo-comp-qty')?.value);
            if (compSku && !isNaN(qty) && qty > 0) {
                components.push({ component_sku: compSku, quantity: qty });
            }
        });
    }

    const payload = { 
        description: desc, 
        category: cat,
        length: length,
        width: width,
        height: height,
        weight: weight,
        is_combo: isCombo,
        components: components
    };

    const btn = event.target.querySelector('button[type="submit"]');
    btn.disabled = true;
    btn.textContent = 'Guardando...';

    try {
        const res = await fetchAPI(`/api/admin/items/${encodeURIComponent(sku)}`, {
            method: 'PUT',
            body: payload
        });
        
        if (res && res.status === 'success') {
            if (typeof window.closeModal === 'function') {
                window.closeModal('modal-edit-item');
            } else {
                document.getElementById('modal-edit-item').style.display = 'none';
            }
            if (typeof showToast === 'function') showToast(res.message, "success");
            handleSearchItems(itemsCurrentPage);
        } else {
            alert("Error al guardar los cambios.");
        }
    } catch (e) {
        console.error(e);
        if (typeof showToast === 'function') showToast(e.message, "error");
    } finally {
        btn.disabled = false;
        btn.textContent = 'Guardar Cambios';
    }
}

// === DESGLOSE DE STOCK Y MODALES ===
function ensureStockModalExists() {
    if (document.getElementById('modal-stock-breakdown')) return;
    
    const modalHTML = `
    <div id="modal-stock-breakdown" class="modal">
        <div class="modal-content" style="max-width:700px;">
            <span class="close-modal" onclick="document.getElementById('modal-stock-breakdown').style.display='none'">&times;</span>
            <h3 style="color:var(--primary-blue); margin-bottom:0.5rem;">Desglose de Stock Físico</h3>
            <h4 id="stock-breakdown-subtitle" style="color:var(--text-muted); font-size:0.95rem; margin-bottom:1.5rem;"></h4>
            
            <div style="max-height:60vh; overflow-y:auto; border:1px solid var(--border-color); border-radius:6px;">
                <table style="width:100%; border-collapse:collapse;">
                    <thead>
                        <tr>
                            <th style="background:#F3F4F6; padding:10px; text-align:left; border-bottom:2px solid var(--border-color);">Sucursal</th>
                            <th style="background:#F3F4F6; padding:10px; text-align:left; border-bottom:2px solid var(--border-color);">Sector</th>
                            <th style="background:#F3F4F6; padding:10px; text-align:left; border-bottom:2px solid var(--border-color);">Ubicación Exacta</th>
                            <th style="background:#F3F4F6; padding:10px; text-align:right; border-bottom:2px solid var(--border-color);">Cant. Disp.</th>
                        </tr>
                    </thead>
                    <tbody id="stock-breakdown-body">
                    </tbody>
                </table>
            </div>
        </div>
    </div>`;
    
    document.body.insertAdjacentHTML('beforeend', modalHTML);
}

async function openStockBreakdownModal(sku) {
    ensureStockModalExists();
    const modal = document.getElementById('modal-stock-breakdown');
    const tbody = document.getElementById('stock-breakdown-body');
    const subtitle = document.getElementById('stock-breakdown-subtitle');
    
    const item = itemsCache[sku] || { description: 'Artículo' };
    subtitle.innerHTML = `SKU: <strong>${escapeHTML(sku)}</strong> - ${escapeHTML(item.description || item.name)}`;
    
    tbody.innerHTML = '<tr><td colspan="4" style="text-align:center; padding:2rem; color:var(--primary-blue);">Consultando existencias en tiempo real...</td></tr>';
    modal.style.display = 'flex';
    
    try {
        const rows = await fetchAPI(`/api/admin/items/${encodeURIComponent(sku)}/stock-breakdown`);
        
        if (!rows || rows.length === 0) {
            tbody.innerHTML = '<tr><td colspan="4" style="text-align:center; padding:2rem; color:var(--error-red); font-weight:bold;">No hay existencias físicas registradas para este artículo.</td></tr>';
            return;
        }

        let html = '';
        let total = 0;

        rows.forEach(r => {
            html += `
                <tr style="border-bottom:1px solid #E5E7EB;">
                    <td style="padding:10px; font-weight:600; color:var(--text-main);">${escapeHTML(r.branch_name)}</td>
                    <td style="padding:10px; color:var(--text-muted);">${escapeHTML(r.sector_name)}</td>
                    <td style="padding:10px; font-family:monospace; color:var(--primary-blue); font-weight:bold;">${escapeHTML(r.location_code)}</td>
                    <td style="padding:10px; text-align:right; font-weight:bold; color:var(--success-green); font-size:1.1rem;">${r.quantity}</td>
                </tr>
            `;
            total += (parseFloat(r.quantity) || 0);
        });

        html += `
            <tr style="background:#F8FAFC;">
                <td colspan="3" style="padding:12px; text-align:right; font-weight:800; color:var(--primary-blue); text-transform:uppercase;">TOTAL CONSOLIDADO:</td>
                <td style="padding:12px; text-align:right; font-weight:900; color:var(--primary-blue); font-size:1.2rem;">${total}</td>
            </tr>
        `;

        tbody.innerHTML = html;

    } catch (err) {
        console.error("Error al obtener desglose de stock:", err);
        tbody.innerHTML = '<tr><td colspan="4" style="text-align:center; padding:2rem; color:var(--error-red);">Error al conectar con la base de datos.</td></tr>';
    }
}

// === IMPORTACIÓN DE CSV (CATÁLOGO Y UBICACIONES) ===
function openImportModal(type) {
    const modal = document.getElementById('modal-import');
    if (!modal) return;
    
    document.getElementById('import-type').value = type;
    const title = document.getElementById('import-modal-title');
    if (type === 'items') title.textContent = 'Importar Catálogo de Artículos';
    else if (type === 'item_locations') title.textContent = 'Importar Ubicaciones de Artículos';
    else title.textContent = 'Importar CSV';

    document.getElementById('import-file').value = '';
    document.getElementById('import-file-name').style.display = 'none';
    
    if (typeof window.openModal === 'function') {
        window.openModal('modal-import');
    } else {
        modal.style.display = 'flex';
    }
}

function handleFileSelect(event) {
    const file = event.target.files[0];
    const nameLabel = document.getElementById('import-file-name');
    if (file) {
        nameLabel.textContent = file.name;
        nameLabel.style.display = 'block';
    } else {
        nameLabel.style.display = 'none';
    }
}

async function uploadCSV() {
    const fileInput = document.getElementById('import-file');
    const type = document.getElementById('import-type').value;
    const file = fileInput.files[0];
    
    if (!file) {
        alert("Por favor seleccione un archivo CSV primero.");
        return;
    }

    const formData = new FormData();
    formData.append("file", file);

    let url = '';
    if (type === 'items') url = '/api/admin/import/items';
    else if (type === 'item_locations') url = '/api/admin/import/item-locations';
    else return;

    const btn = document.getElementById('btn-upload-csv');
    btn.disabled = true;
    btn.textContent = 'Procesando...';

    try {
        const token = document.cookie.split('; ').find(row => row.startsWith('access_token='));
        const headers = {};
        if (token) headers['Authorization'] = decodeURIComponent(token.split('=')[1]);

        const response = await fetch(url, {
            method: 'POST',
            headers: headers,
            body: formData
        });

        const result = await response.json();
        
        if (response.ok) {
            alert(result.message || "Archivo procesado con éxito.");
            if (typeof window.closeModal === 'function') {
                window.closeModal('modal-import');
            } else {
                document.getElementById('modal-import').style.display = 'none';
            }
            handleSearchItems(1);
        } else {
            alert(result.detail || "Error al procesar el archivo.");
        }
    } catch (err) {
        console.error("Error upload:", err);
        alert("Fallo de conexión al subir el archivo.");
    } finally {
        btn.disabled = false;
        btn.textContent = 'Procesar Archivo';
    }
}

// === IMPRESIÓN DE ETIQUETAS ===
function addDynamicLineBatchPrint() {
    const list = document.getElementById('batch-print-lines');
    if (!list) return;
    const div = document.createElement('div');
    div.className = 'dynamic-row';
    div.innerHTML = `
        <input type="text" placeholder="Código SKU" style="flex:2;" required>
        <input type="number" placeholder="Cantidad" style="flex:1;" min="1" value="1" required>
        <button type="button" onclick="this.parentElement.remove()" style="background:var(--error-red); color:white; border:none; padding:4px 8px; border-radius:4px; font-weight:bold; cursor:pointer;">X</button>
    `;
    list.appendChild(div);
}

async function openBatchPrintModal() {
    const modalEl = document.getElementById('modal-batch-print-items');
    if (!modalEl) return;

    const selectEl = modalEl.querySelector('select');
    if (selectEl) {
        selectEl.innerHTML = '<option value="">Cargando sectores...</option>';
        try {
            const data = await fetchAPI('/api/admin/sectors');
            const sectors = Array.isArray(data) ? data : (data.sectors || data.rows || data.data || []);
            if (sectors.length > 0) {
                selectEl.innerHTML = '<option value="">-- Seleccionar Sector --</option>' + 
                    sectors.map(s => `<option value="${s.print_queue_code || s.code || s.name}">${s.name || s.sector_name || s.code}</option>`).join('');
            } else {
                selectEl.innerHTML = '<option value="RECEPCION">Recepcion</option>';
            }
        } catch (e) {
            selectEl.innerHTML = '<option value="RECEPCION">Recepcion</option>';
        }
    }

    if (typeof window.openModal === 'function') {
        window.openModal('modal-batch-print-items');
    } else {
        modalEl.style.display = 'flex';
    }
}

async function sendBatchPrintJobs() {
    const modalEl = document.getElementById('modal-batch-print-items');
    if (!modalEl) return;

    const selectEl = modalEl.querySelector('select');
    let queueCode = '';
    if (selectEl) {
        const selectedOpt = selectEl.options[selectEl.selectedIndex];
        queueCode = selectedOpt ? (selectedOpt.value || selectedOpt.text) : selectEl.value;
    }

    if (!queueCode || queueCode.toLowerCase().includes('seleccionar') || queueCode.toLowerCase().includes('cargando')) {
        alert("Por favor seleccione un Sector de Impresión válido.");
        return;
    }

    const inputs = Array.from(modalEl.querySelectorAll('input')).filter(i => i.type !== 'hidden');
    const skus = [];

    for (let i = 0; i < inputs.length; i++) {
        const val = inputs[i].value.trim();
        if (val && val.length >= 2 && isNaN(val)) {
            let qty = 1;
            if (i + 1 < inputs.length && !isNaN(inputs[i + 1].value) && inputs[i + 1].value.trim() !== '') {
                qty = parseInt(inputs[i + 1].value.trim(), 10) || 1;
            }
            for (let q = 0; q < qty; q++) {
                skus.push(val);
            }
        }
    }

    if (skus.length === 0) {
        alert("Por favor ingrese al menos un SKU válido.");
        return;
    }

    const btn = document.getElementById('btn-send-batch-print') || modalEl.querySelector('button.btn-submit');
    if (btn) { btn.disabled = true; btn.textContent = 'Enviando...'; }

    try {
        const res = await fetchAPI('/api/admin/items/batch-print-labels', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                queue_code: queueCode.trim().toUpperCase(),
                skus: skus
            })
        });

        alert(`¡Orden enviada! Se generaron ${res.jobs_created || skus.length} etiqueta(s) para el sector '${queueCode.toUpperCase()}'.`);
        
        if (typeof window.closeModal === 'function') {
            window.closeModal('modal-batch-print-items');
        } else {
            modalEl.style.display = 'none';
        }
    } catch (err) {
        console.error("Error al enviar trabajo de impresión:", err);
        alert("No se pudo enviar la orden de impresión. Verifique los datos.");
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = 'Enviar a Impresora'; }
    }
}

document.addEventListener('DOMContentLoaded', () => {
    setTimeout(() => {
        handleSearchItems(1);
    }, 150);
});

// Exponer funciones globales
window.handleSearchItems = handleSearchItems;
window.loadItems = handleSearchItems;
window.openBatchPrintModal = openBatchPrintModal;
window.openEditItemModal = openEditItemModal;
window.sendBatchPrintJobs = sendBatchPrintJobs;
window.openStockBreakdownModal = openStockBreakdownModal;
window.addDynamicLineBatchPrint = addDynamicLineBatchPrint;
window.openImportModal = openImportModal;
window.handleFileSelect = handleFileSelect;
window.uploadCSV = uploadCSV;
window.saveItemEdit = saveItemEdit;
window.toggleEditComboSection = toggleEditComboSection;
window.addComboComponentRow = addComboComponentRow;