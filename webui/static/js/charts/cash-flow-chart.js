// Fase 15: "Caja -- flujo real" dentro de Patrimonio. Columnas apiladas
// (ingresos verde / egresos rojo) por mes, nunca una línea de pocos puntos
// -- un flujo de caja se lee en meses, no en días (mismo criterio ya
// validado en el mockup antes de tocar código real).
import { baseApexOptions, currentPalette, isLightTheme } from './apex-theme.js';
import { fmtMoney } from '../shared/format.js';

const MES_LABEL = {
    '01': 'Ene', '02': 'Feb', '03': 'Mar', '04': 'Abr', '05': 'May', '06': 'Jun',
    '07': 'Jul', '08': 'Ago', '09': 'Sep', '10': 'Oct', '11': 'Nov', '12': 'Dic',
};

function mesCorto(key) {
    const [, m] = key.split('-');
    return MES_LABEL[m] || key;
}

export function renderCashFlowChart(container, flow) {
    if (container._chart) { container._chart.destroy(); container._chart = null; }
    if (!flow || !flow.meses.length) {
        container.innerHTML = '<p class="table-empty">Sin movimientos de caja todavía.</p>';
        return;
    }
    container.innerHTML = '';
    const palette = currentPalette();
    const base = baseApexOptions();

    const options = {
        ...base,
        chart: { ...base.chart, type: 'bar', height: 230, stacked: true },
        series: [
            { name: 'Ingresos', data: flow.ingresos },
            { name: 'Egresos', data: flow.egresos },
        ],
        colors: [palette.positive, palette.negative],
        plotOptions: { bar: { borderRadius: 4, columnWidth: '55%' } },
        xaxis: {
            categories: flow.meses.map(mesCorto),
            labels: { style: { colors: palette.text } },
        },
        yaxis: {
            labels: {
                style: { colors: palette.text },
                formatter: (val) => fmtMoney(val, flow.moneda, 0),
            },
        },
        tooltip: {
            theme: isLightTheme() ? 'light' : 'dark',
            y: { formatter: (val) => fmtMoney(val, flow.moneda) },
        },
    };

    container._chart = new ApexCharts(container, options);
    container._chart.render();
}
