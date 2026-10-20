// Arquitectura Dual CORE/SATELITE (ver conversacion): "Oportunidades del
// Día" (tarjetas de compra/venta, este archivo) es la pestaña PLANA default
// de Decisiones, + "Rendimiento Histórico" (backtest global, misma data,
// otra pestaña). "Configuración del Motor" era una tercera pestaña propia
// de solo lectura (Fase 6) -- se retiró del todo (ver conversacion, pase de
// UX/UI): ahora es un tooltip (?) junto a "Monto sugerido" en cada tarjeta
// (ver suggestedAmountTooltip más abajo) -- la asignación Core/Satélite se
// sigue pidiendo igual, ahora solo para calcular ese monto y ese tooltip.
// GET /api/signals, GET/POST /api/config/allocation, GET /api/backtest/summary.
import { $ } from '../shared/dom.js';
import { fmt, fmtPct, fmtMoney, fmtDate } from '../shared/format.js';
import { loadingHtml } from '../shared/loading.js';
import { activateSubtab } from '../shared/subtabs.js';
import { infoTooltipHtml, riskTooltipContentHtml } from '../shared/info-tooltip.js';
import { api } from './api.js';

// Explicaciones de indicadores (ver conversacion, "Perfiles de Inversion")
// -- iconos (?) reusados tanto en esta tabla de Rendimiento Historico como
// en las tarjetas de Perfiles (ajustes.js), para no duplicar el texto.
export const INDICATOR_TOOLTIPS = {
    retornoUsd: infoTooltipHtml(riskTooltipContentHtml(
        'Retorno anualizado medido en dólares (CCL) — la moneda en la que se analiza y opera esta cartera.',
        ['retorno anualizado = (equity final / equity inicial)^(252 / días) − 1'],
    )),
    retornoArs: infoTooltipHtml(riskTooltipContentHtml(
        'El mismo resultado en USD, recompuesto con la variación real del dólar CCL del período — no es una serie en pesos independiente.',
        ['retorno ARS = (1 + retorno USD) × (CCL final / CCL inicial) − 1'],
    )),
    winRate: infoTooltipHtml(riskTooltipContentHtml(
        '% de operaciones cerradas que terminaron con ganancia sobre el total de operaciones cerradas.',
        ['Win Rate = operaciones ganadoras / operaciones totales × 100'],
    )),
    profitFactor: infoTooltipHtml(riskTooltipContentHtml(
        'Cuánto se ganó en las operaciones ganadoras por cada peso perdido en las perdedoras. Por encima de 1 significa que las ganancias superan a las pérdidas en conjunto.',
        ['Profit Factor = suma de ganancias / suma de pérdidas (valor absoluto)'],
    )),
    sharpe: infoTooltipHtml(riskTooltipContentHtml(
        'Retorno ajustado por riesgo: cuánto retorno extra genera por cada unidad de volatilidad asumida. Por encima de 1 se considera sólido, por encima de 2 es excelente.',
        ['Sharpe = (retorno diario promedio / desvío estándar diario) × √252'],
    )),
    maxDrawdown: infoTooltipHtml(riskTooltipContentHtml(
        'La mayor caída desde un máximo previo hasta el mínimo posterior, sobre la curva de equity de la simulación.',
        ['Max Drawdown = (pico − valle) / pico'],
    )),
    dispersionP10P90: infoTooltipHtml(riskTooltipContentHtml(
        'Rango donde cayó la mayoría de los resultados simulados (CPCV, ver backtest unificado): p10 es el escenario pesimista, p90 el optimista. Cuanto más angosto, más consistente el resultado esperado — no es una garantía de que el resultado real caiga adentro de este rango.',
        ['p10 = percentil 10 de los caminos simulados · p90 = percentil 90'],
    )),
};

let loaded = false;
let lastSignals = null; // respuesta cruda de /api/signals, para recalcular montos sin re-pedir al servidor
let allocation = { core_pct: 70, satellite_pct: 30 };
let opportunitiesView = 'cards'; // 'cards' | 'list' (ver conversación: vista de lista para escanear muchas señales de un vistazo)

