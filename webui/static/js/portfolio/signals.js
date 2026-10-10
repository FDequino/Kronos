import { $ } from '../shared/dom.js';
import { fmt } from '../shared/format.js';
import { api } from './api.js';
import { createForecastCard } from './forecast-card.js';

export async function loadSignals() {
    const { data: holdings } = await api.holdings();
    const grid = $('signals-grid');
    const fundsNote = $('signals-funds-note');
    grid.innerHTML = '';
    fundsNote.innerHTML = '';

    if (!holdings.length) {
        grid.innerHTML = '<p class="hint">Sin tenencias todavía — cargá un instrumento y una compra para ver señales acá.</p>';
        return;
    }

    const forecastable = holdings.filter((h) => h.instrument.type !== 'fondo');
    const funds = holdings.filter((h) => h.instrument.type === 'fondo');

    forecastable.forEach((h) => {
        grid.appendChild(createForecastCard({
            symbol: h.instrument.symbol,
            instrumentId: h.instrument.id,
            market: h.instrument.market,
            assetType: h.instrument.type,
        }));
    });

    if (funds.length) {
        const list = funds.map((h) => `${h.instrument.symbol} (${fmt(h.cantidad, 2)} cuotapartes)`).join(', ');
        fundsNote.innerHTML = `<p class="hint">Sin pronóstico: ${list} — Kronos no tiene histórico de precios para fondos comunes.</p>`;
    }
}

async function runAll() {
    const btn = $('run-all-signals-btn');
    btn.disabled = true;
    try {
        await api.runAllForecasts(false);
        await loadSignals();
    } finally {
        btn.disabled = false;
    }
}

export function initSignalsView() {
    $('run-all-signals-btn').addEventListener('click', runAll);
}
