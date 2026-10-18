import { observeChartResize } from './lw-chart-utils.js';
import { fmtPct } from '../shared/format.js';
import { glossaryTerm } from '../shared/glossary.js';
import { getChartPalette, bindChartTheme } from '../shared/chart-palette.js';

// Antes hardcodeado en hex y congelado al tema activo en el momento del
// import -- ver DESIGN_SYSTEM.md hallazgo 1/2. cartera/caucion/inflacion/spy
// son nombres de dominio de este chart (no existen como tales en
// tokens.css), mapeados a los colores semanticos del tema activo.
export function seriesColors(palette = getChartPalette()) {
    return {
        grid: palette.grid, text: palette.text,
        cartera: palette.accent, caucion: palette.warning, inflacion: palette.negative, spy: palette.positive,
    };
}


const PERIODS = [
    { key: '1m', label: '1M' },
    { key: '3m', label: '3M' },
    { key: 'ytd', label: 'YTD' },
    { key: '1a', label: '1A' },
    { key: 'inicio', label: 'Desde inicio' },
];

export function periodLabel(key) {
    return PERIODS.find((p) => p.key === key)?.label || key;
}

// Fase 2 (ver conversación, sistema de diseño unificado): esto es FILTRO/
// SELECCIÓN (elige qué período de datos mostrar en el gráfico de abajo, no
// cambia de vista) -- mismo componente que el segmented-toggle de Moneda/
// Fuente USD en Ajustes, deliberadamente DISTINTO en estilo (fill sólido) de
// las tabs de navegación (.subtab-btn, borde+accent-dim) para que los dos
// roles se distingan a simple vista.
export function benchmarkPeriodControlsHtml(active = 'inicio') {
    return PERIODS.map((p) => `
        <button class="segmented-toggle-option${p.key === active ? ' active' : ''}" data-period="${p.key}" type="button">${p.label}</button>
    `).join('');
}

export function wireBenchmarkPeriodControls(root, onSelect) {
    const buttons = [...root.querySelectorAll('.segmented-toggle-option')];
    buttons.forEach((btn) => {
        btn.addEventListener('click', () => {
            buttons.forEach((b) => b.classList.toggle('active', b === btn));
            onSelect(btn.dataset.period);
        });
    });
}

export function benchmarkComparisonLegendHtml() {
    const colors = seriesColors();
    return `
        <div class="chart-legend">
            <span class="chart-legend-item"><span class="chart-legend-swatch" style="background:${colors.cartera}"></span>Cartera (${glossaryTerm('twr', 'TWR')})</span>
            <span class="chart-legend-item"><span class="chart-legend-swatch" style="background:${colors.caucion}"></span>${glossaryTerm('benchmark_caucion', 'Caución')}</span>
            <span class="chart-legend-item"><span class="chart-legend-swatch" style="background:${colors.spy}"></span>${glossaryTerm('spy_benchmark', 'SPY (USD)')}</span>
        </div>`;
}

export function benchmarkStatStripHtml(data, periodKey) {
    const colors = seriesColors();
    const items = [
        ['Cartera (TWR)', data.cartera?.periods?.[periodKey], colors.cartera],
        ['Caución', data.caucion?.available ? data.caucion.periods?.[periodKey] : null, colors.caucion],
        ['Inflación', data.inflacion?.available ? data.inflacion.periods?.[periodKey] : null, colors.inflacion],
        ['SPY (USD)', data.spy?.available ? data.spy.periods?.[periodKey] : null, colors.spy],
    ];
    return `
        <div class="stat-strip">
            ${items.map(([label, period, color]) => `
                <div class="stat">
                    <div class="stat-label"><span class="chart-legend-swatch" style="background:${color}"></span> ${label}</div>
                    <div class="stat-value num">${period ? fmtPct(period.twr_pct) : '—'}</div>
                </div>
            `).join('')}
        </div>
    `;
}

function _sliceAndRebase(indexSeries, fromDate) {
    const startIdx = indexSeries.findIndex((p) => p.date >= fromDate);
    if (startIdx === -1) return [];
    const base = indexSeries[startIdx].index;
    if (!base) return [];
    return indexSeries.slice(startIdx).map((p) => ({
        time: Math.floor(new Date(`${p.date}T00:00:00Z`).getTime() / 1000),
        value: (p.index / base) * 100,
    }));
}

export function renderBenchmarkComparisonChart(container, data, periodKey) {
    container.innerHTML = '';
    const carteraSeries = data?.cartera?.index_series || [];
    if (carteraSeries.length < 2) {
        container.innerHTML = '<p class="hint">Todavía no hay suficiente historial para comparar rendimiento.</p>';
        return null;
    }

    const periodInfo = data.cartera.periods?.[periodKey];
    const fromDate = periodInfo?.from_date || carteraSeries[0].date;

    const colors = seriesColors();

    const chart = LightweightCharts.createChart(container, {
        width: container.clientWidth,
        height: 320,
        layout: { background: { color: 'transparent' }, textColor: colors.text, attributionLogo: false },
        grid: { vertLines: { color: colors.grid }, horzLines: { color: colors.grid } },
        timeScale: { borderColor: colors.grid },
        rightPriceScale: { borderColor: colors.grid },
    });

    const lineSeriesByKey = {};
    [['cartera', colors.cartera], ['caucion', colors.caucion], ['spy', colors.spy]]
        .forEach(([key, color]) => {
            const benchmarkData = data[key];
            if (key !== 'cartera' && !benchmarkData?.available) return;
            const points = _sliceAndRebase(benchmarkData?.index_series || [], fromDate);
            if (points.length < 2) return;
            const lineSeries = chart.addSeries(LightweightCharts.LineSeries, {
                color, lineWidth: 2, lastValueVisible: false, priceLineVisible: false,
            });
            lineSeries.setData(points);
            lineSeriesByKey[key] = lineSeries;
        });

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
        lineSeriesByKey.cartera?.applyOptions({ color: next.cartera });
        lineSeriesByKey.caucion?.applyOptions({ color: next.caucion });
        lineSeriesByKey.spy?.applyOptions({ color: next.spy });
    });

    return chart;
}