// Qué es cada motor/alerta y qué significan sus métricas (ver conversación:
// "no entiendo qué son" las tarjetas) -- un tooltip por tipo de tarjeta,
// pegado al badge de motor, mismo patrón que INDICATOR_TOOLTIPS más arriba.
const ENGINE_TOOLTIPS = {
    core: infoTooltipHtml(`
        <div>Motor Core: ranking cross-sectional del Motor Cuantitativo Kronos sobre todo el universo. Solo opera si SPY está por encima de su propia EMA(50) (filtro de régimen); si no, el capital queda en Caución.</div>
        <div class="info-tooltip-formula">Score = eficiencia esperada de la señal según el modelo\nDispersión = qué tan consistente fue ese resultado en la simulación (CPCV) — más bajo es más confiable</div>
    `),
    satellite: infoTooltipHtml(`
        <div>Motor Satélite: señales técnicas de corto plazo (1 a 3 ruedas), sin consultar al modelo. La táctica (ej. "mean_reversion") es la regla técnica que disparó esta entrada.</div>
    `),
    pairs: infoTooltipHtml(`
        <div>Pairs Trading: dos activos cuyo precio relativo históricamente vuelve a su media — se compra el que quedó barato (y se vendería el caro, si ya se tiene en cartera).</div>
        <div class="info-tooltip-formula">Z-score = cuán lejos está el spread actual de su media (más alto, más estirado)\nCointegración p = confiabilidad de la relación (cuanto más bajo, mejor)\nHedge ratio = cuánto del activo caro corresponde vender por cada unidad del barato</div>
    `),
    factor: infoTooltipHtml(`
        <div>Factor Investing: rotación hacia los instrumentos con mejor combinación de 4 factores — no es un trade con entrada/salida, es a qué instrumentos conviene estar expuesto esta semana.</div>
        <div class="info-tooltip-formula">Composite = ranking combinado de los 4 factores de abajo\nMomentum = retorno reciente (más alto, mejor)\nLow-Vol = baja volatilidad (más alto, más estable)\nValue = qué tan barato está relativo a sus fundamentals</div>
    `),
    arbitrage: infoTooltipHtml(`
        <div>Arbitraje de Canje MEP/CCL: el mismo bono cotiza distinto según la plaza en la que se opera (ley argentina vs. ley Nueva York) — cuando la brecha se estira, conviene pasar de la versión cara a la barata.</div>
        <div class="info-tooltip-formula">Z-score = cuán lejos está la brecha actual de su promedio histórico\nBrecha = diferencia de precio entre ambas versiones del bono</div>
    `),
    syntheticDollar: infoTooltipHtml(`
        <div>Dólar Sintético: armar un dólar a futuro (ROFEX) prestando pesos a tasa de caución en vez de comprar el futuro directo — conviene cuando la tasa implícita del futuro rinde más que caución + margen.</div>
    `),
    fixedIncomeCarry: infoTooltipHtml(`
        <div>Renta Fija / Carry Trade: compara el rendimiento mensual de un bono a tasa fija (LECAP) contra uno que ajusta por inflación (CER) y sugiere rotar hacia el que rinda más este mes.</div>
        <div class="info-tooltip-formula">TEM = Tasa Efectiva Mensual de cada instrumento\nSpread mensual = diferencia a favor del que conviene</div>
    `),
};

function suggestedAmount(positionWeight, enginePct) {
    if (!lastSignals?.account_value_ars) return null;
    return lastSignals.account_value_ars * (enginePct / 100) * positionWeight;
}

// Reemplaza a la pestaña "Motor" (ver conversacion, pase de UX/UI) -- esa
// pestaña entera solo decia esto: el % de patrimonio asignado al motor que
// generó la señal. El resto (TP/SL, umbral del Core) no hace falta para
// entender el monto sugerido, por eso no se trae de vuelta acá (serían 2
// fetches más solo para texto que no responde "de dónde sale este número").
function suggestedAmountTooltip(isCore) {
    const enginePct = isCore ? allocation.core_pct : allocation.satellite_pct;
    return infoTooltipHtml(`
        <div>El ${fmt(enginePct, 0)}% del patrimonio asignado al Motor ${isCore ? 'Core' : 'Satélite'} (asignación vigente: Core ${fmt(allocation.core_pct, 0)}% / Satélite ${fmt(allocation.satellite_pct, 0)}%), multiplicado por el peso de esta señal.</div>
        <a href="#ajustes/estrategia" class="hint" style="display:inline-block;margin-top:6px;">Editar en Ajustes → Estrategia y Motores</a>
    `);
}

// Permite a proposals.js (otra subtab de Decisiones, mismo riesgo de evento
// por símbolo) marcar una propuesta sin tener que volver a pedirle al
// servidor algo que esta vista ya tiene en memoria -- null hasta que
// loadSignals() resuelva la primera vez.
export function getEventRiskNotes() {
    return lastSignals?.event_risk_notes || {};
}

// Refactorizacion financiera en USD CCL (ver conversacion): el analisis y la
// operatoria son en USD, pero el usuario opera en ARS en BYMA -- siempre se
// muestran las 2 monedas juntas, nunca una sola.
function dualPrice(usd, ars) {
    return `${fmtMoney(usd, 'USD')} <span class="hint">(≈${fmtMoney(ars)})</span>`;
}

// --- Oportunidades del Día (tarjetas, ver conversación: reemplaza la tabla
// con conmutador Core/Satélite que había antes -- ahora las señales de los
// 2 motores conviven en una sola grilla de tarjetas, cada una badgeada por
// motor) ----------------------------------------------------------------

