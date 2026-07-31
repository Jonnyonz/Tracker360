// === MÓDULO DE GESTIÓN DE USUARIOS ===

async function legacy_loadUsers() {
    const tbody = document.getElementById('usersTableBody');
    if (!tbody) return;
    tbody.innerHTML = '<tr><td colspan="6" class="text-center py-3"><div class="spinner-border spinner-border-sm"></div> Cargando...</td></tr>';

    try {
        const users = await fetchAPI('/api/admin/users');
        if (!users || users.length === 0) {
            tbody.innerHTML = '<tr><td colspan="6" class="text-center text-muted">No hay usuarios registrados.</td></tr>';
            return;
        }

        tbody.innerHTML = users.map(u => `
            <tr>
                <td class="fw-bold">${u.username}</td>
                <td>${u.full_name}</td>
                <td><span class="badge ${u.role === 'ADMIN' ? 'bg-danger' : 'bg-primary'}">${u.role}</span></td>
                <td>${u.branch_name || 'Todas'} / ${u.sector_name || 'Todos'}</td>
                <td>
                    <span class="badge ${u.is_active ? 'bg-success' : 'bg-secondary'}">
                        ${u.is_active ? 'Activo' : 'Inactivo'}
                    </span>
                </td>
                <td class="text-end">
                    <button class="btn btn-sm btn-outline-dark me-1" onclick="openEditUserModal('${u.username}', '${u.full_name}', '${u.role}', '${u.email || ''}', '${u.branch_id || ''}', '${u.sector_id || ''}', ${u.is_active})">
                        ✏️ Editar
                    </button>
                </td>
            </tr>
        `).join('');
    } catch (e) {
        tbody.innerHTML = '<tr><td colspan="6" class="text-center text-danger">Error al cargar usuarios.</td></tr>';
    }
}

async function saveUserForm(event) {
    if (event) event.preventDefault();
    const username = document.getElementById('userUsername').value.trim();
    const isEdit = document.getElementById('userIsEdit').value === 'true';

    const payload = {
        full_name: document.getElementById('userFullName').value.trim(),
        role: document.getElementById('userRole').value,
        email: document.getElementById('userEmail').value.trim() || null,
        branch_id: document.getElementById('userBranch').value || null,
        sector_id: document.getElementById('userSector').value || null
    };

    const pass = document.getElementById('userPassword').value;
    if (pass && pass.trim()) {
        payload.password = pass.trim();
    } else if (!isEdit) {
        showToast("La contraseña es requerida para nuevos usuarios.", "danger");
        return;
    }

    if (isEdit) {
        payload.is_active = document.getElementById('userIsActive').checked;
    } else {
        payload.username = username;
    }

    try {
        const url = isEdit ? `/api/admin/users/${username}` : '/api/admin/users';
        const method = isEdit ? 'PUT' : 'POST';
        await fetchAPI(url, { method, body: payload });
        
        showToast(`Usuario ${isEdit ? 'actualizado' : 'creado'} correctamente.`, "success");
        
        const modalEl = document.getElementById('userModal');
        if (modalEl) {
            const modal = bootstrap.Modal.getInstance(modalEl);
            if (modal) modal.hide();
        }
        loadUsers();
    } catch (e) {}
}

function openEditUserModal(username, fullName, role, email, branchId, sectorId, isActive) {
    document.getElementById('userIsEdit').value = 'true';
    document.getElementById('userUsername').value = username;
    document.getElementById('userUsername').disabled = true;
    document.getElementById('userFullName').value = fullName;
    document.getElementById('userRole').value = role;
    document.getElementById('userEmail').value = email;
    document.getElementById('userBranch').value = branchId;
    document.getElementById('userSector').value = sectorId;
    document.getElementById('userPassword').value = '';
    
    const activeDiv = document.getElementById('userActiveCheckContainer');
    if (activeDiv) {
        activeDiv.classList.remove('d-none');
        document.getElementById('userIsActive').checked = isActive;
    }

    const modalEl = document.getElementById('userModal');
    if (modalEl) new bootstrap.Modal(modalEl).show();
}

function openNewUserModal() {
    document.getElementById('userIsEdit').value = 'false';
    document.getElementById('userUsername').value = '';
    document.getElementById('userUsername').disabled = false;
    document.getElementById('userFullName').value = '';
    document.getElementById('userRole').value = 'PREPARADOR';
    document.getElementById('userEmail').value = '';
    document.getElementById('userBranch').value = '';
    document.getElementById('userSector').value = '';
    document.getElementById('userPassword').value = '';

    const activeDiv = document.getElementById('userActiveCheckContainer');
    if (activeDiv) activeDiv.classList.add('d-none');

    const modalEl = document.getElementById('userModal');
    if (modalEl) new bootstrap.Modal(modalEl).show();
}
