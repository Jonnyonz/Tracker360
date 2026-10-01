// Script de agent-auth.html (antes inline en la pagina; la CSP ya no permite scripts inline).
(function () {
    try {
        const savedTheme = localStorage.getItem('jztech-theme') || 'light';
        document.documentElement.setAttribute('data-theme', savedTheme);
    } catch (e) { /* almacenamiento no disponible: se usa el tema claro */ }

    const params = new URLSearchParams(window.location.search);
    const port = parseInt(params.get('port') || '', 10);
    const state = params.get('state') || '';
    const challenge = params.get('challenge') || '';
    const agentName = (params.get('name') || 'Agente de impresion').slice(0, 100);

    // Token CSRF de la sesion (cookie csrf_token), igual que fetchAPI en js/api.js.
    const csrfCookie = () => {
        const c = document.cookie.split('; ').find(row => row.startsWith('csrf_token='));
        return c ? decodeURIComponent(c.slice('csrf_token='.length)) : '';
    };
    const show = (id) => {
        ['viewLoading', 'viewConsent', 'viewDone'].forEach(v => document.getElementById(v).classList.add('hidden'));
        document.getElementById(id).classList.remove('hidden');
    };
    const showError = (msg) => {
        const box = document.getElementById('errorBox');
        box.textContent = msg;
        box.style.display = 'block';
    };

    const validPort = Number.isInteger(port) && port >= 1024 && port <= 65535;
    const validState = /^[A-Za-z0-9_-]{16,128}$/.test(state);
    const validChallenge = /^[A-Za-z0-9_-]{43,128}$/.test(challenge);
    if (!validPort || !validState || !validChallenge) {
        document.getElementById('viewLoading').classList.add('hidden');
        showError('Solicitud de autorizacion invalida. Vuelva a iniciar el agente de impresion.');
        return;
    }

    // El agente escucha solo en la maquina local.
    const callback = (query) => {
        window.location.href = 'http://127.0.0.1:' + port + '/callback?' + query.toString();
    };

    document.getElementById('agentName').textContent = agentName;

    document.getElementById('btnCancel').addEventListener('click', () => {
        callback(new URLSearchParams({ error: 'access_denied', state: state }));
    });

    document.getElementById('btnAuthorize').addEventListener('click', async () => {
        const btn = document.getElementById('btnAuthorize');
        btn.disabled = true;
        btn.textContent = 'Autorizando...';
        try {
            const resp = await fetch('/api/print-agent/authorize', {
                method: 'POST',
                credentials: 'include',
                headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfCookie() },
                body: JSON.stringify({ challenge: challenge, agent_name: agentName })
            });
            const data = await resp.json().catch(() => ({}));
            if (!resp.ok || !data.code) {
                throw new Error(typeof data.detail === 'string' ? data.detail : 'No se pudo autorizar el agente.');
            }
            show('viewDone');
            callback(new URLSearchParams({ code: data.code, state: state }));
        } catch (err) {
            showError(err.message);
            btn.disabled = false;
            btn.textContent = 'Autorizar';
        }
    });

    fetch('/api/auth/me', { credentials: 'include' }).then(async (resp) => {
        if (resp.status === 401) {
            const next = window.location.pathname + window.location.search;
            window.location.href = '/index.html?next=' + encodeURIComponent(next);
            return;
        }
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok) throw new Error('No se pudo verificar la sesion.');
        document.getElementById('currentUser').textContent = data.username || '';
        show('viewConsent');
    }).catch((err) => {
        document.getElementById('viewLoading').classList.add('hidden');
        showError(err.message || 'Falla de conexion con el servidor.');
    });
})();
