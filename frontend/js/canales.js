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

// Cuantos canales muestra la tarjeta de Configuracion; el resto se ve con "Ver canales".
const CANALES_DESTACADOS = 4;

// Mas activos primero: los activos, con mas pedidos en 7 dias y usados mas recientemente.
function canalesPorActividad(canales) {
    const uso = c => (c.last_used_at ? new Date(c.last_used_at).getTime() : 0);
    return [...canales].sort((a, b) => (b.is_active - a.is_active) || ((b.orders_7d || 0) - (a.orders_7d || 0)) || (uso(b) - uso(a)));
}

function botonesCanal(c) {
    const estilo = 'padding:3px 8px; font-size:0.75rem;';
    return `<button type="button" class="btn-secondary" style="${estilo}" data-on-click="abrirCanalVenta(${jsArg(c.id)})">Configuración</button>`
        + `<button type="button" class="btn-secondary" style="${estilo} margin-left:6px;" data-on-click="abrirEventosCanal(${jsArg(c.id)})">Eventos</button>`
        + `<button type="button" class="btn-secondary" style="${estilo} margin-left:6px;" data-on-click="rotarClaveCanal(${jsArg(c.id)})">Rotar clave</button>`;
}

function pintarCanalesDestacados() {
    const caja = document.getElementById('canales-venta-destacados');
    const ver = document.getElementById('btn-ver-canales');
    if (ver) ver.textContent = canalesVentaCache.length ? `Ver canales (${canalesVentaCache.length})` : 'Ver canales';
    if (!caja) return;
    if (canalesVentaCache.length === 0) {
        caja.innerHTML = '<p class="canales-vacio">Todavía no hay canales. Se crean cuando JZ Tech Solutions instala la conexión con una tienda (por ejemplo el middleware de Mercado Libre).</p>';
        return;
    }
    caja.innerHTML = canalesPorActividad(canalesVentaCache).slice(0, CANALES_DESTACADOS).map(c => `
        <div class="canal-linea">
            <div class="canal-linea-datos">
                <div><span class="tienda-logo-chico" title="${escapeHTML(nombreTienda(c.platform))}">${logoTienda(c.platform)}</span><strong>${escapeHTML(c.name)}</strong> <span class="font-mono canal-linea-codigo">${escapeHTML(c.code)}</span>
                    <span class="badge ${c.is_active ? 'badge-success' : 'badge-danger'}">${c.is_active ? 'ACTIVO' : 'INACTIVO'}</span></div>
                <small>${c.orders_7d || 0} pedido${c.orders_7d === 1 ? '' : 's'} en 7 días · último uso: ${escapeHTML(fechaCanal(c.last_used_at))}</small>
            </div>
            <div class="canal-linea-botones">${botonesCanal(c)}</div>
        </div>`).join('');
}

function pintarTodosCanales() {
    const tbody = document.getElementById('tabla-canales-venta');
    if (!tbody) return;
    if (canalesVentaCache.length === 0) {
        tbody.innerHTML = '<tr><td colspan="8" style="text-align:center; padding:1.5rem; color:var(--text-muted);">Todavía no hay canales. Se crean cuando JZ Tech Solutions instala la conexión con una tienda (por ejemplo el middleware de Mercado Libre).</td></tr>';
        return;
    }
    tbody.innerHTML = canalesPorActividad(canalesVentaCache).map(c => `
        <tr>
            <td style="font-weight:bold; color:var(--accent); white-space:nowrap;" class="font-mono"><span class="tienda-logo-chico" title="${escapeHTML(nombreTienda(c.platform))}">${logoTienda(c.platform)}</span>${escapeHTML(c.code)}</td>
            <td>${escapeHTML(c.name)}</td>
            <td><small>${escapeHTML(NOMBRE_MODO_CANAL[c.stock_mode] || c.stock_mode)}</small></td>
            <td><small>${escapeHTML(nombresSucursalesCanal(c.stock_branch_ids))}</small></td>
            <td><span class="badge ${c.is_active ? 'badge-success' : 'badge-danger'}">${c.is_active ? 'ACTIVO' : 'INACTIVO'}</span></td>
            <td>${Number(c.orders_7d) || 0}</td>
            <td><small>${escapeHTML(fechaCanal(c.last_used_at))}</small></td>
            <td style="text-align:right; white-space:nowrap;">${botonesCanal(c)}</td>
        </tr>
    `).join('');
}

