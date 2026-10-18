import { $ } from '../shared/dom.js';
import { showStatus } from '../shared/status.js';
import { fmt, fmtPct, fmtMoney, fmtDate, todayLocalISO } from '../shared/format.js';
import { api } from './api.js';
import { infoTooltipHtml } from '../shared/info-tooltip.js';
import { createSearchBar, applySearchState, tableBodyRowsHtml, wireGroupToggles } from '../shared/search-bar.js';
import { mountColumnToggle, headerCellHtml, updateSortIndicators, wireColumnDrag } from '../shared/table-chrome.js';
import { createSorter, wireSortableHeaders } from '../shared/sortable-table.js';
import { createColumnManager } from '../shared/column-manager.js';
import { rowActionsHtml, wireRowActions } from '../shared/row-actions.js';
import { openModal } from '../shared/modal.js';

let currentPlazos = [];
let currentCauciones = [];
let pfSearchState = { query: '', chips: [], activeFilters: new Set(), groupKey: null };
// Default "Vigentes" -- ver initPlazosFijosView, que inicializa el search-bar
// con este mismo filtro activo (confirmar con createSearchBar({initialState}).
let caucionSearchState = { query: '', chips: [], activeFilters: new Set(['activa']), groupKey: null };
const pfSorter = createSorter();
const caucionSorter = createSorter();
const pfCollapsedGroups = new Set();
const caucionCollapsedGroups = new Set();

const PF_COLUMNS = [
    { key: 'banco', label: 'Banco', numeric: false },
    { key: 'tipo', label: 'Tipo', numeric: false, term: 'pf_tipo' },
    { key: 'monto', label: 'Monto', numeric: true },
    { key: 'tna', label: 'TNA', numeric: true, term: 'tna' },
    { key: 'dias', label: 'Días', numeric: true, sortable: false, term: 'dias_caucion' },
    { key: 'devengado', label: 'Devengado', numeric: true, term: 'devengado' },
    { key: 'valor_actual', label: 'Valor Actual', numeric: true },
    { key: 'estado', label: 'Estado', numeric: false, sortable: false },
];
const pfColumns = createColumnManager('kronos-plazos-fijos-columns', PF_COLUMNS);

const CAUCION_COLUMNS = [
    { key: 'boleto', label: 'Boleto', numeric: false, term: 'boleto_caucion' },
    { key: 'monto_contado', label: 'Monto', numeric: true },
    { key: 'tna', label: 'TNA', numeric: true, term: 'tna' },
    { key: 'dias', label: 'Días', numeric: true, sortable: false, term: 'dias_caucion' },
    { key: 'devengado', label: 'Devengado', numeric: true, term: 'devengado' },
    { key: 'valor_actual', label: 'Valor Actual', numeric: true },
    { key: 'fecha_vencimiento', label: 'Vencimiento', numeric: false },
    { key: 'estado', label: 'Estado', numeric: false, sortable: false },
];
const caucionColumns = createColumnManager('kronos-cauciones-columns', CAUCION_COLUMNS);

const PF_SEARCH_CONFIG = {
    searchFields: [{ key: 'banco', label: 'Banco', value: (r) => r.plazo_fijo.banco }],
    favoritesKey: 'kronos-plazos-fijos-favorites',
    filters: [
        { key: 'tradicional', label: 'Tradicional', block: 'Tipo', test: (r) => r.plazo_fijo.tipo === 'tradicional' },
        { key: 'uva', label: 'UVA', block: 'Tipo', test: (r) => r.plazo_fijo.tipo === 'uva' },
        { key: 'activo', label: 'Activo', block: 'Estado', test: (r) => !r.vencido },
        { key: 'vencido', label: 'Vencido', block: 'Estado', test: (r) => r.vencido },
    ],
    groupBy: [
        { key: 'banco', label: 'Banco', value: (r) => r.plazo_fijo.banco },
        { key: 'tipo', label: 'Tipo', value: (r) => r.plazo_fijo.tipo },
    ],
};

