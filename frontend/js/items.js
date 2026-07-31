// === MÓDULO ARTÍCULOS (TRACKER360) ===
let itemsCurrentPage = 1;
let itemsCache = {};

async function handleSearchItems(page = 1) {
    itemsCurrentPage = page;
    const tbody = document.getElementById('table-items-body');
    if (!tbody) return;

    const skuInput = document.getElementById('search-sku');
    const descInput = document.getElementById('search-desc');

    const sku = skuInput ? skuInput.value.trim() : '';
    const desc = descInput ? descInput.value.trim() : '';

    tbody.innerHTML = '<tr><td colspan="4" style="text-align:center; padding:1.5rem; color:#6c757d;">Cargando artículos...</td></tr>';

    let url = `/api/admin/items?page=${page}&limit=15`;
    if (sku) url += `&sku=${encodeURIComponent(sku)}`;
    if (desc) url += `&search=${encodeURIComponent(desc)}`;

    try {
        const data = await fetchAPI(url);
        const items = Array.isArray(data) ? data : (data.items || data.rows || data.data || []);

        if (!items || items.length === 0) {
            tbody.innerHTML = '<tr><td colspan="4" style="text-align:center; padding:1.5rem; color:#6c757d;">No se encontraron artículos registrados.</td></tr>';
            return;
        }

        itemsCache = {};
        items.forEach(item => {
            const key = item.sku || item.code;
            if (key) itemsCache[key] = item;
        });

        tbody.innerHTML = items.map(item => {
            const itemSku = item.sku || item.code || '-';
            const itemDesc = item.description || item.name || '-';
            const itemLoc = item.locations || item.location_code || 'Sin asignación';

            return `
                <tr>
                    <td style="font-weight:600;"><code>${itemSku}</code></td>
                    <td>${itemDesc}</td>
                    <td>${itemLoc}</td>
                    <td>
                        <button type="button" class="btn-submit" style="width:auto; margin:0; padding:0.3rem 0.8rem; font-size:0.8rem;" onclick="openEditItemModal('${itemSku}')">
                            Editar
                        </button>
                    </td>
                </tr>
            `;
        }).join('');

    } catch (err) {
        console.error("Error al obtener artículos:", err);
        tbody.innerHTML = '<tr><td colspan="4" style="text-align:center; padding:1.5rem; color:#dc3545;">Error de conexión al cargar artículos.</td></tr>';
    }
}

function openEditItemModal(sku) {
    const modalEl = document.getElementById('modal-edit-item');
    if (!modalEl) return;

    const item = itemsCache[sku] || { sku: sku, description: '', category: '', locations: '' };

    if (typeof window.openModal === 'function') {
        window.openModal('modal-edit-item');
    } else {
        modalEl.style.display = 'block';
    }

    const titleEl = modalEl.querySelector('h1, h2, h3, h4, h5, h6, .modal-title');
    if (titleEl) {
        titleEl.textContent = `FICHA TÉCNICA SKU: ${sku}`;
    }

    const visibleInputs = Array.from(modalEl.querySelectorAll('input')).filter(inp => inp.type !== 'hidden' && inp.style.display !== 'none');
    
    let descVal = item.description || item.name || '';
    let catVal = item.category || item.sector_name || item.sector || '';

    if (visibleInputs.length > 0) visibleInputs[0].value = descVal === '-' ? '' : descVal;
    if (visibleInputs.length > 1) visibleInputs[1].value = catVal === '-' ? '' : catVal;
    if (visibleInputs.length > 2) visibleInputs[2].value = '';

    let locVal = item.locations || item.location_code || 'Sin ubicaciones asignadas.';
    let locContainer = modalEl.querySelector('#loc-dynamic-text');
    
    if (!locContainer) {
        const elements = modalEl.querySelectorAll('*');
        for (const el of elements) {
            if (el.children.length === 0) {
                const txt = el.textContent.trim().toLowerCase();
                if (txt === 'cargando...' || txt === 'sin asignación' || txt === 'sin ubicaciones asignadas.') {
                    el.id = 'loc-dynamic-text';
                    locContainer = el;
                    break;
                }
            }
        }
    }
    
    if (locContainer) {
        locContainer.textContent = locVal === '-' ? 'Sin ubicaciones asignadas.' : locVal;
    }
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
        modalEl.style.display = 'block';
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
        const res = await fetchAPI('/api/admin/print-jobs', {
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
