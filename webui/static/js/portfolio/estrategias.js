// Registro de Estrategias / Motor de Riesgo (ver OBJETIVO_MARCO.md, plan
// "grado-fondo" 2026-10-20/21) -- capa de gobierno del mismo motor de
// decisiones de Oportunidades/Propuestas. Flujo real (corregido 2026-10-21):
// Investigación -> Validada (DSR>=0.95) -> Paper Trading (automático, plata
// ficticia, cero riesgo) -> Real. Dos vistas del MISMO estado:
//   - Decisiones > Estrategias: SOLO LECTURA (tabla + banner).
//   - Ajustes > Estrategia y Motores: los controles (arrancar/pausar/
//     continuar Paper Trading, promover a Live, modo de automatización una
//     vez Real) -- nunca se activa ni avanza nada solo.
// NUNCA dispara una re-simulacion desde un click (el Asignador de Capital y
// el Monitor de Salud corren por cron, tardan minutos, ver portfolio/
// capital_allocator.py/strategy_health.py). GET/POST /api/portfolio/strategies*.
import { $ } from '../shared/dom.js';
import { showStatus } from '../shared/status.js';
import { fmt, fmtPct } from '../shared/format.js';
import { loadingHtml } from '../shared/loading.js';
import { infoTooltipHtml } from '../shared/info-tooltip.js';
import { api } from './api.js';

// name del Registro -> etiqueta legible. Una estrategia nueva que todavia
// no esta aca se muestra con su name crudo (nunca rompe el render).
const STRATEGY_LABELS = {
    core: 'Core (Chronos point-forecast)',
    satellite: 'Satélite (reglas técnicas)',
    pairs: 'Pairs Trading',
    canje_al29_gd29: 'Canje AL29/GD29',
    crypto_carry_btcusdt: 'Carry Cripto BTC',
    crypto_carry_ethusdt: 'Carry Cripto ETH',
    factor_investing: 'Factor Investing',
};

const STATUS_PILL = {
    research: { label: 'Investigación', cls: 'info' },
    validated: { label: 'Validada', cls: 'warning' },
    paper_trading: { label: 'Paper Trading', cls: 'warning' },
    live: { label: 'Real', cls: 'success' },
};

