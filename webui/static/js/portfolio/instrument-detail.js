import { $ } from '../shared/dom.js';
import { fmt, fmtPct, fmtMoney, fmtDate, fmtDateTime, fmtQty } from '../shared/format.js';
import { showStatus } from '../shared/status.js';
import { goBack } from '../shared/nav-history.js';
import { api } from './api.js';
import { pollForecast, pollBatch } from './forecast-polling.js';
import {
    renderForecastChart, renderRsiPane, renderMacdPane, chartLegendHtml, macdLegendHtml, rangeControlsHtml, wireRangeControls, wireChartLegend,
    renderAccuracyChart, accuracyLegendHtml, renderForecastFanChart, forecastFanLegendHtml,
} from '../charts/candlestick-chart.js';
import { syncTimeScales } from '../charts/lw-chart-utils.js';
import { glossaryTerm, glossaryText } from '../shared/glossary.js';
import { priceAlertsPanelHtml, wirePriceAlertsPanel, alertToolbarHtml, wireAlertToolbar, wireDraggableAlerts } from './price-alerts.js';
import { EDGE_SIGNIFICANT_PTS } from './technical-signal.js';
import { createSorter, wireSortableHeaders } from '../shared/sortable-table.js';
import { headerCellHtml, updateSortIndicators } from '../shared/table-chrome.js';
import { loadingHtml } from '../shared/loading.js';
import { openModal, closeModal } from '../shared/modal.js';

const SIGNAL_LABEL = { comprar: 'Comprar', mantener: 'Mantener', vender: 'Vender' };
// tag-signal-buy/sell (acento), nunca tag-positive/tag-negative -- es una
// señal predictiva, no P&L real (ver DESIGN_SYSTEM.md).
const SIGNAL_TAG_CLASS = { comprar: 'tag-signal-buy', mantener: 'tag-neutral', vender: 'tag-signal-sell' };
// Riesgo SI es un P&L/perfil real (no una señal predictiva) -- mismo mapeo
// que holdings.js/comparador.js, rojo/verde tal cual.
const RISK_TAG_CLASS = { bajo: 'tag-positive', medio: 'tag-neutral', alto: 'tag-negative' };
const TYPE_LABELS = {
    accion: 'Acción', cedear: 'CEDEAR', bono: 'Bono',
    on: 'Obligación Negociable', fondo: 'Fondo', moneda: 'Moneda',
};
const BACK_ICON_SVG = '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 15 7 10l5-5"/></svg>';

const FUNDAMENTAL_FIELDS = [
    { key: 'pe_ratio', label: 'P/E', term: 'pe_ratio' },
    { key: 'forward_pe', label: 'P/E futuro', term: 'pe_futuro' },
    // profit_margin viene de yfinance como fraccion (0.0612 = 6.12%) -> hace
    // falta *100. dividend_yield en cambio ya viene escalado como numero de
    // porcentaje (2.41 = 2.41%) -- aplicarle el mismo *100 lo deja en 241%.
    { key: 'profit_margin', label: 'Margen neto', pct: true, term: 'margen_neto' },
    { key: 'debt_to_equity', label: 'Deuda/Patrimonio', term: 'deuda_patrimonio' },
    { key: 'dividend_yield', label: 'Dividend yield', pctRaw: true, term: 'dividend_yield' },
    { key: 'market_cap', label: 'Cap. de mercado', money: true, term: 'cap_mercado' },
];

let chartInstance = null;
let rsiInstance = null;
let macdInstance = null;
let currentOverlays = { trendlines: { resistance: [], support: [], breakout: null }, priceAlerts: [] };
let currentInstrumentIdForAlerts = null;
// Guardados para poder repintar SOLO el gráfico + alertas (agregar/mover/
// borrar una alerta) sin disparar renderInstrumentDetail completo de nuevo
// (que re-pide holdings/fundamentales/Kronos/riesgo -- bug real reportado:
// "se recarga todo" al tocar la línea, con el flash y la pérdida de scroll
// que eso implica, para lo que debería ser una actualización de un número).
let lastChartData = null;
let lastChartWithPrediction = false;

// Tarjeta unificada "Análisis Cuantitativo Kronos" (ver conversación, Fase de
// limpieza de UI): varias fuentes async distintas (pronóstico de hoy, RSI +
// niveles sugeridos, backtest RSI, precisión histórica) escriben cada una SU
// propia porción de tiles sin pisar a las demás -- renderAnalysisCard() las
// concatena y pinta el grid entero cada vez que una porción cambia.
let analysisTiles = { model: '', forecast: '', extra: '', technicalBacktest: '' };

function renderAnalysisCard() {
    const el = $('detail-forecast-kpis');
    if (!el) return;
    const joined = `${analysisTiles.model}${analysisTiles.forecast}${analysisTiles.extra}${analysisTiles.technicalBacktest}`;
    el.innerHTML = joined || loadingHtml('Analizando…');
}

