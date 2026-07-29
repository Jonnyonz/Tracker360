// === INICIALIZACIÓN DEL PANEL MÓVIL / PREPARADOR ===

document.addEventListener('DOMContentLoaded', () => {
    if (typeof loadPickingMailbox === 'function') loadPickingMailbox();
    if (typeof loadPackingMailbox === 'function') loadPackingMailbox();
});
