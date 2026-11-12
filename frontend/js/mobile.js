// === CONTROLADOR NATIVO COLECTORA MÓVIL (TRACKER360) ===
// 100% SOBERANO: ZERO DEPENDENCIAS EXTERNAS NI CDN

let activeModule = null; // 'PICKING' | 'RECEPTION' | 'TRANSFER' | 'WAVE'
let moduleState = 'SKU'; // 'SKU' | 'LOCATION' | 'QTY'

let activeDocumentData = null;
let activeWaveOrders = [];
let activeCameraStream = null;
let cameraDetectorInterval = null;
let genericCameraStream = null;
let targetInputIdForCamera = null;

let appSettingsCache = null;

// =========================================================================================
// === UTILIDADES NATIVAS ==================================================================
// =========================================================================================

window.onload = async () => {
    try {
        appSettingsCache = await fetchAPI('/api/settings');
        document.body.classList.toggle('con-lotes', appSettingsCache && appSettingsCache.enable_lots_expiration === 'true');
    } catch (e) { console.warn("No se pudo cargar configuración inicial."); }
    pintarRed();
};

function escapeHTML(str) {
    if (str === null || str === undefined) return '';
    return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

// Argumento de texto para handlers inline: data-on-click="fn(${jsArg(valor)})".
// escapeHTML solo no alcanza ahi (el navegador decodifica &#39; antes de ejecutar el JS).
function jsArg(value) {
    return escapeHTML(JSON.stringify(value === null || value === undefined ? '' : String(value)));
}

// Estados en castellano (lo que no esta en la lista se muestra tal cual).
const ESTADOS_COLECTORA = {
    pedido: { PENDING: 'Pendiente', IN_PROGRESS: 'En preparación', COMPLETED: 'Para empacar' },
    remito: { PENDING: 'Pendiente de control', PENDING_CONTROL: 'Pendiente de control', IN_PROGRESS: 'En control', COMPLETED: 'Ingresado', COMPLETED_DIFF: 'Con diferencias' },
    traspaso: { PENDING: 'Pendiente', PENDING_CONTROL: 'Pendiente', IN_PROGRESS: 'En curso', COMPLETED: 'Completado' },
    conteo: { HOT: 'En caliente', COLD: 'En frío' },
};
function estadoColectora(tipo, estado) { return (ESTADOS_COLECTORA[tipo] || {})[estado] || estado || ''; }

// Error de una accion: si ya lo mostro fetchAPI, solo suena el tono (no se repite el cartel).
function avisarError(e) {
    if (e && e.mostrado) playErrorTone();
    else showToast((e && e.message) || 'Ocurrió un error.', 'error');
}

function handleScannerEnter(event, nextFieldId, formId) {
    if (event.key === 'Enter') {
        event.preventDefault();
        if (nextFieldId) {
            const nextEl = document.getElementById(nextFieldId);
            if (nextEl) {
                nextEl.focus();
                nextEl.select();
            }
        } else if (formId) {
            const form = document.getElementById(formId);
            if (form) {
                const submitEvent = new Event('submit', { cancelable: true, bubbles: true });
                form.dispatchEvent(submitEvent);
            }
        }
    }
}

// La sesion vive en el servidor (cookie HttpOnly): hay que cerrarla ahi, como en el panel.
function logout() {
    logoutUser();
}

function playNativeBeep(freq = 880, type = 'sine', duration = 0.15) {
    try {
        const AudioCtx = window.AudioContext || window.webkitAudioContext;
        if (!AudioCtx) return;
        const ctx = new AudioCtx();
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.type = type;
        osc.frequency.value = freq;
        osc.connect(gain);
        gain.connect(ctx.destination);
        osc.start();
        osc.stop(ctx.currentTime + duration);
    } catch(e) {}
}

function playSuccessChime() {
    playNativeBeep(660, 'sine', 0.1);
    setTimeout(() => playNativeBeep(880, 'sine', 0.15), 100);
}

function playErrorTone() {
    playNativeBeep(220, 'square', 0.3);
}

function showToast(msg, type = 'success') {
    const container = document.getElementById('toast-container');
    if (!container) return;

    const toast = document.createElement('div');
    toast.className = `toast toast-${type === 'danger' ? 'error' : type}`;   // fetchAPI avisa con "danger"
    toast.textContent = msg;
    container.appendChild(toast);

    if (type === 'success') playSuccessChime();
    else if (type === 'error') playErrorTone();

    // Los errores quedan mas tiempo: el operario tiene las manos ocupadas y no siempre mira la pantalla.
    setTimeout(() => toast.remove(), type === 'success' ? 3000 : 7000);
}

// Cabecera de cada pantalla: [rotulo, titulo]. Las de escaneo la ponen al abrir el documento (setCabecera).
const VISTAS = {
    'view-home': ['En vivo', 'Terminal Operativa'],
    'view-picking': ['Picking', 'Pedidos Pendientes'],
    'view-receptions': ['Recepción', 'Remitos Pendientes'],
    'view-transfers': ['Traspasos', 'Traspasos Activos'],
    'view-inventory': ['Inventario', 'Conteos Pendientes'],
    'view-spot-check': ['Control puntual', 'Auditoría Rápida'],
};
// A donde vuelve cada pantalla de escaneo (las demas vuelven al inicio).
const VISTA_PADRE = {
    'view-picking-scan': ['view-picking', loadPicking],
    'view-wave-scan': ['view-picking', loadPicking],
    'view-reception-scan': ['view-receptions', loadReceptions],
    'view-transfer-scan': ['view-transfers', loadTransfers],
    'view-inventory-scan': ['view-inventory', loadInventory],
};
let vistaActual = 'view-home';

function setCabecera(rotulo, titulo, mono = false) {
    document.getElementById('header-eyebrow').textContent = rotulo;
    const t = document.getElementById('header-title');
    t.textContent = titulo;
    t.classList.toggle('mono', mono);
}

function openView(viewId, callback) {
    document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
    const target = document.getElementById(viewId);
    if (target) target.classList.add('active');
    vistaActual = viewId;

    const enInicio = viewId === 'view-home';
    document.getElementById('btn-back').hidden = enInicio;
    document.getElementById('hdr-logo').hidden = !enInicio;
    if (VISTAS[viewId]) setCabecera(...VISTAS[viewId]);
    window.scrollTo(0, 0);

    stopPickingCameraStream();
    if (enInicio) cargarPendientes();

    if (typeof callback === 'function') callback();
}

function goHome() {
    openView('view-home');
}

// Volver: del escaneo a su lista, de la lista al inicio. Tambien con Esc.
function volver() {
    const padre = VISTA_PADRE[vistaActual];
    if (padre) openView(padre[0], padre[1]);
    else goHome();
}
document.addEventListener('keydown', e => {
    if (e.key !== 'Escape') return;
    if (genericCameraStream) stopCameraScanner();
    else if (vistaActual !== 'view-home') volver();
});

// Aviso de red: franja roja bajo la cabecera y punto rojo. Sin red, fetchAPI rechaza la lectura y avisa.
function pintarRed() {
    const sinRed = !navigator.onLine;
    document.getElementById('net-banner').style.display = sinRed ? 'block' : 'none';
    document.getElementById('hdr-live').classList.toggle('off', sinRed);
}
window.addEventListener('online', pintarRed);
window.addEventListener('offline', pintarRed);

// Cuanto hay pendiente en cada tarjeta del inicio. Va con fetch directo: si falla no molesta con avisos.
async function cargarPendientes() {
    const pedir = async (url) => {
        try { const r = await fetch(url, { credentials: 'include' }); return r.ok ? await r.json() : null; } catch (e) { return null; }
    };
    const [pedidos, remitos, traspasos, conteos] = await Promise.all(
        ['/api/picking/orders', '/api/reception/remitos', '/api/transfers/orders', '/api/inventory/sessions?status=OPEN'].map(pedir));
    const poner = (id, lista) => { const el = document.getElementById(id); if (el) el.textContent = Array.isArray(lista) ? lista.length : ''; };
    poner('n-picking', pedidos); poner('n-receptions', remitos); poner('n-transfers', traspasos); poner('n-inventory', conteos);
    const urgentes = (pedidos || []).filter(o => o.priority > 0).length;
    const tagU = document.getElementById('tag-urgentes');
    if (tagU) { tagU.hidden = !urgentes; tagU.textContent = urgentes === 1 ? '1 URGENTE' : `${urgentes} URGENTES`; }
    const tagA = document.getElementById('tag-asignado');
    if (tagA) tagA.hidden = !(conteos || []).some(s => s.assigned_operator && s.assigned_operator === window.usuarioActual);
}

function ajustarCantidad(inputId, delta) {
    const el = document.getElementById(inputId);
    if (el) el.value = Math.max(1, Math.round(((parseFloat(el.value) || 0) + delta) * 100) / 100);
}

// Tarjeta de una lista (pedido, remito, traspaso, conteo): numero, badge, renglones, etiquetas y avance.
function tarjetaLista(accion, titulo, badge, renglones, { tags = '', flex = false, pct = null } = {}) {
    return `<button type="button" class="list-item${flex ? ' flex' : ''}" ${accion}>
        <span class="li-1"><b>${escapeHTML(titulo)}</b>${badge}</span>
        ${renglones.map(r => `<span class="li-2">${escapeHTML(r)}</span>`).join('')}
        <span class="li-tags">${tags}</span>
        ${pct === null ? '' : `<span class="barra"><i style="width:${Math.max(0, Math.min(100, pct))}%"></i></span>`}
    </button>`;
}

// =========================================================================================
// === CÁMARA CONTINUA EN VIVO NATIVA HTML5 ================================================
// =========================================================================================

async function startModuleCameraStream(videoElementId, containerClass) {
    const video = document.getElementById(videoElementId);
    const container = document.querySelector(`#${videoElementId}`).parentElement;
    if (!video || !container) return;

    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        container.classList.add('sin-camara');
        return;
    }

    try {
        if (activeCameraStream) stopPickingCameraStream();
        const constraints = { video: { facingMode: { ideal: "environment" }, width: { ideal: 640 }, height: { ideal: 480 } } };
        activeCameraStream = await navigator.mediaDevices.getUserMedia(constraints);
        video.srcObject = activeCameraStream;
        container.classList.remove('sin-camara');
        await video.play();

        if ('BarcodeDetector' in window) {
            const detector = new BarcodeDetector({ formats: ['qr_code', 'code_128', 'ean_13', 'code_39', 'data_matrix'] });
            let isProcessing = false;
            cameraDetectorInterval = setInterval(async () => {
                if (isProcessing || video.readyState !== video.HAVE_ENOUGH_DATA) return;
                isProcessing = true;
                try {
                    const barcodes = await detector.detect(video);
                    if (barcodes && barcodes.length > 0) {
                        const rawVal = barcodes[0].rawValue;
                        if (rawVal && rawVal.trim()) onCameraCodeDetected(rawVal.trim());
                    }
                } catch (e) {}
                isProcessing = false;
            }, 250);
        }
    } catch (e) {
        console.warn("[CÁMARA OPERATIVA]: Acceso denegado o dispositivo sin cámara.", e);
        container.classList.add('sin-camara');
        showToast("Permiso de cámara denegado. Use teclado manual.", "warning");
    }
}

