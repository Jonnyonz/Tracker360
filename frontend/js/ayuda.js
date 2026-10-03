// === MODO AYUDA: panel "Ayuda de esta pantalla" + ayuda tactil + Esc ===
// Lo activa toggleHelpMode (admin-core.js). Con el modo activo:
//  - un panel abajo a la derecha resume la pantalla actual, con los pasos principales y un enlace a la parte
//    del instructivo que la explica (instructivo.html#ancla);
//  - los elementos con data-help se marcan (CSS body.help-mode-active) y muestran su explicacion al pasar el
//    mouse (admin-core.js) o, en pantallas tactiles, al tocarlos (aca);
//  - Esc lo desactiva.

const AYUDA_SECCIONES = {
    'section-dashboard': {
        titulo: 'Inicio',
        resumen: 'Resumen de la operación del día: pedidos pendientes, traspasos activos, carga de mercadería y productividad.',
        pasos: ['Revisá los pedidos pendientes para organizar el picking.', 'Usá los reportes para ver el detalle de cada número.'],
        ancla: 'auditoria',
    },
    'section-users': {
        titulo: 'Usuarios',
        resumen: 'Cuentas del sistema con su rol (administrador, supervisor o preparador).',
        pasos: ['Creá un usuario por persona: la auditoría registra quién hizo cada cosa.', 'Fijale una sucursal y un sector si siempre trabaja en el mismo lugar.', 'Revisá "Solicitudes" para aprobar a quienes entraron con Google.'],
        ancla: 'usuarios',
    },
    'section-entities': {
        titulo: 'Clientes y proveedores',
        resumen: 'Empresas con las que se trabaja: los proveedores se usan en compras y remitos; los clientes, en los pedidos manuales.',
        pasos: ['No se lista nada hasta buscar: completá CUIT/CUIL, razón social, rol o dirección y tocá Buscar.', 'Cargá los proveedores antes de emitir órdenes de compra.', 'Los pedidos de Mercado Libre no necesitan un cliente cargado.'],
        ancla: 'clientes',
    },
    'section-warehouse': {
        titulo: 'Depósitos',
        resumen: 'La estructura física: sucursales, sectores (cada uno con su impresora) y ubicaciones.',
        pasos: ['Creá la sucursal con su dirección.', 'Cada sucursal tiene Configuración (sus datos) y Sectores (sus sectores y, en cada uno, sus ubicaciones).', 'Agregá los sectores y su cola de impresión.', 'Cargá las ubicaciones e imprimí sus etiquetas.'],
        ancla: 'depositos',
    },
    'section-items': {
        titulo: 'Artículos',
        resumen: 'El maestro de productos. El SKU tiene que ser idéntico al del canal de venta (Mercado Libre).',
        pasos: ['No se lista nada hasta buscar: SKU, descripción, categoría, ubicación, tipo (simple o combo) o stock.', 'Cargá cada artículo con su SKU.', 'Imprimí etiquetas de artículo si las necesitás.'],
        ancla: 'articulos',
    },
    'section-purchases': {
        titulo: 'Compras y recepción',
        resumen: 'Órdenes de compra, remitos de entrada y traspasos.',
        pasos: ['En el menú, Compras y Recepción se abre en Órdenes de compra, Remitos, Facturas, Traspasos y Devoluciones.', 'Cada uno muestra su historial al buscar, con filtros propios; Nuevo abre el formulario de alta.', 'Emití la orden de compra al proveedor y, al llegar la mercadería, registrá el remito contra la OC.', 'Con el segundo control activado, el depósito controla a ciegas con la colectora.'],
        ancla: 'compras',
    },
    'section-orders': {
        titulo: 'Pedidos',
        resumen: 'Seguimiento de los pedidos de venta: pendientes, en picking, completos, despachados, Full y cancelados.',
        pasos: ['No se lista nada hasta buscar: usá los atajos (Para empacar, De hoy, Urgentes) o los filtros.', 'Los urgentes (Flex) se preparan primero.', 'Con el pedido completo, tocá "Empacar" para despachar e imprimir la etiqueta.', '"Detalle" muestra quién preparó cada parte y las observaciones.'],
        ancla: 'pedidos',
    },
    'section-inventory': {
        titulo: 'Conteos de inventario',
        resumen: 'Sesiones de conteo ciego, en caliente o en frío, con revisión antes de aplicar.',
        pasos: ['Al entrar se ven las sesiones de hoy; con los filtros (fechas, sucursal, sector, estado, modalidad, operador) se buscan otras.', 'Creá la sesión de conteo.', 'Contá con la colectora.', 'Revisá las diferencias y aplicá. Lo no contado no se ajusta.'],
        ancla: 'inventario',
    },
    'section-kardex': {
        titulo: 'Traza de artículos',
        resumen: 'Cada movimiento de un artículo: fecha, usuario, origen, destino y documento.',
        pasos: ['Buscá el SKU y filtrá por fechas o sucursal.', 'Exportá a CSV si lo necesitás en Excel.'],
        ancla: 'reportes',
    },
    'section-logs': {
        titulo: 'Auditoría general',
        resumen: 'Registro de las acciones importantes con usuario, fecha e IP.',
        pasos: ['Filtrá por usuario o acción para encontrar un cambio.', 'Las acciones en rojo son errores o intentos rechazados.'],
        ancla: 'auditoria',
    },
    'section-canal-publicaciones': {
        titulo: 'Mercado Libre',
        resumen: 'Publicaciones que informa el middleware, con el stock que tienen en Mercado Libre y el disponible que manda Tracker.',
        pasos: ['Elegí el canal arriba.', 'Buscá por dato: publicación (MLA), SKU, título, cuenta, estado o situación.', 'Tocá un resumen (por ejemplo "Sin SKU") para filtrar.', 'Corregí lo que no se sincroniza: SKU en ML o alta del artículo en Tracker.'],
        ancla: 'mercado-libre',
    },
    'section-settings': {
        titulo: 'Configuración',
        resumen: 'Opciones del sistema: seguridad, numeración, operativa, impresoras, canales de venta y actualizaciones.',
        pasos: ['Guardá los cambios con el botón de abajo del formulario.', 'Canales de venta: la tarjeta muestra los más activos; "Ver canales" abre todos.', 'Cada canal tiene "Configuración" y "Eventos" (sus últimos pedidos y avisos).', 'Eventos del sistema: los últimos 6 registros de la auditoría, con ERROR o APROBADO.'],
        ancla: 'configuracion',
    },
    'section-soporte': {
        titulo: 'Soporte',
        resumen: 'Cómo pedir ayuda a JZ Tech Solutions y qué conviene incluir en el mensaje.',
        pasos: ['Contá qué querías hacer y qué pasó, con una captura.', 'Nunca mandes contraseñas ni claves de canal.'],
        ancla: 'soporte',
    },
};

