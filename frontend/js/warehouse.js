// === MÓDULO DE DEPÓSITO (SUCURSALES, SECTORES Y UBICACIONES - SOBERANO) ===

async function loadWarehouseData() {
    const [branches, sectors, locations] = await Promise.all([ 
        fetchAPI('/api/admin/branches'), 
        fetchAPI('/api/admin/sectors'), 
        fetchAPI('/api/admin/locations') 
    ]);

    if (branches) {
        cachedBranches = branches;
const sBranchSelect = document.getElementById('sector-branch'); 
        if(sBranchSelect) {
            sBranchSelect.innerHTML = '<option value="">-- Seleccionar Sucursal --</option>' + branches.map(b => `<option value="${escapeHTML(b.id)}">${escapeHTML(b.name)} (${escapeHTML(b.code)})</option>`).join('');
        }
    }

    if (sectors) {
        cachedSectors = sectors;
const locSecSelect = document.getElementById('loc-sector'); 
        const impSecSelect = document.getElementById('import-loc-sector');
        const optionsHtml = '<option value="">-- Seleccionar Sector --</option>' + sectors.map(s => `<option value="${escapeHTML(s.id)}">${escapeHTML(s.branch_name || 'Sin Sucursal')} > ${escapeHTML(s.name)}</option>`).join('');
        if(locSecSelect) locSecSelect.innerHTML = optionsHtml; 
        if(impSecSelect) impSecSelect.innerHTML = optionsHtml;
    }

    if (locations) {
        cachedLocations = locations;
    }

    await pintarSucursales();
    // Si el detalle de una sucursal esta abierto (por ejemplo al crear un sector desde ahi), se actualiza.
    if (sucursalSectoresId && document.getElementById('modal-sucursal-sectores')?.style.display === 'flex') pintarSectoresSucursal();
}

// === DEPOSITOS: se listan solo las sucursales; cada una abre su configuracion o sus sectores ===
let sucursalSectoresId = null;
let ubicacionesSectorNombre = null;
const UBICACIONES_VISIBLES = 300;

function plural(n, uno, varios) { return `${n} ${n === 1 ? uno : varios}`; }

function direccionSucursal(b) {
    const calle = [b.street, b.number].filter(Boolean).join(' ');
    return [calle, b.city].filter(Boolean).join(', ') || b.full_address || '';
}

function sectoresDeSucursal(id) {
    return (cachedSectors || []).filter(s => String(s.branch_id) === String(id));
}

function ubicacionesDeSector(nombre) {
    // /api/admin/locations trae el nombre del sector (unico), no su id.
    return (cachedLocations || []).filter(l => l.sector_name === nombre);
}

async function pintarSucursales() {
    const caja = document.getElementById('sucursales-lista');
    if (!caja) return;
    const sucursales = cachedBranches || [];
    if (sucursales.length === 0) {
        caja.innerHTML = '<p class="canales-vacio">Sin sucursales registradas. Creá la primera con "+ Crear Sucursal".</p>';
        return;
    }
    const soloConsulta = await esSupervisor();
    const estilo = 'padding:3px 8px; font-size:0.75rem;';
    caja.innerHTML = sucursales.map(b => {
        const sectores = sectoresDeSucursal(b.id);
        const ubicaciones = sectores.reduce((n, s) => n + ubicacionesDeSector(s.name).length, 0);
        const activa = b.is_active !== false;
        return `
        <div class="canal-linea">
            <div class="canal-linea-datos">
                <div><strong>${escapeHTML(b.name)}</strong> <span class="font-mono canal-linea-codigo">${escapeHTML(b.code)}</span>
                    <span class="badge ${activa ? 'badge-success' : 'badge-neutral'}">${activa ? 'ACTIVA' : 'INACTIVA'}</span></div>
                <small>${escapeHTML(direccionSucursal(b) || 'Sin dirección')} · ${plural(sectores.length, 'sector', 'sectores')} · ${plural(ubicaciones, 'ubicación', 'ubicaciones')}</small>
            </div>
            <div class="canal-linea-botones">
                ${soloConsulta ? '' : `<button type="button" class="btn-secondary" style="${estilo}" data-on-click="openEditBranch(${jsArg(b.id)})">Configuración</button>`}
                <button type="button" class="btn-secondary" style="${estilo} margin-left:6px;" data-on-click="abrirSectoresSucursal(${jsArg(b.id)})">Sectores</button>
            </div>
        </div>`;
    }).join('');
}

function abrirSectoresSucursal(id) {
    sucursalSectoresId = id;
    ubicacionesSectorNombre = null;
    pintarSectoresSucursal();
    openModal('modal-sucursal-sectores');
}

function cerrarSectoresSucursal() {
    sucursalSectoresId = null;
    ubicacionesSectorNombre = null;
    closeModal('modal-sucursal-sectores');
}

