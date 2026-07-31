// frontend/js/settings.js

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

        setVal('cfg-app-name', data.app_name || '');
        setVal('cfg-company-cuit', data.company_cuit || '');
        setVal('cfg-zebra-ip', data.zebra_ip || '');

        setVal('cfg-enable-stock-management', String(data.enable_stock_management ?? 'true'));
        setVal('cfg-allow-negative-stock', String(data.allow_negative_stock ?? 'false'));
        setVal('cfg-enable-committed-stock', String(data.enable_committed_stock ?? 'true'));

        setVal('cfg-require-mobile-reception', String(data.require_mobile_reception ?? 'false'));
        setVal('cfg-allow-multiproduct', String(data.allow_multiproduct_locations ?? 'false'));
        setVal('cfg-enable-item-dimensions', String(data.enable_item_dimensions ?? 'false'));

        setVal('cfg-zpl-item-width', data.zpl_item_width || '38');
        setVal('cfg-zpl-item-height', data.zpl_item_height || '20');
        setVal('cfg-zpl-item-template', data.zpl_item_template || data.zpl_template || window.DEFAULT_ITEM_ZPL);

        setVal('cfg-zpl-order-width', data.zpl_order_width || '100');
        setVal('cfg-zpl-order-height', data.zpl_order_height || '150');
        setVal('cfg-zpl-order-template', data.zpl_order_template || window.DEFAULT_ORDER_ZPL);

        setVal('cfg-tracker360-api-key', data.tracker360_api_key || data.api_key || '');
        console.log("[SETTINGS] Datos aplicados al formulario con éxito.");
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

    const payload = {
        app_name: getVal('cfg-app-name'),
        company_cuit: getVal('cfg-company-cuit'),
        zebra_ip: getVal('cfg-zebra-ip'),
        enable_stock_management: getVal('cfg-enable-stock-management'),
        allow_negative_stock: getVal('cfg-allow-negative-stock'),
        enable_committed_stock: getVal('cfg-enable-committed-stock'),
        require_mobile_reception: getVal('cfg-require-mobile-reception'),
        allow_multiproduct_locations: getVal('cfg-allow-multiproduct'),
        enable_item_dimensions: getVal('cfg-enable-item-dimensions'),

        zpl_item_width: getVal('cfg-zpl-item-width') || '38',
        zpl_item_height: getVal('cfg-zpl-item-height') || '20',
        zpl_item_template: getVal('cfg-zpl-item-template'),

        zpl_order_width: getVal('cfg-zpl-order-width') || '100',
        zpl_order_height: getVal('cfg-zpl-order-height') || '150',
        zpl_order_template: getVal('cfg-zpl-order-template')
    };

    const r = await fetchAPI('/api/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    });

    if (r) {
        if (typeof showToast === 'function') showToast('Configuración guardada exitosamente.');
        await loadSettings();
    }
}

window.loadSettings = loadSettings;
window.saveSettings = saveSettings;
