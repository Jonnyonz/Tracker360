// === MÓDULO ARTÍCULOS (TRACKER360) ===
let itemsCurrentPage = 1;
let itemsCache = {};

function escapeHTML(str) {
    if (str === null || str === undefined) return '';
    return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

// No se lista nada hasta buscar. La busqueda se repite al paginar, ordenar, guardar o importar.
const ARTICULOS_POR_PAGINA = 25;
let busquedaArticulos = null;
let itemsTotalPages = 1;
let itemsOrden = { col: 'sku', dir: 'ASC' };

function buscarArticulos(event) {
    if (event) event.preventDefault();
    const valor = id => (document.getElementById(id)?.value || '').trim();
    busquedaArticulos = {
        sku: valor('search-sku'), description: valor('search-desc'), category: valor('f-art-categoria'),
        location: valor('f-art-ubicacion'), combo: valor('f-art-combo'), stock: valor('f-art-stock'),
    };
    handleSearchItems(1);
}

function limpiarBusquedaArticulos() {
    document.getElementById('form-buscar-articulos')?.reset();
    busquedaArticulos = null;
    itemsOrden = { col: 'sku', dir: 'ASC' };
    handleSearchItems(1);
}

function prevItemPage() { if (busquedaArticulos && itemsCurrentPage > 1) handleSearchItems(itemsCurrentPage - 1); }
function nextItemPage() { if (busquedaArticulos && itemsCurrentPage < itemsTotalPages) handleSearchItems(itemsCurrentPage + 1); }

function resumenArticulos(texto) {
    const p = document.getElementById('articulos-resumen');
    if (!p) return;
    p.textContent = texto;
    p.hidden = !texto;
}

// === LISTA + FICHA (design_handoff_tracker360, fase 4) ===
// La busqueda de siempre (mismos filtros, por paginas) pinta la lista de la izquierda; al elegir un articulo se arma
// su ficha a la derecha con /stock-breakdown, /combo y los movimientos de los ultimos 30 dias de /api/admin/stock/kardex.
// La API no informa EAN, lote, condicion ni reservado: la ficha no los muestra.
const ART_TABS = [['Todos', '', ''], ['Simples', 'NO', ''], ['Combos', 'SI', ''], ['Sin stock', '', 'SIN']];   // etiqueta, filtro Tipo, filtro Stock
let artFilas = [], artSel = null;

function ordenarArticulos(valor) {
    const [col, dir] = valor.split(':');
    itemsOrden = { col, dir };
    if (busquedaArticulos) handleSearchItems(1);
}
// Las pestañas son los filtros Tipo y Stock del formulario: los fijan y buscan.
function artTab(i) {
    document.getElementById('f-art-combo').value = ART_TABS[i][1];
    document.getElementById('f-art-stock').value = ART_TABS[i][2];
    buscarArticulos();
}
function artElegir(sku) { artSel = sku; pintarArticulos(); }

async function handleSearchItems(page = 1) {
    itemsCurrentPage = page;
    const lista = document.getElementById('art-lista');
    if (!lista) return;
    const info = document.getElementById('items-page-info');
    if (!busquedaArticulos) {
        artFilas = [];
        if (info) info.textContent = '';
        resumenArticulos('');
        pintarArticulos('Buscá por SKU, descripción, categoría, ubicación, tipo o stock.');
        return;
    }
    lista.innerHTML = '<div class="vacio">Buscando…</div>';
    const params = new URLSearchParams({ page, limit: ARTICULOS_POR_PAGINA, sort_by: itemsOrden.col, sort_order: itemsOrden.dir });
    Object.entries(busquedaArticulos).forEach(([k, v]) => { if (v) params.set(k, v); });
    try {
        const data = await fetchAPI(`/api/admin/items?${params.toString()}`);
        artFilas = (data && data.items) || [];
        itemsTotalPages = (data && data.total_pages) || 1;
        if (info) info.textContent = `Página ${(data && data.page) || page} de ${itemsTotalPages}`;
        const total = (data && data.total_count) || 0;
        resumenArticulos(`${total} artículo${total === 1 ? '' : 's'}.`);
        itemsCache = {};
        artFilas.forEach(a => { itemsCache[a.sku] = a; });
        pintarArticulos();
    } catch (err) {
        lista.innerHTML = `<div class="vacio" style="color:var(--dg);">Error al cargar artículos: ${escapeHTML(err.message)}</div>`;
    }
}

function pintarArticulos(vacioTexto) {
    const b = busquedaArticulos;
    document.getElementById('art-tabs').innerHTML = ART_TABS.map((t, i) =>
        `<button type="button" class="${b && b.combo === t[1] && b.stock === t[2] ? 'on' : ''}" data-on-click="artTab(${i})">${t[0]}</button>`).join('');
    if (!artFilas.some(a => a.sku === artSel)) artSel = artFilas.length ? artFilas[0].sku : null;
    document.getElementById('art-lista').innerHTML = artFilas.length ? artFilas.map(a => {
        const n = Number(a.total_stock || 0);
        return `<button type="button" class="art-row ${a.sku === artSel ? 'on' : ''}" data-on-click="artElegir(${jsArg(a.sku)})">
            <span class="min0"><b>${escapeHTML(a.sku)}</b><span class="d">${escapeHTML(a.description || '')}</span></span>
            <span class="s"><b style="color:${n < 0 ? 'var(--dgt)' : n === 0 ? 'var(--tm)' : 'var(--tp)'}">${n}</b><small>${a.is_combo ? 'Combo' : 'Simple'}</small></span>
        </button>`;
    }).join('') : `<div class="vacio">${escapeHTML(vacioTexto || 'No hay artículos que coincidan con la búsqueda.')}</div>`;
    pintarFichaArticulo(artFilas.find(a => a.sku === artSel));
}

async function pintarFichaArticulo(a) {
    const box = document.getElementById('art-det');
    if (!box) return;
    if (!a) { box.innerHTML = '<div class="vacio" style="margin:auto;">Elegí un artículo de la lista.</div>'; return; }
    const sku = a.sku, ruta = `/api/admin/items/${encodeURIComponent(sku)}`;
    // El supervisor solo consulta: editar e imprimir etiquetas son de administrador.
    const acciones = (window.ROL_ACTUAL === 'SUPERVISOR' ? '' :
        `<button type="button" class="btn-submit" data-on-click="openEditItemModal(${jsArg(sku)})">Editar</button>
         <button type="button" class="btn-secondary" data-on-click="imprimirEtiquetaArticulo(${jsArg(sku)})">Imprimir etiqueta</button>`)
        + `<button type="button" class="btn-secondary" data-on-click="verTrazaArticulo(${jsArg(sku)})">Ver traza</button>`;
    box.innerHTML = `<div class="det-h"><div class="art-cab"><span class="art-ico"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"></path><polyline points="3.27 6.96 12 12.01 20.73 6.96"></polyline><line x1="12" y1="22.08" x2="12" y2="12"></line></svg></span>
            <div class="min0"><div class="g"><span class="num mono">${escapeHTML(sku)}</span><span class="badge ${a.is_combo ? 'badge-info' : 'badge-neutral'}">${a.is_combo ? 'Combo' : 'Simple'}</span></div>
            <div class="art-desc">${escapeHTML(a.description || '')}</div><div class="sub">${escapeHTML(a.category || 'Sin categoría')}</div></div></div>
            <div class="art-acc">${acciones}</div></div>
        <div class="det-b" id="art-cuerpo"><div class="vacio">Cargando…</div></div>`;
    const hace30 = new Date(Date.now() - 30 * 86400000);
    const desde = new Date(hace30.getTime() - hace30.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
    const [stock, combo, kardex] = await Promise.all([
        fetchAPI(`${ruta}/stock-breakdown`).catch(() => null),
        a.is_combo ? fetchAPI(`${ruta}/combo`).catch(() => null) : null,
        fetchAPI(`/api/admin/stock/kardex?sku=${encodeURIComponent(sku)}&date_from=${desde}`).catch(() => null),
    ]);
    const cuerpo = document.getElementById('art-cuerpo');
    if (!cuerpo || artSel !== sku) return;   // el usuario ya eligio otro
    const total = Number(a.total_stock || 0), filas = stock || [];
    const asignadas = a.locations_summary && a.locations_summary !== 'Sin asignación' ? a.locations_summary.split(', ') : [];
    const kv = (k, v, mono) => `<div class="kv"><span>${escapeHTML(k)}</span><b${mono ? ' class="mono"' : ''}>${escapeHTML(String(v))}</b></div>`;
    let extra;
    if (a.is_combo) {
        const comp = (combo && combo.components) || [];
        extra = comp.length ? comp.map(c => `<div class="kv"><span><b class="mono">${escapeHTML(c.component_sku)}</b> ${escapeHTML(c.component_description || '')}</span><b class="mono">${escapeHTML(String(c.quantity))} ×</b></div>`).join('')
            : '<div class="muted" style="font-size:13px;">Este combo todavía no tiene componentes cargados.</div>';
    } else {
        extra = kv('Categoría', a.category || '—') + kv('Ubicaciones asignadas', asignadas.join(', ') || 'Sin asignación', asignadas.length > 0);
        if (typeof AppConfig !== 'undefined' && AppConfig.enable_item_dimensions === 'true') {
            extra += kv('Largo × ancho × alto (cm)', `${a.length || 0} × ${a.width || 0} × ${a.height || 0}`, true) + kv('Peso (kg)', a.weight || 0, true);
        }
    }
    // El kardex busca por texto (SKU o descripcion): se queda solo con los de este SKU.
    const movs = (kardex || []).filter(m => String(m.sku).toUpperCase() === String(sku).toUpperCase()).slice(0, 6);
    cuerpo.innerHTML = `<div class="datos stats">
            <div><div class="cap">Stock total</div><b style="color:${total < 0 ? 'var(--dgt)' : 'var(--tp)'}">${total}</b></div>
            <div><div class="cap">Ubicaciones con stock</div><b>${stock ? filas.length : '—'}</b></div>
            <div><div class="cap">Ubicaciones asignadas</div><b>${asignadas.length}</b></div>
        </div>
        <div><div class="cap" style="margin-bottom:8px;">Stock por ubicación</div>
            <div class="scrollx"><table class="tbl-lineas tbl-compras"><thead><tr><th>Sucursal</th><th>Sector</th><th>Ubicación</th><th style="text-align:right;">Cant.</th></tr></thead><tbody>${
                !stock ? '<tr><td colspan="4" class="busqueda-vacia" style="color:var(--dg);">No se pudo consultar el stock.</td></tr>'
                : filas.map(r => `<tr><td>${escapeHTML(r.branch_name || '—')}</td><td class="sec">${escapeHTML(r.sector_name || '—')}</td><td class="mono"><b>${escapeHTML(r.location_code)}</b></td><td class="mono" style="text-align:right; font-weight:600;">${escapeHTML(String(r.quantity))}</td></tr>`).join('')
                || '<tr><td colspan="4" class="busqueda-vacia">No hay existencias registradas para este artículo.</td></tr>'}</tbody></table></div></div>
        <div class="det-cols">
            <div><div class="cap" style="margin-bottom:6px;">${a.is_combo ? 'Componentes del combo' : 'Datos del artículo'}</div>${extra}</div>
            <div><div class="cap" style="margin-bottom:6px;">Últimos movimientos</div>${
                !kardex ? '<div class="muted" style="font-size:13px;">No se pudieron consultar los movimientos.</div>'
                : movs.map(m => `<div class="mov-row"><span>${escapeHTML(new Date(m.created_at).toLocaleDateString('es-AR', { day: '2-digit', month: '2-digit' }))}</span>
                    <span class="min0"><span class="t">${escapeHTML(m.movement_type)}</span><span class="d">${escapeHTML([m.reference_document, m.username, m.location_code || m.sector_name].filter(Boolean).join(' · '))}</span></span>
                    <b style="color:${m.quantity < 0 ? 'var(--dgt)' : 'var(--okt)'}">${m.quantity > 0 ? '+' : ''}${escapeHTML(String(m.quantity))}</b></div>`).join('')
                || '<div class="muted" style="font-size:13px;">Sin movimientos en los últimos 30 días.</div>'}</div>
        </div>`;
}

// Abre la impresion de etiquetas con el SKU ya cargado en la primera linea.
async function imprimirEtiquetaArticulo(sku) {
    await openBatchPrintModal();
    const campo = document.querySelector('#batch-print-lines input[type="text"]');
    if (campo) campo.value = sku;
}

// Abre Reportes > Traza de Articulos con el SKU cargado y el reporte generado (fechas por defecto: ultimo mes).
async function verTrazaArticulo(sku) {
    switchView('section-kardex');
    if (typeof window.loadKardexSelectors === 'function') await window.loadKardexSelectors();
    document.getElementById('kardex-filter-sku').value = sku;
    window.loadKardexFiltered();
}

// === EDICIÓN DE ARTÍCULO Y GESTIÓN DE COMBOS/DIMENSIONES ===
async function openEditItemModal(sku) {
    const modalEl = document.getElementById('modal-edit-item');
    if (!modalEl) return;

    const item = itemsCache[sku] || { sku: sku, description: '', category: '', is_combo: false };

    const titleEl = document.getElementById('edit-item-sku-label');
    if (titleEl) titleEl.textContent = sku;

    document.getElementById('edit-item-sku').value = sku;
    
    const descVal = item.description || item.name || '';
    const catVal = item.category || '';
    
    const descInp = document.getElementById('edit-item-desc');
    const catInp = document.getElementById('edit-item-cat');
    if (descInp) descInp.value = descVal === '-' ? '' : descVal;
    if (catInp) catInp.value = catVal === '-' ? '' : catVal;

    // Lógica para Dimensiones y Pesos
    const dimSection = document.getElementById('edit-item-dimensions-section');
    if (dimSection) {
        if (typeof AppConfig !== 'undefined' && AppConfig.enable_item_dimensions === 'true') {
            dimSection.style.display = 'flex';
            document.getElementById('edit-item-length').value = item.length || 0;
            document.getElementById('edit-item-width').value = item.width || 0;
            document.getElementById('edit-item-height').value = item.height || 0;
            document.getElementById('edit-item-weight').value = item.weight || 0;
        } else {
            dimSection.style.display = 'none';
        }
    }

    // Manejo del checkbox de Combo
    const isComboCheck = document.getElementById('edit-item-is-combo');
    const comboSection = document.getElementById('edit-combo-section');
    const list = document.getElementById('edit-combo-components-list');

    if (isComboCheck) {
        isComboCheck.checked = item.is_combo;
        
        if (item.is_combo) {
            comboSection.style.display = 'block';
            list.innerHTML = '<p style="text-align:center; padding:1rem; color:var(--text-muted);">Cargando componentes...</p>';
            
            try {
                const data = await fetchAPI(`/api/admin/items/${encodeURIComponent(sku)}/combo`);
                list.innerHTML = '';
                if (data && data.components && data.components.length > 0) {
                    data.components.forEach(comp => addComboComponentRow(comp.component_sku, comp.quantity));
                } else {
                    addComboComponentRow('', 1);
                }
            } catch (e) {
                list.innerHTML = '';
                addComboComponentRow('', 1);
            }
        } else {
            comboSection.style.display = 'none';
            list.innerHTML = '';
        }
    }

    if (typeof window.openModal === 'function') {
        window.openModal('modal-edit-item');
    } else {
        modalEl.style.display = 'flex';
    }
}

function toggleEditComboSection() {
    const isChecked = document.getElementById('edit-item-is-combo').checked;
    const section = document.getElementById('edit-combo-section');
    const list = document.getElementById('edit-combo-components-list');
    
    if (isChecked) {
        section.style.display = 'block';
        if (list.children.length === 0) {
            addComboComponentRow('', 1);
        }
    } else {
        section.style.display = 'none';
        list.innerHTML = '';
    }
}

function addComboComponentRow(compSku = '', qty = 1) {
    const list = document.getElementById('edit-combo-components-list');
    if (!list) return;

    const div = document.createElement('div');
    div.className = 'dynamic-row';
    div.style.marginBottom = '8px';
    div.innerHTML = `
        <input type="text" placeholder="SKU Componente" class="combo-comp-sku" value="${escapeHTML(compSku)}" style="flex:2;" required autocomplete="off">
        <input type="number" placeholder="Cantidad" class="combo-comp-qty" value="${qty}" style="flex:1;" min="0.01" step="0.01" required>
        <button type="button" data-on-click="quitarPadre(this)" style="background:var(--error-red); color:white; border:none; padding:4px 10px; border-radius:4px; font-weight:bold; cursor:pointer;">X</button>
    `;
    list.appendChild(div);
}

async function saveItemEdit(event) {
    event.preventDefault();
    const sku = document.getElementById('edit-item-sku').value;
    const desc = document.getElementById('edit-item-desc').value.trim();
    const cat = document.getElementById('edit-item-cat').value.trim();
    const isCombo = document.getElementById('edit-item-is-combo').checked;

    // Valores de Dimensiones
    let length = 0, width = 0, height = 0, weight = 0;
    if (typeof AppConfig !== 'undefined' && AppConfig.enable_item_dimensions === 'true') {
        length = parseFloat(document.getElementById('edit-item-length').value) || 0;
        width = parseFloat(document.getElementById('edit-item-width').value) || 0;
        height = parseFloat(document.getElementById('edit-item-height').value) || 0;
        weight = parseFloat(document.getElementById('edit-item-weight').value) || 0;
    }

    const components = [];
    if (isCombo) {
        const rows = document.querySelectorAll('#edit-combo-components-list .dynamic-row');
        rows.forEach(r => {
            const compSku = r.querySelector('.combo-comp-sku')?.value.trim().toUpperCase();
            const qty = parseFloat(r.querySelector('.combo-comp-qty')?.value);
            if (compSku && !isNaN(qty) && qty > 0) {
                components.push({ component_sku: compSku, quantity: qty });
            }
        });
    }

    const payload = { 
        description: desc, 
        category: cat,
        length: length,
        width: width,
        height: height,
        weight: weight,
        is_combo: isCombo,
        components: components
    };

    const btn = event.target.querySelector('button[type="submit"]');
    btn.disabled = true;
    btn.textContent = 'Guardando...';

    try {
        const res = await fetchAPI(`/api/admin/items/${encodeURIComponent(sku)}`, {
            method: 'PUT',
            body: payload
        });
        
        if (res && res.status === 'success') {
            if (typeof window.closeModal === 'function') {
                window.closeModal('modal-edit-item');
            } else {
                document.getElementById('modal-edit-item').style.display = 'none';
            }
            if (typeof showToast === 'function') showToast(res.message, "success");
            handleSearchItems(itemsCurrentPage);
        } else {
            alert("Error al guardar los cambios.");
        }
    } catch (e) {
        console.error(e);
        if (typeof showToast === 'function') showToast(e.message, "error");
    } finally {
        btn.disabled = false;
        btn.textContent = 'Guardar Cambios';
    }
}

// === DESGLOSE DE STOCK Y MODALES ===
// === ALTA DE UN ARTÍCULO ===
async function crearArticulo(event) {
    event.preventDefault();
    const num = id => parseFloat(document.getElementById(id).value) || 0;
    try {
        const r = await fetchAPI('/api/admin/items', { method: 'POST', body: {
            sku: document.getElementById('nuevo-art-sku').value.trim(),
            description: document.getElementById('nuevo-art-desc').value.trim(),
            category: document.getElementById('nuevo-art-cat').value.trim(),
            length: num('nuevo-art-largo'), width: num('nuevo-art-ancho'), height: num('nuevo-art-alto'), weight: num('nuevo-art-peso'),
        } });
        showToast(r.message, 'success');
        document.getElementById('form-nuevo-articulo').reset();
        closeModal('modal-nuevo-articulo');
    } catch (e) { showToast(e.message, 'error'); }
}

// === IMPORTACIÓN (CATÁLOGO Y UBICACIONES): Excel, texto con tabulaciones o CSV ===
function openImportModal(type) {
    const modal = document.getElementById('modal-import');
    if (!modal) return;
    
    document.getElementById('import-type').value = type;
    const title = document.getElementById('import-modal-title');
    const ayuda = document.getElementById('import-modal-ayuda');
    const formatos = 'Excel (.xlsx), texto separado por tabulaciones (.txt) o CSV (con ; o ,). La primera fila son los encabezados.';
    if (type === 'items') {
        title.textContent = 'Importar artículos';
        // Columnas y plantilla para descargar (se arma con DOM: sin HTML de afuera).
        ayuda.textContent = `${formatos} Columnas: sku, descripcion, categoria y, si querés, largo_cm, ancho_cm, alto_cm, peso_kg. Un SKU que ya existe se actualiza. `;
        const enlace = document.createElement('a');
        enlace.href = '/api/admin/import/items/plantilla';
        enlace.textContent = 'Descargar plantilla';
        ayuda.appendChild(enlace);
    } else if (type === 'item_locations') {
        title.textContent = 'Asignar artículos a ubicaciones';
        ayuda.textContent = `${formatos} Columnas: sku y ubicacion (la ubicación tiene que existir).`;
    } else {
        title.textContent = 'Importar';
        ayuda.textContent = formatos;
    }

    document.getElementById('import-file').value = '';
    document.getElementById('import-file-name').style.display = 'none';
    
    if (typeof window.openModal === 'function') {
        window.openModal('modal-import');
    } else {
        modal.style.display = 'flex';
    }
}

function handleFileSelect(event) {
    const file = event.target.files[0];
    const nameLabel = document.getElementById('import-file-name');
    if (file) {
        nameLabel.textContent = file.name;
        nameLabel.style.display = 'block';
    } else {
        nameLabel.style.display = 'none';
    }
}

async function uploadCSV() {
    const fileInput = document.getElementById('import-file');
    const type = document.getElementById('import-type').value;
    const file = fileInput.files[0];
    
    if (!file) {
        alert("Elegí un archivo primero.");
        return;
    }

    const formData = new FormData();
    formData.append("file", file);

    let url = '';
    if (type === 'items') url = '/api/admin/import/items';
    else if (type === 'item_locations') url = '/api/admin/import/item-locations';
    else return;

    const btn = document.getElementById('btn-upload-csv');
    btn.disabled = true;
    btn.textContent = 'Procesando...';

    try {
        const headers = { 'X-CSRF-Token': csrfToken() };

        const response = await fetch(url, {
            method: 'POST',
            headers: headers,
            body: formData
        });

        const result = await response.json();
        
        if (response.ok) {
            alert(result.message || "Archivo procesado con éxito.");
            if (typeof window.closeModal === 'function') {
                window.closeModal('modal-import');
            } else {
                document.getElementById('modal-import').style.display = 'none';
            }
            handleSearchItems(1);
        } else {
            alert(result.detail || "Error al procesar el archivo.");
        }
    } catch (err) {
        console.error("Error upload:", err);
        alert("Fallo de conexión al subir el archivo.");
    } finally {
        btn.disabled = false;
        btn.textContent = 'Procesar Archivo';
    }
}

// === IMPRESIÓN DE ETIQUETAS ===
function addDynamicLineBatchPrint() {
    const list = document.getElementById('batch-print-lines');
    if (!list) return;
    const div = document.createElement('div');
    div.className = 'dynamic-row';
    div.innerHTML = `
        <input type="text" placeholder="Código SKU" style="flex:2;" required>
        <input type="number" placeholder="Cantidad" style="flex:1;" min="1" value="1" required>
        <button type="button" data-on-click="quitarPadre(this)" style="background:var(--error-red); color:white; border:none; padding:4px 8px; border-radius:4px; font-weight:bold; cursor:pointer;">X</button>
    `;
    list.appendChild(div);
}

async function openBatchPrintModal() {
    const modalEl = document.getElementById('modal-batch-print-items');
    if (!modalEl) return;

    const selectEl = modalEl.querySelector('select');
    if (selectEl) {
        selectEl.innerHTML = '<option value="">Cargando sectores...</option>';
        try {
            const data = await fetchAPI('/api/admin/sectors');
            const sectors = Array.isArray(data) ? data : (data.sectors || data.rows || data.data || []);
            if (sectors.length > 0) {
                selectEl.innerHTML = '<option value="">-- Seleccionar Sector --</option>' + 
                    sectors.map(s => `<option value="${escapeHTML(s.print_queue_code || s.code || s.name)}">${escapeHTML(s.name || s.sector_name || s.code)}</option>`).join('');
            } else {
                selectEl.innerHTML = '<option value="RECEPCION">Recepcion</option>';
            }
        } catch (e) {
            selectEl.innerHTML = '<option value="RECEPCION">Recepcion</option>';
        }
    }

    if (typeof window.openModal === 'function') {
        window.openModal('modal-batch-print-items');
    } else {
        modalEl.style.display = 'flex';
    }
}

async function sendBatchPrintJobs() {
    const modalEl = document.getElementById('modal-batch-print-items');
    if (!modalEl) return;

    const selectEl = modalEl.querySelector('select');
    let queueCode = '';
    if (selectEl) {
        const selectedOpt = selectEl.options[selectEl.selectedIndex];
        queueCode = selectedOpt ? (selectedOpt.value || selectedOpt.text) : selectEl.value;
    }

    if (!queueCode || queueCode.toLowerCase().includes('seleccionar') || queueCode.toLowerCase().includes('cargando')) {
        alert("Por favor seleccione un Sector de Impresión válido.");
        return;
    }

    const inputs = Array.from(modalEl.querySelectorAll('input')).filter(i => i.type !== 'hidden');
    const skus = [];

    for (let i = 0; i < inputs.length; i++) {
        const val = inputs[i].value.trim();
        if (val && val.length >= 2 && isNaN(val)) {
            let qty = 1;
            if (i + 1 < inputs.length && !isNaN(inputs[i + 1].value) && inputs[i + 1].value.trim() !== '') {
                qty = parseInt(inputs[i + 1].value.trim(), 10) || 1;
            }
            for (let q = 0; q < qty; q++) {
                skus.push(val);
            }
        }
    }

    if (skus.length === 0) {
        alert("Por favor ingrese al menos un SKU válido.");
        return;
    }

    const btn = document.getElementById('btn-send-batch-print') || modalEl.querySelector('button.btn-submit');
    if (btn) { btn.disabled = true; btn.textContent = 'Enviando...'; }

    try {
        const res = await fetchAPI('/api/admin/items/batch-print-labels', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                queue_code: queueCode.trim().toUpperCase(),
                skus: skus
            })
        });

        alert(`¡Orden enviada! Se generaron ${res.jobs_created || skus.length} etiqueta(s) para el sector '${queueCode.toUpperCase()}'.`);
        
        if (typeof window.closeModal === 'function') {
            window.closeModal('modal-batch-print-items');
        } else {
            modalEl.style.display = 'none';
        }
    } catch (err) {
        console.error("Error al enviar trabajo de impresión:", err);
        alert("No se pudo enviar la orden de impresión. Verifique los datos.");
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = 'Enviar a Impresora'; }
    }
}

document.addEventListener('DOMContentLoaded', () => {
    setTimeout(() => {
        handleSearchItems(1);
    }, 150);
});

// Exponer funciones globales
window.handleSearchItems = handleSearchItems;
window.loadItems = handleSearchItems;
window.buscarArticulos = buscarArticulos;
window.limpiarBusquedaArticulos = limpiarBusquedaArticulos;
window.prevItemPage = prevItemPage;
window.nextItemPage = nextItemPage;
window.openBatchPrintModal = openBatchPrintModal;
window.openEditItemModal = openEditItemModal;
window.sendBatchPrintJobs = sendBatchPrintJobs;
window.addDynamicLineBatchPrint = addDynamicLineBatchPrint;
window.openImportModal = openImportModal;
window.handleFileSelect = handleFileSelect;
window.uploadCSV = uploadCSV;
window.saveItemEdit = saveItemEdit;
window.toggleEditComboSection = toggleEditComboSection;
window.addComboComponentRow = addComboComponentRow;
window.crearArticulo = crearArticulo;
