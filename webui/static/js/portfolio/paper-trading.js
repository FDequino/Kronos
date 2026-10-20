// Paper Trading / dinero ficticio (ver conversacion): panel de la subtab
// "Paper Trading" dentro de Decisiones -- visualiza lo que el scanner
// intradia en background (portfolio/monitor.py, 11:00-17:00 ART) ya abrió o
// cerró. Esta pantalla NUNCA abre/cierra posiciones por si misma, solo lee
// GET /api/portfolio/paper-trading/* y deja prender/apagar el flag y
// cambiar el capital inicial virtual. Misma carga perezosa que
// core-satellite.js.
import { $ } from '../shared/dom.js';
import { fmt, fmtMoney, fmtPct, fmtQty } from '../shared/format.js';
import { showStatus } from '../shared/status.js';
import { api } from './api.js';
import { SHIELD_ICON, BOLT_ICON, TARGET_ICON, STOP_ICON, CLOCK_ICON } from '../shared/icons.js';

let loaded = false;

// Nombres consistentes con el resto de la app (ver auditoría 19/10/2026,
// hallazgo 2.4: "el motor corto plazo se llama 'Estrategia Corto Plazo' acá
// pero 'Satélite' en el resto" -- antes eran "Fondo de Mediano Plazo"/
// "Estrategia Corto Plazo", dos alias propios de esta pantalla sin motivo).
const ENGINE_LABELS = {
    core: `<span class="inline-icon">${SHIELD_ICON}</span> Core`,
    satellite: `<span class="inline-icon">${BOLT_ICON}</span> Satélite`,
};
const EXIT_REASON_LABELS = {
    take_profit: `<span class="inline-icon">${TARGET_ICON}</span> Meta de Ganancia`,
    stop_loss: `<span class="inline-icon">${STOP_ICON}</span> Límite de Pérdida`,
    max_hold: `<span class="inline-icon">${CLOCK_ICON}</span> Tiempo máximo`,
    manual: 'Manual',
};

function signedMoney(value, currency = 'USD') {
    return fmtMoney(value, currency, 2, { signed: true });
}

function signedPct(value) {
    return fmtPct(value, 1, { signed: true });
}

function dualPrice(usd, ars) {
    if (usd === null || usd === undefined) return '—';
    return `${fmtMoney(usd, 'USD')} <span class="hint">(≈${fmtMoney(ars)})</span>`;
}

// Interconexión entre pantallas (ver conversación, pase de UX/UI): cualquier
// símbolo lleva a su /detalle, acá como en el resto de la app.
function symbolLinkHtml(symbol, assetType) {
    const params = new URLSearchParams({ market: 'byma', assetType: assetType || '' });
    return `<a href="#detalle/${encodeURIComponent(symbol)}?${params.toString()}" class="table-link">${symbol}</a>`;
}

async function loadSettings() {
    try {
        const { data } = await api.paperTradingSettings();
        $('paper-trading-enabled-toggle').checked = !!data.enabled;
        $('paper-trading-initial-capital-input').value = data.initial_capital_usd;
    } catch { /* se queda con lo que ya estaba en pantalla si falla */ }
}

async function saveSettings() {
    try {
        const { data } = await api.updatePaperTradingSettings({
            enabled: $('paper-trading-enabled-toggle').checked,
            initial_capital_usd: Number($('paper-trading-initial-capital-input').value),
        });
        $('paper-trading-enabled-toggle').checked = !!data.enabled;
        $('paper-trading-initial-capital-input').value = data.initial_capital_usd;
        showStatus('status-banner', 'success', 'Paper Trading actualizado.');
    } catch (error) {
        showStatus('status-banner', 'error', error.response?.data?.error || error.message);
    }
}

async function loadSummary() {
    const container = $('paper-trading-summary');
    try {
        const { data } = await api.paperTradingSummary();
        container.innerHTML = `
            <div class="mini-stat"><div class="l">Patrimonio virtual (USD CCL)</div><div class="v">${fmtMoney(data.equity_usd, 'USD')}</div></div>
            <div class="mini-stat"><div class="l">Caja virtual (USD)</div><div class="v">${fmtMoney(data.cash_usd, 'USD')}</div></div>
            <div class="mini-stat"><div class="l">Resultado del día</div><div class="v num">${data.resultado_dia_usd === null ? '—' : `${signedMoney(data.resultado_dia_usd, 'USD')} (${signedPct(data.resultado_dia_pct)})`}</div></div>
            <div class="mini-stat"><div class="l">Trades cerrados hoy</div><div class="v num">${data.trades_closed_today_count}</div></div>
            <div class="mini-stat"><div class="l">Posiciones abiertas</div><div class="v num">${data.open_positions_count}</div></div>
        `;
    } catch (error) {
        container.innerHTML = `<p class="table-empty">No se pudo cargar el resumen: ${error.response?.data?.error || error.message}</p>`;
    }
}

