// === CONTROLADOR NATIVO COLECTORA MÓVIL (TRACKER360) ===
// 100% SOBERANO: ZERO DEPENDENCIAS EXTERNAS NI CDN

let activeModule = null; // 'PICKING' | 'RECEPTION' | 'TRANSFER'
let moduleState = 'SKU'; // 'SKU' | 'LOCATION' | 'QTY'

let activeDocumentData = null;
let activeCameraStream = null;
let cameraDetectorInterval = null;
let genericCameraStream = null;
let targetInputIdForCamera = null;

// =========================================================================================
// === UTILIDADES NATIVAS ==================================================================
// =========================================================================================

function escapeHTML(str) {
    if (str === null || str === undefined) return '';
    return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
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

function logout() {
    document.cookie = "access_token=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/;";
    window.location.href = "/";
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
    toast.className = `toast toast-${type}`;
    toast.textContent = msg;
    container.appendChild(toast);

    if (type === 'success') playSuccessChime();
    else if (type === 'error') playErrorTone();

    setTimeout(() => toast.remove(), 3000);
}

function openView(viewId, callback) {
    document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
    const target = document.getElementById(viewId);
    if (target) target.classList.add('active');

    const btnBack = document.getElementById('btn-back');
    if (btnBack) btnBack.style.display = (viewId === 'view-home') ? 'none' : 'inline-block';

    stopPickingCameraStream();

    if (typeof callback === 'function') callback();
}

function goHome() {
    openView('view-home');
}

// =========================================================================================
// === CÁMARA CONTINUA EN VIVO NATIVA HTML5 ================================================
// =========================================================================================

async function startModuleCameraStream(videoElementId, containerClass) {
    const video = document.getElementById(videoElementId);
    const container = document.querySelector(`.${containerClass}`);
    if (!video || !container) return;

    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        console.warn("Cámara bloqueada por falta de HTTPS o permisos.");
        video.style.display = 'none';
        container.style.display = 'flex';
        container.style.alignItems = 'center';
        container.style.justifyContent = 'center';
        container.style.padding = '20px';
        container.style.textAlign = 'center';
        container.innerHTML = `
            <div>
                <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="#DC2626" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="margin-bottom:10px;"><path d="M2 2l20 20M15 15l5.2-3.18A2 2 0 0 0 21 10.1V5.9a2 2 0 0 0-1.09-1.73l-7-3.93a2 2 0 0 0-1.82 0l-7 3.93A2 2 0 0 0 3 5.9v4.2c0 .4.14.79.4 1.1"/></svg>
                <p style="color:#FCA5A5; font-size:0.9rem; font-weight:bold; margin-bottom:5px;">CÁMARA RESTRINGIDA</p>
                <p style="color:#9CA3AF; font-size:0.8rem;">El navegador bloqueó la cámara. Use escáner láser o teclado.</p>
            </div>
        `;
        return;
    }

    try {
        if (activeCameraStream) stopPickingCameraStream();
        const constraints = { video: { facingMode: { ideal: "environment" }, width: { ideal: 640 }, height: { ideal: 480 } } };
        activeCameraStream = await navigator.mediaDevices.getUserMedia(constraints);
        video.srcObject = activeCameraStream;
        video.style.display = 'block';
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

function setModuleStepState(moduleName, newState) {
    activeModule = moduleName;
    moduleState = newState;

    const prefix = moduleName === 'PICKING' ? 'pick' : (moduleName === 'RECEPTION' ? 'rec' : 'tr');
    const banner = document.getElementById(moduleName === 'PICKING' ? 'picking-step-banner' : (moduleName === 'RECEPTION' ? 'reception-step-banner' : 'transfer-step-banner'));
    const grpSku = document.getElementById(`grp-${prefix}-sku`);
    const grpLoc = document.getElementById(`grp-${prefix}-loc`);
    const grpQty = document.getElementById(`grp-${prefix}-qty`);

    const skuInp = document.getElementById(`${prefix}-sku`);
    const locInp = document.getElementById(prefix === 'tr' ? 'tr-dest-loc' : `${prefix}-loc`);
    const qtyInp = document.getElementById(`${prefix}-qty`);

    if (newState === 'SKU') {
        if (banner) { banner.className = 'step-banner step-banner-sku'; banner.textContent = 'PASO 1: ESCANEE CÓDIGO/QR DEL ARTÍCULO'; }
        if (grpSku) grpSku.style.display = 'block';
        if (grpLoc) grpLoc.style.display = 'none';
        if (grpQty) grpQty.style.display = 'none';
        if (skuInp) { skuInp.value = ''; skuInp.focus(); }
    } else if (newState === 'LOCATION') {
        if (banner) { banner.className = 'step-banner step-banner-loc'; banner.textContent = `PASO 2: ESCANEE UBICACIÓN PARA ${skuInp ? skuInp.value.toUpperCase() : ''}`; }
        if (grpSku) grpSku.style.display = 'block';
        if (grpLoc) grpLoc.style.display = 'block';
        if (grpQty) grpQty.style.display = 'none';
        if (locInp) { locInp.value = ''; locInp.focus(); }
    } else if (newState === 'QTY') {
        if (banner) { banner.className = 'step-banner step-banner-qty'; banner.textContent = 'PASO 3: CONFIRME CANTIDAD A PROCESAR'; }
        if (grpSku) grpSku.style.display = 'block';
        if (grpLoc) grpLoc.style.display = 'block';
        if (grpQty) grpQty.style.display = 'block';
        if (qtyInp) { qtyInp.focus(); qtyInp.select(); }
    }
}

function onModuleInputProcess(moduleName, inputType) {
    if (!activeDocumentData) return;
    const lines = activeDocumentData.lines || [];

    const prefix = moduleName === 'PICKING' ? 'pick' : (moduleName === 'RECEPTION' ? 'rec' : 'tr');
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
        } else if (moduleName === 'RECEPTION') {
            lineMatch = lines.find(l => l.sku.toUpperCase() === scannedSku && l.quantity_received < l.quantity_sent);
            if (lineMatch) qtyInp.value = lineMatch.quantity_sent - lineMatch.quantity_received;
        } else if (moduleName === 'TRANSFER') {
            lineMatch = lines.find(l => l.sku.toUpperCase() === scannedSku && l.quantity_received < l.quantity_sent);
            if (lineMatch) qtyInp.value = lineMatch.quantity_sent - lineMatch.quantity_received;
        }

        if (lineMatch) {
            playSuccessChime();
            setModuleStepState(moduleName, 'LOCATION');
        } else {
            playErrorTone();
            showToast(`El SKU '${scannedSku}' no pertenece al comprobante o ya está completo.`, 'error');
            if (skuInp) skuInp.value = '';
        }
    } else if (inputType === 'loc') {
        playSuccessChime();
        setModuleStepState(moduleName, 'QTY');
    }
}

