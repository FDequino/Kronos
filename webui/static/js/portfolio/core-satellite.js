// Arquitectura Dual CORE/SATELITE (ver conversacion): dos pestañas PLANAS de
// Decisiones (sin anidar, ver pase de UX/UI) -- "Oportunidades del Día"
// (tarjetas de compra/venta, este archivo) y "Configuración del Motor"
// (asignación de capital + salida del Satélite, también este archivo) --
// más "Rendimiento Histórico" (backtest global, misma data, otra pestaña).
// GET /api/signals, GET/POST /api/config/allocation, PUT /satellite-exit-params,
// GET /api/backtest/summary.
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
let exitParams = { take_profit_pct: 4.0, stop_loss_pct: 9.0 };
let coreThreshold = null; // min_efficiency_score, ver ajustes.js (Umbral del Core)

function suggestedAmount(positionWeight, enginePct) {
    if (!lastSignals?.account_value_ars) return null;
    return lastSignals.account_value_ars * (enginePct / 100) * positionWeight;
}

// Refactorizacion financiera en USD CCL (ver conversacion): el analisis y la
// operatoria son en USD, pero el usuario opera en ARS en BYMA -- siempre se
// muestran las 2 monedas juntas, nunca una sola.
function dualPrice(usd, ars) {
    return `${fmtMoney(usd, 'USD')} <span class="hint">(≈${fmtMoney(ars)})</span>`;
}

// --- Configuración del Motor -------------------------------------------------
// Fase 6 (ver conversación): esto ANTES era un editor completo (slider +
// inputs de TP/SL + guardar), un duplicado exacto del bloque "Motor
// Cuantitativo y Estrategia" de Ajustes -- mismos endpoints
// (capitalAllocation/satelliteExitParams), dos lugares para cambiar el mismo
// valor. Ahora es de solo lectura acá -- un link lleva a Ajustes, la única
// fuente editable. `allocation`/`exitParams` se siguen pidiendo igual (la
// fila de Oportunidades del Día los necesita para el "Monto sugerido" de
// cada tarjeta).
function renderAllocationControls() {
    const el = $('core-satellite-allocation');
    if (!el) return;
    el.innerHTML = `
        <p class="hint">
            Core <b>${fmt(allocation.core_pct, 0)}%</b> / Satélite <b>${fmt(allocation.satellite_pct, 0)}%</b>
            · Take Profit Satélite <b>+${fmt(exitParams.take_profit_pct, 1)}%</b>
            · Stop Loss Satélite hasta <b>-${fmt(exitParams.stop_loss_pct, 1)}%</b>
            ${coreThreshold !== null ? `· Umbral del Core <b>${fmt(coreThreshold, 0)}</b>` : ''}
        </p>
        <a href="#ajustes/estrategia" class="btn btn-secondary">Editar en Ajustes</a>
    `;
}

// --- Oportunidades del Día (tarjetas, ver conversación: reemplaza la tabla
// con conmutador Core/Satélite que había antes -- ahora las señales de los
// 2 motores conviven en una sola grilla de tarjetas, cada una badgeada por
// motor) ----------------------------------------------------------------

