// === AJUSTES DE STOCK (section-ajustes) ===
// Al entrar no se lista nada: se busca con un campo por dato. "Crear ajuste" abre la tarjeta de alta: sucursal,
// seccion (sector), concepto y observacion; los articulos se agregan de a uno (SKU, suma o resta, cantidad,
// ubicacion si el sector las usa, lote si estan activados). "Continuar" muestra el resumen; ahi se confirma
// (POST /api/admin/stock-adjustments), se vuelve a editar o se descarta. Esc descarta (o vuelve a editar).

const AJUSTES_POR_BUSQUEDA = 200;
let ajusteBusqueda = null;         // URLSearchParams de la ultima busqueda (null = todavia no se busco)
let ajusteSectores = [];           // GET /api/admin/sectors
let ajusteLineas = [];             // [{sku, description, quantity (firmada), location_code, lot_number}]
let ajusteConLotes = false;        // Configuracion: enable_lots_expiration

function fechaAjuste(valor) {
    return valor ? new Date(valor).toLocaleString('es-AR', { dateStyle: 'short', timeStyle: 'short' }) : '-';
}

function cantidadAjuste(q) {
    const n = Number(q) || 0;
    return `<span class="badge ${n >= 0 ? 'badge-info' : 'badge-warning'}">${n > 0 ? '+' : ''}${n}</span>`;
}

// --- Historial ---
async function loadAjustes() {
    const tbody = document.getElementById('table-ajustes-body');
    const resumen = document.getElementById('ajustes-resumen');
    if (!tbody) return;
    resumen.hidden = true;
    if (!ajusteBusqueda) {
        tbody.innerHTML = '<tr><td colspan="10" class="busqueda-vacia">Completá los filtros y tocá Buscar.</td></tr>';
        return;
    }
    try {
        const rows = await fetchAPI('/api/admin/stock-adjustments?' + ajusteBusqueda.toString()) || [];
        resumen.textContent = rows.length >= AJUSTES_POR_BUSQUEDA
            ? `Se muestran los primeros ${AJUSTES_POR_BUSQUEDA}: afiná la búsqueda.`
            : `${rows.length} resultado${rows.length === 1 ? '' : 's'}.`;
        resumen.hidden = false;
        tbody.innerHTML = rows.length === 0
            ? '<tr><td colspan="10" class="busqueda-vacia">No hay ajustes que coincidan con la búsqueda.</td></tr>'
            : rows.map(a => `<tr>
                <td class="font-mono" style="color:var(--accent); font-weight:bold;">${escapeHTML(a.adjustment_number)}</td>
                <td><small>${escapeHTML(fechaAjuste(a.created_at))}</small></td>
                <td>${escapeHTML(a.branch_name)}</td>
                <td>${escapeHTML(a.sector_name)}</td>
                <td>${escapeHTML(a.reason)}</td>
                <td style="text-align:right;">${a.lines_count}</td>
                <td style="text-align:right;">${a.total_in ? '+' + a.total_in : '-'}</td>
                <td style="text-align:right;">${a.total_out ? '-' + a.total_out : '-'}</td>
                <td>${escapeHTML(a.created_by || '-')}</td>
                <td><button type="button" class="btn-secondary" style="padding:3px 8px; font-size:0.75rem;" data-on-click="verAjuste(${jsArg(a.adjustment_number)})">Ver</button></td>
            </tr>`).join('');
    } catch (e) {
        tbody.innerHTML = '<tr><td colspan="10" style="text-align:center; color:var(--danger);">Error al cargar los ajustes.</td></tr>';
    }
}

function buscarAjustes(event) {
    if (event) event.preventDefault();
    const params = new URLSearchParams({ limit: AJUSTES_POR_BUSQUEDA });
    document.querySelectorAll('#filtros-ajustes [name]').forEach(el => {
        const valor = (el.value || '').trim();
        if (valor) params.set(el.name, valor);
    });
    ajusteBusqueda = params;
    loadAjustes();
}

function limpiarAjustes() {
    document.getElementById('filtros-ajustes')?.reset();
    ajusteBusqueda = null;
    loadAjustes();
}