// =========================================================================================
// === 1. PICKING MÓVIL GUIADO =============================================================
// =========================================================================================

async function loadPicking() {
    const container = document.getElementById('picking-list');
    if (!container) return;
    container.innerHTML = '<p style="text-align:center; padding:1.5rem; color:var(--text-muted);">Cargando pedidos pendientes...</p>';

    try {
        const orders = await fetchAPI('/api/picking/orders');
        if (!orders || orders.length === 0) {
            container.innerHTML = '<p style="text-align:center; padding:1.5rem; color:var(--text-muted);">No hay pedidos pendientes de armado.</p>';
            return;
        }
        container.innerHTML = orders.map(o => `
            <div class="list-item" onclick="startOrderPicking('${escapeHTML(o.document_number)}')">
                <div style="display:flex; justify-content:space-between; align-items:center;">
                    <strong>${escapeHTML(o.document_number)}</strong>
                    <span class="badge ${o.status === 'PENDING' ? 'badge-warning' : 'badge-info'}">${o.status}</span>
                </div>
                <p>Cliente: ${escapeHTML(o.company_name)}</p>
                <p><small style="color:var(--accent-blue); font-weight:bold;">A recolectar: ${o.requested_items || 0} unidades (${o.total_items || 0} ítems)</small></p>
            </div>
        `).join('');
    } catch (e) {
        container.innerHTML = `<p style="text-align:center; color:var(--danger); padding:1rem;">Error de conexión: ${escapeHTML(e.message)}</p>`;
    }
}

async function startOrderPicking(documentNumber) {
    openView('view-picking-scan');
    document.getElementById('pick-number').value = documentNumber;
    document.getElementById('pick-title').textContent = `Pedido #${documentNumber}`;
    
    await refreshPickingOrderSheet(documentNumber);
    startModuleCameraStream('picking-video-stream', 'picking-camera-container');
    setModuleStepState('PICKING', 'SKU');
}

