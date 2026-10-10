import { $ } from '../shared/dom.js';
import { showStatus, setLoading } from '../shared/status.js';
import { fmt, fmtPct, fmtMoney } from '../shared/format.js';
import { api } from './api.js';
import { renderAllocationDonut } from '../charts/allocation-donut.js';
import { renderRiskGauge, instrumentRiskFormulaLines, portfolioRiskFormulaLines } from '../charts/risk-gauge.js';
import { renderPortfolioHistoryChart, portfolioHistoryLegendHtml } from '../charts/portfolio-history-chart.js';
import { renderPnlBarChart } from '../charts/pnl-bar-chart.js';
import { renderRiskDistributionChart } from '../charts/risk-distribution-chart.js';
import { createSorter, wireSortableHeaders } from '../shared/sortable-table.js';
import { createColumnManager } from '../shared/column-manager.js';
import { createSearchBar, applySearchState } from '../shared/search-bar.js';
import { infoTooltipHtml, riskTooltipContentHtml } from '../shared/info-tooltip.js';
import { animateValue } from '../shared/animate-number.js';
import { pollBatch } from './forecast-polling.js';

const TENENCIAS_COLUMNS = [
    { key: 'symbol', label: 'Símbolo', numeric: false },
    { key: 'type', label: 'Tipo', numeric: false },
    { key: 'risk', label: 'Riesgo', numeric: false },
    { key: 'cantidad', label: 'Cantidad', numeric: true },
    { key: 'costo_promedio', label: 'Costo Prom.', numeric: true },
    { key: 'precio_actual', label: 'Precio Actual', numeric: true },
    { key: 'valor_actual', label: 'Valor Actual', numeric: true },
    { key: 'pnl', label: 'P&L', numeric: true },
    { key: 'pnl_pct', label: 'P&L %', numeric: true },
];

const TYPE_LABELS = {
    accion: 'Acciones', cedear: 'CEDEARs', bono: 'Bonos',
    on: 'Obligaciones Negociables', fondo: 'Fondos', moneda: 'Moneda',
};
const RISK_TAG_CLASS = { bajo: 'tag-positive', medio: 'tag-neutral', alto: 'tag-negative' };
const BNA_VARIANT_LABEL = {
    divisa_venta: 'Divisa venta', divisa_compra: 'Divisa compra',
    billete_venta: 'Billete venta', billete_compra: 'Billete compra',
};

const RISK_LABEL_TITLE = { bajo: 'Riesgo bajo', medio: 'Riesgo medio', alto: 'Riesgo alto' };

const TENENCIAS_SEARCH_CONFIG = {
    searchFields: (h) => [h.instrument.symbol, h.instrument.name],
    filters: [
        { key: 'accion', label: 'Acciones', test: (h) => h.instrument.type === 'accion' },
        { key: 'cedear', label: 'CEDEARs', test: (h) => h.instrument.type === 'cedear' },
        { key: 'bono', label: 'Bonos', test: (h) => h.instrument.type === 'bono' },
        { key: 'on', label: 'Obligaciones Negociables', test: (h) => h.instrument.type === 'on' },
        { key: 'fondo', label: 'Fondos', test: (h) => h.instrument.type === 'fondo' },
        { key: 'moneda', label: 'Moneda', test: (h) => h.instrument.type === 'moneda' },
        { key: 'riesgo-bajo', label: 'Riesgo bajo', test: (h) => h.risk?.label === 'bajo' },
        { key: 'riesgo-medio', label: 'Riesgo medio', test: (h) => h.risk?.label === 'medio' },
        { key: 'riesgo-alto', label: 'Riesgo alto', test: (h) => h.risk?.label === 'alto' },
    ],
    groupBy: [
        { key: 'type', label: 'Tipo', value: (h) => TYPE_LABELS[h.instrument.type] || h.instrument.type },
        { key: 'risk', label: 'Riesgo', value: (h) => (h.risk ? RISK_LABEL_TITLE[h.risk.label] : 'Sin dato') },
    ],
};

let currentCurrency = 'ARS';
let lastHoldings = [];
let pnlField = 'pnl';
let tenenciasSearchState = { query: '', activeFilters: new Set(), groupKey: null };
let tenenciasSearchBar = null;
const tenenciasSorter = createSorter();
const tenenciasColumns = createColumnManager('kronos-tenencias-columns', TENENCIAS_COLUMNS);

