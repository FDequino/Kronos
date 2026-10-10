import { api } from './api.js';
import { pollForecast } from './forecast-polling.js';
import { fmtPct } from '../shared/format.js';

const SIGNAL_LABEL = { comprar: 'Comprar', mantener: 'Mantener', vender: 'Vender' };
const SIGNAL_TAG_CLASS = { comprar: 'tag-positive', mantener: 'tag-neutral', vender: 'tag-negative' };

export function createForecastCard({ symbol, instrumentId = null, market = 'byma', assetType }) {
    const el = document.createElement('div');
    el.className = 'forecast-card';
    el.innerHTML = `
        <div class="forecast-card-header">
            <span class="forecast-card-symbol">${symbol}</span>
            <span class="tag tag-neutral forecast-card-badge">Sin pronóstico</span>
        </div>
        <p class="forecast-card-meta hint" hidden></p>
        <button class="btn btn-secondary forecast-card-toggle" type="button">Ver detalle</button>
    `;

    const badge = el.querySelector('.forecast-card-badge');
    const meta = el.querySelector('.forecast-card-meta');
    const detailBtn = el.querySelector('.forecast-card-toggle');

    function updateBadge(signal, predChangePct) {
        badge.textContent = SIGNAL_LABEL[signal] || 'Sin pronóstico';
        badge.className = `tag forecast-card-badge ${SIGNAL_TAG_CLASS[signal] || 'tag-neutral'}`;
        if (predChangePct !== null && predChangePct !== undefined) {
            const dir = predChangePct >= 0 ? 'cell-positive' : 'cell-negative';
            const arrow = predChangePct >= 0 ? '▲' : '▼';
            meta.innerHTML = `Pronóstico Kronos: <span class="${dir}">${arrow} ${fmtPct(Math.abs(predChangePct))}</span>`;
            meta.hidden = false;
        }
    }

    function updateProgress(pct) {
        badge.textContent = `Corriendo… ${pct}%`;
        badge.className = 'tag tag-neutral forecast-card-badge';
    }

    detailBtn.addEventListener('click', () => {
        const params = new URLSearchParams({
            market,
            ...(assetType ? { assetType } : {}),
            ...(instrumentId ? { instrumentId } : {}),
        });
        location.hash = `#detalle/${encodeURIComponent(symbol)}?${params.toString()}`;
    });

    const latestParams = instrumentId ? { instrument_id: instrumentId } : { symbol, market };
    api.latestForecast(latestParams)
        .then(({ data }) => {
            if (!data) return;
            if (data.status === 'done') {
                updateBadge(data.result.signal, data.pred_change_pct);
            } else if (data.status === 'queued' || data.status === 'running') {
                updateProgress(data.progress_pct ?? 0);
                pollForecast(`/api/portfolio/forecasts/${data.id}`, {
                    onProgress: updateProgress,
                    onDone: (result) => updateBadge(result.result.signal, result.pred_change_pct),
                    onError: () => updateBadge(null),
                });
            }
        })
        .catch(() => {});

    return el;
}