async function refreshPickingOrderSheet(documentNumber) {
    try {
        const data = await fetchAPI(`/api/picking/orders/${encodeURIComponent(documentNumber)}`);
        activeDocumentData = data;

        const lines = data.lines || [];
        const pendingLines = lines.filter(l => l.quantity_picked < l.quantity_requested);

        document.getElementById('pick-items-count-badge').textContent = `${lines.length - pendingLines.length}/${lines.length} listos`;

        const sheetBody = document.getElementById('pick-items-sheet-body');
        if (sheetBody) {
            if (pendingLines.length === 0) {
                sheetBody.innerHTML = '<p style="text-align:center; color:var(--success); font-weight:bold; padding:1rem;">¡Todos los artículos recolectados!</p>';
            } else {
                sheetBody.innerHTML = pendingLines.map(l => {
                    const remaining = l.quantity_requested - l.quantity_picked;
                    return `
                        <div style="padding:8px 0; border-bottom:1px solid var(--border-color); font-size:0.85rem;">
                            <div style="display:flex; justify-content:space-between; align-items:center;">
                                <strong style="color:var(--primary-blue); font-family:monospace;">${escapeHTML(l.sku)}</strong>
                                <span class="badge badge-warning">Faltan: ${remaining} un</span>
                            </div>
                            <div style="color:var(--text-main); font-weight:600; margin-top:2px;">${escapeHTML(l.description)}</div>
                            <div style="color:var(--text-muted); font-size:0.75rem; margin-top:2px;">Sugerido: ${escapeHTML(l.suggested_locations)}</div>
                        </div>
                    `;
                }).join('');
            }
        }
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
    } catch (e) { showToast(e.message, "error"); }
}

// =========================================================================================
// === 2. RECEPCIONES GUIADAS POR CÁMARA (REMITOS DE ENTRADA) ==============================
// =========================================================================================

async function loadReceptions() {
    const container = document.getElementById('receptions-list');
    if (!container) return;
    container.innerHTML = '<p style="text-align:center; padding:1rem;">Cargando remitos...</p>';
    try {
        const remitos = await fetchAPI('/api/reception/remitos');
        if (!remitos || remitos.length === 0) {
            container.innerHTML = '<p style="text-align:center; padding:1rem; color:var(--text-muted);">No hay ingresos pendientes.</p>';
            return;
        }
        container.innerHTML = remitos.map(r => `
            <div class="list-item" onclick="startReceptionScan('${escapeHTML(r.remito_number)}')">
                <strong>${escapeHTML(r.remito_number)}</strong>
                <p>Proveedor: ${escapeHTML(r.supplier_name)}</p>
                <span class="badge ${r.status === 'PENDING' ? 'badge-warning' : 'badge-info'}">${r.status}</span>
            </div>
        `).join('');
    } catch (e) { container.innerHTML = `<p style="text-align:center; color:var(--danger);">Error: ${escapeHTML(e.message)}</p>`; }
}

async function startReceptionScan(remitoNumber) {
    openView('view-reception-scan');
    document.getElementById('rec-number').value = remitoNumber;
    document.getElementById('rec-title').textContent = `Remito #${remitoNumber}`;
    
    await refreshReceptionOrderSheet(remitoNumber);
    startModuleCameraStream('reception-video-stream', 'picking-camera-container');
    setModuleStepState('RECEPTION', 'SKU');
}

async function refreshReceptionOrderSheet(remitoNumber) {
    try {
        const data = await fetchAPI(`/api/reception/remitos/${encodeURIComponent(remitoNumber)}`);
        activeDocumentData = data;

        const lines = data.lines || [];
        const pendingLines = lines.filter(l => l.quantity_received < l.quantity_sent);

        document.getElementById('rec-items-count-badge').textContent = `${lines.length - pendingLines.length}/${lines.length} listos`;

        const sheetBody = document.getElementById('rec-items-sheet-body');
        if (sheetBody) {
            if (pendingLines.length === 0) {
                sheetBody.innerHTML = '<p style="text-align:center; color:var(--success); font-weight:bold; padding:1rem;">¡Remito controlado e ingresado totalmente!</p>';
            } else {
                sheetBody.innerHTML = pendingLines.map(l => {
                    const remaining = l.quantity_sent - l.quantity_received;
                    return `
                        <div style="padding:8px 0; border-bottom:1px solid var(--border-color); font-size:0.85rem;">
                            <div style="display:flex; justify-content:space-between; align-items:center;">
                                <strong style="color:var(--primary-blue); font-family:monospace;">${escapeHTML(l.sku)}</strong>
                                <span class="badge badge-warning">Faltan: ${remaining} un</span>
                            </div>
                            <div style="color:var(--text-muted); font-size:0.75rem; margin-top:2px;">Sugerido: ${escapeHTML(l.location_code || 'General')}</div>
                        </div>
                    `;
                }).join('');
            }
        }
    } catch (e) { console.error(e); }
}