async function verAjuste(numero) {
    let a;
    try { a = await fetchAPI('/api/admin/stock-adjustments/' + encodeURIComponent(numero)); } catch (e) { return; }
    document.getElementById('aj-det-titulo').textContent = `Ajuste ${a.adjustment_number}`;
    document.getElementById('aj-det-cabecera').innerHTML =
        `<strong>${escapeHTML(a.reason)}</strong> · ${escapeHTML(a.branch_name)} / ${escapeHTML(a.sector_name)} · ${escapeHTML(fechaAjuste(a.created_at))} · ${escapeHTML(a.created_by || '')}`
        + (a.notes ? `<br><small>${escapeHTML(a.notes)}</small>` : '');
    document.getElementById('aj-det-lineas').innerHTML = (a.lines || []).map(l => `<tr>
        <td class="font-mono">${escapeHTML(l.sku)}</td><td>${escapeHTML(l.description || '')}</td>
        <td class="font-mono">${escapeHTML(l.location_code || '-')}</td><td>${escapeHTML(l.lot_number || '-')}</td>
        <td style="text-align:right;">${cantidadAjuste(l.quantity)}</td></tr>`).join('');
    openModal('modal-ajuste');
}

// --- Alta ---
async function nuevoAjuste() {
    ajusteLineas = [];
    document.getElementById('form-ajuste').reset();
    document.getElementById('aj-sku-ayuda').textContent = '';
    pintarLineasAjuste();
    document.getElementById('ajuste-resumen').hidden = true;
    const alta = document.getElementById('ajuste-alta');
    alta.hidden = false;
    alta.scrollIntoView({ behavior: 'smooth', block: 'start' });
    try {
        const [sucursales, sectores, conceptos, cfg, prox] = await Promise.all([
            fetchAPI('/api/admin/branches'), fetchAPI('/api/admin/sectors'), fetchAPI('/api/admin/stock-adjustments/reasons'),
            fetchAPI('/api/admin/settings'), fetchAPI('/api/admin/stock-adjustments/next-number')]);
        ajusteSectores = sectores || [];
        ajusteConLotes = String((cfg || {}).enable_lots_expiration) === 'true';
        document.getElementById('aj-lote').hidden = !ajusteConLotes;
        document.getElementById('aj-num').value = (prox || {}).number || '';
        const conSectores = (sucursales || []).filter(b => ajusteSectores.some(s => String(s.branch_id) === String(b.id)));
        document.getElementById('aj-branch').innerHTML = conSectores.length
            ? conSectores.map(b => `<option value="${escapeHTML(b.id)}">${escapeHTML(b.name)}</option>`).join('')
            : '<option value="">Primero creá una sucursal con un sector (Depósitos)</option>';
        document.getElementById('aj-reason').innerHTML = (conceptos || []).length
            ? '<option value="">Elegí un concepto</option>' + conceptos.map(c => `<option value="${escapeHTML(c)}">${escapeHTML(c)}</option>`).join('')
            : '<option value="">Cargá los conceptos en Configuración, Numeración</option>';
        pintarSectoresAjuste();
    } catch (e) { /* fetchAPI ya avisa */ }
}

function pintarSectoresAjuste() {
    const sucursal = document.getElementById('aj-branch').value;
    const opciones = ajusteSectores.filter(s => String(s.branch_id) === String(sucursal))
        .map(s => `<option value="${escapeHTML(s.id)}">${escapeHTML(s.name)}</option>`).join('');
    document.getElementById('aj-sector').innerHTML = opciones || '<option value="">Esta sucursal no tiene sectores</option>';
    onSectorAjuste();
}

function sectorAjuste() {
    const id = document.getElementById('aj-sector').value;
    return ajusteSectores.find(s => String(s.id) === String(id)) || null;
}

function onSectorAjuste() {
    const s = sectorAjuste();
    document.getElementById('aj-ubic').hidden = !(s && s.uses_locations);
    // Las lineas ya cargadas son de otro sector: la ubicacion puede no existir en el nuevo.
    if (ajusteLineas.length) {
        ajusteLineas = [];
        pintarLineasAjuste();
        showToast('Cambiaste la sucursal o el sector: los artículos agregados se quitaron.', 'warning');
    }
}