function stopPickingCameraStream() {
    if (cameraDetectorInterval) {
        clearInterval(cameraDetectorInterval);
        cameraDetectorInterval = null;
    }
    if (activeCameraStream) {
        activeCameraStream.getTracks().forEach(track => track.stop());
        activeCameraStream = null;
    }
}

function onCameraCodeDetected(code) {
    if (activeModule === 'PICKING') {
        if (moduleState === 'SKU') {
            const el = document.getElementById('pick-sku');
            if (el) { el.value = code; onModuleInputProcess('PICKING', 'sku'); }
        } else if (moduleState === 'LOCATION') {
            const el = document.getElementById('pick-loc');
            if (el) { el.value = code; onModuleInputProcess('PICKING', 'loc'); }
        }
    } else if (activeModule === 'WAVE') {
        if (moduleState === 'SKU') {
            const el = document.getElementById('wave-sku');
            if (el) { el.value = code; onModuleInputProcess('WAVE', 'sku'); }
        } else if (moduleState === 'LOCATION') {
            const el = document.getElementById('wave-loc');
            if (el) { el.value = code; onModuleInputProcess('WAVE', 'loc'); }
        }
    } else if (activeModule === 'RECEPTION') {
        if (moduleState === 'SKU') {
            const el = document.getElementById('rec-sku');
            if (el) { el.value = code; onModuleInputProcess('RECEPTION', 'sku'); }
        } else if (moduleState === 'LOCATION') {
            const el = document.getElementById('rec-loc');
            if (el) { el.value = code; onModuleInputProcess('RECEPTION', 'loc'); }
        }
    } else if (activeModule === 'TRANSFER') {
        if (moduleState === 'SKU') {
            const el = document.getElementById('tr-sku');
            if (el) { el.value = code; onModuleInputProcess('TRANSFER', 'sku'); }
        } else if (moduleState === 'LOCATION') {
            const el = document.getElementById('tr-dest-loc');
            if (el) { el.value = code; onModuleInputProcess('TRANSFER', 'loc'); }
        }
    }
}