function opportunityCardHtml(sig, enginePct, isCore, eventRiskNotes) {
    const amount = suggestedAmount(sig.position_weight, enginePct);
    const subtitle = isCore
        ? `Score ${fmt(sig.efficiency_score, 1)} · Dispersión ${fmt(sig.dispersion_ratio, 3)}`
        : (sig.tactics || []).join(', ');
    const detailHref = `#detalle/${encodeURIComponent(sig.symbol)}`;
    // event_risk_notes (ver portfolio/nlp_agent.py, portfolio/live_signals.py)
    // -- SOLO existe entrada para un symbol si el riesgo es MEDIO/ALTO (earnings
    // cerca o sentimiento de noticias marcado), la mayoría de los días no hay
    // nada que mostrar acá -- nunca un placeholder vacío.
    const eventNote = eventRiskNotes?.[sig.symbol];
    return `
        <div class="opportunity-card">
            <div class="opportunity-card-head">
                <a href="${detailHref}" class="opportunity-card-symbol table-link">${sig.symbol}</a>
                <span class="tag ${isCore ? 'tag-neutral' : 'tag-positive'}">${isCore ? '🛡️ Core' : '⚡ Satélite'}${isCore ? ENGINE_TOOLTIPS.core : ENGINE_TOOLTIPS.satellite}</span>
            </div>
            <p class="hint">${subtitle}</p>
            ${eventNote ? `<p class="hint cell-warning">${eventNote}</p>` : ''}
            <div class="opportunity-card-prices">
                <div><span class="l">Entrada</span><span class="v">${dualPrice(sig.entry_price_usd, sig.entry_price_ars)}</span></div>
                <div><span class="l">Take Profit</span><span class="v cell-positive">${dualPrice(sig.take_profit_price_usd, sig.take_profit_price_ars)} (+${fmt(sig.take_profit_pct, 1)}%)</span></div>
                <div><span class="l">Stop</span><span class="v cell-negative">${dualPrice(sig.initial_stop_price_usd, sig.initial_stop_price_ars)} (-${fmt(sig.initial_stop_pct, 1)}%)</span></div>
                <div><span class="l">Monto sugerido${suggestedAmountTooltip(isCore)}</span><span class="v">${amount !== null ? fmtMoney(amount) : '—'}</span></div>
            </div>
            <div class="opportunity-card-actions">
                <a href="${detailHref}" class="btn btn-secondary">Ver Gráfico</a>
                <button type="button" class="btn btn-primary opportunity-paper-trading-btn">Operar en Paper Trading</button>
            </div>
        </div>
    `;
}

// Fila equivalente de opportunityCardHtml para la vista de lista (ver
// conversación) -- mismos datos, 5 columnas comunes a todos los *RowHtml
// (Instrumento/Motor/Detalle/Monto sugerido/Acciones) para que una sola
// tabla pueda listar las 7 estrategias juntas sin columnas vacías por tipo.
function opportunityRowHtml(sig, enginePct, isCore, eventRiskNotes) {
    const amount = suggestedAmount(sig.position_weight, enginePct);
    const subtitle = isCore
        ? `Score ${fmt(sig.efficiency_score, 1)} · Dispersión ${fmt(sig.dispersion_ratio, 3)}`
        : (sig.tactics || []).join(', ');
    const detailHref = `#detalle/${encodeURIComponent(sig.symbol)}`;
    const eventNote = eventRiskNotes?.[sig.symbol];
    return `
        <tr>
            <td><a href="${detailHref}" class="table-link">${sig.symbol}</a></td>
            <td><span class="tag ${isCore ? 'tag-neutral' : 'tag-positive'}">${isCore ? '🛡️ Core' : '⚡ Satélite'}${isCore ? ENGINE_TOOLTIPS.core : ENGINE_TOOLTIPS.satellite}</span></td>
            <td>
                <div class="hint">${subtitle}</div>
                <div>Entrada ${dualPrice(sig.entry_price_usd, sig.entry_price_ars)} · <span class="cell-positive">TP ${dualPrice(sig.take_profit_price_usd, sig.take_profit_price_ars)} (+${fmt(sig.take_profit_pct, 1)}%)</span> · <span class="cell-negative">Stop ${dualPrice(sig.initial_stop_price_usd, sig.initial_stop_price_ars)} (-${fmt(sig.initial_stop_pct, 1)}%)</span></div>
                ${eventNote ? `<div class="hint cell-warning">${eventNote}</div>` : ''}
            </td>
            <td class="num">${amount !== null ? fmtMoney(amount) : '—'}${suggestedAmountTooltip(isCore)}</td>
            <td>
                <div class="opportunity-row-actions">
                    <a href="${detailHref}" class="btn btn-secondary">Ver Gráfico</a>
                    <button type="button" class="btn btn-primary opportunity-paper-trading-btn">Operar en Paper Trading</button>
                </div>
            </td>
        </tr>
    `;
}

// Link de símbolo a su detalle -- toda otra tarjeta/fila de este archivo ya
// lo tenía (Core/Satélite/Factor), Pairs era la única excepción (ver
// conversación, relevamiento de tooltips/links faltantes).
function symbolLinkHtml(symbol, extraClass = '') {
    return `<a href="#detalle/${encodeURIComponent(symbol)}" class="table-link ${extraClass}">${symbol}</a>`;
}