async function loadPositions() {
    const container = $('paper-trading-positions-container');
    try {
        const { data } = await api.paperTradingPositions();
        if (!data.length) {
            container.innerHTML = '<p class="table-empty">No hay posiciones virtuales abiertas.</p>';
            return;
        }
        container.innerHTML = `
            <div class="table-scroll">
                <table>
                    <thead><tr>
                        <th>Activo</th><th>Motor</th><th>Entrada</th><th>Actual</th>
                        <th>Take Profit</th><th>Stop</th><th>Cantidad</th><th>PnL no realizado</th>
                    </tr></thead>
                    <tbody>${data.map((p) => `
                        <tr>
                            <td>${symbolLinkHtml(p.symbol, p.asset_type)}${p.tactic ? `<br><span class="hint">${p.tactic}</span>` : ''}</td>
                            <td>${ENGINE_LABELS[p.engine] || p.engine}</td>
                            <td>${dualPrice(p.entry_price_usd, p.entry_price_ars)}</td>
                            <td>${dualPrice(p.current_price_usd, p.current_price_ars)}</td>
                            <td>${dualPrice(p.take_profit_price_usd, p.take_profit_price_ars)}</td>
                            <td>${dualPrice(p.stop_loss_price_usd, p.stop_loss_price_ars)}</td>
                            <td>${fmtQty(p.quantity)}</td>
                            <td>${p.unrealized_pnl_usd === null ? '—' : `${signedMoney(p.unrealized_pnl_usd, 'USD')} (${signedPct(p.unrealized_pnl_pct)})`}</td>
                        </tr>
                    `).join('')}</tbody>
                </table>
            </div>
        `;
    } catch (error) {
        container.innerHTML = `<p class="table-empty">No se pudieron cargar las posiciones: ${error.response?.data?.error || error.message}</p>`;
    }
}

async function loadTrades() {
    const container = $('paper-trading-trades-container');
    try {
        const { data } = await api.paperTradingTrades(100);
        if (!data.length) {
            container.innerHTML = '<p class="table-empty">Todavía no se cerró ninguna operación simulada.</p>';
            return;
        }
        container.innerHTML = `
            <div class="table-scroll">
                <table>
                    <thead><tr>
                        <th>Activo</th><th>Motor</th><th>Entrada</th><th>Salida</th><th>Motivo</th><th>PnL</th>
                    </tr></thead>
                    <tbody>${data.map((t) => `
                        <tr>
                            <td>${symbolLinkHtml(t.symbol, t.asset_type)}</td>
                            <td>${ENGINE_LABELS[t.engine] || t.engine}</td>
                            <td>${t.entry_date} · ${dualPrice(t.entry_price_usd, t.entry_price_ars)}</td>
                            <td>${t.exit_date} · ${dualPrice(t.exit_price_usd, t.exit_price_ars)}</td>
                            <td>${EXIT_REASON_LABELS[t.exit_reason] || t.exit_reason}</td>
                            <td>${signedMoney(t.pnl_usd, 'USD')} (${signedPct(t.pnl_pct)})</td>
                        </tr>
                    `).join('')}</tbody>
                </table>
            </div>
        `;
    } catch (error) {
        container.innerHTML = `<p class="table-empty">No se pudo cargar el historial: ${error.response?.data?.error || error.message}</p>`;
    }
}

async function loadPaperTradingIfNeeded() {
    if (loaded) return;
    loaded = true;
    await loadSettings();
    await Promise.all([loadSummary(), loadPositions(), loadTrades()]);
}

export function initPaperTradingView() {
    const subtabBtn = document.querySelector('[data-subtabs="decisiones"] [data-subtab-target="paper-trading"]');
    if (subtabBtn) subtabBtn.addEventListener('click', loadPaperTradingIfNeeded);
    if (document.querySelector('[data-subtab-panel="paper-trading"]')?.classList.contains('active')) loadPaperTradingIfNeeded();

    const saveBtn = $('paper-trading-save-settings-btn');
    if (saveBtn) saveBtn.addEventListener('click', saveSettings);
}