// Exportado (ver tabs.js::setPageMeta/goBackFromDetail) -- el router top-level
// necesita el simbolo real para el titulo de pagina sin reimplementar el
// parseo de query params, y sin crear un import circular (tabs.js ya importa
// este archivo para renderInstrumentDetail).
export function parseDetailRoute(hash) {
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

// Fase 2 (ver conversación, sistema de diseño unificado): esta barra es
// NAVEGACIÓN (cambia qué panel se ve, no qué datos), mismo rol que
// .subtab-btn del resto de la app -- reusa esa clase/estilo en vez de
// .detail-tab-btn (antes subrayado, un cuarto estilo de tab distinto sin
// motivo). Sigue siendo estado LOCAL del DOM (ver wireTabs), nunca pasa por
// el router de hash de Fase 1 -- ".detail-tabs" en el contenedor es solo el
// separador inferior propio de esta barra, el layout (flex/gap/overflow) ya
// lo da ".subtabs" compartido.
function tabsBarHtml(tabs) {
    if (tabs.length <= 1) return '';
    return `<div class="subtabs detail-tabs">${tabs.map((t, i) => `
        <button class="subtab-btn${i === 0 ? ' active' : ''}" data-tab="${t.id}" type="button">${t.label}</button>
    `).join('')}</div>`;
}

function shellHtml(symbol, tabs) {
    const hasPrecision = tabs.some((t) => t.id === 'precision');
    const hasRentaFija = tabs.some((t) => t.id === 'renta-fija');
    // Fase 1 (ver conversación): breadcrumb jerárquico fijo "Instrumentos ›
    // SÍMBOLO" -- antes dependía del último .nav-item activo (poco confiable:
    // caía a "Resumen" si no había ninguno). El link es el fallback real (si
    // se entró directo a esta URL, sin historial interno -- ver
    // tabs.js::goBackFromDetail), el click normal usa history.back().
    return `
        <div class="detail-top-row">
            <a href="#instrumentos" class="detail-breadcrumb" id="detail-breadcrumb-back">${BACK_ICON_SVG}<span>Instrumentos</span></a>
            <span class="detail-breadcrumb-sep">›</span>
            <span class="detail-breadcrumb-current">${symbol}</span>
            <a href="#instrumentos" class="btn btn-secondary">Ver en Cartera</a>
        </div>
        <div class="panel detail-panel">
            <div class="panel-body">
                <div class="detail-header">
                    <div>
                        <div class="detail-title-row">
                            <h2>${symbol}</h2>
                            <span class="tag tag-neutral" id="detail-type-tag"></span>
                        </div>
                        <div class="detail-price-row">
                            <span class="detail-price-value num" id="detail-price-value">—</span>
                            <span class="detail-price-change num" id="detail-price-change"></span>
                            <span class="hint" id="detail-price-timestamp"></span>
                        </div>
                    </div>
                    <div class="detail-badges">
                        <span class="tag tag-neutral" id="detail-technical-badge">Técnico: —</span>
                        <span class="tag tag-neutral" id="detail-signal-badge">Kronos: sin pronóstico</span>
                    </div>
                </div>

                <!-- Posicion + Riesgo: SIEMPRE visibles, ya no son pestañas
                     (antes había que cambiar de tab para ver cualquiera de
                     las dos, ver DESIGN_SYSTEM.md). Vacio (sin holding, ej.
                     catálogo unificado) simplemente no ocupa lugar. -->
                <div id="detail-position-bar"></div>

                ${tabsBarHtml(tabs)}

                <!-- Layout de 2 columnas (ver conversación, limpieza de UI):
                     izquierda el gráfico (con su selector de temporalidad,
                     ver rangeControlsHtml) + alertas de precio (viven acá
                     porque se dibujan/arrastran directo sobre el gráfico);
                     derecha la tarjeta unificada "Análisis Cuantitativo
                     Kronos" (señal, confianza, RSI, TP/SL sugerido, precisión
                     histórica -- todo lo que antes vivía repartido en 3
                     tarjetas sueltas) + Fundamentales. -->
                <div class="detail-tab-panel" data-tab-panel="pronostico">
                    <div class="loading show" id="detail-chart-loading">
                        <div class="spinner"></div><span>Cargando gráfico…</span>
                    </div>
                    <div id="detail-chart-content" class="hidden-block">
                        <div class="detail-layout-2col">
                            <div class="detail-chart-col">
                                <div id="detail-chart-legend"></div>
                                <div id="detail-breakout-note"></div>
                                <div class="detail-chart-toolbar">
                                    <div id="detail-chart-range">${rangeControlsHtml()}</div>
                                    <div id="detail-alert-toolbar">${alertToolbarHtml()}</div>
                                </div>
                                <div class="forecast-card-chart" id="detail-chart"></div>
                                <div class="chart-pane-title">${glossaryTerm('rsi14', 'RSI (14)')}</div>
                                <div class="forecast-card-rsi" id="detail-rsi"></div>
                                <div class="chart-pane-title">${glossaryTerm('macd', 'MACD (12, 26, 9)')}</div>
                                ${macdLegendHtml()}
                                <div class="forecast-card-rsi" id="detail-macd"></div>
                                <div class="panel" id="detail-price-alerts">
                                    <div class="panel-header"><h2>Alertas de precio</h2></div>
                                    <div class="panel-body"><p class="hint">Agregá este instrumento a tu cartera para configurar alertas.</p></div>
                                </div>
                            </div>
                            <div class="detail-analysis-col">
                                <div class="panel">
                                    <div class="panel-header">
                                        <div class="panel-header-text">
                                            <h2>Análisis Cuantitativo Kronos</h2>
                                            <p class="panel-subtitle">Pronóstico de hoy (horizonte configurable), RSI(14 ruedas), MACD(12,26,9) y precisión histórica sobre pronósticos no superpuestos ya resueltos.</p>
                                        </div>
                                    </div>
                                    <div class="panel-body">
                                        <p class="hint" id="detail-kronos-status"></p>
                                        <div class="mini-stat-grid" id="detail-forecast-kpis"></div>
                                    </div>
                                </div>
                                <div class="panel">
                                    <div class="panel-header">
                                        <div class="panel-header-text">
                                            <h2>Fundamentales</h2>
                                            <p class="panel-subtitle">Vía yfinance (actualizado cada 24h) — evaluados contra tus umbrales configurados en Ajustes; solo aplica a acciones/CEDEARs, bonos y ON no tienen estos datos.</p>
                                        </div>
                                    </div>
                                    <div class="panel-body" id="detail-fundamentals"></div>
                                </div>
                                <div class="panel">
                                    <div class="panel-header">
                                        <div class="panel-header-text">
                                            <h2>Noticias y Sentimiento</h2>
                                            <p class="panel-subtitle">Vía Finnhub (si hay API key configurada) o yfinance -- cacheado 6hs. Alimenta el filtro de riesgo preventivo del Motor Satélite.</p>
                                        </div>
                                    </div>
                                    <div class="panel-body" id="detail-news"></div>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>

                ${hasPrecision ? `
                <div class="detail-tab-panel" data-tab-panel="precision" hidden>
                    <div id="detail-accuracy-root"></div>
                </div>` : ''}

                ${hasRentaFija ? `
                <div class="detail-tab-panel" data-tab-panel="renta-fija" hidden>
                    <div id="detail-renta-fija-root"></div>
                </div>` : ''}
            </div>
        </div>
    `;
}

function wireTabs(root) {
    const buttons = [...root.querySelectorAll('.subtabs.detail-tabs .subtab-btn')];
    buttons.forEach((btn) => {
        btn.addEventListener('click', () => {
            buttons.forEach((b) => b.classList.toggle('active', b === btn));
            root.querySelectorAll('.detail-tab-panel').forEach((panel) => {
                panel.hidden = panel.dataset.tabPanel !== btn.dataset.tab;
            });
        });
    });
}

// Barra SIEMPRE visible debajo del precio de cabecera (ya no pestañas
// "Posición"/"Riesgo" aparte, ver DESIGN_SYSTEM.md) -- "Precio Actual" se
// saca a propósito (ya está arriba, en detail-price-value; repetirlo acá era
// el mismo número dos veces en la misma pantalla).
function positionHtml(h) {
    const cls = h.pnl === null ? 'cell-muted' : (h.pnl >= 0 ? 'cell-positive' : 'cell-negative');
    const currency = h.moneda_mostrada || h.instrument.currency;
    const risk = h.risk;
    return `
        <div class="detail-position-grid detail-position-compact">
            <div class="detail-position-item"><div class="label">Cantidad</div><div class="value">${fmtQty(h.cantidad)}</div></div>
            <div class="detail-position-item"><div class="label">Costo Promedio</div><div class="value">${fmtMoney(h.costo_promedio, currency)}</div></div>
            <div class="detail-position-item"><div class="label">Valor Actual</div><div class="value">${fmtMoney(h.valor_actual, currency)}</div></div>
            <div class="detail-position-item">
                <div class="label">P&amp;L</div>
                <div class="value ${cls}">${fmtMoney(h.pnl, currency)}${h.pnl_pct !== null ? ` (${fmtPct(h.pnl_pct)})` : ''}</div>
            </div>
            ${risk ? `
            <div class="detail-position-item">
                <div class="label">${glossaryTerm('volatilidad_anualizada', 'Volatilidad anualizada')}</div>
                <div class="value">${fmtPct(risk.annualized_volatility_pct)}</div>
            </div>
            <div class="detail-position-item">
                <div class="label">Riesgo</div>
                <div class="value"><span class="tag ${RISK_TAG_CLASS[risk.label] || 'tag-neutral'}">${risk.label}</span></div>
            </div>` : ''}
        </div>
    `;
}

function updateKronosBadge(signal, validation) {
    const badge = $('detail-signal-badge');
    if (signal && validation && !validation.validated) {
        badge.textContent = 'Kronos: sin validar';
        badge.className = 'tag tag-neutral';
        badge.title = validation.reason || glossaryText('kronos_sin_validar');
        return;
    }
    badge.textContent = signal ? `Kronos: ${SIGNAL_LABEL[signal] || signal}` : 'Kronos: sin pronóstico';
    badge.className = `tag ${SIGNAL_TAG_CLASS[signal] || 'tag-neutral'}`;
    badge.title = signal ? '' : glossaryText('kronos_sin_pronostico');
}

// explanation (data.technical_explanation, una sola oracion) reemplaza el
// parrafo que antes vivia aparte, debajo del grafico (ver DESIGN_SYSTEM.md)
// -- ahora vive en el tooltip nativo del propio badge, nunca se pierde.
function updateTechnicalBadge(signal, explanation) {
    const badge = $('detail-technical-badge');
    badge.textContent = signal ? `Técnico: ${SIGNAL_LABEL[signal] || signal}` : 'Técnico: —';
    badge.className = `tag ${SIGNAL_TAG_CLASS[signal] || 'tag-neutral'}`;
    badge.title = explanation || glossaryText('tecnico_senal');
}

function revealChartContent() {
    $('detail-chart-loading').classList.remove('show');
    $('detail-chart-content').style.display = 'block';
}

// Pinta el gráfico (velas+RSI+MACD) + lo cablea (leyenda, botones de
// alertas, arrastre) -- factorizado para que agregar/mover/borrar una
// alerta pueda repintar SOLO esto (ver refreshAlertsAndChart), sin repetir
// el resto de renderForecastResult/loadChartFast (badge de Kronos, técnico,
// fundamentales, etc. no cambian por tocar una alerta).
function paintChartAndWireAlerts(instrumentId, chartData, withPrediction) {
    if (chartInstance) chartInstance.remove();
    if (rsiInstance) rsiInstance.remove();
    if (macdInstance) macdInstance.remove();
    $('detail-chart-legend').innerHTML = chartLegendHtml(withPrediction);
    chartInstance = renderForecastChart($('detail-chart'), chartData, currentOverlays);
    wireChartLegend($('detail-chart-legend'), chartInstance._legendSeries);
    const alertDragState = { dragging: false };
    wireAlertToolbar($('detail-alert-toolbar'), chartInstance, chartInstance._candleSeries, instrumentId, refreshAlertsAndChart, alertDragState);
    wireDraggableAlerts($('detail-chart'), chartInstance, chartInstance._candleSeries, chartInstance._alertLines, refreshAlertsAndChart, alertDragState);
    rsiInstance = renderRsiPane($('detail-rsi'), chartData);
    macdInstance = renderMacdPane($('detail-macd'), chartData);
    syncTimeScales([chartInstance, rsiInstance, macdInstance]);
    wireRangeControls($('detail-chart-range'), chartInstance, chartData);
    renderBreakoutNote();
    lastChartData = chartData;
    lastChartWithPrediction = withPrediction;
    currentInstrumentIdForAlerts = instrumentId;
}

// Agregar/mover/borrar una alerta llega acá (en vez de renderInstrumentDetail
// completo): re-pide sólo /overlays (liviano, sin yfinance/Kronos/holdings de
// por medio) y repinta el gráfico + la lista de abajo con el mismo
// chartData ya en memoria -- nada de lo demás de la página se toca.
async function refreshAlertsAndChart() {
    if (!currentInstrumentIdForAlerts || !lastChartData) return;
    const { data } = await api.instrumentOverlays(currentInstrumentIdForAlerts);
    currentOverlays = { trendlines: data.trendlines, priceAlerts: data.price_alerts };
    paintChartAndWireAlerts(currentInstrumentIdForAlerts, lastChartData, lastChartWithPrediction);
    const alertsEl = $('detail-price-alerts');
    if (alertsEl) {
        alertsEl.innerHTML = priceAlertsPanelHtml(currentOverlays.priceAlerts);
        wirePriceAlertsPanel(alertsEl, refreshAlertsAndChart);
    }
}

// Reemplaza el parrafo largo de result.explanation por tarjetas compactas
// (ver DESIGN_SYSTEM.md) -- la explicacion en prosa sigue viajando en
// result.explanation (otras vistas la siguen usando, ej. Señales), esta
// pagina ya no la vuelca tal cual.
// Parámetros claros (ver conversación, pase de UX/UI): cada pronóstico de
// Kronos trae su propio horizonte real (data.pred_len, cuántas ruedas hacia
// adelante predijo ESTA corrida puntual -- configurable, puede variar entre
// corridas) -- se muestra explícito en vez de dejar "Mediana"/"Rango p10-p90"
// como si fueran válidos para cualquier plazo.
function forecastKpisHtml(result, validation, predLen) {
    const dist = result.distribution;
    const pct = result.pred_change_pct;
    const horizonte = predLen ? ` (${predLen}d)` : '';
    const tiles = [
        `<div class="mini-stat"><div class="l">Señal actual</div><div class="v"><span class="tag ${SIGNAL_TAG_CLASS[result.signal] || 'tag-neutral'}">${SIGNAL_LABEL[result.signal] || result.signal}</span></div></div>`,
        `<div class="mini-stat"><div class="l">${glossaryTerm('mediana_pronostico', `Mediana${horizonte}`)}</div><div class="v num cell-signal">${fmtPct(pct, 2, { signed: true })}</div></div>`,
    ];
    if (dist) {
        tiles.push(`<div class="mini-stat"><div class="l">${glossaryTerm('rango_p10_p90', `Rango p10-p90${horizonte}`)}</div><div class="v num">${fmtPct(dist.p10_pct)} / +${fmtPct(dist.p90_pct)}</div></div>`);
        tiles.push(`<div class="mini-stat"><div class="l">${glossaryTerm('prob_suba', `P(suba)${horizonte}`)}</div><div class="v num">${fmt(dist.prob_up_pct, 0)}%</div></div>`);
        if (dist.edge_ratio !== null && dist.edge_ratio !== undefined) {
            const edgeTitle = dist.degraded_low_edge ? 'Movimiento chico frente a la incertidumbre — señal degradada a mantener.' : '';
            tiles.push(`<div class="mini-stat"><div class="l">Edge ratio</div><div class="v num${dist.degraded_low_edge ? ' cell-muted' : ''}" title="${edgeTitle}">${fmt(dist.edge_ratio, 2)}</div></div>`);
        }
    }
    const validated = validation?.validated;
    tiles.push(`<div class="mini-stat"><div class="l">Nivel de Confianza</div><div class="v num ${validated ? 'cell-positive' : 'cell-muted'}" title="${validation?.reason || ''}">${validated ? 'Alta (habilitada)' : 'Sin validar'}</div></div>`);
    return tiles.join('');
}

function renderForecastResult(data) {
    const result = data.result;
    revealChartContent();
    updateKronosBadge(result.signal, data.validation);
    $('detail-kronos-status').textContent = '';
    analysisTiles.forecast = forecastKpisHtml(result, data.validation, data.pred_len);
    renderAnalysisCard();
    paintChartAndWireAlerts(data.instrument_id, result, true);
}

function showForecastError(message) {
    revealChartContent();
    $('detail-kronos-status').textContent = `Error: ${message}`;
}

function startPolling(forecastId) {
    pollForecast(`/api/portfolio/forecasts/${forecastId}`, {
        onProgress: (pct) => { $('detail-kronos-status').textContent = `Analizando con Kronos… ${pct}%`; },
        onDone: renderForecastResult,
        onError: (err) => showForecastError(err.message),
    });
}

// Ya no es un boton manual (ver conversación, "Correr Chronos-Bolt de nuevo"
// se elimino de la UI) -- se dispara solo apenas se entra al detalle de un
// activo sin pronostico cacheado (ver renderInstrumentDetail/checkKronosStatus).
async function runKronosForecast({ symbol, instrumentId, assetType }) {
    $('detail-kronos-status').textContent = 'Encolando…';
    try {
        const { data: created } = instrumentId
            ? await api.createForecast(instrumentId)
            : await api.explorerForecast(symbol, assetType);
        startPolling(created.id);
    } catch (err) {
        showForecastError(err.response?.data?.error || err.message);
    }
}

// Devuelve true si ya habia (o se retomo el polling de) un pronostico -- el
// llamador usa esto para decidir si hace falta disparar uno nuevo solo.
async function checkKronosStatus({ symbol, instrumentId, market }) {
    const latestParams = instrumentId ? { instrument_id: instrumentId } : { symbol, market };
    try {
        const { data } = await api.latestForecast(latestParams);
        if (data && data.status === 'done') { renderForecastResult(data); return true; }
        if (data && ['queued', 'running'].includes(data.status)) { startPolling(data.id); return true; }
    } catch {
        // sin pronóstico corrido todavía -- el llamador dispara uno nuevo
    }
    return false;
}

// --- Gráfico técnico rápido: no espera a Kronos, sale del price_history ya guardado ---

// Reemplaza el parrafo largo del backtest del RSI por tarjetas compactas
// (ver DESIGN_SYSTEM.md) -- el detalle completo (hit-rate crudo, retorno
// promedio, tasa base) queda en el title (tooltip nativo), no se pierde,
// solo deja de ocupar un parrafo entero por default. Se funde en la tarjeta
// unificada "Análisis Cuantitativo Kronos" (ver analysisTiles/renderAnalysisCard).
async function loadTechnicalBacktest(instrumentId) {
    try {
        const { data } = await api.instrumentTechnicalBacktest(instrumentId);
        if (data.pending) {
            analysisTiles.technicalBacktest = '';
            renderAnalysisCard();
            return;
        }
        const { comprar, vender } = data.by_signal;
        const baseRate = data.base_rate_up_pct;
        const baseDown = baseRate !== null ? Math.round((100 - baseRate) * 10) / 10 : null;
        // Se lidera con el edge (acierto menos lo que pasaría igual sin ninguna
        // señal) -- un acierto del 55% no dice nada si el activo sube el 55%
        // de las veces de por sí, así que el % crudo queda de contexto en el
        // tooltip, no como el número destacado.
        const tile = (label, bucket, baseRateForDirection) => {
            if (!bucket) return '';
            const edge = bucket.edge_vs_base_rate_pct;
            const significant = edge !== null && Math.abs(edge) >= EDGE_SIGNIFICANT_PTS;
            const cls = significant ? 'cell-signal' : 'cell-muted';
            const edgeTxt = edge !== null ? `${fmt(edge, 1, { signed: true })} pts` : 'sin dato';
            const title = `${bucket.count} señales de ${label.toLowerCase()} pasadas — acierto crudo ${bucket.hit_rate_pct}%, retorno promedio ${bucket.avg_forward_return_pct}%, tasa base sin señal ${baseRateForDirection ?? '—'}% (ventanas de ${data.horizon_days} días)`;
            return `<div class="mini-stat"><div class="l">RSI ${label} (edge)</div><div class="v num ${cls}" title="${title}">${edgeTxt}</div></div>`;
        };
        analysisTiles.technicalBacktest = [tile('Comprar', comprar, baseRate), tile('Vender', vender, baseDown)].filter(Boolean).join('');
    } catch {
        analysisTiles.technicalBacktest = '';
    }
    renderAnalysisCard();
}

// Una ruptura de soporte/resistencia es un evento informativo -- NUNCA una
// propuesta de compra/venta (ver portfolio/trendlines.py y proposals.py):
// todavia no hay un backtest propio que mida si esta señal acierta mas que
// el azar, mismo principio ya aplicado al RSI.
function renderBreakoutNote() {
    const el = $('detail-breakout-note');
    if (!el) return;
    const breakout = currentOverlays.trendlines?.breakout;
    if (!breakout) {
        el.innerHTML = '';
        return;
    }
    el.innerHTML = `<p class="hint">⚠ Ruptura de ${breakout.kind} detectada (precio ${fmt(breakout.price)} vs. nivel ${fmt(breakout.level)}) — ${glossaryTerm('tendencia_soporte_resistencia', 'evento informativo, no genera una propuesta')}.</p>`;
}

async function loadPriceAlertsPanel(instrumentId) {
    currentInstrumentIdForAlerts = instrumentId;
    const el = $('detail-price-alerts');
    if (!el || !instrumentId) return;
    el.innerHTML = priceAlertsPanelHtml(currentOverlays.priceAlerts);
    wirePriceAlertsPanel(el, refreshAlertsAndChart);
}

// quote (ver conversación, Fase 7): el precio+variación grandes del
// encabezado ahora usan quotes_latest (igual que Instrumentos/Tenencias),
// no el último close de price_history -- ese puede quedar días atrás (solo
// se backfillea al correr un pronóstico) y antes de este fix mostraba un
// precio viejo mientras el resto de la app ya mostraba el correcto. El
// gráfico de abajo se sigue pintando con `historical` tal cual (su fuente
// real, sin tocar).
// Variación (ver conversación/auditoría, "la variación del día no coincide
// entre pantallas"): quote.pct_change es el % real de la fuente en vivo
// (contra el cierre de AYER, ej. el mismo que ya usa el catálogo de
// Instrumentos) -- se usa SIEMPRE que esté disponible. El cálculo viejo
// (quote.price vs. último close de price_history) queda solo como fallback
// para quotes persistidos ANTES de este fix (sin pct_change guardado) o
// fuentes que no lo proveen (dolarapi/binance/bróker/manual): ese cálculo
// podía comparar contra un cierre de días atrás y dar un % sin relación con
// la variación real (bug real confirmado: price_history de QQQ/GGAL con
// 10-11 días de atraso en producción).
// Devuelve siempre el último CLOSE de price_history (nunca el precio en
// vivo) -- lo siguen usando los niveles sugeridos de TP/SL ("aplicado sobre
// el último cierre", ver loadSuggestedLevels), que son una cuenta aparte del
// precio en vivo que se muestra en el encabezado.
function updatePriceHeader(historical, currency, quote) {
    const priceEl = $('detail-price-value');
    const changeEl = $('detail-price-change');
    const tsEl = $('detail-price-timestamp');
    const prevClose = historical && historical.length >= 1 ? historical[historical.length - 1].close : null;
    const displayPrice = quote?.price ?? prevClose;
    if (displayPrice === null || displayPrice === undefined) {
        priceEl.textContent = '—';
        changeEl.textContent = '';
        tsEl.textContent = '';
        return prevClose;
    }
    priceEl.textContent = fmtMoney(displayPrice, currency);
    tsEl.textContent = quote?.fetched_at ? `al ${fmtDateTime(quote.fetched_at)}` : '';

    let diffPct = null;
    if (quote?.pct_change !== null && quote?.pct_change !== undefined) {
        diffPct = quote.pct_change;
    } else if (quote?.price && prevClose) {
        // Fallback (ver docstring de arriba): solo si no hay pct_change real.
        diffPct = ((displayPrice - prevClose) / prevClose) * 100;
    }
    if (diffPct === null) {
        changeEl.textContent = '';
        return prevClose;
    }
    changeEl.textContent = `${diffPct >= 0 ? '▲' : '▼'} ${fmtPct(Math.abs(diffPct))}`;
    changeEl.classList.toggle('cell-positive', diffPct >= 0);
    changeEl.classList.toggle('cell-negative', diffPct < 0);
    return prevClose;
}

// RSI (14) actual + niveles sugeridos de Take Profit / Stop Loss -- parte de
// la tarjeta unificada "Análisis Cuantitativo Kronos" (ver conversación).
// El TP/SL sugerido NO es un backtest propio por instrumento (no existe tal
// cosa fuera del universo Core/Satélite, ver portfolio/backtest.py) -- es el
// mismo % configurado para el Motor Satélite (Ajustes > Estrategia y
// Motores), aplicado sobre el último cierre. Declarado como tal en el
// tooltip, nunca presentado como un nivel calculado especificamente para
// este activo.
//
// Botones TOGGLE "Fijar"/"Quitar" TP y SL (ver conversación, pase de UX/UI):
// si YA hay una alerta de ese tipo para este activo, el botón pasa a "Quitar"
// (rojo) y la borra -- nunca se deja crear una segunda alerta del mismo tipo
// sobre el mismo instrumento. Guarda el último contexto usado (lastClose/
// rsiValue/currency) para poder re-pintar estas dos tiles solas después de
// fijar/quitar, sin tener que recargar el gráfico entero.
let lastSuggestedLevelsCtx = null;

function tpSlTileHtml(tipo, label, shortLabel, suggestedNivel, pct, sign, currency, note) {
    const existing = currentOverlays.priceAlerts.find((a) => a.tipo === tipo);
    const button = existing
        ? `<button type="button" class="btn btn-secondary btn-compact mini-stat-action-btn btn-danger-outline" data-tipo="${tipo}" data-action="quitar" data-alert-id="${existing.id}">Quitar ${shortLabel}</button>`
        : `<button type="button" class="btn btn-secondary btn-compact mini-stat-action-btn" data-tipo="${tipo}" data-action="fijar" data-nivel="${suggestedNivel}">Fijar ${shortLabel}</button>`;
    return `
        <div class="mini-stat">
            <div class="l">${label} sugerido</div>
            <div class="v num" title="${note}">${fmtMoney(suggestedNivel, currency)} (${sign}${fmt(pct, 1)}%)</div>
            ${button}
        </div>
    `;
}

// Botones "Fijar TP"/"Fijar SL" (ver conversación, pase de UX/UI): aplican
// el nivel SUGERIDO directo como alerta de precio real (misma API que ya usa
// el click-en-el-gráfico de price-alerts.js) -- no hay un "modal de ajuste"
// aparte en esta app (las alertas siempre se crean apuntando un nivel, nunca
// con un formulario de texto), así que la confirmación rápida (toast +
// repintado del gráfico con la línea nueva) es el equivalente real de "aplicar
// directamente el nivel al gráfico" que pide el enunciado. Duplicate-safe:
// si para cuando se procesa el click YA hay una alerta de este tipo (ej.
// agregada con el toolbar del gráfico mientras tanto), se ACTUALIZA en vez
// de crear una segunda fila -- "evita la duplicación de alertas" del enunciado.
async function fijarNivelSugerido(instrumentId, tipo, nivel) {
    const label = tipo === 'tp' ? 'Take Profit' : 'Stop Loss';
    const nivelRedondeado = Math.round(nivel * 100) / 100;
    try {
        const existing = currentOverlays.priceAlerts.find((a) => a.tipo === tipo);
        if (existing) {
            await api.updatePriceAlert(existing.id, { nivel: nivelRedondeado });
            showStatus('status-banner', 'success', `${label} actualizado a ${fmt(nivelRedondeado)}.`);
        } else {
            await api.addPriceAlert({ instrument_id: instrumentId, tipo, nivel: nivelRedondeado });
            showStatus('status-banner', 'success', `${label} fijado en ${fmt(nivelRedondeado)}.`);
        }
        await refreshAlertsAndChart();
        await refreshSuggestedLevelsTiles();
    } catch (err) {
        showStatus('status-banner', 'error', err.response?.data?.error || err.message);
    }
}

// "Quitar TP"/"Quitar SL" (botón toggle cuando la alerta ya existe).
async function quitarNivelSugerido(tipo, alertId) {
    const label = tipo === 'tp' ? 'Take Profit' : 'Stop Loss';
    try {
        await api.deletePriceAlert(alertId);
        showStatus('status-banner', 'success', `${label} quitado.`);
        await refreshAlertsAndChart();
        await refreshSuggestedLevelsTiles();
    } catch (err) {
        showStatus('status-banner', 'error', err.response?.data?.error || err.message);
    }
}

async function refreshSuggestedLevelsTiles() {
    if (!lastSuggestedLevelsCtx) return;
    const { instrumentId, lastClose, rsiValue, currency } = lastSuggestedLevelsCtx;
    await loadSuggestedLevels(instrumentId, lastClose, rsiValue, currency);
}

async function loadSuggestedLevels(instrumentId, lastClose, rsiValue, currency) {
    lastSuggestedLevelsCtx = { instrumentId, lastClose, rsiValue, currency };
    const tiles = [];
    if (rsiValue !== null && rsiValue !== undefined) {
        tiles.push(`<div class="mini-stat"><div class="l">${glossaryTerm('rsi14', 'RSI (14 ruedas)')}</div><div class="v num">${fmt(rsiValue, 1)}</div></div>`);
    }
    if (lastClose) {
        try {
            const { data: exitParams } = await api.satelliteExitParams();
            const tpPrice = lastClose * (1 + exitParams.take_profit_pct / 100);
            const slPrice = lastClose * (1 - exitParams.stop_loss_pct / 100);
            const note = 'Mismo % configurado para el Motor Satélite (Ajustes > Estrategia y Motores) aplicado sobre el último cierre — no es un backtest propio de este activo.';
            tiles.push(tpSlTileHtml('tp', 'Take Profit', 'TP', tpPrice, exitParams.take_profit_pct, '+', currency, note));
            tiles.push(tpSlTileHtml('sl', 'Stop Loss', 'SL', slPrice, exitParams.stop_loss_pct, '-', currency, note));
        } catch {
            // sin niveles sugeridos si el endpoint no responde -- nunca se inventa un %
        }
    }
    analysisTiles.extra = tiles.join('');
    renderAnalysisCard();
}

async function loadChartFast(instrumentId, currency = 'ARS') {
    let data;
    try {
        ({ data } = await api.instrumentChart(instrumentId));
        updateTechnicalBadge(data.technical_signal, data.technical_explanation);
        const lastClose = updatePriceHeader(data.historical, currency, data.current_quote);
        revealChartContent();
        const chartData = { ...data, predicted: [] };
        paintChartAndWireAlerts(instrumentId, chartData, false);
        const rsiSeries = data.indicators?.rsi_14 || [];
        const lastRsi = rsiSeries.length ? rsiSeries[rsiSeries.length - 1] : null;
        loadSuggestedLevels(instrumentId, lastClose, lastRsi, currency);
    } catch (err) {
        // Antes esto ocultaba el spinner sin mostrar nada en su lugar y
        // guardaba el motivo real adentro de "Cómo se calcula" (un <details>
        // colapsado que nadie abre) -- el área del gráfico quedaba
        // completamente vacía y sin explicación visible (bug real: bonos,
        // fondos y cripto siempre caen acá porque data912/Binance no tienen
        // historial de velas para esas fuentes).
        $('detail-chart-loading').innerHTML = `<span class="hint">${err.response?.data?.error || err.message}</span>`;
        return;
    }
    loadTechnicalBacktest(instrumentId);
}

// --- Fundamental (P/E, márgenes, etc. vía yfinance) ------------------------

// Evalúa cada ratio por separado (no solo el combinado P/E+margen que ya usa
// el filtro de Señales) -- P/E y Margen neto usan el umbral REAL configurado
// en Preferencias (nunca inventado); el resto no tiene umbral propio
// configurable, así que usa un rango general de mercado, declarado como tal
// en el tooltip (nunca se presenta una guía general como si fuera un corte
// exacto calculado para este instrumento puntual).
export function evaluateFundamental(key, raw, config) {
    if (raw === null || raw === undefined) return { cls: '', note: '' };
    switch (key) {
        case 'pe_ratio': {
            const max = config.fa_max_pe;
            const pass = raw > 0 && raw < max;
            return { cls: pass ? 'cell-positive' : 'cell-negative', note: raw <= 0 ? 'negativo — la empresa está perdiendo plata' : `tu umbral: < ${fmt(max, 0)}` };
        }
        case 'profit_margin': {
            const min = config.fa_min_margin_pct;
            const pass = (raw * 100) > min;
            return { cls: pass ? 'cell-positive' : 'cell-negative', note: `tu umbral: > ${fmt(min, 0)}%` };
        }
        case 'forward_pe': {
            if (raw <= 0) return { cls: 'cell-negative', note: 'negativo — el mercado espera pérdidas' };
            if (raw < 25) return { cls: 'cell-positive', note: 'rango típico de mercado (10-25)' };
            if (raw < 40) return { cls: '', note: 'por encima del rango típico — puede ser crecimiento esperado' };
            return { cls: 'cell-negative', note: 'muy por encima del rango típico — posible sobrevaluación' };
        }
        case 'debt_to_equity': {
            if (raw < 100) return { cls: 'cell-positive', note: 'conservador (deuda < patrimonio)' };
            if (raw < 200) return { cls: '', note: 'apalancamiento moderado' };
            return { cls: 'cell-negative', note: 'apalancamiento alto (deuda > 2x patrimonio)' };
        }
        default:
            return { cls: '', note: '' };
    }
}

function fundamentalsHtml(data, config) {
    if (!data.available) {
        return `<p class="hint">${data.reason || 'Sin datos fundamentales disponibles.'}</p>`;
    }
    const cells = FUNDAMENTAL_FIELDS.map((f) => {
        const raw = data[f.key];
        let value = '—';
        if (raw !== null && raw !== undefined) {
            if (f.pct) value = fmtPct(raw * 100);
            else if (f.pctRaw) value = fmtPct(raw);
            else if (f.money) value = fmtMoney(raw, 'USD');
            else value = fmt(raw, 2);
        }
        const { cls, note } = evaluateFundamental(f.key, raw, config);
        return `
            <div class="detail-fundamental-item">
                <div class="label">${glossaryTerm(f.term, f.label)}</div>
                <div class="value num ${cls}">${value}</div>
                ${note ? `<div class="hint detail-fundamental-note">${note}</div>` : ''}
            </div>
        `;
    }).join('');
    return `
        <div class="detail-fundamentals-grid">${cells}</div>
        <p class="hint hint-spaced">${data.explanation}</p>
    `;
}

// Noticias y Sentimiento (ver conversación: "no hay sección de noticias" /
// "dentro de cada instrumento las que le afectan") -- mismo origen de
// datos que ya alimenta el filtro de riesgo preventivo del Motor Satélite
// (portfolio/nlp_agent.py::assess_event_risk), nunca una fuente separada.
const NEWS_RISK_TAG_CLASS = { BAJO: 'tag-positive', MEDIO: 'tag-warning', ALTO: 'tag-negative' };

const IMPACT_TAG_CLASS = { positivo: 'tag-positive', negativo: 'tag-negative', neutral: 'tag-neutral' };

// Cada titular es {"title", "url", "summary", "source", "published_at",
// "impact_score", "impact_direction", "impact_magnitude"} desde el backend
// (ver conversación: "debo poder entrar a las mismas" / "no puedo entrar a
// la tarjeta o fila... quiero que esté el impacto"). El titular se muestra
// como texto (no como link), el link de fuente queda aparte y chico, y
// hacer click en la fila abre un detalle con resumen + impacto estimado --
// headlines viejos ya persistidos como string, o sin esos campos, degradan
// a "sin evaluar" en el modal, nunca rompen el render.
function headlineItemHtml(h, idx) {
    const title = typeof h === 'object' && h !== null ? h.title : h;
    const url = typeof h === 'object' && h !== null ? h.url : null;
    return `
        <li class="detail-news-item" data-headline-idx="${idx}">
            <span class="detail-news-title" title="${title}">${title}</span>
            ${url ? `<a href="${url}" target="_blank" rel="noopener noreferrer" class="detail-news-source-link">Fuente ↗</a>` : ''}
        </li>
    `;
}

function newsDetailModalHtml(h) {
    const title = typeof h === 'object' && h !== null ? h.title : h;
    const url = typeof h === 'object' && h !== null ? h.url : null;
    const summary = typeof h === 'object' && h !== null ? h.summary : null;
    const source = typeof h === 'object' && h !== null ? h.source : null;
    const publishedAt = typeof h === 'object' && h !== null ? h.published_at : null;
    const direction = typeof h === 'object' && h !== null ? h.impact_direction : null;
    const magnitude = typeof h === 'object' && h !== null ? h.impact_magnitude : null;
    const metaParts = [source, publishedAt ? fmtDateTime(publishedAt) : null].filter(Boolean);
    const impactHtml = direction
        ? `<p><span class="tag ${IMPACT_TAG_CLASS[direction] || 'tag-neutral'}">${direction.toUpperCase()}</span> <span class="hint">impacto estimado: ${magnitude}</span></p>`
        : '<p class="hint">Impacto no evaluado para este titular (guardado antes de este análisis).</p>';
    // Secciones explícitas (ver conversación: "quiero que abra la noticia,
    // y tenga un resumen y la noticia como tal, y el impacto") -- la
    // noticia en sí es el título (lo más parecido a texto completo que da
    // la fuente gratuita, ver docstring de portfolio/nlp_agent.py: no hay
    // scraping del cuerpo completo del artículo), con Resumen e Impacto
    // etiquetados aparte para que no se lean como un solo bloque de texto.
    return `
        <div class="stat-detail-label" style="margin-top:0;">La noticia</div>
        <h3 style="margin:4px 0 8px;">${title}</h3>
        ${metaParts.length ? `<p class="hint">${metaParts.join(' · ')}</p>` : ''}
        <div class="stat-detail-label">Resumen</div>
        ${summary ? `<p>${summary}</p>` : '<p class="hint">Sin resumen disponible para este titular.</p>'}
        <div class="stat-detail-label">Impacto estimado</div>
        ${impactHtml}
        <div class="modal-footer">
            ${url ? `<a href="${url}" target="_blank" rel="noopener noreferrer" class="btn btn-secondary">Ver fuente ↗</a>` : ''}
            <button type="button" class="btn btn-secondary modal-close-btn">Cerrar</button>
        </div>
    `;
}

function openNewsDetailModal(ticker, h) {
    openModal({
        title: `${ticker} — noticia`,
        bodyHtml: newsDetailModalHtml(h),
        onMount: (form) => form.querySelector('.modal-close-btn')?.addEventListener('click', () => closeModal()),
    });
}

function newsHtml(data) {
    const riskTag = `<span class="tag ${NEWS_RISK_TAG_CLASS[data.risk_level] || 'tag-neutral'}">${data.risk_level}</span>`;
    const eventLine = data.event_date
        ? `<p class="hint">Próximo balance: ${fmtDate(data.event_date)} (en ${fmt(data.hours_until_event, 0)}hs)</p>`
        : '';
    const headlinesHtml = data.headlines.length
        ? `<ul class="detail-news-list">${data.headlines.map(headlineItemHtml).join('')}</ul>`
        : '<p class="table-empty">Sin titulares disponibles hoy.</p>';
    return `
        <div class="mini-stat-grid row">
            <div class="mini-stat"><div class="l">Riesgo de evento</div><div class="v">${riskTag}</div></div>
            <div class="mini-stat"><div class="l">Score de sentimiento</div><div class="v num">${fmt(data.sentiment_score, 2)}</div></div>
        </div>
        ${eventLine}
        <p class="hint">${data.summary}</p>
        ${headlinesHtml}
    `;
}

async function loadNews(instrumentId, ticker) {
    const root = $('detail-news');
    if (!root) return;
    root.innerHTML = loadingHtml('Buscando noticias…');
    try {
        const { data } = await api.instrumentNews(instrumentId);
        root.innerHTML = newsHtml(data);
        root.addEventListener('click', (e) => {
            if (e.target.closest('.detail-news-source-link')) return;
            const item = e.target.closest('.detail-news-item');
            if (!item) return;
            const headline = data.headlines[Number(item.dataset.headlineIdx)];
            if (headline) openNewsDetailModal(ticker, headline);
        });
    } catch (err) {
        root.innerHTML = `<p class="hint">Error: ${err.response?.data?.error || err.message}</p>`;
    }
}

async function loadFundamentals(instrumentId) {
    const root = $('detail-fundamentals');
    root.innerHTML = loadingHtml('Cargando fundamentales…');
    try {
        const [{ data }, { data: config }] = await Promise.all([
            api.instrumentFundamentals(instrumentId),
            api.signalConfig(),
        ]);
        root.innerHTML = fundamentalsHtml(data, config);
    } catch (err) {
        root.innerHTML = `<p class="hint">Error: ${err.response?.data?.error || err.message}</p>`;
    }
}

function rentaFijaHtml(data, events) {
    if (!data.available) {
        return `<p class="hint">${data.reason}</p>`;
    }
    const stats = [
        [glossaryTerm('tir', 'TIR'), fmtPct(data.tir_pct)],
        [glossaryTerm('duration', 'Duration'), data.duration_years !== null ? `${fmt(data.duration_years, 2)} años` : '—'],
    ];
    const eventsHtml = events.length
        ? events.map((e) => `
            <div class="stat-detail-row">
                <span>${e.kind === 'vencimiento' ? 'Vencimiento' : 'Cupón'} (${e.date})</span>
                <span class="num">en ${e.dias} día${e.dias === 1 ? '' : 's'}</span>
            </div>
        `).join('')
        : '<p class="hint">Sin pagos programados en el próximo año.</p>';

    return `
        <div class="detail-position-grid">
            ${stats.map(([label, value]) => `
                <div class="detail-position-item"><div class="label">${label}</div><div class="value">${value}</div></div>
            `).join('')}
        </div>
        <p class="hint" style="margin-top: 10px;">${data.explanation}</p>
        <div class="stat-detail-label" style="margin-top: 14px;">Próximos pagos</div>
        ${eventsHtml}
    `;
}

async function loadRentaFija(instrumentId, symbol) {
    const root = $('detail-renta-fija-root');
    root.innerHTML = loadingHtml('Cargando…');
    try {
        const [{ data }, { data: calendar }] = await Promise.all([
            api.instrumentFixedIncome(instrumentId),
            api.fixedIncomeCalendar(),
        ]);
        const events = calendar.filter((e) => e.symbol === symbol);
        root.innerHTML = rentaFijaHtml(data, events);
    } catch (err) {
        root.innerHTML = `<p class="hint">Error: ${err.response?.data?.error || err.message}</p>`;
    }
}

// Resumen de precisión histórica (MAPE/cobertura/acierto direccional) --
// antes era una tarjeta "Modelo" propia, ahora se funde en la tarjeta
// unificada "Análisis Cuantitativo Kronos" (ver analysisTiles). Se pide en
// paralelo con accuracy/calibration porque son dos endpoints distintos
// (/accuracy y /forecasts-history).
// Parámetros claros (ver conversación): MAPE/acierto direccional/calibración
// se calculan sobre pronósticos NO superpuestos pasados (ver
// portfolio/accuracy.py) -- cada uno con el horizonte vigente al momento de
// correrlo, que puede no ser el mismo que el configurado HOY si se cambió
// pred_len en Ajustes entretanto. currentPredLen (el horizonte vigente
// ahora) se muestra como referencia, con esa salvedad en el tooltip.
function mapeHorizonteNote(currentPredLen) {
    return currentPredLen
        ? `El horizonte configurado hoy es ${currentPredLen} ruedas — los pronósticos ya evaluados pueden haberse corrido con un horizonte distinto si ese valor cambió desde entonces.`
        : '';
}

function modelMetricsTilesHtml(acc, cal, currentPredLen) {
    if (!acc) return '';
    if (acc.pending) return '';
    const note = mapeHorizonteNote(currentPredLen);
    const horizonte = currentPredLen ? ` (~${currentPredLen}d)` : '';
    return `
        <div class="mini-stat"><div class="l">${glossaryTerm('mape', `MAPE${horizonte}`)}</div><div class="v num" title="${note}">${acc.mape}%</div></div>
        <div class="mini-stat">
            <div class="l">${glossaryTerm('calibracion_kronos', 'Cobertura p10-p90')}</div>
            <div class="v num">${cal ? `${cal.coverage_pct}%` : '—'}</div>
        </div>
        <div class="mini-stat">
            <div class="l">${glossaryTerm('acierto_direccion', `Acierto direccional${horizonte}`)}</div>
            <div class="v num" title="${note}">${acc.directional_hit_pct !== null ? `${acc.directional_hit_pct}%` : '—'}</div>
        </div>
    `;
}

async function loadModelMetrics(instrumentId) {
    try {
        const [{ data: acc }, { data: history }, { data: config }] = await Promise.all([
            api.instrumentAccuracy(instrumentId),
            api.forecastsHistory({ instrument_id: instrumentId }),
            api.signalConfig(),
        ]);
        analysisTiles.model = modelMetricsTilesHtml(acc, history?.calibration, config?.default_pred_len);
    } catch {
        analysisTiles.model = '';
    }
    renderAnalysisCard();
}

function accuracyStatStripHtml(data, currentPredLen) {
    const validated = data.validation?.validated;
    const note = mapeHorizonteNote(currentPredLen);
    const horizonte = currentPredLen ? ` (~${currentPredLen}d)` : '';
    return `
        <div class="stat-strip">
            <div class="stat"><div class="stat-label">${glossaryTerm('mape', `MAPE${horizonte}`)}</div><div class="stat-value num" title="${note}">${data.mape}%</div></div>
            <div class="stat">
                <div class="stat-label">${glossaryTerm('acierto_direccion', `Acierto direccional${horizonte}`)}</div>
                <div class="stat-value num" title="${note}">${data.directional_hit_pct !== null ? `${data.directional_hit_pct}%` : '—'}</div>
            </div>
            <div class="stat"><div class="stat-label">${glossaryTerm('pronosticos_no_superpuestos', 'Pronósticos no superpuestos')}</div><div class="stat-value num">${data.forecasts_evaluated}</div></div>
            <div class="stat">
                <div class="stat-label">Señal</div>
                <div class="stat-value num ${validated ? 'cell-positive' : 'cell-muted'}" title="${data.validation?.reason || ''}">
                    ${validated ? 'Habilitada' : 'Sin validar'}
                </div>
            </div>
        </div>
    `;
}

function backtestButtonHtml(label) {
    return `<button type="button" class="btn btn-secondary btn-spaced" id="detail-backtest-btn">${label}</button>
        <p class="hint hint-spaced" id="detail-backtest-status"></p>`;
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

// --- Histórico de pronósticos + Calibración (ver conversación: antes vivían
// en una vista propia "Kronos" dentro de Decisiones, con un selector de
// instrumento aparte -- ahora que esta misma página YA está fijada a un
// instrumento puntual, ese selector sobra: el contenido se muestra directo
// dentro de la pestaña Precisión). -----------------------------------------

const HISTORY_COLUMNS = [
    { key: 'created_at', label: 'Fecha', numeric: false },
    { key: 'pred_len', label: 'Horizonte', numeric: true, term: 'pasos_horizonte' },
    { key: 'median_pct', label: 'Predijo', numeric: true, term: 'mediana_pronostico' },
    { key: 'actual_change_pct', label: 'Pasó', numeric: true },
    { key: 'error_pct', label: 'Error abs.', numeric: true, term: 'mape' },
    { key: 'within_band', label: 'Dentro p10-p90', numeric: false, sortable: false, term: 'abanico_pronostico' },
];
// Mas de esto satura el grafico de lineas superpuestas sin aportar lectura --
// la tabla de abajo igual lista el historial completo, sin este recorte.
const FAN_MAX_OVERLAY = 8;
const historySorter = createSorter();
let historySelectedForecastId = null;
let historyData = null;

function forecastDate(f) {
    const ts = f.predicted?.[0]?.time;
    if (!ts) return null;
    return new Date(ts * 1000);
}

function fmtForecastDate(f) {
    const d = forecastDate(f);
    if (!d) return '—';
    return d.toLocaleDateString('es-AR', { day: '2-digit', month: '2-digit', year: 'numeric' });
}

function historySortValue(row, key) {
    if (key === 'created_at') return forecastDate(row)?.getTime() ?? 0;
    return row[key];
}

function historyForecastRowHtml(f) {
    const cells = HISTORY_COLUMNS.map((c) => {
        switch (c.key) {
            case 'created_at': return `<td>${fmtForecastDate(f)}</td>`;
            case 'pred_len': return `<td class="num">${f.pred_len}</td>`;
            case 'median_pct': return `<td class="num">${fmtPct(f.median_pct, 1, { signed: true })}</td>`;
            case 'actual_change_pct': return `<td class="num">${f.resolved ? fmtPct(f.actual_change_pct, 1, { signed: true }) : '<span class="hint">pendiente</span>'}</td>`;
            case 'error_pct': return `<td class="num">${f.error_pct !== null ? `${fmt(f.error_pct, 1)} pts` : '—'}</td>`;
            case 'within_band': {
                if (!f.resolved || f.within_band === null) return '<td><span class="hint">—</span></td>';
                return `<td><span class="${f.within_band ? 'cell-positive' : 'cell-negative'}">${f.within_band ? 'Sí' : 'No'}</span></td>`;
            }
            default: return '<td></td>';
        }
    });
    return `<tr class="kronos-forecast-row${f.id === historySelectedForecastId ? ' selected' : ''}" data-forecast-id="${f.id}">${cells.join('')}</tr>`;
}

function renderHistoryFanChart() {
    const container = $('detail-fan-chart');
    if (!container || !historyData) return;
    const overlay = [...historyData.forecasts]
        .sort((a, b) => (a.id === historySelectedForecastId ? 1 : b.id === historySelectedForecastId ? -1 : 0))
        .slice(0, FAN_MAX_OVERLAY);
    const chart = renderForecastFanChart(container, historyData.price_history, overlay, historySelectedForecastId);
    const zoomBtn = $('detail-fan-zoom-all');
    if (zoomBtn) zoomBtn.onclick = () => chart.timeScale().fitContent();
}

function historicoBlockHtml(symbol) {
    if (!historyData.forecasts.length) {
        return `<p class="hint">Todavía no hay pronósticos corridos para ${symbol}.</p>`;
    }
    const sorted = historySorter.key ? historySorter.apply(historyData.forecasts, historySortValue) : historyData.forecasts;
    return `
        <div style="display:flex; justify-content:space-between; align-items:flex-start; flex-wrap:wrap; gap:8px;">
            ${forecastFanLegendHtml()}
            <button type="button" class="btn btn-secondary btn-compact" id="detail-fan-zoom-all">Ver todo el histórico</button>
        </div>
        <div class="forecast-card-chart" id="detail-fan-chart"></div>
        <p class="hint" style="margin: 12px 0;">Click en una fila para resaltar ese pronóstico en el gráfico.</p>
        <div class="table-scroll">
            <table>
                <thead><tr>${HISTORY_COLUMNS.map((c) => headerCellHtml(c)).join('')}</tr></thead>
                <tbody id="detail-forecast-tbody">${sorted.map(historyForecastRowHtml).join('')}</tbody>
            </table>
        </div>
    `;
}

function calibracionBlockHtml(symbol) {
    const cal = historyData.calibration;
    if (!cal) {
        return `<p class="hint">Todavía no hay pronósticos no superpuestos y ya resueltos para ${symbol} — la calibración necesita al menos uno.</p>`;
    }
    const diff = cal.coverage_pct - cal.expected_pct;
    const cls = Math.abs(diff) <= 10 ? 'cell-positive' : 'cell-negative';
    return `
        <div class="kronos-calibration-tiles stat-strip">
            <div class="stat">
                <div class="stat-label">${glossaryTerm('calibracion_kronos', 'Cobertura p10-p90')}</div>
                <div class="stat-value num ${cls}">${fmt(cal.coverage_pct, 1)}%</div>
                <div class="stat-sub hint">Esperado ~${fmt(cal.expected_pct, 0)}%</div>
            </div>
            <div class="stat">
                <div class="stat-label">${glossaryTerm('pronosticos_no_superpuestos', 'Pronósticos evaluados')}</div>
                <div class="stat-value num">${cal.n}</div>
            </div>
        </div>
    `;
}

function wireHistoryInteractions(root, symbol) {
    const tbody = $('detail-forecast-tbody');
    if (!tbody) return;
    tbody.addEventListener('click', (e) => {
        const row = e.target.closest('.kronos-forecast-row');
        if (!row) return;
        const id = Number(row.dataset.forecastId);
        historySelectedForecastId = historySelectedForecastId === id ? null : id;
        tbody.querySelectorAll('.kronos-forecast-row').forEach((r) => {
            r.classList.toggle('selected', Number(r.dataset.forecastId) === historySelectedForecastId);
        });
        renderHistoryFanChart();
    });
    const historicoContainer = $('detail-historico-section');
    wireSortableHeaders(historicoContainer, historySorter, () => {
        historicoContainer.innerHTML = historicoBlockHtml(symbol);
        wireHistoryInteractions(root, symbol);
        renderHistoryFanChart();
    });
    updateSortIndicators(historicoContainer, historySorter);
}

async function loadAccuracy(instrumentId, symbol) {
    const root = $('detail-accuracy-root');
    root.innerHTML = loadingHtml('Cargando…');
    try {
        const [{ data }, { data: history }, { data: config }] = await Promise.all([
            api.instrumentAccuracy(instrumentId),
            api.forecastsHistory({ instrument_id: instrumentId }),
            api.signalConfig(),
        ]);
        historyData = history;
        historySelectedForecastId = null;
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
            ${accuracyStatStripHtml(data, config?.default_pred_len)}
            ${accuracyLegendHtml()}
            <div class="forecast-card-chart" id="detail-accuracy-chart"></div>
            <p class="hint hint-spaced">${data.explanation}</p>
            ${backtestButtonHtml('Ampliar backtest histórico')}
            <h3 class="detail-section-title">Histórico de pronósticos</h3>
            <div id="detail-historico-section">${historicoBlockHtml(symbol)}</div>
            <h3 class="detail-section-title">${glossaryTerm('calibracion_kronos', 'Calibración')}</h3>
            <div id="detail-calibracion-section">${calibracionBlockHtml(symbol)}</div>
        `;
        renderAccuracyChart($('detail-accuracy-chart'), data);
        wireBacktestButton(instrumentId, symbol);
        renderHistoryFanChart();
        wireHistoryInteractions(root, symbol);
    } catch (err) {
        root.innerHTML = `<p class="hint">Error: ${err.response?.data?.error || err.message}</p>`;
    }
}

export async function renderInstrumentDetail(hash) {
    const { symbol, instrumentId, market, assetType } = parseDetailRoute(hash);
    const root = $('instrument-detail-root');
    chartInstance = null;
    rsiInstance = null;
    macdInstance = null;
    analysisTiles = { model: '', forecast: '', extra: '', technicalBacktest: '' };
    lastSuggestedLevelsCtx = null;

    const { data: holdings } = await api.holdings();
    const holding = holdings.find((h) => h.instrument.symbol === symbol);
    const resolvedType = holding ? holding.instrument.type : assetType;
    const resolvedInstrumentId = holding ? holding.instrument.id : instrumentId;
    const resolvedMarket = holding ? holding.instrument.market : market;

    const tabs = [{ id: 'pronostico', label: 'Pronóstico' }];
    if (resolvedInstrumentId) tabs.push({ id: 'precision', label: 'Precisión' });
    if (resolvedInstrumentId && ['bono', 'on'].includes(resolvedType)) tabs.push({ id: 'renta-fija', label: 'Renta fija' });

    root.innerHTML = shellHtml(symbol, tabs);
    wireTabs(root);
    // Fase 1: "atrás" real (vuelve al scroll/estado exacto de la lista, ver
    // tabs.js::handleScroll) si hay historial interno (goBack hace
    // history.back()); si no (link compartido/recarga directa), goBack cae a
    // href="#instrumentos" -- el mismo destino que el link ya tiene de
    // fallback, por eso preventDefault siempre (nunca navegación duplicada).
    $('detail-breadcrumb-back')?.addEventListener('click', (e) => {
        e.preventDefault();
        goBack('#instrumentos');
    });

    // Delegado (no por-tile): detail-forecast-kpis se reconstruye entero en
    // cada renderAnalysisCard (ver loadSuggestedLevels) -- un listener puesto
    // directo en el botón se perdería apenas se re-pinta.
    $('detail-forecast-kpis').addEventListener('click', (e) => {
        const btn = e.target.closest('.mini-stat-action-btn');
        if (!btn || !resolvedInstrumentId) return;
        if (btn.dataset.action === 'quitar') {
            quitarNivelSugerido(btn.dataset.tipo, Number(btn.dataset.alertId));
        } else {
            fijarNivelSugerido(resolvedInstrumentId, btn.dataset.tipo, Number(btn.dataset.nivel));
        }
    });

    $('detail-type-tag').textContent = TYPE_LABELS[resolvedType] || resolvedType || '';

    // Posición + Riesgo: ya no son pestañas, se pintan siempre (vacío si no
    // hay holding -- ver DESIGN_SYSTEM.md).
    $('detail-position-bar').innerHTML = holding ? positionHtml(holding) : '';

    const precisionBtn = root.querySelector('.subtab-btn[data-tab="precision"]');
    if (precisionBtn && resolvedInstrumentId) {
        let accuracyLoaded = false;
        precisionBtn.addEventListener('click', () => {
            if (accuracyLoaded) return;
            accuracyLoaded = true;
            loadAccuracy(resolvedInstrumentId, symbol);
        });
    }

    const rentaFijaBtn = root.querySelector('.subtab-btn[data-tab="renta-fija"]');
    if (rentaFijaBtn && resolvedInstrumentId) {
        let rentaFijaLoaded = false;
        rentaFijaBtn.addEventListener('click', () => {
            if (rentaFijaLoaded) return;
            rentaFijaLoaded = true;
            loadRentaFija(resolvedInstrumentId, symbol);
        });
    }

    if (resolvedInstrumentId) {
        try {
            const { data } = await api.instrumentOverlays(resolvedInstrumentId);
            currentOverlays = { trendlines: data.trendlines, priceAlerts: data.price_alerts };
        } catch {
            currentOverlays = { trendlines: { resistance: [], support: [], breakout: null }, priceAlerts: [] };
        }
        loadPriceAlertsPanel(resolvedInstrumentId);
        loadFundamentals(resolvedInstrumentId);
        loadNews(resolvedInstrumentId, symbol);
        loadModelMetrics(resolvedInstrumentId);
        await loadChartFast(resolvedInstrumentId, holding?.instrument.currency || 'ARS');
        const hasForecast = await checkKronosStatus({ symbol, instrumentId: resolvedInstrumentId, market: resolvedMarket });
        if (!hasForecast) await runKronosForecast({ symbol, instrumentId: resolvedInstrumentId, market: resolvedMarket, assetType: resolvedType });
    } else {
        // Catálogo unificado, instrumento todavía no agregado a la cartera:
        // no hay price_history propio para un gráfico instantáneo, así que se
        // dispara Kronos directo (ya no hace falta un click manual, ver
        // conversación) -- puede tardar ~70s, el status de abajo del badge
        // avisa mientras corre.
        $('detail-fundamentals').innerHTML = '<p class="hint">Agregá este instrumento a tu cartera para ver fundamentales e indicadores técnicos.</p>';
        analysisTiles.forecast = '';
        renderAnalysisCard();
        $('detail-chart-loading').innerHTML = '<span class="hint">Sin gráfico disponible — agregá este instrumento a tu cartera para generar su historial de precios.</span>';
        const hasForecast = await checkKronosStatus({ symbol, instrumentId: resolvedInstrumentId, market: resolvedMarket });
        if (!hasForecast) await runKronosForecast({ symbol, instrumentId: resolvedInstrumentId, market: resolvedMarket, assetType: resolvedType });
    }
}

document.addEventListener('theme-changed', () => {
    if (location.hash.slice(1).startsWith('detalle/')) renderInstrumentDetail(location.hash.slice(1));
});
