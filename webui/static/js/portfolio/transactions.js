import { $ } from '../shared/dom.js';
import { showStatus } from '../shared/status.js';
import { fmt, fmtQty, fmtMoney, fmtDate, todayLocalISO } from '../shared/format.js';
import { api } from './api.js';
import { fetchInstrumentsList } from './instruments.js';
import { loadHoldings } from './holdings.js';
import { createSearchBar, applySearchState, wireGroupToggles, tableBodyRowsHtml } from '../shared/search-bar.js';
import { mountColumnToggle, headerCellHtml, updateSortIndicators, wireColumnDrag } from '../shared/table-chrome.js';
import { benchmarkPeriodControlsHtml, wireBenchmarkPeriodControls } from '../charts/benchmark-comparison-chart.js';
import { createSorter, wireSortableHeaders } from '../shared/sortable-table.js';
import { createColumnManager } from '../shared/column-manager.js';
import { rowActionsHtml, wireRowActions } from '../shared/row-actions.js';
import { openModal } from '../shared/modal.js';

let instrumentById = new Map();
// export: proposals.js necesita resolver symbol->instrument_id para decidir
// si una propuesta es "accionable" (ver proposals.js::isActionable) -- let
// (no const) para que la reasignación en populateInstrumentSelect() se vea
// reflejada en el import (ES modules exportan bindings en vivo).
export let instrumentBySymbol = new Map();
let currentTransactions = [];
let currentRentas = [];
let txSearchState = { query: '', chips: [], activeFilters: new Set(), groupKey: null };
let rentaSearchState = { query: '', chips: [], activeFilters: new Set(), groupKey: null };
let txDateFrom = null;
let txDateTo = null;
const txSorter = createSorter();
const rentaSorter = createSorter();
const txCollapsedGroups = new Set();
const rentaCollapsedGroups = new Set();

const TIPO_RENTA_LABEL = { dividendo: 'Dividendo', renta: 'Renta', amortizacion: 'Amortización' };

const TX_COLUMNS = [
    { key: 'fecha', label: 'Fecha', numeric: false },
    { key: 'instrumento', label: 'Instrumento', numeric: false },
    { key: 'tipo', label: 'Tipo', numeric: false },
    { key: 'cantidad', label: 'Cantidad', numeric: true },
    { key: 'precio', label: 'Precio', numeric: true },
    { key: 'monto', label: 'Monto', numeric: true },
    { key: 'comision', label: 'Comisión', numeric: true, term: 'comision_tx' },
    { key: 'moneda', label: 'Moneda', numeric: false, sortable: false },
    { key: 'notas', label: 'Notas', numeric: false, sortable: false },
];
const txColumns = createColumnManager('kronos-transacciones-columns', TX_COLUMNS);
// Notas no suele tener datos (no hay campo en el formulario todavia) -- empieza oculta.
if (!txColumns.isHidden('notas')) txColumns.toggleVisible('notas');

const RENTA_COLUMNS = [
    { key: 'fecha', label: 'Fecha', numeric: false },
    { key: 'symbol', label: 'Símbolo', numeric: false },
    { key: 'tipo', label: 'Tipo', numeric: false, term: 'renta_tipo' },
    { key: 'bruto', label: 'Bruto', numeric: true },
    { key: 'gastos', label: 'Gastos', numeric: true, term: 'renta_tipo' },
    { key: 'neto', label: 'Neto', numeric: true },
    { key: 'moneda', label: 'Moneda', numeric: false, sortable: false },
];
const rentaColumns = createColumnManager('kronos-rentas-columns', RENTA_COLUMNS);

const TX_SEARCH_CONFIG = {
    searchFields: [
        { key: 'instrumento', label: 'Instrumento', value: (tx) => instrumentById.get(tx.instrument_id)?.symbol },
        { key: 'notas', label: 'Notas', value: (tx) => tx.notas },
    ],
    favoritesKey: 'kronos-transacciones-favorites',
    filters: [
        { key: 'compra', label: 'Compra', block: 'Tipo', test: (tx) => tx.tipo === 'compra' },
        { key: 'venta', label: 'Venta', block: 'Tipo', test: (tx) => tx.tipo === 'venta' },
    ],
    groupBy: [
        { key: 'instrument', label: 'Instrumento', value: (tx) => instrumentById.get(tx.instrument_id)?.symbol || `#${tx.instrument_id}` },
        { key: 'tipo', label: 'Tipo', value: (tx) => (tx.tipo === 'compra' ? 'Compra' : 'Venta') },
        { key: 'mes', label: 'Mes', value: (tx) => tx.fecha.slice(0, 7) },
    ],
};