export async function loadHoldings() {
    const [
        { data: holdings }, { data: cauciones }, { data: settings }, { data: portfolioRisk },
        { data: historyData }, usdRate,
    ] = await Promise.all([
        api.holdings(), api.cauciones(), api.settings(), api.portfolioRisk(),
        api.portfolioHistoryReconstructed(), api.bnaRateCurrent().catch(() => ({ data: null })),
    ]);
    currentCurrency = settings.display_currency;
    $('holdings-currency-note').textContent = `Mostrando en ${currentCurrency}`;

    renderGroups(holdings);
    renderStatStrip(holdings, cauciones, historyData);
    renderUsdStat(usdRate.data);
    renderAllocationDonut($('allocation-chart-container'), buildAllocationSegments(holdings), selectType, currentCurrency);
    renderRiskGauge(
        $('portfolio-risk-container'),
        portfolioRisk?.score, portfolioRisk?.label,
        portfolioRisk?.explanation || 'Sin tenencias todavía.',
        portfolioRisk ? portfolioRiskFormulaLines(portfolioRisk) : null,
    );
    if (portfolioRisk) {
        $('portfolio-risk-container').insertAdjacentHTML('beforeend', riskContributorsHtml(holdings));
    }
    renderPortfolioHistoryChart($('portfolio-history-container'), historyData);
    $('portfolio-history-legend').innerHTML = historyData?.series?.length >= 2 ? portfolioHistoryLegendHtml() : '';
    renderMissingSymbolsNote(historyData);
    renderPnlBarChart($('pnl-bar-chart-container'), holdings, goToDetail, { currency: currentCurrency, field: pnlField });
    renderRiskDistributionChart($('risk-distribution-container'), holdings);
    $('resumen-narrativo').innerHTML = buildNarrative(holdings);
}

function goToDetail(symbol) {
    location.hash = `#detalle/${symbol}`;
}

function renderMissingSymbolsNote(historyData) {
    const el = $('portfolio-history-missing');
    const missing = historyData?.missing_symbols || [];
    el.textContent = missing.length
        ? `No incluye (sin histórico de precios disponible): ${missing.join(', ')}.`
        : '';
}

function renderUsdStat(data) {
    if (!data) {
        $('stat-usd-rate').textContent = '—';
        $('stat-usd-sub').textContent = 'No se pudo consultar el BNA';
        return;
    }
    animateValue($('stat-usd-rate'), { to: data.rate, formatFn: (v) => fmtMoney(v, 'ARS') });
    $('stat-usd-sub').textContent = BNA_VARIANT_LABEL[data.variant] || '';
    $('stat-usd-detail').innerHTML = Object.entries(BNA_VARIANT_LABEL).map(([key, label]) => `
        <div class="stat-detail-row"><span>${label}</span><span class="num">${fmtMoney(data.rates?.[key], 'ARS')}</span></div>
    `).join('');
}

function riskContributorsHtml(holdings) {
    const withRisk = holdings.filter((h) => h.risk && h.valor_actual);
    if (!withRisk.length) return '';
    const top = [...withRisk]
        .sort((a, b) => (b.valor_actual * b.risk.score) - (a.valor_actual * a.risk.score))
        .slice(0, 3);
    return `
        <div class="risk-contributors">
            <div class="risk-contributors-label">Mayores contribuyentes al riesgo</div>
            ${top.map((h) => `
                <div class="risk-contributor-row">
                    <a href="#detalle/${h.instrument.symbol}" class="table-link">${h.instrument.symbol}</a>
                    <span class="tag ${RISK_TAG_CLASS[h.risk.label] || 'tag-neutral'}">${h.risk.label}</span>
                    <span class="num">${h.risk.score}/100</span>
                </div>
            `).join('')}
        </div>`;
}

function capitalize(s) {
    return s.charAt(0).toUpperCase() + s.slice(1);
}

function buildNarrative(holdings) {
    if (!holdings.length) return 'Sin tenencias todavía.';

    const withPct = holdings.filter((h) => h.pnl_pct !== null && h.pnl_pct !== undefined);
    const biggestMover = withPct.length
        ? withPct.reduce((a, b) => (Math.abs(b.pnl_pct) > Math.abs(a.pnl_pct) ? b : a))
        : null;
    const withRisk = holdings.filter((h) => h.risk);
    const riskiest = withRisk.length
        ? withRisk.reduce((a, b) => (b.risk.score > a.risk.score ? b : a))
        : null;

    const parts = [];
    if (biggestMover) {
        const dir = biggestMover.pnl_pct >= 0 ? 'ganancia' : 'pérdida';
        parts.push(`el mayor movimiento es <strong>${biggestMover.instrument.symbol}</strong> con ${fmtPct(Math.abs(biggestMover.pnl_pct))} de ${dir}`);
    }
    if (riskiest) {
        parts.push(`tu posición más riesgosa es <strong>${riskiest.instrument.symbol}</strong> (${riskiest.risk.score}/100)`);
    }
    if (!parts.length) return 'Todavía no hay suficiente información para un resumen.';
    return capitalize(parts.join(', ')) + '.';
}