async function handleReceptionFormSubmit(event) {
    event.preventDefault();
    const num = document.getElementById('rec-number').value;
    const sku = document.getElementById('rec-sku').value.trim().toUpperCase();
    const qty = parseFloat(document.getElementById('rec-qty').value);
    const loc = document.getElementById('rec-loc').value.trim().toUpperCase() || 'GENERAL';
    
    if (!sku || isNaN(qty) || qty <= 0) return showToast("Revise SKU y cantidad", "error");

    try {
        const res = await fetchAPI(`/api/reception/remitos/${encodeURIComponent(num)}/scan`, {
            method: 'POST', body: { remito_number: num, sku: sku, quantity: qty, location_code: loc }
        });
        showToast(res.message, "success");
        if (res.remito_completed) {
            showToast("Remito completado e ingresado", "success");
            setTimeout(() => openView('view-receptions', loadReceptions), 1000);
        } else {
            await refreshReceptionOrderSheet(num);
            setModuleStepState('RECEPTION', 'SKU');
        }
    } catch (e) { showToast(e.message, "error"); }
}

// =========================================================================================
// === 3. TRASPASOS GUIADOS POR CÁMARA (ODTs) ==============================================
// =========================================================================================

async function loadTransfers() {
    const container = document.getElementById('transfers-list');
    if (!container) return;
    container.innerHTML = '<p style="text-align:center; padding:1rem;">Cargando traspasos...</p>';
    try {
        const transfers = await fetchAPI('/api/transfers/orders');
        if (!transfers || transfers.length === 0) {
            container.innerHTML = '<p style="text-align:center; padding:1rem; color:var(--text-muted);">No hay traspasos activos.</p>';
            return;
        }
        container.innerHTML = transfers.map(t => `
            <div class="list-item" onclick="startTransferScan('${escapeHTML(t.transfer_number)}')">
                <strong>${escapeHTML(t.transfer_number)}</strong>
                <p>Origen: ${escapeHTML(t.origin_branch)} > Destino: ${escapeHTML(t.destination_branch)}</p>
                <span class="badge badge-warning">${t.status}</span>
            </div>
        `).join('');
    } catch (e) { container.innerHTML = `<p style="text-align:center; color:var(--danger);">Error: ${escapeHTML(e.message)}</p>`; }
}

async function startTransferScan(transferNumber) {
    openView('view-transfer-scan');
    document.getElementById('tr-number').value = transferNumber;
    document.getElementById('tr-title').textContent = `Traspaso #${transferNumber}`;
    
    await refreshTransferOrderSheet(transferNumber);
    startModuleCameraStream('transfer-video-stream', 'picking-camera-container');
    setModuleStepState('TRANSFER', 'SKU');
}

async function refreshTransferOrderSheet(transferNumber) {
    try {
        const data = await fetchAPI(`/api/transfers/orders/${encodeURIComponent(transferNumber)}`);
        activeDocumentData = data;

        const lines = data.lines || [];
        const pendingLines = lines.filter(l => l.quantity_received < l.quantity_sent);

        document.getElementById('tr-items-count-badge').textContent = `${lines.length - pendingLines.length}/${lines.length} listos`;

        const sheetBody = document.getElementById('tr-items-sheet-body');
        if (sheetBody) {
            if (pendingLines.length === 0) {
                sheetBody.innerHTML = '<p style="text-align:center; color:var(--success); font-weight:bold; padding:1rem;">¡Traspaso completado totalmente!</p>';
            } else {
                sheetBody.innerHTML = pendingLines.map(l => {
                    const remaining = l.quantity_sent - l.quantity_received;
                    return `
                        <div style="padding:8px 0; border-bottom:1px solid var(--border-color); font-size:0.85rem;">
                            <div style="display:flex; justify-content:space-between; align-items:center;">
                                <strong style="color:var(--primary-blue); font-family:monospace;">${escapeHTML(l.sku)}</strong>
                                <span class="badge badge-warning">Faltan: ${remaining} un</span>
                            </div>
                            <div style="color:var(--text-muted); font-size:0.75rem; margin-top:2px;">Origen: ${escapeHTML(l.origin_location || 'Gral')} > Destino: ${escapeHTML(l.destination_location || 'Gral')}</div>
                        </div>
                    `;
                }).join('');
            }
        }
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
    } catch (e) { showToast(e.message, "error"); }
}

