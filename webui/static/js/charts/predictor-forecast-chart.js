import { fmt } from '../shared/format.js';
import { observeChartResize } from './lw-chart-utils.js';
import { getChartPalette, bindChartTheme, withAlpha } from '../shared/chart-palette.js';

// Chart del Predictor Kronos (pagina standalone `/`, fuera de la cartera --
// ver DESIGN.md) -- reemplaza al Plotly que arma webui/app.py (ver
// build_prediction_chart_data). No reutiliza renderForecastChart de
// candlestick-chart.js: esta pagina no tiene SMA/EMA/Bollinger/RSI/MACD ni
// banda p10-p90, y sus 3 series (historico/prediccion/real) son velas OHLC
// completas, no una linea de cierre -- mezclar ambos casos en una sola
// funcion dejaria un monton de parametros sin uso real de un lado o del otro.
// Si reutiliza el helper generico de resize/zoom (observeChartResize) y la
// paleta de colores/tema (shared/chart-palette.js), que si generalizan.

function seriesColors(palette, tone) {
    const { up, down } = tone(palette);
    return {
        upColor: up, downColor: down,
        borderUpColor: up, borderDownColor: down,
        wickUpColor: up, wickDownColor: down,
    };
}

// Igual esquema de 3 tramos que tenia el Plotly original (cada tramo con su
// propio par de colores), pero con tokens de diseño en vez de hex
// hardcodeado -- historico en positive/negative (igual que cualquier otra
// vela real de la app), prediccion en acento (para que se lea como "esto lo
// dijo el modelo, no paso todavia"), real-de-comparacion en warning (un tercer
// tono, distinto de los otros dos, para no confundirlo con el historico
// aunque sea el mismo tipo de dato real).
const HISTORICAL_TONE = (p) => ({ up: p.positive, down: p.negative });
const PREDICTION_TONE = (p) => ({ up: p.accent, down: p.accentStrong });
const ACTUAL_TONE = (p) => ({ up: p.warning, down: withAlpha(p.warning, 0.55) });

function legendHtml(hasActual) {
    const palette = getChartPalette();
    const item = (color, label) => `
        <span class="chart-legend-item"><span class="chart-legend-swatch" style="background:${color}"></span>${label}</span>`;
    return `
        <div class="chart-legend">
            ${item(palette.positive, 'Histórico')}
            ${item(palette.accent, 'Predicción (Kronos)')}
            ${hasActual ? item(palette.warning, 'Real (comparación)') : ''}
        </div>`;
}

function buildIndex(candles) {
    const map = new Map();
    (candles || []).forEach((c, i) => map.set(c.time, i));
    return map;
}

function formatTooltipDate(time) {
    return new Date(time * 1000).toLocaleString('es-AR', {
        day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit',
    });
}

// Indexa directo en los mismos arrays que arman las series (nunca vuelve a
// recorrer param.seriesData) -- mismo patron que candlestick-chart.js::
// tooltipHtml. Prediccion y real comparten los mismos timestamps (se grafican
// una sobre la otra a proposito, para comparar), asi que un mismo `time`
// puede pertenecer a ambas a la vez -- el tooltip muestra las dos.
function tooltipHtml(data, indexes, time) {
    if (indexes.historicalIndex.has(time)) {
        const c = data.historical[indexes.historicalIndex.get(time)];
        return `
            <div class="chart-tooltip-date">${formatTooltipDate(time)}</div>
            <div class="chart-tooltip-ohlc">
                <span>O <b class="num">${fmt(c.open)}</b></span>
                <span>H <b class="num">${fmt(c.high)}</b></span>
                <span>L <b class="num">${fmt(c.low)}</b></span>
                <span>C <b class="num">${fmt(c.close)}</b></span>
            </div>`;
    }

    const predIdx = indexes.predictionIndex.get(time);
    const actualIdx = indexes.actualIndex.get(time);
    if (predIdx === undefined && actualIdx === undefined) return '';

    const rows = [];
    if (predIdx !== undefined) {
        const c = data.prediction[predIdx];
        rows.push(`<span>Predicho O/H/L/C <b class="num">${fmt(c.open)}/${fmt(c.high)}/${fmt(c.low)}/${fmt(c.close)}</b></span>`);
    }
    if (actualIdx !== undefined) {
        const c = data.actual[actualIdx];
        rows.push(`<span>Real O/H/L/C <b class="num">${fmt(c.open)}/${fmt(c.high)}/${fmt(c.low)}/${fmt(c.close)}</b></span>`);
    }
    return `
        <div class="chart-tooltip-date">${formatTooltipDate(time)}</div>
        <div class="chart-tooltip-extra">${rows.join(' · ')}</div>`;
}