const CAUCION_SEARCH_CONFIG = {
    searchFields: [{ key: 'boleto', label: 'Boleto', value: (r) => r.caucion.numero_boleto }],
    favoritesKey: 'kronos-cauciones-favorites',
    filters: [
        { key: 'activa', label: 'Vigentes', block: 'Estado', test: (r) => !r.vencido },
        { key: 'vencida', label: 'Vencidas', block: 'Estado', test: (r) => r.vencido },
        { key: 'ars', label: 'ARS', block: 'Moneda', test: (r) => r.caucion.moneda === 'ARS' },
        { key: 'usd', label: 'USD', block: 'Moneda', test: (r) => r.caucion.moneda === 'USD' },
    ],
    groupBy: [
        { key: 'moneda', label: 'Moneda', value: (r) => r.caucion.moneda },
    ],
};

export async function loadPlazosFijos() {
    const { data } = await api.plazosFijos();
    currentPlazos = data;
    renderTable();
    renderPfSummary();
}

// Fase 16: panel "Resumen" al costado, mismo patrón que ya tiene Cauciones.
function renderPfSummary() {
    const el = $('pf-summary');
    if (!el) return;
    const vigentes = currentPlazos.filter((r) => !r.vencido);
    const hoy = todayLocalISO();
    const venceHoy = vigentes.filter((r) => r.plazo_fijo.fecha_vencimiento === hoy);
    // plazos_fijos no tiene columna de moneda (siempre ARS, confirmado en
    // schema.sql) -- a diferencia de cauciones, acá no hace falta resolverla.
    const montoVenceHoy = venceHoy.length
        ? venceHoy.map((r) => fmtMoney(r.valor_actual, 'ARS')).join(' + ')
        : '—';
    el.innerHTML = `
        <div class="mini-stat"><div class="l">Vigentes</div><div class="v num">${vigentes.length}</div></div>
        <div class="mini-stat"><div class="l">Vencen hoy</div><div class="v num${venceHoy.length ? ' cell-negative' : ''}">${venceHoy.length}</div></div>
        <div class="mini-stat span2"><div class="l">Monto que vence hoy</div><div class="v num${venceHoy.length ? ' cell-negative' : ''}">${montoVenceHoy}</div></div>
    `;
}

// Fase 15: panel "Resumen" al costado de la tabla.
function renderCaucionesSummary() {
    const el = $('cauciones-summary');
    if (!el) return;
    const vigentes = currentCauciones.filter((r) => !r.vencido);
    const hoy = todayLocalISO();
    const venceHoy = vigentes.filter((r) => r.caucion.fecha_vencimiento === hoy);
    const montoVenceHoy = venceHoy.length
        ? venceHoy.map((r) => fmtMoney(r.valor_actual, r.caucion.moneda)).join(' + ')
        : '—';
    el.innerHTML = `
        <div class="mini-stat"><div class="l">Vigentes</div><div class="v num">${vigentes.length}</div></div>
        <div class="mini-stat"><div class="l">Vencen hoy</div><div class="v num${venceHoy.length ? ' cell-negative' : ''}">${venceHoy.length}</div></div>
        <div class="mini-stat span2"><div class="l">Monto que vence hoy</div><div class="v num${venceHoy.length ? ' cell-negative' : ''}">${montoVenceHoy}</div></div>
    `;
}

export async function loadCauciones() {
    const { data } = await api.cauciones();
    currentCauciones = data;
    renderCaucionesTable();
    renderCaucionesSummary();
}

function pfSortValue(r, key) {
    const pf = r.plazo_fijo;
    switch (key) {
        case 'banco': return pf.banco;
        case 'tipo': return pf.tipo;
        case 'monto': return pf.monto;
        case 'tna': return pf.tna;
        case 'devengado': return r.devengado;
        case 'valor_actual': return r.valor_actual;
        default: return null;
    }
}

