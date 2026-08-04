// === MÓDULO DE REPORTES Y EXPORTACIÓN (TRACKER360) ===

let lastStockReportData = [];
let lastStockReportFilters = {};

let lastKardexReportData = [];
let lastKardexReportFilters = {};

let lastOrdersReportData = [];
let lastOrdersReportFilters = {};

let lastRemitosReportData = [];
let lastRemitosReportFilters = {};

let lastInvoicesReportData = [];
let lastInvoicesReportFilters = {};

let lastPOReportData = [];
let lastPOReportFilters = {};

function generateTimestampString() {
    const now = new Date();
    const pad = (n) => String(n).padStart(2, '0');
    const yyyy = now.getFullYear();
    const mm = pad(now.getMonth() + 1);
    const dd = pad(now.getDate());
    const hh = pad(now.getHours());
    const min = pad(now.getMinutes());
    const ss = pad(now.getSeconds());
    const ms = String(now.getMilliseconds()).padStart(3, '0');
    return `${yyyy}-${mm}-${dd}_${hh}.${min}.${ss}.${ms}`;
}

// === 1. REPORTE DE STOCK CONSOLIDADO ===

window.loadReportStockSelectors = async function() {
    if (!cachedBranches || cachedBranches.length === 0) {
        const b = await fetchAPI('/api/admin/branches');
        if (b) cachedBranches = b;
    }
    if (!cachedSectors || cachedSectors.length === 0) {
        const s = await fetchAPI('/api/admin/sectors');
        if (s) cachedSectors = s;
    }

    const bSelect = document.getElementById('rep-stock-branch');
    if (bSelect && cachedBranches) {
        bSelect.innerHTML = '<option value="">-- Todas --</option>' + 
            cachedBranches.map(b => `<option value="${b.id}">${escapeHTML(b.name)}</option>`).join('');
    }
    window.onRepStockBranchChange();
};

window.onRepStockBranchChange = function() {
    const branchId = document.getElementById('rep-stock-branch')?.value;
    const sSelect = document.getElementById('rep-stock-sector');
    if (!sSelect || !cachedSectors) return;
    
    sSelect.innerHTML = '<option value="">-- Todos --</option>';
    cachedSectors.forEach(s => {
        if (!branchId || String(s.branch_id) === String(branchId)) {
            sSelect.innerHTML += `<option value="${s.id}">${escapeHTML(s.name)}</option>`;
        }
    });
};

window.generateStockReport = async function(e) {
    if (e) e.preventDefault();
    const tbody = document.getElementById('table-rep-stock-body');
    const btn = document.querySelector('#form-rep-stock button[type="submit"]');
    
    if (btn) { btn.disabled = true; btn.textContent = "Generando..."; }
    tbody.innerHTML = '<tr><td colspan="6" style="text-align:center; padding:2rem; font-weight:bold; color:var(--primary-blue);">Procesando Base de Datos...</td></tr>';

    const sku = document.getElementById('rep-stock-sku').value.trim();
    const branch = document.getElementById('rep-stock-branch').value;
    const branchName = branch ? document.getElementById('rep-stock-branch').options[document.getElementById('rep-stock-branch').selectedIndex].text : 'Todas';
    const sector = document.getElementById('rep-stock-sector').value;
    const sectorName = sector ? document.getElementById('rep-stock-sector').options[document.getElementById('rep-stock-sector').selectedIndex].text : 'Todos';
    const incZero = document.getElementById('rep-stock-inc-zero').checked;
    const incNeg = document.getElementById('rep-stock-inc-neg').checked;

    lastStockReportFilters = {
        "Filtro SKU": sku || "Todos",
        "Sucursal": branchName,
        "Sector": sectorName,
        "Incluir Ceros": incZero ? "SI" : "NO",
        "Incluir Negativos": incNeg ? "SI" : "NO",
        "Fecha de Emision": new Date().toLocaleString()
    };

    const params = new URLSearchParams();
    if (sku) params.append('sku', sku);
    if (branch) params.append('branch_id', branch);
    if (sector) params.append('sector_id', sector);
    params.append('include_zero', incZero);
    params.append('include_negative', incNeg);

    try {
        const rows = await fetchAPI(`/api/admin/reports/stock?${params.toString()}`);
        lastStockReportData = rows || [];

        if (lastStockReportData.length === 0) {
            tbody.innerHTML = '<tr><td colspan="6" style="text-align:center; padding:2rem; color:var(--error-red); font-weight:bold;">El reporte no arrojó resultados.</td></tr>';
            return;
        }

        tbody.innerHTML = lastStockReportData.map(r => {
            const qtyColor = r.quantity > 0 ? 'var(--success-green)' : (r.quantity < 0 ? 'var(--error-red)' : 'var(--text-muted)');
            return `
                <tr style="border-bottom:1px solid #E5E7EB;">
                    <td>${escapeHTML(r.branch_name)}</td>
                    <td>${escapeHTML(r.sector_name)}</td>
                    <td style="font-family:monospace; font-weight:bold;">${escapeHTML(r.location_code)}</td>
                    <td style="color:var(--primary-blue); font-weight:bold;">${escapeHTML(r.sku)}</td>
                    <td><small>${escapeHTML(r.description)}</small></td>
                    <td style="text-align:right; font-weight:900; color:${qtyColor}; font-size:1.1rem;">${r.quantity}</td>
                </tr>
            `;
        }).join('');

    } catch (err) {
        tbody.innerHTML = '<tr><td colspan="6" style="text-align:center; padding:2rem; color:var(--error-red);">Error al generar reporte.</td></tr>';
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = "Generar Reporte"; }
    }
};

