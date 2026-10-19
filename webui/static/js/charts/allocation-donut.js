import { baseApexOptions, currentPalette, isLightTheme } from './apex-theme.js';
import { fmtMoney, fmtPct } from '../shared/format.js';
import { bindChartTheme } from '../shared/chart-palette.js';

export const TYPE_COLOR = {
    accion: '#4c7dff', cedear: '#e0a63c', bono: '#30c177',
    on: '#c75dd1', fondo: '#22b8cf', moneda: '#8a93a3', caucion: '#5dc7b8',
    cripto: '#f7931a', prestamo: '#ff6b8a',
};

// Fase 17: la leyenda HTML propia (que ya había reemplazado la leyenda nativa
// de Apex por el bug de max-height) terminó siendo el pedido explícito de
// sacar: "centralo, saca la leyenda y dejala en el tooltip" -- el hover de
// Apex ya es un tooltip real, solo había que enriquecerlo (nombre + % +
// monto en vez de solo el monto) para que no se pierda nada al sacar la
// lista de abajo. El arco ahora también se centra dentro de su columna
// (antes quedaba pegado a la izquierda).
//
// `segments` acepta un `color` propio por ítem (además de `type`, que sigue
// resolviendo contra TYPE_COLOR) -- lo necesita el donut de distribución por
// riesgo (bajo/medio/alto no son una "clase de activo", no tienen entrada en
// TYPE_COLOR).
export function renderAllocationDonut(container, segments, onSelectType, currency = 'ARS') {
    if (container._chart) { container._chart.destroy(); container._chart = null; }
    container.innerHTML = '<div class="allocation-donut-chart"></div>';

    const total = segments.reduce((sum, s) => sum + s.value, 0);
    if (!total) {
        container.innerHTML = '<p class="table-empty">Sin datos todavía.</p>';
        return;
    }

    const palette = currentPalette();
    const base = baseApexOptions();
    const colors = segments.map((s) => s.color || TYPE_COLOR[s.type] || '#8a93a3');
    const chartEl = container.querySelector('.allocation-donut-chart');

    const options = {
        ...base,
        chart: {
            ...base.chart,
            type: 'donut',
            height: 220,
            events: {
                dataPointSelection: (_e, _ctx, config) => {
                    const type = segments[config.dataPointIndex]?.type;
                    if (type && onSelectType) onSelectType(type);
                },
            },
        },
        series: segments.map((s) => s.value),
        labels: segments.map((s) => s.label),
        colors,
        stroke: { width: 2, colors: [palette.surface] },
        legend: { show: false },
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
            custom: ({ seriesIndex }) => {
                const s = segments[seriesIndex];
                const pct = fmtPct(s.value / total * 100, 1);
                return `<div class="apexcharts-tooltip-custom">
                    <strong>${s.label}</strong><br>
                    ${pct} · ${fmtMoney(s.value, currency)}
                </div>`;
            },
        },
    };

    container._chart = new ApexCharts(chartEl, options);
    container._chart.render();

    // ApexCharts no reacciona solo a un cambio de [data-theme] -- a
    // diferencia de Lightweight Charts, no tiene un applyOptions barato para
    // recolorear en el lugar, asi que ante 'theme-changed' se vuelve a armar
    // el chart entero con los mismos argumentos (los datos no cambiaron,
    // solo la paleta). bindChartTheme limpia el listener anterior si este
    // container ya tenia uno (mismo patron que observeChartResize).
    bindChartTheme(container, () => {
        renderAllocationDonut(container, segments, onSelectType, currency);
    });
}
