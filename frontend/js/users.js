// === MÓDULO DE USUARIOS Y APROBACIÓN DE SOLICITUDES (TRACKER360) ===

let cachedUsersList = [];

async function loadUsers() {
    const tbody = document.getElementById('table-users-body');
    const tbodyPending = document.getElementById('table-pending-requests-body');
    const countBadge = document.getElementById('pending-requests-count');
    if (!tbody) return;

    try {
        const users = await fetchAPI('/api/admin/users');
        cachedUsersList = users || [];

        const activeUsers = cachedUsersList.filter(u => u.is_active);
        const pendingUsers = cachedUsersList.filter(u => !u.is_active);

        if (countBadge) {
            countBadge.textContent = pendingUsers.length;
            countBadge.className = pendingUsers.length > 0 ? 'badge badge-warning' : 'badge badge-neutral';
        }

        // Renderizado de usuarios activos
        if (activeUsers.length === 0) {
            tbody.innerHTML = '<tr><td colspan="8" style="text-align:center; color:var(--text-muted); padding:1rem;">Sin usuarios activos registrados.</td></tr>';
        } else {
            tbody.innerHTML = activeUsers.map(u => `
                <tr>
                    <td style="font-weight:bold; color:var(--primary-blue);">${escapeHTML(u.username)}</td>
                    <td>${escapeHTML(u.full_name)}</td>
                    <td><small>${escapeHTML(u.email || '-')}</small></td>
                    <td><span class="badge ${u.role === 'ADMIN' ? 'badge-info' : 'badge-neutral'}">${escapeHTML(u.role)}</span></td>
                    <td><small>${escapeHTML(u.branch_name || 'Todas')}</small></td>
                    <td><small>${escapeHTML(u.sector_name || 'Todos')}</small></td>
                    <td><span class="badge badge-success">ACTIVO</span></td>
                    <td>
                        <button class="btn-secondary" style="padding:3px 8px; font-size:0.75rem;" onclick="openEditUserModal('${u.id}')">Editar</button>
                    </td>
                </tr>
            `).join('');
        }

        // Renderizado de solicitudes pendientes en el modal
        if (tbodyPending) {
            if (pendingUsers.length === 0) {
                tbodyPending.innerHTML = '<tr><td colspan="5" style="text-align:center; color:var(--text-muted); padding:1.5rem;">No hay solicitudes pendientes de Google OAuth.</td></tr>';
            } else {
                tbodyPending.innerHTML = pendingUsers.map(u => `
                    <tr style="background:#FFFBEB;">
                        <td style="font-weight:bold; color:#92400E;">${escapeHTML(u.full_name)}</td>
                        <td><code>${escapeHTML(u.email || u.username)}</code></td>
                        <td><small class="text-muted">${new Date(u.created_at || Date.now()).toLocaleDateString()}</small></td>
                        <td><span class="badge badge-warning">PENDIENTE</span></td>
                        <td>
                            <div style="display:flex; gap:6px;">
                                <button class="btn-submit" style="padding:4px 10px; width:auto; font-size:0.75rem; background:var(--success-green);" onclick="openApproveUserModal('${u.id}')">Aprobar</button>
                                <button class="btn-secondary" style="padding:4px 10px; width:auto; font-size:0.75rem; color:var(--error-red); border-color:var(--error-red);" onclick="rejectUser('${u.id}')">Rechazar</button>
                            </div>
                        </td>
                    </tr>
                `).join('');
            }
        }

    } catch (e) {
        tbody.innerHTML = '<tr><td colspan="8" style="text-align:center; color:var(--error-red); padding:1rem;">Error al cargar usuarios.</td></tr>';
    }
}

function openPendingRequestsModal() {
    loadUsers();
    if (typeof openModal === 'function') {
        openModal('modal-pending-requests');
    }
}

function populateUserBranchSectorDropdowns(branchSelectId, sectorSelectId) {
    const branchSelect = document.getElementById(branchSelectId);
    const sectorSelect = document.getElementById(sectorSelectId);

    if (branchSelect && typeof cachedBranches !== 'undefined') {
        branchSelect.innerHTML = '<option value="">-- Todas / Sin restricción --</option>' +
            cachedBranches.map(b => `<option value="${b.id}">${escapeHTML(b.name)}</option>`).join('');
    }

    if (sectorSelect) {
        sectorSelect.innerHTML = '<option value="">-- Todos / Sin restricción --</option>';
    }
}

function onUserBranchChange(branchSelectId, sectorSelectId) {
    const branchId = document.getElementById(branchSelectId)?.value;
    const sectorSelect = document.getElementById(sectorSelectId);
    if (!sectorSelect || typeof cachedSectors === 'undefined') return;

    sectorSelect.innerHTML = '<option value="">-- Todos / Sin restricción --</option>';
    cachedSectors.forEach(s => {
        if (!branchId || String(s.branch_id) === String(branchId)) {
            sectorSelect.innerHTML += `<option value="${s.id}">${escapeHTML(s.name)}</option>`;
        }
    });
}

function openUserModal() {
    const form = document.getElementById('form-create-user');
    if (form) form.reset();
    populateUserBranchSectorDropdowns('user-branch', 'user-sector');
    if (typeof openModal === 'function') {
        openModal('modal-create-user');
    }
}

