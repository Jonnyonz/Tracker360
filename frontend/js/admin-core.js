// === NÚCLEO DE LÓGICA FRONTEND (admin-core.js) ===

let cachedUsers = [], cachedBranches = [], cachedSectors = [], cachedEntities = [], cachedLocations = [], suppliersCache = [];
let cachedAddressesCurrentEntity = [];
let currentItemPage = 1, totalItemPages = 1, currentItemLimit = 50, currentItemSort = 'sku', currentItemOrder = 'ASC', currentItemSearchSKU = '', currentItemSearchDesc = '';
let AppConfig = {};

let cachedOrdersList = [];
let cachedLogsList = [];

function escapeHTML(str) {
    if (str === null || str === undefined) return '';
    return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function showToast(msg, type = 'success') {
    const container = document.getElementById('toast-container');
    if(!container) return;
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.innerHTML = `<span>${escapeHTML(msg)}</span><span style="cursor:pointer; font-weight:bold; margin-left:10px;" onclick="this.parentElement.remove()">&times;</span>`;
    container.appendChild(toast);
    setTimeout(() => { if(toast.parentElement) toast.remove(); }, 3500);
}

function setFormVal(id, val) {
    const el = document.getElementById(id);
    if (el && val !== undefined && val !== null) {
        el.value = val;
    }
}

function getFormVal(id) {
    const el = document.getElementById(id);
    return el ? el.value.trim() : '';
}

window.addEventListener('online', () => { const b = document.getElementById('net-banner'); if(b) b.style.display = 'none'; });
window.addEventListener('offline', () => { const b = document.getElementById('net-banner'); if(b) b.style.display = 'block'; });

// === CONTROL DEL ACORDEÓN DE REPORTES ===
function toggleAccordion(id) {
    const acc = document.getElementById(id);
    const icon = document.getElementById('icon-acc-reports');
    const btn = document.getElementById('btn-acc-reports');
    
    if (acc.classList.contains('open')) {
        acc.classList.remove('open');
        icon.textContent = '?';
        btn.classList.remove('active');
    } else {
        acc.classList.add('open');
        icon.textContent = '?';
        document.querySelectorAll('.sidebar > div > .nav-tabs > .tab-btn').forEach(e => e.classList.remove('active'));
        btn.classList.add('active');
    }
}

function switchView(secId, btnElement = null) {
    document.querySelectorAll('.view-section, .tab-btn, .tab-sub-btn').forEach(e => e.classList.remove('active'));
    
    const sec = document.getElementById(secId);
    if (sec) sec.classList.add('active');
    
    if (btnElement) {
        btnElement.classList.add('active');
        if (btnElement.classList.contains('tab-sub-btn')) {
            document.getElementById('btn-acc-reports').classList.add('active');
        }
    } else {
        const b = document.querySelector(`[onclick*="${secId}"]`);
        if(b) {
            b.classList.add('active');
            if (b.classList.contains('tab-sub-btn')) {
                document.getElementById('btn-acc-reports').classList.add('active');
                document.getElementById('acc-reports').classList.add('open');
                document.getElementById('icon-acc-reports').textContent = '?';
            }
        }
    }

    if(secId === 'section-dashboard') loadDashboard();
    if(secId === 'section-users') loadUsers();
    if(secId === 'section-entities') loadEntities();
    if(secId === 'section-warehouse') loadWarehouseData();
    if(secId === 'section-items') loadItems();
    if(secId === 'section-purchases') { loadPurchaseSelectors(); loadAllPurchaseHistories(); }
    if(secId === 'section-orders') loadOrders();
    if(secId === 'section-logs') loadLogs();
    if(secId === 'section-settings' && typeof loadSettings === 'function') { loadSettings(); loadIntegrationChannels(); }
    
    if(secId === 'section-kardex') { if(typeof window.loadKardexSelectors === 'function') window.loadKardexSelectors(); }
    if(secId === 'section-rep-stock') { if(typeof window.loadReportStockSelectors === 'function') window.loadReportStockSelectors(); }
    if(secId === 'section-rep-remitos') { if(typeof window.loadReportRemitosSelectors === 'function') window.loadReportRemitosSelectors(); }
    if(secId === 'section-rep-invoices') { if(typeof window.loadReportInvoicesSelectors === 'function') window.loadReportInvoicesSelectors(); }
    if(secId === 'section-rep-po') { if(typeof window.loadReportPOSelectors === 'function') window.loadReportPOSelectors(); }
}

function navigateToSubTab(sectionId, tabId) { switchView(sectionId); switchPurchaseTab(tabId); }
function openModal(id) { document.getElementById(id).style.display = 'flex'; }
function closeModal(id) { document.getElementById(id).style.display = 'none'; }

async function fetchAPI(url, options = {}) {
    try {
        const r = await fetch(url, options);
        if (r.status === 401) { window.location.href = '/index.html'; return null; }
        if(!r.ok) {
            const err = await r.json().catch(()=>({}));
            const msg = err.detail || r.statusText || 'Error en el servidor';
            if(options.method && options.method !== 'GET') { showToast('Error: ' + msg, 'error'); }
            return null;
        }
        return await r.json();
    } catch(e) {
        const banner = document.getElementById('net-banner');
        if (banner) banner.style.display = 'block';
        if(options.method && options.method !== 'GET') { showToast('Falla de conexión con el servidor', 'error'); }
        return null;
    }
}

async function loadDashboard() {
    try {
        const data = await fetchAPI('/api/admin/dashboard');
        if (!data) return;
        const bodyOrders = document.getElementById('dash-orders-body');
        if (bodyOrders) {
            bodyOrders.innerHTML = '';
            const pending = data.pending_orders || [];
            if (pending.length === 0) {
                bodyOrders.innerHTML = '<tr><td colspan="3" style="color:var(--text-muted); text-align:center;">No hay pedidos pendientes.</td></tr>';
            } else {
                pending.forEach(o => {
                    bodyOrders.innerHTML += `<tr><td style="font-weight:bold;">${escapeHTML(o.document_number)}</td><td>${escapeHTML(o.company_name)}</td><td><span class="badge badge-warning">${escapeHTML(o.status)}</span></td></tr>`;
                });
            }
        }
    } catch (err) {
        console.error("Error en loadDashboard:", err);
    }
}

async function loadUsers() {
    const [users, branches, sectors] = await Promise.all([ fetchAPI('/api/admin/users'), fetchAPI('/api/admin/branches'), fetchAPI('/api/admin/sectors') ]);
    if(users) cachedUsers = users;
    if(branches) {
        cachedBranches = branches; const filterB = document.getElementById('search-user-branch');
        if(filterB) { filterB.innerHTML = '<option value="ALL">-- Todas las Sucursales --</option>'; branches.forEach(b => filterB.innerHTML += `<option value="${b.id}">${escapeHTML(b.name)}</option>`); }
    }
    if(sectors) cachedSectors = sectors;
    filterUsers();
}

function filterUsers() {
    const query = document.getElementById('search-user-text')?.value.toLowerCase().trim() || '';
    const role = document.getElementById('search-user-role')?.value || 'ALL';
    const branch = document.getElementById('search-user-branch')?.value || 'ALL';
    const status = document.getElementById('search-user-status')?.value || 'ALL';

    const filtered = cachedUsers.filter(u => {
        const matchText = !query || u.username.toLowerCase().includes(query) || u.full_name.toLowerCase().includes(query);
        const matchRole = role === 'ALL' || u.role === role;
        const matchBranch = branch === 'ALL' || String(u.branch_id) === String(branch);
        const matchStatus = status === 'ALL' || String(u.is_active) === status;
        return matchText && matchRole && matchBranch && matchStatus;
    });

    const body = document.getElementById('table-users-body'); if(!body) return; body.innerHTML = '';
    if(filtered.length === 0) { body.innerHTML = '<tr><td colspan="8" style="text-align:center; color:var(--text-muted);">No se encontraron usuarios.</td></tr>'; return; }
    filtered.forEach(u => {
        const escUser = escapeHTML(u.username);
        body.innerHTML += `<tr><td style="font-weight:bold; color:var(--primary-blue);">${escUser}</td><td>${escapeHTML(u.full_name)}</td><td>${escapeHTML(u.email || '-')}</td><td><span class="badge badge-info">${escapeHTML(u.role)}</span></td><td>${escapeHTML(u.branch_name || '-')}</td><td>${escapeHTML(u.sector_name || '-')}</td><td><span class="badge ${u.is_active ? 'badge-success' : 'badge-neutral'}">${u.is_active ? 'ACTIVO' : 'INACTIVO'}</span></td><td><button onclick="openUserModal('${escUser}')" class="btn-secondary" style="padding:4px 8px; font-size:0.8rem;">Editar</button></td></tr>`;
    });
}

function openUserModal(usernameToEdit = null) {
    document.getElementById('form-user')?.reset();
    const bSelect = document.getElementById('user-branch');
    if(bSelect) { bSelect.innerHTML = '<option value="">-- Sin Asignar --</option>'; cachedBranches.forEach(b => bSelect.innerHTML += `<option value="${b.id}">${escapeHTML(b.name)}</option>`); }
    onUserBranchChange();

    const editMode = document.getElementById('user-edit-mode'); const userInput = document.getElementById('user-username');
    const passLabel = document.getElementById('label-user-password'); const passInput = document.getElementById('user-password');
    const activeGrp = document.getElementById('group-user-is-active'); const title = document.getElementById('modal-user-title');

    if (usernameToEdit) {
        const user = cachedUsers.find(u => u.username.toLowerCase() === usernameToEdit.toLowerCase()); if(!user) return;
        editMode.value = 'true'; title.textContent = 'Editar Usuario'; userInput.value = user.username; userInput.disabled = true;
        document.getElementById('user-fullname').value = user.full_name || ''; document.getElementById('user-email').value = user.email || '';
        document.getElementById('user-role').value = user.role || 'PREPARADOR'; document.getElementById('user-branch').value = user.branch_id || '';
        onUserBranchChange(); document.getElementById('user-sector').value = user.sector_id || ''; document.getElementById('user-is-active').value = user.is_active ? 'true' : 'false';
        passLabel.textContent = 'Nueva Contraseña (vacío para mantener)'; passInput.required = false; activeGrp.style.display = 'block';
    } else {
        editMode.value = 'false'; title.textContent = 'Nuevo Usuario'; userInput.disabled = false; passLabel.textContent = 'Contraseña *'; passInput.required = true; activeGrp.style.display = 'none';
    }
    openModal('modal-user');
}

function onUserBranchChange() {
    const selectedB = document.getElementById('user-branch').value; const sSelect = document.getElementById('user-sector'); if(!sSelect) return;
    sSelect.innerHTML = '<option value="">-- Sin Asignar --</option>';
    cachedSectors.forEach(s => { if(!selectedB || String(s.branch_id) === String(selectedB)) { sSelect.innerHTML += `<option value="${s.id}">${escapeHTML(s.name)}</option>`; } });
}

async function saveUser(e) {
    e.preventDefault(); const isEdit = document.getElementById('user-edit-mode').value === 'true'; const username = document.getElementById('user-username').value.trim();
    if (isEdit) {
        const payload = { full_name: document.getElementById('user-fullname').value.trim(), role: document.getElementById('user-role').value, email: document.getElementById('user-email').value.trim() || null, branch_id: document.getElementById('user-branch').value || null, sector_id: document.getElementById('user-sector').value || null, is_active: document.getElementById('user-is-active').value === 'true', password: document.getElementById('user-password').value || null };
        const r = await fetchAPI('/api/admin/users/' + encodeURIComponent(username), { method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload) });
        if (r) { showToast('Usuario actualizado correctamente'); closeModal('modal-user'); loadUsers(); }
    } else {
        const payload = { username: username, full_name: document.getElementById('user-fullname').value.trim(), password: document.getElementById('user-password').value, role: document.getElementById('user-role').value, email: document.getElementById('user-email').value.trim() || null, branch_id: document.getElementById('user-branch').value || null, sector_id: document.getElementById('user-sector').value || null };
        const r = await fetchAPI('/api/admin/users', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload) });
        if (r) { showToast('Usuario registrado correctamente'); closeModal('modal-user'); loadUsers(); }
    }
}

