import { $ } from '../shared/dom.js';
import { fmt, fmtPct, fmtMoney } from '../shared/format.js';
import { api } from './api.js';
import { pollForecast, pollBatch } from './forecast-polling.js';
import {
    renderForecastChart, renderRsiPane, chartLegendHtml, rangeControlsHtml, wireRangeControls,
    renderAccuracyChart, accuracyLegendHtml,
} from '../charts/candlestick-chart.js';
import { renderRiskGauge, instrumentRiskFormulaLines } from '../charts/risk-gauge.js';

const SIGNAL_LABEL = { comprar: 'Comprar', mantener: 'Mantener', vender: 'Vender' };
const SIGNAL_TAG_CLASS = { comprar: 'tag-positive', mantener: 'tag-neutral', vender: 'tag-negative' };
const TYPE_LABELS = {
    accion: 'Acción', cedear: 'CEDEAR', bono: 'Bono',
    on: 'Obligación Negociable', fondo: 'Fondo', moneda: 'Moneda',
};
const BACK_ICON_SVG = '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 15 7 10l5-5"/></svg>';

let chartInstance = null;
let rsiInstance = null;

function parseRoute(hash) {
    const path = hash.slice('detalle/'.length);
    const [symbolPart, queryPart] = path.split('?');
    const params = new URLSearchParams(queryPart || '');
    return {
        symbol: decodeURIComponent(symbolPart),
        instrumentId: params.get('instrumentId') ? Number(params.get('instrumentId')) : null,
        market: params.get('market') || 'byma',
        assetType: params.get('assetType') || null,
    };
}

function tabsBarHtml(tabs) {
    if (tabs.length <= 1) return '';
    return `<div class="detail-tabs">${tabs.map((t, i) => `
        <button class="detail-tab-btn${i === 0 ? ' active' : ''}" data-tab="${t.id}" type="button">${t.label}</button>
    `).join('')}</div>`;
}

function shellHtml(symbol, tabs) {
    const hasPosicion = tabs.some((t) => t.id === 'posicion');
    const hasRiesgo = tabs.some((t) => t.id === 'riesgo');
    const hasPrecision = tabs.some((t) => t.id === 'precision');
    return `
        <a href="#resumen" class="detail-breadcrumb">${BACK_ICON_SVG}<span>Resumen</span></a>
        <div class="panel detail-panel">
            <div class="panel-body">
                <div class="detail-title-row">
                    <h2>${symbol}</h2>
                    <span class="tag tag-neutral" id="detail-type-tag"></span>
                    <span class="tag tag-neutral" id="detail-signal-badge">Sin pronóstico</span>
                </div>

                ${tabsBarHtml(tabs)}

                <div class="detail-tab-panel" data-tab-panel="pronostico">
                    <div class="loading show" id="detail-forecast-loading">
                        <div class="spinner"></div><span id="detail-forecast-progress">Calculando…</span>
                    </div>
                    <div id="detail-forecast-content" style="display: none;">
                        ${chartLegendHtml()}
                        <div id="detail-chart-range">${rangeControlsHtml()}</div>
                        <div class="forecast-card-chart" id="detail-chart"></div>
                        <div class="forecast-card-rsi" id="detail-rsi"></div>
                        <p class="hint" id="detail-signal-explanation"></p>
                    </div>
                </div>

                ${hasPosicion ? `
                <div class="detail-tab-panel" data-tab-panel="posicion" hidden>
                    <div id="detail-position"></div>
                </div>` : ''}

                ${hasRiesgo ? `
                <div class="detail-tab-panel" data-tab-panel="riesgo" hidden>
                    <div id="detail-risk-gauge"></div>
                </div>` : ''}

                ${hasPrecision ? `
                <div class="detail-tab-panel" data-tab-panel="precision" hidden>
                    <div id="detail-accuracy-root"></div>
                </div>` : ''}
            </div>
        </div>
    `;
}

function wireTabs(root) {
    const buttons = [...root.querySelectorAll('.detail-tab-btn')];
    buttons.forEach((btn) => {
        btn.addEventListener('click', () => {
            buttons.forEach((b) => b.classList.toggle('active', b === btn));
            root.querySelectorAll('.detail-tab-panel').forEach((panel) => {
                panel.hidden = panel.dataset.tabPanel !== btn.dataset.tab;
            });
        });
    });
}