async function startCameraScanner(inputId) {
    targetInputIdForCamera = inputId;
    const modal = document.getElementById('camera-modal');
    const video = document.getElementById('camera-video');
    if (!modal || !video) return;

    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        showToast("El navegador bloquea la cámara sin HTTPS.", "error");
        return;
    }

    modal.style.display = 'flex';
    try {
        genericCameraStream = await navigator.mediaDevices.getUserMedia({video: {facingMode: "environment"}});
        video.srcObject = genericCameraStream;
        await video.play();
        if ('BarcodeDetector' in window) {
            const detector = new BarcodeDetector({ formats: ['qr_code', 'code_128', 'ean_13', 'code_39', 'data_matrix'] });
            cameraDetectorInterval = setInterval(async () => {
                try {
                    const codes = await detector.detect(video);
                    if(codes.length > 0) {
                        const tgt = document.getElementById(targetInputIdForCamera);
                        if(tgt) { tgt.value = codes[0].rawValue; tgt.focus(); }
                        stopCameraScanner();
                        playSuccessChime();
                    }
                } catch(e){}
            }, 300);
        }
    } catch(e) {
        showToast("No se pudo acceder a la cámara", "error");
        stopCameraScanner();
    }
}

function stopCameraScanner() {
    if(cameraDetectorInterval) { clearInterval(cameraDetectorInterval); cameraDetectorInterval = null; }
    if(genericCameraStream) { genericCameraStream.getTracks().forEach(t => t.stop()); genericCameraStream = null; }
    const modal = document.getElementById('camera-modal');
    if(modal) modal.style.display = 'none';
}

// =========================================================================================
// === CONTROLADOR DE PASOS Y MÁQUINA DE ESTADOS REUTILIZABLE ==============================
// =========================================================================================

function prefijoModulo(moduleName) {
    return moduleName === 'PICKING' ? 'pick' : (moduleName === 'RECEPTION' ? 'rec' : (moduleName === 'WAVE' ? 'wave' : 'tr'));
}

// Lo que falta de una linea (pickear o mover). La recepcion es ciega: no hay "falta".
function faltaLinea(moduleName, l) {
    return moduleName === 'TRANSFER' ? l.quantity_sent - l.quantity_received : l.quantity_requested - l.quantity_picked;
}

// Linea que se esta trabajando: la del SKU leido o, si todavia no se leyo ninguno, la proxima pendiente de la ruta.
let lineaObjetivo = null;
function objetivoActual(moduleName) {
    if (lineaObjetivo) return lineaObjetivo;
    if (moduleName === 'RECEPTION' || !activeDocumentData) return null;
    return (activeDocumentData.lines || []).find(l => faltaLinea(moduleName, l) > 0) || null;
}

// Tarjeta objetivo: que hay que leer y donde. Solo refleja el estado; no valida nada.
function pintarObjetivo(moduleName) {
    const caja = document.getElementById(`${prefijoModulo(moduleName)}-datos`);
    if (!caja) return;
    const l = objetivoActual(moduleName);
    if (!l) { caja.innerHTML = ''; return; }
    const dato = (k, v, cls = '') => `<div class="${cls}"><span>${k}</span><b>${escapeHTML(v)}</b></div>`;
    const donde = moduleName === 'TRANSFER' ? dato('Destino', l.destination_location || 'Gral')
        : moduleName === 'RECEPTION' ? (l.location_code ? dato('Sugerido', l.location_code) : '')
        : dato('Ubicación', l.suggested_locations || '—');
    const falta = moduleName === 'RECEPTION' ? ''
        : dato(moduleName === 'TRANSFER' ? 'Faltan' : 'Falta pickear', `${faltaLinea(moduleName, l)} u.`, 'ancho');
    caja.innerHTML = dato('SKU', l.sku, 'mono') + donde + (l.description ? dato('Artículo', l.description, 'ancho') : '') + falta;
}

