// === MÓDULO DE DEPÓSITO (SUCURSALES, SECTORES Y UBICACIONES) ===

async function legacy_loadBranches() {
    const tbody = document.getElementById('branchesTableBody');
    if (!tbody) return;
    tbody.innerHTML = '<tr><td colspan="3" class="text-center py-3"><div class="spinner-border spinner-border-sm"></div> Cargando sucursales...</td></tr>';

    try {
        const branches = await fetchAPI('/api/admin/branches');
        
        // Actualizar selects globales de sucursales
        const selects = document.querySelectorAll('.branch-select-populate');
        selects.forEach(sel => {
            sel.innerHTML = '<option value="">-- Seleccionar Sucursal --</option>' + 
                branches.map(b => `<option value="${b.id}">${b.name} (${b.code})</option>`).join('');
        });

        if (!branches || branches.length === 0) {
            tbody.innerHTML = '<tr><td colspan="3" class="text-center text-muted">No hay sucursales registradas.</td></tr>';
            return;
        }

        tbody.innerHTML = branches.map(b => `
            <tr>
                <td class="fw-bold"><code>${b.code}</code></td>
                <td>${b.name}</td>
                <td><span class="badge ${b.is_active ? 'bg-success' : 'bg-secondary'}">${b.is_active ? 'Activa' : 'Inactiva'}</span></td>
            </tr>
        `).join('');
    } catch (e) {
        tbody.innerHTML = '<tr><td colspan="3" class="text-center text-danger">Error al cargar sucursales.</td></tr>';
    }
}

async function saveBranchForm(event) {
    if (event) event.preventDefault();
    const payload = {
        code: document.getElementById('branchCode').value.trim(),
        name: document.getElementById('branchName').value.trim()
    };

    try {
        await fetchAPI('/api/admin/branches', { method: 'POST', body: payload });
        showToast("Sucursal creada con éxito.", "success");
        
        const modalEl = document.getElementById('branchModal');
        if (modalEl) {
            const modal = bootstrap.Modal.getInstance(modalEl);
            if (modal) modal.hide();
        }
        loadBranches();
    } catch (e) {}
}

async function legacy_loadSectors() {
    const tbody = document.getElementById('sectorsTableBody');
    if (!tbody) return;
    tbody.innerHTML = '<tr><td colspan="5" class="text-center py-3"><div class="spinner-border spinner-border-sm"></div> Cargando sectores...</td></tr>';

    try {
        const sectors = await fetchAPI('/api/admin/sectors');
        
        // Actualizar selects globales de sectores
        const selects = document.querySelectorAll('.sector-select-populate');
        selects.forEach(sel => {
            sel.innerHTML = '<option value="">-- Seleccionar Sector --</option>' + 
                sectors.map(s => `<option value="${s.id}">${s.name} (${s.branch_name || 'Sin Sucursal'})</option>`).join('');
        });

        if (!sectors || sectors.length === 0) {
            tbody.innerHTML = '<tr><td colspan="5" class="text-center text-muted">No hay sectores registrados.</td></tr>';
            return;
        }

        tbody.innerHTML = sectors.map(s => `
            <tr>
                <td class="fw-bold">${s.name}</td>
                <td><code>${s.print_queue_code}</code></td>
                <td>${s.branch_name || 'Sin Sucursal'}</td>
                <td><span class="badge ${s.uses_locations ? 'bg-info text-dark' : 'bg-secondary'}">${s.uses_locations ? 'Usa Ubicaciones' : 'Sector Plano'}</span></td>
                <td class="text-end">
                    ${s.uses_locations ? `<button class="btn btn-sm btn-outline-primary" onclick="openImportLocationsModal('${s.id}', '${s.name}')">📤 Importar Ubicaciones</button>` : ''}
                </td>
            </tr>
        `).join('');
    } catch (e) {
        tbody.innerHTML = '<tr><td colspan="5" class="text-center text-danger">Error al cargar sectores.</td></tr>';
    }
}

