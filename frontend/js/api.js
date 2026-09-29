// === MOTOR CENTRAL DE PETICIONES Y NOTIFICACIONES ===

async function fetchAPI(url, options = {}) {
    options.credentials = 'include';
    
    if (options.body && typeof options.body === 'object' && !(options.body instanceof FormData)) {
        options.headers = {
            ...options.headers,
            'Content-Type': 'application/json'
        };
        options.body = JSON.stringify(options.body);
    }

    try {
        const response = await fetch(url, options);
        
        if (response.status === 401) {
            if (!window.location.pathname.endsWith('index.html') && window.location.pathname !== '/') {
                window.location.href = '/index.html';
            }
            throw new Error("Sesión expirada. Por favor, reingrese.");
        }
        
        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.detail || "Error al procesar la solicitud.");
        }
        return data;
    } catch (error) {
        showToast(error.message, "danger");
        throw error;
    }
}

function showToast(message, type = "info") {
    let container = document.getElementById('toastContainer');
    if (!container) {
        container = document.createElement('div');
        container.id = 'toastContainer';
        container.className = 'toast-container position-fixed bottom-0 end-0 p-3';
        container.style.zIndex = '9999';
        document.body.appendChild(container);
    }
    
    const toastEl = document.createElement('div');
    const bgClass = type === 'danger' ? 'bg-danger' : type === 'success' ? 'bg-success' : 'bg-dark';
    toastEl.className = `toast align-items-center text-white ${bgClass} border-0 show mb-2`;
    toastEl.role = 'alert';
    // El mensaje puede venir del servidor y repetir lo que escribio el usuario: se inserta
    // siempre como texto (textContent), nunca como HTML, para que no pueda inyectar codigo.
    const fila = document.createElement('div');
    fila.className = 'd-flex';
    const cuerpo = document.createElement('div');
    cuerpo.className = 'toast-body fw-bold';
    cuerpo.textContent = String(message);
    const cerrar = document.createElement('button');
    cerrar.type = 'button';
    cerrar.className = 'btn-close btn-close-white me-2 m-auto';
    cerrar.addEventListener('click', () => toastEl.remove());
    fila.append(cuerpo, cerrar);
    toastEl.appendChild(fila);
    container.appendChild(toastEl);
    setTimeout(() => { if (toastEl) toastEl.remove(); }, 4000);
}

async function logoutUser() {
    try {
        await fetchAPI('/api/auth/logout', { method: 'POST' });
        window.location.href = '/index.html';
    } catch (e) {
        window.location.href = '/index.html';
    }
}
