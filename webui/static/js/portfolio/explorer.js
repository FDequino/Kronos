import { $ } from '../shared/dom.js';
import { fmt, fmtPct } from '../shared/format.js';
import { api } from './api.js';
import { createSorter, wireSortableHeaders } from '../shared/sortable-table.js';
import { createSearchBar, applySearchState } from '../shared/search-bar.js';

const TYPE_LABELS = { accion: 'Acción', cedear: 'CEDEAR', bono: 'Bono' };

const SEARCH_CONFIG = {
    searchFields: (item) => [item.symbol],
    filters: [
        { key: 'accion', label: 'Acción', test: (item) => item.type === 'accion' },
        { key: 'cedear', label: 'CEDEAR', test: (item) => item.type === 'cedear' },
        { key: 'bono', label: 'Bono', test: (item) => item.type === 'bono' },
    ],
    groupBy: [
        { key: 'type', label: 'Tipo', value: (item) => TYPE_LABELS[item.type] || item.type },
    ],
};

let debounceTimer = null;
let currentRows = [];
let loaded = false;
let lastQuery = '';
let searchState = { query: '', activeFilters: new Set(), groupKey: null };
const sorter = createSorter();
sorter.set('symbol', 1);

async function fetchAndRender() {
    lastQuery = searchState.query;
    try {
        const { data } = await api.explorerSearch(searchState.query);
        currentRows = data;
        renderRows();
    } catch (error) {
        $('explorer-results-tbody').innerHTML = `<tr><td colspan="4" class="table-empty">No se pudo consultar el mercado ahora mismo (${error.response?.data?.error || error.message}). Probá de nuevo en un momento.</td></tr>`;
    }
}

function loadIfNeeded() {
    if (loaded) return;
    loaded = true;
    fetchAndRender();
}

function sortRows(rows) {
    return sorter.apply(rows, (item, key) => item[key]);
}

function rowHtml(item) {
    const pct = item.pct_change;
    const cls = pct === null || pct === undefined ? 'cell-muted' : (pct >= 0 ? 'cell-positive' : 'cell-negative');
    const params = new URLSearchParams({ market: item.market, assetType: item.type });
    return `<tr>
        <td><a href="#detalle/${encodeURIComponent(item.symbol)}?${params.toString()}" class="table-link">${item.symbol}</a></td>
        <td>${TYPE_LABELS[item.type] || item.type}</td>
        <td class="num">${fmt(item.price)}</td>
        <td class="num ${cls}">${pct !== null && pct !== undefined ? fmtPct(pct) : '—'}</td>
    </tr>`;
}

function renderRows() {
    const result = applySearchState(currentRows, searchState, SEARCH_CONFIG);
    const tbody = $('explorer-results-tbody');
    if (result.grouped) {
        tbody.innerHTML = result.groups.map((g) => `
            <tr class="table-group-header"><td colspan="4">${g.label} (${g.items.length})</td></tr>
            ${sortRows(g.items).map(rowHtml).join('')}
        `).join('');
    } else {
        const rows = sortRows(result.items);
        tbody.innerHTML = rows.length ? rows.map(rowHtml).join('') : '<tr><td colspan="4" class="table-empty">Sin resultados.</td></tr>';
    }
    $('explorer-count').textContent = `${result.grouped ? result.groups.reduce((n, g) => n + g.items.length, 0) : result.items.length} instrumentos`;
}

export function initExplorerView() {
    createSearchBar($('explorer-search-bar'), {
        ...SEARCH_CONFIG,
        searchPlaceholder: 'Buscar por símbolo...',
        onChange: (state) => {
            searchState = state;
            renderRows();
            if (state.query !== lastQuery) {
                clearTimeout(debounceTimer);
                debounceTimer = setTimeout(fetchAndRender, 300);
            }
        },
    });
    wireSortableHeaders($('view-explorador'), sorter, renderRows);

    if (location.hash.slice(1) === 'explorador') loadIfNeeded();
    window.addEventListener('hashchange', () => {
        if (location.hash.slice(1) === 'explorador') loadIfNeeded();
    });
}