async function submitCreateUser(event) {
    event.preventDefault();
    const payload = {
        username: document.getElementById('user-username').value.trim(),
        full_name: document.getElementById('user-fullname').value.trim(),
        email: document.getElementById('user-email').value.trim() || null,
        password: document.getElementById('user-password').value,
        role: document.getElementById('user-role').value,
        branch_id: document.getElementById('user-branch').value || null,
        sector_id: document.getElementById('user-sector').value || null
    };

    try {
        await fetchAPI('/api/admin/users', { method: 'POST', body: payload });
        showToast("Usuario creado exitosamente.", "success");
        closeModal('modal-create-user');
        loadUsers();
    } catch (e) {
        showToast(e.message, "error");
    }
}

function openApproveUserModal(userId) {
    const user = cachedUsersList.find(u => String(u.id) === String(userId));
    if (!user) return;

    document.getElementById('approve-user-id').value = user.id;
    document.getElementById('approve-user-name').textContent = user.full_name;
    document.getElementById('approve-user-email').textContent = user.email || user.username;
    
    populateUserBranchSectorDropdowns('approve-user-branch', 'approve-user-sector');
    if (typeof openModal === 'function') {
        openModal('modal-approve-user');
    }
}

async function submitApproveUser(event) {
    event.preventDefault();
    const userId = document.getElementById('approve-user-id').value;
    const role = document.getElementById('approve-user-role').value;
    const branchId = document.getElementById('approve-user-branch').value || null;
    const sectorId = document.getElementById('approve-user-sector').value || null;

    const payload = {
        role: role,
        branch_id: branchId,
        sector_id: sectorId,
        is_active: true
    };

    try {
        await fetchAPI(`/api/admin/users/${userId}`, { method: 'PUT', body: payload });
        showToast("Usuario aprobado y activado en el sistema.", "success");
        closeModal('modal-approve-user');
        loadUsers();
    } catch (e) {
        showToast(e.message, "error");
    }
}

async function rejectUser(userId) {
    const user = cachedUsersList.find(u => String(u.id) === String(userId));
    const name = user ? user.full_name : 'esta solicitud';

    if (!confirm(`¿Está seguro de rechazar y eliminar la solicitud de acceso de "${name}"?`)) return;

    try {
        await fetchAPI(`/api/admin/users/${userId}`, { method: 'DELETE' });
        showToast("Solicitud rechazada y eliminada correctamente.", "success");
        loadUsers();
    } catch (e) {
        showToast(e.message, "error");
    }
}

function openEditUserModal(userId) {
    const user = cachedUsersList.find(u => String(u.id) === String(userId));
    if (!user) return;

    document.getElementById('edit-user-id').value = user.id;
    document.getElementById('edit-user-username').value = user.username;
    document.getElementById('edit-user-fullname').value = user.full_name;
    document.getElementById('edit-user-email').value = user.email || '';
    document.getElementById('edit-user-role').value = user.role;
    document.getElementById('edit-user-active').value = String(user.is_active);

    populateUserBranchSectorDropdowns('edit-user-branch', 'edit-user-sector');
    
    if (user.branch_id) {
        document.getElementById('edit-user-branch').value = user.branch_id;
        onUserBranchChange('edit-user-branch', 'edit-user-sector');
    }
    if (user.sector_id) {
        document.getElementById('edit-user-sector').value = user.sector_id;
    }

    if (typeof openModal === 'function') {
        openModal('modal-edit-user');
    }
}

async function submitEditUser(event) {
    event.preventDefault();
    const userId = document.getElementById('edit-user-id').value;
    const passwordVal = document.getElementById('edit-user-password').value;

    const payload = {
        full_name: document.getElementById('edit-user-fullname').value.trim(),
        email: document.getElementById('edit-user-email').value.trim() || null,
        role: document.getElementById('edit-user-role').value,
        branch_id: document.getElementById('edit-user-branch').value || null,
        sector_id: document.getElementById('edit-user-sector').value || null,
        is_active: document.getElementById('edit-user-active').value === 'true'
    };

    if (passwordVal && passwordVal.trim()) {
        payload.password = passwordVal.trim();
    }

    try {
        await fetchAPI(`/api/admin/users/${userId}`, { method: 'PUT', body: payload });
        showToast("Usuario actualizado correctamente.", "success");
        closeModal('modal-edit-user');
        loadUsers();
    } catch (e) {
        showToast(e.message, "error");
    }
}

function filterUsers() {
    const text = (document.getElementById('search-user-text')?.value || '').toLowerCase();
    const role = document.getElementById('search-user-role')?.value || 'ALL';
    const status = document.getElementById('search-user-status')?.value || 'ALL';

    const rows = document.querySelectorAll('#table-users-body tr');
    rows.forEach(r => {
        const uText = r.textContent.toLowerCase();
        const matchText = uText.includes(text);
        const matchRole = (role === 'ALL') || uText.includes(role.toLowerCase());
        const matchStatus = (status === 'ALL') || (status === 'true' && uText.includes('activo')) || (status === 'false' && uText.includes('inactivo'));
        
        r.style.display = (matchText && matchRole && matchStatus) ? '' : 'none';
    });
}

window.loadUsers = loadUsers;
window.openPendingRequestsModal = openPendingRequestsModal;
window.openUserModal = openUserModal;
window.submitCreateUser = submitCreateUser;
window.openApproveUserModal = openApproveUserModal;
window.submitApproveUser = submitApproveUser;
window.rejectUser = rejectUser;
window.openEditUserModal = openEditUserModal;
window.submitEditUser = submitEditUser;
window.filterUsers = filterUsers;
window.onUserBranchChange = onUserBranchChange;