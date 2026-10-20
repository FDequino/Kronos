import { fmtPct, fmtMoney, fmtDate } from '../shared/format.js';
import { glossaryTerm, glossaryText } from '../shared/glossary.js';
import { infoTooltipHtml, riskTooltipContentHtml } from '../shared/info-tooltip.js';

export const LABEL_COLOR = { bajo: '#30c177', medio: '#e0a63c', alto: '#ef5b5b' };

export function instrumentRiskFormulaLines(risk) {
    return [
        `volatilidad anualizada = ${risk.annualized_volatility_pct}% -> riesgo ${risk.label}`,
        `caída máxima desde un pico = ${risk.max_drawdown_pct}%`,
    ];
}

export function renderPortfolioRiskPanel(container, metrics) {
    if (!metrics?.available) {
        container.innerHTML = `<p class="table-empty">${metrics?.reason || 'Sin tenencias todavía.'}</p>`;
        return;
    }

    const cvarAvailable = metrics.cvar95_monthly_pct !== null && metrics.cvar95_monthly_pct !== undefined;
    const cvarText = cvarAvailable ? fmtPct(Math.abs(metrics.cvar95_monthly_pct)) : '—';
    const top5Available = metrics.top5_concentration_pct !== null && metrics.top5_concentration_pct !== undefined;

    const moraArs = metrics.mora_ars || 0;
    const moraUsd = metrics.mora_usd || 0;
    const hasMora = moraArs > 0 || moraUsd > 0;
    const moraParts = [];
    if (moraArs > 0) moraParts.push(fmtMoney(moraArs, 'ARS'));
    if (moraUsd > 0) moraParts.push(fmtMoney(moraUsd, 'USD'));

    container.innerHTML = `
        <div class="risk-metrics-strip">
            <div class="stat">
                <div class="stat-label">${glossaryTerm('volatilidad_anualizada', 'Volatilidad anualizada')}</div>
                <div class="stat-value num">${fmtPct(metrics.annualized_volatility_pct)}</div>
            </div>
            <div class="stat">
                <div class="stat-label">${glossaryTerm('caida_maxima', 'Caída máxima')}</div>
                <div class="stat-value num cell-negative">${fmtPct(metrics.max_drawdown_pct)}</div>
            </div>
            <div class="stat">
                <div class="stat-label">${glossaryTerm('cvar95', 'CVaR 95% mensual')}</div>
                <div class="stat-value num ${cvarAvailable ? 'cell-negative' : 'cell-muted'}">${cvarText}</div>
                ${!cvarAvailable && metrics.cvar95_reason ? `<div class="hint" style="margin-top: 2px;">${metrics.cvar95_reason}</div>` : ''}
            </div>
            <div class="stat">
                <div class="stat-label">${glossaryTerm('top5_concentracion', 'Top 5 posiciones')}</div>
                <div class="stat-value num">${top5Available ? fmtPct(metrics.top5_concentration_pct) : '—'}</div>
                ${!top5Available && metrics.top5_reason ? `<div class="hint" style="margin-top: 2px;">${metrics.top5_reason}</div>` : ''}
            </div>
            <div class="stat">
                <div class="stat-label">${glossaryTerm('mora_prestamos', 'Préstamos en mora')}</div>
                <div class="stat-value num ${hasMora ? 'cell-negative' : ''}">${hasMora ? moraParts.join(' + ') : 'Sin mora'}</div>
            </div>
        </div>
        <p class="hint" style="margin-top: 10px;">${metrics.scope_note || ''}</p>
    `;
}

// --- Gauges (medio velocímetro) -- Fase 15, Patrimonio > Riesgo ------------
// Mismo componente ya validado en el mockup (Artifact), portado tal cual:
// zonas de color fijas por métrica + aguja, SVG puro, sin librería nueva.

function _gaugeArc(cx, cy, r, aFrom, aTo) {
    const pt = (a) => {
        const rad = (a * Math.PI) / 180;
        return [cx + r * Math.cos(rad), cy - r * Math.sin(rad)];
    };
    const [x1, y1] = pt(aFrom);
    const [x2, y2] = pt(aTo);
    return `M ${x1.toFixed(1)} ${y1.toFixed(1)} A ${r} ${r} 0 0 1 ${x2.toFixed(1)} ${y2.toFixed(1)}`;
}

