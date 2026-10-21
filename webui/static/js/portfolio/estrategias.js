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

function renderReadOnlyTable(strategies) {
    const container = $('strategies-table-container');
    if (!container) return;
    if (!strategies.length) {
        container.innerHTML = '<p class="hint" style="padding:20px;">Todavía no se corrió ninguna validación de estrategia.</p>';
        return;
    }
    const rows = strategies.map((s) => `
        <tr>
            <td>${strategyLabel(s.name)}</td>
            <td>${statusPillHtml(s)}</td>
            <td>${s.last_dsr === null || s.last_dsr === undefined ? '—' : fmt(s.last_dsr, 4)}</td>
            <td>${fmtPct(s.capital_pct, 1)}</td>
        </tr>
    `).join('');
    container.innerHTML = `
        <table>
            <thead><tr><th>Estrategia</th><th>Etapa</th><th>DSR</th><th>Capital asignado</th></tr></thead>
            <tbody>${rows}</tbody>
        </table>
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
            <td>${strategyLabel(s.name)}</td>
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