function filaHoja(sku, descripcion, donde, cantidad, completa, actual) {
    return `<div class="fila${completa ? ' ok' : ''}${actual ? ' on' : ''}">
        <i class="est">${completa ? '✓' : ''}</i>
        <div class="min0"><b>${escapeHTML(sku)}</b><span>${escapeHTML(descripcion || '')}</span><small>${escapeHTML(donde)}</small></div>
        <b class="cnt">${escapeHTML(cantidad)}</b>
    </div>`;
}

// Hoja de ruta de la tarea activa: todas las lineas, las completas con tilde y la que se esta trabajando resaltada.
const HOJA_COMPLETA = { PICKING: 'Pedido completo · listo para empacar', WAVE: '¡Toda la Ola recolectada con éxito!', TRANSFER: '¡Traspaso completado totalmente!' };
function pintarHoja(moduleName) {
    const prefix = prefijoModulo(moduleName);
    const cuerpo = document.getElementById(`${prefix}-items-sheet-body`);
    const badge = document.getElementById(`${prefix}-items-count-badge`);
    if (!cuerpo || !activeDocumentData) return;
    const lines = activeDocumentData.lines || [];
    const actual = objetivoActual(moduleName);

    if (moduleName === 'RECEPTION') {
        // Control ciego: se muestra lo escaneado, nunca lo que dice el remito.
        const estado = { PENDING: 'En cuarentena', APPROVED: 'Aprobado', REJECTED: 'Rechazado' };
        const noEsperados = activeDocumentData.unexpected || [];
        if (badge) badge.textContent = `${lines.length} art.`;
        cuerpo.innerHTML = lines.map(l => filaHoja(l.sku, l.description, `Sugerido: ${l.location_code || 'General'}`, `Escaneado: ${l.scanned}`, false, !!actual && actual.sku === l.sku)).join('')
            + (noEsperados.length ? '<div class="sub-rot">No esperados</div>' + noEsperados.map(u =>
                filaHoja(u.sku, estado[u.status] || u.status, '', `x ${u.quantity}`, false, false)).join('') : '');
        return;
    }

    const pendientes = lines.filter(l => faltaLinea(moduleName, l) > 0);
    if (badge) badge.textContent = `${lines.length - pendientes.length}/${lines.length} listos`;
    const fin = pendientes.length || !lines.length ? '' : `<div class="listo"><b>${HOJA_COMPLETA[moduleName]}</b><button type="button" data-on-click="volver()">Volver a la lista</button></div>`;
    cuerpo.innerHTML = fin + lines.map(l => {
        const falta = faltaLinea(moduleName, l);
        const total = moduleName === 'TRANSFER' ? l.quantity_sent : l.quantity_requested;
        const donde = moduleName === 'TRANSFER' ? `Origen: ${l.origin_location || 'Gral'} > Destino: ${l.destination_location || 'Gral'}`
            : `Ruta / ubicación: ${l.suggested_locations || '—'}`;
        return filaHoja(l.sku, l.description, donde, `${total - falta}/${total}`, falta <= 0, falta > 0 && !!actual && actual.sku === l.sku);
    }).join('');
}

function setModuleStepState(moduleName, newState) {
    activeModule = moduleName;
    moduleState = newState;

    const prefix = prefijoModulo(moduleName);
    const banner = document.getElementById({ pick: 'picking', rec: 'reception', wave: 'wave', tr: 'transfer' }[prefix] + '-step-banner');
    const grupos = ['sku', 'loc', 'qty'].map(g => document.getElementById(`grp-${prefix}-${g}`));
    const skuInp = document.getElementById(`${prefix}-sku`);
    const locInp = document.getElementById(prefix === 'tr' ? 'tr-dest-loc' : `${prefix}-loc`);
    const qtyInp = document.getElementById(`${prefix}-qty`);

    const paso = { SKU: 0, LOCATION: 1, QTY: 2 }[newState];
    const textos = ['PASO 1: ESCANEE CÓDIGO/QR DEL ARTÍCULO', `PASO 2: ESCANEE UBICACIÓN PARA ${skuInp ? skuInp.value.toUpperCase() : ''}`, 'PASO 3: CONFIRME CANTIDAD A PROCESAR'];
    if (banner) { banner.className = 'step-banner step-banner-' + ['sku', 'loc', 'qty'][paso]; banner.textContent = textos[paso]; }
    grupos.forEach((g, i) => { if (g) g.style.display = i <= paso ? 'block' : 'none'; });

    // Stepper de 3 segmentos: el actual en su color, los anteriores en verde.
    const pasos = document.getElementById(`${prefix}-pasos`);
    if (pasos) {
        pasos.dataset.paso = newState;
        [...pasos.children].forEach((el, i) => { el.className = i < paso ? 'ok' : (i === paso ? 'on' : ''); });
    }

    if (newState === 'SKU') {
        lineaObjetivo = null;
        if (skuInp) { skuInp.value = ''; skuInp.focus(); }
    } else if (newState === 'LOCATION') {
        if (locInp) { locInp.value = ''; locInp.focus(); }
    } else if (newState === 'QTY') {
        const de = document.getElementById(`${prefix}-qty-de`);
        if (de) de.textContent = lineaObjetivo && moduleName !== 'RECEPTION' ? `de ${faltaLinea(moduleName, lineaObjetivo)} pendientes` : '';
        if (qtyInp) { qtyInp.focus(); qtyInp.select(); }
    }
    pintarObjetivo(moduleName);
    pintarHoja(moduleName);
}

// Escaneo de SKU y ubicacion: se valida al terminar (Enter). Los lectores laser y de colectora mandan Enter al final
// del codigo; validar en cada tecla rompia el escaneo en el primer caracter.
function scanEnter(event, moduleName, inputType) {
    if (event.key !== 'Enter') return;
    event.preventDefault();
    onModuleInputProcess(moduleName, inputType);
}

