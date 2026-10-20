import { $ } from '../shared/dom.js';
import { showStatus, setLoading } from '../shared/status.js';
import { fmt, fmtQty, fmtPct, fmtMoney } from '../shared/format.js';
import { api } from './api.js';
import { instrumentRiskFormulaLines } from '../charts/risk-gauge.js';
import { createSorter, wireSortableHeaders } from '../shared/sortable-table.js';
import { createColumnManager } from '../shared/column-manager.js';
import { createSearchBar, applySearchState, wireGroupToggles, tableBodyRowsHtml } from '../shared/search-bar.js';
import { mountColumnToggle, headerCellHtml, updateSortIndicators, wireColumnDrag, totalRowHtml, withMobileCardLayout, mobileExpandToggleHtml, wireMobileCardToggle } from '../shared/table-chrome.js';
import { infoTooltipHtml, riskTooltipContentHtml } from '../shared/info-tooltip.js';
import { renderPatrimonio } from './patrimonio.js';
import { onQuoteUpdate } from './quote-stream.js';

const TENENCIAS_COLUMNS = [
    { key: 'symbol', label: 'Símbolo', numeric: false },
    { key: 'type', label: 'Tipo', numeric: false },
    { key: 'risk', label: 'Riesgo', numeric: false },
    { key: 'cantidad', label: 'Cantidad', numeric: true },
    { key: 'costo_promedio', label: 'Costo Prom.', numeric: true, term: 'costo_promedio' },
    { key: 'precio_actual', label: 'Precio Actual', numeric: true },
    {
        key: 'valor_actual', label: 'Valor Actual', numeric: true, aggregate: 'sum',
        value: (h) => h.valor_actual, format: (n) => fmtMoney(n, currentCurrency),
    },
    {
        key: 'pnl', label: 'P&L', numeric: true, aggregate: 'sum', term: 'pnl',
        value: (h) => h.pnl, format: (n) => fmtMoney(n, currentCurrency),
    },
    {
        key: 'pnl_pct', label: 'P&L %', numeric: true, term: 'pnl_pct',
        // Promedio ponderado por costo (sum(pnl)/sum(costo), nunca un
        // promedio simple de porcentajes -- un -50% en una posición chica y
        // un +5% en una grande no pesan igual). costo = valor_actual - pnl,
        // no hace falta un campo nuevo.
        aggregateFn: (rows) => {
            let totalCosto = 0;
            let totalPnl = 0;
            rows.forEach((h) => {
                if (h.pnl === null || h.pnl === undefined || h.valor_actual === null || h.valor_actual === undefined) return;
                totalCosto += h.valor_actual - h.pnl;
                totalPnl += h.pnl;
            });
            return totalCosto ? (totalPnl / totalCosto) * 100 : null;
        },
        format: (n) => fmtPct(n),
    },
];

const TYPE_LABELS = {
    accion: 'Acciones', cedear: 'CEDEARs', bono: 'Bonos',
    on: 'Obligaciones Negociables', fondo: 'Fondos', moneda: 'Moneda', caucion: 'Cauciones',
    cripto: 'Cripto',
};
const RISK_TAG_CLASS = { bajo: 'tag-positive', medio: 'tag-neutral', alto: 'tag-negative' };

const RISK_LABEL_TITLE = { bajo: 'Riesgo bajo', medio: 'Riesgo medio', alto: 'Riesgo alto' };

const TENENCIAS_SEARCH_CONFIG = {
    searchFields: [
        { key: 'symbol', label: 'Símbolo', value: (h) => h.instrument.symbol },
        { key: 'name', label: 'Nombre', value: (h) => h.instrument.name },
    ],
    favoritesKey: 'kronos-tenencias-favorites',
    filters: [
        { key: 'accion', label: 'Acciones', block: 'Tipo', test: (h) => h.instrument.type === 'accion' },
        { key: 'cedear', label: 'CEDEARs', block: 'Tipo', test: (h) => h.instrument.type === 'cedear' },
        { key: 'bono', label: 'Bonos', block: 'Tipo', test: (h) => h.instrument.type === 'bono' },
        { key: 'on', label: 'Obligaciones Negociables', block: 'Tipo', test: (h) => h.instrument.type === 'on' },
        { key: 'fondo', label: 'Fondos', block: 'Tipo', test: (h) => h.instrument.type === 'fondo' },
        { key: 'moneda', label: 'Moneda', block: 'Tipo', test: (h) => h.instrument.type === 'moneda' },
        { key: 'cripto', label: 'Cripto', block: 'Tipo', test: (h) => h.instrument.type === 'cripto' },
        { key: 'riesgo-bajo', label: 'Riesgo bajo', block: 'Riesgo', test: (h) => h.risk?.label === 'bajo' },
        { key: 'riesgo-medio', label: 'Riesgo medio', block: 'Riesgo', test: (h) => h.risk?.label === 'medio' },
        { key: 'riesgo-alto', label: 'Riesgo alto', block: 'Riesgo', test: (h) => h.risk?.label === 'alto' },
    ],
    groupBy: [
        { key: 'type', label: 'Tipo', value: (h) => TYPE_LABELS[h.instrument.type] || h.instrument.type },
        { key: 'risk', label: 'Riesgo', value: (h) => (h.risk ? RISK_LABEL_TITLE[h.risk.label] : 'Sin dato') },
    ],
};

