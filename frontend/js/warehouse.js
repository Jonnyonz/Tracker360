// === MÓDULO DE DEPÓSITO (SUCURSALES, SECTORES Y UBICACIONES - SOBERANO) ===

async function loadWarehouseData() {
    const [branches, sectors, locations] = await Promise.all([ 
        fetchAPI('/api/admin/branches'), 
        fetchAPI('/api/admin/sectors'), 
        fetchAPI('/api/admin/locations') 
    ]);

    if (branches) {
        cachedBranches = branches; 
        const bBody = document.getElementById('table-branches-body');
        if(bBody) {
            bBody.innerHTML = ''; 
            if(branches.length === 0) {
                bBody.innerHTML = '<tr><td colspan="5" style="color:var(--text-muted); text-align:center;">Sin sucursales registradas.</td></tr>';
            } else {
                const soloConsulta = await esSupervisor();
                branches.forEach(b => {
                    const editar = soloConsulta ? '' : `<button class="btn-secondary" style="padding:4px 8px; font-size:0.8rem;" data-on-click="openEditBranch(${jsArg(b.id)})">Editar</button>`;
                    bBody.innerHTML += `<tr><td style="font-weight:bold;">${escapeHTML(b.code)}</td><td>${escapeHTML(b.name)}</td><td><small>${escapeHTML(b.full_address || '-')}</small></td><td><span class="badge badge-success">ACTIVA</span></td><td>${editar}</td></tr>`;
                });
            }
        }
        const sBranchSelect = document.getElementById('sector-branch'); 
        if(sBranchSelect) {
            sBranchSelect.innerHTML = '<option value="">-- Seleccionar Sucursal --</option>' + branches.map(b => `<option value="${b.id}">${escapeHTML(b.name)} (${escapeHTML(b.code)})</option>`).join('');
        }
    }

    if (sectors) {
        cachedSectors = sectors; 
        const sBody = document.getElementById('table-sectors-body');
        if(sBody) {
            sBody.innerHTML = ''; 
            if(sectors.length === 0) {
                sBody.innerHTML = '<tr><td colspan="4" style="color:var(--text-muted); text-align:center;">Sin sectores registrados.</td></tr>';
            } else {
                sectors.forEach(s => {
                    sBody.innerHTML += `<tr><td>${escapeHTML(s.branch_name||'-')}</td><td style="font-weight:bold;">${escapeHTML(s.name)}</td><td><span class="badge badge-info">${escapeHTML(s.print_queue_code)}</span></td><td><span class="badge badge-neutral">${s.uses_locations ? 'SÍ' : 'NO'}</span></td></tr>`;
                });
            }
        }
        const locSecSelect = document.getElementById('loc-sector'); 
        const impSecSelect = document.getElementById('import-loc-sector');
        const optionsHtml = '<option value="">-- Seleccionar Sector --</option>' + sectors.map(s => `<option value="${s.id}">${escapeHTML(s.branch_name || 'Sin Sucursal')} > ${escapeHTML(s.name)}</option>`).join('');
        if(locSecSelect) locSecSelect.innerHTML = optionsHtml; 
        if(impSecSelect) impSecSelect.innerHTML = optionsHtml;
    }

    if (locations) {
        cachedLocations = locations; 
        const lBody = document.getElementById('table-locations-body');
        if(lBody) {
            lBody.innerHTML = ''; 
            if(locations.length === 0) {
                lBody.innerHTML = '<tr><td colspan="5" style="color:var(--text-muted); text-align:center;">Sin ubicaciones registradas.</td></tr>';
            } else {
                locations.forEach(l => {
                    lBody.innerHTML += `<tr><td>${escapeHTML(l.branch_name||'-')}</td><td>${escapeHTML(l.sector_name||'-')}</td><td style="font-weight:bold; color:var(--accent);">${escapeHTML(l.location_code)}</td><td>${escapeHTML(l.description||'-')}</td><td><span class="badge badge-success">ACTIVA</span></td></tr>`;
                });
            }
        }
    }
}

function resetBranchModal() {
    document.getElementById('form-branch').reset();
    document.getElementById('branch-id').value = '';
    document.getElementById('branch-code').disabled = false;
    document.getElementById('branch-modal-title').textContent = 'Crear Sucursal';
}