// Explicación en criollo de cada estrategia (ver conversación: "no veo una
// explicación de cada una") + la evidencia real detrás del número que
// aparece en "Retorno esperado" -- para Canje, la advertencia de que ese
// número es teórico es la parte más importante de la explicación, no un
// detalle al margen (ver live_strategy_engine.py, el backtest de la
// posición real ejecutable da DSR~0, pierde plata).
// Comisión real descontada en cada backtest (ver conversación: "no aclara
// si descuenta comisiones" -- cada motor la cobra explícito, no es un
// retorno bruto en ningún caso de los 5 con número):
//   Canje: CANJE_ROUND_TRIP_COMMISSION_PCT=1.21% (arbitrage.py)
//   Carry BTC/ETH: CARRY_ROUND_LEG_COST_PCT=0.15% por ciclo (crypto_carry.py)
//   Factor/Core: ROUND_TRIP_COMMISSION_PCT (~2.5% ida+vuelta, backtest.py)
const STRATEGY_DESCRIPTIONS = {
    core: 'Pronóstico direccional con el modelo de IA Chronos-Bolt + un meta-modelo encima. Confirmado SIN edge real: ni el modelo base ni un ajuste (LoRA) con datos propios superan el punto de partida, y la literatura externa muestra que este tipo de modelo apenas le gana a una caminata aleatoria en acciones líquidas. Descartado para seguir iterando tal cual. <i>Retorno en USD, ya neto de la comisión real de ida y vuelta (~2.5%).</i>',
    satellite: 'Reglas técnicas de corto plazo (RSI) sobre el mismo universo que Core. Con la tasa de acierto REAL medida (35.6%, no un número viejo desactualizado), el propio sistema de manejo de riesgo (Kelly) decide no apostar nada — no pierde, pero tampoco gana. Sin backtest de Sharpe/retorno propio todavía (por eso no tiene número en la tabla).',
    canje_al29_gd29: 'Dos bonos soberanos económicamente equivalentes (misma deuda, distinta ley: Argentina vs. Nueva York) que deberían moverse pegados. Cuando se separan más de lo normal, convendría dolarizarse por el más barato. <b>El 24.8% de la tabla es un número TEÓRICO en USD</b> (mide el spread achicándose, ya neto de la comisión real de canje de IOL/PPI de 1.21% — pero no una posición real) — el único modelo que sí se puede ejecutar con IOL/PPI (comprar y rotar entre los 2 bonos, también neto de comisión) se probó con datos reales y <b>pierde -20.8% anual en USD</b> (Max Drawdown -68.8%). Por eso sigue solo en Paper Trading, sin ejecución real.',
    crypto_carry_btcusdt: 'Cash-and-carry: comprás BTC de contado y vendés el futuro perpetuo al mismo tiempo (delta-neutral, sin apuesta a que el precio suba o baje), cobrando el funding rate que el mercado de futuros paga cada 8 horas. Totalmente ejecutable con plata real — es la estrategia con la evidencia más sólida de las 6. <i>Retorno en USD (todo cripto cotiza en USD), ya neto de la comisión real de Binance por ciclo (~0.15%).</i>',
    crypto_carry_ethusdt: 'Mismo mecanismo que Carry BTC pero sobre ETH: comprás spot, vendés el futuro perpetuo, cobrás el funding. Delta-neutral, ejecutable con plata real. <i>Retorno en USD, ya neto de la comisión real de Binance por ciclo (~0.15%).</i>',
    factor_investing: 'Rota el portfolio de CEDEARs hacia los que combinan mejor 3 factores: Momentum, Baja Volatilidad y Value. Da retorno real positivo pero todavía no pasa el filtro estadístico (DSR) con margen suficiente para confiar en que no sea ruido — sigue en investigación. <i>Retorno en USD, ya neto de la comisión real de ida y vuelta (~2.5%).</i>',
};

let loadedDecisiones = false;
let loadedAjustes = false;

function strategyLabel(name) {
    return STRATEGY_LABELS[name] || name;
}

function statusPillHtml(strategy) {
    const s = STATUS_PILL[strategy.status] || { label: strategy.status, cls: 'info' };
    const pausedTag = strategy.status === 'paper_trading' && strategy.paper_trading_paused ? ' (pausada)' : '';
    return `<span class="status-pill ${s.cls}">${s.label}${pausedTag}</span>`;
}

function renderCircuitBreakerBanner(elId, circuitBreaker) {
    const el = $(elId);
    if (!el) return;
    if (circuitBreaker.drawdown_pct === null) {
        el.className = 'status show info';
        el.textContent = 'Circuit breaker: sin historial suficiente para medir el drawdown todavía.';
        return;
    }
    if (circuitBreaker.tripped) {
        el.className = 'status show error';
        el.textContent = `⚠️ Circuit breaker ACTIVADO: caída real de ${fmtPct(circuitBreaker.drawdown_pct)} (límite del perfil activo: ${fmtPct(circuitBreaker.limit_pct)}). Todas las estrategias en full-auto se pasaron a apagadas.`;
    } else {
        el.className = 'status show success';
        el.textContent = `Circuit breaker OK: caída real ${fmtPct(circuitBreaker.drawdown_pct)}, dentro del límite del perfil activo (${fmtPct(circuitBreaker.limit_pct)}).`;
    }
}

// --- Decisiones > Estrategias (solo lectura) --------------------------------

function strategyNameWithTooltip(s) {
    const description = STRATEGY_DESCRIPTIONS[s.name];
    return `${strategyLabel(s.name)}${description ? infoTooltipHtml(`<div>${description}</div>`) : ''}`;
}

