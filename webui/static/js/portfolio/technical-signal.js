// Señal técnica (RSI) + edge histórico + filtro fundamental + Kronos, por
// holding -- extraído de signals.js (Fase 3) para que Posiciones del
// Dashboard (Fase 5) lo reutilice en vez de reimplementar las mismas 4
// llamadas a la API. Un edge solo se considera "real" por encima de este
// umbral -- por debajo, la señal técnica se muestra gris aunque exista.
import { fmt } from '../shared/format.js';
import { api } from './api.js';

export const EDGE_SIGNIFICANT_PTS = 5;
export const SIGNAL_LABEL = { comprar: 'Comprar', mantener: 'Mantener', vender: 'Vender' };

// prefetchedForecast: si el caller ya tiene el ultimo forecast de este holding
// (ej. Posiciones del Dashboard, que lo pide igual para la columna Kronos),
// se lo pasa para no duplicar esa llamada -- undefined (no pasado) hace que
// se pida igual, para no romper a signals.js que no lo tiene de antes.
//
// prefetchedTechnical (Fase 9): el Dashboard trae rsi/tecnico/edge/n ya
// resueltos y cacheados por dia desde /dashboard-bundle (ver
// portfolio/routes.py::_get_technical_row) -- si viene, se saltean las 3
// llamadas en vivo (instrumentChart/technicalBacktest/fundamentals) que antes
// se repetian por cada tenencia en cada carga de pagina (~18-30s con 36
// instrumentos). Señales no lo tiene (sigue pidiendo en vivo, sin cambios) y
// sigue funcionando igual que antes -- es un atajo opcional, no un cambio de
// contrato. `fundamentals`/`fundamentalPass` quedan null en este camino: hoy
// ningun consumidor del Dashboard los lee (solo signals.js lo hace, y ese no
// usa este atajo).
export async function buildTechnicalRow(holding, config, prefetchedForecast, prefetchedTechnical) {
    const id = holding.instrument.id;

    if (prefetchedTechnical) {
        const forecast = prefetchedForecast !== undefined
            ? prefetchedForecast
            : await api.latestForecast({ instrument_id: id }).then((r) => r.data).catch(() => null);
        return {
            holding,
            rsi: prefetchedTechnical.rsi,
            tecnico: prefetchedTechnical.tecnico,
            edge: prefetchedTechnical.edge,
            n: prefetchedTechnical.n,
            fundamentals: null,
            fundamentalPass: prefetchedTechnical.fundamental_pass,
            forecast,
        };
    }

    const [chart, backtest, fundamentals, forecast] = await Promise.all([
        api.instrumentChart(id).then((r) => r.data).catch(() => null),
        api.instrumentTechnicalBacktest(id).then((r) => r.data).catch(() => null),
        api.instrumentFundamentals(id).then((r) => r.data).catch(() => null),
        prefetchedForecast !== undefined ? Promise.resolve(prefetchedForecast) : api.latestForecast({ instrument_id: id }).then((r) => r.data).catch(() => null),
    ]);

    const rsiSeries = chart?.indicators?.rsi_14 || [];
    const rsi = rsiSeries.length ? rsiSeries[rsiSeries.length - 1] : null;
    const tecnico = chart?.technical_signal || null;

    let edge = null;
    let n = null;
    if (backtest && !backtest.pending && tecnico && backtest.by_signal?.[tecnico]) {
        const bucket = backtest.by_signal[tecnico];
        edge = bucket.edge_vs_base_rate_pct ?? null;
        n = bucket.count ?? null;
    }

    let fundamentalPass = null;
    if (fundamentals?.available && fundamentals.pe_ratio != null && fundamentals.profit_margin != null) {
        fundamentalPass = fundamentals.pe_ratio < config.fa_max_pe && fundamentals.profit_margin > config.fa_min_margin_pct / 100;
    }

    return { holding, rsi, tecnico, edge, n, fundamentals, fundamentalPass, forecast };
}

export function edgeSignificant(edge) {
    return edge !== null && edge !== undefined && Math.abs(edge) >= EDGE_SIGNIFICANT_PTS;
}

// Celda compartida: texto gris si el edge no supera el umbral (aunque haya
// señal), coloreado solo si supera -- mismo criterio en Señales y Posiciones.
// cell-signal (acento), nunca cell-positive/cell-negative -- esto es un
// indicador tecnico, no P&L real (ver DESIGN_SYSTEM.md). La direccion
// (Comprar/Vender) la sigue diciendo la palabra misma, el color solo marca
// "hay edge real detras".
export function technicalCellHtml(row) {
    if (!row.tecnico) return '<span class="hint">—</span>';
    const significant = edgeSignificant(row.edge);
    const cls = significant ? 'cell-signal' : 'cell-muted';
    return `<span class="${cls}">${SIGNAL_LABEL[row.tecnico] || row.tecnico}</span>`;
}

export function edgeCellHtml(row) {
    return fmt(row.edge, 1, { signed: true });
}
