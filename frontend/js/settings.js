// === MÓDULO DE CONFIGURACIÓN E INTEGRACIONES (TRACKER360) ===

if (typeof window.DEFAULT_ITEM_ZPL === 'undefined') {
    window.DEFAULT_ITEM_ZPL = `^XA
^PW304
^LL160
^LS0
^FO20,25^A0N,22,22^FD{{SKU}}^FS
^FO20,65^A0N,16,14^FD{{DESC}}^FS
^FO195,15^BQN,2,3^FDLA,{{SKU}}^FS
^XZ`;
}

if (typeof window.DEFAULT_ORDER_ZPL === 'undefined') {
    window.DEFAULT_ORDER_ZPL = `^XA
^PW608
^LL380
^LS0
^FO30,30^A0N,30,30^FDTRACKER360 - ENVIO^FS
^FO30,80^A0N,24,24^FDORDEN: {{ORDER_NUM}}^FS
^FO30,120^A0N,20,20^FDDESTINO: {{DESTINATION}}^FS
^FO380,60^BQN,2,5^FDLA,{{ORDER_NUM}}^FS
^XZ`;
}

if (typeof window.DEFAULT_LOCATION_ZPL === 'undefined') {
    window.DEFAULT_LOCATION_ZPL = `^XA
^PW400
^LL200
^LS0
^FO30,25^A0N,28,28^FDUBICACION: {{LOCATION_CODE}}^FS
^FO30,65^A0N,20,18^FD{{BRANCH}} - {{SECTOR}}^FS
^FO30,105^BY3,2.0,60^BCN,70,Y,N,N^FD{{LOCATION_CODE}}^FS
^XZ`;
}

async function loadSettings() {
    try {
        console.log("[SETTINGS] Solicitando configuraciones a /api/settings...");
        const data = await fetchAPI('/api/settings');
        if (!data) {
            console.warn("[SETTINGS] Respuesta vacía de la API.");
            return;
        }
        if (typeof AppConfig !== 'undefined') AppConfig = data;

        const setVal = (id, val) => {
            const el = document.getElementById(id);
            if (el) {
                const strVal = (val !== undefined && val !== null) ? String(val) : '';
                el.value = strVal;
                if (el.tagName === 'TEXTAREA') {
                    el.textContent = strVal;
                }
            }
        };

        // Identidad Corporativa
        setVal('cfg-app-name', data.app_name || '');
        setVal('cfg-company-cuit', data.company_cuit || '');

        // Seguridad y Sesiones
        setVal('cfg-session-timeout-minutes', data.session_timeout_minutes || '240');
        setVal('cfg-max-login-attempts', data.max_login_attempts || '5');
        setVal('cfg-lockout-time-minutes', data.lockout_time_minutes || '15');

        // Documentos y Correlativos
        setVal('cfg-transfer-number-prefix', data.transfer_number_prefix || 'TR-');
        setVal('cfg-sales-order-prefix', data.sales_order_prefix || 'PED-');
        setVal('cfg-correlative-zeros-pad', data.correlative_zeros_pad || '6');

        // Reglas de Operativa, Depósito y Auditoría
        setVal('cfg-enable-stock-management', String(data.enable_stock_management ?? 'true'));
        setVal('cfg-allow-negative-stock', String(data.allow_negative_stock ?? 'false'));
        setVal('cfg-enable-lots-expiration', String(data.enable_lots_expiration ?? 'false'));
        setVal('cfg-enable-committed-stock', String(data.enable_committed_stock ?? 'true'));
        setVal('cfg-require-mobile-reception', String(data.require_mobile_reception ?? 'false'));
        setVal('cfg-allow-multiproduct', String(data.allow_multiproduct_locations ?? 'false'));
        setVal('cfg-enable-item-dimensions', String(data.enable_item_dimensions ?? 'false'));
        setVal('cfg-auto-complete-picking', String(data.auto_complete_picking ?? 'true'));
        setVal('cfg-default-inventory-count-type', data.default_inventory_count_type || 'HOT');

        // Configuración Google OAUTH2
        const ssoEnabled = (data.enable_google_sso === 'true');
        const ssoCheck = document.getElementById('cfg-enable-google-sso');
        if (ssoCheck) ssoCheck.checked = ssoEnabled;
        
        // Inyectar URL de redirección calculada en base al dominio actual
        const cbUrl = document.getElementById('cfg-google-callback-url');
        if (cbUrl) {
            cbUrl.value = window.location.origin + '/api/auth/google/callback';
        }

        setVal('cfg-google-client-id', data.google_client_id || '');
        setVal('cfg-google-client-secret', data.google_client_secret || '');
        setVal('cfg-google-allowed-domain', data.google_allowed_domain || '');

        // Configuración Impresoras de Etiquetas ZPL
        setVal('cfg-default-print-queue', data.default_print_queue || 'PRINT-SEC-01');
        
        setVal('cfg-zpl-item-width', data.zpl_item_width || '38');
        setVal('cfg-zpl-item-height', data.zpl_item_height || '20');
        setVal('cfg-zpl-item-template', data.zpl_item_template || data.zpl_template || window.DEFAULT_ITEM_ZPL);

        setVal('cfg-zpl-order-width', data.zpl_order_width || '100');
        setVal('cfg-zpl-order-height', data.zpl_order_height || '150');
        setVal('cfg-zpl-order-template', data.zpl_order_template || window.DEFAULT_ORDER_ZPL);

        setVal('cfg-zpl-location-width', data.zpl_location_width || '50');
        setVal('cfg-zpl-location-height', data.zpl_location_height || '25');
        setVal('cfg-zpl-location-template', data.zpl_location_template || window.DEFAULT_LOCATION_ZPL);

        console.log("[SETTINGS] Datos aplicados al formulario con éxito.");

        if (typeof toggleGoogleFields === 'function') toggleGoogleFields();

        if (typeof loadIntegrations === 'function') {
            loadIntegrations();
        }
    } catch (err) {
        console.error("[SETTINGS ERROR]:", err);
    }
}