function onModuleInputProcess(moduleName, inputType) {
    if (!activeDocumentData) return;
    const lines = activeDocumentData.lines || [];

    const prefix = moduleName === 'PICKING' ? 'pick' : (moduleName === 'RECEPTION' ? 'rec' : (moduleName === 'WAVE' ? 'wave' : 'tr'));
    const skuInp = document.getElementById(`${prefix}-sku`);
    const qtyInp = document.getElementById(`${prefix}-qty`);
    const locInp = document.getElementById(prefix === 'tr' ? 'tr-dest-loc' : `${prefix}-loc`);

    if (inputType === 'sku') {
        const scannedSku = skuInp ? skuInp.value.trim().toUpperCase() : '';
        if (!scannedSku) return;

        let lineMatch = null;
        if (moduleName === 'PICKING') {
            lineMatch = lines.find(l => l.sku.toUpperCase() === scannedSku && l.quantity_picked < l.quantity_requested);
            if (lineMatch) qtyInp.value = lineMatch.quantity_requested - lineMatch.quantity_picked;
        } else if (moduleName === 'WAVE') {
            lineMatch = lines.find(l => l.sku.toUpperCase() === scannedSku && l.quantity_picked < l.quantity_requested);
            if (lineMatch) qtyInp.value = lineMatch.quantity_requested - lineMatch.quantity_picked;
        } else if (moduleName === 'RECEPTION') {
            // Control ciego: el detalle no trae cantidades del remito. Se reconoce el SKU y la cantidad la carga el
            // operario (lo que cuenta). Un SKU que no figura sigue igual: al confirmar se ofrece como no esperado.
            lineMatch = lines.find(l => l.sku.toUpperCase() === scannedSku);
            if (qtyInp) qtyInp.value = '';
            lineaObjetivo = lineMatch || { sku: scannedSku, description: 'No figura en este remito' };
            if (!lineMatch) {
                playErrorTone();
                showToast(`El SKU '${scannedSku}' no figura en este remito: al confirmar se puede registrar como no esperado.`, 'warning');
                setModuleStepState(moduleName, 'LOCATION');
                return;
            }
        } else if (moduleName === 'TRANSFER') {
            lineMatch = lines.find(l => l.sku.toUpperCase() === scannedSku && l.quantity_received < l.quantity_sent);
            if (lineMatch) qtyInp.value = lineMatch.quantity_sent - lineMatch.quantity_received;
        }

        if (lineMatch) {
            lineaObjetivo = lineMatch;
            playSuccessChime();
            setModuleStepState(moduleName, 'LOCATION');
        } else {
            playErrorTone();
            showToast(`El SKU '${scannedSku}' no pertenece a la tarea activa o ya está completo.`, 'error');
            if (skuInp) skuInp.value = '';
        }
    } else if (inputType === 'loc') {
        playSuccessChime();
        setModuleStepState(moduleName, 'QTY');
    }
}

// =========================================================================================
// === 1. PICKING MÓVIL GUIADO E INTELIGENTE ===============================================
// =========================================================================================

// Tipo de envio del pedido, con los nombres del panel. Flex (self_service) se marca con barra roja.
const ENVIO_PEDIDO = { CROSS_DOCKING: 'Colecta', SELF_SERVICE: 'Flex', FULFILLMENT: 'Full', DROP_OFF: 'Correo', MOSTRADOR: 'Mostrador' };

async function loadPicking() {
    const container = document.getElementById('picking-list');
    const waveContainer = document.getElementById('wave-picking-container');

    if (appSettingsCache && appSettingsCache.enable_wave_picking === 'true' && waveContainer) {
        waveContainer.style.display = 'block';
    }

    if (!container) return;
    container.innerHTML = '<p class="vacio">Cargando pedidos pendientes...</p>';

    try {
        const orders = await fetchAPI('/api/picking/orders');
        if (!orders || orders.length === 0) {
            container.innerHTML = '<p class="vacio">No hay pedidos pendientes de armado.</p>';
            return;
        }
        container.innerHTML = orders.map(o => {
            const tipo = String(o.shipping_type || '').toUpperCase();
            const flex = tipo === 'SELF_SERVICE';
            const envio = ENVIO_PEDIDO[tipo] || o.shipping_type || '';
            const tags = (o.priority > 0 ? '<span class="badge badge-danger">URGENTE</span>' : '')
                + (envio ? `<span class="badge ${flex ? 'badge-danger' : 'badge-neutral'}">${escapeHTML(envio)}</span>` : '');
            return tarjetaLista(`data-on-click="startOrderPicking(${jsArg(o.document_number)})"`, o.document_number,
                `<span class="badge ${o.status === 'PENDING' ? 'badge-warning' : 'badge-info'}">${escapeHTML(estadoColectora('pedido', o.status))}</span>`,
                [`Cliente: ${o.company_name}`, `A recolectar: ${o.requested_items || 0} unidades (${o.total_items || 0} ítems)`],
                { tags, flex, pct: o.requested_items ? (o.picked_items || 0) / o.requested_items * 100 : 0 });
        }).join('');
    } catch (e) {
        container.innerHTML = `<p class="vacio err">Error de conexión: ${escapeHTML(e.message)}</p>`;
    }
}

async function startOrderPicking(documentNumber) {
    openView('view-picking-scan');
    setCabecera('Pedido', documentNumber, true);
    document.getElementById('pick-number').value = documentNumber;

    await refreshPickingOrderSheet(documentNumber);
    startModuleCameraStream('picking-video-stream', 'picking-camera-container');
    setModuleStepState('PICKING', 'SKU');
}

async function refreshPickingOrderSheet(documentNumber) {
    try {
        activeDocumentData = await fetchAPI(`/api/picking/orders/${encodeURIComponent(documentNumber)}`);
        pintarHoja('PICKING');
    } catch (e) { console.error(e); }
}

