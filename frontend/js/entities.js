// === MÓDULO DE CLIENTES, PROVEEDORES Y DIRECCIONES ===

async function legacy_loadEntities() {
    const tbody = document.getElementById('entitiesTableBody');
    if (!tbody) return;
    tbody.innerHTML = '<tr><td colspan="5" class="text-center py-3"><div class="spinner-border spinner-border-sm"></div> Cargando...</td></tr>';

    try {
        const entities = await fetchAPI('/api/admin/entities');
        if (!entities || entities.length === 0) {
            tbody.innerHTML = '<tr><td colspan="5" class="text-center text-muted">No hay clientes ni proveedores registrados.</td></tr>';
            return;
        }

        tbody.innerHTML = entities.map(e => `
            <tr>
                <td class="fw-bold">${e.company_name}</td>
                <td><code>${e.tax_id}</code></td>
                <td>
                    ${e.is_customer ? '<span class="badge bg-primary me-1">Cliente</span>' : ''}
                    ${e.is_supplier ? '<span class="badge bg-warning text-dark">Proveedor</span>' : ''}
                </td>
                <td><small class="text-muted">${e.addresses && e.addresses.length ? e.addresses.length + ' dirección(es)' : 'Sin direcciones'}</small></td>
                <td class="text-end">
                    <button class="btn btn-sm btn-outline-primary me-1" onclick="openEntityAddressesModal('${e.id}', '${e.company_name}')">📍 Direcciones</button>
                    <button class="btn btn-sm btn-outline-dark" onclick="openEditEntityModal('${e.id}', '${e.company_name}', '${e.tax_id}', ${e.is_customer}, ${e.is_supplier})">✏️ Editar</button>
                </td>
            </tr>
        `).join('');
    } catch (e) {
        tbody.innerHTML = '<tr><td colspan="5" class="text-center text-danger">Error al cargar entidades.</td></tr>';
    }
}

async function saveEntityForm(event) {
    if (event) event.preventDefault();
    const entityId = document.getElementById('entityId').value;
    const isEdit = !!entityId;

    const payload = {
        company_name: document.getElementById('entityCompanyName').value.trim(),
        tax_id: document.getElementById('entityTaxId').value.trim(),
        is_customer: document.getElementById('entityIsCustomer').checked,
        is_supplier: document.getElementById('entityIsSupplier').checked
    };

    if (!isEdit) {
        const street = document.getElementById('entityStreet').value.trim();
        if (street) {
            payload.initial_address = {
                address_label: document.getElementById('entityAddressLabel').value.trim() || 'Principal',
                street: street,
                number: document.getElementById('entityNumber').value.trim(),
                zip_code: document.getElementById('entityZipCode').value.trim(),
                city_neighborhood: document.getElementById('entityCity').value.trim(),
                is_default: true
            };
        }
    }

    try {
        const url = isEdit ? `/api/admin/entities/${entityId}` : '/api/admin/entities';
        const method = isEdit ? 'PUT' : 'POST';
        await fetchAPI(url, { method, body: payload });
        
        showToast(`Entidad ${isEdit ? 'actualizada' : 'creada'} con éxito.`, "success");
        
        const modalEl = document.getElementById('entityModal');
        if (modalEl) {
            const modal = bootstrap.Modal.getInstance(modalEl);
            if (modal) modal.hide();
        }
        loadEntities();
    } catch (e) {}
}

function openNewEntityModal() {
    document.getElementById('entityId').value = '';
    document.getElementById('entityCompanyName').value = '';
    document.getElementById('entityTaxId').value = '';
    document.getElementById('entityIsCustomer').checked = true;
    document.getElementById('entityIsSupplier').checked = false;
    
    const addrBlock = document.getElementById('initialAddressContainer');
    if (addrBlock) addrBlock.classList.remove('d-none');

    const modalEl = document.getElementById('entityModal');
    if (modalEl) new bootstrap.Modal(modalEl).show();
}

function openEditEntityModal(id, name, taxId, isCust, isSupp) {
    document.getElementById('entityId').value = id;
    document.getElementById('entityCompanyName').value = name;
    document.getElementById('entityTaxId').value = taxId;
    document.getElementById('entityIsCustomer').checked = isCust;
    document.getElementById('entityIsSupplier').checked = isSupp;

    const addrBlock = document.getElementById('initialAddressContainer');
    if (addrBlock) addrBlock.classList.add('d-none');

    const modalEl = document.getElementById('entityModal');
    if (modalEl) new bootstrap.Modal(modalEl).show();
}

async function openEntityAddressesModal(entityId, entityName) {
    document.getElementById('addressEntityId').value = entityId;
    document.getElementById('addressModalTitle').innerText = `Direcciones de: ${entityName}`;
    await reloadAddressesList(entityId);
    
    const modalEl = document.getElementById('entityAddressesModal');
    if (modalEl) new bootstrap.Modal(modalEl).show();
}

async function reloadAddressesList(entityId) {
    const listDiv = document.getElementById('addressesList');
    if (!listDiv) return;
    listDiv.innerHTML = '<div class="text-center py-2"><div class="spinner-border spinner-border-sm"></div></div>';

    try {
        const addresses = await fetchAPI(`/api/admin/entities/${entityId}/addresses`);
        if (!addresses || addresses.length === 0) {
            listDiv.innerHTML = '<p class="text-muted small">Sin direcciones registradas.</p>';
            return;
        }

        listDiv.innerHTML = addresses.map(a => `
            <div class="card mb-2 p-2 border">
                <div class="d-flex justify-content-between align-items-center">
                    <div>
                        <strong class="small">${a.address_label}</strong> ${a.is_default ? '<span class="badge bg-success" style="font-size: 10px;">Principal</span>' : ''}
                        <div class="text-muted small">${a.full_address}</div>
                    </div>
                    <button class="btn btn-sm btn-outline-danger" onclick="deleteAddress('${a.id}', '${entityId}')">🗑️</button>
                </div>
            </div>
        `).join('');
    } catch (e) {
        listDiv.innerHTML = '<p class="text-danger small">Error al cargar direcciones.</p>';
    }
}

async function saveAddressForm(event) {
    if (event) event.preventDefault();
    const entityId = document.getElementById('addressEntityId').value;
    
    const payload = {
        address_label: document.getElementById('addrLabel').value.trim() || 'Sucursal',
        street: document.getElementById('addrStreet').value.trim(),
        number: document.getElementById('addrNumber').value.trim(),
        zip_code: document.getElementById('addrZip').value.trim(),
        city_neighborhood: document.getElementById('addrCity').value.trim(),
        is_default: document.getElementById('addrIsDefault').checked
    };

    try {
        await fetchAPI(`/api/admin/entities/${entityId}/addresses`, { method: 'POST', body: payload });
        showToast("Dirección agregada.", "success");
        document.getElementById('addressForm').reset();
        reloadAddressesList(entityId);
    } catch (e) {}
}

async function deleteAddress(addressId, entityId) {
    if (!confirm("¿Eliminar esta dirección?")) return;
    try {
        await fetchAPI(`/api/admin/addresses/${addressId}`, { method: 'DELETE' });
        showToast("Dirección eliminada.", "success");
        reloadAddressesList(entityId);
    } catch (e) {}
}
