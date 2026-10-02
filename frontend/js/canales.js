// === CANALES DE VENTA (Configuracion) ===
// Alta, edicion, activacion y rotacion de clave de los canales (/api/admin/sales-channels). Solo admin.
// La clave se muestra una sola vez (al crear o al rotar): se carga en el sistema externo (por ejemplo la
// pagina del middleware de Mercado Libre, en "Conexion con Tracker360").

let canalesVentaCache = [];
let sucursalesCanalCache = [];

const NOMBRE_MODO_CANAL = {
    DISPONIBLE: 'Disponible',
    DISPONIBLE_MENOS_COMPROMETIDO: 'Disponible menos comprometido',
};

function fechaCanal(valor) {
    if (!valor) return 'Nunca';
    return new Date(valor).toLocaleString('es-AR', { dateStyle: 'short', timeStyle: 'short' });
}

async function cargarSucursalesCanal() {
    try {
        sucursalesCanalCache = await fetchAPI('/api/admin/branches') || [];
    } catch (e) {
        sucursalesCanalCache = [];
    }
}

function nombresSucursalesCanal(ids) {
    if (!ids || ids.length === 0) return 'Todas';
    return ids.map(id => (sucursalesCanalCache.find(b => String(b.id) === String(id)) || {}).name || id).join(', ');
}

async function cargarCanalesVenta() {
    const tbody = document.getElementById('tabla-canales-venta');
    if (!tbody) return;
    try {
        const [canales] = await Promise.all([fetchAPI('/api/admin/sales-channels'), cargarSucursalesCanal()]);
        canalesVentaCache = canales || [];
        if (canalesVentaCache.length === 0) {
            tbody.innerHTML = '<tr><td colspan="7" style="text-align:center; padding:1.5rem; color:var(--text-muted);">Todavía no hay canales. Creá uno para conectar el middleware de Mercado Libre.</td></tr>';
            return;
        }
        tbody.innerHTML = canalesVentaCache.map(c => `
            <tr>
                <td style="font-weight:bold; color:var(--accent);" class="font-mono">${escapeHTML(c.code)}</td>
                <td>${escapeHTML(c.name)}</td>
                <td><small>${escapeHTML(NOMBRE_MODO_CANAL[c.stock_mode] || c.stock_mode)}</small></td>
                <td><small>${escapeHTML(nombresSucursalesCanal(c.stock_branch_ids))}</small></td>
                <td><span class="badge ${c.is_active ? 'badge-success' : 'badge-danger'}">${c.is_active ? 'ACTIVO' : 'INACTIVO'}</span></td>
                <td><small>${escapeHTML(fechaCanal(c.last_used_at))}</small></td>
                <td style="text-align:right; white-space:nowrap;">
                    <button type="button" class="btn-secondary" style="padding:3px 8px; font-size:0.75rem;" data-on-click="abrirCanalVenta(${jsArg(c.id)})">Editar</button>
                    <button type="button" class="btn-secondary" style="padding:3px 8px; font-size:0.75rem; margin-left:6px;" data-on-click="rotarClaveCanal(${jsArg(c.id)})">Rotar clave</button>
                </td>
            </tr>
        `).join('');
    } catch (e) {
        tbody.innerHTML = `<tr><td colspan="7" style="text-align:center; padding:1.5rem; color:var(--danger);">Error al cargar los canales: ${escapeHTML(e.message)}</td></tr>`;
    }
}

function pintarSucursalesCanal(seleccionadas) {
    const caja = document.getElementById('canal-venta-sucursales');
    if (sucursalesCanalCache.length === 0) {
        caja.innerHTML = '<small style="color:var(--text-muted);">No hay sucursales cargadas.</small>';
        return;
    }
    caja.innerHTML = sucursalesCanalCache.map(b => `
        <label style="display:flex; align-items:center; gap:8px; font-weight:normal; margin:0.25rem 0;">
            <input type="checkbox" class="canal-venta-sucursal" value="${escapeHTML(b.id)}" ${seleccionadas.includes(String(b.id)) ? 'checked' : ''}> ${escapeHTML(b.name)}
        </label>`).join('');
}