function renderReadOnlyTable(strategies) {
    const container = $('strategies-table-container');
    if (!container) return;
    if (!strategies.length) {
        container.innerHTML = '<p class="hint" style="padding:20px;">Todavía no se corrió ninguna validación de estrategia.</p>';
        return;
    }
    const rows = strategies.map((s) => `
        <tr>
            <td>${strategyNameWithTooltip(s)}</td>
            <td>${statusPillHtml(s)}</td>
            <td>${s.last_dsr === null || s.last_dsr === undefined ? '—' : fmt(s.last_dsr, 4)}</td>
            <td>${s.baseline_annualized_return_pct === null || s.baseline_annualized_return_pct === undefined ? '—' : fmtPct(s.baseline_annualized_return_pct, 1)}</td>
            <td>${s.baseline_sharpe === null || s.baseline_sharpe === undefined ? '—' : fmt(s.baseline_sharpe, 2)}</td>
            <td>${fmtPct(s.capital_pct, 1)}</td>
        </tr>
    `).join('');
    container.innerHTML = `
        <table>
            <thead><tr><th>Estrategia</th><th>Etapa</th><th>DSR</th><th>Retorno esp. (USD/año, neto de comisiones)</th><th>Sharpe esp.</th><th>Capital asignado</th></tr></thead>
            <tbody>${rows}</tbody>
        </table>
        <p class="hint" style="padding:12px 4px 0;">Todos los números de esta tabla están en <b>dólares (USD) por año</b> y <b>ya restan la comisión real</b> de cada operación (nunca brutos) — así mide el éxito todo el proyecto (ver vara de éxito en USD). "Retorno esp."/"Sharpe esp." son el resultado del backtest que pasó (o no) el filtro estadístico, no una garantía ni una proyección hacia adelante — tocá el ícono ⓘ junto a cada nombre para la explicación completa, incluida la advertencia de Canje (ese número es teórico, no plata real).</p>
    `;
}

export async function loadStrategiesReadOnly() {
    const container = $('strategies-table-container');
    if (container) container.innerHTML = loadingHtml('Cargando estrategias…');
    try {
        const { data } = await api.strategies();
        renderCircuitBreakerBanner('strategies-circuit-breaker-banner', data.circuit_breaker);
        renderReadOnlyTable(data.strategies);
    } catch (e) {
        if (container) container.innerHTML = '<p class="hint" style="padding:20px;">No se pudo cargar el Registro de Estrategias.</p>';
    }
}

// --- Decisiones > Estrategias: Decisiones de Ejecución Real -----------------
// A diferencia de la tabla de arriba (solo lectura), Aceptar/Rechazar ACA SI
// ejecuta contra el broker real (ver portfolio/execution_router.py::
// resolve_decision) -- lo que resuelva primero entre esto y el boton de
// Telegram gana, el otro canal queda en no-op (already_resolved=True).

const DECISION_STATUS_PILL = {
    pending_confirmation: { label: 'Pendiente de confirmación', cls: 'warning' },
    confirmed: { label: 'Confirmada', cls: 'success' },
    rejected: { label: 'Rechazada', cls: 'info' },
    auto_executed: { label: 'Ejecutada (full-auto)', cls: 'success' },
    executed: { label: 'Ejecutada', cls: 'success' },
    failed: { label: 'Falló', cls: 'error' },
    partially_filled: { label: 'Ejecución parcial — revisar a mano', cls: 'error' },
};

function legsSummaryHtml(decision) {
    return decision.legs.map((leg) => (
        `${leg.side === 'buy' ? 'Comprar' : 'Vender'} ${fmt(leg.quantity, 6)} ${leg.symbol} (${leg.market}) @ ${fmt(leg.price_usd, 2)} USD`
    )).join('<br>');
}

function executionDecisionActionsHtml(decision) {
    if (decision.status !== 'pending_confirmation') return '';
    return `
        <button type="button" class="btn btn-primary btn-sm execution-decision-accept-btn" data-id="${decision.id}">Aceptar</button>
        <button type="button" class="btn btn-secondary btn-sm execution-decision-reject-btn" data-id="${decision.id}">Rechazar</button>
    `;
}