async function saveSettings(e) {
    if (e) e.preventDefault();
    const getVal = (id) => {
        const el = document.getElementById(id);
        return el ? el.value.trim() : '';
    };

    const ssoEl = document.getElementById('cfg-enable-google-sso');
    const enable_google_sso = ssoEl ? (ssoEl.checked ? 'true' : 'false') : 'false';

    const payload = {
        app_name: getVal('cfg-app-name'),
        company_cuit: getVal('cfg-company-cuit'),
        session_timeout_minutes: getVal('cfg-session-timeout-minutes'),
        max_login_attempts: getVal('cfg-max-login-attempts'),
        lockout_time_minutes: getVal('cfg-lockout-time-minutes'),
        transfer_number_prefix: getVal('cfg-transfer-number-prefix'),
        sales_order_prefix: getVal('cfg-sales-order-prefix'),
        correlative_zeros_pad: getVal('cfg-correlative-zeros-pad'),
        enable_stock_management: getVal('cfg-enable-stock-management'),
        allow_negative_stock: getVal('cfg-allow-negative-stock'),
        enable_lots_expiration: getVal('cfg-enable-lots-expiration'),
        enable_committed_stock: getVal('cfg-enable-committed-stock'),
        require_mobile_reception: getVal('cfg-require-mobile-reception'),
        allow_multiproduct_locations: getVal('cfg-allow-multiproduct'),
        enable_item_dimensions: getVal('cfg-enable-item-dimensions'),
        auto_complete_picking: getVal('cfg-auto-complete-picking'),
        default_inventory_count_type: getVal('cfg-default-inventory-count-type'),
        
        enable_google_sso: enable_google_sso,
        google_client_id: getVal('cfg-google-client-id'),
        google_client_secret: getVal('cfg-google-client-secret'),
        google_allowed_domain: getVal('cfg-google-allowed-domain'),

        default_print_queue: getVal('cfg-default-print-queue'),
        zpl_item_width: getVal('cfg-zpl-item-width') || '38',
        zpl_item_height: getVal('cfg-zpl-item-height') || '20',
        zpl_item_template: getVal('cfg-zpl-item-template'),
        zpl_order_width: getVal('cfg-zpl-order-width') || '100',
        zpl_order_height: getVal('cfg-zpl-order-height') || '150',
        zpl_order_template: getVal('cfg-zpl-order-template'),
        zpl_location_width: getVal('cfg-zpl-location-width') || '50',
        zpl_location_height: getVal('cfg-zpl-location-height') || '25',
        zpl_location_template: getVal('cfg-zpl-location-template')
    };

    const r = await fetchAPI('/api/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    });

    if (r) {
        if (typeof showToast === 'function') showToast('Configuración guardada exitosamente.', 'success');
        await loadSettings();
    }
}

// === GOOGLE OAUTH2 MODAL ===
function editGoogleSSO() {
    if (typeof openModal === 'function') {
        openModal('modal-edit-google-sso');
    }
    toggleGoogleFields();
}

async function saveGoogleSSOModal(event) {
    event.preventDefault();
    await saveSettings(event);
    if (typeof closeModal === 'function') {
        closeModal('modal-edit-google-sso');
    }
}

function toggleGoogleFields() {
    const checkEl = document.getElementById('cfg-enable-google-sso');
    const isChecked = checkEl ? checkEl.checked : false;
    const fields = document.getElementById('google-sso-fields');
    if (fields) {
        fields.style.display = isChecked ? 'block' : 'none';
    }
}

// === ZPL MODAL ===
function editZPLSettings(tabId) {
    if (typeof openModal === 'function') {
        openModal('modal-edit-zpl');
    }
    // Buscamos el botón de la pestaña que le corresponde para activarlo visualmente
    const btn = document.querySelector(`[onclick*="${tabId}"]`);
    switchZPLTab(tabId, btn);
}

async function saveZPLModal(event) {
    event.preventDefault();
    await saveSettings(event);
    if (typeof closeModal === 'function') {
        closeModal('modal-edit-zpl');
    }
}

function switchZPLTab(tabId, btn) {
    document.querySelectorAll('.zpl-tab-content').forEach(t => t.style.display = 'none');
    document.querySelectorAll('.zpl-tab-btn').forEach(b => {
        b.style.borderColor = 'transparent';
        b.style.color = 'var(--text-muted)';
    });
    
    const target = document.getElementById(tabId);
    if (target) target.style.display = 'block';
    if (btn) {
        btn.style.borderColor = 'var(--primary-blue)';
        btn.style.color = 'var(--primary-blue)';
    }
}

window.loadSettings = loadSettings;
window.saveSettings = saveSettings;
window.editGoogleSSO = editGoogleSSO;
window.saveGoogleSSOModal = saveGoogleSSOModal;
window.toggleGoogleFields = toggleGoogleFields;
window.editZPLSettings = editZPLSettings;
window.saveZPLModal = saveZPLModal;
window.switchZPLTab = switchZPLTab;