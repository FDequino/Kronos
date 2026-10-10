import { baseApexOptions, isLightTheme } from './apex-theme.js';
import { infoTooltipHtml, riskTooltipContentHtml } from '../shared/info-tooltip.js';

const ZONE_COLOR = { bajo: '#30c177', medio: '#e0a63c', alto: '#ef5b5b' };

export function instrumentRiskFormulaLines(risk) {
    if (risk.volatility_score === null || risk.volatility_score === undefined) {
        return ['riesgo = riesgo_base (sin histórico suficiente)', `= ${risk.baseline}/100`];
    }
    return [
        'riesgo = volatilidad × peso + base × (1 − peso)',
        `= ${risk.volatility_score} × ${risk.weight} + ${risk.baseline} × ${(1 - risk.weight).toFixed(2)}`,
        `= ${risk.score}/100`,
    ];
}

export function portfolioRiskFormulaLines(risk) {
    return [
        'riesgo = promedio_ponderado + penalización_concentración',
        `= ${risk.weighted_score} + ${risk.concentration_penalty}`,
        `= ${risk.score}/100`,
    ];
}

export function renderRiskGauge(container, score, label, explanation, formulaLines) {
    if (container._chart) { container._chart.destroy(); container._chart = null; }

    if (score === null || score === undefined) {
        container.innerHTML = '<p class="table-empty">Sin tenencias todavía.</p>';
        return;
    }

    const color = ZONE_COLOR[label] || '#8a93a3';
    const info = formulaLines ? infoTooltipHtml(riskTooltipContentHtml(explanation, formulaLines)) : '';

    container.innerHTML = `
        <div class="risk-gauge">
            <div class="risk-gauge-chart"></div>
            <div class="risk-gauge-value">
                <span class="tag" style="background:${color}22; color:${color};">${label}</span>
                ${info}
            </div>
            <p class="hint risk-gauge-explanation">${explanation}</p>
        </div>`;

    const base = baseApexOptions();
    const options = {
        ...base,
        chart: { ...base.chart, type: 'radialBar', height: 240 },
        series: [score],
        colors: [color],
        stroke: { lineCap: 'round' },
        plotOptions: {
            radialBar: {
                hollow: { size: '62%' },
                track: { background: base.grid.borderColor },
                dataLabels: {
                    name: { show: false },
                    value: {
                        fontSize: '28px', fontWeight: 700, color,
                        offsetY: 8,
                        formatter: () => `${score}/100`,
                    },
                },
            },
        },
        tooltip: { enabled: false },
    };

    container._chart = new ApexCharts(container.querySelector('.risk-gauge-chart'), options);
    container._chart.render();
}
