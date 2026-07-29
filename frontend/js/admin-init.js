// === INICIALIZACIÓN DEL PANEL DE ADMINISTRACIÓN ===

document.addEventListener('DOMContentLoaded', () => {
    if (typeof loadDashboardSummary === 'function') loadDashboardSummary();
    if (typeof loadUsers === 'function') loadUsers();
    if (typeof loadEntities === 'function') loadEntities();
    if (typeof loadItems === 'function') loadItems(1);
    if (typeof loadBranches === 'function') loadBranches();
    if (typeof loadSectors === 'function') loadSectors();
    if (typeof loadLocations === 'function') loadLocations();
    if (typeof loadAdminStock === 'function') loadAdminStock();
    if (typeof loadAdminKardex === 'function') loadAdminKardex();
    if (typeof loadSettings === 'function') loadSettings();
    if (typeof loadIntegrations === 'function') loadIntegrations();
});
