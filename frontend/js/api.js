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
        let response;
        try {
            response = await fetch(url, options);
        } catch (e) {
            throw new Error("No hay conexión con el servidor. Revisá la red y probá de nuevo.");
        }

        if (response.status === 401) {
            if (!window.location.pathname.endsWith('index.html') && window.location.pathname !== '/') {
                window.location.href = '/index.html';
            }
            throw new Error("Sesión expirada. Por favor, reingrese.");
        }

        // Una respuesta que no es JSON (pagina de error del proxy, servidor caido a medias) no se muestra cruda.
        let data = null;
        try { data = await response.json(); } catch (e) { data = undefined; }
        if (data === undefined) {
            if (response.ok && response.status === 204) return null;
            throw new Error(`El servidor no respondió bien (código ${response.status}). Probá de nuevo.`);
        }
        if (!response.ok) {
            throw new Error(mensajeDeError(data, response.status));
        }
        return data;
    } catch (error) {
        showToast(error.message, "danger");
        error.mostrado = true;   // quien llama no tiene que volver a mostrarlo
        throw error;
    }
}

// Texto legible del error que devuelve el servidor. FastAPI manda detail como texto o, si los datos no
// pasan la validacion, como lista de {loc, msg, type}: se arma "campo: problema" en castellano.
const ERRORES_VALIDACION = {
    missing: 'es obligatorio',
    string_too_short: 'es demasiado corto',
    string_too_long: 'es demasiado largo',
    int_parsing: 'tiene que ser un número entero',
    int_type: 'tiene que ser un número entero',
    float_parsing: 'tiene que ser un número',
    float_type: 'tiene que ser un número',
    bool_parsing: 'tiene que ser sí o no',
    greater_than: 'tiene que ser mayor',
    greater_than_equal: 'es menor al mínimo',
    less_than: 'tiene que ser menor',
    less_than_equal: 'supera el máximo',
    uuid_parsing: 'no es un identificador válido',
    date_parsing: 'no es una fecha válida',
    date_from_datetime_parsing: 'no es una fecha válida',
    json_invalid: 'no es válido',
    enum: 'no es una opción válida',
    literal_error: 'no es una opción válida',
};

function mensajeDeError(data, status) {
    const detail = data && data.detail;
    if (typeof detail === 'string' && detail.trim()) return detail;
    if (Array.isArray(detail) && detail.length) {
        const partes = detail.map(d => {
            const campo = (Array.isArray(d.loc) ? d.loc : []).filter(x => x !== 'body' && x !== 'query' && x !== 'path').join(' > ');
            const problema = ERRORES_VALIDACION[d.type] || d.msg || 'no es válido';
            return campo ? `${campo}: ${problema}` : problema;
        });
        return `Revisá los datos: ${partes.join('; ')}.`;
    }
    if (data && typeof data.message === 'string' && data.message.trim()) return data.message;
    return `El servidor no respondió bien (código ${status}). Probá de nuevo.`;
}

async function logoutUser() {
    try {
        await fetchAPI('/api/auth/logout', { method: 'POST' });
        window.location.href = '/index.html';
    } catch (e) {
        window.location.href = '/index.html';
    }
}