// Pairs Trading (ver conversacion, portfolio/pairs_trading.py) -- esta app
// no soporta venta en corto real (ver portfolio/backtest.py::summarize),
// asi que la pata "cara" del par solo es operable de verdad si ya se tiene
// esa tenencia (expensive_leg.actionable) -- se lo marca visualmente en vez
// de insinuar una venta en corto que no va a pasar.
function pairCardHtml(sig) {
    const { cheap_leg: cheap, expensive_leg: expensive } = sig;
    return `
        <div class="opportunity-card">
            <div class="opportunity-card-head">
                <span class="opportunity-card-symbol">${sig.pair[0]} / ${sig.pair[1]}</span>
                <span class="tag tag-neutral">📊 Pairs${ENGINE_TOOLTIPS.pairs}</span>
            </div>
            <p class="hint">Z-score ${fmt(sig.zscore, 2)} · Cointegración p=${fmt(sig.cointegration_pvalue, 3)} · Hedge ratio ${fmt(sig.hedge_ratio, 3)}</p>
            <div class="opportunity-card-prices">
                <div><span class="l">Comprar (barata)</span><span class="v">${symbolLinkHtml(cheap.symbol, 'cell-positive')}</span></div>
                <div><span class="l">${expensive.actionable ? 'Vender (cara, en cartera)' : 'Cara (informativo)'}</span>
                     <span class="v">${symbolLinkHtml(expensive.symbol, expensive.actionable ? 'cell-negative' : '')}${expensive.actionable ? '' : ' ⓘ'}</span></div>
                <div><span class="l">Monto sugerido${infoTooltipHtml('<div>Capital asignado a Pairs Trading (ver Ajustes → Estrategia y Motores), completo para esta oportunidad.</div>')}</span><span class="v">${sig.suggested_amount_ars !== null && sig.suggested_amount_ars !== undefined ? fmtMoney(sig.suggested_amount_ars) : '—'}</span></div>
            </div>
            <p class="hint">${expensive.note}</p>
        </div>
    `;
}

function pairRowHtml(sig) {
    const { cheap_leg: cheap, expensive_leg: expensive } = sig;
    return `
        <tr>
            <td>${sig.pair[0]} / ${sig.pair[1]}</td>
            <td><span class="tag tag-neutral">📊 Pairs${ENGINE_TOOLTIPS.pairs}</span></td>
            <td>
                <div class="hint">Z-score ${fmt(sig.zscore, 2)} · Cointegración p=${fmt(sig.cointegration_pvalue, 3)} · Hedge ratio ${fmt(sig.hedge_ratio, 3)}</div>
                <div>Comprar ${symbolLinkHtml(cheap.symbol, 'cell-positive')} · ${expensive.actionable ? 'Vender' : 'Cara (informativo)'} ${symbolLinkHtml(expensive.symbol, expensive.actionable ? 'cell-negative' : '')}</div>
                <div class="hint">${expensive.note}</div>
            </td>
            <td class="num">${sig.suggested_amount_ars !== null && sig.suggested_amount_ars !== undefined ? fmtMoney(sig.suggested_amount_ars) : '—'}${infoTooltipHtml('<div>Capital asignado a Pairs Trading (ver Ajustes → Estrategia y Motores), completo para esta oportunidad.</div>')}</td>
            <td>—</td>
        </tr>
    `;
}

// Factor Investing (ver conversacion, portfolio/factor_investing.py) --
// rotacion de cartera (Top N por composite score), no un trade TP/SL
// individual, por eso no comparte el layout de precios de opportunityCardHtml.
function factorCardHtml(sig) {
    const valueLabel = sig.value_raw !== null && sig.value_raw !== undefined ? fmt(sig.value_raw, 3) : '—';
    return `
        <div class="opportunity-card">
            <div class="opportunity-card-head">
                <a href="#detalle/${encodeURIComponent(sig.symbol)}" class="opportunity-card-symbol table-link">${sig.symbol}</a>
                <span class="tag tag-neutral">🔄 Factor #${sig.rank}${ENGINE_TOOLTIPS.factor}</span>
            </div>
            <p class="hint">Composite ${fmt(sig.composite_score, 2)} · Momentum ${fmtPct(sig.momentum_raw)} · Low-Vol ${fmt(sig.low_vol_raw, 3)} · Value ${valueLabel}</p>
            <div class="opportunity-card-prices">
                <div><span class="l">Monto sugerido${infoTooltipHtml('<div>Capital asignado a Factor Investing (ver Ajustes → Estrategia y Motores), repartido en partes iguales entre los instrumentos del Top N de esta rotación.</div>')}</span><span class="v">${sig.suggested_amount_ars !== null && sig.suggested_amount_ars !== undefined ? fmtMoney(sig.suggested_amount_ars) : '—'}</span></div>
            </div>
        </div>
    `;
}

