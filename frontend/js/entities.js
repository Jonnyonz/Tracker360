// === MÓDULO DE CLIENTES, PROVEEDORES Y DIRECCIONES (SOBERANO) ===

async function loadEntities() {
    const ents = await fetchAPI('/api/admin/entities'); 
    if(!ents) return; 
    cachedEntities = ents;
    const body = document.getElementById('table-entities-body'); 
    if(!body) return; 
    body.innerHTML = '';
    
    if(ents.length === 0) {
        body.innerHTML = '<tr><td colspan="6" style="text-align:center; color:var(--text-muted); padding:1.5rem;">No hay clientes ni proveedores registrados.</td></tr>';
        return;
    }

    ents.forEach(e => {
        let addrs = []; 
        try { addrs = typeof e.addresses === 'string' ? JSON.parse(e.addresses) : (e.addresses || []); } catch(err) {}
        
        const addrSummary = addrs.length > 0 
            ? addrs.map(a => `<small><strong>${escapeHTML(a.address_label || a.label)}${a.is_default ? ' (Principal)' : ''}:</strong> ${escapeHTML(a.full_address || a.address)}</small>`).join('<br>') 
            : '<span style="color:var(--text-muted)">Sin direcciones</span>';
            
        const roles = [];
        if(e.is_customer) roles.push('CLIENTE');
        if(e.is_supplier) roles.push('PROVEEDOR');
        const roleBadge = roles.length > 0 ? `<span class="badge badge-info">${roles.join(' / ')}</span>` : '<span class="badge badge-neutral">-</span>';

        body.innerHTML += `<tr>
            <td style="font-weight:bold;">${escapeHTML(e.tax_id)}</td>
            <td style="color:var(--accent); font-weight:bold;">${escapeHTML(e.company_name)}</td>
            <td>${roleBadge}</td>
            <td>${addrSummary}</td>
            <td><span class="badge ${e.is_active!==false?'badge-success':'badge-neutral'}">${e.is_active!==false?'ACTIVO':'INACTIVO'}</span></td>
            <td><button onclick="openEditEntityModal('${e.id}', '${escapeHTML(e.tax_id)}', '${escapeHTML(e.company_name)}', ${e.is_customer}, ${e.is_supplier}, ${e.is_active!==false})" class="btn-secondary" style="padding:4px 8px; font-size:0.8rem;">Editar / Direcciones</button></td>
        </tr>`;
    });
}

function openEntityModal() { 
    const form = document.getElementById('form-entity');
    if(form) form.reset(); 
    openModal('modal-entity'); 
}

async function saveEntity(e) {
    e.preventDefault(); 
    const addrLabel = document.getElementById('ent-addr-label').value.trim(); 
    const street = document.getElementById('ent-street').value.trim();
    const number = document.getElementById('ent-number').value.trim();
    const zip = document.getElementById('ent-zip').value.trim();
    const city = document.getElementById('ent-city').value.trim();

    const initialAddr = (street || addrLabel || city) ? {
        address_label: addrLabel || 'Principal',
        street: street || null,
        number: number || null,
        zip_code: zip || null,
        city_neighborhood: city || null,
        is_default: true
    } : null;

    const payload = { 
        tax_id: document.getElementById('ent-taxid').value.trim(), 
        company_name: document.getElementById('ent-name').value.trim(), 
        is_customer: document.getElementById('ent-is-customer').checked, 
        is_supplier: document.getElementById('ent-is-supplier').checked, 
        initial_address: initialAddr 
    };
    
    const r = await fetchAPI('/api/admin/entities', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload) });
    if(r) { 
        showToast('Entidad registrada exitosamente.', 'success'); 
        closeModal('modal-entity'); 
        loadEntities(); 
    }
}

async function openEditEntityModal(id, taxId, name, isCustomer, isSupplier, isActive) {
    document.getElementById('edit-ent-id').value = id; 
    document.getElementById('edit-ent-taxid').value = taxId; 
    document.getElementById('edit-ent-name').value = name;
    document.getElementById('edit-ent-is-customer').checked = isCustomer; 
    document.getElementById('edit-ent-is-supplier').checked = isSupplier; 
    document.getElementById('edit-ent-is-active').checked = isActive;
    
    resetAddressForm();
    openModal('modal-edit-entity'); 
    await loadEntityAddresses(id);
}

async function saveEntityBasic(e) {
    e.preventDefault(); 
    const entId = document.getElementById('edit-ent-id').value;
    const payload = { 
        tax_id: document.getElementById('edit-ent-taxid').value.trim(), 
        company_name: document.getElementById('edit-ent-name').value.trim(), 
        is_customer: document.getElementById('edit-ent-is-customer').checked, 
        is_supplier: document.getElementById('edit-ent-is-supplier').checked, 
        is_active: document.getElementById('edit-ent-is-active').checked 
    };
    const r = await fetchAPI(`/api/admin/entities/${entId}`, { method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload) });
    if(r) { 
        showToast('Entidad actualizada correctamente.', 'success'); 
        loadEntities(); 
    }
}