function positionHtml(h) {
    const cls = h.pnl === null ? 'cell-muted' : (h.pnl >= 0 ? 'cell-positive' : 'cell-negative');
    const currency = h.moneda_mostrada || h.instrument.currency;
    return `
        <div class="detail-position-grid">
            <div class="detail-position-item"><div class="label">Cantidad</div><div class="value">${fmt(h.cantidad, 4)}</div></div>
            <div class="detail-position-item"><div class="label">Costo Promedio</div><div class="value">${fmtMoney(h.costo_promedio, currency)}</div></div>
            <div class="detail-position-item"><div class="label">Precio Actual</div><div class="value">${fmtMoney(h.precio_actual, currency)}</div></div>
            <div class="detail-position-item"><div class="label">Valor Actual</div><div class="value">${fmtMoney(h.valor_actual, currency)}</div></div>
            <div class="detail-position-item"><div class="label">P&amp;L</div><div class="value ${cls}">${fmtMoney(h.pnl, currency)}</div></div>
            <div class="detail-position-item"><div class="label">P&amp;L %</div><div class="value ${cls}">${h.pnl_pct !== null ? fmtPct(h.pnl_pct) : '—'}</div></div>
        </div>
    `;
}

function updateBadge(signal) {
    const badge = $('detail-signal-badge');
    badge.textContent = SIGNAL_LABEL[signal] || 'Sin pronóstico';
    badge.className = `tag ${SIGNAL_TAG_CLASS[signal] || 'tag-neutral'}`;
}

function renderForecastResult(data) {
    const result = data.result;
    updateBadge(result.signal);
    $('detail-signal-explanation').textContent = result.explanation;
    $('detail-forecast-loading').classList.remove('show');
    $('detail-forecast-content').style.display = 'block';
    if (chartInstance) chartInstance.remove();
    if (rsiInstance) rsiInstance.remove();
    chartInstance = renderForecastChart($('detail-chart'), result);
    rsiInstance = renderRsiPane($('detail-rsi'), result);
    wireRangeControls($('detail-chart-range'), chartInstance, result);
}

function showForecastError(message) {
    $('detail-forecast-loading').classList.remove('show');
    $('detail-forecast-content').style.display = 'block';
    $('detail-signal-explanation').textContent = `Error: ${message}`;
}

function startPolling(forecastId) {
    $('detail-forecast-loading').classList.add('show');
    $('detail-forecast-content').style.display = 'none';
    pollForecast(`/api/portfolio/forecasts/${forecastId}`, {
        onProgress: (pct) => { $('detail-forecast-progress').textContent = `Calculando… ${pct}%`; },
        onDone: renderForecastResult,
        onError: (err) => showForecastError(err.message),
    });
}

function accuracyStatStripHtml(data) {
    return `
        <div class="stat-strip">
            <div class="stat"><div class="stat-label">MAPE</div><div class="stat-value num">${data.mape}%</div></div>
            <div class="stat">
                <div class="stat-label">Acierto direccional</div>
                <div class="stat-value num">${data.directional_hit_pct !== null ? `${data.directional_hit_pct}%` : '—'}</div>
            </div>
            <div class="stat"><div class="stat-label">Pronósticos evaluados</div><div class="stat-value num">${data.forecasts_evaluated}</div></div>
        </div>
    `;
}

function backtestButtonHtml(label) {
    return `<button type="button" class="btn btn-secondary" id="detail-backtest-btn" style="margin-top: 12px;">${label}</button>
        <p class="hint" id="detail-backtest-status" style="margin-top: 8px;"></p>`;
}

function wireBacktestButton(instrumentId, symbol) {
    const btn = $('detail-backtest-btn');
    if (!btn) return;
    btn.addEventListener('click', async () => {
        btn.disabled = true;
        const statusEl = $('detail-backtest-status');
        try {
            const { data } = await api.backtestInstrument(instrumentId);
            if (!data.forecast_ids.length) {
                statusEl.textContent = 'Ya hay backtest corrido para todo el rango disponible.';
                btn.disabled = false;
                return;
            }
            statusEl.textContent = `Corriendo backtest… 0/${data.forecast_ids.length}`;
            pollBatch(data.forecast_ids, {
                onProgress: ({ total, done }) => { statusEl.textContent = `Corriendo backtest… ${done}/${total}`; },
                onDone: ({ total, errors }) => {
                    statusEl.textContent = errors
                        ? `Listo, con ${errors} de ${total} fallidos.`
                        : `Backtest completo (${total} corridas).`;
                    loadAccuracy(instrumentId, symbol);
                },
            });
        } catch (err) {
            statusEl.textContent = `Error: ${err.response?.data?.error || err.message}`;
            btn.disabled = false;
        }
    });
}