function alternarSucursalesCanal(casilla) {
    document.getElementById('canal-venta-sucursales').style.display = casilla.checked ? 'none' : 'block';
}

async function abrirCanalVenta(id) {
    if (sucursalesCanalCache.length === 0) await cargarSucursalesCanal();
    const canal = id ? canalesVentaCache.find(c => c.id === id) : null;
    const codigo = document.getElementById('canal-venta-codigo');
    document.getElementById('canal-venta-id').value = canal ? canal.id : '';
    document.getElementById('canal-venta-titulo').textContent = canal ? `Editar canal ${canal.code}` : 'Nuevo canal de venta';
    document.getElementById('canal-venta-guardar').textContent = canal ? 'Guardar cambios' : 'Crear canal';
    codigo.value = canal ? canal.code : '';
    codigo.readOnly = !!canal;   // el codigo identifica al canal: no se cambia
    document.getElementById('canal-venta-nombre').value = canal ? canal.name : '';
    document.getElementById('canal-venta-modo').value = canal ? canal.stock_mode : 'DISPONIBLE_MENOS_COMPROMETIDO';
    const elegidas = canal && canal.stock_branch_ids ? canal.stock_branch_ids.map(String) : [];
    const todas = document.getElementById('canal-venta-todas');
    todas.checked = elegidas.length === 0;
    pintarSucursalesCanal(elegidas);
    alternarSucursalesCanal(todas);
    document.getElementById('canal-venta-activo-fila').style.display = canal ? 'block' : 'none';
    document.getElementById('canal-venta-activo').checked = canal ? canal.is_active : true;
    openModal('modal-canal-venta');
}

async function guardarCanalVenta(event) {
    event.preventDefault();
    const id = document.getElementById('canal-venta-id').value;
    const todas = document.getElementById('canal-venta-todas').checked;
    const sucursales = Array.from(document.querySelectorAll('.canal-venta-sucursal:checked')).map(c => c.value);
    if (!todas && sucursales.length === 0) {
        showToast('Elegí al menos una sucursal o marcá "Todas las sucursales".', 'error');
        return;
    }
    const datos = {
        name: document.getElementById('canal-venta-nombre').value.trim(),
        stock_mode: document.getElementById('canal-venta-modo').value,
    };
    try {
        if (id) {
            Object.assign(datos, { is_active: document.getElementById('canal-venta-activo').checked, todas_las_sucursales: todas,
                                   stock_branch_ids: todas ? null : sucursales });
            await fetchAPI(`/api/admin/sales-channels/${encodeURIComponent(id)}`, { method: 'PUT', body: datos });
            closeModal('modal-canal-venta');
            showToast('Canal actualizado.', 'success');
        } else {
            Object.assign(datos, { code: document.getElementById('canal-venta-codigo').value.trim().toUpperCase(),
                                   stock_branch_ids: todas ? null : sucursales });
            const r = await fetchAPI('/api/admin/sales-channels', { method: 'POST', body: datos });
            closeModal('modal-canal-venta');
            mostrarClaveCanal(r.api_key, `Canal ${r.code} creado`);
        }
        await cargarCanalesVenta();
    } catch (e) {
        showToast(e.message, 'error');
    }
}

async function rotarClaveCanal(id) {
    const canal = canalesVentaCache.find(c => c.id === id);
    if (!canal) return;
    if (!confirm(`¿Rotar la clave del canal ${canal.code}? La clave actual deja de funcionar en el momento: hay que cargar la nueva en el sistema que usa el canal.`)) return;
    try {
        const r = await fetchAPI(`/api/admin/sales-channels/${encodeURIComponent(id)}/rotate-key`, { method: 'POST' });
        mostrarClaveCanal(r.api_key, `Nueva clave del canal ${canal.code}`);
    } catch (e) {
        showToast(e.message, 'error');
    }
}

