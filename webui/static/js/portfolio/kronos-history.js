// Precisión de Kronos por instrumento -- leaderboard comparativo (ver
// portfolio/accuracy.py::build_forecast_history). Antes esta vista tenía su
// propio selector de instrumento con el detalle completo (fan chart +
// calibración) por cada uno; ese detalle ahora vive directo en la página de
// cada instrumento (pestaña Precisión, ver instrument-detail.js) -- acá
// sólo queda el resumen "quién viene acertando más" entre todos, dentro de
// la pestaña "Rendimiento Histórico" de Decisiones (ver conversación,
// limpieza de UI). La sub-vista "Cartera" (real vs. teórico Kronos) se
// eliminó -- ya está cubierta por la vista Patrimonio.
import { $ } from '../shared/dom.js';
import { fmt } from '../shared/format.js';
import { api } from './api.js';
import { createSorter, wireSortableHeaders } from '../shared/sortable-table.js';
import { headerCellHtml, updateSortIndicators } from '../shared/table-chrome.js';
import { pollBatch } from './forecast-polling.js';
import { loadingHtml } from '../shared/loading.js';

const ALL_COLUMNS = [
    { key: 'symbol', label: 'Activo', numeric: false },
    { key: 'mape', label: 'MAPE', numeric: true, term: 'mape' },
    { key: 'directional_hit_pct', label: 'Acierto direccional', numeric: true, term: 'acierto_direccion' },
    { key: 'forecasts_evaluated', label: 'No superpuestos', numeric: true, term: 'pronosticos_no_superpuestos' },
    { key: 'calibration_pct', label: 'Calibración', numeric: true, term: 'calibracion_kronos' },
];

let loaded = false;
let rows = [];
const sorter = createSorter();

function rowHtml(r) {
    const params = new URLSearchParams({ instrumentId: r.instrument_id });
    return `
        <tr>
            <td><a href="#detalle/${encodeURIComponent(r.symbol)}?${params.toString()}" class="table-link">${r.symbol}</a></td>
            <td class="num">${r.mape !== null ? `${fmt(r.mape, 2)}%` : '—'}</td>
            <td class="num">${r.directional_hit_pct !== null ? `${fmt(r.directional_hit_pct, 1)}%` : '—'}</td>
            <td class="num">${r.forecasts_evaluated}</td>
            <td class="num">${r.calibration_pct !== null ? `${fmt(r.calibration_pct, 1)}% (n=${r.calibration_n})` : '—'}</td>
        </tr>
    `;
}

// Ver conversación (pase de UX/UI): no mostrar filas 100% vacías (guiones en
// todas las columnas) -- forecasts_evaluated=0 pasa seguido ahora que el
// pronóstico se dispara solo al entrar al detalle de un activo (ver
// instrument-detail.js): queda UN pronóstico recién corrido, pendiente de
// que pase el tiempo para resolverse, sin ningún MAPE real todavía. Esos
// instrumentos se listan aparte, con un atajo real para calibrarlos YA
// (backtest histórico, que corre sobre fechas pasadas y resuelve al toque,
// sin esperar al calendario).
function pendingRowHtml(r) {
    return `
        <div class="kronos-pending-row" data-instrument-id="${r.instrument_id}">
            <a href="#detalle/${encodeURIComponent(r.symbol)}?instrumentId=${r.instrument_id}" class="table-link">${r.symbol}</a>
            <span class="hint">Sin pronósticos resueltos todavía</span>
            <button type="button" class="btn btn-secondary btn-compact kronos-calibrar-btn" data-instrument-id="${r.instrument_id}" data-symbol="${r.symbol}">Calibrar</button>
        </div>
    `;
}

function render() {
    const container = $('rendimiento-kronos-leaderboard');
    if (!container) return;
    if (!rows.length) {
        container.innerHTML = '<p class="table-empty">Ningún instrumento tiene pronósticos corridos todavía.</p>';
        return;
    }
    const calibrated = rows.filter((r) => r.forecasts_evaluated > 0);
    const pending = rows.filter((r) => r.forecasts_evaluated === 0);
    const sorted = sorter.key ? sorter.apply(calibrated, (r, key) => r[key]) : calibrated;

    const tableHtml = sorted.length ? `
        <div class="table-scroll">
            <table>
                <thead><tr>${ALL_COLUMNS.map((c) => headerCellHtml(c)).join('')}</tr></thead>
                <tbody>${sorted.map(rowHtml).join('')}</tbody>
            </table>
        </div>
    ` : '<p class="table-empty">Todavía ningún instrumento tiene pronósticos ya resueltos — calibrá alguno de la lista de abajo.</p>';

    const pendingHtml = pending.length ? `
        <h3 class="detail-section-title">Sin calibrar todavía (${pending.length})</h3>
        <p class="hint">Tienen pronósticos corridos, pero ninguno ya resuelto contra un precio real — corré un backtest histórico para calibrarlos al instante.</p>
        <div class="kronos-pending-list">${pending.map(pendingRowHtml).join('')}</div>
    ` : '';

    container.innerHTML = tableHtml + pendingHtml;
    if (sorted.length) {
        wireSortableHeaders(container, sorter, render);
        updateSortIndicators(container, sorter);
    }
    container.querySelectorAll('.kronos-calibrar-btn').forEach((btn) => {
        btn.addEventListener('click', () => calibrarInstrumento(btn));
    });
}

async function calibrarInstrumento(btn) {
    const instrumentId = Number(btn.dataset.instrumentId);
    const symbol = btn.dataset.symbol;
    const row = btn.closest('.kronos-pending-row');
    btn.disabled = true;
    const statusEl = document.createElement('span');
    statusEl.className = 'hint';
    row.appendChild(statusEl);
    try {
        const { data } = await api.backtestInstrument(instrumentId);
        if (!data.forecast_ids.length) {
            statusEl.textContent = 'Ya hay backtest corrido para todo el rango disponible.';
            btn.disabled = false;
            return;
        }
        statusEl.textContent = `Calibrando ${symbol}… 0/${data.forecast_ids.length}`;
        pollBatch(data.forecast_ids, {
            onProgress: ({ total, done }) => { statusEl.textContent = `Calibrando ${symbol}… ${done}/${total}`; },
            onDone: async () => {
                await loadLeaderboard();
            },
        });
    } catch (error) {
        statusEl.textContent = `Error: ${error.response?.data?.error || error.message}`;
        btn.disabled = false;
    }
}

async function loadLeaderboard() {
    const container = $('rendimiento-kronos-leaderboard');
    try {
        const { data } = await api.forecastsHistory({});
        rows = data.instruments;
        render();
    } catch (error) {
        container.innerHTML = `<p class="table-empty">No se pudo cargar: ${error.response?.data?.error || error.message}</p>`;
    }
}

async function loadIfNeeded() {
    if (loaded) return;
    loaded = true;
    const container = $('rendimiento-kronos-leaderboard');
    container.innerHTML = loadingHtml('Cargando precisión por instrumento…');
    await loadLeaderboard();
}

export function initKronosHistoryView() {
    const rendimientoBtn = document.querySelector('[data-subtabs="decisiones"] [data-subtab-target="rendimiento"]');
    if (rendimientoBtn) rendimientoBtn.addEventListener('click', loadIfNeeded);
    if (document.querySelector('[data-subtab-panel="rendimiento"]')?.classList.contains('active')) loadIfNeeded();
}
