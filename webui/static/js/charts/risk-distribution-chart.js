const ZONES = [
    { key: 'bajo', label: 'Bajo', color: '#30c177' },
    { key: 'medio', label: 'Medio', color: '#e0a63c' },
    { key: 'alto', label: 'Alto', color: '#ef5b5b' },
];

export function renderRiskDistributionChart(container, holdings) {
    if (container._chart) { container._chart.destroy(); container._chart = null; }

    const counts = { bajo: 0, medio: 0, alto: 0 };
    holdings.forEach((h) => { if (h.risk) counts[h.risk.label] = (counts[h.risk.label] || 0) + 1; });
    const total = counts.bajo + counts.medio + counts.alto;

    if (!total) {
        container.innerHTML = '<p class="table-empty">Sin datos todavía.</p>';
        return;
    }

    container.innerHTML = `
        <div class="risk-distribution">
            <div class="risk-distribution-bar">
                ${ZONES.filter((z) => counts[z.key] > 0).map((z) => `
                    <div class="risk-distribution-segment" style="width:${(counts[z.key] / total * 100).toFixed(2)}%; background:${z.color};" title="${z.label}: ${counts[z.key]}"></div>
                `).join('')}
            </div>
            <div class="risk-distribution-legend">
                ${ZONES.map((z) => `
                    <div class="risk-distribution-item">
                        <span class="risk-distribution-dot" style="background:${z.color}"></span>
                        <span class="risk-distribution-label">${z.label}</span>
                        <span class="risk-distribution-count">${counts[z.key]}</span>
                        <span class="risk-distribution-pct">${(counts[z.key] / total * 100).toFixed(0)}%</span>
                    </div>
                `).join('')}
            </div>
        </div>
    `;
}