async function loadAccuracy(instrumentId, symbol) {
    const root = $('detail-accuracy-root');
    root.innerHTML = '<p class="hint">Cargando…</p>';
    try {
        const { data } = await api.instrumentAccuracy(instrumentId);
        if (!data) {
            root.innerHTML = `
                <p class="hint">Todavía no corriste ningún pronóstico de ${symbol} — andá a la pestaña Pronóstico para generar el primero, o generá un backtest histórico para ver qué hubiera dicho Kronos en el pasado.</p>
                ${backtestButtonHtml('Generar backtest histórico')}
            `;
            wireBacktestButton(instrumentId, symbol);
            return;
        }
        if (data.pending) {
            const date = new Date(`${data.earliest_evaluable_date}T00:00:00Z`)
                .toLocaleDateString('es-AR', { day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC' });
            root.innerHTML = `
                <p class="hint">El pronóstico más antiguo de ${symbol} recién predice a partir del ${date} — todavía no llegó esa fecha, así que no hay precio real con qué compararlo. Generá un backtest para ver la precisión histórica sin esperar.</p>
                ${backtestButtonHtml('Generar backtest histórico')}
            `;
            wireBacktestButton(instrumentId, symbol);
            return;
        }
        root.innerHTML = `
            ${accuracyStatStripHtml(data)}
            ${accuracyLegendHtml()}
            <div class="forecast-card-chart" id="detail-accuracy-chart"></div>
            <p class="hint" style="margin-top: 12px;">${data.explanation}</p>
            ${backtestButtonHtml('Ampliar backtest histórico')}
        `;
        renderAccuracyChart($('detail-accuracy-chart'), data);
        wireBacktestButton(instrumentId, symbol);
    } catch (err) {
        root.innerHTML = `<p class="hint">Error: ${err.response?.data?.error || err.message}</p>`;
    }
}

async function loadForecast({ symbol, instrumentId, market, assetType }) {
    const latestParams = instrumentId ? { instrument_id: instrumentId } : { symbol, market };
    try {
        const { data } = await api.latestForecast(latestParams);
        if (data && data.status === 'done') return renderForecastResult(data);
        if (data && ['queued', 'running'].includes(data.status)) return startPolling(data.id);

        const { data: created } = instrumentId
            ? await api.createForecast(instrumentId)
            : await api.explorerForecast(symbol, assetType);
        startPolling(created.id);
    } catch (err) {
        showForecastError(err.response?.data?.error || err.message);
    }
}

export async function renderInstrumentDetail(hash) {
    const { symbol, instrumentId, market, assetType } = parseRoute(hash);
    const root = $('instrument-detail-root');
    chartInstance = null;
    rsiInstance = null;

    const { data: holdings } = await api.holdings();
    const holding = holdings.find((h) => h.instrument.symbol === symbol);
    const resolvedType = holding ? holding.instrument.type : assetType;
    const resolvedInstrumentId = holding ? holding.instrument.id : instrumentId;

    const tabs = [{ id: 'pronostico', label: 'Pronóstico' }];
    if (holding) tabs.push({ id: 'posicion', label: 'Posición' });
    if (holding?.risk) tabs.push({ id: 'riesgo', label: 'Riesgo' });
    if (resolvedInstrumentId) tabs.push({ id: 'precision', label: 'Precisión' });

    root.innerHTML = shellHtml(symbol, tabs);
    wireTabs(root);

    $('detail-type-tag').textContent = TYPE_LABELS[resolvedType] || resolvedType || '';

    if (holding) {
        $('detail-position').innerHTML = positionHtml(holding);
        if (holding.risk) {
            renderRiskGauge(
                $('detail-risk-gauge'), holding.risk.score, holding.risk.label,
                holding.risk.explanation, instrumentRiskFormulaLines(holding.risk),
            );
        }
    }

    const precisionBtn = root.querySelector('.detail-tab-btn[data-tab="precision"]');
    if (precisionBtn && resolvedInstrumentId) {
        let accuracyLoaded = false;
        precisionBtn.addEventListener('click', () => {
            if (accuracyLoaded) return;
            accuracyLoaded = true;
            loadAccuracy(resolvedInstrumentId, symbol);
        });
    }

    await loadForecast({
        symbol,
        instrumentId: resolvedInstrumentId,
        market: holding ? holding.instrument.market : market,
        assetType: resolvedType,
    });
}

document.addEventListener('theme-changed', () => {
    if (location.hash.slice(1).startsWith('detalle/')) renderInstrumentDetail(location.hash.slice(1));
});