async function cargarCanalesVenta() {
    const caja = document.getElementById('canales-venta-destacados');
    try {
        const [canales] = await Promise.all([fetchAPI('/api/admin/sales-channels'), cargarSucursalesCanal()]);
        canalesVentaCache = canales || [];
        pintarCanalesDestacados();
        pintarTodosCanales();
    } catch (e) {
        if (caja) caja.innerHTML = `<p class="canales-vacio" style="color:var(--danger);">Error al cargar los canales: ${escapeHTML(e.message)}</p>`;
    }
}

function abrirTodosCanales() {
    pintarTodosCanales();
    openModal('modal-canales-todos');
}

const ESTADO_PEDIDO_CANAL = {
    PENDING: 'Pendiente', IN_PROGRESS: 'En preparación', COMPLETED: 'Para empacar', DISPATCHED: 'Despachado',
    CANCELLED: 'Cancelado', FULL: 'Full (informativo)',
};
const ENVIO_CANAL = { fulfillment: 'Full', self_service: 'Flex', cross_docking: 'Colecta', drop_off: 'Correo' };

function claseEstadoPedidoCanal(estado) {
    if (estado === 'CANCELLED') return 'badge-danger';
    if (estado === 'FULL') return 'badge-info';
    return (estado === 'DISPATCHED' || estado === 'COMPLETED') ? 'badge-success' : 'badge-warning';
}

function textoAvisoCanal(ev) {
    const p = ev.payload || {};
    if (ev.type === 'stock.changed') return `Cambió el stock de <span class="font-mono">${escapeHTML(p.sku)}</span>`;
    if (ev.type === 'order.status') {
        return `Pedido <span class="font-mono">${escapeHTML(p.document_number)}</span> (venta ${escapeHTML(p.external_ref)}) pasó a `
            + `<span class="badge ${claseEstadoPedidoCanal(p.status)}">${escapeHTML(ESTADO_PEDIDO_CANAL[p.status] || p.status)}</span>`;
    }
    return escapeHTML(ev.type);
}

async function abrirEventosCanal(id) {
    const pedidos = document.getElementById('canal-eventos-pedidos');
    const avisos = document.getElementById('canal-eventos-avisos');
    const canal = canalesVentaCache.find(c => String(c.id) === String(id)) || {};
    document.getElementById('canal-eventos-titulo').textContent = `Eventos de ${canal.name || 'el canal'}`;
    document.getElementById('canal-eventos-resumen').textContent = '';
    const cargando = c => `<tr><td colspan="${c}" style="text-align:center; padding:1rem; color:var(--text-muted);">Cargando...</td></tr>`;
    pedidos.innerHTML = cargando(6);
    avisos.innerHTML = cargando(2);
    openModal('modal-canal-eventos');
    try {
        const d = await fetchAPI(`/api/admin/sales-channels/${encodeURIComponent(id)}/events?limit=30`);
        document.getElementById('canal-eventos-resumen').textContent =
            `Código ${d.channel.code} · ${d.channel.is_active ? 'activo' : 'inactivo'} · último uso: ${fechaCanal(d.channel.last_used_at)}`;
        pedidos.innerHTML = d.orders.length === 0
            ? '<tr><td colspan="6" style="text-align:center; padding:1rem; color:var(--text-muted);">El canal todavía no cargó pedidos.</td></tr>'
            : d.orders.map(o => `
                <tr>
                    <td class="font-mono" style="color:var(--accent);">${escapeHTML(o.document_number)}${o.priority > 0 ? ' <span class="badge badge-danger">URGENTE</span>' : ''}</td>
                    <td class="font-mono">${escapeHTML(o.external_ref)}</td>
                    <td>${escapeHTML(o.external_account || '-')}</td>
                    <td>${escapeHTML(ENVIO_CANAL[String(o.shipping_type || '').toLowerCase()] || o.shipping_type || '-')}</td>
                    <td><span class="badge ${claseEstadoPedidoCanal(o.status)}">${escapeHTML(ESTADO_PEDIDO_CANAL[o.status] || o.status)}</span></td>
                    <td><small>${escapeHTML(fechaCanal(o.created_at))}</small></td>
                </tr>`).join('');
        avisos.innerHTML = d.events.length === 0
            ? '<tr><td colspan="2" style="text-align:center; padding:1rem; color:var(--text-muted);">Sin avisos en los últimos 14 días.</td></tr>'
            : d.events.map(ev => `<tr><td style="white-space:nowrap;"><small>${escapeHTML(fechaCanal(ev.created_at))}</small></td><td>${textoAvisoCanal(ev)}</td></tr>`).join('');
    } catch (e) {
        pedidos.innerHTML = `<tr><td colspan="6" style="text-align:center; padding:1rem; color:var(--danger);">Error: ${escapeHTML(e.message)}</td></tr>`;
        avisos.innerHTML = '';
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
    // Canal nuevo: la tienda del modulo abierto o la primera activada.
    const tiendaPorDefecto = tiendaActual || (tiendasCache.find(t => t.enabled) || {}).code || 'MERCADOLIBRE';
    document.getElementById('canal-venta-tienda').value = canal ? canal.platform : tiendaPorDefecto;
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
        platform: document.getElementById('canal-venta-tienda').value,
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
            mostrarClaveCanal(r.api_key, `Canal ${r.code} creado`, r.platform);
        }
        await cargarCanalesVenta();
        cargarTiendas();   // los canales conectados de cada tienda cambian el aviso y el menu
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
        mostrarClaveCanal(r.api_key, `Nueva clave del canal ${canal.code}`, canal.platform);
    } catch (e) {
        showToast(e.message, 'error');
    }
}

