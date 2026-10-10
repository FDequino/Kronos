import { baseApexOptions, currentPalette, isLightTheme } from './apex-theme.js';
import { fmtMoney } from '../shared/format.js';

const TYPE_COLOR = {
    accion: '#4c7dff', cedear: '#e0a63c', bono: '#30c177',
    on: '#c75dd1', fondo: '#22b8cf', moneda: '#8a93a3',
};

export function renderAllocationDonut(container, segments, onSelectType, currency = 'ARS') {
    if (container._chart) { container._chart.destroy(); container._chart = null; }
    container.innerHTML = '';

    const total = segments.reduce((sum, s) => sum + s.value, 0);
    if (!total) {
        container.innerHTML = '<p class="table-empty">Sin datos todavía.</p>';
        return;
    }

    const palette = currentPalette();
    const base = baseApexOptions();

    const options = {
        ...base,
        chart: {
            ...base.chart,
            type: 'donut',
            height: 240,
            events: {
                dataPointSelection: (_e, _ctx, config) => {
                    const type = segments[config.dataPointIndex]?.type;
                    if (type && onSelectType) onSelectType(type);
                },
                legendClick: (_ctx, seriesIndex) => {
                    const type = segments[seriesIndex]?.type;
                    if (type && onSelectType) onSelectType(type);
                },
            },
        },
        series: segments.map((s) => s.value),
        labels: segments.map((s) => s.label),
        colors: segments.map((s) => TYPE_COLOR[s.type] || '#8a93a3'),
        stroke: { width: 2, colors: [palette.surface] },
        legend: {
            ...base.legend,
            position: 'bottom',
            formatter: (label, opts) => `${label} — ${(segments[opts.seriesIndex].value / total * 100).toFixed(1)}%`,
        },
        plotOptions: {
            pie: {
                donut: {
                    size: '72%',
                    labels: {
                        show: true,
                        name: { show: true, color: palette.text, fontSize: '12.5px', fontWeight: 500, offsetY: -4 },
                        value: {
                            show: true, color: palette.textStrong, fontSize: '19px', fontWeight: 600, offsetY: 4,
                            formatter: (val) => fmtMoney(Number(val), currency),
                        },
                        total: {
                            show: true, label: 'Total', color: palette.text, fontSize: '12.5px', fontWeight: 500,
                            formatter: () => fmtMoney(total, currency),
                        },
                    },
                },
            },
        },
        dataLabels: { enabled: false },
        tooltip: {
            theme: isLightTheme() ? 'light' : 'dark',
            y: { formatter: (val) => fmtMoney(val, currency) },
        },
    };

    container._chart = new ApexCharts(container, options);
    container._chart.render();
}
