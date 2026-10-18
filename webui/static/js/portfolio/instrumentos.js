// Catálogo unificado de Instrumentos (ver conversación, limpieza de UI):
// antes "Catálogo" (instrumentos.js, con CRUD completo) y "Explorador BYMA"
// (explorer.js, búsqueda en vivo de todo el mercado) eran 2 pestañas
// distintas que mostraban en parte la misma info con interacciones
// distintas -- ahora conviven en UNA sola tabla con un filtro superior
// ([Todos] [En Cartera] [CEDEARs] [Acciones] [Bonos]), fuente combinada:
// - Catálogo real (fetchInstrumentsList, CRUD completo vía instruments.js).
// - Universo BYMA buscable (api.explorerSearch) para descubrir símbolos que
//   todavía no se agregaron -- se muestran con precio/var.% en vivo y un
//   botón "Agregar a cartera" en vez de Editar/Eliminar.
// Un símbolo que esté en LOS DOS (ya agregado) se muestra una sola vez, como
// fila de catálogo, enriquecida con el precio/var.% que trae el Explorador.
import { $ } from '../shared/dom.js';
import { fmt, fmtPct, fmtMoney } from '../shared/format.js';
import { loadingHtml } from '../shared/loading.js';
import { api } from './api.js';
import { createSearchBar, applySearchState, tableBodyRowsHtml, wireGroupToggles } from '../shared/search-bar.js';
import { headerCellHtml, updateSortIndicators } from '../shared/table-chrome.js';
import { createSorter, wireSortableHeaders } from '../shared/sortable-table.js';
import { rowActionsHtml, wireRowActions } from '../shared/row-actions.js';
import { openInstrumentModal, fetchInstrumentsList, deleteInstrument } from './instruments.js';
import { instrumentClase, claseDotHtml } from './instrument-clase.js';

const EXPLORER_TYPE_LABELS = { accion: 'Acción', cedear: 'CEDEAR', bono: 'Bono' };

const BUCKETS = [
    { key: 'todos', label: 'Todos' },
    { key: 'cartera', label: 'En Cartera' },
    { key: 'cedear', label: 'CEDEARs' },
    { key: 'accion', label: 'Acciones' },
    { key: 'bono', label: 'Bonos' },
];

// "valor" (Valorizado) lleva aggregate+value+format (ver
// shared/search-bar.js::groupRowHtml) -- es la columna que arma el subtotal
// en el encabezado de cada grupo ("Acción (2) • Total: USD 4.250,00", ver
// conversación). Solo las filas EN CARTERA tienen un valor real (holdings ya
// convertido a la moneda de visualización elegida, ver displayCurrency);
// las de descubrimiento (todavía no agregadas) no suman nada -- nunca se
// inventa un "valorizado" para algo que no se posee.
const COLUMNS = [
    { key: 'symbol', label: 'Símbolo', numeric: false },
    { key: 'name', label: 'Nombre', numeric: false },
    { key: 'clase', label: 'Clase', numeric: false, term: 'clase_taxonomia' },
    { key: 'en_cartera', label: 'En cartera', numeric: false, sortable: false },
    { key: 'price', label: 'Precio', numeric: true },
    { key: 'pct_change', label: 'Var. %', numeric: true },
    {
        key: 'valor', label: 'Valorizado', numeric: true, aggregate: 'sum',
        value: (r) => r.valorActual, format: (n) => fmtMoney(n, displayCurrency),
    },
    { key: 'cuenta', label: 'Cuenta', numeric: false },
];

const SEARCH_CONFIG = {
    searchFields: [
        { key: 'symbol', label: 'Símbolo', value: (r) => r.symbol },
        { key: 'name', label: 'Nombre', value: (r) => r.name },
    ],
    filters: [],
    groupBy: [
        { key: 'clase', label: 'Clase', value: (r) => r.clase },
        { key: 'cuenta', label: 'Cuenta', value: (r) => r.cuenta || 'Sin agregar' },
    ],
};

let catalogInstruments = [];
let holdings = [];
let explorerRows = [];
let mergedRows = [];
let activeBucket = 'todos';
let searchState = { query: '', activeFilters: new Set(), groupKey: null };
let displayCurrency = 'ARS';
let loaded = false;
// "Mostrar más" (ver conversación, Fase 3): el universo BYMA buscable ronda
// las ~1300 filas -- pintarlas todas de una, sin filtro ni agrupación activa,
// generaba un <tbody> gigante por una tabla que casi nadie scrollea entera.
// Solo aplica al caso SIN agrupar: agrupado, cada grupo ya arranca colapsado
// (ver lastRenderedGroupKey abajo), así que el problema de volumen no se
// repite ahí.
const PAGE_SIZE = 100;
let visibleCount = PAGE_SIZE;
const sorter = createSorter();
const collapsedGroups = new Set();
// Distinto del groupKey ACTUAL (searchState.groupKey) -- sirve para detectar
// cuándo se elige una agrupación nueva (o se cambia de una a otra) para
// arrancarla colapsada por defecto (ver conversación, pase de UX/UI); cambiar
// de bucket/búsqueda con el MISMO groupKey no debe re-colapsar lo que el
// usuario ya abrió a mano.
let lastRenderedGroupKey = undefined;

