// === MÓDULO DE CATÁLOGO DE ARTÍCULOS Y UBICACIONES ===

let currentItemsPage = 1;
let totalItemsPages = 1;

async function loadItems(page = 1) {
    currentItemsPage = page;
    const tbody = document.getElementById('itemsTableBody');
    if (!tbody) return;

    const skuSearch = (document.getElementById('searchItemSku')?.value || '').trim();
    const descSearch = (document.getElementById('searchItemDesc')?.value || '').trim();

    tbody.innerHTML = '<tr><td colspan="7" class="text-center py-3"><div class="spinner-border spinner-border-sm"></div> Cargando artículos...</td></tr>';

    try {
        const query = `?sku=${encodeURIComponent(skuSearch)}&description=${encodeURIComponent(descSearch)}&page=${page}&limit=50`;
        const res = await fetchAPI(`/api/admin/items${query}`);
        
        const items = res.items || [];
        totalItemsPages = res.total_pages || 1;

        if (items.length === 0) {
            tbody.innerHTML = '<tr><td colspan="7" class="text-center text-muted">No se encontraron artículos.</td></tr>';
            updateItemsPagination();
            return;
        }

        tbody.innerHTML = items.map(i => `
            <tr>
                <td class="fw-bold text-primary"><code>${i.sku}</code></td>
                <td>${i.description}</td>
                <td><span class="badge bg-light text-dark border">${i.category || 'Sin Cat.'}</span></td>
                <td><small class="text-muted">${i.locations_summary || 'Sin asignación'}</small></td>
                <td><small>${i.weight ? i.weight + ' kg' : '-'}</small></td>
                <td><small>${i.volume ? i.volume + ' m³' : '-'}</small></td>
                <td class="text-end">
                    <button class="btn btn-sm btn-outline-primary me-1" onclick="openItemLocationsModal('${i.sku}')">📍 Ubicaciones</button>
                    <button class="btn btn-sm btn-outline-dark" onclick="openEditItemModal('${i.sku}', '${i.description.replace(/'/g, "\\'")}', '${i.category || ''}', ${i.length || 0}, ${i.width || 0}, ${i.height || 0}, ${i.weight || 0}, ${i.volume || 0})">✏️ Ficha</button>
                </td>
            </tr>
        `).join('');

        updateItemsPagination();
    } catch (e) {
        tbody.innerHTML = '<tr><td colspan="7" class="text-center text-danger">Error al cargar el catálogo de artículos.</td></tr>';
    }
}

function updateItemsPagination() {
    const container = document.getElementById('itemsPaginationControls');
    if (!container) return;

    container.innerHTML = `
        <div class="d-flex justify-content-between align-items-center mt-2">
            <span class="text-muted small">Página ${currentItemsPage} de ${totalItemsPages}</span>
            <div>
                <button class="btn btn-sm btn-outline-secondary me-1" ${currentItemsPage <= 1 ? 'disabled' : ''} onclick="loadItems(${currentItemsPage - 1})">◀ Anterior</button>
                <button class="btn btn-sm btn-outline-secondary" ${currentItemsPage >= totalItemsPages ? 'disabled' : ''} onclick="loadItems(${currentItemsPage + 1})">Siguiente ▶</button>
            </div>
        </div>
    `;
}

async function saveItemFicha(event) {
    if (event) event.preventDefault();
    const sku = document.getElementById('itemFichaSku').value;

    const payload = {
        description: document.getElementById('itemFichaDesc').value.trim(),
        category: document.getElementById('itemFichaCategory').value.trim(),
        length: parseFloat(document.getElementById('itemFichaLength').value) || 0,
        width: parseFloat(document.getElementById('itemFichaWidth').value) || 0,
        height: parseFloat(document.getElementById('itemFichaHeight').value) || 0,
        weight: parseFloat(document.getElementById('itemFichaWeight').value) || 0,
        volume: parseFloat(document.getElementById('itemFichaVolume').value) || 0
    };

    try {
        await fetchAPI(`/api/admin/items/${encodeURIComponent(sku)}`, { method: 'PUT', body: payload });
        showToast("Ficha técnica de artículo actualizada.", "success");
        
        const modalEl = document.getElementById('itemFichaModal');
        if (modalEl) {
            const modal = bootstrap.Modal.getInstance(modalEl);
            if (modal) modal.hide();
        }
        loadItems(currentItemsPage);
    } catch (e) {}
}

function openEditItemModal(sku, desc, cat, l, w, h, wt, vol) {
    document.getElementById('itemFichaSku').value = sku;
    document.getElementById('itemFichaDesc').value = desc;
    document.getElementById('itemFichaCategory').value = cat;
    document.getElementById('itemFichaLength').value = l;
    document.getElementById('itemFichaWidth').value = w;
    document.getElementById('itemFichaHeight').value = h;
    document.getElementById('itemFichaWeight').value = wt;
    document.getElementById('itemFichaVolume').value = vol;

    const modalEl = document.getElementById('itemFichaModal');
    if (modalEl) new bootstrap.Modal(modalEl).show();
}