let currentCurrency = 'ARS';
let lastHoldings = [];
let lastCauciones = [];
let tenenciasSearchState = { query: '', activeFilters: new Set(), groupKey: null };
let tenenciasSearchBar = null;
const tenenciasCollapsedGroups = new Set();
const tenenciasSorter = createSorter();
const tenenciasColumns = createColumnManager('kronos-tenencias-columns', TENENCIAS_COLUMNS);

export async function loadHoldings() {
    // Fase 7: una sola request (/dashboard-bundle) en vez de las ~13 que
    // hacian esta funcion + los paneles de Resumen por separado -- ver
    // portfolio/routes.py::dashboard_bundle. Fase 16: el Dashboard viejo
    // (resumen-v2.js) se eliminó -- Resumen ahora solo pinta Tabla (esta
    // función) + Patrimonio.
    const { data: bundle } = await api.dashboardBundle();
    lastCauciones = bundle.cauciones;
    currentCurrency = bundle.settings.display_currency;
    const otherCurrency = currentCurrency === 'ARS' ? 'USD' : 'ARS';
    $('holdings-currency-note').textContent = `Mostrando en ${currentCurrency} (tocar para ver en ${otherCurrency})`;

    renderGroups(bundle.holdings);
    await renderPatrimonio(bundle);
}


function buildAllocationSegments(holdings, cauciones = []) {
    const byType = new Map();
    holdings.forEach((h) => {
        const type = h.instrument.type;
        byType.set(type, (byType.get(type) || 0) + (h.valor_actual || 0));
    });
    // Solo vigentes -- una vencida ya no es capital en caución (ver nota en
    // portfolio/routes.py::portfolio_risk, confirmado contra datos_broker/).
    const caucionesTotal = cauciones.filter((c) => !c.vencido).reduce((sum, c) => sum + c.valor_actual, 0);
    if (caucionesTotal > 0) byType.set('caucion', caucionesTotal);
    return [...byType.entries()]
        .filter(([, value]) => value > 0)
        .map(([type, value]) => ({ type, label: TYPE_LABELS[type] || type, value }));
}

function sortValue(h, key) {
    switch (key) {
        case 'symbol': return h.instrument.symbol;
        case 'type': return TYPE_LABELS[h.instrument.type] || h.instrument.type;
        case 'risk': return h.risk ? h.risk.annualized_volatility_pct : null;
        case 'cantidad': return h.cantidad;
        case 'costo_promedio': return h.costo_promedio;
        case 'precio_actual': return h.precio_actual;
        case 'valor_actual': return h.valor_actual;
        case 'pnl': return h.pnl;
        case 'pnl_pct': return h.pnl_pct;
        default: return null;
    }
}

function renderGroups(holdings) {
    lastHoldings = holdings;
    const container = $('holdings-groups');
    if (!holdings.length) {
        container.innerHTML = '<p class="table-empty">Sin tenencias todavía. Cargá un instrumento y una transacción de compra.</p>';
        const slot = $('holdings-columns-slot');
        if (slot) slot.innerHTML = '';
        return;
    }

    const columns = tenenciasColumns.visibleColumns();
    const sorted = tenenciasSorter.apply(holdings, sortValue);
    const result = applySearchState(sorted, tenenciasSearchState, TENENCIAS_SEARCH_CONFIG);
    const bodyRows = tableBodyRowsHtml(result, columns, (h) => rowHtml(h, columns), { collapsedGroups: tenenciasCollapsedGroups });
    const visibleRows = result.grouped ? result.groups.flatMap((g) => g.items) : result.items;

    container.innerHTML = `
        <div class="table-scroll">
            <table>
                <thead>
                    <tr>${columns.map((c) => headerCellHtml(c)).join('')}</tr>
                </thead>
                <tbody>${bodyRows}</tbody>
                ${visibleRows.length ? `<tfoot>${totalRowHtml(columns, visibleRows)}</tfoot>` : ''}
            </table>
        </div>
    `;

    const rerender = () => renderGroups(lastHoldings);
    mountColumnToggle($('holdings-columns-slot'), tenenciasColumns, rerender);
    wireColumnDrag(container.querySelector('thead tr'), tenenciasColumns, rerender);
    wireSortableHeaders(container, tenenciasSorter, rerender);
    wireGroupToggles(container, tenenciasCollapsedGroups, rerender);
    updateSortIndicators(container, tenenciasSorter);
}