function pfColumnsWithAggregates(baseColumns) {
    const AGG = {
        devengado: { aggregate: 'sum', value: (r) => r.devengado, format: (n) => fmt(n, 2) },
        valor_actual: { aggregate: 'sum', value: (r) => r.valor_actual, format: (n) => fmt(n, 2) },
    };
    return baseColumns.map((c) => {
        if (c.key === 'devengado') {
            return {
                ...c, ...AGG[c.key],
                headerExtra: infoTooltipHtml('<div class="info-tooltip-formula">devengado = monto × TNA × días_transcurridos / 365</div>'),
            };
        }
        return { ...c, ...(AGG[c.key] || {}) };
    });
}

function rowHtml(r, columns) {
    const pf = r.plazo_fijo;
    const estado = r.vencido
        ? '<span class="tag tag-negative">Vencido</span>'
        : '<span class="tag tag-positive">Activo</span>';
    const cells = columns.map((c) => {
        switch (c.key) {
            case 'banco': return `<td>${pf.banco}</td>`;
            case 'tipo': return `<td>${pf.tipo}</td>`;
            case 'monto': return `<td class="num">${fmtMoney(pf.monto)}</td>`;
            case 'tna': return `<td class="num">${fmtPct(pf.tna * 100)}</td>`;
            case 'dias': return `<td class="num">${r.dias_transcurridos}/${pf.plazo_dias}</td>`;
            case 'devengado': return `<td class="num cell-positive">${fmtMoney(r.devengado)}</td>`;
            case 'valor_actual': return `<td class="num">${fmtMoney(r.valor_actual)}</td>`;
            case 'estado': return `<td>${estado}</td>`;
            default: return '<td></td>';
        }
    });
    return `<tr>${cells.join('')}${rowActionsHtml(pf.id)}</tr>`;
}

function renderTable() {
    const container = $('pf-table-container');
    const columns = pfColumnsWithAggregates(pfColumns.visibleColumns());
    const colspan = columns.length + 1;
    if (!currentPlazos.length) {
        container.innerHTML = `
            <table><tbody><tr><td colspan="${colspan}" class="table-empty">
                Todavía no cargáste ningún plazo fijo. Un plazo fijo tradicional o UVA en un banco, para ver el devengado y compararlo contra el resto de la cartera.
            </td></tr></tbody></table>`;
        const slot = $('pf-columns-slot');
        if (slot) slot.innerHTML = '';
        return;
    }
    const sorted = pfSorter.key ? pfSorter.apply(currentPlazos, pfSortValue) : currentPlazos;
    const result = applySearchState(sorted, pfSearchState, PF_SEARCH_CONFIG);
    const bodyRows = tableBodyRowsHtml(result, columns, (r) => rowHtml(r, columns), { collapsedGroups: pfCollapsedGroups, colspan });

    container.innerHTML = `
        <div class="table-scroll">
            <table>
                <thead><tr>${columns.map((c) => headerCellHtml(c)).join('')}<th></th></tr></thead>
                <tbody>${bodyRows}</tbody>
            </table>
        </div>
    `;
    const rerender = () => renderTable();
    mountColumnToggle($('pf-columns-slot'), pfColumns, rerender);
    wireColumnDrag(container.querySelector('thead tr'), pfColumns, rerender);
    wireSortableHeaders(container, pfSorter, rerender);
    wireGroupToggles(container, pfCollapsedGroups, rerender);
    updateSortIndicators(container, pfSorter);
}

function caucionSortValue(r, key) {
    const c = r.caucion;
    switch (key) {
        case 'boleto': return c.numero_boleto;
        case 'monto_contado': return c.monto_contado;
        case 'tna': return c.tasa;
        case 'devengado': return r.devengado;
        case 'valor_actual': return r.valor_actual;
        case 'fecha_vencimiento': return c.fecha_vencimiento;
        default: return null;
    }
}