function factorRowHtml(sig) {
    const valueLabel = sig.value_raw !== null && sig.value_raw !== undefined ? fmt(sig.value_raw, 3) : '—';
    return `
        <tr>
            <td><a href="#detalle/${encodeURIComponent(sig.symbol)}" class="table-link">${sig.symbol}</a></td>
            <td><span class="tag tag-neutral">🔄 Factor #${sig.rank}${ENGINE_TOOLTIPS.factor}</span></td>
            <td class="hint">Composite ${fmt(sig.composite_score, 2)} · Momentum ${fmtPct(sig.momentum_raw)} · Low-Vol ${fmt(sig.low_vol_raw, 3)} · Value ${valueLabel}</td>
            <td class="num">${sig.suggested_amount_ars !== null && sig.suggested_amount_ars !== undefined ? fmtMoney(sig.suggested_amount_ars) : '—'}${infoTooltipHtml('<div>Capital asignado a Factor Investing (ver Ajustes → Estrategia y Motores), repartido en partes iguales entre los instrumentos del Top N de esta rotación.</div>')}</td>
            <td><a href="#detalle/${encodeURIComponent(sig.symbol)}" class="btn btn-secondary">Ver Gráfico</a></td>
        </tr>
    `;
}

// Arbitraje de Canje MEP/CCL (ver conversacion, portfolio/arbitrage.py) --
// alerta puntual entre AL30/AL30D y GD30/GD30D, sin monto sugerido propio
// (no es un motor que gestiona una porcion de la cartera).
function arbitrageCardHtml(sig) {
    return `
        <div class="opportunity-card">
            <div class="opportunity-card-head">
                <span class="opportunity-card-symbol">${sig.pair[0]} / ${sig.pair[1]}</span>
                <span class="tag tag-neutral">🔀 Canje${ENGINE_TOOLTIPS.arbitrage}</span>
            </div>
            <p class="hint">Z-score ${fmt(sig.zscore, 2)} · Brecha ${fmtPct(sig.brecha_pct)}</p>
            <div class="opportunity-card-prices">
                <div><span class="l">Dólar barato vía</span><span class="v cell-positive">${sig.cheap_leg}</span></div>
                <div><span class="l">Dólar caro vía</span><span class="v cell-negative">${sig.expensive_leg}</span></div>
            </div>
            <p class="hint">${sig.note}</p>
        </div>
    `;
}

function arbitrageRowHtml(sig) {
    return `
        <tr>
            <td>${sig.pair[0]} / ${sig.pair[1]}</td>
            <td><span class="tag tag-neutral">🔀 Canje${ENGINE_TOOLTIPS.arbitrage}</span></td>
            <td>
                <div class="hint">Z-score ${fmt(sig.zscore, 2)} · Brecha ${fmtPct(sig.brecha_pct)}</div>
                <div><span class="cell-positive">Barato vía ${sig.cheap_leg}</span> · <span class="cell-negative">Caro vía ${sig.expensive_leg}</span></div>
                <div class="hint">${sig.note}</div>
            </td>
            <td>—</td>
            <td>—</td>
        </tr>
    `;
}

// Dólar Sintético / Futuros ROFEX (ver conversacion, portfolio/synthetic_dollar.py)
function syntheticDollarCardHtml(sig) {
    return `
        <div class="opportunity-card">
            <div class="opportunity-card-head">
                <span class="opportunity-card-symbol">${sig.contract}</span>
                <span class="tag tag-positive">💵 Dólar Sintético${ENGINE_TOOLTIPS.syntheticDollar}</span>
            </div>
            <p class="hint">Vencimiento ${fmtDate(sig.maturity_date)}</p>
            <div class="opportunity-card-prices">
                <div><span class="l">Tasa implícita</span><span class="v cell-positive">${fmtPct(sig.implied_tna_pct)} TNA</span></div>
                <div><span class="l">Caución + margen</span><span class="v">${fmtPct(sig.caucion_tna_pct + sig.margin_pct)} TNA</span></div>
                <div><span class="l">Spread</span><span class="v cell-positive">${fmtPct(sig.spread_pct)}</span></div>
            </div>
            <p class="hint">${sig.note}</p>
        </div>
    `;
}

function syntheticDollarRowHtml(sig) {
    return `
        <tr>
            <td>${sig.contract}</td>
            <td><span class="tag tag-positive">💵 Dólar Sintético${ENGINE_TOOLTIPS.syntheticDollar}</span></td>
            <td>
                <div class="hint">Vencimiento ${fmtDate(sig.maturity_date)}</div>
                <div>Implícita <span class="cell-positive">${fmtPct(sig.implied_tna_pct)} TNA</span> · Caución+margen ${fmtPct(sig.caucion_tna_pct + sig.margin_pct)} TNA · Spread <span class="cell-positive">${fmtPct(sig.spread_pct)}</span></div>
                <div class="hint">${sig.note}</div>
            </td>
            <td>—</td>
            <td>—</td>
        </tr>
    `;
}

// Renta Fija: rotacion LECAP/CER (ver conversacion, portfolio/fixed_income.py::compute_carry_trade_rotation_signals)
function fixedIncomeCarryCardHtml(sig) {
    const betterSymbol = sig.better === 'LECAP' ? sig.lecap_symbol : sig.cer_symbol;
    return `
        <div class="opportunity-card">
            <div class="opportunity-card-head">
                <span class="opportunity-card-symbol">${sig.lecap_symbol} / ${sig.cer_symbol}</span>
                <span class="tag tag-neutral">🔁 ${sig.bucket_label}${ENGINE_TOOLTIPS.fixedIncomeCarry}</span>
            </div>
            <p class="hint">TEM LECAP ${fmtPct(sig.lecap_tem_pct)} · TEM CER (nominal) ${fmtPct(sig.cer_tem_nominal_pct)}</p>
            <div class="opportunity-card-prices">
                <div><span class="l">Rota hacia</span><span class="v cell-positive">${sig.better} (${betterSymbol})</span></div>
                <div><span class="l">Spread mensual</span><span class="v">${fmtPct(sig.spread_pct)}</span></div>
            </div>
            <p class="hint">${sig.note}</p>
        </div>
    `;
}

