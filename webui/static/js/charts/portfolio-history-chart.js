import { observeChartResize } from './lw-chart-utils.js';
import { glossaryTerm } from '../shared/glossary.js';
import { getChartPalette, bindChartTheme } from '../shared/chart-palette.js';

// Antes hardcodeado en hex y congelado al tema activo en el momento del
// import -- ver DESIGN_SYSTEM.md hallazgo 1/2. "real"/"teorico" son nombres
// de dominio de este chart (no existen como tales en tokens.css), mapeados
// a los colores semanticos positive/accent.
function seriesColors(palette = getChartPalette()) {
    return { grid: palette.grid, text: palette.text, real: palette.positive, teorico: palette.accent };
}

export function portfolioHistoryLegendHtml() {
    const colors = seriesColors();
    return `
        <div class="chart-legend">
            <span class="chart-legend-item"><span class="chart-legend-swatch" style="background:${colors.real}"></span>Real</span>
            <span class="chart-legend-item"><span class="chart-legend-swatch" style="background:${colors.teorico}"></span>${glossaryTerm('teorico_kronos', 'Teórico (si Kronos hubiera decidido)')}</span>
        </div>`;
}

export function renderPortfolioHistoryChart(container, data) {
    container.innerHTML = '';
    const series = data?.series || [];
    if (series.length < 2) {
        container.innerHTML = `<p class="hint portfolio-history-empty">Todavía no hay suficientes transacciones con histórico de precios real para reconstruir la evolución de tu cartera.</p>`;
        return null;
    }

    const colors = seriesColors();

    const chart = LightweightCharts.createChart(container, {
        width: container.clientWidth,
        height: 320,
        layout: { background: { color: 'transparent' }, textColor: colors.text, attributionLogo: false },
        grid: { vertLines: { color: colors.grid }, horzLines: { color: colors.grid } },
        timeScale: { borderColor: colors.grid },
        rightPriceScale: { borderColor: colors.grid },
        crosshair: {
            horzLine: { labelVisible: false },
            vertLine: { labelVisible: false },
        },
    });

    const real = chart.addSeries(LightweightCharts.LineSeries, {
        color: colors.real, lineWidth: 2, lastValueVisible: false, priceLineVisible: false,
    });
    real.setData(series.map((s) => ({ time: s.date, value: s.valor_real })));

    const teoricoPoints = series
        .filter((s) => s.valor_teorico !== null && s.valor_teorico !== undefined)
        .map((s) => ({ time: s.date, value: s.valor_teorico }));
    let teorico = null;
    if (teoricoPoints.length) {
        teorico = chart.addSeries(LightweightCharts.LineSeries, {
            color: colors.teorico, lineWidth: 2, lineStyle: LightweightCharts.LineStyle.Dashed,
            lastValueVisible: false, priceLineVisible: false,
        });
        teorico.setData(teoricoPoints);
    }

    chart.timeScale().fitContent();
    observeChartResize(chart, container);

    // Mismos datos, solo reaplica colores -- no hace falta re-pedir ni
    // re-armar series al cambiar de tema (ver chart-palette.js).
    bindChartTheme(container, (palette) => {
        const next = seriesColors(palette);
        chart.applyOptions({
            layout: { textColor: next.text },
            grid: { vertLines: { color: next.grid }, horzLines: { color: next.grid } },
            timeScale: { borderColor: next.grid },
            rightPriceScale: { borderColor: next.grid },
        });
        real.applyOptions({ color: next.real });
        teorico?.applyOptions({ color: next.teorico });
    });

    return chart;
}