function renderExecutionDecisions(decisions) {
    const container = $('execution-decisions-container');
    if (!container) return;
    if (!decisions.length) {
        container.innerHTML = '<p class="hint" style="padding:20px;">Sin decisiones de ejecución real todavía — ninguna estrategia está en modo Real con una acción pendiente.</p>';
        return;
    }
    const rows = decisions.map((d) => {
        const pill = DECISION_STATUS_PILL[d.status] || { label: d.status, cls: 'info' };
        return `
            <tr>
                <td>${strategyLabel(d.strategy_name)}</td>
                <td>${d.action}</td>
                <td>${legsSummaryHtml(d)}</td>
                <td>${d.reason}</td>
                <td><span class="status-pill ${pill.cls}">${pill.label}</span>${d.error_detalle ? `<br><span class="hint cell-warning">${d.error_detalle}</span>` : ''}</td>
                <td>${executionDecisionActionsHtml(d)}</td>
            </tr>
        `;
    }).join('');
    container.innerHTML = `
        <table>
            <thead><tr><th>Estrategia</th><th>Acción</th><th>Patas</th><th>Motivo</th><th>Estado</th><th></th></tr></thead>
            <tbody>${rows}</tbody>
        </table>
    `;
    container.querySelectorAll('.execution-decision-accept-btn').forEach((btn) => {
        btn.addEventListener('click', () => resolveExecutionDecisionClick(Number(btn.dataset.id), true));
    });
    container.querySelectorAll('.execution-decision-reject-btn').forEach((btn) => {
        btn.addEventListener('click', () => resolveExecutionDecisionClick(Number(btn.dataset.id), false));
    });
}

async function resolveExecutionDecisionClick(id, aprobada) {
    try {
        const { data: result } = await api.resolveExecutionDecision(id, aprobada);
        if (result.already_resolved) {
            showStatus('strategies-circuit-breaker-banner', 'info', `Esta decisión ya se había resuelto antes (vía ${result.decision.resolved_via}).`);
        }
    } catch (e) {
        const message = e?.response?.data?.error || 'No se pudo resolver la decisión.';
        showStatus('strategies-circuit-breaker-banner', 'error', message);
    } finally {
        await loadExecutionDecisions();
    }
}

export async function loadExecutionDecisions() {
    const container = $('execution-decisions-container');
    if (container) container.innerHTML = loadingHtml('Cargando decisiones de ejecución…');
    try {
        const { data } = await api.executionDecisions();
        renderExecutionDecisions(data);
    } catch (e) {
        if (container) container.innerHTML = '<p class="hint" style="padding:20px;">No se pudieron cargar las decisiones de ejecución.</p>';
    }
}

// --- Ajustes > Estrategia y Motores (controles) -----------------------------

function actionButtonsHtml(s) {
    if (s.status === 'validated') {
        return `<button type="button" class="btn btn-secondary btn-sm strategy-start-paper-btn" data-strategy="${s.name}">Arrancar Paper Trading</button>`;
    }
    if (s.status === 'paper_trading') {
        const toggleLabel = s.paper_trading_paused ? 'Continuar' : 'Pausar';
        const toggleValue = s.paper_trading_paused ? 'false' : 'true';
        return `
            <button type="button" class="btn btn-secondary btn-sm strategy-toggle-pause-btn" data-strategy="${s.name}" data-paused="${toggleValue}">${toggleLabel}</button>
            <button type="button" class="btn btn-primary btn-sm strategy-promote-btn" data-strategy="${s.name}">Promover a Real</button>
        `;
    }
    if (s.status === 'live') {
        const options = ['off', 'semi_auto', 'full_auto'].map((mode) => {
            const selected = mode === s.automation_mode ? 'selected' : '';
            const modeLabel = { off: 'Apagada', semi_auto: 'Semi-auto', full_auto: 'Full-auto' }[mode];
            return `<option value="${mode}" ${selected}>${modeLabel}</option>`;
        }).join('');
        return `<select class="strategy-automation-select" data-strategy="${s.name}">${options}</select>`;
    }
    return '<span class="hint">Esperando pasar el filtro estadístico.</span>';
}