function fixedIncomeCarryRowHtml(sig) {
    const betterSymbol = sig.better === 'LECAP' ? sig.lecap_symbol : sig.cer_symbol;
    return `
        <tr>
            <td>${sig.lecap_symbol} / ${sig.cer_symbol}</td>
            <td><span class="tag tag-neutral">🔁 ${sig.bucket_label}${ENGINE_TOOLTIPS.fixedIncomeCarry}</span></td>
            <td>
                <div class="hint">TEM LECAP ${fmtPct(sig.lecap_tem_pct)} · TEM CER (nominal) ${fmtPct(sig.cer_tem_nominal_pct)}</div>
                <div>Rota hacia <span class="cell-positive">${sig.better} (${betterSymbol})</span> · Spread mensual ${fmtPct(sig.spread_pct)}</div>
                <div class="hint">${sig.note}</div>
            </td>
            <td>—</td>
            <td>—</td>
        </tr>
    `;
}

function renderOpportunityCards() {
    const container = $('dual-engine-signals-container');
    const lastUpdatedSlot = $('dual-engine-last-updated');
    if (!container) return;
    if (!lastSignals) {
        container.innerHTML = '<p class="table-empty">Todavía no se consultaron las señales de hoy.</p>';
        if (lastUpdatedSlot) lastUpdatedSlot.textContent = '';
        return;
    }
    const eventRiskNotes = lastSignals.event_risk_notes || {};
    const htmlFn = opportunitiesView === 'list' ? opportunityRowHtml : opportunityCardHtml;
    const pairFn = opportunitiesView === 'list' ? pairRowHtml : pairCardHtml;
    const factorFn = opportunitiesView === 'list' ? factorRowHtml : factorCardHtml;
    const arbitrageFn = opportunitiesView === 'list' ? arbitrageRowHtml : arbitrageCardHtml;
    const syntheticDollarFn = opportunitiesView === 'list' ? syntheticDollarRowHtml : syntheticDollarCardHtml;
    const fixedIncomeFn = opportunitiesView === 'list' ? fixedIncomeCarryRowHtml : fixedIncomeCarryCardHtml;
    const coreItems = lastSignals.core_signals.map((s) => htmlFn(s, allocation.core_pct, true, eventRiskNotes));
    const satelliteItems = lastSignals.satellite_signals.map((s) => htmlFn(s, allocation.satellite_pct, false, eventRiskNotes));
    const pairItems = (lastSignals.pairs_signals || []).map((s) => pairFn(s));
    const factorItems = (lastSignals.factor_signals || []).map((s) => factorFn(s));
    const arbitrageItems = (lastSignals.arbitrage_signals || []).map((s) => arbitrageFn(s));
    const syntheticDollarItems = (lastSignals.synthetic_dollar_signals || []).map((s) => syntheticDollarFn(s));
    const fixedIncomeItems = (lastSignals.fixed_income_signals || []).map((s) => fixedIncomeFn(s));
    const allItems = [
        ...coreItems, ...satelliteItems, ...pairItems, ...factorItems,
        ...arbitrageItems, ...syntheticDollarItems, ...fixedIncomeItems,
    ];

    // Notas de régimen (ver conversación: estaban como texto suelto antes de
    // la grilla de KPIs, "colgadas" sin jerarquía visual) -- ahora son un
    // status banner real (mismo componente que los mensajes de éxito/error
    // de la app), pegado arriba de las tarjetas/lista en vez de flotar entre
    // el botón de refresco y los KPIs.
    let note = '';
    if (!lastSignals.spy_above_ema50) {
        // "el capital queda 100% en Caución" afirmaba una colocación real
        // que no necesariamente existe (ver conversación/auditoría, hallazgo
        // 2.2: con 0 cauciones vigentes y poca caja libre, el mensaje sonaba
        // a que SÍ había plata puesta en Caución) -- ahora describe la regla
        // del motor sin afirmar un estado que puede no ser cierto hoy.
        note += '<div class="status warning show">SPY está por debajo de su propia EMA(50) — filtro de régimen no favorable: el Motor Core no abre posiciones nuevas hoy (su capital libre va a Caución solo si hay efectivo sin asignar).</div>';
    }
    if (lastSignals.market_regime === 'alta_volatilidad') {
        note += '<div class="status warning show">Régimen de alta volatilidad (VIX disparado) — el Motor Satélite no abre posiciones nuevas hoy.</div>';
    }

    // run_at (ver conversación, Fase 7): ahora que esto viene de una caché
    // por día hábil (dual_engine_signals_cache), nunca más "recién
    // calculado" por default -- sin este timestamp no había forma de saber
    // si lo que se ve es de hace 2 minutos o de la mañana. Vive en el
    // view-toolbar (junto al botón "Actualizar"), no flotando arriba de los
    // KPIs (ver conversación: estaba "colgado" sin relación visual con nada).
    if (lastUpdatedSlot) {
        lastUpdatedSlot.textContent = lastSignals.run_at
            ? `Última actualización: ${new Date(lastSignals.run_at).toLocaleString('es-AR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })}${lastSignals.cached ? '' : ' (recién calculado)'}`
            : '';
    }

    const itemsHtml = allItems.length
        ? (opportunitiesView === 'list'
            ? `<div class="table-scroll"><table>
                <thead><tr>
                    <th>Instrumento</th><th>Motor</th><th>Detalle</th><th>Monto sugerido</th><th>Acciones</th>
                </tr></thead>
                <tbody>${allItems.join('')}</tbody>
            </table></div>`
            : `<div class="opportunity-cards-grid">${allItems.join('')}</div>`)
        : '<p class="table-empty">Ninguna oportunidad hoy.</p>';

    container.innerHTML = `
        <div class="mini-stat-grid row">
            <div class="mini-stat"><div class="l">Capital Core</div><div class="v">${fmtMoney(lastSignals.core_capital_ars)}</div></div>
            <div class="mini-stat"><div class="l">Capital Satélite</div><div class="v">${fmtMoney(lastSignals.satellite_capital_ars)}</div></div>
            <div class="mini-stat"><div class="l">Capital Pairs</div><div class="v">${fmtMoney(lastSignals.pairs_capital_ars)}</div></div>
            <div class="mini-stat"><div class="l">Capital Factor</div><div class="v">${fmtMoney(lastSignals.factor_capital_ars)}</div></div>
            <div class="mini-stat"><div class="l">Oportunidades hoy</div><div class="v num">${allItems.length}</div></div>
        </div>
        ${note}
        ${itemsHtml}
    `;
}