async function loadEntityAddresses(entId) {
    const list = document.getElementById('entity-addresses-list'); 
    if(!list) return;
    list.innerHTML = 'Cargando direcciones...';
    
    const addrs = await fetchAPI(`/api/admin/entities/${entId}/addresses`);
    if(!addrs || addrs.length === 0) { 
        cachedAddressesCurrentEntity = [];
        list.innerHTML = '<p style="color:var(--text-muted); font-size:0.85rem;">No hay direcciones registradas.</p>'; 
        return; 
    }
    
    cachedAddressesCurrentEntity = addrs;
    let html = '<ul style="list-style:none;">';
    addrs.forEach(a => { 
        const defaultBadge = a.is_default ? '<span class="badge badge-success" style="font-size:0.65rem; margin-left:4px;">PRINCIPAL</span>' : '';
        html += `<li style="display:flex; justify-content:space-between; align-items:center; padding:8px 0; border-bottom:1px solid var(--border);">
            <div>
                <strong style="color:var(--accent);">${escapeHTML(a.address_label || a.label)}:</strong> ${escapeHTML(a.full_address || a.address)} ${defaultBadge}
            </div>
            <div style="display:flex; gap:6px;">
                <button type="button" onclick="editEntityAddress('${a.id}')" class="btn-secondary" style="padding:2px 6px; font-size:0.75rem;">Editar</button>
                <button type="button" onclick="deleteEntityAddress('${a.id}', '${entId}')" class="btn-danger" style="padding:2px 6px; font-size:0.75rem;">Eliminar</button>
            </div>
        </li>`; 
    });
    html += '</ul>'; 
    list.innerHTML = html;
}

function editEntityAddress(addrId) {
    const addr = cachedAddressesCurrentEntity.find(a => String(a.id) === String(addrId));
    if(!addr) return;
    document.getElementById('edit-addr-id').value = addr.id;
    document.getElementById('new-ent-addr-label').value = addr.address_label || addr.label || '';
    document.getElementById('new-ent-street').value = addr.street || '';
    document.getElementById('new-ent-number').value = addr.number || '';
    document.getElementById('new-ent-zip').value = addr.zip_code || '';
    document.getElementById('new-ent-city').value = addr.city_neighborhood || '';
    document.getElementById('new-ent-is-default').checked = !!addr.is_default;

    document.getElementById('form-addr-title').textContent = 'Editar Dirección Existente';
    document.getElementById('btn-save-addr').textContent = 'Actualizar Dirección';
    document.getElementById('btn-cancel-edit-addr').style.display = 'inline-block';
}

function resetAddressForm() {
    const form = document.getElementById('form-entity-address');
    if(form) form.reset();
    document.getElementById('edit-addr-id').value = '';
    document.getElementById('form-addr-title').textContent = '+ Añadir Nueva Dirección';
    document.getElementById('btn-save-addr').textContent = 'Guardar Dirección';
    document.getElementById('btn-cancel-edit-addr').style.display = 'none';
}

async function saveAddressToEntity(e) {
    e.preventDefault(); 
    const entId = document.getElementById('edit-ent-id').value; 
    const addrId = document.getElementById('edit-addr-id').value;

    const payload = {
        entity_id: entId,
        address_label: document.getElementById('new-ent-addr-label').value.trim(),
        street: document.getElementById('new-ent-street').value.trim() || null,
        number: document.getElementById('new-ent-number').value.trim() || null,
        zip_code: document.getElementById('new-ent-zip').value.trim() || null,
        city_neighborhood: document.getElementById('new-ent-city').value.trim() || null,
        is_default: document.getElementById('new-ent-is-default').checked
    };

    let r = null;
    if (addrId) {
        r = await fetchAPI(`/api/admin/addresses/${addrId}`, { method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload) });
    } else {
        r = await fetchAPI(`/api/admin/entities/${entId}/addresses`, { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload) });
    }

    if(r) { 
        showToast(addrId ? 'Dirección actualizada.' : 'Dirección guardada.', 'success'); 
        resetAddressForm(); 
        loadEntityAddresses(entId); 
        loadEntities(); 
    }
}

async function deleteEntityAddress(addrId, entId) {
    if(!confirm("¿Desea eliminar esta dirección?")) return;
    const r = await fetchAPI(`/api/admin/addresses/${addrId}`, { method: 'DELETE' }); 
    if(r) { 
        showToast('Dirección eliminada correctamente.', 'success');
        loadEntityAddresses(entId); 
        loadEntities(); 
    }
}

window.loadEntities = loadEntities;
window.openEntityModal = openEntityModal;
window.saveEntity = saveEntity;
window.openEditEntityModal = openEditEntityModal;
window.saveEntityBasic = saveEntityBasic;
window.loadEntityAddresses = loadEntityAddresses;
window.editEntityAddress = editEntityAddress;
window.resetAddressForm = resetAddressForm;
window.saveAddressToEntity = saveAddressToEntity;
window.deleteEntityAddress = deleteEntityAddress;