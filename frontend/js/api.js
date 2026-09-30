// === MOTOR CENTRAL DE PETICIONES Y NOTIFICACIONES ===

// Unico fetchAPI del panel y del celular. showToast lo define cada pagina (admin-core.js / mobile.js).
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

async function logoutUser() {
    try {
        await fetchAPI('/api/auth/logout', { method: 'POST' });
        window.location.href = '/index.html';
    } catch (e) {
        window.location.href = '/index.html';
    }
}
