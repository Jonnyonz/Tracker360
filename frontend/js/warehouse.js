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

    stockSectorCache = {};   // el stock pudo cambiar: se vuelve a pedir por sector
    await pintarDepositos();
}

// === DEPOSITOS: tres columnas (design_handoff_tracker360, fase 5) ===
// Sucursales -> sectores de la elegida -> ubicaciones del sector elegido. Cada ubicacion muestra si esta libre o
// que SKU tiene (/api/admin/reports/stock?sector_id=). La API no informa la capacidad de una ubicacion: no hay
// porcentaje de ocupacion.
let sucursalSectoresId = null;        // sucursal elegida
let ubicacionesSectorNombre = null;   // sector elegido (por nombre: /api/admin/locations no trae su id)
let stockSectorCache = {};            // id de sector -> { codigo de ubicacion: [{ sku, quantity }] }
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

function sectorElegido() {
    return (cachedSectors || []).find(s => s.name === ubicacionesSectorNombre);
}

async function pintarDepositos() {
    const cajaSuc = document.getElementById('dep-sucursales');
    if (!cajaSuc) return;
    const soloConsulta = await esSupervisor();
    const sucursales = cachedBranches || [];
    if (!sucursales.some(b => String(b.id) === String(sucursalSectoresId))) sucursalSectoresId = sucursales.length ? sucursales[0].id : null;
    cajaSuc.innerHTML = sucursales.length ? sucursales.map(b => {
        const sectores = sectoresDeSucursal(b.id);
        const ubicaciones = sectores.reduce((n, s) => n + ubicacionesDeSector(s.name).length, 0);
        return `<div class="dep-row ${String(b.id) === String(sucursalSectoresId) ? 'on' : ''}">
            <button type="button" class="dep-main" data-on-click="depSucursal(${jsArg(b.id)})">
                <span class="l1"><b>${escapeHTML(b.name)}</b><span class="mono">${escapeHTML(b.code)}</span></span>
                <span class="l2">${escapeHTML(direccionSucursal(b) || 'Sin dirección')}</span>
                <span class="l3">${plural(sectores.length, 'sector', 'sectores')} · ${plural(ubicaciones, 'ubicación', 'ubicaciones')}${b.is_active !== false ? '' : ' · <span class="badge badge-neutral">Inactiva</span>'}</span>
            </button>
            ${soloConsulta ? '' : `<button type="button" class="btn-link" data-on-click="openEditBranch(${jsArg(b.id)})">Configuración</button>`}
        </div>`;
    }).join('') : '<div class="vacio">Sin sucursales registradas. Creá la primera con "+ Crear Sucursal".</div>';

    const suc = sucursales.find(b => String(b.id) === String(sucursalSectoresId));
    const sectores = suc ? sectoresDeSucursal(suc.id) : [];
    if (!sectores.some(s => s.name === ubicacionesSectorNombre)) ubicacionesSectorNombre = sectores.length ? sectores[0].name : null;
    document.getElementById('dep-sectores-titulo').textContent = suc ? `Sectores · ${suc.name}` : 'Sectores';
    document.getElementById('sucursal-sectores-nuevo').style.display = soloConsulta || !suc ? 'none' : '';
    document.getElementById('dep-sectores').innerHTML = sectores.length ? sectores.map(s => `<div class="dep-row ${s.name === ubicacionesSectorNombre ? 'on' : ''}">
            <button type="button" class="dep-main" data-on-click="depSector(${jsArg(s.name)})">
                <span class="l1"><b>${escapeHTML(s.name)}</b><span class="mono">${ubicacionesDeSector(s.name).length} ubic.</span></span>
                <span class="l2 mono"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 6 2 18 2 18 9"></polyline><path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"></path><rect x="6" y="14" width="12" height="8"></rect></svg>${escapeHTML(s.print_queue_code || 'Sin cola')}${s.uses_locations ? '' : ' · no usa ubicaciones'}</span>
            </button>
        </div>`).join('') : `<div class="vacio">${suc ? 'Esta sucursal todavía no tiene sectores.' : 'Elegí una sucursal.'}</div>`;

    ['sector-ubicaciones-nueva', 'sector-ubicaciones-importar'].forEach(id => {
        document.getElementById(id).style.display = soloConsulta || !ubicacionesSectorNombre ? 'none' : '';
    });
    pintarUbicacionesSector();
    cargarStockSector();
}

function depSucursal(id) { sucursalSectoresId = id; depSector(null); }
function depSector(nombre) {
    ubicacionesSectorNombre = nombre;
    const filtro = document.getElementById('sector-ubicaciones-filtro');
    if (filtro) filtro.value = '';
    pintarDepositos();
}

// Trae una vez por sector que SKU hay en cada ubicacion y repinta la grilla.
async function cargarStockSector() {
    const sector = sectorElegido();
    if (!sector || stockSectorCache[sector.id]) return;
    try {
        const filas = await fetchAPI(`/api/admin/reports/stock?sector_id=${encodeURIComponent(sector.id)}`) || [];
        const mapa = {};
        filas.forEach(f => { (mapa[f.location_code] = mapa[f.location_code] || []).push({ sku: f.sku, quantity: f.quantity }); });
        stockSectorCache[sector.id] = mapa;
    } catch (e) { return; }   // sin el dato, las celdas quedan sin estado (no se muestran como libres)
    pintarUbicacionesSector();
}

function pintarUbicacionesSector() {
    const caja = document.getElementById('dep-ubicaciones');
    if (!caja) return;
    const titulo = document.getElementById('dep-ubis-titulo');
    if (!ubicacionesSectorNombre) {
        titulo.textContent = 'Ubicaciones';
        caja.innerHTML = '<div class="vacio">Elegí un sector para ver sus ubicaciones.</div>';
        return;
    }
    const filtro = (document.getElementById('sector-ubicaciones-filtro')?.value || '').trim().toLowerCase();
    const todas = ubicacionesDeSector(ubicacionesSectorNombre);
    const lista = todas.filter(l => !filtro || l.location_code.toLowerCase().includes(filtro) || (l.description || '').toLowerCase().includes(filtro));
    titulo.textContent = `Ubicaciones · ${ubicacionesSectorNombre} (${todas.length})`;
    if (lista.length === 0) {
        caja.innerHTML = `<div class="vacio">${todas.length === 0 ? 'Este sector no tiene ubicaciones.' : 'Ninguna ubicación coincide.'}</div>`;
        return;
    }
    const sector = sectorElegido(), stock = sector ? stockSectorCache[sector.id] : null;
    caja.innerHTML = `<div class="dep-grid">${lista.slice(0, UBICACIONES_VISIBLES).map(l => {
        const hay = stock ? (stock[l.location_code] || []) : null;
        const detalle = [l.description, hay && hay.map(x => `${x.sku} × ${x.quantity}`).join(', ')].filter(Boolean).join(' · ');
        const texto = !hay ? '' : hay.length ? hay[0].sku + (hay.length > 1 ? ` +${hay.length - 1}` : '') : 'Libre';
        return `<div class="dep-celda ${hay && hay.length ? 'con' : ''}" title="${escapeHTML(detalle)}"><b>${escapeHTML(l.location_code)}</b><span>${escapeHTML(texto)}</span></div>`;
    }).join('')}</div>`
        + (lista.length > UBICACIONES_VISIBLES ? `<p class="busqueda-resumen" style="margin:0 16px 14px;">Se muestran ${UBICACIONES_VISIBLES} de ${lista.length}: buscá para encontrar el resto.</p>` : '');
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