async function saveSectorForm(event) {
    if (event) event.preventDefault();
    const payload = {
        name: document.getElementById('sectorName').value.trim(),
        print_queue_code: document.getElementById('sectorPrintQueue').value.trim(),
        uses_locations: document.getElementById('sectorUsesLocations').checked,
        branch_id: document.getElementById('sectorBranchId').value
    };

    if (!payload.branch_id) {
        showToast("Debe seleccionar una sucursal para el sector.", "danger");
        return;
    }

    try {
        await fetchAPI('/api/admin/sectors', { method: 'POST', body: payload });
        showToast("Sector creado con éxito.", "success");
        
        const modalEl = document.getElementById('sectorModal');
        if (modalEl) {
            const modal = bootstrap.Modal.getInstance(modalEl);
            if (modal) modal.hide();
        }
        loadSectors();
    } catch (e) {}
}

async function legacy_loadLocations() {
    const tbody = document.getElementById('locationsTableBody');
    if (!tbody) return;
    tbody.innerHTML = '<tr><td colspan="4" class="text-center py-3"><div class="spinner-border spinner-border-sm"></div> Cargando ubicaciones...</td></tr>';

    try {
        const locations = await fetchAPI('/api/admin/locations');
        if (!locations || locations.length === 0) {
            tbody.innerHTML = '<tr><td colspan="4" class="text-center text-muted">No hay ubicaciones registradas.</td></tr>';
            return;
        }

        tbody.innerHTML = locations.map(l => `
            <tr>
                <td class="fw-bold text-primary"><code>${l.location_code}</code></td>
                <td>${l.description || '-'}</td>
                <td>${l.sector_name || 'Sin sector'}</td>
                <td>${l.branch_name || 'Sin sucursal'}</td>
            </tr>
        `).join('');
    } catch (e) {
        tbody.innerHTML = '<tr><td colspan="4" class="text-center text-danger">Error al cargar ubicaciones.</td></tr>';
    }
}

async function saveLocationForm(event) {
    if (event) event.preventDefault();
    const payload = {
        sector_id: document.getElementById('locationSectorId').value,
        location_code: document.getElementById('locationCode').value.trim(),
        description: document.getElementById('locationDesc').value.trim()
    };

    if (!payload.sector_id) {
        showToast("Debe seleccionar un sector.", "danger");
        return;
    }

    try {
        await fetchAPI('/api/admin/locations', { method: 'POST', body: payload });
        showToast("Ubicación creada con éxito.", "success");
        
        const modalEl = document.getElementById('locationModal');
        if (modalEl) {
            const modal = bootstrap.Modal.getInstance(modalEl);
            if (modal) modal.hide();
        }
        loadLocations();
    } catch (e) {}
}

function openImportLocationsModal(sectorId, sectorName) {
    document.getElementById('importSectorIdHidden').value = sectorId;
    document.getElementById('importSectorNameTitle').innerText = sectorName;
    const modalEl = document.getElementById('importLocationsModal');
    if (modalEl) new bootstrap.Modal(modalEl).show();
}

async function submitImportLocationsCSV() {
    const fileInput = document.getElementById('importLocationsFileInput');
    const sectorId = document.getElementById('importSectorIdHidden').value;

    if (!fileInput.files || fileInput.files.length === 0) {
        showToast("Seleccione un archivo CSV.", "danger");
        return;
    }

    const formData = new FormData();
    formData.append('file', fileInput.files[0]);

    try {
        showToast("Importando ubicaciones...", "info");
        const res = await fetch(`/api/admin/sectors/${sectorId}/locations/import`, { method: 'POST', body: formData, credentials: 'include' });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || "Error al importar ubicaciones.");
        showToast(data.message || "Ubicaciones importadas correctamente.", "success");
        
        const modalEl = document.getElementById('importLocationsModal');
        if (modalEl) {
            const modal = bootstrap.Modal.getInstance(modalEl);
            if (modal) modal.hide();
        }
        loadLocations();
    } catch (e) {
        showToast(e.message, "danger");
    } finally {
        fileInput.value = '';
    }
}