async function loadEntities() {
    const ents = await fetchAPI('/api/admin/entities'); if(!ents) return; cachedEntities = ents;
    const body = document.getElementById('table-entities-body'); if(!body) return; body.innerHTML = '';
    ents.forEach(e => {
        let addrs = []; try { addrs = typeof e.addresses === 'string' ? JSON.parse(e.addresses) : (e.addresses || []); } catch(err) {}
        const addrSummary = addrs.length > 0 ? addrs.map(a => `<small><strong>${escapeHTML(a.address_label || a.label)}${a.is_default ? ' ?' : ''}:</strong> ${escapeHTML(a.full_address || a.address)}</small>`).join('<br>') : '<span style="color:var(--text-muted)">Sin direcciones</span>';
        body.innerHTML += `<tr><td style="font-weight:bold;">${escapeHTML(e.tax_id)}</td><td style="color:var(--primary-blue); font-weight:bold;">${escapeHTML(e.company_name)}</td><td><span class="badge badge-neutral">${e.is_customer?'CLIENTE':''} ${e.is_supplier?'PROVEEDOR':''}</span></td><td>${addrSummary}</td><td><span class="badge ${e.is_active!==false?'badge-success':'badge-neutral'}">${e.is_active!==false?'ACTIVO':'INACTIVO'}</span></td><td><button onclick="openEditEntityModal('${e.id}', '${escapeHTML(e.tax_id)}', '${escapeHTML(e.company_name)}', ${e.is_customer}, ${e.is_supplier}, ${e.is_active!==false})" class="btn-secondary" style="padding:4px 8px; font-size:0.8rem;">Editar / Direcciones</button></td></tr>`;
    });
}

function openEntityModal() { document.getElementById('form-entity')?.reset(); openModal('modal-entity'); }
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
    if(r) { showToast('Entidad creada con éxito'); closeModal('modal-entity'); loadEntities(); }
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
    e.preventDefault(); const entId = document.getElementById('edit-ent-id').value;
    const payload = { tax_id: document.getElementById('edit-ent-taxid').value.trim(), company_name: document.getElementById('edit-ent-name').value.trim(), is_customer: document.getElementById('edit-ent-is-customer').checked, is_supplier: document.getElementById('edit-ent-is-supplier').checked, is_active: document.getElementById('edit-ent-is-active').checked };
    const r = await fetchAPI(`/api/admin/entities/${entId}`, { method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload) });
    if(r) { showToast('Entidad actualizada correctamente'); loadEntities(); }
}