function mostrarClaveCanal(clave, titulo) {
    document.getElementById('canal-clave-titulo').textContent = titulo;
    document.getElementById('canal-clave-valor').value = clave;
    document.getElementById('canal-clave-ayuda').textContent =
        'Para el middleware de Mercado Libre: en su página, "Conexión con Tracker360", cargá la dirección interna de ' +
        'Tracker en este servidor (por ejemplo http://127.0.0.1:8001) y esta clave, y tocá "Probar conexión".';
    openModal('modal-canal-clave');
}

async function copiarClaveCanal() {
    const campo = document.getElementById('canal-clave-valor');
    try {
        await navigator.clipboard.writeText(campo.value);
        showToast('Clave copiada.', 'success');
    } catch (e) {
        campo.select();
        showToast('No se pudo copiar: seleccionala y copiala a mano.', 'error');
    }
}

function cerrarClaveCanal() {
    document.getElementById('canal-clave-valor').value = '';   // no queda en la pagina
    closeModal('modal-canal-clave');
}

// === MERCADO LIBRE: publicaciones que informa el canal (/api/admin/sales-channels/{id}/listings) ===
const PUBLICACIONES_POR_PAGINA = 100;
let publicacionesCanalDesde = 0;
let publicacionesCanalTotal = 0;

const SITUACION_PUBLICACION = {
    SIN_SKU: ['Sin SKU', 'badge-warning'],
    SKU_NO_EN_TRACKER: ['SKU no está en Tracker', 'badge-warning'],
    FULL: ['Full (stock de Mercado Libre)', 'badge-info'],
    ERROR: ['Error', 'badge-danger'],
};

async function abrirPublicacionesCanal() {
    const select = document.getElementById('canal-pub-canal');
    try {
        const canales = await fetchAPI('/api/admin/sales-channels');
        const previo = select.value;
        if (!canales || canales.length === 0) {
            select.innerHTML = '<option value="">Sin canales</option>';
            document.getElementById('canal-pub-tabla').innerHTML = '<tr><td colspan="9" style="text-align:center; padding:1.5rem; color:var(--text-muted);">Todavía no hay canales de venta: se crean en Configuración, Canales de venta.</td></tr>';
            return;
        }
        select.innerHTML = canales.map(c => `<option value="${escapeHTML(c.id)}">${escapeHTML(c.code)} - ${escapeHTML(c.name)}</option>`).join('');
        if (previo && canales.some(c => c.id === previo)) select.value = previo;
        await cargarPublicacionesCanal(0);
    } catch (e) {
        showToast(e.message, 'error');
    }
}

function buscarPublicacionesCanal(event) {
    if (event && event.key && event.key !== 'Enter') return;
    cargarPublicacionesCanal(0);
}

function paginaPublicacionesCanal(direccion) {
    const desde = publicacionesCanalDesde + direccion * PUBLICACIONES_POR_PAGINA;
    if (desde < 0 || desde >= publicacionesCanalTotal) return;
    cargarPublicacionesCanal(desde);
}

function chipResumenCanal(texto, valor, problema, clase) {
    return `<button type="button" class="badge ${clase}" style="border:none; cursor:pointer; padding:6px 10px;" data-on-click="filtrarPublicacionesCanal(${jsArg(problema)})">${escapeHTML(texto)}: ${escapeHTML(valor)}</button>`;
}

function filtrarPublicacionesCanal(problema) {
    document.getElementById('canal-pub-problema').value = problema;
    cargarPublicacionesCanal(0);
}

function situacionPublicacion(p) {
    let html;
    if (p.problem) {
        const [texto, clase] = SITUACION_PUBLICACION[p.problem] || [p.problem, 'badge-neutral'];
        html = `<span class="badge ${clase}">${escapeHTML(texto)}</span>`;
    } else if (p.tracker_available !== null && p.quantity !== null && Math.floor(p.tracker_available) !== p.quantity) {
        html = '<span class="badge badge-warning">Por actualizar</span>';
    } else {
        html = '<span class="badge badge-success">Sincronizada</span>';
    }
    if (p.detail) html += `<div style="font-size:0.75rem; color:var(--text-muted); margin-top:3px;">${escapeHTML(p.detail)}</div>`;
    return html;
}