// Boton "Rotar clave" del modal de configuracion del canal.
function rotarClaveCanalAbierto() {
    closeModal('modal-canal-venta');
    rotarClaveCanal(document.getElementById('canal-venta-id').value);
}

function mostrarClaveCanal(clave, titulo, tienda) {
    document.getElementById('canal-clave-titulo').textContent = titulo;
    document.getElementById('canal-clave-valor').value = clave;
    document.getElementById('canal-clave-ayuda').textContent = tienda === 'MERCADOLIBRE'
        ? 'Para el middleware de Mercado Libre: en su página, "Conexión con Tracker360", cargá la dirección interna de ' +
          'Tracker en este servidor (por ejemplo http://127.0.0.1:8001) y esta clave, y tocá "Probar conexión".'
        : `Se carga en la conexión con ${nombreTienda(tienda)} junto con la dirección interna de Tracker en este servidor ` +
          '(por ejemplo http://127.0.0.1:8001).';
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

// === TIENDA ONLINE: publicaciones que informa el canal (/api/admin/sales-channels/{id}/listings) ===
// Un modulo por tienda activada (tiendas.js): muestra solo los canales de esa tienda (tiendaActual).
const PUBLICACIONES_POR_PAGINA = 100;
let publicacionesCanalDesde = 0;
let publicacionesCanalTotal = 0;

const SITUACION_PUBLICACION = {
    SIN_SKU: ['Sin SKU', 'badge-warning'],
    SKU_NO_EN_TRACKER: ['SKU no está en Tracker', 'badge-warning'],
    FULL: ['Full (stock de Mercado Libre)', 'badge-info'],
    ERROR: ['Error', 'badge-danger'],
};

// Textos del modulo segun la tienda: lo propio de Mercado Libre (MLA, Full) solo en la suya.
function prepararModuloTienda(tienda) {
    const nombre = nombreTienda(tienda);
    const esML = tienda === 'MERCADOLIBRE';
    document.getElementById('canal-pub-titulo').textContent = `${nombre}: publicaciones y stock`;
    document.getElementById('canal-pub-th-tienda').textContent = `En ${nombre}`;
    document.getElementById('canal-pub-label-publicacion').textContent = esML ? 'Publicación (MLA)' : 'Publicación';
    document.getElementById('f-pub-mla').placeholder = esML ? 'MLA123456' : '';
    document.getElementById('canal-pub-label-estado').textContent = `Estado en ${esML ? 'ML' : nombre}`;
    document.getElementById('canal-pub-opcion-full').hidden = !esML;
}

function mostrarSinConexionTienda(tienda, sinConexion) {
    document.getElementById('canal-pub-sin-conexion').hidden = !sinConexion;
    document.getElementById('canal-pub-contenido').hidden = sinConexion;
    document.getElementById('canal-pub-controles').hidden = sinConexion;
    if (!sinConexion) return;
    const nombre = nombreTienda(tienda);
    document.getElementById('canal-pub-sin-conexion-logo').innerHTML = logoTienda(tienda);
    document.getElementById('canal-pub-sin-conexion-titulo').textContent = `Todavía no hay una conexión con ${nombre}`;
    document.getElementById('canal-pub-sin-conexion-texto').textContent =
        `Para que Tracker360 reciba las ventas de ${nombre} y le mande el stock hace falta instalar la conexión con tu tienda. ` +
        'Contactá a JZ Tech Solutions y la dejamos funcionando. Si ya la instalaron, su canal se da de alta en Configuración, ' +
        `Canales de venta, eligiendo ${nombre} como tienda.`;
    document.getElementById('canal-pub-sin-conexion-mail').href = mailContactoTienda(tienda);
}

async function abrirPublicacionesCanal() {
    const select = document.getElementById('canal-pub-canal');
    if (!tiendaActual) tiendaActual = (tiendasCache.find(t => t.enabled) || {}).code || 'MERCADOLIBRE';
    const tienda = tiendaActual;
    prepararModuloTienda(tienda);
    if (select.dataset.tienda !== tienda) {   // otra tienda: se arranca sin filtros
        document.getElementById('filtros-publicaciones')?.reset();
        select.value = '';
    }
    select.dataset.tienda = tienda;
    try {
        const canales = (await fetchAPI('/api/admin/sales-channels') || []).filter(c => c.platform === tienda);
        if (tienda !== tiendaActual) return;   // se cambio de tienda mientras cargaba
        const previo = select.value;
        mostrarSinConexionTienda(tienda, canales.length === 0);
        if (canales.length === 0) {
            select.innerHTML = '<option value="">Sin canales</option>';
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
    if (event && event.type === 'submit') event.preventDefault();
    cargarPublicacionesCanal(0);
}

function limpiarPublicacionesCanal() {
    document.getElementById('filtros-publicaciones')?.reset();
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
    // Un filtro por dato (publicacion, SKU, titulo, cuenta, estado, situacion).
    document.querySelectorAll('#filtros-publicaciones [name]').forEach(el => {
        const valor = (el.value || '').trim();
        if (valor) params.set(el.name, valor);
    });
    try {
        const r = await fetchAPI(`/api/admin/sales-channels/${encodeURIComponent(id)}/listings?${params}`);
        publicacionesCanalTotal = r.total;
        const s = r.summary;
        const nombre = nombreTienda(r.channel.platform);
        document.getElementById('canal-pub-info').textContent = r.channel.listings_synced_at
            ? `Último informe del canal: ${fechaCanal(r.channel.listings_synced_at)} · Stock que Tracker le manda: ${NOMBRE_MODO_CANAL[r.channel.stock_mode] || r.channel.stock_mode}.`
            : `El canal todavía no informó publicaciones (la conexión las manda después de repasarlas en ${nombre}).`;
        document.getElementById('canal-pub-resumen').innerHTML = [
            chipResumenCanal('Todas', s.total, '', 'badge-neutral'),
            chipResumenCanal('Se sincronizan', s.ok, 'OK', 'badge-success'),
            chipResumenCanal('Sin SKU', s.sin_sku, 'SIN_SKU', 'badge-warning'),
            chipResumenCanal('SKU que no está en Tracker', s.sku_no_en_tracker, 'SKU_NO_EN_TRACKER', 'badge-warning'),
            r.channel.platform === 'MERCADOLIBRE' ? chipResumenCanal('Full', s.full, 'FULL', 'badge-info') : '',
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