function caucionColumnsWithAggregates(baseColumns) {
    const AGG = {
        devengado: { aggregate: 'sum', value: (r) => r.devengado, format: (n) => fmt(n, 2) },
        valor_actual: { aggregate: 'sum', value: (r) => r.valor_actual, format: (n) => fmt(n, 2) },
    };
    return baseColumns.map((c) => {
        if (c.key === 'devengado') {
            return {
                ...c, ...AGG[c.key],
                headerExtra: infoTooltipHtml('<div class="info-tooltip-formula">devengado = interés_bruto × días_transcurridos / plazo_días</div>'),
            };
        }
        return { ...c, ...(AGG[c.key] || {}) };
    });
}

function caucionRowHtml(r, columns) {
    const c = r.caucion;
    const estado = r.vencido
        ? '<span class="tag tag-negative">Vencida</span>'
        : '<span class="tag tag-positive">Activa</span>';
    const cells = columns.map((col) => {
        switch (col.key) {
            case 'boleto': return `<td>${c.numero_boleto || '—'}</td>`;
            case 'monto_contado': return `<td class="num">${fmtMoney(c.monto_contado, c.moneda)}</td>`;
            case 'tna': return `<td class="num">${fmtPct(c.tasa)}</td>`;
            case 'dias': return `<td class="num">${r.dias_transcurridos}/${c.plazo_dias}</td>`;
            case 'devengado': return `<td class="num cell-positive">${fmtMoney(r.devengado, c.moneda)}</td>`;
            case 'valor_actual': return `<td class="num">${fmtMoney(r.valor_actual, c.moneda)}</td>`;
            case 'fecha_vencimiento': return `<td class="cell-date">${fmtDate(c.fecha_vencimiento)}</td>`;
            case 'estado': return `<td>${estado}</td>`;
            default: return '<td></td>';
        }
    });
    return `<tr>${cells.join('')}${rowActionsHtml(c.id)}</tr>`;
}

function renderCaucionesTable() {
    const container = $('cauciones-table-container');
    const columns = caucionColumnsWithAggregates(caucionColumns.visibleColumns());
    const colspan = columns.length + 1;
    if (!currentCauciones.length) {
        container.innerHTML = `<table><tbody><tr><td colspan="${colspan}" class="table-empty">Sin cauciones registradas.</td></tr></tbody></table>`;
        const slot = $('cauciones-columns-slot');
        if (slot) slot.innerHTML = '';
        return;
    }
    const sorted = caucionSorter.key ? caucionSorter.apply(currentCauciones, caucionSortValue) : currentCauciones;
    const result = applySearchState(sorted, caucionSearchState, CAUCION_SEARCH_CONFIG);
    const bodyRows = tableBodyRowsHtml(result, columns, (r) => caucionRowHtml(r, columns), { collapsedGroups: caucionCollapsedGroups, colspan });

    container.innerHTML = `
        <div class="table-scroll">
            <table>
                <thead><tr>${columns.map((c) => headerCellHtml(c)).join('')}<th></th></tr></thead>
                <tbody>${bodyRows}</tbody>
            </table>
        </div>
    `;
    const rerender = () => renderCaucionesTable();
    mountColumnToggle($('cauciones-columns-slot'), caucionColumns, rerender);
    wireColumnDrag(container.querySelector('thead tr'), caucionColumns, rerender);
    wireSortableHeaders(container, caucionSorter, rerender);
    wireGroupToggles(container, caucionCollapsedGroups, rerender);
    updateSortIndicators(container, caucionSorter);
}