function attachTooltip(container, chart, data) {
    const tooltip = document.createElement('div');
    tooltip.className = 'chart-tooltip';
    container.appendChild(tooltip);
    const indexes = {
        historicalIndex: buildIndex(data.historical),
        predictionIndex: buildIndex(data.prediction),
        actualIndex: buildIndex(data.actual),
    };

    chart.subscribeCrosshairMove((param) => {
        if (!param.point || param.time === undefined || param.point.x < 0 || param.point.y < 0) {
            tooltip.classList.remove('visible');
            return;
        }
        const html = tooltipHtml(data, indexes, param.time);
        if (!html) {
            tooltip.classList.remove('visible');
            return;
        }
        tooltip.innerHTML = html;
        tooltip.classList.add('visible');
    });
}

// `container` es el <div id="chart"> ya existente en index.html (alto fijo
// en CSS, nunca display:none -- ver DESIGN.md gotcha de clientWidth/0). Se le
// agregan 2 hijos (leyenda + el div que lightweight-charts realmente toma),
// repartiendo el mismo alto fijo entre los dos -- así el tamaño del card no
// cambia por migrar de libreria (la leyenda ya ocupaba lugar adentro del
// propio chart en el Plotly original, vía showlegend:true).
export function renderPredictorChart(container, data) {
    container.innerHTML = '';
    container.style.position = 'relative';

    const hasActual = !!(data.actual && data.actual.length);
    const legend = document.createElement('div');
    legend.innerHTML = legendHtml(hasActual);
    container.appendChild(legend);

    const chartEl = document.createElement('div');
    chartEl.style.position = 'relative';
    container.appendChild(chartEl);

    const palette = getChartPalette();
    const chartHeight = Math.max(200, container.clientHeight - legend.offsetHeight);
    const chart = LightweightCharts.createChart(chartEl, {
        width: chartEl.clientWidth,
        height: chartHeight,
        layout: { background: { color: 'transparent' }, textColor: palette.text, attributionLogo: false },
        grid: { vertLines: { color: palette.grid }, horzLines: { color: palette.grid } },
        timeScale: { borderColor: palette.grid },
        rightPriceScale: { borderColor: palette.grid },
    });

    const historical = chart.addSeries(LightweightCharts.CandlestickSeries, seriesColors(palette, HISTORICAL_TONE));
    historical.setData(data.historical);

    // Prediccion y real no llevan price-line/etiqueta propia -- ya hay 3
    // series candlestick compartiendo el mismo panel de precio, y con las 3
    // mostrando su ultimo valor las etiquetas se apilarian una sobre otra.
    const prediction = chart.addSeries(LightweightCharts.CandlestickSeries, {
        ...seriesColors(palette, PREDICTION_TONE), lastValueVisible: false, priceLineVisible: false,
    });
    prediction.setData(data.prediction);

    let actual = null;
    if (hasActual) {
        actual = chart.addSeries(LightweightCharts.CandlestickSeries, {
            ...seriesColors(palette, ACTUAL_TONE), lastValueVisible: false, priceLineVisible: false,
        });
        actual.setData(data.actual);
    }

    chart.timeScale().fitContent();
    attachTooltip(chartEl, chart, data);
    observeChartResize(chart, chartEl);

    bindChartTheme(container, (next) => {
        chart.applyOptions({
            layout: { textColor: next.text },
            grid: { vertLines: { color: next.grid }, horzLines: { color: next.grid } },
            timeScale: { borderColor: next.grid },
            rightPriceScale: { borderColor: next.grid },
        });
        historical.applyOptions(seriesColors(next, HISTORICAL_TONE));
        prediction.applyOptions(seriesColors(next, PREDICTION_TONE));
        if (actual) actual.applyOptions(seriesColors(next, ACTUAL_TONE));
    });

    return chart;
}