function renderControlsTable(strategies) {
    const container = $('strategies-controls-container');
    if (!container) return;
    if (!strategies.length) {
        container.innerHTML = '<p class="hint" style="padding:20px;">Todavía no se corrió ninguna validación de estrategia.</p>';
        return;
    }
    const rows = strategies.map((s) => `
        <tr>
            <td>${strategyNameWithTooltip(s)}</td>
            <td>${statusPillHtml(s)}</td>
            <td>${fmtPct(s.capital_pct, 1)}</td>
            <td>${actionButtonsHtml(s)}</td>
        </tr>
    `).join('');
    container.innerHTML = `
        <table>
            <thead><tr><th>Estrategia</th><th>Etapa</th><th>Capital asignado</th><th>Acción</th></tr></thead>
            <tbody>${rows}</tbody>
        </table>
    `;
}

export async function loadStrategiesControls() {
    const container = $('strategies-controls-container');
    if (container) container.innerHTML = loadingHtml('Cargando estrategias…');
    try {
        const { data } = await api.strategies();
        renderCircuitBreakerBanner('strategies-settings-circuit-breaker-banner', data.circuit_breaker);
        renderControlsTable(data.strategies);
    } catch (e) {
        if (container) container.innerHTML = '<p class="hint" style="padding:20px;">No se pudo cargar el Registro de Estrategias.</p>';
    }
}

// El cambio de estado ya se ve solo (la tabla se re-renderiza con la etapa
// nueva) -- este helper solo necesita mostrar algo cuando el servidor
// RECHAZA la accion (ValueError del backend, ver portfolio/routes.py), que
// es el unico caso donde la tabla no cambia y el usuario necesita saber por que.
async function withActionFeedback(action) {
    try {
        await action();
    } catch (err) {
        const message = err?.response?.data?.error || 'No se pudo completar la acción.';
        showStatus('strategies-settings-circuit-breaker-banner', 'error', message);
    } finally {
        await loadStrategiesControls();
    }
}

async function handleControlsClick(e) {
    const startBtn = e.target.closest('.strategy-start-paper-btn');
    if (startBtn) {
        await withActionFeedback(() => api.startStrategyPaperTrading(startBtn.dataset.strategy));
        return;
    }
    const pauseBtn = e.target.closest('.strategy-toggle-pause-btn');
    if (pauseBtn) {
        const paused = pauseBtn.dataset.paused === 'true';
        await withActionFeedback(() => api.setStrategyPaperTradingPaused(pauseBtn.dataset.strategy, paused));
        return;
    }
    const promoteBtn = e.target.closest('.strategy-promote-btn');
    if (promoteBtn) {
        await withActionFeedback(() => api.promoteStrategyToLive(promoteBtn.dataset.strategy));
    }
}

async function handleControlsChange(e) {
    const select = e.target.closest('.strategy-automation-select');
    if (!select) return;
    await withActionFeedback(() => api.setStrategyAutomationMode(select.dataset.strategy, select.value));
}

function loadReadOnlyIfNeeded() {
    if (loadedDecisiones) return;
    loadedDecisiones = true;
    loadStrategiesReadOnly();
    loadExecutionDecisions();
}

function loadControlsIfNeeded() {
    if (loadedAjustes) return;
    loadedAjustes = true;
    loadStrategiesControls();
}

export function initEstrategiasView() {
    const readOnlyContainer = $('strategies-table-container');
    const estrategiasBtn = document.querySelector('[data-subtabs="decisiones"] [data-subtab-target="estrategias"]');
    if (estrategiasBtn) estrategiasBtn.addEventListener('click', loadReadOnlyIfNeeded);
    if (document.querySelector('[data-subtab-panel="estrategias"]')?.classList.contains('active')) loadReadOnlyIfNeeded();

    const controlsContainer = $('strategies-controls-container');
    if (controlsContainer) {
        controlsContainer.addEventListener('click', handleControlsClick);
        controlsContainer.addEventListener('change', handleControlsChange);
    }
    const estrategiaBtn = document.querySelector('[data-subtabs="ajustes"] [data-subtab-target="estrategia"]');
    if (estrategiaBtn) estrategiaBtn.addEventListener('click', loadControlsIfNeeded);
    if (document.querySelector('[data-subtab-panel="estrategia"]')?.classList.contains('active')) loadControlsIfNeeded();
}