async function openItemLocationsModal(sku) {
    document.getElementById('itemLocSkuHidden').value = sku;
    document.getElementById('itemLocModalTitle').innerText = `Ubicaciones Asignadas a: ${sku}`;
    await reloadItemLocationsList(sku);

    const modalEl = document.getElementById('itemLocationsModal');
    if (modalEl) new bootstrap.Modal(modalEl).show();
}

async function reloadItemLocationsList(sku) {
    const listDiv = document.getElementById('itemLocationsList');
    if (!listDiv) return;
    listDiv.innerHTML = '<div class="text-center py-2"><div class="spinner-border spinner-border-sm"></div></div>';

    try {
        const locations = await fetchAPI(`/api/admin/items/${encodeURIComponent(sku)}/locations`);
        if (!locations || locations.length === 0) {
            listDiv.innerHTML = '<p class="text-muted small">El artículo no tiene ubicaciones asignadas.</p>';
            return;
        }

        listDiv.innerHTML = locations.map(l => `
            <div class="card mb-2 p-2 border">
                <div class="d-flex justify-content-between align-items-center">
                    <div>
                        <strong class="text-primary">${l.location_code}</strong>
                        <div class="text-muted small">${l.branch_name || ''} - ${l.sector_name || ''}</div>
                    </div>
                    <button class="btn btn-sm btn-outline-danger" onclick="deleteItemLocation('${l.assignment_id}', '${sku}')">🗑️ Unlink</button>
                </div>
            </div>
        `).join('');
    } catch (e) {
        listDiv.innerHTML = '<p class="text-danger small">Error al cargar asignaciones de ubicación.</p>';
    }
}

async function addItemLocationForm(event) {
    if (event) event.preventDefault();
    const sku = document.getElementById('itemLocSkuHidden').value;
    const locCode = document.getElementById('itemLocCodeInput').value.trim();

    if (!locCode) return;

    try {
        await fetchAPI('/api/admin/item-locations', {
            method: 'POST',
            body: { sku: sku, location_code: locCode }
        });
        showToast("Ubicación asignada correctamente.", "success");
        document.getElementById('itemLocCodeInput').value = '';
        reloadItemLocationsList(sku);
    } catch (e) {}
}

async function deleteItemLocation(assignmentId, sku) {
    if (!confirm("¿Desvincular esta ubicación del artículo?")) return;
    try {
        await fetchAPI(`/api/admin/item-locations/${assignmentId}`, { method: 'DELETE' });
        showToast("Asignación eliminada.", "success");
        reloadItemLocationsList(sku);
    } catch (e) {}
}

async function importItemsCSV(fileInput) {
    if (!fileInput.files || fileInput.files.length === 0) return;
    const formData = new FormData();
    formData.append('file', fileInput.files[0]);

    try {
        showToast("Procesando archivo CSV...", "info");
        const res = await fetch('/api/admin/import/items', { method: 'POST', body: formData, credentials: 'include' });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || "Error al importar CSV.");
        showToast(data.message || "Artículos importados con éxito.", "success");
        loadItems(1);
    } catch (e) {
        showToast(e.message, "danger");
    } finally {
        fileInput.value = '';
    }
}

async function openBatchPrintModal() {
    const modalEl = document.getElementById('batchPrintModal');
    if (modalEl) new bootstrap.Modal(modalEl).show();
}

async function sendBatchPrintRequest(event) {
    if (event) event.preventDefault();
    const queueCode = document.getElementById('batchPrintQueue').value.trim();
    const rawText = document.getElementById('batchPrintItemsText').value.trim();

    if (!queueCode || !rawText) {
        showToast("Complete la cola y la lista de artículos.", "danger");
        return;
    }

    const lines = rawText.split('\n');
    const items = [];

    for (let line of lines) {
        line = line.trim();
        if (!line) continue;
        const parts = line.split(',');
        const sku = parts[0].trim();
        const qty = parts.length > 1 ? parseInt(parts[1].trim()) || 1 : 1;
        if (sku) items.push({ sku: sku, quantity: qty });
    }

    if (items.length === 0) {
        showToast("No se detectaron líneas válidas. Formato: SKU,CANTIDAD", "danger");
        return;
    }

    try {
        const res = await fetchAPI('/api/admin/items/batch-print-labels', {
            method: 'POST',
            body: { queue_code: queueCode, items: items }
        });
        showToast(res.message || "Etiquetas enviadas a la cola de impresión.", "success");
        
        const modalEl = document.getElementById('batchPrintModal');
        if (modalEl) {
            const modal = bootstrap.Modal.getInstance(modalEl);
            if (modal) modal.hide();
        }
    } catch (e) {}
}
