import { observeChartResize } from './lw-chart-utils.js';

const PALETTE = { grid: '#242933', text: '#8a93a3', real: '#30c177', teorico: '#4c7dff' };

export function portfolioHistoryLegendHtml() {
    return `
        <div class="chart-legend">
            <span class="chart-legend-item"><span class="chart-legend-swatch" style="background:${PALETTE.real}"></span>Real</span>
            <span class="chart-legend-item"><span class="chart-legend-swatch" style="background:${PALETTE.teorico}"></span>Teórico (si Kronos hubiera decidido)</span>
        </div>`;
}

export function renderPortfolioHistoryChart(container, data) {
    container.innerHTML = '';
    const series = data?.series || [];
    if (series.length < 2) {
        container.innerHTML = `<p class="hint portfolio-history-empty">Todavía no hay suficientes transacciones con histórico de precios real para reconstruir la evolución de tu cartera.</p>`;
        return null;
    }

    const chart = LightweightCharts.createChart(container, {
        width: container.clientWidth,
        height: 320,
        layout: { background: { color: 'transparent' }, textColor: PALETTE.text, attributionLogo: false },
        grid: { vertLines: { color: PALETTE.grid }, horzLines: { color: PALETTE.grid } },
        timeScale: { borderColor: PALETTE.grid },
        rightPriceScale: { borderColor: PALETTE.grid },
        crosshair: {
            horzLine: { labelVisible: false },
            vertLine: { labelVisible: false },
        },
    });

    const real = chart.addSeries(LightweightCharts.LineSeries, {
        color: PALETTE.real, lineWidth: 2, lastValueVisible: false, priceLineVisible: false,
    });
    real.setData(series.map((s) => ({ time: s.date, value: s.valor_real })));

    const teoricoPoints = series
        .filter((s) => s.valor_teorico !== null && s.valor_teorico !== undefined)
        .map((s) => ({ time: s.date, value: s.valor_teorico }));
    if (teoricoPoints.length) {
        const teorico = chart.addSeries(LightweightCharts.LineSeries, {
            color: PALETTE.teorico, lineWidth: 2, lineStyle: LightweightCharts.LineStyle.Dashed,
            lastValueVisible: false, priceLineVisible: false,
        });
        teorico.setData(teoricoPoints);
    }

    chart.timeScale().fitContent();
    observeChartResize(chart, container);
    return chart;
}