// =========================================================================================
// === 4. INVENTARIO (CONTEOS CÍCLICOS) ====================================================
// =========================================================================================

async function loadInventory() {
    const container = document.getElementById('inventory-list');
    if (!container) return;
    container.innerHTML = '<p style="text-align:center; padding:1rem;">Cargando sesiones...</p>';
    try {
        const sessions = await fetchAPI('/api/inventory/sessions');
        const openSessions = sessions.filter(s => s.status === 'OPEN');
        if (openSessions.length === 0) {
            container.innerHTML = '<p style="text-align:center; padding:1rem; color:var(--text-muted);">No hay conteos asignados.</p>';
            return;
        }
        container.innerHTML = openSessions.map(s => `
            <div class="list-item" onclick="startInventoryScan('${s.id}')">
                <strong>Sector: ${escapeHTML(s.sector_name)}</strong>
                <p>Sucursal: ${escapeHTML(s.branch_name)}</p>
                <span class="badge badge-info">${s.count_type}</span>
            </div>
        `).join('');
    } catch (e) { container.innerHTML = `<p style="text-align:center; color:var(--danger);">Error: ${escapeHTML(e.message)}</p>`; }
}

function startInventoryScan(sessionId) {
    openView('view-inventory-scan');
    document.getElementById('inv-session-id').value = sessionId;
    document.getElementById('inv-session-id-label').textContent = sessionId.split('-')[0].toUpperCase();
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
        document.getElementById('inv-sku').value = '';
        document.getElementById('inv-qty').value = '';
        document.getElementById('inv-sku').focus();
    } catch (e) { showToast(e.message, "error"); }
}

async function finishInventorySession() {
    if (!confirm("¿Seguro que terminó de contar todo el sector?")) return;
    const sessId = document.getElementById('inv-session-id').value;
    try {
        await fetchAPI(`/api/inventory/sessions/${sessId}/finish`, { method: 'POST' });
        showToast("Conteo finalizado y enviado a revisión.", "success");
        openView('view-inventory', loadInventory);
    } catch (e) { showToast(e.message, "error"); }
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
        resDiv.style.display = 'block';
        
        if (res.match) {
            playSuccessChime();
            resDiv.style.backgroundColor = '#ECFDF5';
            resDiv.style.border = '1px solid #10B981';
            resDiv.innerHTML = `<h3 style="color:#065F46; margin-bottom:5px;">STOCK COINCIDE</h3>
                                <p style="color:#047857; margin:0;">El stock esperado y el contado son idénticos (${res.expected} un).</p>`;
        } else {
            playErrorTone();
            resDiv.style.backgroundColor = '#FEF2F2';
            resDiv.style.border = '1px solid #EF4444';
            resDiv.innerHTML = `<h3 style="color:#B91C1C; margin-bottom:5px;">ATENCION: DIFERENCIA DETECTADA</h3>
                                <p style="color:#991B1B; margin:0;">Esperado: <strong>${res.expected}</strong> | Faltante/Sobrante: <strong>${res.delta > 0 ? '+'+res.delta : res.delta}</strong></p>`;
        }
        document.getElementById('spot-check-sku').select();
    } catch (e) { showToast(e.message, "error"); }
}

// Exponer funciones globales al objeto Window
window.escapeHTML = escapeHTML;
window.handleScannerEnter = handleScannerEnter;
window.startCameraScanner = startCameraScanner;
window.stopCameraScanner = stopCameraScanner;
window.openView = openView;
window.goHome = goHome;
window.logout = logout;
window.onModuleInputProcess = onModuleInputProcess;

window.loadPicking = loadPicking;
window.startOrderPicking = startOrderPicking;
window.handlePickingFormSubmit = handlePickingFormSubmit;

window.loadReceptions = loadReceptions;
window.startReceptionScan = startReceptionScan;
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