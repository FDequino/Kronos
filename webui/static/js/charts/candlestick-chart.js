import { fmt } from '../shared/format.js';
import { observeChartResize } from './lw-chart-utils.js';

const PALETTE = {
    grid: '#242933',
    text: '#8a93a3',
    positive: '#30c177',
    negative: '#ef5b5b',
    accent: '#4c7dff',
    accentStrong: '#759bff',
    warning: '#e0a63c',
};

const RANGE_PRESETS = [
    { key: '1m', label: '1M', months: 1 },
    { key: '3m', label: '3M', months: 3 },
    { key: '6m', label: '6M', months: 6 },
    { key: '1a', label: '1A', months: 12 },
    { key: 'todo', label: 'Todo', months: null },
];

const DAY_SECONDS = 24 * 3600;

function baseChartOptions(container, height) {
    return {
        width: container.clientWidth,
        height,
        layout: { background: { color: 'transparent' }, textColor: PALETTE.text, attributionLogo: false },
        grid: { vertLines: { color: PALETTE.grid }, horzLines: { color: PALETTE.grid } },
        timeScale: { borderColor: PALETTE.grid },
        rightPriceScale: { borderColor: PALETTE.grid },
    };
}

function zipSeries(candles, values) {
    return candles
        .map((c, i) => ({ time: c.time, value: values[i] }))
        .filter((p) => p.value !== null && p.value !== undefined && !Number.isNaN(p.value));
}

export function chartLegendHtml() {
    return `
        <div class="chart-legend">
            <span class="chart-legend-item"><span class="chart-legend-swatch" style="background:${PALETTE.positive}"></span>Real</span>
            <span class="chart-legend-item"><span class="chart-legend-swatch" style="background:${PALETTE.warning}"></span>SMA 20</span>
            <span class="chart-legend-item"><span class="chart-legend-swatch" style="background:${PALETTE.accentStrong}"></span>EMA 20</span>
            <span class="chart-legend-item"><span class="chart-legend-swatch" style="background:${PALETTE.accent}"></span>Predicho</span>
        </div>`;
}

export function accuracyLegendHtml() {
    return `
        <div class="chart-legend">
            <span class="chart-legend-item"><span class="chart-legend-swatch" style="background:${PALETTE.positive}"></span>Real</span>
            <span class="chart-legend-item"><span class="chart-legend-swatch" style="background:${PALETTE.accent}"></span>Predicho en su momento</span>
        </div>`;
}

export function renderAccuracyChart(container, data) {
    container.innerHTML = '';
    container.style.position = 'relative';

    const chart = LightweightCharts.createChart(container, baseChartOptions(container, 320));

    const candles = chart.addSeries(LightweightCharts.CandlestickSeries, {
        upColor: PALETTE.positive, downColor: PALETTE.negative,
        borderUpColor: PALETTE.positive, borderDownColor: PALETTE.negative,
        wickUpColor: PALETTE.positive, wickDownColor: PALETTE.negative,
    });
    candles.setData(data.historical);

    const predicted = chart.addSeries(LightweightCharts.LineSeries, {
        lastValueVisible: false, priceLineVisible: false,
        color: PALETTE.accent, lineWidth: 2, lineStyle: LightweightCharts.LineStyle.Dashed,
    });
    predicted.setData(data.predicted_points.map((p) => ({ time: p.time, value: p.predicted })));

    chart.timeScale().fitContent();
    observeChartResize(chart, container);
    return chart;
}

export function rangeControlsHtml() {
    return `<div class="chart-range-controls">${RANGE_PRESETS.map((r, i) => `
        <button class="chart-range-btn${i === RANGE_PRESETS.length - 1 ? ' active' : ''}" data-range="${r.key}" type="button">${r.label}</button>
    `).join('')}</div>`;
}

export function wireRangeControls(root, chart, forecast) {
    const buttons = [...root.querySelectorAll('.chart-range-btn')];
    if (!forecast.historical.length) return;
    const lastHistorical = forecast.historical[forecast.historical.length - 1].time;
    const firstHistorical = forecast.historical[0].time;
    const lastPredicted = forecast.predicted.length
        ? forecast.predicted[forecast.predicted.length - 1].time
        : lastHistorical;

    buttons.forEach((btn) => {
        btn.addEventListener('click', () => {
            buttons.forEach((b) => b.classList.toggle('active', b === btn));
            const preset = RANGE_PRESETS.find((r) => r.key === btn.dataset.range);
            if (preset.months === null) {
                chart.timeScale().fitContent();
                return;
            }
            const from = Math.max(firstHistorical, lastHistorical - preset.months * 30 * DAY_SECONDS);
            chart.timeScale().setVisibleRange({ from, to: lastPredicted });
        });
    });
}

function buildTimeIndexes(forecast) {
    const historicalIndex = new Map();
    forecast.historical.forEach((c, i) => historicalIndex.set(c.time, i));
    const predictedIndex = new Map();
    forecast.predicted.forEach((c, i) => predictedIndex.set(c.time, i));
    return { historicalIndex, predictedIndex };
}

