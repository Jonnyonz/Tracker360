// Script de index.html (antes inline en la pagina; la CSP ya no permite scripts inline).
function toggleTheme() {
    const html = document.documentElement;
    const currentTheme = html.getAttribute('data-theme');
    const newTheme = currentTheme === 'dark' ? 'light' : 'dark';
    html.setAttribute('data-theme', newTheme);
    localStorage.setItem('jztech-theme', newTheme);
}

// Retorno tras el login: solo se permite volver a la pantalla de autorizacion del agente.
function getSafeNext() {
    const next = new URLSearchParams(window.location.search).get('next') || '';
    return /^\/agent-auth\.html\?[A-Za-z0-9_\-=&%.+]*$/.test(next) ? next : null;
}

function redirectAfterLogin(role) {
    const next = getSafeNext();
    if (next) { window.location.href = next; return; }
    const userRole = (role || '').toUpperCase();
    if (userRole === 'PREPARADOR') {
        window.location.href = '/preparador.html';
    } else {
        window.location.href = '/admin.html';
    }
}

document.addEventListener('DOMContentLoaded', async () => {
    const savedTheme = localStorage.getItem('jztech-theme') || 'light';
    document.documentElement.setAttribute('data-theme', savedTheme);

    const loginContainer = document.getElementById('loginContainer');
    const setupContainer = document.getElementById('setupContainer');

    try {
        const statusResp = await fetch('/api/auth/setup/status');
        if (statusResp.ok) {
            const statusData = await statusResp.json();
            if (statusData.needs_setup) {
                loginContainer.style.display = 'none';
                setupContainer.style.display = 'block';

                const setupForm = document.getElementById('setupForm');
                const btnSetupSubmit = document.getElementById('btnSetupSubmit');
                const setupErrorBox = document.getElementById('setupErrorBox');

                setupForm.addEventListener('submit', async (e) => {
                    e.preventDefault();
                    setupErrorBox.style.display = 'none';

                    const password = document.getElementById('su-password').value;
                    const password2 = document.getElementById('su-password2').value;
                    if (password !== password2) {
                        setupErrorBox.textContent = 'Las contraseñas no coinciden.';
                        setupErrorBox.style.display = 'block';
                        return;
                    }

                    btnSetupSubmit.disabled = true;
                    btnSetupSubmit.textContent = 'Creando...';

                    try {
                        const resp = await fetch('/api/auth/setup/admin', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({
                                token: document.getElementById('su-token').value.trim(),
                                username: document.getElementById('su-username').value.trim(),
                                full_name: document.getElementById('su-fullname').value.trim(),
                                password: password
                            })
                        });
                        const data = await resp.json();
                        if (!resp.ok) {
                            const msg = typeof data.detail === 'string' ? data.detail : 'No se pudo crear el administrador.';
                            throw new Error(msg);
                        }
                        window.location.href = '/admin.html';
                    } catch (err) {
                        setupErrorBox.textContent = err.message;
                        setupErrorBox.style.display = 'block';
                        btnSetupSubmit.disabled = false;
                        btnSetupSubmit.textContent = 'Crear Administrador';
                    }
                });
                return;
            }
        }
    } catch (err) {
        console.warn('No se pudo verificar el estado de configuración inicial:', err);
    }

    const loginForm = document.getElementById('loginForm');
    const btnSubmit = document.getElementById('btnSubmit');
    const errorBox = document.getElementById('errorBox');
    const usernameInput = document.getElementById('username');
    const passwordInput = document.getElementById('password');

    const showError = (message) => {
        errorBox.textContent = message;
        errorBox.style.display = 'block';
        passwordInput.value = '';
        passwordInput.focus();
    };

    const hideError = () => {
        errorBox.style.display = 'none';
    };

    try {
        const confResp = await fetch('/api/auth/google/config');
        if (confResp.ok) {
            const googleConf = await confResp.json();
            if (googleConf.enabled && googleConf.client_id) {
                document.getElementById('googleSection').style.display = 'block';

                window.handleCredentialResponse = async (googleResponse) => {
                    hideError();
                    btnSubmit.disabled = true;
                    btnSubmit.textContent = 'Verificando con Google...';

                    try {
                        const resp = await fetch('/api/auth/google/verify', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ id_token: googleResponse.credential })
                        });

                        const data = await resp.json();
                        if (!resp.ok) {
                            const msg = typeof data.detail === 'string' ? data.detail : 'Error al autenticar con Google.';
                            throw new Error(msg);
                        }

                        redirectAfterLogin(data.role);
                    } catch (err) {
                        showError(err.message);
                        btnSubmit.disabled = false;
                        btnSubmit.textContent = 'Ingresar al Sistema';
                    }
                };

                if (typeof google !== 'undefined') {
                    google.accounts.id.initialize({
                        client_id: googleConf.client_id,
                        callback: handleCredentialResponse
                    });
                    google.accounts.id.renderButton(
                        document.getElementById("googleBtn"),
                        { theme: "outline", size: "large", text: "signin_with", locale: "es", width: 300 }
                    );
                }
            }
        }
    } catch (err) {
        console.warn("No se pudo cargar la configuración SSO:", err);
    }

    loginForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        hideError();

        btnSubmit.disabled = true;
        btnSubmit.textContent = 'Verificando...';

        const credentials = {
            username: (usernameInput.value || '').trim(),
            password: passwordInput.value
        };

        try {
            const response = await fetch('/api/auth/login', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(credentials)
            });

            if (!response.ok) {
                let errorMsg = 'Error al iniciar sesión.';
                try {
                    const errData = await response.json();
                    if (errData && errData.detail) {
                        if (typeof errData.detail === 'string') {
                            errorMsg = errData.detail;
                        } else if (Array.isArray(errData.detail)) {
                            errorMsg = errData.detail.map(item => item.msg || 'Dato inválido').join(', ');
                        }
                    }
                } catch (err) {
                    if (response.status === 401 || response.status === 403) {
                        errorMsg = 'Credenciales incorrectas o usuario inactivo.';
                    }
                }
                throw new Error(errorMsg);
            }

            const data = await response.json();
            redirectAfterLogin(data.role);

        } catch (error) {
            showError(error.message);
            btnSubmit.disabled = false;
            btnSubmit.textContent = 'Ingresar al Sistema';
        }
    });
});