function pfFormFieldsHtml(pf = {}) {
    const isEdit = !!pf.id;
    return `
        <div class="field-row">
            <div class="field"><label for="pf-banco">Banco</label><input type="text" id="pf-banco" placeholder="Banco Naci&oacute;n" value="${pf.banco || ''}"></div>
            <div class="field">
                <label for="pf-tipo">Tipo</label>
                <select id="pf-tipo">
                    <option value="tradicional" ${pf.tipo === 'tradicional' ? 'selected' : ''}>Tradicional</option>
                    <option value="uva" ${pf.tipo === 'uva' ? 'selected' : ''}>UVA</option>
                </select>
            </div>
            <div class="field"><label for="pf-monto">Monto</label><input type="number" id="pf-monto" placeholder="500000" value="${pf.monto ?? ''}"></div>
            <div class="field"><label for="pf-tna">TNA (%)</label><input type="number" step="0.01" id="pf-tna" placeholder="40" value="${pf.tna != null ? pf.tna * 100 : ''}"></div>
            <div class="field"><label for="pf-fecha-inicio">Fecha inicio</label><input type="date" id="pf-fecha-inicio" value="${pf.fecha_inicio || ''}"></div>
            <div class="field"><label for="pf-plazo-dias">Plazo (d&iacute;as)</label><input type="number" id="pf-plazo-dias" placeholder="90" value="${pf.plazo_dias ?? ''}"></div>
        </div>
        <p class="modal-error"></p>
        <div class="modal-footer">
            <button type="submit" class="btn btn-primary">${isEdit ? 'Guardar' : 'Agregar'}</button>
        </div>
    `;
}

function openPlazoFijoModal(pf = null) {
    openModal({
        title: pf ? `Editar plazo fijo (${pf.banco})` : 'Nuevo plazo fijo',
        bodyHtml: pfFormFieldsHtml(pf || {}),
        onMount: (form) => { if (!pf) form.querySelector('#pf-fecha-inicio').value = todayLocalISO(); },
        onSubmit: async (form) => {
            const payload = {
                banco: form.querySelector('#pf-banco').value,
                tipo: form.querySelector('#pf-tipo').value,
                monto: form.querySelector('#pf-monto').value,
                // El campo pide % (ej. 40 = 40%, mismo criterio visual que
                // Cauciones) -- se guarda como fraccion porque el devengado
                // del backend ya asume esa convencion (monto * tna * dias/365).
                tna: Number(form.querySelector('#pf-tna').value) / 100,
                fecha_inicio: form.querySelector('#pf-fecha-inicio').value,
                plazo_dias: form.querySelector('#pf-plazo-dias').value,
            };
            if (pf) {
                await api.updatePlazoFijo(pf.id, payload);
                showStatus('status-banner', 'success', 'Plazo fijo actualizado');
            } else {
                await api.addPlazoFijo(payload);
                showStatus('status-banner', 'success', 'Plazo fijo agregado');
            }
            await loadPlazosFijos();
        },
    });
}

async function deletePlazoFijo(id) {
    if (!confirm('¿Eliminar este plazo fijo? Esta acción no se puede deshacer.')) return;
    try {
        await api.deletePlazoFijo(id);
        showStatus('status-banner', 'success', 'Plazo fijo eliminado');
        await loadPlazosFijos();
    } catch (error) {
        showStatus('status-banner', 'error', error.response?.data?.error || error.message);
    }
}

function caucionFormFieldsHtml(c = {}) {
    const isEdit = !!c.id;
    return `
        <div class="field-row">
            <div class="field"><label for="ca-boleto">N&uacute;mero de boleto</label><input type="text" id="ca-boleto" value="${c.numero_boleto || ''}"></div>
            <div class="field"><label for="ca-fecha-apertura">Fecha apertura</label><input type="date" id="ca-fecha-apertura" value="${c.fecha_apertura || ''}"></div>
            <div class="field"><label for="ca-fecha-vencimiento">Fecha vencimiento</label><input type="date" id="ca-fecha-vencimiento" value="${c.fecha_vencimiento || ''}"></div>
            <div class="field"><label for="ca-plazo-dias">Plazo (d&iacute;as)</label><input type="number" id="ca-plazo-dias" placeholder="7" value="${c.plazo_dias ?? ''}"></div>
            <div class="field"><label for="ca-monto-contado">Monto contado</label><input type="number" step="any" id="ca-monto-contado" value="${c.monto_contado ?? ''}"></div>
            <div class="field"><label for="ca-monto-futuro">Monto futuro</label><input type="number" step="any" id="ca-monto-futuro" value="${c.monto_futuro ?? ''}"></div>
            <div class="field"><label for="ca-tasa">TNA (%)</label><input type="number" step="any" id="ca-tasa" value="${c.tasa ?? ''}"></div>
            <div class="field"><label for="ca-interes-bruto">Inter&eacute;s bruto</label><input type="number" step="any" id="ca-interes-bruto" value="${c.interes_bruto ?? ''}"></div>
            <div class="field">
                <label for="ca-moneda">Moneda</label>
                <select id="ca-moneda">
                    <option value="ARS" ${c.moneda === 'ARS' ? 'selected' : ''}>ARS</option>
                    <option value="USD" ${c.moneda === 'USD' ? 'selected' : ''}>USD</option>
                </select>
            </div>
        </div>
        <p class="modal-error"></p>
        <div class="modal-footer">
            <button type="submit" class="btn btn-primary">${isEdit ? 'Guardar' : 'Agregar'}</button>
        </div>
    `;
}