function buildAllocationSegments(holdings) {
    const byType = new Map();
    holdings.forEach((h) => {
        const type = h.instrument.type;
        byType.set(type, (byType.get(type) || 0) + (h.valor_actual || 0));
    });
    return [...byType.entries()]
        .filter(([, value]) => value > 0)
        .map(([type, value]) => ({ type, label: TYPE_LABELS[type] || type, value }));
}

function columnToggleHtml() {
    return `
    <div class="column-toggle">
        <button type="button" class="column-toggle-btn" id="column-toggle-btn">Columnas</button>
        <div class="column-toggle-panel" id="column-toggle-panel" hidden>
            ${tenenciasColumns.allColumns().map((c) => `
                <label><input type="checkbox" data-col="${c.key}" ${tenenciasColumns.isHidden(c.key) ? '' : 'checked'}> ${c.label}</label>
            `).join('')}
        </div>
    </div>`;
}

function wireColumnToggle(container) {
    const btn = container.querySelector('#column-toggle-btn');
    const panel = container.querySelector('#column-toggle-panel');
    btn.addEventListener('click', (e) => {
        e.stopPropagation();
        panel.hidden = !panel.hidden;
    });
    panel.querySelectorAll('input[type="checkbox"]').forEach((cb) => {
        cb.addEventListener('click', (e) => e.stopPropagation());
        cb.addEventListener('change', () => {
            tenenciasColumns.toggleVisible(cb.dataset.col);
            renderGroups(lastHoldings);
        });
    });
}

function headerCellHtml(c) {
    return `<th class="${c.numeric ? 'num ' : ''}sortable" data-sort="${c.key}" draggable="true">${c.label}<span class="sort-indicator"></span></th>`;
}

function updateSortIndicators(container) {
    container.querySelectorAll('th.sortable').forEach((th) => {
        const indicator = th.querySelector('.sort-indicator');
        if (!indicator) return;
        indicator.textContent = th.dataset.sort === tenenciasSorter.key ? (tenenciasSorter.dir > 0 ? ' ▲' : ' ▼') : '';
    });
}

function wireColumnDrag(theadRow) {
    let dragKey = null;
    theadRow.querySelectorAll('th').forEach((th) => {
        th.addEventListener('dragstart', (e) => {
            dragKey = th.dataset.sort;
            e.dataTransfer.effectAllowed = 'move';
        });
        th.addEventListener('dragover', (e) => {
            e.preventDefault();
            e.dataTransfer.dropEffect = 'move';
        });
        th.addEventListener('drop', (e) => {
            e.preventDefault();
            const targetKey = th.dataset.sort;
            if (dragKey && dragKey !== targetKey) {
                tenenciasColumns.moveColumn(dragKey, targetKey);
                renderGroups(lastHoldings);
            }
        });
    });
}

function selectType(type) {
    tenenciasSearchBar?.setActiveFilters([type]);
}

