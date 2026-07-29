// === MÓDULO OPERATIVO TÁCTIL Y SCANNER ZEBRA (PREPARADOR / PANTALLA MÓVIL) ===

let activePickingDoc = null;
let activePackingDoc = null;
let activeReceptionDoc = null;
let activeTransferDoc = null;

// --- PICKING ---
async function loadPickingMailbox() {
    const container = document.getElementById('pickingListContainer');
    if (!container) return;
    container.innerHTML = '<div class="text-center py-4"><div class="spinner-border text-primary"></div></div>';

    try {
        const orders = await fetchAPI('/api/picking/orders');
        if (!orders || orders.length === 0) {
            container.innerHTML = '<div class="alert alert-info text-center">No hay pedidos pendientes de picking.</div>';
            return;
        }

        container.innerHTML = orders.map(o => `
            <div class="card mb-3 shadow-sm border-0">
                <div class="card-body d-flex justify-content-between align-items-center">
                    <div>
                        <h5 class="fw-bold mb-1">${o.document_number}</h5>
                        <p class="text-muted mb-0 small">${o.company_name}</p>
                    </div>
                    <button class="btn btn-primary fw-bold" onclick="openPickingOrder('${o.document_number}')">Iniciar ➔</button>
                </div>
            </div>
        `).join('');
    } catch (e) {
        container.innerHTML = '<div class="alert alert-danger text-center">Error al cargar bandeja de picking.</div>';
    }
}

async function openPickingOrder(docNum) {
    activePickingDoc = docNum;
    const viewList = document.getElementById('pickingListView');
    const viewDetail = document.getElementById('pickingDetailView');

    try {
        const data = await fetchAPI(`/api/picking/orders/${docNum}`);
        document.getElementById('pickDocTitle').innerText = data.document.document_number;

        const linesTbody = document.getElementById('pickLinesTbody');
        linesTbody.innerHTML = data.lines.map(l => `
            <tr class="${l.quantity_picked >= l.quantity_requested ? 'table-success' : ''}">
                <td class="fw-bold">${l.sku}<br><small class="text-muted fw-normal">${l.suggested_locations}</small></td>
                <td class="text-end fw-bold fs-5">${l.quantity_picked} / ${l.quantity_requested}</td>
            </tr>
        `).join('');

        if (viewList) viewList.classList.add('d-none');
        if (viewDetail) viewDetail.classList.remove('d-none');
    } catch (e) {}
}

async function submitPickingScan(event) {
    if (event) event.preventDefault();
    if (!activePickingDoc) return;

    const sku = document.getElementById('pickInputSku').value.trim();
    const loc = document.getElementById('pickInputLoc').value.trim();
    const qty = parseFloat(document.getElementById('pickInputQty').value) || 1;

    if (!sku || !loc) {
        showToast("Escanee el SKU y la ubicación.", "danger");
        return;
    }

    try {
        const res = await fetchAPI(`/api/picking/orders/${activePickingDoc}/scan`, {
            method: 'POST',
            body: { sku: sku, quantity: qty, location_code: loc }
        });

        showToast(res.message || "Ítem registrado.", "success");
        document.getElementById('pickInputSku').value = '';
        document.getElementById('pickInputLoc').value = '';
        document.getElementById('pickInputQty').value = '1';

        if (res.order_completed) {
            showToast("🎉 ¡Pedido completado!", "success");
            closePickingDetail();
            loadPickingMailbox();
        } else {
            openPickingOrder(activePickingDoc);
        }
    } catch (e) {}
}

function closePickingDetail() {
    activePickingDoc = null;
    const viewList = document.getElementById('pickingListView');
    const viewDetail = document.getElementById('pickingDetailView');
    if (viewList) viewList.classList.remove('d-none');
    if (viewDetail) viewDetail.classList.add('d-none');
}

// --- PACKING ---
async function loadPackingMailbox() {
    const container = document.getElementById('packingListContainer');
    if (!container) return;
    container.innerHTML = '<div class="text-center py-4"><div class="spinner-border text-primary"></div></div>';

    try {
        const orders = await fetchAPI('/api/packing/orders');
        if (!orders || orders.length === 0) {
            container.innerHTML = '<div class="alert alert-info text-center">No hay pedidos listos para empaque.</div>';
            return;
        }

        container.innerHTML = orders.map(o => `
            <div class="card mb-3 shadow-sm border-0">
                <div class="card-body d-flex justify-content-between align-items-center">
                    <div>
                        <h5 class="fw-bold mb-1">${o.document_number}</h5>
                        <p class="text-muted mb-0 small">${o.company_name}</p>
                    </div>
                    <button class="btn btn-success fw-bold" onclick="openPackingOrder('${o.document_number}')">Empacar 📦</button>
                </div>
            </div>
        `).join('');
    } catch (e) {
        container.innerHTML = '<div class="alert alert-danger text-center">Error al cargar bandeja de packing.</div>';
    }
}

async function openPackingOrder(docNum) {
    activePackingDoc = docNum;
    try {
        const data = await fetchAPI(`/api/packing/orders/${docNum}`);
        document.getElementById('packModalTitle').innerText = `Empacar Pedido: ${data.document.document_number}`;
        document.getElementById('packClientName').innerText = data.document.company_name;
        document.getElementById('packClientAddress').innerText = data.document.address;
        document.getElementById('packCalcWeight').innerText = `${data.totals.calc_weight} kg`;
        document.getElementById('packCalcVolume').innerText = `${data.totals.calc_volume} m³`;

        const modalEl = document.getElementById('packingModal');
        if (modalEl) new bootstrap.Modal(modalEl).show();
    } catch (e) {}
}

async function submitPackDispatch(event) {
    if (event) event.preventDefault();
    if (!activePackingDoc) return;

    const boxes = parseInt(document.getElementById('packBoxesInput').value) || 1;

    try {
        const res = await fetchAPI(`/api/packing/orders/${activePackingDoc}/pack`, {
            method: 'POST',
            body: { boxes: boxes }
        });

        showToast(res.message || "Pedido despachado.", "success");
        
        const modalEl = document.getElementById('packingModal');
        if (modalEl) {
            const modal = bootstrap.Modal.getInstance(modalEl);
            if (modal) modal.hide();
        }
        loadPackingMailbox();
    } catch (e) {}
}