async function handlePickingFormSubmit(event) {
    event.preventDefault();
    const docNumber = document.getElementById('pick-number').value;
    const sku = document.getElementById('pick-sku').value.trim().toUpperCase();
    const loc = document.getElementById('pick-loc').value.trim().toUpperCase() || 'GENERAL';
    const qty = parseFloat(document.getElementById('pick-qty').value);

    if (!sku || isNaN(qty) || qty <= 0) { showToast("Verifique SKU y cantidad.", "error"); return; }

    try {
        const res = await fetchAPI(`/api/picking/orders/${encodeURIComponent(docNumber)}/scan`, {
            method: 'POST', body: { sku: sku, quantity: qty, location_code: loc }
        });
        showToast(res.message, "success");
        if (res.order_completed) {
            showToast("¡Pedido completado totalmente!", "success");
            setTimeout(() => { openView('view-picking', loadPicking); }, 1000);
        } else {
            await refreshPickingOrderSheet(docNumber);
            setModuleStepState('PICKING', 'SKU');
        }
    } catch (e) { avisarError(e); }
}

// === FASE 3: PICKING POR OLAS (WAVE) ===
async function startWavePicking(limit) {
    try {
        const data = await fetchAPI(`/api/picking/waves/pending?limit=${limit}`);
        if (!data || !data.order_numbers || data.order_numbers.length === 0) {
            showToast("No hay pedidos pendientes para agrupar.", "warning");
            return;
        }

        activeWaveOrders = data.order_numbers;
        activeDocumentData = data; 
        
        openView('view-wave-scan');
        setCabecera('Ola de Picking Activa', `${activeWaveOrders.length} pedidos`);
        document.getElementById('wave-orders-subtitle').textContent = `Agrupando ${activeWaveOrders.length} pedidos en una sola ruta`;
        
        refreshWaveOrderSheet(data.lines);
        startModuleCameraStream('wave-video-stream', 'picking-camera-container');
        setModuleStepState('WAVE', 'SKU');
        showToast(`Ola generada con éxito. Ruta optimizada.`, "success");

    } catch (e) {
        avisarError(e);
    }
}

function refreshWaveOrderSheet() {
    pintarHoja('WAVE');
}

async function handleWaveFormSubmit(event) {
    event.preventDefault();
    if (activeWaveOrders.length === 0) return;

    const sku = document.getElementById('wave-sku').value.trim().toUpperCase();
    const loc = document.getElementById('wave-loc').value.trim().toUpperCase() || 'GENERAL';
    const qty = parseFloat(document.getElementById('wave-qty').value);

    if (!sku || isNaN(qty) || qty <= 0) { showToast("Verifique SKU y cantidad.", "error"); return; }

    try {
        const payload = {
            order_numbers: activeWaveOrders,
            sku: sku,
            quantity: qty,
            location_code: loc
        };

        const res = await fetchAPI('/api/picking/waves/scan', {
            method: 'POST', body: payload
        });

        showToast(res.message, "success");

        if (res.wave_completed) {
            showToast("¡Ola completada totalmente!", "success");
            activeWaveOrders = [];
            setTimeout(() => { openView('view-picking', loadPicking); }, 1500);
        } else {
            const freshData = await fetchAPI(`/api/picking/waves/pending?limit=${activeWaveOrders.length}`);
            activeDocumentData = freshData;
            refreshWaveOrderSheet(freshData.lines);
            setModuleStepState('WAVE', 'SKU');
        }
    } catch (e) { avisarError(e); }
}

// =========================================================================================
// === 2. RECEPCIONES GUIADAS POR CÁMARA (REMITOS DE ENTRADA) ==============================
// =========================================================================================

async function loadReceptions() {
    const container = document.getElementById('receptions-list');
    if (!container) return;
    container.innerHTML = '<p class="vacio">Cargando remitos...</p>';
    try {
        const remitos = await fetchAPI('/api/reception/remitos');
        if (!remitos || remitos.length === 0) {
            container.innerHTML = '<p class="vacio">No hay ingresos pendientes.</p>';
            return;
        }
        container.innerHTML = remitos.map(r => tarjetaLista(`data-on-click="startReceptionScan(${jsArg(r.id)}, ${jsArg(r.remito_number)})"`, r.remito_number,
            `<span class="badge ${r.status === 'PENDING' ? 'badge-warning' : 'badge-info'}">${escapeHTML(estadoColectora('remito', r.status))}</span>`,
            [`Proveedor: ${r.supplier_name}`, `${r.branch_name} · ${r.sector_name}`])).join('');
    } catch (e) { container.innerHTML = `<p class="vacio err">Error: ${escapeHTML(e.message)}</p>`; }
}

async function startReceptionScan(remitoId, remitoNumber) {
    openView('view-reception-scan');
    setCabecera('Remito · control ciego', remitoNumber || remitoId, true);
    document.getElementById('rec-number').value = remitoId;

    await refreshReceptionOrderSheet(remitoId);
    startModuleCameraStream('reception-video-stream', 'picking-camera-container');
    setModuleStepState('RECEPTION', 'SKU');
}

async function refreshReceptionOrderSheet(remitoId) {
    try {
        activeDocumentData = await fetchAPI(`/api/reception/remitos/${encodeURIComponent(remitoId)}`);
        pintarHoja('RECEPTION');
    } catch (e) { console.error(e); }
}