function _gaugeSvg(value, max, zones) {
    const cx = 90, cy = 80, r = 70, sw = 14;
    const clamped = Math.max(0, Math.min(value, max));
    const toAngle = (v) => 180 * (1 - v / max);
    const arcs = zones.map((z) => `<path d="${_gaugeArc(cx, cy, r, toAngle(z.from), toAngle(z.to))}" fill="none" stroke="${z.color}" stroke-width="${sw}"/>`).join('');
    const angle = toAngle(clamped);
    const rad = (angle * Math.PI) / 180;
    const nx = cx + (r - 22) * Math.cos(rad);
    const ny = cy - (r - 22) * Math.sin(rad);
    const needleColor = '#e7e9ee';
    return `<svg viewBox="0 0 180 92" width="150" height="80">${arcs}<line x1="${cx}" y1="${cy}" x2="${nx.toFixed(1)}" y2="${ny.toFixed(1)}" stroke="${needleColor}" stroke-width="3" stroke-linecap="round"/><circle cx="${cx}" cy="${cy}" r="5" fill="${needleColor}"/></svg>`;
}

const GAUGE_COLOR = { bajo: '#30c177', medio: '#e0a63c', alto: '#ef5b5b' };

function _gaugeTile(label, value, max, zones, displayValue, tagLabel, tooltipHtml) {
    return `
        <div class="gauge-tile">
            <div class="gauge-label">${label}${tooltipHtml || ''}</div>
            ${_gaugeSvg(value, max, zones)}
            <div class="gauge-value num">${displayValue}</div>
            <span class="tag" style="background:${GAUGE_COLOR[tagLabel] || '#8a93a3'}22; color:${GAUGE_COLOR[tagLabel] || '#8a93a3'};">${tagLabel}</span>
        </div>
    `;
}

// Fase 17: el usuario pidió que el tooltip de cada gauge explique la fórmula
// Y la cuenta hecha con sus números reales, no solo la definición genérica.
// FIX (ver conversación/auditoría, hallazgo 3: "doble tooltip superpuesto")
// -- antes el label de cada gauge llevaba un glossaryTerm() (subrayado
// punteado, con la definición genérica) Y, pegado al lado, este ⓘ propio
// (con fórmula + números reales) -- dos triggers de tooltip uno al lado del
// otro para EL MISMO concepto se leía como un bug visual. Ahora cada
// tooltip arranca SIEMPRE con la misma explicación que antes daba
// glossaryText(), y le agrega la fórmula+números reales solo cuando hay
// datos -- un único ⓘ por gauge, nunca se pierde contenido.
// Umbrales bajo/medio/alto EXPLÍCITOS en el tooltip de cada gauge (ver
// conversación/auditoría, hallazgo 3: "por qué 33,9% de volatilidad es
// 'medio'" -- antes solo se veía en el color de la barra del gauge, nunca
// como número) -- mismos valores que ya usan las zonas de color pasadas a
// _gaugeTile más abajo, nunca un segundo umbral inventado aparte.
function _bandThresholdLine(low, high, unit = '%') {
    return `banda: bajo < ${low}${unit} · medio ${low}-${high}${unit} · alto > ${high}${unit}`;
}

function _volatilityTooltip(vol, nDays) {
    const explanation = glossaryText('volatilidad_anualizada');
    const bandLine = _bandThresholdLine(25, 45);
    if (vol === null || vol === undefined || !nDays) return infoTooltipHtml(riskTooltipContentHtml(explanation, [bandLine]));
    const dailyVol = vol / Math.sqrt(252);
    return infoTooltipHtml(riskTooltipContentHtml(
        explanation,
        [
            'volatilidad anualizada = desvío estándar de los retornos diarios × √252',
            `tu desvío diario fue ${fmtPct(dailyVol)} sobre los últimos ${nDays} días hábiles`,
            `${fmtPct(dailyVol)} × √252 = ${fmtPct(vol)}`,
            bandLine,
        ],
    ));
}

function _drawdownTooltip(dd, peakDate, troughDate) {
    const explanation = glossaryText('caida_maxima');
    const bandLine = _bandThresholdLine(10, 20);
    if (dd === null || dd === undefined || !peakDate) return infoTooltipHtml(riskTooltipContentHtml(explanation, [bandLine]));
    return infoTooltipHtml(riskTooltipContentHtml(
        explanation,
        [
            'caída máxima = (pico − valle) / pico',
            `tu pico más reciente fue el ${fmtDate(peakDate)}`,
            `el valle posterior más bajo fue el ${fmtDate(troughDate)}`,
            `resultado: -${fmtPct(dd)}`,
            bandLine,
        ],
    ));
}