async function agregarLineaAjuste() {
    const skuEl = document.getElementById('aj-sku');
    const sku = skuEl.value.trim().toUpperCase();
    const signo = Number(document.getElementById('aj-tipo').value) || 1;
    const cantidad = Number(document.getElementById('aj-cant').value);
    const s = sectorAjuste();
    const ubicacion = (s && s.uses_locations) ? document.getElementById('aj-ubic').value.trim().toUpperCase() : '';
    const lote = ajusteConLotes ? document.getElementById('aj-lote').value.trim() : '';
    const ayuda = document.getElementById('aj-sku-ayuda');
    if (!s) { showToast('Elegí la sucursal y el sector antes de agregar artículos.', 'danger'); return; }
    if (!sku) { showToast('Escribí el SKU.', 'danger'); skuEl.focus(); return; }
    if (!(cantidad > 0)) { showToast('La cantidad tiene que ser mayor a 0.', 'danger'); document.getElementById('aj-cant').focus(); return; }
    if (s.uses_locations && !ubicacion) { showToast(`El sector ${s.name} trabaja con ubicaciones: indicá una.`, 'danger'); document.getElementById('aj-ubic').focus(); return; }
    let articulo = null;
    try {
        const r = await fetchAPI(`/api/admin/items?sku=${encodeURIComponent(sku)}&limit=50`);
        articulo = ((r && r.items) || []).find(i => String(i.sku).toUpperCase() === sku) || null;
    } catch (e) { return; }
    if (!articulo) { ayuda.textContent = `El artículo ${sku} no existe en el maestro.`; showToast(`El artículo ${sku} no existe.`, 'danger'); skuEl.focus(); return; }
    const q = signo * cantidad;
    const igual = ajusteLineas.find(l => l.sku === sku && l.location_code === ubicacion && l.lot_number === lote);
    if (igual) {
        igual.quantity += q;    // misma linea: se suma (el servidor hace lo mismo)
        if (igual.quantity === 0) ajusteLineas = ajusteLineas.filter(l => l !== igual);
    } else {
        ajusteLineas.push({ sku, description: articulo.description || '', quantity: q, location_code: ubicacion, lot_number: lote });
    }
    ayuda.textContent = `${sku}: ${articulo.description || ''} (stock total ${articulo.total_stock ?? '-'})`;
    pintarLineasAjuste();
    skuEl.value = ''; document.getElementById('aj-cant').value = '';
    if (s.uses_locations) document.getElementById('aj-ubic').value = '';
    if (ajusteConLotes) document.getElementById('aj-lote').value = '';
    skuEl.focus();
}

// Enter en los campos del articulo agrega la linea (y no manda el formulario al resumen).
function enterAgregaAjuste(evento) {
    if (evento.key !== 'Enter') return;
    evento.preventDefault();
    agregarLineaAjuste();
}

function quitarLineaAjuste(i) {
    ajusteLineas.splice(i, 1);
    pintarLineasAjuste();
}

function filaLineaAjuste(l, i) {
    return `<tr>
        <td class="font-mono" style="color:var(--accent); font-weight:bold;">${escapeHTML(l.sku)}</td>
        <td>${escapeHTML(l.description)}</td>
        <td class="font-mono">${escapeHTML(l.location_code || '-')}</td>
        <td>${escapeHTML(l.lot_number || '-')}</td>
        <td style="text-align:right;">${cantidadAjuste(l.quantity)}</td>
        ${i === undefined ? '' : `<td><button type="button" class="btn-danger" style="padding:3px 8px; font-size:0.75rem;" data-on-click="quitarLineaAjuste(${i})">X</button></td>`}
    </tr>`;
}

function pintarLineasAjuste() {
    document.getElementById('aj-lineas').innerHTML = ajusteLineas.length
        ? ajusteLineas.map((l, i) => filaLineaAjuste(l, i)).join('')
        : '<tr><td colspan="6" class="busqueda-vacia">Todavía no agregaste artículos.</td></tr>';
}