const RENTA_SEARCH_CONFIG = {
    searchFields: [
        { key: 'symbol', label: 'Símbolo', value: (r) => r.symbol },
    ],
    favoritesKey: 'kronos-rentas-favorites',
    filters: [
        { key: 'dividendo', label: 'Dividendo', block: 'Tipo', test: (r) => r.tipo === 'dividendo' },
        { key: 'renta', label: 'Renta', block: 'Tipo', test: (r) => r.tipo === 'renta' },
        { key: 'amortizacion', label: 'Amortización', block: 'Tipo', test: (r) => r.tipo === 'amortizacion' },
    ],
    groupBy: [
        { key: 'symbol', label: 'Símbolo', value: (r) => r.symbol },
        { key: 'mes', label: 'Mes', value: (r) => r.fecha.slice(0, 7) },
    ],
};

export async function populateInstrumentSelect() {
    const instruments = await fetchInstrumentsList();
    instrumentById = new Map(instruments.map((i) => [i.id, i]));
    instrumentBySymbol = new Map(instruments.map((i) => [i.symbol, i]));
}

function instrumentOptionsHtml(selectedId) {
    return [...instrumentById.values()]
        .map((i) => `<option value="${i.id}" ${String(i.id) === String(selectedId) ? 'selected' : ''}>${i.symbol} (${i.type})</option>`)
        .join('');
}

export function detailLinkHtml(inst, fallbackSymbol) {
    if (!inst) return fallbackSymbol;
    const params = new URLSearchParams({ market: inst.market, assetType: inst.type, instrumentId: inst.id });
    return `<a href="#detalle/${encodeURIComponent(inst.symbol)}?${params.toString()}" class="table-link">${inst.symbol}</a>`;
}

function inDateRange(fecha) {
    return (!txDateFrom || fecha >= txDateFrom) && (!txDateTo || fecha <= txDateTo);
}

// Mismos presets que el selector de período del Dashboard (benchmarkPeriodControlsHtml)
// -- "inicio" (Desde inicio) significa sin filtro, no una fecha real.
function periodToRange(period) {
    if (period === 'inicio') return { from: null, to: null };
    const today = new Date();
    const to = todayLocalISO();
    if (period === 'ytd') return { from: `${today.getFullYear()}-01-01`, to };
    const months = { '1m': 1, '3m': 3, '1a': 12 }[period] || 1;
    const from = new Date(today);
    from.setMonth(from.getMonth() - months);
    return { from: todayLocalISO(from), to };
}

export async function loadTransactionHistory() {
    const { data } = await api.transactions();
    currentTransactions = [...data].reverse();
    renderHistory();
}

export async function loadRentasDividendos() {
    const { data } = await api.rentasDividendos();
    currentRentas = data;
    $('rentas-kpi-container').innerHTML = rentasKpiHtml(currentRentas);
    renderRentasDividendos();
}

// Separado de "cobrado": la amortización es devolución de capital, no
// rendimiento -- mezclarla en el mismo número infla el "cobrado" artificialmente.
// Fase 16: convertido de .stat-strip ad-hoc a .mini-stat-grid, mismo patrón
// que el resto de la plataforma (aside "Resumen" junto a la tabla).
function rentasKpiHtml(rentas) {
    const cutoff = new Date();
    cutoff.setDate(cutoff.getDate() - 365);
    const cutoffStr = todayLocalISO(cutoff);
    const recent = rentas.filter((r) => r.fecha >= cutoffStr);
    const sum = (tipos, moneda) => recent
        .filter((r) => tipos.includes(r.tipo) && r.moneda === moneda)
        .reduce((s, r) => s + r.neto, 0);
    return `
        <div class="mini-stat"><div class="l">Cobrado (12m, ARS)</div><div class="v num">${fmtMoney(sum(['dividendo', 'renta'], 'ARS'), 'ARS')}</div></div>
        <div class="mini-stat"><div class="l">Cobrado (12m, USD)</div><div class="v num">${fmtMoney(sum(['dividendo', 'renta'], 'USD'), 'USD')}</div></div>
        <div class="mini-stat"><div class="l">Amortización (ARS)</div><div class="v num">${fmtMoney(sum(['amortizacion'], 'ARS'), 'ARS')}</div></div>
        <div class="mini-stat"><div class="l">Amortización (USD)</div><div class="v num">${fmtMoney(sum(['amortizacion'], 'USD'), 'USD')}</div></div>
    `;
}