function _top5Tooltip(top5pct, positions) {
    const explanation = glossaryText('top5_concentracion');
    const bandLine = _bandThresholdLine(40, 65);
    if (top5pct === null || top5pct === undefined || !positions?.length) return infoTooltipHtml(riskTooltipContentHtml(explanation, [bandLine]));
    return infoTooltipHtml(riskTooltipContentHtml(
        explanation,
        [
            ...positions.map((p) => `${p.symbol}: ${fmtPct(p.pct)}`),
            `suma = ${fmtPct(top5pct)}`,
            bandLine,
        ],
    ));
}

function _bandLabel(value, low, high) {
    if (value === null || value === undefined) return 'medio';
    if (value < low) return 'bajo';
    if (value < high) return 'medio';
    return 'alto';
}

export function renderPortfolioRiskGauges(container, metrics) {
    if (!metrics?.available) {
        container.innerHTML = `<p class="table-empty">${metrics?.reason || 'Sin tenencias todavía.'}</p>`;
        return;
    }
    const vol = metrics.annualized_volatility_pct;
    const dd = metrics.max_drawdown_pct;
    const top5 = metrics.top5_concentration_pct;
    const moraArs = metrics.mora_ars || 0;
    const moraUsd = metrics.mora_usd || 0;
    const hasMora = moraArs > 0 || moraUsd > 0;
    const moraParts = [];
    if (moraArs > 0) moraParts.push(fmtMoney(moraArs, 'ARS'));
    if (moraUsd > 0) moraParts.push(fmtMoney(moraUsd, 'USD'));

    container.innerHTML = `
        <div class="gauge-row">
            ${_gaugeTile(
                'Volatilidad anualizada', vol ?? 0, 60,
                [{ from: 0, to: 25, color: GAUGE_COLOR.bajo }, { from: 25, to: 45, color: GAUGE_COLOR.medio }, { from: 45, to: 60, color: GAUGE_COLOR.alto }],
                vol !== null && vol !== undefined ? fmtPct(vol) : '—', _bandLabel(vol, 25, 45),
                _volatilityTooltip(vol, metrics.n_days),
            )}
            ${_gaugeTile(
                'Caída máxima', dd ?? 0, 40,
                [{ from: 0, to: 10, color: GAUGE_COLOR.bajo }, { from: 10, to: 20, color: GAUGE_COLOR.medio }, { from: 20, to: 40, color: GAUGE_COLOR.alto }],
                dd !== null && dd !== undefined ? fmtPct(dd) : '—', _bandLabel(dd, 10, 20),
                _drawdownTooltip(dd, metrics.max_dd_peak_date, metrics.max_dd_trough_date),
            )}
            ${_gaugeTile(
                'Top 5 posiciones', top5 ?? 0, 100,
                [{ from: 0, to: 40, color: GAUGE_COLOR.bajo }, { from: 40, to: 65, color: GAUGE_COLOR.medio }, { from: 65, to: 100, color: GAUGE_COLOR.alto }],
                top5 !== null && top5 !== undefined ? fmtPct(top5) : '—', _bandLabel(top5, 40, 65),
                _top5Tooltip(top5, metrics.top5_positions),
            )}
        </div>
        <div class="mora-banner${hasMora ? ' has-mora' : ''}">
            <span class="ml">${glossaryTerm('mora_prestamos', 'Préstamos en mora')}</span>
            <span class="mv num${hasMora ? ' has-mora' : ''}">${hasMora ? moraParts.join(' + ') : 'Sin mora'}</span>
        </div>
        <p class="hint" style="margin-top: 10px;">${metrics.scope_note || ''}</p>
    `;
}

export function renderInstrumentRiskPanel(container, risk) {
    if (!risk) {
        container.innerHTML = '<p class="table-empty">Sin historial de precios suficiente para medir riesgo.</p>';
        return;
    }

    const color = LABEL_COLOR[risk.label] || '#8a93a3';
    container.innerHTML = `
        <div class="detail-position-grid">
            <div class="detail-position-item">
                <div class="label">${glossaryTerm('volatilidad_anualizada', 'Volatilidad anualizada')}</div>
                <div class="value">${fmtPct(risk.annualized_volatility_pct)}</div>
            </div>
            <div class="detail-position-item">
                <div class="label">${glossaryTerm('caida_maxima', 'Caída máxima')}</div>
                <div class="value">${fmtPct(risk.max_drawdown_pct)}</div>
            </div>
            <div class="detail-position-item">
                <div class="label">Riesgo</div>
                <div class="value"><span class="tag" style="background:${color}22; color:${color};">${risk.label}</span></div>
            </div>
        </div>
        <p class="hint" style="margin-top: 10px;">${risk.explanation || ''}</p>
    `;
}