async function loadEntityAddresses(entId) {
    const list = document.getElementById('entity-addresses-list'); 
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
        const defaultBadge = a.is_default ? '<span class="badge badge-success" style="font-size:0.65rem; margin-left:4px;">PREDETERMINADA</span>' : '';
        html += `<li style="display:flex; justify-content:space-between; align-items:center; padding:8px 0; border-bottom:1px solid #f0f0f0;">
            <div>
                <strong style="color:var(--primary-blue);">${escapeHTML(a.address_label || a.label)}:</strong> ${escapeHTML(a.full_address || a.address)} ${defaultBadge}
            </div>
            <div style="display:flex; gap:6px;">
                <button type="button" onclick="editEntityAddress('${a.id}')" style="background:var(--accent-blue); color:white; border:none; padding:4px 8px; border-radius:4px; font-weight:bold; cursor:pointer;" title="Editar Dirección">Editar</button>
                <button type="button" onclick="deleteEntityAddress('${a.id}', '${entId}')" style="background:var(--error-red); color:white; border:none; padding:4px 8px; border-radius:4px; font-weight:bold; cursor:pointer;" title="Eliminar Dirección">X</button>
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
    document.getElementById('form-entity-address').reset();
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
        showToast(addrId ? 'Dirección actualizada' : 'Dirección guardada'); 
        resetAddressForm(); 
        loadEntityAddresses(entId); 
        loadEntities(); 
    }
}

async function deleteEntityAddress(addrId, entId) {
    const r = await fetchAPI(`/api/admin/addresses/${addrId}`, { method: 'DELETE' }); 
    if(r) { 
        showToast('Dirección eliminada correctamente');
        loadEntityAddresses(entId); 
        loadEntities(); 
    }
}

async function loadWarehouseData() {
    const [branches, sectors, locations] = await Promise.all([ fetchAPI('/api/admin/branches'), fetchAPI('/api/admin/sectors'), fetchAPI('/api/admin/locations') ]);
    if (branches) {
        cachedBranches = branches; const bBody = document.getElementById('table-branches-body');
        if(bBody) {
            bBody.innerHTML = ''; if(branches.length === 0) bBody.innerHTML = '<tr><td colspan="3" style="color:var(--text-muted); text-align:center;">Sin sucursales.</td></tr>';
            else branches.forEach(b => bBody.innerHTML += `<tr><td style="font-weight:bold;">${escapeHTML(b.code)}</td><td>${escapeHTML(b.name)}</td><td><span class="badge badge-success">ACTIVA</span></td></tr>`);
        }
        const sBranchSelect = document.getElementById('sector-branch'); if(sBranchSelect) sBranchSelect.innerHTML = branches.map(b => `<option value="${b.id}">${escapeHTML(b.name)} (${escapeHTML(b.code)})</option>`).join('');
    }
    if (sectors) {
        cachedSectors = sectors; const sBody = document.getElementById('table-sectors-body');
        if(sBody) {
            sBody.innerHTML = ''; if(sectors.length === 0) sBody.innerHTML = '<tr><td colspan="4" style="color:var(--text-muted); text-align:center;">Sin sectores.</td></tr>';
            else sectors.forEach(s => sBody.innerHTML += `<tr><td>${escapeHTML(s.branch_name||'-')}</td><td style="font-weight:bold;">${escapeHTML(s.name)}</td><td><span class="badge badge-info">${escapeHTML(s.print_queue_code)}</span></td><td><span class="badge badge-neutral">${s.uses_locations ? 'SÍ' : 'NO'}</span></td></tr>`);
        }
        const locSecSelect = document.getElementById('loc-sector'); const impSecSelect = document.getElementById('import-loc-sector');
        const optionsHtml = sectors.map(s => `<option value="${s.id}">${escapeHTML(s.branch_name)} > ${escapeHTML(s.name)}</option>`).join('');
        if(locSecSelect) locSecSelect.innerHTML = optionsHtml; if(impSecSelect) impSecSelect.innerHTML = optionsHtml;
    }
    if (locations) {
        cachedLocations = locations; const lBody = document.getElementById('table-locations-body');
        if(lBody) {
            lBody.innerHTML = ''; if(locations.length === 0) lBody.innerHTML = '<tr><td colspan="5" style="color:var(--text-muted); text-align:center;">Sin ubicaciones registradas.</td></tr>';
            else locations.forEach(l => lBody.innerHTML += `<tr><td>${escapeHTML(l.branch_name||'-')}</td><td>${escapeHTML(l.sector_name||'-')}</td><td style="font-weight:bold; color:var(--primary-blue);">${escapeHTML(l.location_code)}</td><td>${escapeHTML(l.description||'-')}</td><td><span class="badge badge-success">ACTIVA</span></td></tr>`);
        }
    }
}

async function saveBranch(e) { e.preventDefault(); const payload = { code: document.getElementById('branch-code').value.trim(), name: document.getElementById('branch-name').value.trim() }; const r = await fetchAPI('/api/admin/branches', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload) }); if(r) { showToast('Sucursal creada exitosamente'); closeModal('modal-branch'); loadWarehouseData(); } }
async function saveSector(e) { e.preventDefault(); const payload = { branch_id: document.getElementById('sector-branch').value, name: document.getElementById('sector-name').value.trim(), print_queue_code: document.getElementById('sector-print-code').value.trim(), uses_locations: document.getElementById('sector-uses-locations').checked }; const r = await fetchAPI('/api/admin/sectors', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload) }); if(r) { showToast('Sector creado exitosamente'); closeModal('modal-sector'); loadWarehouseData(); } }
async function saveLocation(e) { e.preventDefault(); const payload = { sector_id: document.getElementById('loc-sector').value, location_code: document.getElementById('loc-code').value.trim(), description: document.getElementById('loc-desc').value.trim() || null }; const r = await fetchAPI('/api/admin/locations', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload) }); if(r) { showToast('Ubicación registrada exitosamente'); closeModal('modal-location'); loadWarehouseData(); } }
async function uploadLocationsCSV(e) { e.preventDefault(); const sectorId = document.getElementById('import-loc-sector').value; const fileInput = document.getElementById('import-loc-file'); if(!fileInput.files[0]) { showToast('Seleccione un archivo CSV.', 'warning'); return; } const formData = new FormData(); formData.append('file', fileInput.files[0]); const r = await fetchAPI(`/api/admin/sectors/${sectorId}/locations/import`, { method: 'POST', body: formData }); if(r) { showToast(r.message || 'Ubicaciones importadas correctamente.'); closeModal('modal-import-locations'); loadWarehouseData(); } }

function handleSearchItems() { currentItemPage = 1; currentItemSearchSKU = document.getElementById('search-sku').value.trim(); currentItemSearchDesc = document.getElementById('search-desc').value.trim(); loadItems(); }
function sortItems(col) { currentItemSort = col; currentItemOrder = currentItemOrder === 'ASC' ? 'DESC' : 'ASC'; loadItems(); }
function prevItemPage() { if(currentItemPage > 1) { currentItemPage--; loadItems(); } }
function nextItemPage() { if(currentItemPage < totalItemPages) { currentItemPage++; loadItems(); } }

async function loadItems() {
    const url = `/api/admin/items?page=${currentItemPage}&limit=${currentItemLimit}&sku=${encodeURIComponent(currentItemSearchSKU)}&description=${encodeURIComponent(currentItemSearchDesc)}&sort_by=${currentItemSort}&sort_order=${currentItemOrder}`;
    const data = await fetchAPI(url); if(!data) return;
    
    totalItemPages = data.total_pages || 1;
    document.getElementById('items-page-info').textContent = `Página ${data.page} de ${totalItemPages} (${data.total_count} ítems)`;

    const body = document.getElementById('table-items-body'); if(!body) return; body.innerHTML = '';
    data.items.forEach(i => {
        body.innerHTML += `
            <tr>
                <td style="font-weight:bold; color:var(--primary-blue);">${escapeHTML(i.sku)}</td>
                <td>${escapeHTML(i.description)}</td>
                <td><span class="badge badge-info">${escapeHTML(i.locations_summary || 'Sin asignación')}</span></td>
                <td><button onclick="openEditItemModal('${escapeHTML(i.sku)}', '${escapeHTML(i.description || '')}', '${escapeHTML(i.category || '')}', ${i.length||0}, ${i.width||0}, ${i.height||0}, ${i.weight||0}, ${i.volume||0})" class="btn-secondary" style="padding:4px 8px; font-size:0.8rem;">Ficha Técnica</button></td>
            </tr>
        `;
    });
}

async function openBatchPrintModal() {
    const sectors = await fetchAPI('/api/admin/sectors');
    const qSelect = document.getElementById('batch-print-queue');
    if (qSelect && sectors) {
        qSelect.innerHTML = sectors.map(s => `<option value="${s.print_queue_code}">${s.branch_name} > ${s.name} (${s.print_queue_code})</option>`).join('');
    }
    document.getElementById('form-batch-print').reset();
    document.getElementById('batch-print-lines').innerHTML = '';
    addDynamicLineBatchPrint();
    openModal('modal-batch-print-items');
}

function addDynamicLineBatchPrint(sku = '', qty = 1) {
    const container = document.getElementById('batch-print-lines');
    const row = document.createElement('div');
    row.className = 'dynamic-row';
    row.innerHTML = `
        <input type="text" placeholder="SKU" class="batch-sku" value="${escapeHTML(sku)}" style="flex:2;" required>
        <input type="number" placeholder="Cant. Etiquetas" class="batch-qty" value="${qty}" min="1" style="flex:1;" required>
        <button type="button" onclick="this.parentElement.remove()" style="background:var(--error-red); color:white; border:none; padding:4px 8px; border-radius:4px; font-weight:bold; cursor:pointer;">X</button>
    `;
    container.appendChild(row);
}

function importBatchPrintCSV(input) {
    const file = input.files[0]; if (!file) return;
    const reader = new FileReader();
    reader.onload = function(e) {
        const text = e.target.result;
        const lines = text.split(/\r\n|\n/);
        let loadedCount = 0;
        const container = document.getElementById('batch-print-lines');
        container.innerHTML = '';

        lines.forEach((line, index) => {
            if (!line.trim()) return;
            const delimiter = line.includes(';') ? ';' : ',';
            const parts = line.split(delimiter);
            const sku = parts[0] ? parts[0].trim().replace(/^["']|["']$/g, '') : '';
            const qty = parts[1] ? parseInt(parts[1].trim().replace(/^["']|["']$/g, '')) : 1;

            if (index === 0 && isNaN(qty)) return;

            if (sku) {
                addDynamicLineBatchPrint(sku, Math.max(1, qty));
                loadedCount++;
            }
        });

        showToast(`Cargados ${loadedCount} SKUs desde archivo CSV.`);
        input.value = '';
    };
    reader.readAsText(file);
}

async function submitBatchPrint(e) {
    e.preventDefault();
    const queueCode = document.getElementById('batch-print-queue').value;
    const items = [];
    document.querySelectorAll('#batch-print-lines .dynamic-row').forEach(row => {
        const sku = row.querySelector('.batch-sku').value.trim();
        const qty = parseInt(row.querySelector('.batch-qty').value) || 1;
        if(sku) items.push({ sku: sku, quantity: qty });
    });

    if (items.length === 0) {
        showToast('Agregue al menos un SKU.', 'warning');
        return;
    }

    const r = await fetchAPI('/api/admin/items/batch-print-labels', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ queue_code: queueCode, items: items })
    });

    if(r) {
        showToast(r.message || 'Lote de impresión enviado.');
        closeModal('modal-batch-print-items');
    }
}

function openEditItemModal(sku, desc, cat, length, width, height, weight, volume) {
    document.getElementById('edit-item-sku').value = sku;
    document.getElementById('edit-item-sku-label').textContent = sku;
    document.getElementById('edit-item-desc').value = desc;
    document.getElementById('edit-item-cat').value = cat;
    
    document.getElementById('edit-item-length').value = length;
    document.getElementById('edit-item-width').value = width;
    document.getElementById('edit-item-height').value = height;
    document.getElementById('edit-item-weight').value = weight;
    document.getElementById('edit-item-volume').value = volume;
    
    document.getElementById('group-dimensions').style.display = AppConfig.enable_item_dimensions === 'true' ? 'flex' : 'none';
    openModal('modal-edit-item');
    loadItemLocations(sku);
}

function calcItemVolume() {
    const l = parseFloat(document.getElementById('edit-item-length').value) || 0;
    const w = parseFloat(document.getElementById('edit-item-width').value) || 0;
    const h = parseFloat(document.getElementById('edit-item-height').value) || 0;
    const vol = (l * w * h) / 1000000.0;
    document.getElementById('edit-item-volume').value = vol.toFixed(6);
}

async function saveItemBasic(e) {
    e.preventDefault();
    const sku = document.getElementById('edit-item-sku').value;
    const payload = { 
        description: document.getElementById('edit-item-desc').value, 
        category: document.getElementById('edit-item-cat').value,
        length: parseFloat(document.getElementById('edit-item-length').value) || 0.0,
        width: parseFloat(document.getElementById('edit-item-width').value) || 0.0,
        height: parseFloat(document.getElementById('edit-item-height').value) || 0.0,
        weight: parseFloat(document.getElementById('edit-item-weight').value) || 0.0,
        volume: parseFloat(document.getElementById('edit-item-volume').value) || 0.0
    };
    const r = await fetchAPI('/api/admin/items/' + encodeURIComponent(sku), { method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload) });
    if(r) { showToast('Ficha de artículo actualizada correctamente.'); closeModal('modal-edit-item'); loadItems(); }
}

async function loadItemLocations(sku) {
    const list = document.getElementById('item-assigned-locations-list'); list.innerHTML = 'Cargando...';
    const locs = await fetchAPI(`/api/admin/items/${encodeURIComponent(sku)}/locations`);
    if(!locs || locs.length === 0) { list.innerHTML = '<p style="color:var(--text-muted); font-size:0.9rem;">Sin ubicaciones fijas asignadas.</p>'; return; }
    let html = '<ul style="list-style:none;">';
    locs.forEach(l => { html += `<li style="display:flex; justify-content:space-between; align-items:center; padding:6px 0; border-bottom:1px solid #f0f0f0;"><span><strong>${escapeHTML(l.location_code)}</strong> <small>(${escapeHTML(l.branch_name)} > ${escapeHTML(l.sector_name)})</small></span><button type="button" onclick="deleteItemLocation('${l.assignment_id}', '${sku}')" style="background:var(--error-red); color:white; border:none; padding:3px 8px; border-radius:4px; font-weight:bold; cursor:pointer;">X</button></li>`; });
    html += '</ul>'; list.innerHTML = html;
}

async function addLocationToItem(e) { e.preventDefault(); const sku = document.getElementById('edit-item-sku').value; const code = document.getElementById('new-item-loc-code').value.trim(); const r = await fetchAPI('/api/admin/item-locations', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ sku: sku, location_code: code }) }); if(r && r.status === 'success') { document.getElementById('new-item-loc-code').value = ''; loadItemLocations(sku); loadItems(); } }
async function deleteItemLocation(assignId, sku) { const r = await fetchAPI(`/api/admin/item-locations/${assignId}`, { method: 'DELETE' }); if(r) { loadItemLocations(sku); loadItems(); } }
function openImportModal(type) { document.getElementById('import-type').value = type; document.getElementById('import-title').textContent = type === 'items' ? 'Agregar Catálogo Masivamente' : 'Agregar Ubicaciones Masivamente'; openModal('modal-import'); }
function updateImportFileName(input) { if(input.files[0]) document.getElementById('import-file-name').textContent = input.files[0].name; }
async function processCSVImport(e) { e.preventDefault(); const type = document.getElementById('import-type').value; const fileInput = document.getElementById('import-file-input'); if(!fileInput.files[0]) { showToast('Seleccione un archivo CSV', 'warning'); return; } const formData = new FormData(); formData.append('file', fileInput.files[0]); const endpoint = type === 'items' ? '/api/admin/import/items' : '/api/admin/import/item-locations'; const r = await fetchAPI(endpoint, { method: 'POST', body: formData }); if(r) { showToast(r.message || 'Importación procesada.'); closeModal('modal-import'); loadItems(); } }

function switchPurchaseTab(tabId, btnElement = null) {
    document.querySelectorAll('.purchase-tab').forEach(e => e.style.display = 'none');
    document.querySelectorAll('.purchase-subtab-btn').forEach(e => e.classList.remove('active'));
    document.getElementById(tabId).style.display = 'block';
    if (btnElement) { btnElement.classList.add('active'); } 
    else { const b = document.querySelector(`.purchase-subtab-btn[onclick*="${tabId}"]`); if(b) b.classList.add('active'); }
}

async function loadPurchaseSelectors() {
    const [ents, branches, sectors] = await Promise.all([ fetchAPI('/api/admin/entities'), fetchAPI('/api/admin/branches'), fetchAPI('/api/admin/sectors') ]);
    if(ents) {
        suppliersCache = ents.filter(e => e.is_supplier);
        ['po-supplier', 'rem-supplier', 'inv-supplier'].forEach(id => {
            const el = document.getElementById(id);
            if(el) el.innerHTML = '<option value="">-- Seleccionar Proveedor --</option>' + suppliersCache.map(s => `<option value="${s.id}">${escapeHTML(s.company_name)} (${escapeHTML(s.tax_id)})</option>`).join('');
        });
    }
    if(branches) {
        cachedBranches = branches;
        ['rem-branch', 'inv-branch', 'tr-orig-branch', 'tr-dest-branch'].forEach(id => {
            const el = document.getElementById(id);
            if(el) el.innerHTML = '<option value="">-- Seleccionar --</option>' + branches.map(b => `<option value="${b.id}">${escapeHTML(b.name)}</option>`).join('');
        });
    }
    if(sectors) cachedSectors = sectors;
}

async function onInvSupplierChange() {
    const supplierId = document.getElementById('inv-supplier').value; const container = document.getElementById('inv-remitos-container'); if (!container) return;
    if (!supplierId) { container.innerHTML = '<p style="color:var(--text-muted);">Seleccione un proveedor para consultar remitos registrados.</p>'; return; }
    container.innerHTML = 'Consultando remitos del proveedor...';
    const remitos = await fetchAPI(`/api/admin/suppliers/${encodeURIComponent(supplierId)}/remitos`);
    if (!remitos || remitos.length === 0) { container.innerHTML = '<p style="color:var(--text-muted);">El proveedor no tiene remitos registrados.</p>'; return; }
    let html = '';
    remitos.forEach(r => { html += `<div style="display:flex; align-items:center; gap:8px; margin-bottom:6px; background:#fff; padding:6px; border-radius:4px; border:1px solid #E0F2FE;"><input type="checkbox" class="inv-remito-checkbox" value="${r.id}" id="rem-chk-${r.id}"><label for="rem-chk-${r.id}" style="margin:0; font-weight:normal; cursor:pointer; flex:1;"><strong style="color:var(--primary-blue);">${escapeHTML(r.remito_number)}</strong> (${escapeHTML(r.branch_name)} > ${escapeHTML(r.sector_name)}) - <span class="badge badge-warning">${escapeHTML(r.status)}</span></label></div>`; });
    container.innerHTML = html;
}

function loadAllPurchaseHistories() { loadPOData(); loadRemitoData(); loadInvoiceData(); loadTransferData(); }

async function loadPOData(search = '', limit = 5) { const text = search || document.getElementById('search-po-text')?.value.trim() || ''; const data = await fetchAPI(`/api/admin/purchase-orders?search=${encodeURIComponent(text)}&limit=${limit}`); const body = document.getElementById('table-po-body'); if(!body) return; body.innerHTML = ''; if(!data || data.length === 0) { body.innerHTML = '<tr><td colspan="3" style="text-align:center; color:var(--text-muted);">Sin órdenes registradas.</td></tr>'; return; } data.forEach(po => { body.innerHTML += `<tr><td style="font-weight:bold; color:var(--primary-blue);">${escapeHTML(po.order_number)}</td><td>${escapeHTML(po.supplier_name)}</td><td><span class="badge badge-info">${escapeHTML(po.status)}</span></td></tr>`; }); }

async function loadRemitoData(search = '', limit = 5) { 
    const text = search || document.getElementById('search-rem-text')?.value.trim() || ''; 
    const data = await fetchAPI(`/api/admin/purchase-remitos?search=${encodeURIComponent(text)}&limit=${limit}`); 
    const body = document.getElementById('table-remito-body'); if(!body) return; body.innerHTML = ''; 
    if(!data || data.length === 0) { body.innerHTML = '<tr><td colspan="5" style="text-align:center; color:var(--text-muted);">Sin remitos registrados.</td></tr>'; return; } 
    data.forEach(r => { 
        body.innerHTML += `<tr>
            <td style="font-weight:bold; color:var(--primary-blue);">${escapeHTML(r.remito_number)}</td>
            <td>${escapeHTML(r.supplier_name)}</td>
            <td>${escapeHTML(r.branch_name)} > ${escapeHTML(r.sector_name)}</td>
            <td><span class="badge badge-warning">${escapeHTML(r.status)}</span></td>
            <td><button onclick="printRemitoLabels('${r.id}')" class="btn-secondary" style="padding:3px 6px; font-size:0.75rem;">Etiquetas</button></td>
        </tr>`; 
    }); 
}

async function printRemitoLabels(remitoId) {
    const r = await fetchAPI(`/api/admin/purchase-remitos/${remitoId}/print-labels`, { method: 'POST' });
    if(r) showToast(r.message || 'Etiquetas enviadas a la cola de impresión.');
}

async function loadInvoiceData(search = '', limit = 5) { const text = search || document.getElementById('search-inv-text')?.value.trim() || ''; const data = await fetchAPI(`/api/admin/purchase-invoices?search=${encodeURIComponent(text)}&limit=${limit}`); const body = document.getElementById('table-invoice-body'); if(!body) return; body.innerHTML = ''; if(!data || data.length === 0) { body.innerHTML = '<tr><td colspan="4" style="text-align:center; color:var(--text-muted);">Sin facturas registradas.</td></tr>'; return; } data.forEach(i => { body.innerHTML += `<tr><td style="font-weight:bold; color:var(--primary-blue);">${escapeHTML(i.invoice_number)}</td><td><span class="badge badge-neutral">TIPO ${escapeHTML(i.invoice_type)}</span></td><td>${escapeHTML(i.supplier_name)}</td><td>${new Date(i.created_at).toLocaleDateString()}</td></tr>`; }); }
async function loadTransferData(search = '', limit = 5) { const text = search || document.getElementById('search-tr-text')?.value.trim() || ''; const trs = await fetchAPI(`/api/admin/transfer-orders?search=${encodeURIComponent(text)}&limit=${limit}`); const body = document.getElementById('table-transfers-body'); if(!body) return; body.innerHTML = ''; if(!trs || trs.length === 0) { body.innerHTML = '<tr><td colspan="4" style="text-align:center; color:var(--text-muted);">Sin traspasos registrados.</td></tr>'; return; } trs.forEach(t => { body.innerHTML += `<tr><td style="font-weight:bold; color:var(--primary-blue);">${escapeHTML(t.transfer_number)}</td><td>${escapeHTML(t.origin_branch)}</td><td>${escapeHTML(t.destination_branch)}</td><td><span class="badge badge-warning">${escapeHTML(t.status)}</span></td></tr>`; }); }

function filterSectorsByBranch(branchId) { return cachedSectors.filter(s => !branchId || String(s.branch_id) === String(branchId)); }
function onRemBranchChange() { document.getElementById('rem-sector').innerHTML = filterSectorsByBranch(document.getElementById('rem-branch').value).map(s => `<option value="${s.id}">${escapeHTML(s.name)}</option>`).join(''); }
function onInvBranchChange() { document.getElementById('inv-sector').innerHTML = filterSectorsByBranch(document.getElementById('inv-branch').value).map(s => `<option value="${s.id}">${escapeHTML(s.name)}</option>`).join(''); }
function onTrOrigBranchChange() { document.getElementById('tr-orig-sector').innerHTML = filterSectorsByBranch(document.getElementById('tr-orig-branch').value).map(s => `<option value="${s.id}">${escapeHTML(s.name)}</option>`).join(''); }
function onTrDestBranchChange() { document.getElementById('tr-dest-sector').innerHTML = filterSectorsByBranch(document.getElementById('tr-dest-branch').value).map(s => `<option value="${s.id}">${escapeHTML(s.name)}</option>`).join(''); }

function addDynamicLinePO() { document.getElementById('po-lines').innerHTML += `<div class="dynamic-row"><input type="text" placeholder="SKU" class="po-sku" style="flex:2;" required><input type="number" placeholder="Cantidad" class="po-qty" style="flex:1;" min="0.01" step="0.01" required></div>`; }
function addDynamicLineRemito() { document.getElementById('rem-lines').innerHTML += `<div class="dynamic-row"><input type="text" placeholder="SKU" class="rem-sku" style="flex:2;" required><input type="number" placeholder="Cant" class="rem-qty" style="flex:1;" min="0.01" step="0.01" required><input type="text" placeholder="Ubicación Destino" class="rem-loc" style="flex:1;"></div>`; }
function addDynamicLineInvoice() { document.getElementById('inv-lines').innerHTML += `<div class="dynamic-row"><input type="text" placeholder="SKU" class="inv-sku" style="flex:2;"><input type="number" placeholder="Cantidad" class="inv-qty" style="flex:1;" min="0.01" step="0.01"><input type="number" placeholder="Precio Unitario" class="inv-price" style="flex:1;" min="0" step="0.01"></div>`; }
function addDynamicLineTransfer() { document.getElementById('tr-lines').innerHTML += `<div class="dynamic-row"><input type="text" placeholder="SKU" class="tr-sku" style="flex:2;" required><input type="number" placeholder="Cant" class="tr-qty" style="flex:1;" min="0.01" step="0.01" required><input type="text" placeholder="Ubic. Origen" class="tr-orig-loc" style="flex:1;"><input type="text" placeholder="Ubic. Destino" class="tr-dest-loc" style="flex:1;"></div>`; }

async function savePO(e) { e.preventDefault(); const lines = []; document.querySelectorAll('#po-lines .dynamic-row').forEach(row => { const sku = row.querySelector('.po-sku').value.trim(); const qty = parseFloat(row.querySelector('.po-qty').value); if(sku && qty > 0) lines.push({ sku: sku, quantity_ordered: qty }); }); const payload = { order_number: document.getElementById('po-num').value.trim(), supplier_id: document.getElementById('po-supplier').value, lines: lines }; const r = await fetchAPI('/api/admin/purchase-orders', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload) }); if(r) { showToast('Orden de Compra registrada.'); document.getElementById('form-po').reset(); loadPOData(); } }
async function saveRemito(e) { e.preventDefault(); const lines = []; document.querySelectorAll('#rem-lines .dynamic-row').forEach(row => { const sku = row.querySelector('.rem-sku').value.trim(); const qty = parseFloat(row.querySelector('.rem-qty').value); const loc = row.querySelector('.rem-loc').value.trim() || null; if(sku && qty > 0) lines.push({ sku: sku, quantity_sent: qty, location_code: loc }); }); const payload = { remito_number: document.getElementById('rem-num').value.trim(), supplier_id: document.getElementById('rem-supplier').value, branch_id: document.getElementById('rem-branch').value, sector_id: document.getElementById('rem-sector').value, lines: lines }; const r = await fetchAPI('/api/admin/purchase-remitos', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload) }); if(r) { showToast(r.message || 'Remito registrado.'); document.getElementById('form-remito').reset(); loadRemitoData(); } }
async function saveInvoice(e) { e.preventDefault(); const remitoIds = []; document.querySelectorAll('.inv-remito-checkbox:checked').forEach(cb => remitoIds.push(cb.value)); const manualItems = []; document.querySelectorAll('#inv-lines .dynamic-row').forEach(row => { const sku = row.querySelector('.inv-sku').value.trim(); const qty = parseFloat(row.querySelector('.inv-qty').value); const price = parseFloat(row.querySelector('.inv-price').value) || 0; if(sku && qty > 0) manualItems.push({ sku: sku, quantity: qty, unit_price: price }); }); const payload = { invoice_number: document.getElementById('inv-num').value.trim(), supplier_id: document.getElementById('inv-supplier').value, invoice_type: document.getElementById('inv-type').value, branch_id: document.getElementById('inv-branch').value || null, sector_id: document.getElementById('inv-sector').value || null, manual_items: manualItems, remito_ids: remitoIds }; const r = await fetchAPI('/api/admin/purchase-invoices', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload) }); if(r) { showToast('Factura registrada.'); document.getElementById('form-invoice').reset(); onInvSupplierChange(); loadInvoiceData(); } }
async function saveTransfer(e) { e.preventDefault(); const lines = []; document.querySelectorAll('#tr-lines .dynamic-row').forEach(row => { const sku = row.querySelector('.tr-sku').value.trim(); const qty = parseFloat(row.querySelector('.tr-qty').value); const oLoc = row.querySelector('.tr-orig-loc').value.trim() || null; const dLoc = row.querySelector('.tr-dest-loc').value.trim() || null; if(sku && qty > 0) lines.push({ sku: sku, quantity_sent: qty, origin_location_code: oLoc, destination_location_code: dLoc }); }); const payload = { transfer_number: document.getElementById('tr-num').value.trim(), origin_branch_id: document.getElementById('tr-orig-branch').value, origin_sector_id: document.getElementById('tr-orig-sector').value, destination_branch_id: document.getElementById('tr-dest-branch').value, destination_sector_id: document.getElementById('tr-dest-sector').value, lines: lines }; const r = await fetchAPI('/api/admin/transfer-orders', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload) }); if(r) { showToast(r.message || 'Traspaso registrado.'); document.getElementById('form-transfer').reset(); loadTransferData(); } }

async function loadStock() { const stock = await fetchAPI('/api/admin/stock'); const body = document.getElementById('table-stock-body'); if(body && stock) { body.innerHTML = ''; stock.forEach(s => body.innerHTML += `<tr><td>${escapeHTML(s.sector_name)}</td><td style="font-weight:bold; color:var(--primary-blue);">${escapeHTML(s.location_code)}</td><td style="font-weight:bold;">${escapeHTML(s.sku)}</td><td style="font-weight:bold; color:var(--success-green);">${s.quantity}</td></tr>`); } }

async function loadOrders() { 
    const body = document.getElementById('table-orders-body');
    if(body) body.innerHTML = '<tr><td colspan="5" style="text-align:center; padding:2rem; color:var(--primary-blue); font-weight:bold;">Cargando base de datos...</td></tr>';
    
    const docs = await fetchAPI('/api/admin/documents'); 
    if(docs) { 
        cachedOrdersList = docs;
        filterOrders();
    } else if(body) {
        body.innerHTML = '<tr><td colspan="5" style="text-align:center; padding:2rem; color:var(--error-red);">Error al cargar los pedidos.</td></tr>';
    }
}

function filterOrders() {
    const body = document.getElementById('table-orders-body');
    if(!body) return;
    
    const query = document.getElementById('search-order-text')?.value.toLowerCase().trim() || '';
    const status = document.getElementById('search-order-status')?.value || 'ALL';

    const filtered = cachedOrdersList.filter(d => {
        const matchText = !query || d.document_number.toLowerCase().includes(query) || d.company_name.toLowerCase().includes(query);
        const matchStatus = status === 'ALL' || d.status === status;
        return matchText && matchStatus;
    });

    if (filtered.length === 0) {
        body.innerHTML = '<tr><td colspan="5" style="text-align:center; padding:2rem; color:var(--text-muted); font-weight:bold;">No hay pedidos registrados o que coincidan con la búsqueda.</td></tr>';
        return;
    }

    body.innerHTML = filtered.map(d => {
        const canPrint = (d.status === 'COMPLETED' || d.status === 'DISPATCHED');
        const btnPrint = canPrint ? `<button onclick="printOrderLabelAgain('${escapeHTML(d.document_number)}')" class="btn-secondary" style="padding:3px 6px; font-size:0.75rem;">Etiqueta</button>` : '<span style="color:var(--text-muted);">-</span>';
        
        let badgeClass = 'badge-neutral';
        if(d.status === 'PENDING') badgeClass = 'badge-warning';
        if(d.status === 'IN_PROGRESS') badgeClass = 'badge-info';
        if(d.status === 'COMPLETED') badgeClass = 'badge-success';
        if(d.status === 'DISPATCHED') badgeClass = 'badge-success';

        return `<tr>
            <td style="font-weight:bold; color:var(--primary-blue);">${escapeHTML(d.document_number)}</td>
            <td>${escapeHTML(d.company_name)}</td>
            <td><span class="badge ${badgeClass}">${escapeHTML(d.status)}</span></td>
            <td style="font-weight:bold;">${d.progress_pct}%</td>
            <td>${btnPrint}</td>
        </tr>`; 
    }).join('');
}

async function printOrderLabelAgain(docNum) {
    const r = await fetchAPI(`/api/admin/sales-orders/${encodeURIComponent(docNum)}/print-label`, { method: 'POST' });
    if(r) showToast(r.message || 'Etiqueta enviada a la cola de impresión.');
}

async function loadLogs() { 
    const body = document.getElementById('table-logs-body');
    if(body) body.innerHTML = '<tr><td colspan="4" style="text-align:center; padding:2rem; color:var(--primary-blue); font-weight:bold;">Cargando auditoría...</td></tr>';
    
    const logs = await fetchAPI('/api/admin/logs'); 
    if(logs) { 
        cachedLogsList = logs;
        filterLogs();
    } else if(body) {
        body.innerHTML = '<tr><td colspan="4" style="text-align:center; padding:2rem; color:var(--error-red);">Error al cargar los registros de auditoría.</td></tr>';
    }
}

function filterLogs() {
    const body = document.getElementById('table-logs-body');
    if(!body) return;
    
    const query = document.getElementById('search-log-text')?.value.toLowerCase().trim() || '';

    const filtered = cachedLogsList.filter(l => {
        const matchText = !query || l.username.toLowerCase().includes(query) || l.action.toLowerCase().includes(query) || (l.details && l.details.toLowerCase().includes(query));
        return matchText;
    });

    if (filtered.length === 0) {
        body.innerHTML = '<tr><td colspan="4" style="text-align:center; padding:2rem; color:var(--text-muted); font-weight:bold;">No hay registros de auditoría.</td></tr>';
        return;
    }

    body.innerHTML = filtered.map(l => {
        return `<tr>
            <td><small class="text-muted" style="font-weight:600;">${new Date(l.created_at).toLocaleString()}</small></td>
            <td style="font-weight:bold; color:var(--primary-blue);">${escapeHTML(l.username)}</td>
            <td><span class="badge badge-neutral">${escapeHTML(l.action)}</span></td>
            <td><small>${escapeHTML(l.details || '-')}</small></td>
        </tr>`;
    }).join('');
}

async function openManualOrderModal() {
    document.getElementById('form-manual-order').reset();
    document.getElementById('manual-order-lines').innerHTML = '';
    addDynamicLineManualOrder();
    
    const addrSelect = document.getElementById('manual-addr-label');
    if (addrSelect) addrSelect.innerHTML = '<option value="Principal">Principal</option>';

    document.getElementById('manual-doc-num').value = "000001";
    openModal('modal-manual-order');

    const nextData = await fetchAPI('/api/admin/sales-orders/next-number');
    if (nextData && nextData.next_number) {
        document.getElementById('manual-doc-num').value = nextData.next_number;
    }
}

function addDynamicLineManualOrder(sku = '', qty = '') {
    const container = document.getElementById('manual-order-lines');
    const row = document.createElement('div');
    row.className = 'dynamic-row';
    row.innerHTML = `
        <input type="text" placeholder="SKU" class="manual-sku" value="${escapeHTML(sku)}" style="flex:2;" required>
        <input type="number" placeholder="Cantidad" class="manual-qty" value="${qty}" style="flex:1;" min="0.01" step="0.01" required>
        <button type="button" onclick="this.parentElement.remove()" style="background:var(--error-red); color:white; border:none; padding:4px 8px; border-radius:4px; font-weight:bold; cursor:pointer;">X</button>
    `;
    container.appendChild(row);
}

async function openSearchCustomerModal() {
    if (!cachedEntities || cachedEntities.length === 0) {
        const ents = await fetchAPI('/api/admin/entities');
        if (ents) cachedEntities = ents;
    }
    document.getElementById('search-cust-query').value = '';
    filterCustomerSearch();
    openModal('modal-search-customer');
}

function filterCustomerSearch() {
    const q = document.getElementById('search-cust-query').value.toLowerCase().trim();
    const customers = cachedEntities.filter(e => e.is_customer);
    const body = document.getElementById('table-search-cust-body');
    body.innerHTML = '';

    const filtered = customers.filter(c => {
        let addrs = []; try { addrs = typeof c.addresses === 'string' ? JSON.parse(c.addresses) : (c.addresses || []); } catch(e){}
        const addrStr = addrs.map(a => `${a.address_label || a.label} ${a.full_address || a.address}`).join(' ').toLowerCase();
        return !q || c.tax_id.toLowerCase().includes(q) || c.company_name.toLowerCase().includes(q) || addrStr.includes(q);
    });

    if(filtered.length === 0) {
        body.innerHTML = '<tr><td colspan="4" style="text-align:center; color:var(--text-muted); padding:1rem;">No se encontraron clientes.</td></tr>';
        return;
    }

    filtered.forEach(c => {
        let addrs = []; try { addrs = typeof c.addresses === 'string' ? JSON.parse(c.addresses) : (c.addresses || []); } catch(e){}
        const addrText = addrs.length > 0 ? addrs.map(a => `<b>${escapeHTML(a.address_label || a.label)}:</b> ${escapeHTML(a.full_address || a.address)}`).join('<br>') : 'Sin dirección';

        body.innerHTML += `
            <tr>
                <td style="font-weight:bold;">${escapeHTML(c.tax_id)}</td>
                <td style="color:var(--primary-blue); font-weight:bold;">${escapeHTML(c.company_name)}</td>
                <td><small>${addrText}</small></td>
                <td>
                    <button type="button" class="btn-secondary" style="padding:4px 8px; font-size:0.75rem;" 
                        onclick="selectCustomerForManualOrder('${escapeHTML(c.tax_id)}', '${escapeHTML(c.company_name)}', '${c.id}')">
                        Seleccionar
                    </button>
                </td>
            </tr>`;
    });
}

function selectCustomerForManualOrder(taxId, name, entityId) {
    document.getElementById('manual-cust-taxid').value = taxId;
    document.getElementById('manual-cust-name').value = name;
    
    const select = document.getElementById('manual-addr-label');
    select.innerHTML = '';
    
    const customer = cachedEntities.find(e => String(e.tax_id) === String(taxId) || String(e.id) === String(entityId));
    let addrs = [];
    if (customer) {
        try { addrs = typeof customer.addresses === 'string' ? JSON.parse(customer.addresses) : (customer.addresses || []); } catch (err) { console.error(err); }
    }

    if (addrs && addrs.length > 0) {
        addrs.forEach(a => {
            const lbl = escapeHTML(a.address_label || a.label);
            const full = escapeHTML(a.full_address || a.address);
            select.innerHTML += `<option value="${lbl}">${lbl} (${full})</option>`;
        });
    } else {
        select.innerHTML = '<option value="Principal">Principal</option>';
    }

    closeModal('modal-search-customer');
}

function importManualOrderCSV(input) {
    const file = input.files[0]; if (!file) return;
    const reader = new FileReader();
    reader.onload = function(e) {
        const text = e.target.result;
        const lines = text.split(/\r\n|\n/);
        let loadedCount = 0;
        const container = document.getElementById('manual-order-lines');
        container.querySelectorAll('.dynamic-row').forEach(row => { if(!row.querySelector('.manual-sku').value.trim()) row.remove(); });

        lines.forEach((line, index) => {
            if (!line.trim()) return;
            const delimiter = line.includes(';') ? ';' : ',';
            const parts = line.split(delimiter);
            const sku = parts[0] ? parts[0].trim().replace(/^["']|["']$/g, '') : '';
            const qty = parts[1] ? parseFloat(parts[1].trim().replace(/^["']|["']$/g, '')) : 0;
            if (index === 0 && isNaN(qty)) return;
            if (sku && qty > 0) { addDynamicLineManualOrder(sku, qty); loadedCount++; }
        });

        showToast(`Se agregaron ${loadedCount} artículos masivamente.`);
        input.value = '';
    };
    reader.readAsText(file);
}

async function saveManualOrder(e) {
    e.preventDefault();
    const lines = [];
    document.querySelectorAll('#manual-order-lines .dynamic-row').forEach(row => {
        const sku = row.querySelector('.manual-sku').value.trim();
        const qty = parseFloat(row.querySelector('.manual-qty').value);
        if(sku && qty > 0) lines.push({ sku: sku, quantity: qty });
    });

    if(lines.length === 0) {
        showToast('Debe agregar al menos un artículo al pedido.', 'warning');
        return;
    }

    const payload = {
        document_number: document.getElementById('manual-doc-num').value.trim(),
        customer_tax_id: document.getElementById('manual-cust-taxid').value.trim(),
        customer_name: document.getElementById('manual-cust-name').value.trim() || null,
        address_label: document.getElementById('manual-addr-label').value || 'Principal',
        lines: lines
    };

    const r = await fetchAPI('/api/admin/sales-orders', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload)
    });

    if(r) {
        showToast(r.message || 'Pedido creado correctamente.');
        closeModal('modal-manual-order');
        loadOrders();
    }
}

async function loadIntegrationChannels() {
    try {
        const channels = await fetchAPI('/api/admin/integrations');
        const body = document.getElementById('table-channels-body'); if(!body) return; body.innerHTML = '';
        if(!channels || channels.length === 0) { body.innerHTML = '<tr><td colspan="5" style="text-align:center; color:var(--text-muted);">No hay conexiones externas registradas.</td></tr>'; return; }
        channels.forEach(ch => {
            const badgeType = ch.channel_type === 'OUTBOUND_DESPACHO' ? '<span class="badge badge-info">DESPACHOS</span>' : '<span class="badge badge-success">STOCK REALTIME</span>';
            body.innerHTML += `<tr><td style="font-weight:bold; color:var(--primary-blue);">${escapeHTML(ch.name)}</td><td>${badgeType}</td><td style="font-family:monospace; font-size:0.8rem;">${escapeHTML(ch.target_url)}</td><td><span class="badge badge-success">ACTIVA</span></td><td><button onclick="deleteChannel('${ch.id}')" style="background:var(--error-red); color:white; border:none; padding:4px 8px; border-radius:4px; font-weight:bold; cursor:pointer;">Eliminar</button></td></tr>`;
        });
    } catch (err) {
        console.error("Error en loadIntegrationChannels:", err);
    }
}

async function saveNewChannel(e) {
    e.preventDefault();
    const payload = { name: document.getElementById('chan-name').value.trim(), channel_type: document.getElementById('chan-type').value, target_url: document.getElementById('chan-url').value.trim(), api_key: document.getElementById('chan-key').value.trim() || null };
    const r = await fetchAPI('/api/admin/integrations', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload) });
    if(r) { showToast('Conexión registrada'); closeModal('modal-add-channel'); loadIntegrationChannels(); }
}

async function deleteChannel(id) {
    const r = await fetchAPI(`/api/admin/integrations/${id}`, { method: 'DELETE' }); if(r) { showToast('Conexión eliminada'); loadIntegrationChannels(); }
}

async function generateNewSystemApiKey() {
    const r = await fetchAPI('/api/admin/settings/generate-key', { method: 'POST' });
    if(r && r.new_key) { document.getElementById('cfg-tracker360-api-key').value = r.new_key; showToast('Nueva API Key generada con éxito.'); }
}

document.addEventListener('DOMContentLoaded', () => {
    const btnLogout = document.getElementById('btnLogout');
    if (btnLogout) {
        btnLogout.addEventListener('click', async () => {
            await fetch('/api/auth/logout', { method: 'POST' });
            window.location.href = '/index.html';
        });
    }
});

window.onload = async () => {
    if (typeof loadSettings === 'function') {
        try { await loadSettings(); } catch (err) { console.error("Error en loadSettings:", err); }
    }
    try { await loadDashboard(); } catch (err) { console.error("Error en loadDashboard:", err); }
};