function findHolding(instrumentId) {
    return holdings.find((h) => h.instrument.id === instrumentId && h.cantidad > 0);
}

function isHeld(instrumentId) {
    return !!findHolding(instrumentId);
}

function buildMergedRows() {
    const catalogSymbols = new Set(catalogInstruments.map((i) => i.symbol.toUpperCase()));
    const rows = catalogInstruments.map((inst) => {
        const match = explorerRows.find((e) => e.symbol.toUpperCase() === inst.symbol.toUpperCase());
        const holding = findHolding(inst.id);
        return {
            id: inst.id,
            symbol: inst.symbol,
            name: inst.name,
            type: inst.type,
            clase: instrumentClase(inst),
            cuenta: inst.cuenta || 'NH Capital',
            market: inst.market,
            isCartera: !!holding,
            valorActual: holding ? holding.valor_actual : null,
            price: match ? match.price : null,
            pct_change: match ? match.pct_change : null,
            inCatalog: true,
            raw: inst,
        };
    });
    explorerRows.forEach((e) => {
        if (catalogSymbols.has(e.symbol.toUpperCase())) return;
        rows.push({
            id: null,
            symbol: e.symbol,
            name: null,
            type: e.type,
            clase: EXPLORER_TYPE_LABELS[e.type] || e.type,
            cuenta: null,
            market: e.market,
            isCartera: false,
            valorActual: null,
            price: e.price,
            pct_change: e.pct_change,
            inCatalog: false,
        });
    });
    mergedRows = rows;
}

function matchesBucket(row) {
    switch (activeBucket) {
        case 'cartera': return row.isCartera;
        case 'cedear': return row.type === 'cedear';
        case 'accion': return row.type === 'accion';
        case 'bono': return row.type === 'bono' || row.type === 'on';
        default: return true;
    }
}

function sortValue(row, key) {
    switch (key) {
        case 'symbol': return row.symbol;
        case 'name': return row.name || '';
        case 'clase': return row.clase;
        case 'price': return row.price;
        case 'pct_change': return row.pct_change;
        case 'cuenta': return row.cuenta || '';
        default: return null;
    }
}

function pctClass(pct) {
    if (pct === null || pct === undefined) return 'cell-muted';
    return pct > 0 ? 'cell-positive' : (pct < 0 ? 'cell-negative' : 'cell-muted');
}

function actionsCellHtml(row) {
    if (row.inCatalog) return rowActionsHtml(row.id);
    return `<td><button type="button" class="btn btn-secondary instrumentos-add-btn" data-symbol="${row.symbol}">Agregar a cartera</button></td>`;
}

function rowHtml(row) {
    const params = new URLSearchParams({ market: row.market || 'byma', assetType: row.type });
    if (row.id) params.set('instrumentId', row.id);
    const link = `<a href="#detalle/${encodeURIComponent(row.symbol)}?${params.toString()}" class="table-link">${row.symbol}</a>`;
    return `
        <tr>
            <td>${link}</td>
            <td class="cell-truncate" title="${row.name || ''}">${row.name || '—'}</td>
            <td>${claseDotHtml({ type: row.type })}${row.clase}</td>
            <td>${row.isCartera ? '✓' : ''}</td>
            <td class="num">${row.price !== null && row.price !== undefined ? fmt(row.price) : '—'}</td>
            <td class="num ${pctClass(row.pct_change)}">${row.pct_change !== null && row.pct_change !== undefined ? fmtPct(row.pct_change) : '—'}</td>
            <td class="num">${row.valorActual !== null && row.valorActual !== undefined ? fmtMoney(row.valorActual, displayCurrency) : '—'}</td>
            <td>${row.cuenta || '<span class="hint">Sin agregar</span>'}</td>
            ${actionsCellHtml(row)}
        </tr>
    `;
}

function renderSummary(items) {
    const el = $('instrumentos-summary');
    if (!el) return;
    const enCartera = items.filter((i) => i.isCartera).length;
    const sinAgregar = items.filter((i) => !i.inCatalog).length;
    const clases = new Set(items.map((i) => i.clase)).size;
    el.innerHTML = `
        <div class="mini-stat"><div class="l">Mostrados</div><div class="v num">${items.length}</div></div>
        <div class="mini-stat"><div class="l">En cartera</div><div class="v num">${enCartera}</div></div>
        <div class="mini-stat"><div class="l">Sin agregar</div><div class="v num">${sinAgregar}</div></div>
        <div class="mini-stat"><div class="l">Clases</div><div class="v num">${clases}</div></div>
    `;
}

