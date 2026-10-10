import { baseApexOptions, currentPalette, isLightTheme } from './apex-theme.js';
import { fmtMoney, fmtPct } from '../shared/format.js';

export function renderPnlBarChart(container, holdings, onSelectSymbol, { currency = 'ARS', field = 'pnl' } = {}) {
    if (container._chart) { container._chart.destroy(); container._chart = null; }

    const rows = holdings
        .filter((h) => h[field] !== null && h[field] !== undefined)
        .sort((a, b) => b[field] - a[field]);

    if (!rows.length) {
        container.innerHTML = '<p class="table-empty">Sin datos todavía.</p>';
        return;
    }

    container.innerHTML = '';
    const palette = currentPalette();
    const base = baseApexOptions();
    const isPercent = field === 'pnl_pct';

    const options = {
        ...base,
        chart: {
            ...base.chart,
            type: 'bar',
            height: Math.max(280, rows.length * 34),
            events: {
                dataPointSelection: (_e, _ctx, config) => {
                    const h = rows[config.dataPointIndex];
                    if (h && onSelectSymbol) onSelectSymbol(h.instrument.symbol);
                },
            },
        },
        series: [{ name: isPercent ? 'P&L %' : 'P&L', data: rows.map((h) => h[field]) }],
        plotOptions: {
            bar: {
                horizontal: true,
                distributed: true,
                borderRadius: 3,
                barHeight: '65%',
            },
        },
        colors: rows.map((h) => (h[field] >= 0 ? palette.positive : palette.negative)),
        xaxis: {
            categories: rows.map((h) => h.instrument.symbol),
            labels: {
                style: { colors: palette.text },
                formatter: (val) => (isPercent ? `${val}%` : val),
            },
        },
        yaxis: { labels: { style: { colors: palette.text } } },
        legend: { show: false },
        tooltip: {
            theme: isLightTheme() ? 'light' : 'dark',
            y: { formatter: (val) => (isPercent ? fmtPct(val) : fmtMoney(val, currency)) },
        },
    };

    container._chart = new ApexCharts(container, options);
    container._chart.render();
}