async function handleReceptionFormSubmit(event) {
    event.preventDefault();
    const ref = document.getElementById('rec-number').value;
    const sku = document.getElementById('rec-sku').value.trim().toUpperCase();
    const qty = parseFloat(document.getElementById('rec-qty').value);
    const locRaw = document.getElementById('rec-loc').value.trim().toUpperCase();
    const loc = locRaw && locRaw !== 'GENERAL' ? locRaw : null;

    if (!sku || isNaN(qty) || qty <= 0) return showToast("Revise SKU y cantidad", "error");

    const enRemito = ((activeDocumentData && activeDocumentData.lines) || []).some(l => l.sku === sku);
    try {
        if (!enRemito) {
            if (!confirm(`El artículo ${sku} no figura en este remito.\n\n¿Registrarlo como no esperado? Queda en cuarentena hasta que un responsable lo apruebe o lo rechace.`)) return;
            const r = await fetchAPI(`/api/reception/remitos/${encodeURIComponent(ref)}/unexpected`, {
                method: 'POST', body: { sku: sku, quantity: qty, location_code: loc }
            });
            showToast(r.message, "warning");
        } else {
            const res = await fetchAPI(`/api/reception/remitos/${encodeURIComponent(ref)}/scan`, {
                method: 'POST', body: { remito_number: ref, sku: sku, quantity: qty, location_code: loc }
            });
            showToast(res.warning || res.message, res.warning ? "warning" : "success");
        }
        await refreshReceptionOrderSheet(ref);
        setModuleStepState('RECEPTION', 'SKU');
    } catch (e) { /* fetchAPI ya mostro el error */ }
}

async function finishReceptionControl() {
    const ref = document.getElementById('rec-number').value;
    if (!ref) return;
    if (!confirm("¿Finalizar el control de este remito?\n\nDespués no se puede escanear más.")) return;
    try {
        const r = await fetchAPI(`/api/reception/remitos/${encodeURIComponent(ref)}/finish`, { method: 'POST' });
        if (r.status === 'COMPLETED') {
            showToast("Control finalizado: sin diferencias con el remito.", "success");
        } else {
            const partes = [];
            if (r.faltantes.length) partes.push("Faltan: " + r.faltantes.map(x => `${x.sku} ${x.quantity}`).join(", "));
            if (r.sobrantes.length) partes.push("Sobran: " + r.sobrantes.map(x => `${x.sku} ${x.quantity}`).join(", "));
            if (r.cuarentena) partes.push(`En cuarentena: ${r.cuarentena}`);
            showToast("Control finalizado con diferencias. " + partes.join(". "), "warning");
        }
        setTimeout(() => openView('view-receptions', loadReceptions), 1500);
    } catch (e) { /* fetchAPI ya mostro el error */ }
}

// =========================================================================================
// === 3. TRASPASOS GUIADOS POR CÁMARA (ODTs) ==============================================
// =========================================================================================

async function loadTransfers() {
    const container = document.getElementById('transfers-list');
    if (!container) return;
    container.innerHTML = '<p class="vacio">Cargando traspasos...</p>';
    try {
        const transfers = await fetchAPI('/api/transfers/orders');
        if (!transfers || transfers.length === 0) {
            container.innerHTML = '<p class="vacio">No hay traspasos activos.</p>';
            return;
        }
        container.innerHTML = transfers.map(t => tarjetaLista(`data-on-click="startTransferScan(${jsArg(t.transfer_number)})"`, t.transfer_number,
            `<span class="badge badge-warning">${escapeHTML(estadoColectora('traspaso', t.status))}</span>`,
            [`Origen: ${t.origin_branch} > Destino: ${t.destination_branch}`])).join('');
    } catch (e) { container.innerHTML = `<p class="vacio err">Error: ${escapeHTML(e.message)}</p>`; }
}

async function startTransferScan(transferNumber) {
    openView('view-transfer-scan');
    setCabecera('Traspaso', transferNumber, true);
    document.getElementById('tr-number').value = transferNumber;

    await refreshTransferOrderSheet(transferNumber);
    startModuleCameraStream('transfer-video-stream', 'picking-camera-container');
    setModuleStepState('TRANSFER', 'SKU');
}

async function refreshTransferOrderSheet(transferNumber) {
    try {
        activeDocumentData = await fetchAPI(`/api/transfers/orders/${encodeURIComponent(transferNumber)}`);
        pintarHoja('TRANSFER');
    } catch (e) { console.error(e); }
}

async function handleTransferFormSubmit(event) {
    event.preventDefault();
    const num = document.getElementById('tr-number').value;
    const sku = document.getElementById('tr-sku').value.trim().toUpperCase();
    const qty = parseFloat(document.getElementById('tr-qty').value);
    const destLoc = document.getElementById('tr-dest-loc').value.trim().toUpperCase() || 'GENERAL';

    if (!sku || isNaN(qty) || qty <= 0) return showToast("Revise SKU y cantidad", "error");

    try {
        const res = await fetchAPI(`/api/transfers/orders/${encodeURIComponent(num)}/scan`, {
            method: 'POST', body: { transfer_number: num, sku: sku, quantity: qty, destination_location_code: destLoc }
        });
        showToast(res.message, "success");
        if (res.transfer_completed) {
            showToast("Traspaso completado", "success");
            setTimeout(() => openView('view-transfers', loadTransfers), 1000);
        } else {
            await refreshTransferOrderSheet(num);
            setModuleStepState('TRANSFER', 'SKU');
        }
    } catch (e) { avisarError(e); }
}

// =========================================================================================
// === 4. INVENTARIO (CONTEOS CÍCLICOS) ====================================================
// =========================================================================================

async function loadInventory() {
    const container = document.getElementById('inventory-list');
    if (!container) return;
    container.innerHTML = '<p class="vacio">Cargando sesiones...</p>';
    try {
        const sessions = await fetchAPI('/api/inventory/sessions');
        const openSessions = sessions.filter(s => s.status === 'OPEN');
        if (openSessions.length === 0) {
            container.innerHTML = '<p class="vacio">No hay conteos asignados.</p>';
            return;
        }
        container.innerHTML = openSessions.map(s => tarjetaLista(`data-on-click="startInventoryScan(${jsArg(s.id)}, ${jsArg(s.sector_name)})"`, `Sector: ${s.sector_name}`,
            `<span class="badge badge-info">${escapeHTML(estadoColectora('conteo', s.count_type))}</span>`,
            [`Sucursal: ${s.branch_name}`],
            { tags: s.assigned_operator && s.assigned_operator === window.usuarioActual ? '<span class="badge badge-info">ASIGNADO A VOS</span>' : '' })).join('');
    } catch (e) { container.innerHTML = `<p class="vacio err">Error: ${escapeHTML(e.message)}</p>`; }
}