function seccionActualAyuda() {
    const activa = document.querySelector('.view-section.active');
    if (!activa) return null;
    if (activa.id.startsWith('section-rep-')) {
        return { titulo: 'Reportes', resumen: 'Listados con filtros y exportación a CSV.', pasos: ['Elegí los filtros y generá el reporte.', 'Exportá a CSV para abrirlo en Excel.'], ancla: 'reportes' };
    }
    return AYUDA_SECCIONES[activa.id] || null;
}

function actualizarPanelAyuda() {
    const panel = document.getElementById('ayuda-panel');
    if (!panel) return;
    const activo = document.body.classList.contains('help-mode-active');
    panel.hidden = !activo;
    if (!activo) return;
    const ayuda = seccionActualAyuda() || { titulo: 'Ayuda', resumen: 'Pasá el mouse (o tocá) los elementos marcados para ver su explicación.', pasos: [], ancla: 'inicio' };
    document.getElementById('ayuda-panel-titulo').textContent = ayuda.titulo;
    document.getElementById('ayuda-panel-resumen').textContent = ayuda.resumen;
    const lista = document.getElementById('ayuda-panel-pasos');
    lista.replaceChildren(...ayuda.pasos.map(t => { const li = document.createElement('li'); li.textContent = t; return li; }));
    lista.hidden = ayuda.pasos.length === 0;
    document.getElementById('ayuda-panel-enlace').href = '/instructivo.html#' + ayuda.ancla;
    const marcados = document.querySelectorAll('.view-section.active [data-help]').length;
    document.getElementById('ayuda-panel-marcados').textContent = marcados
        ? `En esta pantalla hay ${marcados} ${marcados === 1 ? 'elemento marcado' : 'elementos marcados'} con ayuda: pasá el mouse o tocalos.`
        : '';
}

function cerrarAyuda() {
    if (document.body.classList.contains('help-mode-active') && typeof toggleHelpMode === 'function') toggleHelpMode();
}

document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && document.body.classList.contains('help-mode-active')) cerrarAyuda();
});

// Pantallas tactiles: no hay "pasar el mouse". Con el modo ayuda activo, tocar un elemento marcado muestra
// su explicacion (y no ejecuta su accion); tocar afuera la oculta.
document.addEventListener('click', (e) => {
    if (!document.body.classList.contains('help-mode-active')) return;
    if (!window.matchMedia || !window.matchMedia('(hover: none)').matches) return;
    if (e.target.closest('#ayuda-panel') || e.target.closest('.nav-rail')) return;
    const objetivo = e.target.closest('[data-help]');
    const popover = document.getElementById('help-popover');
    if (!objetivo) {
        if (popover) popover.style.display = 'none';
        return;
    }
    e.preventDefault();
    e.stopPropagation();
    objetivo.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }));
}, true);
