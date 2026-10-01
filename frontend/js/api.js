// === MOTOR CENTRAL DE PETICIONES Y NOTIFICACIONES ===

// Token CSRF de la sesion: lo pone el servidor en la cookie csrf_token y hay que repetirlo en el
// header X-CSRF-Token de cada escritura (POST/PUT/PATCH/DELETE).
function csrfToken() {
    const c = document.cookie.split('; ').find(row => row.startsWith('csrf_token='));
    return c ? decodeURIComponent(c.slice('csrf_token='.length)) : '';
}

// Unico fetchAPI del panel y del celular. showToast lo define cada pagina (admin-core.js / mobile.js).
async function fetchAPI(url, options = {}) {
    options.credentials = 'include';
    const method = (options.method || 'GET').toUpperCase();
    if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) {
        options.headers = { ...options.headers, 'X-CSRF-Token': csrfToken() };
    }
    
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

async function logoutUser() {
    try {
        await fetchAPI('/api/auth/logout', { method: 'POST' });
        window.location.href = '/index.html';
    } catch (e) {
        window.location.href = '/index.html';
    }
}