function opportunityCardHtml(sig, enginePct, isCore) {
    const amount = suggestedAmount(sig.position_weight, enginePct);
    const subtitle = isCore
        ? `Score ${fmt(sig.efficiency_score, 1)} · Dispersión ${fmt(sig.dispersion_ratio, 3)}`
        : (sig.tactics || []).join(', ');
    const detailHref = `#detalle/${encodeURIComponent(sig.symbol)}`;
    return `
        <div class="opportunity-card">
            <div class="opportunity-card-head">
                <a href="${detailHref}" class="opportunity-card-symbol table-link">${sig.symbol}</a>
                <span class="tag ${isCore ? 'tag-neutral' : 'tag-positive'}">${isCore ? '🛡️ Core' : '⚡ Satélite'}</span>
            </div>
            <p class="hint">${subtitle}</p>
            <div class="opportunity-card-prices">
                <div><span class="l">Entrada</span><span class="v">${dualPrice(sig.entry_price_usd, sig.entry_price_ars)}</span></div>
                <div><span class="l">Take Profit</span><span class="v cell-positive">${dualPrice(sig.take_profit_price_usd, sig.take_profit_price_ars)} (+${fmt(sig.take_profit_pct, 1)}%)</span></div>
                <div><span class="l">Stop</span><span class="v cell-negative">${dualPrice(sig.initial_stop_price_usd, sig.initial_stop_price_ars)} (-${fmt(sig.initial_stop_pct, 1)}%)</span></div>
                <div><span class="l">Monto sugerido</span><span class="v">${amount !== null ? fmtMoney(amount) : '—'}</span></div>
            </div>
            <div class="opportunity-card-actions">
                <a href="${detailHref}" class="btn btn-secondary">Ver Gráfico</a>
                <button type="button" class="btn btn-primary opportunity-paper-trading-btn">Operar en Paper Trading</button>
            </div>
        </div>
    `;
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
                <span class="tag tag-neutral">📊 Pairs</span>
            </div>
            <p class="hint">Z-score ${fmt(sig.zscore, 2)} · Cointegración p=${fmt(sig.cointegration_pvalue, 3)} · Hedge ratio ${fmt(sig.hedge_ratio, 3)}</p>
            <div class="opportunity-card-prices">
                <div><span class="l">Comprar (barata)</span><span class="v cell-positive">${cheap.symbol}</span></div>
                <div><span class="l">${expensive.actionable ? 'Vender (cara, en cartera)' : 'Cara (informativo)'}</span>
                     <span class="v ${expensive.actionable ? 'cell-negative' : ''}">${expensive.symbol}${expensive.actionable ? '' : ' ⓘ'}</span></div>
                <div><span class="l">Monto sugerido</span><span class="v">${sig.suggested_amount_ars !== null && sig.suggested_amount_ars !== undefined ? fmtMoney(sig.suggested_amount_ars) : '—'}</span></div>
            </div>
            <p class="hint">${expensive.note}</p>
        </div>
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
                <span class="tag tag-neutral">🔄 Factor #${sig.rank}</span>
            </div>
            <p class="hint">Composite ${fmt(sig.composite_score, 2)} · Momentum ${fmtPct(sig.momentum_raw)} · Low-Vol ${fmt(sig.low_vol_raw, 3)} · Value ${valueLabel}</p>
            <div class="opportunity-card-prices">
                <div><span class="l">Monto sugerido</span><span class="v">${sig.suggested_amount_ars !== null && sig.suggested_amount_ars !== undefined ? fmtMoney(sig.suggested_amount_ars) : '—'}</span></div>
            </div>
        </div>
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
                <span class="tag tag-neutral">🔀 Canje</span>
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

// Dólar Sintético / Futuros ROFEX (ver conversacion, portfolio/synthetic_dollar.py)
function syntheticDollarCardHtml(sig) {
    return `
        <div class="opportunity-card">
            <div class="opportunity-card-head">
                <span class="opportunity-card-symbol">${sig.contract}</span>
                <span class="tag tag-positive">💵 Dólar Sintético</span>
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

// Renta Fija: rotacion LECAP/CER (ver conversacion, portfolio/fixed_income.py::compute_carry_trade_rotation_signals)
function fixedIncomeCarryCardHtml(sig) {
    const betterSymbol = sig.better === 'LECAP' ? sig.lecap_symbol : sig.cer_symbol;
    return `
        <div class="opportunity-card">
            <div class="opportunity-card-head">
                <span class="opportunity-card-symbol">${sig.lecap_symbol} / ${sig.cer_symbol}</span>
                <span class="tag tag-neutral">🔁 ${sig.bucket_label}</span>
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

function renderOpportunityCards() {
    const container = $('dual-engine-signals-container');
    if (!container) return;
    if (!lastSignals) {
        container.innerHTML = '<p class="table-empty">Todavía no se consultaron las señales de hoy.</p>';
        return;
    }
    const coreCards = lastSignals.core_signals.map((s) => opportunityCardHtml(s, allocation.core_pct, true));
    const satelliteCards = lastSignals.satellite_signals.map((s) => opportunityCardHtml(s, allocation.satellite_pct, false));
    const pairCards = (lastSignals.pairs_signals || []).map((s) => pairCardHtml(s));
    const factorCards = (lastSignals.factor_signals || []).map((s) => factorCardHtml(s));
    const arbitrageCards = (lastSignals.arbitrage_signals || []).map((s) => arbitrageCardHtml(s));
    const syntheticDollarCards = (lastSignals.synthetic_dollar_signals || []).map((s) => syntheticDollarCardHtml(s));
    const fixedIncomeCards = (lastSignals.fixed_income_signals || []).map((s) => fixedIncomeCarryCardHtml(s));
    const allCards = [
        ...coreCards, ...satelliteCards, ...pairCards, ...factorCards,
        ...arbitrageCards, ...syntheticDollarCards, ...fixedIncomeCards,
    ];

    let note = '';
    if (!lastSignals.spy_above_ema50) {
        note += '<p class="panel-note">SPY está por debajo de su propia EMA(50) — filtro de régimen no favorable: el Motor Core no opera hoy, el capital queda 100% en Caución.</p>';
    }
    if (lastSignals.market_regime === 'alta_volatilidad') {
        note += '<p class="panel-note">Régimen de alta volatilidad (VIX disparado) — el Motor Satélite no abre posiciones nuevas hoy.</p>';
    }

    // run_at (ver conversación, Fase 7): ahora que esto viene de una caché
    // por día hábil (dual_engine_signals_cache), nunca más "recién
    // calculado" por default -- sin este timestamp no había forma de saber
    // si lo que se ve es de hace 2 minutos o de la mañana.
    const runAtHint = lastSignals.run_at
        ? `<p class="hint">Última actualización: ${new Date(lastSignals.run_at).toLocaleString('es-AR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })}${lastSignals.cached ? '' : ' (recién calculado)'}</p>`
        : '';

    container.innerHTML = `
        ${runAtHint}
        <div class="mini-stat-grid row">
            <div class="mini-stat"><div class="l">Capital Core</div><div class="v">${fmtMoney(lastSignals.core_capital_ars)}</div></div>
            <div class="mini-stat"><div class="l">Capital Satélite</div><div class="v">${fmtMoney(lastSignals.satellite_capital_ars)}</div></div>
            <div class="mini-stat"><div class="l">Capital Pairs</div><div class="v">${fmtMoney(lastSignals.pairs_capital_ars)}</div></div>
            <div class="mini-stat"><div class="l">Capital Factor</div><div class="v">${fmtMoney(lastSignals.factor_capital_ars)}</div></div>
            <div class="mini-stat"><div class="l">Oportunidades hoy</div><div class="v num">${allCards.length}</div></div>
        </div>
        ${note}
        ${allCards.length
            ? `<div class="opportunity-cards-grid">${allCards.join('')}</div>`
            : '<p class="table-empty">Ninguna oportunidad hoy — el capital queda en Caución.</p>'}
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

async function loadAllocationAndRender() {
    try {
        const { data } = await api.capitalAllocation();
        allocation = data;
    } catch { /* se queda con el default 70/30 si el endpoint falla */ }
    try {
        const { data } = await api.satelliteExitParams();
        exitParams = data;
    } catch { /* se queda con el default 4.0/9.0 si el endpoint falla */ }
    try {
        const { data } = await api.coreParams();
        coreThreshold = data.min_efficiency_score;
    } catch { /* se omite del resumen si el endpoint falla, nunca se inventa un valor */ }
    renderAllocationControls();
}

async function loadCoreSatelliteIfNeeded() {
    if (loaded) return;
    loaded = true;
    await loadAllocationAndRender();
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
    if (!container) return;
    container.innerHTML = loadingHtml('Cargando backtest global…');
    try {
        const { data } = await api.dualEngineBacktestSummary();
        if (!data.available) {
            container.innerHTML = `<p class="table-empty">${data.message}</p>`;
            return;
        }
        const alloc = data.capital_allocation;
        container.innerHTML = `
            <p class="hint">Última corrida: ${new Date(data.run_at).toLocaleString('es-AR')} · ${data.lookback_days} ruedas · análisis y operatoria en USD CCL.
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