async function cargarPublicacionesCanal(desde) {
    const id = document.getElementById('canal-pub-canal').value;
    const tbody = document.getElementById('canal-pub-tabla');
    if (!id) return;
    publicacionesCanalDesde = desde || 0;
    const params = new URLSearchParams({ limit: PUBLICACIONES_POR_PAGINA, offset: publicacionesCanalDesde });
    const problema = document.getElementById('canal-pub-problema').value;
    const q = document.getElementById('canal-pub-buscar').value.trim();
    if (problema) params.set('problem', problema);
    if (q) params.set('q', q);
    try {
        const r = await fetchAPI(`/api/admin/sales-channels/${encodeURIComponent(id)}/listings?${params}`);
        publicacionesCanalTotal = r.total;
        const s = r.summary;
        document.getElementById('canal-pub-info').textContent = r.channel.listings_synced_at
            ? `Último informe del canal: ${fechaCanal(r.channel.listings_synced_at)} · Stock que Tracker le manda: ${NOMBRE_MODO_CANAL[r.channel.stock_mode] || r.channel.stock_mode}.`
            : 'El canal todavía no informó publicaciones (el middleware las manda después de repasarlas en Mercado Libre).';
        document.getElementById('canal-pub-resumen').innerHTML = [
            chipResumenCanal('Todas', s.total, '', 'badge-neutral'),
            chipResumenCanal('Se sincronizan', s.ok, 'OK', 'badge-success'),
            chipResumenCanal('Sin SKU', s.sin_sku, 'SIN_SKU', 'badge-warning'),
            chipResumenCanal('SKU que no está en Tracker', s.sku_no_en_tracker, 'SKU_NO_EN_TRACKER', 'badge-warning'),
            chipResumenCanal('Full', s.full, 'FULL', 'badge-info'),
            chipResumenCanal('Con error', s.error, 'ERROR', 'badge-danger'),
        ].join('');
        if (r.items.length === 0) {
            tbody.innerHTML = '<tr><td colspan="9" style="text-align:center; padding:1.5rem; color:var(--text-muted);">No hay publicaciones con ese filtro.</td></tr>';
        } else {
            tbody.innerHTML = r.items.map(p => {
                const disponible = p.tracker_available === null || p.tracker_available === undefined ? '-' : Math.floor(p.tracker_available);
                return `<tr>
                    <td class="font-mono"><small>${escapeHTML(p.listing_id)}${p.variation_id ? ' / ' + escapeHTML(p.variation_id) : ''}</small></td>
                    <td>${escapeHTML(p.title || '-')}</td>
                    <td><small>${escapeHTML(p.account || '-')}</small></td>
                    <td class="font-mono">${escapeHTML(p.sku || '-')}</td>
                    <td><small>${escapeHTML(p.status || '-')}</small></td>
                    <td style="text-align:right;">${escapeHTML(p.quantity === null ? '-' : p.quantity)}</td>
                    <td style="text-align:right;">${escapeHTML(disponible)}</td>
                    <td>${situacionPublicacion(p)}</td>
                    <td><small>${escapeHTML(p.stock_sent_at ? fechaCanal(p.stock_sent_at) : '-')}</small></td>
                </tr>`;
            }).join('');
        }
        const hasta = Math.min(publicacionesCanalDesde + PUBLICACIONES_POR_PAGINA, r.total);
        document.getElementById('canal-pub-pagina').textContent = r.total ? `${publicacionesCanalDesde + 1}-${hasta} de ${r.total}` : '';
        document.getElementById('canal-pub-anterior').disabled = publicacionesCanalDesde === 0;
        document.getElementById('canal-pub-siguiente').disabled = hasta >= r.total;
    } catch (e) {
        tbody.innerHTML = `<tr><td colspan="9" style="text-align:center; padding:1.5rem; color:var(--danger);">Error: ${escapeHTML(e.message)}</td></tr>`;
    }
}

document.addEventListener('DOMContentLoaded', async () => {
    if (typeof esSupervisor === 'function' && await esSupervisor()) return;   // solo admin
    cargarCanalesVenta();
});