function renderBucketBarActiveState() {
    const bar = $('instrumentos-bucket-bar');
    if (!bar) return;
    bar.querySelectorAll('.segmented-toggle-option').forEach((btn) => {
        btn.classList.toggle('active', btn.dataset.bucket === activeBucket);
    });
}

function renderTable() {
    const container = $('instrumentos-table-container');
    if (!container) return;
    const filtered = mergedRows.filter(matchesBucket);
    const sorted = sorter.key ? sorter.apply(filtered, sortValue) : filtered;
    const result = applySearchState(sorted, searchState, SEARCH_CONFIG);
    // Agrupar arranca COLAPSADO por defecto (ver conversación, pase de UX/UI)
    // -- solo al ACTIVAR una agrupación nueva (o cambiar a otra distinta),
    // nunca en cada re-render con la misma agrupación activa (eso pisaría lo
    // que el usuario ya expandió a mano con un click).
    if (result.grouped && searchState.groupKey !== lastRenderedGroupKey) {
        collapsedGroups.clear();
        result.groups.forEach((g) => collapsedGroups.add(g.label));
    }
    lastRenderedGroupKey = searchState.groupKey;

    let pageResult = result;
    let showMoreHtml = '';
    if (!result.grouped && result.items.length > visibleCount) {
        pageResult = { grouped: false, items: result.items.slice(0, visibleCount) };
        showMoreHtml = `
            <div class="table-show-more">
                <button type="button" class="btn btn-secondary" id="instrumentos-show-more-btn">Mostrar más (${pageResult.items.length} de ${result.items.length})</button>
            </div>
        `;
    }
    const bodyRows = tableBodyRowsHtml(pageResult, COLUMNS, rowHtml, { collapsedGroups, colspan: COLUMNS.length + 1, emptyMessage: 'Sin resultados para este filtro.' });
    container.innerHTML = `
        <div class="table-scroll">
            <table>
                <thead><tr>${COLUMNS.map((c) => headerCellHtml(c)).join('')}<th></th></tr></thead>
                <tbody>${bodyRows}</tbody>
            </table>
        </div>
        ${showMoreHtml}
    `;
    wireSortableHeaders(container, sorter, renderTable);
    wireGroupToggles(container, collapsedGroups, renderTable);
    updateSortIndicators(container, sorter);
    renderSummary(filtered);
    $('instrumentos-show-more-btn')?.addEventListener('click', () => {
        visibleCount += PAGE_SIZE;
        renderTable();
    });
}

async function loadAll() {
    const container = $('instrumentos-table-container');
    if (container && !loaded) container.innerHTML = loadingHtml('Cargando instrumentos…');
    const [catalog, holdingsResp, explorerResp, settingsResp] = await Promise.all([
        fetchInstrumentsList(),
        api.holdings(),
        api.explorerSearch(''),
        api.settings(),
    ]);
    catalogInstruments = catalog;
    holdings = holdingsResp.data;
    explorerRows = explorerResp.data;
    displayCurrency = settingsResp.data.display_currency;
    loaded = true;
    buildMergedRows();
    renderTable();
}

export async function loadInstrumentos() {
    await loadAll();
}

export function initInstrumentosView() {
    createSearchBar($('instrumentos-search-bar'), {
        ...SEARCH_CONFIG,
        searchPlaceholder: 'Buscar por símbolo o nombre...',
        onChange: (state) => { searchState = state; visibleCount = PAGE_SIZE; renderTable(); },
    });

    $('new-instrument-btn').addEventListener('click', () => openInstrumentModal());

    $('instrumentos-bucket-bar').querySelectorAll('.segmented-toggle-option').forEach((btn) => {
        btn.addEventListener('click', () => {
            activeBucket = btn.dataset.bucket;
            visibleCount = PAGE_SIZE;
            renderBucketBarActiveState();
            renderTable();
        });
    });

    const container = $('instrumentos-table-container');
    container.addEventListener('click', (e) => {
        const addBtn = e.target.closest('.instrumentos-add-btn');
        if (!addBtn) return;
        const row = mergedRows.find((r) => r.symbol === addBtn.dataset.symbol && !r.inCatalog);
        if (row) openInstrumentModal({ symbol: row.symbol, type: row.type, market: row.market, currency: 'ARS', price_source: 'data912' });
    });
    wireRowActions(container, {
        onEdit: (id) => {
            const row = mergedRows.find((r) => String(r.id) === String(id));
            if (row) openInstrumentModal(row.raw);
        },
        onDelete: async (id) => {
            const row = mergedRows.find((r) => String(r.id) === String(id));
            if (!row) return;
            if (await deleteInstrument(id, row.symbol)) await loadAll();
        },
    });

    document.addEventListener('instruments-changed', loadAll);
    document.addEventListener('currency-changed', loadAll);
}