async function pintarSectoresSucursal() {
    const b = (cachedBranches || []).find(x => String(x.id) === String(sucursalSectoresId));
    if (!b) return;
    const soloConsulta = await esSupervisor();
    document.getElementById('sucursal-sectores-titulo').textContent = `Sectores de ${b.name}`;
    document.getElementById('sucursal-sectores-nuevo').style.display = soloConsulta ? 'none' : '';
    document.getElementById('sector-ubicaciones-nueva').style.display = soloConsulta ? 'none' : '';
    document.getElementById('sector-ubicaciones-importar').style.display = soloConsulta ? 'none' : '';
    const sectores = sectoresDeSucursal(b.id);
    const body = document.getElementById('sucursal-sectores-body');
    body.innerHTML = sectores.length === 0
        ? '<tr><td colspan="4" class="busqueda-vacia">Esta sucursal todavía no tiene sectores.</td></tr>'
        : sectores.map(s => `
            <tr>
                <td style="font-weight:bold;">${escapeHTML(s.name)}</td>
                <td><span class="badge badge-info">${escapeHTML(s.print_queue_code)}</span></td>
                <td><span class="badge badge-neutral">${s.uses_locations ? 'SÍ' : 'NO'}</span></td>
                <td style="text-align:right;"><button type="button" class="btn-secondary" style="padding:3px 8px; font-size:0.75rem;" data-on-click="verUbicacionesSector(${jsArg(s.name)})">Ver ubicaciones (${ubicacionesDeSector(s.name).length})</button></td>
            </tr>`).join('');
    if (ubicacionesSectorNombre && !sectores.some(s => s.name === ubicacionesSectorNombre)) ubicacionesSectorNombre = null;
    pintarUbicacionesSector();
}

function verUbicacionesSector(nombre) {
    ubicacionesSectorNombre = nombre;
    const filtro = document.getElementById('sector-ubicaciones-filtro');
    if (filtro) filtro.value = '';
    pintarUbicacionesSector();
    document.getElementById('sector-ubicaciones')?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function pintarUbicacionesSector() {
    const panel = document.getElementById('sector-ubicaciones');
    if (!panel) return;
    panel.hidden = !ubicacionesSectorNombre;
    if (!ubicacionesSectorNombre) return;
    const filtro = (document.getElementById('sector-ubicaciones-filtro')?.value || '').trim().toLowerCase();
    const todas = ubicacionesDeSector(ubicacionesSectorNombre);
    const lista = todas.filter(l => !filtro || l.location_code.toLowerCase().includes(filtro) || (l.description || '').toLowerCase().includes(filtro));
    document.getElementById('sector-ubicaciones-titulo').textContent = `Ubicaciones de ${ubicacionesSectorNombre} (${todas.length})`;
    const caja = document.getElementById('sector-ubicaciones-lista');
    if (lista.length === 0) {
        caja.innerHTML = `<p class="canales-vacio">${todas.length === 0 ? 'Este sector no tiene ubicaciones.' : 'Ninguna ubicación coincide.'}</p>`;
        return;
    }
    caja.innerHTML = lista.slice(0, UBICACIONES_VISIBLES).map(l =>
        `<span class="ubicacion-chip font-mono" title="${escapeHTML(l.description || '')}">${escapeHTML(l.location_code)}</span>`).join('')
        + (lista.length > UBICACIONES_VISIBLES ? `<p class="busqueda-resumen" style="width:100%;">Se muestran ${UBICACIONES_VISIBLES} de ${lista.length}: buscá para encontrar el resto.</p>` : '');
}

function nuevoSectorEnSucursal() {
    const sel = document.getElementById('sector-branch');
    if (sel) sel.value = sucursalSectoresId || '';
    openModal('modal-sector');
}

function nuevaUbicacionEnSector() {
    const sector = (cachedSectors || []).find(s => s.name === ubicacionesSectorNombre);
    const sel = document.getElementById('loc-sector');
    if (sel && sector) sel.value = sector.id;
    openModal('modal-location');
}

// Importar ubicaciones a un sector (Excel, texto con tabulaciones o CSV: columnas ubicacion y descripcion).
function abrirImportarUbicaciones(sectorId) {
    document.getElementById('form-import-locations').reset();
    const sel = document.getElementById('import-loc-sector');
    if (sel) sel.value = sectorId || '';
    openModal('modal-import-locations');
}

function importarUbicacionesEnSector() {
    const sector = (cachedSectors || []).find(s => s.name === ubicacionesSectorNombre);
    abrirImportarUbicaciones(sector ? sector.id : '');
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
    if (!sectorId) { showToast('Elegí el sector.', 'warning'); return; }
    if (!fileInput.files[0]) { showToast('Elegí el archivo.', 'warning'); return; }
    const formData = new FormData();
    formData.append('file', fileInput.files[0]);
    const btn = document.getElementById('btn-import-loc');
    btn.disabled = true; btn.textContent = 'Importando...';
    try {
        const r = await fetchAPI(`/api/admin/sectors/${encodeURIComponent(sectorId)}/locations/import`, { method: 'POST', body: formData });
        showToast(r.message || 'Ubicaciones importadas.', 'success');
        closeModal('modal-import-locations');
        await loadWarehouseData();
    } catch (err) { /* fetchAPI ya mostro el error */ }
    finally { btn.disabled = false; btn.textContent = 'Importar'; }
}

window.loadWarehouseData = loadWarehouseData;
window.saveBranch = saveBranch;
window.openEditBranch = openEditBranch;
window.resetBranchModal = resetBranchModal;
window.saveSector = saveSector;
window.saveLocation = saveLocation;
window.uploadLocationsCSV = uploadLocationsCSV;
window.abrirImportarUbicaciones = abrirImportarUbicaciones;
window.importarUbicacionesEnSector = importarUbicacionesEnSector;