function pnlClass(pnl) {
    if (pnl === null) return 'cell-muted';
    return pnl >= 0 ? 'cell-positive' : 'cell-negative';
}

function cellHtml(h, key) {
    const type = h.instrument.type;
    const cls = pnlClass(h.pnl);
    switch (key) {
        case 'symbol':
            return `<td><a href="#detalle/${h.instrument.symbol}" class="table-link">${h.instrument.symbol}</a></td>`;
        case 'type':
            return `<td>${TYPE_LABELS[type] || type}</td>`;
        case 'risk':
            // Alto uniforme de fila (ver conversación, Fase 3): tag + ícono de
            // tooltip son dos inline-flex separados -- sin este wrapper,
            // columnas angostas los partían en dos líneas (fila de 62px en
            // vez de 36px, la única columna que rompía la altura pareja).
            return `<td>${h.risk
                ? `<span class="row-inline-tag"><span class="tag ${RISK_TAG_CLASS[h.risk.label] || 'tag-neutral'}">${h.risk.label}</span>${infoTooltipHtml(riskTooltipContentHtml(h.risk.explanation, instrumentRiskFormulaLines(h.risk)))}</span>`
                : '<span class="hint">—</span>'}</td>`;
        case 'cantidad':
            return `<td class="num">${fmtQty(h.cantidad)}</td>`;
        case 'costo_promedio':
            return `<td class="num">${fmtMoney(h.costo_promedio, currentCurrency)}</td>`;
        case 'precio_actual':
            return `<td class="num" data-cell="precio_actual">${h.instrument.price_source === 'manual'
                ? `<input type="number" step="any" class="num manual-price-input" data-instrument-id="${h.instrument.id}" value="${h.precio_actual ?? ''}" placeholder="precio" style="width: 110px;">`
                : fmtMoney(h.precio_actual, currentCurrency)}</td>`;
        case 'valor_actual':
            return `<td class="num" data-cell="valor_actual">${fmtMoney(h.valor_actual, currentCurrency)}</td>`;
        case 'pnl':
            return `<td class="num ${cls}" data-cell="pnl">${fmtMoney(h.pnl, currentCurrency)}</td>`;
        case 'pnl_pct':
            return `<td class="num ${cls}" data-cell="pnl_pct">${h.pnl_pct !== null ? fmtPct(h.pnl_pct) : '—'}</td>`;
        default:
            return '<td></td>';
    }
}

function rowHtml(h, columns) {
    const cells = withMobileCardLayout(columns.map((c) => cellHtml(h, c.key)), columns, 1);
    return `<tr data-instrument-id="${h.instrument.id}">${cells.join('')}${mobileExpandToggleHtml(columns, 1)}</tr>`;
}

// Fase 7: cuando llega un evento SSE de cotizacion, no alcanza con pintar el
// precio crudo -- precio_actual/valor_actual/pnl/pnl_pct ya vienen convertidos
// a currentCurrency por el backend (_apply_display_currency), y duplicar esa
// conversion aca (con la fuente de USD que el usuario eligio en Preferencias,
// que puede ser BNA/MEP/CCL) arriesgaria un numero sutilmente distinto al que
// muestra el resto de la pagina. Se coalescen los ids tocados en una ventana
// corta y se pide /holdings (liviano, sin el resto del bundle) una sola vez
// por lote -- mucho mas barato que recargar todo, pero sigue siendo el
// backend el que calcula, nunca una segunda implementacion en JS.
let quotePatchTimer = null;
const pendingQuoteIds = new Set();