function sortValue(h, key) {
    switch (key) {
        case 'symbol': return h.instrument.symbol;
        case 'type': return TYPE_LABELS[h.instrument.type] || h.instrument.type;
        case 'risk': return h.risk ? h.risk.score : null;
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
        return;
    }

    const columns = tenenciasColumns.visibleColumns();
    const sorted = tenenciasSorter.apply(holdings, sortValue);
    const result = applySearchState(sorted, tenenciasSearchState, TENENCIAS_SEARCH_CONFIG);
    const bodyRows = result.grouped
        ? result.groups.map((g) => `
            <tr class="table-group-header"><td colspan="${columns.length}">${g.label} (${g.items.length})</td></tr>
            ${g.items.map((h) => rowHtml(h, columns)).join('')}
        `).join('')
        : (result.items.length
            ? result.items.map((h) => rowHtml(h, columns)).join('')
            : `<tr><td colspan="${columns.length}" class="table-empty">Sin resultados para este filtro.</td></tr>`);

    container.innerHTML = `
        <div class="table-toolbar">
            ${columnToggleHtml()}
        </div>
        <div class="table-scroll">
            <table>
                <thead>
                    <tr>${columns.map((c) => headerCellHtml(c)).join('')}</tr>
                </thead>
                <tbody>${bodyRows}</tbody>
            </table>
        </div>
    `;

    wireColumnToggle(container);
    wireColumnDrag(container.querySelector('thead tr'));
    wireSortableHeaders(container, tenenciasSorter, () => renderGroups(lastHoldings));
    updateSortIndicators(container);
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
            return `<td>${h.risk
                ? `<span class="tag ${RISK_TAG_CLASS[h.risk.label] || 'tag-neutral'}">${h.risk.label}</span>${infoTooltipHtml(riskTooltipContentHtml(h.risk.explanation, instrumentRiskFormulaLines(h.risk)))}`
                : '<span class="hint">—</span>'}</td>`;
        case 'cantidad':
            return `<td class="num">${fmt(h.cantidad, 4)}</td>`;
        case 'costo_promedio':
            return `<td class="num">${fmtMoney(h.costo_promedio, currentCurrency)}</td>`;
        case 'precio_actual':
            return `<td class="num">${h.instrument.price_source === 'manual'
                ? `<input type="number" step="any" class="num manual-price-input" data-instrument-id="${h.instrument.id}" value="${h.precio_actual ?? ''}" placeholder="precio" style="width: 110px;">`
                : fmtMoney(h.precio_actual, currentCurrency)}</td>`;
        case 'valor_actual':
            return `<td class="num">${fmtMoney(h.valor_actual, currentCurrency)}</td>`;
        case 'pnl':
            return `<td class="num ${cls}">${fmtMoney(h.pnl, currentCurrency)}</td>`;
        case 'pnl_pct':
            return `<td class="num ${cls}">${h.pnl_pct !== null ? fmtPct(h.pnl_pct) : '—'}</td>`;
        default:
            return '<td></td>';
    }
}

function rowHtml(h, columns) {
    return `<tr>${columns.map((c) => cellHtml(h, c.key)).join('')}</tr>`;
}