function openEditBranch(branchId) {
    const b = (cachedBranches || []).find(x => x.id === branchId);
    if (!b) return;
    resetBranchModal();
    document.getElementById('branch-modal-title').textContent = 'Editar Sucursal';
    document.getElementById('branch-id').value = b.id;
    document.getElementById('branch-code').value = b.code;
    document.getElementById('branch-code').disabled = true;
    document.getElementById('branch-name').value = b.name;
    document.getElementById('branch-street').value = b.street || '';
    document.getElementById('branch-number').value = b.number || '';
    document.getElementById('branch-city').value = b.city || '';
    document.getElementById('branch-zip').value = b.zip_code || '';
    openModal('modal-branch');
}

async function saveBranch(e) {
    e.preventDefault();
    const id = document.getElementById('branch-id').value;
    const payload = {
        name: document.getElementById('branch-name').value.trim(),
        street: document.getElementById('branch-street').value.trim(),
        number: document.getElementById('branch-number').value.trim(),
        city: document.getElementById('branch-city').value.trim(),
        zip_code: document.getElementById('branch-zip').value.trim()
    };
    if (!id) payload.code = document.getElementById('branch-code').value.trim();
    const url = id ? `/api/admin/branches/${encodeURIComponent(id)}` : '/api/admin/branches';
    const r = await fetchAPI(url, { method: id ? 'PUT' : 'POST', body: payload });
    if(r) {
        showToast(id ? 'Sucursal actualizada.' : 'Sucursal creada exitosamente.', 'success');
        closeModal('modal-branch');
        resetBranchModal();
        loadWarehouseData();
    }
}

async function saveSector(e) { 
    e.preventDefault(); 
    const payload = { 
        branch_id: document.getElementById('sector-branch').value, 
        name: document.getElementById('sector-name').value.trim(), 
        print_queue_code: document.getElementById('sector-print-code').value.trim(), 
        uses_locations: document.getElementById('sector-uses-locations').checked 
    }; 
    if(!payload.branch_id) {
        showToast('Debe seleccionar una sucursal.', 'error');
        return;
    }
    const r = await fetchAPI('/api/admin/sectors', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload) }); 
    if(r) { 
        showToast('Sector creado exitosamente.', 'success'); 
        closeModal('modal-sector'); 
        document.getElementById('form-sector').reset();
        loadWarehouseData(); 
    } 
}

async function saveLocation(e) { 
    e.preventDefault(); 
    const payload = { 
        sector_id: document.getElementById('loc-sector').value, 
        location_code: document.getElementById('loc-code').value.trim(), 
        description: document.getElementById('loc-desc').value.trim() || null 
    }; 
    if(!payload.sector_id) {
        showToast('Debe seleccionar un sector.', 'error');
        return;
    }
    const r = await fetchAPI('/api/admin/locations', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload) }); 
    if(r) { 
        showToast('Ubicación registrada exitosamente.', 'success'); 
        closeModal('modal-location'); 
        document.getElementById('form-location').reset();
        loadWarehouseData(); 
    } 
}

async function uploadLocationsCSV(e) { 
    e.preventDefault(); 
    const sectorId = document.getElementById('import-loc-sector').value; 
    const fileInput = document.getElementById('import-loc-file'); 
    if(!fileInput.files[0]) { 
        showToast('Seleccione un archivo CSV.', 'warning'); 
        return; 
    } 
    const formData = new FormData(); 
    formData.append('file', fileInput.files[0]); 
    const r = await fetchAPI(`/api/admin/sectors/${sectorId}/locations/import`, { method: 'POST', body: formData }); 
    if(r) { 
        showToast(r.message || 'Ubicaciones importadas correctamente.', 'success'); 
        closeModal('modal-import-locations'); 
        loadWarehouseData(); 
    } 
}

window.loadWarehouseData = loadWarehouseData;
window.saveBranch = saveBranch;
window.openEditBranch = openEditBranch;
window.resetBranchModal = resetBranchModal;
window.saveSector = saveSector;
window.saveLocation = saveLocation;
window.uploadLocationsCSV = uploadLocationsCSV;