function txSortValue(tx, key) {
    switch (key) {
        case 'fecha': return tx.fecha;
        case 'instrumento': return instrumentById.get(tx.instrument_id)?.symbol;
        case 'tipo': return tx.tipo;
        case 'cantidad': return tx.cantidad;
        case 'precio': return tx.precio;
        case 'monto': return tx.cantidad * tx.precio;
        case 'comision': return tx.comision;
        default: return null;
    }
}

function txColumnsWithAggregates(baseColumns) {
    // El subtotal de "Monto" no antepone moneda: un grupo (ej. por Mes o Tipo)
    // puede mezclar transacciones en ARS y USD, y sumarlas con un solo
    // simbolo de moneda seria enganoso -- se muestra el numero puro.
    const AGG = {
        monto: { aggregate: 'sum', value: (tx) => tx.cantidad * tx.precio, format: (n) => fmt(n, 2) },
        comision: { aggregate: 'sum', value: (tx) => tx.comision, format: (n) => fmt(n, 2) },
    };
    return baseColumns.map((c) => ({ ...c, ...(AGG[c.key] || {}) }));
}

function txRowHtml(tx, columns) {
    const inst = instrumentById.get(tx.instrument_id);
    const cells = columns.map((c) => {
        switch (c.key) {
            case 'fecha': return `<td class="cell-date">${fmtDate(tx.fecha)}</td>`;
            case 'instrumento': return `<td>${detailLinkHtml(inst, `#${tx.instrument_id}`)}</td>`;
            case 'tipo': return `<td><span class="tag ${tx.tipo === 'compra' ? 'tag-positive' : 'tag-negative'}">${tx.tipo === 'compra' ? '▲ Compra' : '▼ Venta'}</span></td>`;
            case 'cantidad': return `<td class="num">${fmtQty(tx.cantidad)}</td>`;
            case 'precio': return `<td class="num">${fmt(tx.precio)}</td>`;
            case 'monto': return `<td class="num">${fmtMoney(tx.cantidad * tx.precio, inst?.currency || 'ARS')}</td>`;
            case 'comision': return `<td class="num">${fmt(tx.comision)}</td>`;
            case 'moneda': return `<td><span class="tag tag-neutral">${inst?.currency || '—'}</span></td>`;
            case 'notas': return `<td>${tx.notas || '—'}</td>`;
            default: return '<td></td>';
        }
    });
    return `<tr>${cells.join('')}${rowActionsHtml(tx.id)}</tr>`;
}

// Fase 15: panel "Resumen" al costado -- sobre lo filtrado (período/búsqueda),
// no sobre el total crudo, mismo criterio que explorer.js.
function renderTxSummary(inRange) {
    const el = $('tx-summary');
    if (!el) return;
    const compras = inRange.filter((tx) => tx.tipo === 'compra').length;
    const ventas = inRange.filter((tx) => tx.tipo === 'venta').length;
    const ultima = inRange.length ? inRange.reduce((a, b) => (b.fecha > a.fecha ? b : a)).fecha : null;
    el.innerHTML = `
        <div class="mini-stat"><div class="l">Operaciones</div><div class="v num">${inRange.length}</div></div>
        <div class="mini-stat"><div class="l">Última</div><div class="v num">${ultima ? fmtDate(ultima) : '—'}</div></div>
        <div class="mini-stat"><div class="l">Compras</div><div class="v num">${compras}</div></div>
        <div class="mini-stat"><div class="l">Ventas</div><div class="v num">${ventas}</div></div>
    `;
}