function formatTooltipDate(time) {
    return new Date(time * 1000).toLocaleDateString('es-AR', { day: '2-digit', month: 'short', year: 'numeric' });
}

function tooltipHtml(forecast, indexes, time) {
    if (indexes.historicalIndex.has(time)) {
        const i = indexes.historicalIndex.get(time);
        const c = forecast.historical[i];
        const sma = forecast.indicators.sma_20[i];
        const ema = forecast.indicators.ema_20[i];
        const rsi = forecast.indicators.rsi_14[i];
        return `
            <div class="chart-tooltip-date">${formatTooltipDate(time)}</div>
            <div class="chart-tooltip-row"><span>O</span><span class="num">${fmt(c.open)}</span></div>
            <div class="chart-tooltip-row"><span>H</span><span class="num">${fmt(c.high)}</span></div>
            <div class="chart-tooltip-row"><span>L</span><span class="num">${fmt(c.low)}</span></div>
            <div class="chart-tooltip-row"><span>C</span><span class="num">${fmt(c.close)}</span></div>
            ${sma != null ? `<div class="chart-tooltip-row"><span>SMA 20</span><span class="num">${fmt(sma)}</span></div>` : ''}
            ${ema != null ? `<div class="chart-tooltip-row"><span>EMA 20</span><span class="num">${fmt(ema)}</span></div>` : ''}
            ${rsi != null ? `<div class="chart-tooltip-row"><span>RSI 14</span><span class="num">${fmt(rsi)}</span></div>` : ''}
        `;
    }
    if (indexes.predictedIndex.has(time)) {
        const i = indexes.predictedIndex.get(time);
        return `
            <div class="chart-tooltip-date">${formatTooltipDate(time)}</div>
            <div class="chart-tooltip-row"><span>Predicho</span><span class="num">${fmt(forecast.predicted[i].close)}</span></div>
        `;
    }
    return '';
}

function attachTooltip(container, chart, forecast) {
    const tooltip = document.createElement('div');
    tooltip.className = 'chart-tooltip';
    container.appendChild(tooltip);
    const indexes = buildTimeIndexes(forecast);

    chart.subscribeCrosshairMove((param) => {
        if (!param.point || param.time === undefined || param.point.x < 0 || param.point.y < 0) {
            tooltip.classList.remove('visible');
            return;
        }
        const html = tooltipHtml(forecast, indexes, param.time);
        if (!html) {
            tooltip.classList.remove('visible');
            return;
        }
        tooltip.innerHTML = html;
        tooltip.classList.add('visible');
        const maxLeft = Math.max(container.clientWidth - tooltip.offsetWidth - 8, 8);
        tooltip.style.left = `${Math.min(param.point.x + 16, maxLeft)}px`;
        tooltip.style.top = `${Math.max(param.point.y - 16, 8)}px`;
    });
}

export function renderForecastChart(container, forecast) {
    container.innerHTML = '';
    container.style.position = 'relative';

    const chart = LightweightCharts.createChart(container, baseChartOptions(container, 360));

    const candles = chart.addSeries(LightweightCharts.CandlestickSeries, {
        upColor: PALETTE.positive, downColor: PALETTE.negative,
        borderUpColor: PALETTE.positive, borderDownColor: PALETTE.negative,
        wickUpColor: PALETTE.positive, wickDownColor: PALETTE.negative,
    });
    candles.setData(forecast.historical);

    // Solo las velas llevan price-line/etiqueta de ultimo valor; el resto
    // informa por color para que las etiquetas no se amontonen unas con otras.
    const seriesOptions = { lastValueVisible: false, priceLineVisible: false };

    const predicted = chart.addSeries(LightweightCharts.LineSeries, {
        ...seriesOptions, color: PALETTE.accent, lineWidth: 2, lineStyle: LightweightCharts.LineStyle.Dashed,
    });
    predicted.setData(forecast.predicted.map((c) => ({ time: c.time, value: c.close })));

    const sma = chart.addSeries(LightweightCharts.LineSeries, { ...seriesOptions, color: PALETTE.warning, lineWidth: 1 });
    sma.setData(zipSeries(forecast.historical, forecast.indicators.sma_20));

    const ema = chart.addSeries(LightweightCharts.LineSeries, { ...seriesOptions, color: PALETTE.accentStrong, lineWidth: 1 });
    ema.setData(zipSeries(forecast.historical, forecast.indicators.ema_20));

    chart.timeScale().fitContent();
    attachTooltip(container, chart, forecast);
    observeChartResize(chart, container);
    return chart;
}

export function renderRsiPane(container, forecast) {
    container.innerHTML = '';

    const chart = LightweightCharts.createChart(container, {
        ...baseChartOptions(container, 120),
        timeScale: { visible: false },
    });

    const rsi = chart.addSeries(LightweightCharts.LineSeries, { color: PALETTE.accentStrong, lineWidth: 1 });
    rsi.setData(zipSeries(forecast.historical, forecast.indicators.rsi_14));

    chart.timeScale().fitContent();
    observeChartResize(chart, container);
    return chart;
}