window.exportStockReportCSV = function() {
    if (!lastStockReportData || lastStockReportData.length === 0) {
        showToast("No hay datos generados para exportar. Presione 'Generar Reporte' primero.", "error");
        return;
    }

    let csvContent = "\uFEFF"; 
    csvContent += "=== REPORTE DE STOCK INVENTARIO ===\r\n";
    for (const [key, value] of Object.entries(lastStockReportFilters)) {
        csvContent += `"${key}";"${value}"\r\n`;
    }
    csvContent += "\r\n";
    csvContent += '"Sucursal";"Sector";"Ubicacion";"SKU";"Descripcion";"Cantidad";"Peso_Total_Kg";"Volumen_Total_m3"\r\n';

    lastStockReportData.forEach(r => {
        const escapeCSV = (str) => String(str || '').replace(/"/g, '""');
        csvContent += `"${escapeCSV(r.branch_name)}";"${escapeCSV(r.sector_name)}";"${escapeCSV(r.location_code)}";"${escapeCSV(r.sku)}";"${escapeCSV(r.description)}";"${r.quantity}";"${r.total_weight_kg}";"${r.total_volume_m3}"\r\n`;
    });

    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    const link = document.createElement("a");
    const url = URL.createObjectURL(blob);
    link.setAttribute("href", url);
    link.setAttribute("download", `Tracker360_ReporteStock_${generateTimestampString()}.csv`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
};

// === 2. REPORTE DE TRAZA DE ARTÍCULOS (KARDEX) ===

window.loadKardexSelectors = async function() {
    if (!cachedBranches || cachedBranches.length === 0) {
        const b = await fetchAPI('/api/admin/branches');
        if (b) cachedBranches = b;
    }
    if (!cachedSectors || cachedSectors.length === 0) {
        const s = await fetchAPI('/api/admin/sectors');
        if (s) cachedSectors = s;
    }

    const branchSelect = document.getElementById('kardex-filter-branch');
    if (branchSelect && cachedBranches) {
        branchSelect.innerHTML = '<option value="">-- Todas --</option>' + 
            cachedBranches.map(b => `<option value="${b.id}">${escapeHTML(b.name)}</option>`).join('');
    }
    
    if (typeof window.onKardexBranchChange === 'function') window.onKardexBranchChange();
    
    const dateFrom = document.getElementById('kardex-filter-date-from');
    const dateTo = document.getElementById('kardex-filter-date-to');
    if (dateFrom && dateTo && !dateFrom.value) {
        const today = new Date();
        dateTo.value = today.toISOString().split('T')[0];
        const lastMonth = new Date(today);
        lastMonth.setMonth(lastMonth.getMonth() - 1);
        dateFrom.value = lastMonth.toISOString().split('T')[0];
    }
};

window.onKardexBranchChange = function() {
    const branchId = document.getElementById('kardex-filter-branch')?.value;
    const sectorSelect = document.getElementById('kardex-filter-sector');
    
    if (!sectorSelect || !cachedSectors) return;
    
    sectorSelect.innerHTML = '<option value="">-- Todos --</option>';
    cachedSectors.forEach(s => {
        if (!branchId || String(s.branch_id) === String(branchId)) {
            sectorSelect.innerHTML += `<option value="${s.id}">${escapeHTML(s.name)}</option>`;
        }
    });
};

window.loadKardexFiltered = async function(event) {
    if (event) event.preventDefault();
    
    const tbody = document.getElementById('table-kardex-body');
    if (!tbody) return;
    
    const btn = event ? event.target.querySelector('button[type="submit"]') : null;
    if (btn) { btn.disabled = true; btn.textContent = "Generando..."; }
    
    tbody.innerHTML = '<tr><td colspan="9" style="text-align:center; padding:2rem;"><div style="color:var(--primary-blue); font-weight:bold;">Generando reporte en base de datos...</div></td></tr>';
    
    const sku = document.getElementById('kardex-filter-sku').value.trim();
    const branchId = document.getElementById('kardex-filter-branch').value;
    const branchName = branchId ? document.getElementById('kardex-filter-branch').options[document.getElementById('kardex-filter-branch').selectedIndex].text : 'Todas';
    const sectorId = document.getElementById('kardex-filter-sector').value;
    const sectorName = sectorId ? document.getElementById('kardex-filter-sector').options[document.getElementById('kardex-filter-sector').selectedIndex].text : 'Todos';
    const locCode = document.getElementById('kardex-filter-location').value.trim();
    const dateFrom = document.getElementById('kardex-filter-date-from').value;
    const dateTo = document.getElementById('kardex-filter-date-to').value;
    const timeFrom = document.getElementById('kardex-filter-time-from').value;
    const timeTo = document.getElementById('kardex-filter-time-to').value;
    const mType = document.getElementById('kardex-filter-type').value;
    const mTypeName = mType ? document.getElementById('kardex-filter-type').options[document.getElementById('kardex-filter-type').selectedIndex].text : 'Todas las Operaciones';

    lastKardexReportFilters = {
        "Filtro SKU": sku || "Todos",
        "Sucursal": branchName,
        "Sector": sectorName,
        "Ubicacion Fisica": locCode || "Todas",
        "Fecha Desde": `${dateFrom} ${timeFrom || '00:00'}`,
        "Fecha Hasta": `${dateTo} ${timeTo || '23:59'}`,
        "Tipo de Operacion": mTypeName,
        "Fecha de Emision": new Date().toLocaleString()
    };

    const params = new URLSearchParams();
    if (sku) params.append('sku', sku);
    if (branchId) params.append('branch_id', branchId);
    if (sectorId) params.append('sector_id', sectorId);
    if (locCode) params.append('location_code', locCode);
    if (dateFrom) params.append('date_from', dateFrom);
    if (timeFrom) params.append('time_from', timeFrom);
    if (dateTo) params.append('date_to', dateTo);
    if (timeTo) params.append('time_to', timeTo);
    if (mType) params.append('movement_type', mType);

    try {
        const rows = await fetchAPI(`/api/admin/stock/kardex?${params.toString()}`);
        lastKardexReportData = rows || [];
        
        if (lastKardexReportData.length === 0) {
            tbody.innerHTML = '<tr><td colspan="9" style="text-align:center; color:var(--error-red); padding:2rem; font-weight:bold;">No se encontraron movimientos para los filtros seleccionados.</td></tr>';
        } else {
            tbody.innerHTML = lastKardexReportData.map(r => `
                <tr>
                    <td><small class="text-muted" style="font-weight:600;">${new Date(r.created_at).toLocaleString()}</small></td>
                    <td class="fw-bold" style="color:var(--primary-blue); font-weight:bold;"><code>${escapeHTML(r.sku)}</code></td>
                    <td><small>${escapeHTML(r.description)}</small></td>
                    <td><span class="badge ${r.quantity > 0 ? 'badge-info' : 'badge-warning'}">${escapeHTML(r.movement_type)}</span></td>
                    <td><small>${escapeHTML(r.branch_name || '')} > ${escapeHTML(r.sector_name || '')}</small></td>
                    <td style="font-weight:bold;">${escapeHTML(r.location_code || '-')}</td>
                    <td style="font-weight:bold; color:var(--success-green); font-size:1rem;">${r.quantity > 0 ? '+' + r.quantity : ''}</td>
                    <td style="font-weight:bold; color:var(--error-red); font-size:1rem;">${r.quantity < 0 ? r.quantity : ''}</td>
                    <td><small>${escapeHTML(r.username)}</small></td>
                </tr>
            `).join('');
        }
    } catch (e) {
        tbody.innerHTML = '<tr><td colspan="9" style="text-align:center; color:var(--error-red); padding:2rem;">Fallo de conexión o error al generar el reporte de traza.</td></tr>';
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = "Generar Reporte de Traza"; }
    }
};

window.exportKardexReportCSV = function() {
    if (!lastKardexReportData || lastKardexReportData.length === 0) {
        showToast("No hay datos generados para exportar. Presione 'Generar Reporte de Traza' primero.", "error");
        return;
    }

    let csvContent = "\uFEFF"; 
    csvContent += "=== REPORTE DE TRAZA DE ARTICULOS (KARDEX) ===\r\n";
    for (const [key, value] of Object.entries(lastKardexReportFilters)) {
        csvContent += `"${key}";"${value}"\r\n`;
    }
    csvContent += "\r\n";

    csvContent += '"Fecha_Hora";"SKU";"Descripcion";"Operacion";"Sucursal";"Sector";"Ubicacion";"Ingreso";"Salida";"Usuario"\r\n';

    lastKardexReportData.forEach(r => {
        const escapeCSV = (str) => String(str || '').replace(/"/g, '""');
        const fecha = new Date(r.created_at).toLocaleString();
        const ingreso = r.quantity > 0 ? r.quantity : '';
        const salida = r.quantity < 0 ? r.quantity : '';
        
        csvContent += `"${escapeCSV(fecha)}";"${escapeCSV(r.sku)}";"${escapeCSV(r.description)}";"${escapeCSV(r.movement_type)}";"${escapeCSV(r.branch_name)}";"${escapeCSV(r.sector_name)}";"${escapeCSV(r.location_code)}";"${ingreso}";"${salida}";"${escapeCSV(r.username)}"\r\n`;
    });

    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    const link = document.createElement("a");
    const url = URL.createObjectURL(blob);
    link.setAttribute("href", url);
    link.setAttribute("download", `Tracker360_TrazaArticulos_${generateTimestampString()}.csv`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
};

// === 3. REPORTE DE PEDIDOS DE VENTA (SALIDAS) ===

window.generateOrdersReport = async function(e) {
    if (e) e.preventDefault();
    const tbody = document.getElementById('table-rep-orders-body');
    const btn = document.querySelector('#form-rep-orders button[type="submit"]');
    
    if (btn) { btn.disabled = true; btn.textContent = "Generando..."; }
    tbody.innerHTML = '<tr><td colspan="6" style="text-align:center; padding:2rem; font-weight:bold; color:var(--primary-blue);">Procesando Base de Datos...</td></tr>';

    const docNum = document.getElementById('rep-order-num').value.trim();
    const client = document.getElementById('rep-order-client').value.trim();
    const sku = document.getElementById('rep-order-sku').value.trim();
    const dateFrom = document.getElementById('rep-order-date-from').value;
    const dateTo = document.getElementById('rep-order-date-to').value;
    const status = document.getElementById('rep-order-status').value;
    const statusName = status ? document.getElementById('rep-order-status').options[document.getElementById('rep-order-status').selectedIndex].text : 'Todos';
    const related = document.getElementById('rep-order-related').value;

    lastOrdersReportFilters = {
        "Numero de Pedido": docNum || "Todos",
        "Cliente": client || "Todos",
        "SKU Contenido": sku || "Todos",
        "Fecha Desde": dateFrom || "Sin limite",
        "Fecha Hasta": dateTo || "Sin limite",
        "Estado del Pedido": statusName,
        "Filtro de Vinculacion": related === 'ONLY_RELATED' ? "Solo documentos vinculados" : "Mostrar todos",
        "Fecha de Emision": new Date().toLocaleString()
    };

    const params = new URLSearchParams();
    if (docNum) params.append('document_number', docNum);
    if (client) params.append('customer', client);
    if (sku) params.append('sku', sku);
    if (dateFrom) params.append('date_from', dateFrom);
    if (dateTo) params.append('date_to', dateTo);
    if (status) params.append('status', status);
    if (related === 'ONLY_RELATED') params.append('related_only', "true");

    try {
        const rows = await fetchAPI(`/api/admin/reports/orders?${params.toString()}`);
        lastOrdersReportData = rows || [];

        if (lastOrdersReportData.length === 0) {
            tbody.innerHTML = '<tr><td colspan="6" style="text-align:center; padding:2rem; color:var(--error-red); font-weight:bold;">El reporte no arrojó resultados.</td></tr>';
            return;
        }

        tbody.innerHTML = lastOrdersReportData.map(r => {
            return `
                <tr style="border-bottom:1px solid #E5E7EB;">
                    <td style="font-weight:bold; color:var(--primary-blue);">${escapeHTML(r.document_number)}</td>
                    <td><small class="text-muted" style="font-weight:600;">${new Date(r.created_at).toLocaleDateString()}</small></td>
                    <td>${escapeHTML(r.customer_name)} <br><small class="text-muted">(${escapeHTML(r.customer_tax_id)})</small></td>
                    <td><span class="badge ${r.status === 'COMPLETED' ? 'badge-success' : 'badge-warning'}">${escapeHTML(r.status)}</span></td>
                    <td style="font-weight:bold;">${r.progress_pct}%</td>
                    <td><span class="badge badge-neutral">${escapeHTML(r.related_document)}</span></td>
                </tr>
            `;
        }).join('');

    } catch (err) {
        tbody.innerHTML = '<tr><td colspan="6" style="text-align:center; padding:2rem; color:var(--error-red);">Error al generar reporte.</td></tr>';
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = "Generar Reporte"; }
    }
};

window.exportOrdersReportCSV = function() {
    if (!lastOrdersReportData || lastOrdersReportData.length === 0) {
        showToast("No hay datos generados para exportar. Presione 'Generar Reporte' primero.", "error");
        return;
    }

    let csvContent = "\uFEFF"; 
    csvContent += "=== REPORTE DE PEDIDOS DE VENTA ===\r\n";
    for (const [key, value] of Object.entries(lastOrdersReportFilters)) {
        csvContent += `"${key}";"${value}"\r\n`;
    }
    csvContent += "\r\n";
    csvContent += '"N_Pedido";"Fecha_Creacion";"Cliente";"CUIT";"Estado";"Progreso_Pct";"Vinculado_A"\r\n';

    lastOrdersReportData.forEach(r => {
        const escapeCSV = (str) => String(str || '').replace(/"/g, '""');
        const fecha = new Date(r.created_at).toLocaleString();
        
        csvContent += `"${escapeCSV(r.document_number)}";"${escapeCSV(fecha)}";"${escapeCSV(r.customer_name)}";"${escapeCSV(r.customer_tax_id)}";"${escapeCSV(r.status)}";"${r.progress_pct}%";"${escapeCSV(r.related_document)}"\r\n`;
    });

    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    const link = document.createElement("a");
    const url = URL.createObjectURL(blob);
    link.setAttribute("href", url);
    link.setAttribute("download", `Tracker360_ReportePedidos_${generateTimestampString()}.csv`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
};

// === 4. REPORTE DE REMITOS DE COMPRA (INGRESOS) ===

window.loadReportRemitosSelectors = async function() {
    if (!suppliersCache || suppliersCache.length === 0) {
        const ents = await fetchAPI('/api/admin/entities');
        if (ents) suppliersCache = ents.filter(e => e.is_supplier);
    }
    if (!cachedBranches || cachedBranches.length === 0) {
        const b = await fetchAPI('/api/admin/branches');
        if (b) cachedBranches = b;
    }

    const supSelect = document.getElementById('rep-remito-supplier');
    if (supSelect && suppliersCache) {
        supSelect.innerHTML = '<option value="">-- Todos los Proveedores --</option>' + 
            suppliersCache.map(s => `<option value="${s.id}">${escapeHTML(s.company_name)}</option>`).join('');
    }

    const bSelect = document.getElementById('rep-remito-branch');
    if (bSelect && cachedBranches) {
        bSelect.innerHTML = '<option value="">-- Todas las Sucursales --</option>' + 
            cachedBranches.map(b => `<option value="${b.id}">${escapeHTML(b.name)}</option>`).join('');
    }
};

window.generateRemitosReport = async function(e) {
    if (e) e.preventDefault();
    const tbody = document.getElementById('table-rep-remitos-body');
    const btn = document.querySelector('#form-rep-remitos button[type="submit"]');
    
    if (btn) { btn.disabled = true; btn.textContent = "Generando..."; }
    tbody.innerHTML = '<tr><td colspan="5" style="text-align:center; padding:2rem; font-weight:bold; color:var(--primary-blue);">Procesando Base de Datos...</td></tr>';

    const docNum = document.getElementById('rep-remito-num').value.trim();
    const supplierId = document.getElementById('rep-remito-supplier').value;
    const supplierName = supplierId ? document.getElementById('rep-remito-supplier').options[document.getElementById('rep-remito-supplier').selectedIndex].text : 'Todos';
    const sku = document.getElementById('rep-remito-sku').value.trim();
    const dateFrom = document.getElementById('rep-remito-date-from').value;
    const dateTo = document.getElementById('rep-remito-date-to').value;
    const status = document.getElementById('rep-remito-status').value;
    const statusName = status ? document.getElementById('rep-remito-status').options[document.getElementById('rep-remito-status').selectedIndex].text : 'Todos';
    const branchId = document.getElementById('rep-remito-branch').value;
    const branchName = branchId ? document.getElementById('rep-remito-branch').options[document.getElementById('rep-remito-branch').selectedIndex].text : 'Todas';

    lastRemitosReportFilters = {
        "Numero de Remito": docNum || "Todos",
        "Proveedor": supplierName,
        "Sucursal Destino": branchName,
        "SKU Contenido": sku || "Todos",
        "Fecha Desde": dateFrom || "Sin limite",
        "Fecha Hasta": dateTo || "Sin limite",
        "Estado de Recepcion": statusName,
        "Fecha de Emision": new Date().toLocaleString()
    };

    const params = new URLSearchParams();
    if (docNum) params.append('remito_number', docNum);
    if (supplierId) params.append('supplier_id', supplierId);
    if (sku) params.append('sku', sku);
    if (dateFrom) params.append('date_from', dateFrom);
    if (dateTo) params.append('date_to', dateTo);
    if (status) params.append('status', status);
    if (branchId) params.append('branch_id', branchId);

    try {
        const rows = await fetchAPI(`/api/admin/reports/remitos?${params.toString()}`);
        lastRemitosReportData = rows || [];

        if (lastRemitosReportData.length === 0) {
            tbody.innerHTML = '<tr><td colspan="5" style="text-align:center; padding:2rem; color:var(--error-red); font-weight:bold;">El reporte no arrojó resultados.</td></tr>';
            return;
        }

        tbody.innerHTML = lastRemitosReportData.map(r => {
            return `
                <tr style="border-bottom:1px solid #E5E7EB;">
                    <td style="font-weight:bold; color:var(--primary-blue);">${escapeHTML(r.remito_number)}</td>
                    <td><small class="text-muted" style="font-weight:600;">${new Date(r.created_at).toLocaleDateString()}</small></td>
                    <td>${escapeHTML(r.supplier_name)}</td>
                    <td><small>${escapeHTML(r.branch_name)} > ${escapeHTML(r.sector_name)}</small></td>
                    <td><span class="badge ${r.status === 'COMPLETED' ? 'badge-success' : 'badge-warning'}">${escapeHTML(r.status)}</span> <span style="font-weight:bold; margin-left:8px;">${r.progress_pct}%</span></td>
                </tr>
            `;
        }).join('');

    } catch (err) {
        tbody.innerHTML = '<tr><td colspan="5" style="text-align:center; padding:2rem; color:var(--error-red);">Error al generar reporte.</td></tr>';
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = "Generar Reporte"; }
    }
};

window.exportRemitosReportCSV = function() {
    if (!lastRemitosReportData || lastRemitosReportData.length === 0) {
        showToast("No hay datos generados para exportar. Presione 'Generar Reporte' primero.", "error");
        return;
    }

    let csvContent = "\uFEFF"; 
    csvContent += "=== REPORTE DE REMITOS DE COMPRA ===\r\n";
    for (const [key, value] of Object.entries(lastRemitosReportFilters)) {
        csvContent += `"${key}";"${value}"\r\n`;
    }
    csvContent += "\r\n";
    csvContent += '"N_Remito";"Fecha_Ingreso";"Proveedor";"CUIT_Proveedor";"Sucursal";"Sector";"Estado";"Progreso_Pct"\r\n';

    lastRemitosReportData.forEach(r => {
        const escapeCSV = (str) => String(str || '').replace(/"/g, '""');
        const fecha = new Date(r.created_at).toLocaleString();
        
        csvContent += `"${escapeCSV(r.remito_number)}";"${escapeCSV(fecha)}";"${escapeCSV(r.supplier_name)}";"${escapeCSV(r.supplier_tax_id)}";"${escapeCSV(r.branch_name)}";"${escapeCSV(r.sector_name)}";"${escapeCSV(r.status)}";"${r.progress_pct}%"\r\n`;
    });

    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    const link = document.createElement("a");
    const url = URL.createObjectURL(blob);
    link.setAttribute("href", url);
    link.setAttribute("download", `Tracker360_ReporteRemitos_${generateTimestampString()}.csv`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
};

// === 5. REPORTE DE FACTURAS DE COMPRA ===

window.loadReportInvoicesSelectors = async function() {
    if (!suppliersCache || suppliersCache.length === 0) {
        const ents = await fetchAPI('/api/admin/entities');
        if (ents) suppliersCache = ents.filter(e => e.is_supplier);
    }

    const supSelect = document.getElementById('rep-invoice-supplier');
    if (supSelect && suppliersCache) {
        supSelect.innerHTML = '<option value="">-- Todos los Proveedores --</option>' + 
            suppliersCache.map(s => `<option value="${s.id}">${escapeHTML(s.company_name)}</option>`).join('');
    }
};

window.generateInvoicesReport = async function(e) {
    if (e) e.preventDefault();
    const tbody = document.getElementById('table-rep-invoices-body');
    const btn = document.querySelector('#form-rep-invoices button[type="submit"]');
    
    if (btn) { btn.disabled = true; btn.textContent = "Generando..."; }
    tbody.innerHTML = '<tr><td colspan="5" style="text-align:center; padding:2rem; font-weight:bold; color:var(--primary-blue);">Procesando Base de Datos...</td></tr>';

    const invNum = document.getElementById('rep-invoice-num').value.trim();
    const supplierId = document.getElementById('rep-invoice-supplier').value;
    const supplierName = supplierId ? document.getElementById('rep-invoice-supplier').options[document.getElementById('rep-invoice-supplier').selectedIndex].text : 'Todos';
    const dateFrom = document.getElementById('rep-invoice-date-from').value;
    const dateTo = document.getElementById('rep-invoice-date-to').value;
    const invType = document.getElementById('rep-invoice-type').value;
    const invTypeName = invType ? document.getElementById('rep-invoice-type').options[document.getElementById('rep-invoice-type').selectedIndex].text : 'Todos';

    lastInvoicesReportFilters = {
        "Numero de Factura": invNum || "Todos",
        "Proveedor": supplierName,
        "Tipo de Comprobante": invTypeName,
        "Fecha Desde": dateFrom || "Sin limite",
        "Fecha Hasta": dateTo || "Sin limite",
        "Fecha de Emision": new Date().toLocaleString()
    };

    const params = new URLSearchParams();
    if (invNum) params.append('invoice_number', invNum);
    if (supplierId) params.append('supplier_id', supplierId);
    if (invType) params.append('invoice_type', invType);
    if (dateFrom) params.append('date_from', dateFrom);
    if (dateTo) params.append('date_to', dateTo);

    try {
        const rows = await fetchAPI(`/api/admin/reports/invoices?${params.toString()}`);
        lastInvoicesReportData = rows || [];

        if (lastInvoicesReportData.length === 0) {
            tbody.innerHTML = '<tr><td colspan="5" style="text-align:center; padding:2rem; color:var(--error-red); font-weight:bold;">El reporte no arrojó resultados.</td></tr>';
            return;
        }

        tbody.innerHTML = lastInvoicesReportData.map(r => {
            return `
                <tr style="border-bottom:1px solid #E5E7EB;">
                    <td style="font-weight:bold; color:var(--primary-blue);">${escapeHTML(r.invoice_number)}</td>
                    <td><small class="text-muted" style="font-weight:600;">${new Date(r.created_at).toLocaleDateString()}</small></td>
                    <td>${escapeHTML(r.supplier_name)}</td>
                    <td style="font-family:monospace;">${escapeHTML(r.supplier_tax_id)}</td>
                    <td><span class="badge badge-neutral">TIPO ${escapeHTML(r.invoice_type)}</span></td>
                </tr>
            `;
        }).join('');

    } catch (err) {
        tbody.innerHTML = '<tr><td colspan="5" style="text-align:center; padding:2rem; color:var(--error-red);">Error al generar reporte.</td></tr>';
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = "Generar Reporte"; }
    }
};

window.exportInvoicesReportCSV = function() {
    if (!lastInvoicesReportData || lastInvoicesReportData.length === 0) {
        showToast("No hay datos generados para exportar. Presione 'Generar Reporte' primero.", "error");
        return;
    }

    let csvContent = "\uFEFF"; 
    csvContent += "=== REPORTE DE FACTURAS DE COMPRA ===\r\n";
    for (const [key, value] of Object.entries(lastInvoicesReportFilters)) {
        csvContent += `"${key}";"${value}"\r\n`;
    }
    csvContent += "\r\n";
    csvContent += '"N_Factura";"Fecha_Emision";"Proveedor";"CUIT_Proveedor";"Tipo_Comprobante"\r\n';

    lastInvoicesReportData.forEach(r => {
        const escapeCSV = (str) => String(str || '').replace(/"/g, '""');
        const fecha = new Date(r.created_at).toLocaleString();
        
        csvContent += `"${escapeCSV(r.invoice_number)}";"${escapeCSV(fecha)}";"${escapeCSV(r.supplier_name)}";"${escapeCSV(r.supplier_tax_id)}";"${escapeCSV(r.invoice_type)}"\r\n`;
    });

    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    const link = document.createElement("a");
    const url = URL.createObjectURL(blob);
    link.setAttribute("href", url);
    link.setAttribute("download", `Tracker360_ReporteFacturas_${generateTimestampString()}.csv`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
};

// === 6. REPORTE DE ÓRDENES DE COMPRA ===

window.loadReportPOSelectors = async function() {
    if (!suppliersCache || suppliersCache.length === 0) {
        const ents = await fetchAPI('/api/admin/entities');
        if (ents) suppliersCache = ents.filter(e => e.is_supplier);
    }

    const supSelect = document.getElementById('rep-po-supplier');
    if (supSelect && suppliersCache) {
        supSelect.innerHTML = '<option value="">-- Todos los Proveedores --</option>' + 
            suppliersCache.map(s => `<option value="${s.id}">${escapeHTML(s.company_name)}</option>`).join('');
    }
};

window.generatePOReport = async function(e) {
    if (e) e.preventDefault();
    const tbody = document.getElementById('table-rep-po-body');
    const btn = document.querySelector('#form-rep-po button[type="submit"]');
    
    if (btn) { btn.disabled = true; btn.textContent = "Generando..."; }
    tbody.innerHTML = '<tr><td colspan="5" style="text-align:center; padding:2rem; font-weight:bold; color:var(--primary-blue);">Procesando Base de Datos...</td></tr>';

    const orderNum = document.getElementById('rep-po-num').value.trim();
    const supplierId = document.getElementById('rep-po-supplier').value;
    const supplierName = supplierId ? document.getElementById('rep-po-supplier').options[document.getElementById('rep-po-supplier').selectedIndex].text : 'Todos';
    const sku = document.getElementById('rep-po-sku').value.trim();
    const dateFrom = document.getElementById('rep-po-date-from').value;
    const dateTo = document.getElementById('rep-po-date-to').value;
    const status = document.getElementById('rep-po-status').value;
    const statusName = status ? document.getElementById('rep-po-status').options[document.getElementById('rep-po-status').selectedIndex].text : 'Todos';

    lastPOReportFilters = {
        "Numero de Orden": orderNum || "Todos",
        "Proveedor": supplierName,
        "SKU Contenido": sku || "Todos",
        "Fecha Desde": dateFrom || "Sin limite",
        "Fecha Hasta": dateTo || "Sin limite",
        "Estado de Orden": statusName,
        "Fecha de Emision": new Date().toLocaleString()
    };

    const params = new URLSearchParams();
    if (orderNum) params.append('order_number', orderNum);
    if (supplierId) params.append('supplier_id', supplierId);
    if (sku) params.append('sku', sku);
    if (dateFrom) params.append('date_from', dateFrom);
    if (dateTo) params.append('date_to', dateTo);
    if (status) params.append('status', status);

    try {
        const rows = await fetchAPI(`/api/admin/reports/purchase-orders?${params.toString()}`);
        lastPOReportData = rows || [];

        if (lastPOReportData.length === 0) {
            tbody.innerHTML = '<tr><td colspan="5" style="text-align:center; padding:2rem; color:var(--error-red); font-weight:bold;">El reporte no arrojó resultados.</td></tr>';
            return;
        }

        tbody.innerHTML = lastPOReportData.map(r => {
            return `
                <tr style="border-bottom:1px solid #E5E7EB;">
                    <td style="font-weight:bold; color:var(--primary-blue);">${escapeHTML(r.order_number)}</td>
                    <td><small class="text-muted" style="font-weight:600;">${new Date(r.created_at).toLocaleDateString()}</small></td>
                    <td>${escapeHTML(r.supplier_name)}</td>
                    <td><span class="badge ${r.status === 'COMPLETED' ? 'badge-success' : 'badge-warning'}">${escapeHTML(r.status)}</span></td>
                    <td style="font-weight:bold;">${r.total_units} un. en ${r.total_skus} SKUs</td>
                </tr>
            `;
        }).join('');

    } catch (err) {
        tbody.innerHTML = '<tr><td colspan="5" style="text-align:center; padding:2rem; color:var(--error-red);">Error al generar reporte.</td></tr>';
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = "Generar Reporte"; }
    }
};

window.exportPOReportCSV = function() {
    if (!lastPOReportData || lastPOReportData.length === 0) {
        showToast("No hay datos generados para exportar. Presione 'Generar Reporte' primero.", "error");
        return;
    }

    let csvContent = "\uFEFF"; 
    csvContent += "=== REPORTE DE ORDENES DE COMPRA ===\r\n";
    for (const [key, value] of Object.entries(lastPOReportFilters)) {
        csvContent += `"${key}";"${value}"\r\n`;
    }
    csvContent += "\r\n";
    csvContent += '"N_Orden";"Fecha_Emision";"Proveedor";"CUIT_Proveedor";"Estado";"Total_SKUs";"Total_Unidades"\r\n';

    lastPOReportData.forEach(r => {
        const escapeCSV = (str) => String(str || '').replace(/"/g, '""');
        const fecha = new Date(r.created_at).toLocaleString();
        
        csvContent += `"${escapeCSV(r.order_number)}";"${escapeCSV(fecha)}";"${escapeCSV(r.supplier_name)}";"${escapeCSV(r.supplier_tax_id)}";"${escapeCSV(r.status)}";"${r.total_skus}";"${r.total_units}"\r\n`;
    });

    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    const link = document.createElement("a");
    const url = URL.createObjectURL(blob);
    link.setAttribute("href", url);
    link.setAttribute("download", `Tracker360_ReporteOrdenesCompra_${generateTimestampString()}.csv`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
};


// Inicializador modificado para inyectar selectores correctamente en todos los reportes
const originalSwitchView = window.switchView;
window.switchView = function(secId, btnElement = null) {
    originalSwitchView(secId, btnElement);
    if(secId === 'section-rep-stock') { window.loadReportStockSelectors(); }
    if(secId === 'section-kardex') { window.loadKardexSelectors(); }
    if(secId === 'section-rep-remitos') { window.loadReportRemitosSelectors(); }
    if(secId === 'section-rep-invoices') { window.loadReportInvoicesSelectors(); }
    if(secId === 'section-rep-po') { window.loadReportPOSelectors(); }
};