function renderHistory() {
    const container = $('transactions-table-container');
    const columns = txColumnsWithAggregates(txColumns.visibleColumns());
    const colspan = columns.length + 1;
    const inRange = currentTransactions.filter((tx) => inDateRange(tx.fecha));
    const sorted = txSorter.key ? txSorter.apply(inRange, txSortValue) : inRange;
    const result = applySearchState(sorted, txSearchState, TX_SEARCH_CONFIG);
    const bodyRows = !currentTransactions.length
        ? `<tr><td colspan="${colspan}" class="table-empty">Sin transacciones cargadas.</td></tr>`
        : tableBodyRowsHtml(result, columns, (tx) => txRowHtml(tx, columns), { collapsedGroups: txCollapsedGroups, colspan });

    container.innerHTML = `
        <div class="table-scroll">
            <table>
                <thead><tr>${columns.map((c) => headerCellHtml(c)).join('')}<th></th></tr></thead>
                <tbody>${bodyRows}</tbody>
            </table>
        </div>
    `;
    renderTxSummary(inRange);

    const rerender = () => renderHistory();
    mountColumnToggle($('tx-columns-slot'), txColumns, rerender);
    wireColumnDrag(container.querySelector('thead tr'), txColumns, rerender);
    wireSortableHeaders(container, txSorter, rerender);
    wireGroupToggles(container, txCollapsedGroups, rerender);
    updateSortIndicators(container, txSorter);
}

function rentaSortValue(r, key) {
    switch (key) {
        case 'fecha': return r.fecha;
        case 'symbol': return r.symbol;
        case 'tipo': return r.tipo;
        case 'bruto': return r.bruto;
        case 'gastos': return r.gastos;
        case 'neto': return r.neto;
        default: return null;
    }
}

function rentaColumnsWithAggregates(baseColumns) {
    const AGG = {
        bruto: { aggregate: 'sum', value: (r) => r.bruto, format: (n) => fmt(n, 2) },
        gastos: { aggregate: 'sum', value: (r) => r.gastos, format: (n) => fmt(n, 2) },
        neto: { aggregate: 'sum', value: (r) => r.neto, format: (n) => fmt(n, 2) },
    };
    return baseColumns.map((c) => ({ ...c, ...(AGG[c.key] || {}) }));
}

function rentaRowHtml(r, columns) {
    const cells = columns.map((c) => {
        switch (c.key) {
            case 'fecha': return `<td class="cell-date">${fmtDate(r.fecha)}</td>`;
            case 'symbol': return `<td>${detailLinkHtml(instrumentBySymbol.get(r.symbol), r.symbol)}</td>`;
            case 'tipo': return `<td>${TIPO_RENTA_LABEL[r.tipo] || r.tipo}</td>`;
            case 'bruto': return `<td class="num">${fmt(r.bruto, 2)}</td>`;
            case 'gastos': return `<td class="num">${fmt(r.gastos, 2)}</td>`;
            case 'neto': return `<td class="num cell-positive">${fmt(r.neto, 2)}</td>`;
            case 'moneda': return `<td><span class="tag tag-neutral">${r.moneda}</span></td>`;
            default: return '<td></td>';
        }
    });
    return `<tr>${cells.join('')}</tr>`;
}

function renderRentasDividendos() {
    const container = $('rentas-table-container');
    const columns = rentaColumnsWithAggregates(rentaColumns.visibleColumns());
    const inRange = currentRentas.filter((r) => inDateRange(r.fecha));
    const sorted = rentaSorter.key ? rentaSorter.apply(inRange, rentaSortValue) : inRange;
    const result = applySearchState(sorted, rentaSearchState, RENTA_SEARCH_CONFIG);
    const bodyRows = !currentRentas.length
        ? `<tr><td colspan="${columns.length}" class="table-empty">Sin dividendos o rentas registrados.</td></tr>`
        : tableBodyRowsHtml(result, columns, (r) => rentaRowHtml(r, columns), { collapsedGroups: rentaCollapsedGroups });

    container.innerHTML = `
        <div class="table-scroll">
            <table>
                <thead><tr>${columns.map((c) => headerCellHtml(c)).join('')}</tr></thead>
                <tbody>${bodyRows}</tbody>
            </table>
        </div>
    `;

    const rerender = () => renderRentasDividendos();
    mountColumnToggle($('rentas-columns-slot'), rentaColumns, rerender);
    wireColumnDrag(container.querySelector('thead tr'), rentaColumns, rerender);
    wireSortableHeaders(container, rentaSorter, rerender);
    wireGroupToggles(container, rentaCollapsedGroups, rerender);
    updateSortIndicators(container, rentaSorter);
}