// force (ver conversación, Fase 7): el backend cachea el resultado por día
// hábil ART (schema.sql::dual_engine_signals_cache) -- la entrada normal a
// la pestaña usa esa caché (rápida, sin molestar a Chronos-Bolt de nuevo),
// "Actualizar señales de hoy" es el único que pide force=1 y recalcula.
async function loadSignals(force = false) {
    const container = $('dual-engine-signals-container');
    container.innerHTML = loadingHtml(force
        ? 'Evaluando señales de hoy (puede tardar si el Motor Core consulta al Motor Cuantitativo Kronos)&hellip;'
        : 'Cargando señales de hoy&hellip;');
    try {
        const { data } = await api.dualEngineSignals(force);
        lastSignals = data;
        renderOpportunityCards();
    } catch (error) {
        container.innerHTML = `<p class="table-empty">No se pudieron cargar las señales de hoy: ${error.response?.data?.error || error.message}</p>`;
    }
}

// Fase 6 (ver conversación): antes esto también pedía satellite-exit-params
// y core-params para pintarlos en la pestaña "Motor" -- esa pestaña se
// retiró del todo (ver tooltip "Monto sugerido" más arriba), así que ya no
// hace falta pedir TP/SL/umbral acá, solo la asignación de capital que SÍ
// sigue haciendo falta para calcular los montos sugeridos de cada tarjeta.
async function loadAllocation() {
    try {
        const { data } = await api.capitalAllocation();
        allocation = data;
    } catch { /* se queda con el default 70/30 si el endpoint falla */ }
}

async function loadCoreSatelliteIfNeeded() {
    if (loaded) return;
    loaded = true;
    await loadAllocation();
    await loadSignals();
}

// Fase de limpieza de UI (ver conversacion): "Oportunidades del Día"
// (señales de HOY, este archivo) y "Rendimiento Histórico" (backtest
// global, misma data pero otra pestaña de nivel superior) son disparadores
// de carga perezosa independientes.
let backtestLoaded = false;

function metricsRowHtml(label, m) {
    if (!m) return '';
    const pf = m.profit_factor === null || m.profit_factor === undefined ? '—' : (m.profit_factor === Infinity ? '∞' : fmt(m.profit_factor, 2));
    return `
        <tr>
            <td><b>${label}</b></td>
            <td>${fmtPct(m.annualized_return_pct, 1)}</td>
            <td>${fmtPct(m.annualized_return_ars_pct, 1)}</td>
            <td>${fmtPct(m.win_rate_pct, 1)}</td>
            <td>${pf}</td>
            <td>${fmt(m.sharpe_ratio, 2)}</td>
            <td>${fmtPct(m.max_drawdown_pct, 1)}</td>
            <td>${fmtPct(m.pct_time_caucion, 1)}</td>
            <td>${m.n_trades}</td>
        </tr>
    `;
}

