const INFO_ICON_SVG = '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="10" cy="10" r="7"/><path d="M10 9v4M10 6.5v.01"/></svg>';

export function infoTooltipHtml(contentHtml) {
    return `<span class="info-tooltip" tabindex="0">
        <button class="info-tooltip-trigger" type="button" aria-label="Cómo se calcula">${INFO_ICON_SVG}</button>
        <div class="info-tooltip-panel">${contentHtml}</div>
    </span>`;
}

export function riskTooltipContentHtml(explanation, formulaLines) {
    return `<div>${explanation}</div><div class="info-tooltip-formula">${formulaLines.join('\n')}</div>`;
}