function renderStatStrip(holdings, cauciones, historyData) {
    const valorHoldings = holdings.reduce((sum, h) => sum + (h.valor_actual ?? 0), 0);
    const valorCauciones = cauciones.reduce((sum, c) => sum + c.valor_actual, 0);
    const pnlTotal = holdings.reduce((sum, h) => sum + (h.pnl ?? 0), 0);

    animateValue($('stat-valor-total'), { to: valorHoldings + valorCauciones, formatFn: (v) => fmtMoney(v, currentCurrency) });

    let subHtml = `Tenencias ${fmtMoney(valorHoldings, currentCurrency)} · Cauciones ${fmtMoney(valorCauciones, currentCurrency)}`;
    const series = historyData?.series || [];
    if (series.length >= 2) {
        const last = series[series.length - 1];
        const prev = series[series.length - 2];
        if (prev.valor_real) {
            const diffPct = (last.valor_real - prev.valor_real) / prev.valor_real * 100;
            const dir = diffPct >= 0 ? 'positive' : 'negative';
            const arrow = diffPct >= 0 ? '▲' : '▼';
            subHtml = `<span class="stat-sub-delta ${dir}">${arrow} ${fmtPct(Math.abs(diffPct))} vs. último día hábil</span> · ${subHtml}`;
        }
    }
    $('stat-valor-sub').innerHTML = subHtml;

    const byType = new Map();
    holdings.forEach((h) => { byType.set(h.instrument.type, (byType.get(h.instrument.type) || 0) + (h.valor_actual || 0)); });
    $('stat-valor-detail').innerHTML = [...byType.entries()].map(([type, value]) => `
        <div class="stat-detail-row"><span>${TYPE_LABELS[type] || type}</span><span class="num">${fmtMoney(value, currentCurrency)}</span></div>
    `).join('') + `<div class="stat-detail-row"><span>Cauciones</span><span class="num">${fmtMoney(valorCauciones, currentCurrency)}</span></div>`;

    const pnlEl = $('stat-pnl-total');
    animateValue(pnlEl, { to: pnlTotal, formatFn: (v) => fmtMoney(v, currentCurrency) });
    pnlEl.classList.toggle('positive', holdings.length > 0 && pnlTotal >= 0);
    pnlEl.classList.toggle('negative', holdings.length > 0 && pnlTotal < 0);

    const sortedByPnl = [...holdings].filter((h) => h.pnl !== null).sort((a, b) => b.pnl - a.pnl);
    const top = sortedByPnl.slice(0, 3);
    const bottom = sortedByPnl.slice(-3).reverse();
    $('stat-pnl-detail').innerHTML = `
        <div class="stat-detail-label">Ganando</div>
        ${top.map((h) => `<div class="stat-detail-row"><span>${h.instrument.symbol}</span><span class="num cell-positive">${fmtMoney(h.pnl, currentCurrency)}</span></div>`).join('')}
        <div class="stat-detail-label">Perdiendo</div>
        ${bottom.map((h) => `<div class="stat-detail-row"><span>${h.instrument.symbol}</span><span class="num cell-negative">${fmtMoney(h.pnl, currentCurrency)}</span></div>`).join('')}
    `;

    animateValue($('stat-posiciones'), { to: holdings.length, duration: 400 });
    const countByType = new Map();
    holdings.forEach((h) => countByType.set(h.instrument.type, (countByType.get(h.instrument.type) || 0) + 1));
    $('stat-posiciones-detail').innerHTML = [...countByType.entries()].map(([type, count]) => `
        <div class="stat-detail-row"><span>${TYPE_LABELS[type] || type}</span><span class="num">${count}</span></div>
    `).join('');
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

function toggleDetail(id) {
    const el = $(id);
    el.hidden = !el.hidden;
}

function wireStatClicks() {
    $('stat-valor-total-card').addEventListener('click', () => toggleDetail('stat-valor-detail'));
    $('stat-pnl-total-card').addEventListener('click', () => toggleDetail('stat-pnl-detail'));
    $('stat-posiciones-card').addEventListener('click', () => toggleDetail('stat-posiciones-detail'));
    $('stat-usd-card').addEventListener('click', () => toggleDetail('stat-usd-detail'));
}

export function initHoldingsView() {
    tenenciasSearchBar = createSearchBar($('holdings-search-bar'), {
        ...TENENCIAS_SEARCH_CONFIG,
        searchPlaceholder: 'Buscar por símbolo o nombre...',
        onChange: (state) => { tenenciasSearchState = state; renderGroups(lastHoldings); },
    });
    $('refresh-quotes-btn').addEventListener('click', refreshQuotes);
    $('pnl-field-toggle').querySelectorAll('.segmented-toggle-option').forEach((btn) => {
        btn.addEventListener('click', () => {
            pnlField = btn.dataset.value;
            $('pnl-field-toggle').querySelectorAll('.segmented-toggle-option').forEach((b) => b.classList.toggle('active', b === btn));
            renderPnlBarChart($('pnl-bar-chart-container'), lastHoldings, goToDetail, { currency: currentCurrency, field: pnlField });
        });
    });
    $('holdings-groups').addEventListener('change', (e) => {
        if (e.target.classList.contains('manual-price-input')) saveManualPrice(e.target);
    });
    document.addEventListener('click', (e) => {
        const panel = document.getElementById('column-toggle-panel');
        if (panel && !panel.hidden && !e.target.closest('.column-toggle')) panel.hidden = true;
    });
    wireStatClicks();
    document.addEventListener('currency-changed', loadHoldings);
    document.addEventListener('theme-changed', loadHoldings);
    $('portfolio-backtest-btn').addEventListener('click', runPortfolioBacktest);
}

async function runPortfolioBacktest() {
    const btn = $('portfolio-backtest-btn');
    const statusEl = $('portfolio-backtest-status');
    btn.disabled = true;
    try {
        const { data } = await api.backtestPortfolio();
        if (!data.forecast_ids.length) {
            statusEl.textContent = 'Ya hay backtest corrido para todo el rango disponible de tus tenencias.';
            btn.disabled = false;
            return;
        }
        statusEl.textContent = `Corriendo backtest histórico… 0/${data.forecast_ids.length}`;
        pollBatch(data.forecast_ids, {
            onProgress: ({ total, done }) => { statusEl.textContent = `Corriendo backtest histórico… ${done}/${total}`; },
            onDone: async ({ total, errors }) => {
                statusEl.textContent = errors
                    ? `Backtest completo, con ${errors} de ${total} fallidos.`
                    : `Backtest histórico completo (${total} corridas).`;
                btn.disabled = false;
                await loadHoldings();
            },
        });
    } catch (error) {
        statusEl.textContent = `Error: ${error.response?.data?.error || error.message}`;
        btn.disabled = false;
    }
}