function patchHoldingsRow(instrumentId) {
    const container = $('holdings-groups');
    const row = container?.querySelector(`tr[data-instrument-id="${instrumentId}"]`);
    const h = lastHoldings.find((x) => x.instrument.id === instrumentId);
    if (!row || !h || h.instrument.price_source === 'manual') return;
    // withMobileCardLayout(..., 0) (ver conversación, Fase 8) -- un patch
    // puntual de celda con outerHTML normal perdía el data-label que la hace
    // legible en modo tarjeta mobile (ninguna de estas 4 es la columna
    // primary, siempre va con label).
    ['precio_actual', 'valor_actual', 'pnl', 'pnl_pct'].forEach((key) => {
        const cell = row.querySelector(`[data-cell="${key}"]`);
        if (!cell) return;
        const col = TENENCIAS_COLUMNS.find((c) => c.key === key);
        cell.outerHTML = col ? withMobileCardLayout([cellHtml(h, key)], [col], 0)[0] : cellHtml(h, key);
    });
    const freshRow = container.querySelector(`tr[data-instrument-id="${instrumentId}"]`);
    if (!freshRow) return;
    freshRow.classList.remove('quote-flash');
    void freshRow.offsetWidth; // reflow: reinicia la animacion si ya estaba flasheando
    freshRow.classList.add('quote-flash');
}

function scheduleQuotePatch(instrumentId) {
    pendingQuoteIds.add(instrumentId);
    if (quotePatchTimer) return;
    quotePatchTimer = setTimeout(async () => {
        const ids = [...pendingQuoteIds];
        pendingQuoteIds.clear();
        quotePatchTimer = null;
        try {
            const { data: fresh } = await api.holdings();
            const byId = new Map(fresh.map((h) => [h.instrument.id, h]));
            ids.forEach((id) => {
                const idx = lastHoldings.findIndex((h) => h.instrument.id === id);
                if (byId.has(id) && idx !== -1) lastHoldings[idx] = byId.get(id);
            });
            ids.forEach(patchHoldingsRow);
        } catch {
            // si /holdings falla este lote se pierde -- el proximo evento SSE
            // (o el refresco manual) lo va a reintentar, no hace falta retry propio.
        }
    }, 1500);
}

async function refreshQuotes() {
    const btn = $('refresh-quotes-btn');
    btn.disabled = true;
    setLoading('holdings-loading', true);
    try {
        const { data } = await api.refreshQuotes();
        const errCount = Object.keys(data.errors).length;
        showStatus('status-banner', errCount ? 'error' : 'success',
            errCount ? `Actualizado con ${errCount} error(es)` : 'Cotizaciones actualizadas');
        await loadHoldings();
    } catch (error) {
        showStatus('status-banner', 'error', error.response?.data?.error || error.message);
    } finally {
        btn.disabled = false;
        setLoading('holdings-loading', false);
    }
}

async function saveManualPrice(input) {
    const instrumentId = input.dataset.instrumentId;
    const price = parseFloat(input.value);
    if (Number.isNaN(price)) return;
    try {
        await api.setManualQuote(instrumentId, price);
        showStatus('status-banner', 'success', 'Precio actualizado');
        await loadHoldings();
    } catch (error) {
        showStatus('status-banner', 'error', error.response?.data?.error || error.message);
    }
}

export function initHoldingsView() {
    tenenciasSearchBar = createSearchBar($('holdings-search-bar'), {
        ...TENENCIAS_SEARCH_CONFIG,
        searchPlaceholder: 'Buscar por símbolo o nombre...',
        onChange: (state) => { tenenciasSearchState = state; renderGroups(lastHoldings); },
    });
    $('refresh-quotes-btn').addEventListener('click', refreshQuotes);
    // "Mostrando en USD" no era clickeable -- había que ir hasta Ajustes
    // para cambiar la moneda (ver conversación/auditoría, hallazgo 2.5).
    // Mismo mecanismo que el toggle de Ajustes → Moneda: togglea
    // display_currency y dispara 'currency-changed' para que todas las
    // vistas que escuchan ese evento (esta incluida) se actualicen solas.
    $('holdings-currency-note').addEventListener('click', async () => {
        const next = currentCurrency === 'ARS' ? 'USD' : 'ARS';
        await api.updateSettings({ display_currency: next });
        document.dispatchEvent(new CustomEvent('currency-changed', { detail: next }));
    });
    onQuoteUpdate((event) => scheduleQuotePatch(event.instrument_id));
    $('holdings-groups').addEventListener('change', (e) => {
        if (e.target.classList.contains('manual-price-input')) saveManualPrice(e.target);
    });
    wireMobileCardToggle($('holdings-groups'));
    document.addEventListener('currency-changed', loadHoldings);
    document.addEventListener('theme-changed', loadHoldings);
}