function openCaucionModal(c = null) {
    openModal({
        title: c ? 'Editar caución' : 'Nueva caución',
        bodyHtml: caucionFormFieldsHtml(c || {}),
        onMount: (form) => { if (!c) form.querySelector('#ca-fecha-apertura').value = todayLocalISO(); },
        onSubmit: async (form) => {
            const payload = {
                numero_boleto: form.querySelector('#ca-boleto').value || null,
                fecha_apertura: form.querySelector('#ca-fecha-apertura').value,
                fecha_vencimiento: form.querySelector('#ca-fecha-vencimiento').value,
                plazo_dias: form.querySelector('#ca-plazo-dias').value,
                monto_contado: form.querySelector('#ca-monto-contado').value,
                monto_futuro: form.querySelector('#ca-monto-futuro').value,
                tasa: form.querySelector('#ca-tasa').value,
                interes_bruto: form.querySelector('#ca-interes-bruto').value,
                moneda: form.querySelector('#ca-moneda').value,
            };
            if (c) {
                await api.updateCaucion(c.id, payload);
                showStatus('status-banner', 'success', 'Caución actualizada');
            } else {
                await api.addCaucion(payload);
                showStatus('status-banner', 'success', 'Caución agregada');
            }
            await loadCauciones();
        },
    });
}

async function deleteCaucion(id) {
    if (!confirm('¿Eliminar esta caución? Esta acción no se puede deshacer.')) return;
    try {
        await api.deleteCaucion(id);
        showStatus('status-banner', 'success', 'Caución eliminada');
        await loadCauciones();
    } catch (error) {
        showStatus('status-banner', 'error', error.response?.data?.error || error.message);
    }
}

export function initPlazosFijosView() {
    createSearchBar($('pf-search-bar'), {
        ...PF_SEARCH_CONFIG,
        searchPlaceholder: 'Buscar por banco...',
        onChange: (state) => { pfSearchState = state; renderTable(); },
    });
    createSearchBar($('cauciones-search-bar'), {
        ...CAUCION_SEARCH_CONFIG,
        searchPlaceholder: 'Buscar por número de boleto...',
        initialState: caucionSearchState,
        onChange: (state) => { caucionSearchState = state; renderCaucionesTable(); },
    });
    $('new-pf-btn').addEventListener('click', () => openPlazoFijoModal());
    $('new-caucion-btn').addEventListener('click', () => openCaucionModal());
    wireRowActions($('pf-table-container'), {
        onEdit: (id) => openPlazoFijoModal(currentPlazos.find((r) => String(r.plazo_fijo.id) === String(id))?.plazo_fijo),
        onDelete: deletePlazoFijo,
    });
    wireRowActions($('cauciones-table-container'), {
        onEdit: (id) => openCaucionModal(currentCauciones.find((r) => String(r.caucion.id) === String(id))?.caucion),
        onDelete: deleteCaucion,
    });
}