async function loadBacktestSummary() {
    const container = $('dual-engine-backtest-container');
    const lastRunSlot = $('dual-engine-backtest-last-run');
    if (!container) return;
    container.innerHTML = loadingHtml('Cargando backtest global…');
    try {
        const { data } = await api.dualEngineBacktestSummary();
        if (!data.available) {
            container.innerHTML = `<p class="table-empty">${data.message}</p>`;
            if (lastRunSlot) lastRunSlot.textContent = '';
            return;
        }
        // Sin botón de refresco acá (a diferencia de Oportunidades/Propuestas,
        // ver conversación) -- este backtest es un job de CLI de varios
        // minutos que consulta a Chronos-Bolt (python3 -m portfolio.backtest
        // dual), no algo que la web pueda disparar de forma segura en un
        // request. Solo se muestra cuándo corrió la última vez.
        if (lastRunSlot) {
            lastRunSlot.textContent = `Último backtest: ${new Date(data.run_at).toLocaleString('es-AR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })} (corre manualmente, no desde la web)`;
        }
        const alloc = data.capital_allocation;
        container.innerHTML = `
            <p class="hint">${data.lookback_days} ruedas · análisis y operatoria en USD CCL.
            Retorno Anual ARS = el mismo resultado en USD recompuesto con la variación real del CCL del período.</p>
            <div class="table-scroll">
                <table>
                    <thead><tr>
                        <th>Motor</th>
                        <th>Retorno Anual USD${INDICATOR_TOOLTIPS.retornoUsd}</th>
                        <th>Retorno Anual ARS${INDICATOR_TOOLTIPS.retornoArs}</th>
                        <th>Win Rate${INDICATOR_TOOLTIPS.winRate}</th>
                        <th>Profit Factor${INDICATOR_TOOLTIPS.profitFactor}</th>
                        <th>Sharpe USD${INDICATOR_TOOLTIPS.sharpe}</th>
                        <th>Max DD USD${INDICATOR_TOOLTIPS.maxDrawdown}</th>
                        <th>% Tiempo Caución</th><th>Trades</th>
                    </tr></thead>
                    <tbody>
                        ${metricsRowHtml('CORE', data.core_metrics)}
                        ${metricsRowHtml('SATÉLITE', data.satellite_metrics)}
                        ${alloc ? metricsRowHtml(`GLOBAL (${fmt(alloc.core_pct, 0)}/${fmt(alloc.satellite_pct, 0)})`, data.global_metrics) : ''}
                    </tbody>
                </table>
            </div>
        `;
    } catch (error) {
        container.innerHTML = `<p class="table-empty">No se pudo cargar el resumen del backtest: ${error.response?.data?.error || error.message}</p>`;
    }
}

async function loadBacktestSummaryIfNeeded() {
    if (backtestLoaded) return;
    backtestLoaded = true;
    await loadBacktestSummary();
}

export function initCoreSatelliteView() {
    // "Oportunidades del Día" (señales en vivo, consulta a Kronos) es ahora
    // la pestaña PLANA por defecto de Decisiones (ya no una subtab anidada
    // no-default) -- la carga perezosa se dispara al entrar a la vista
    // Decisiones por primera vez: click en el ítem del nav lateral, O un
    // hash directo #decisiones (recarga/bookmark, sin ningún click real).
    const navBtn = document.querySelector('.nav-item[data-nav-id="decisiones"]');
    if (navBtn) navBtn.addEventListener('click', loadCoreSatelliteIfNeeded);
    // split('/')[0] (no solo split('?')[0]) -- el hash de 2 segmentos
    // #decisiones/motor (Fase 1) no matcheaba antes acá, así que entrar
    // directo a esa URL (recarga/bookmark) dejaba el panel en su skeleton
    // para siempre (bug real, encontrado al probar el resumen de Fase 6).
    if (location.hash.slice(1).split('?')[0].split('/')[0] === 'decisiones') loadCoreSatelliteIfNeeded();

    const refreshBtn = $('refresh-dual-engine-signals-btn');
    if (refreshBtn) refreshBtn.addEventListener('click', () => loadSignals(true));

    const viewToggle = $('dual-engine-view-toggle');
    if (viewToggle) {
        viewToggle.addEventListener('click', (e) => {
            const btn = e.target.closest('.segmented-toggle-option');
            if (!btn || btn.dataset.view === opportunitiesView) return;
            opportunitiesView = btn.dataset.view;
            viewToggle.querySelectorAll('.segmented-toggle-option').forEach((b) => b.classList.toggle('active', b === btn));
            renderOpportunityCards();
        });
    }

    const signalsContainer = $('dual-engine-signals-container');
    if (signalsContainer) {
        signalsContainer.addEventListener('click', (e) => {
            if (e.target.closest('.opportunity-paper-trading-btn')) activateSubtab('decisiones', 'paper-trading');
        });
    }

    const rendimientoBtn = document.querySelector('[data-subtabs="decisiones"] [data-subtab-target="rendimiento"]');
    if (rendimientoBtn) rendimientoBtn.addEventListener('click', loadBacktestSummaryIfNeeded);
    if (document.querySelector('[data-subtab-panel="rendimiento"]')?.classList.contains('active')) loadBacktestSummaryIfNeeded();
}