function totalesAjuste() {
    const suma = ajusteLineas.filter(l => l.quantity > 0).reduce((t, l) => t + l.quantity, 0);
    const resta = -ajusteLineas.filter(l => l.quantity < 0).reduce((t, l) => t + l.quantity, 0);
    return { suma, resta };
}

function resumirAjuste(event) {
    event.preventDefault();
    const reason = document.getElementById('aj-reason').value;
    if (!document.getElementById('aj-branch').value || !document.getElementById('aj-sector').value) { showToast('Elegí la sucursal y el sector.', 'danger'); return; }
    if (!reason) { showToast('Elegí el concepto del ajuste.', 'danger'); return; }
    if (!ajusteLineas.length) { showToast('Agregá al menos un artículo.', 'danger'); return; }
    const { suma, resta } = totalesAjuste();
    const sucursal = document.getElementById('aj-branch').selectedOptions[0]?.textContent || '';
    const sector = document.getElementById('aj-sector').selectedOptions[0]?.textContent || '';
    const notas = document.getElementById('aj-notes').value.trim();
    document.getElementById('aj-resumen-cabecera').innerHTML =
        `<strong>${escapeHTML(reason)}</strong> · ${escapeHTML(sucursal)} / ${escapeHTML(sector)} · ${ajusteLineas.length} artículo${ajusteLineas.length === 1 ? '' : 's'}`
        + (notas ? `<br><small>${escapeHTML(notas)}</small>` : '');
    document.getElementById('aj-resumen-lineas').innerHTML = ajusteLineas.map(l => filaLineaAjuste(l)).join('');
    document.getElementById('aj-resumen-totales').innerHTML =
        `<tr><td colspan="4" style="text-align:right; font-weight:bold;">Suma +${suma} · Resta -${resta}</td><td></td></tr>`;
    document.getElementById('ajuste-alta').hidden = true;
    const r = document.getElementById('ajuste-resumen');
    r.hidden = false;
    r.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function volverEditarAjuste() {
    document.getElementById('ajuste-resumen').hidden = true;
    document.getElementById('ajuste-alta').hidden = false;
}

function cerrarAltaAjuste() {
    ajusteLineas = [];
    document.getElementById('ajuste-resumen').hidden = true;
    document.getElementById('ajuste-alta').hidden = true;
}

function descartarAjuste() {
    if (ajusteLineas.length && !confirm('¿Descartar el ajuste? Se pierden los artículos agregados.')) return;
    cerrarAltaAjuste();
}

async function confirmarAjuste() {
    const boton = document.getElementById('aj-confirmar');
    boton.disabled = true;
    try {
        const clave = (window.crypto && crypto.randomUUID) ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
        const r = await fetchAPI('/api/admin/stock-adjustments', {
            method: 'POST', headers: { 'X-Idempotency-Key': clave },
            body: {
                branch_id: document.getElementById('aj-branch').value, sector_id: document.getElementById('aj-sector').value,
                reason: document.getElementById('aj-reason').value, notes: document.getElementById('aj-notes').value.trim(),
                lines: ajusteLineas.map(l => ({ sku: l.sku, quantity: l.quantity, location_code: l.location_code || null, lot_number: l.lot_number || '' })),
            },
        });
        showToast(r.message || 'Ajuste registrado.');
        cerrarAltaAjuste();
        // Muestra el ajuste recien registrado en el historial.
        document.getElementById('filtros-ajustes').reset();
        document.getElementById('f-aj-numero').value = r.adjustment_number || '';
        buscarAjustes(null);
    } catch (e) {
        /* fetchAPI ya mostro el motivo; se queda en el resumen para corregir */
    } finally {
        boton.disabled = false;
    }
}

// Esc en Ajustes de stock (sin modal abierto): en el resumen vuelve a editar; en el alta descarta.
document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape' || document.body.classList.contains('help-mode-active')) return;
    if (!document.getElementById('section-ajustes')?.classList.contains('active')) return;
    if ([...document.querySelectorAll('.modal')].some(m => m.style.display === 'flex')) return;
    if (!document.getElementById('ajuste-resumen').hidden) volverEditarAjuste();
    else if (!document.getElementById('ajuste-alta').hidden) descartarAjuste();
});