function txFormFieldsHtml(tx = {}) {
    const isEdit = !!tx.id;
    return `
        <div class="field-row">
            <div class="field"><label for="tx-instrument">Instrumento</label><select id="tx-instrument">${instrumentOptionsHtml(tx.instrument_id)}</select></div>
            <div class="field">
                <label for="tx-tipo">Tipo</label>
                <select id="tx-tipo">
                    <option value="compra" ${tx.tipo === 'compra' ? 'selected' : ''}>Compra</option>
                    <option value="venta" ${tx.tipo === 'venta' ? 'selected' : ''}>Venta</option>
                </select>
            </div>
            <div class="field"><label for="tx-fecha">Fecha</label><input type="date" id="tx-fecha" value="${tx.fecha || ''}"></div>
            <div class="field"><label for="tx-cantidad">Cantidad</label><input type="number" step="any" id="tx-cantidad" value="${tx.cantidad ?? ''}"></div>
            <div class="field"><label for="tx-precio">Precio</label><input type="number" step="any" id="tx-precio" value="${tx.precio ?? ''}"></div>
            <div class="field"><label for="tx-comision">Comisi&oacute;n</label><input type="number" step="any" id="tx-comision" value="${tx.comision ?? 0}"></div>
        </div>
        <p class="modal-error"></p>
        <div class="modal-footer">
            <button type="submit" class="btn btn-primary">${isEdit ? 'Guardar' : 'Agregar'}</button>
        </div>
    `;
}

export function openTransactionModal(tx = null) {
    // isEdit se define por tx?.id, no por la verdad de todo el objeto --
    // Propuestas (Fase 8) precarga este modal con {instrument_id, tipo} sin
    // id (es una transaccion NUEVA sugerida, no una edicion), y con el chequeo
    // viejo (`if (tx)`) caia en la rama de "editar" -- tanto el titulo como,
    // mas grave, onSubmit intentaba updateTransaction(undefined, ...).
    const isEdit = !!tx?.id;
    openModal({
        title: isEdit ? 'Editar transacción' : 'Nueva transacción',
        bodyHtml: txFormFieldsHtml(tx || {}),
        onMount: (form) => {
            if (!tx?.fecha) form.querySelector('#tx-fecha').value = todayLocalISO();
        },
        onSubmit: async (form) => {
            const payload = {
                instrument_id: form.querySelector('#tx-instrument').value,
                tipo: form.querySelector('#tx-tipo').value,
                fecha: form.querySelector('#tx-fecha').value,
                cantidad: form.querySelector('#tx-cantidad').value,
                precio: form.querySelector('#tx-precio').value,
                comision: form.querySelector('#tx-comision').value,
            };
            if (isEdit) {
                await api.updateTransaction(tx.id, payload);
                showStatus('status-banner', 'success', 'Transacción actualizada');
            } else {
                await api.addTransaction(payload);
                showStatus('status-banner', 'success', 'Transacción agregada');
            }
            await Promise.all([loadTransactionHistory(), loadHoldings()]);
        },
    });
}

async function deleteTransaction(id) {
    if (!confirm('¿Eliminar esta transacción? Esta acción no se puede deshacer.')) return;
    try {
        await api.deleteTransaction(id);
        showStatus('status-banner', 'success', 'Transacción eliminada');
        await Promise.all([loadTransactionHistory(), loadHoldings()]);
    } catch (error) {
        showStatus('status-banner', 'error', error.response?.data?.error || error.message);
    }
}

export function initTransactionsView() {
    createSearchBar($('transactions-search-bar'), {
        ...TX_SEARCH_CONFIG,
        searchPlaceholder: 'Buscar por instrumento o nota...',
        onChange: (state) => { txSearchState = state; renderHistory(); },
    });
    createSearchBar($('rentas-search-bar'), {
        ...RENTA_SEARCH_CONFIG,
        searchPlaceholder: 'Buscar por símbolo...',
        onChange: (state) => { rentaSearchState = state; renderRentasDividendos(); },
    });
    const periodControls = $('tx-period-controls');
    periodControls.innerHTML = benchmarkPeriodControlsHtml('inicio');
    wireBenchmarkPeriodControls(periodControls, (period) => {
        const { from, to } = periodToRange(period);
        txDateFrom = from;
        txDateTo = to;
        renderHistory();
        renderRentasDividendos();
    });
    $('new-transaction-btn').addEventListener('click', () => openTransactionModal());
    wireRowActions($('transactions-table-container'), {
        onEdit: (id) => openTransactionModal(currentTransactions.find((t) => String(t.id) === String(id))),
        onDelete: deleteTransaction,
    });
}
