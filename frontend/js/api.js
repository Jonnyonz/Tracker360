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
    toastEl.innerHTML = `
        <div class="d-flex">
            <div class="toast-body fw-bold">${message}</div>
            <button type="button" class="btn-close btn-close-white me-2 m-auto" onclick="this.parentElement.parentElement.remove()"></button>
        </div>
    `;
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