// Lecturas hechas desde que se abrio esta pantalla (la API no devuelve las anteriores de la sesion).
let lecturasConteo = [];
function pintarLecturasConteo() {
    document.getElementById('inv-lecturas-badge').textContent = lecturasConteo.length;
    document.getElementById('inv-lecturas').innerHTML = lecturasConteo.length
        ? lecturasConteo.map(l => filaHoja(l.sku, '', l.location_code || 'Sin ubicación', String(l.quantity), true, false)).join('')
        : '<p class="vacio">Todavía no registraste lecturas en esta pantalla.</p>';
}

function startInventoryScan(sessionId, sectorName) {
    openView('view-inventory-scan');
    setCabecera(`Conteo · ID ${sessionId.split('-')[0].toUpperCase()}`, sectorName || 'Escaneo de Sector');
    document.getElementById('inv-session-id').value = sessionId;
    lecturasConteo = [];
    pintarLecturasConteo();
    document.getElementById('inv-sku').focus();
}

async function scanInventoryCount(event) {
    event.preventDefault();
    const sessId = document.getElementById('inv-session-id').value;
    const payload = {
        sku: document.getElementById('inv-sku').value.trim(),
        quantity: parseFloat(document.getElementById('inv-qty').value),
        location_code: document.getElementById('inv-loc').value.trim(),
        lot_number: document.getElementById('inv-lot').value.trim()
    };
    try {
        const res = await fetchAPI(`/api/inventory/sessions/${sessId}/scan`, { method: 'POST', body: payload });
        showToast(res.message, "success");
        lecturasConteo.unshift(payload);
        pintarLecturasConteo();
        document.getElementById('inv-sku').value = '';
        document.getElementById('inv-qty').value = '';
        document.getElementById('inv-sku').focus();
    } catch (e) { avisarError(e); }
}

async function finishInventorySession() {
    if (!confirm("¿Seguro que terminó de contar todo el sector?")) return;
    const sessId = document.getElementById('inv-session-id').value;
    try {
        await fetchAPI(`/api/inventory/sessions/${sessId}/finish`, { method: 'POST' });
        showToast("Conteo finalizado y enviado a revisión.", "success");
        openView('view-inventory', loadInventory);
    } catch (e) { avisarError(e); }
}

// =========================================================================================
// === 5. SPOT CHECK (AUDITORÍA RÁPIDA) ====================================================
// =========================================================================================

function openSpotCheck() {
    openView('view-spot-check');
    document.getElementById('form-spot-check').reset();
    document.getElementById('spot-check-result').style.display = 'none';
}

async function runSpotCheck(event) {
    event.preventDefault();
    const payload = {
        sku: document.getElementById('spot-check-sku').value.trim(),
        quantity: parseFloat(document.getElementById('spot-check-qty').value),
        location_code: document.getElementById('spot-check-loc').value.trim(),
        lot_number: document.getElementById('spot-check-lot').value.trim()
    };
    try {
        const res = await fetchAPI('/api/inventory/spot-check', { method: 'POST', body: payload });
        const resDiv = document.getElementById('spot-check-result');
        if (res.match) playSuccessChime(); else playErrorTone();
        const delta = res.match ? 0 : (res.delta > 0 ? '+' + res.delta : res.delta);
        const stat = (k, v) => `<div><span>${k}</span><b>${escapeHTML(v)}</b></div>`;
        resDiv.className = 'spot-res ' + (res.match ? 'ok' : 'dif');
        resDiv.innerHTML = `<div class="spot-h">${res.match ? 'STOCK COINCIDE' : 'ATENCION: DIFERENCIA DETECTADA'}</div>
            <div class="spot-st">${stat('Sistema', res.expected)}${stat('Físico', payload.quantity)}${stat('Diferencia', delta)}</div>`;
        resDiv.style.display = 'block';
        document.getElementById('spot-check-sku').select();
    } catch (e) { avisarError(e); }
}

// Exponer funciones globales al objeto Window
window.escapeHTML = escapeHTML;
window.handleScannerEnter = handleScannerEnter;
window.startCameraScanner = startCameraScanner;
window.stopCameraScanner = stopCameraScanner;
window.openView = openView;
window.goHome = goHome;
window.volver = volver;
window.ajustarCantidad = ajustarCantidad;
window.logout = logout;
window.onModuleInputProcess = onModuleInputProcess;
window.scanEnter = scanEnter;

window.loadPicking = loadPicking;
window.startOrderPicking = startOrderPicking;
window.handlePickingFormSubmit = handlePickingFormSubmit;

window.startWavePicking = startWavePicking;
window.handleWaveFormSubmit = handleWaveFormSubmit;

window.loadReceptions = loadReceptions;
window.startReceptionScan = startReceptionScan;
window.finishReceptionControl = finishReceptionControl;
window.handleReceptionFormSubmit = handleReceptionFormSubmit;

window.loadTransfers = loadTransfers;
window.startTransferScan = startTransferScan;
window.handleTransferFormSubmit = handleTransferFormSubmit;

window.loadInventory = loadInventory;
window.startInventoryScan = startInventoryScan;
window.scanInventoryCount = scanInventoryCount;
window.finishInventorySession = finishInventorySession;

window.openSpotCheck = openSpotCheck;
window.runSpotCheck = runSpotCheck;