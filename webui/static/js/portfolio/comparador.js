// Comparador de instrumentos (Fase 13, §11.4 del prompt del usuario): lado a
// lado, no hace falta que esten en cartera -- reusa los mismos endpoints que
// ya alimentan el detalle de instrumento, sin duplicar logica de calculo.
import { $ } from '../shared/dom.js';
import { fmt, fmtPct, fmtMoney } from '../shared/format.js';
import { api } from './api.js';
import { fetchInstrumentsList } from './instruments.js';
import { instrumentClase } from './instrument-clase.js';
import { glossaryTerm } from '../shared/glossary.js';
import { evaluateFundamental } from './instrument-detail.js';
import { EDGE_SIGNIFICANT_PTS, SIGNAL_LABEL } from './technical-signal.js';

const MAX_INSTRUMENTS = 4;
let allInstruments = [];
let selected = [];
// Config real de Preferencias (umbral de P/E, margen) -- se pide una sola vez,
// no por columna, para no repetir la misma llamada 4 veces.
let signalConfig = null;

function tagHtml(text, cls) {
    return `<span class="tag ${cls}">${text}</span>`;
}

// --- Veredictos (Fase 17): "no entiendo qué es cada cosa" + "que haya un
// resumen de según tal análisis comprar, según tal otro vender" -- cada
// dimensión se resuelve a UN tag (Comprar/Vender/Mantener/Bajo/Medio/Alto/
// Atractivo/Caro/Neutral), reutilizado tanto en la fila "Resumen" como al
// principio de su propia fila de detalle.
// tag-signal-buy/sell (acento), nunca tag-positive/tag-negative -- es una
// señal predictiva, no P&L real (ver DESIGN_SYSTEM.md).
function kronosVerdict(forecast) {
    if (!forecast || forecast.status !== 'done') return null;
    const validation = forecast.validation;
    if (!validation?.validated) return { label: 'Sin validar', cls: 'tag-neutral' };
    const signal = forecast.result?.signal;
    const cls = signal === 'comprar' ? 'tag-signal-buy' : signal === 'vender' ? 'tag-signal-sell' : 'tag-neutral';
    return { label: SIGNAL_LABEL[signal] || signal, cls };
}

function riesgoVerdict(risk) {
    if (!risk?.available) return null;
    const cls = { bajo: 'tag-positive', medio: 'tag-neutral', alto: 'tag-negative' }[risk.label] || 'tag-neutral';
    return { label: risk.label[0].toUpperCase() + risk.label.slice(1), cls };
}

// Entre los buckets comprar/vender, el "accionable" es el que tiene edge
// significativo (>= EDGE_SIGNIFICANT_PTS puntos, mismo umbral que ya usa
// Señales/Posiciones) -- un edge chico o no direccional no alcanza para decir
// "comprá" o "vendé", por más que el número no sea exactamente 0.
// tag-signal-buy/sell (acento), nunca tag-positive/tag-negative -- es un
// indicador tecnico, no P&L real (ver DESIGN_SYSTEM.md).
function tecnicoVerdict(backtest) {
    if (backtest?.pending || !backtest?.by_signal) return null;
    const comprar = backtest.by_signal.comprar?.edge_vs_base_rate_pct;
    const vender = backtest.by_signal.vender?.edge_vs_base_rate_pct;
    if (comprar !== null && comprar !== undefined && comprar >= EDGE_SIGNIFICANT_PTS) return { label: 'Comprar', cls: 'tag-signal-buy' };
    if (vender !== null && vender !== undefined && vender >= EDGE_SIGNIFICANT_PTS) return { label: 'Vender', cls: 'tag-signal-sell' };
    return { label: 'Sin edge', cls: 'tag-neutral' };
}

// Mismo criterio que ya usa la pestaña Fundamental del detalle de instrumento
// (evaluateFundamental, Preferencias -> umbral de P/E real) -- "atractivo" si
// pasa tu propio umbral configurado, no un corte inventado acá.
function fundamentalVerdict(fund, config) {
    if (!fund || fund.not_applicable || !fund.available || !config) return null;
    const { cls } = evaluateFundamental('pe_ratio', fund.pe_ratio, config);
    if (cls === 'cell-positive') return { label: 'Atractivo', cls: 'tag-positive' };
    if (cls === 'cell-negative') return { label: 'Caro', cls: 'tag-negative' };
    return { label: 'Neutral', cls: 'tag-neutral' };
}

function resumenCellHtml(c) {
    const items = [
        ['Kronos', kronosVerdict(c.forecast)],
        ['Riesgo', riesgoVerdict(c.risk)],
        ['Técnico', tecnicoVerdict(c.backtest)],
        ['Fundamental', fundamentalVerdict(c.fundamentals, signalConfig)],
    ].filter(([, v]) => v);
    if (!items.length) return '<span class="hint">Sin datos suficientes todavía.</span>';
    return items.map(([label, v]) => `
        <div class="stat-detail-row"><span class="stat-detail-label">${label}</span>${tagHtml(v.label, v.cls)}</div>
    `).join('');
}

function pronosticoCellHtml(forecast) {
    if (!forecast || forecast.status !== 'done') return '<span class="hint">Sin pronóstico corrido.</span>';
    const result = forecast.result;
    const dist = result?.distribution;
    const verdict = kronosVerdict(forecast);
    const validation = forecast.validation;
    if (!dist) return `${tagHtml(verdict.label, verdict.cls)}<p class="hint" title="${validation?.reason || ''}">${validation?.reason || ''}</p>`;
    return `
        ${tagHtml(verdict.label, verdict.cls)}
        <div class="stat-detail-row"><span class="stat-detail-label">Mediana</span><span class="num cell-signal">${fmtPct(dist.median_pct, 2, { signed: true })}</span></div>
        <div class="stat-detail-row"><span class="stat-detail-label">${glossaryTerm('rango_p10_p90', 'p10 / p90')}</span><span class="num">${fmtPct(dist.p10_pct)} / ${fmtPct(dist.p90_pct)}</span></div>
        <p class="hint" title="${validation?.reason || ''}">${validation?.reason || ''}</p>
    `;
}

function riesgoCellHtml(risk) {
    if (!risk?.available) return `<span class="hint">${risk?.reason || 'Sin dato.'}</span>`;
    const verdict = riesgoVerdict(risk);
    return `
        ${tagHtml(verdict.label, verdict.cls)}
        <div class="stat-detail-row"><span class="stat-detail-label">${glossaryTerm('volatilidad_anualizada', 'Volatilidad anualizada')}</span><span class="num">${fmtPct(risk.annualized_volatility_pct)}</span></div>
        <div class="stat-detail-row"><span class="stat-detail-label">${glossaryTerm('caida_maxima', 'Caída máxima')}</span><span class="num cell-negative">${fmtPct(risk.max_drawdown_pct)}</span></div>
    `;
}

function tecnicoCellHtml(backtest) {
    if (backtest?.pending) return '<span class="hint">Historial insuficiente todavía.</span>';
    if (!backtest?.by_signal) return '<span class="hint">Sin dato.</span>';
    const verdict = tecnicoVerdict(backtest);
    const rows = Object.entries(backtest.by_signal).map(([signal, b]) => {
        const edge = b.edge_vs_base_rate_pct;
        const edgeText = edge === null || edge === undefined
            ? `sin ${glossaryTerm('edge_tecnico', 'edge')} (no direccional)`
            : `${fmtPct(edge, 2, { signed: true })} ${glossaryTerm('edge_tecnico', 'edge')}`;
        return `<div class="stat-detail-row"><span class="stat-detail-label">${SIGNAL_LABEL[signal] || signal}</span><span class="num">${edgeText} (n=${b.count})</span></div>`;
    }).join('');
    return `
        ${tagHtml(verdict.label, verdict.cls)}
        <p class="hint">${glossaryTerm('tasa_base', 'Tasa base')}: ${fmtPct(backtest.base_rate_up_pct)} sube — significativo desde ±${EDGE_SIGNIFICANT_PTS}pts</p>
        ${rows}
    `;
}

function fundamentalCellHtml(fund, config) {
    if (!fund || fund.not_applicable) return `<span class="hint">${fund?.reason || 'No aplica.'}</span>`;
    if (!fund.available) return `<span class="hint">${fund.reason}</span>`;
    const verdict = fundamentalVerdict(fund, config);
    const row = (key, label, value, fmtValue) => {
        if (value === null || value === undefined) return '';
        const { cls, note } = config ? evaluateFundamental(key, value, config) : { cls: '', note: '' };
        return `
            <div class="stat-detail-row"><span class="stat-detail-label">${label}</span><span class="num ${cls}">${fmtValue}</span></div>
            ${note ? `<p class="hint" style="margin:-2px 0 4px;">${note}</p>` : ''}
        `;
    };
    return `
        ${verdict ? tagHtml(verdict.label, verdict.cls) : ''}
        ${row('pe_ratio', glossaryTerm('pe_ratio', 'P/E'), fund.pe_ratio, fmt(fund.pe_ratio, 2))}
        ${row('profit_margin', glossaryTerm('margen_neto', 'Margen neto'), fund.profit_margin, fund.profit_margin !== null && fund.profit_margin !== undefined ? fmtPct(fund.profit_margin * 100) : '—')}
        ${row('debt_to_equity', glossaryTerm('deuda_patrimonio', 'Deuda/Patrimonio'), fund.debt_to_equity, fmt(fund.debt_to_equity, 2))}
    `;
}

function confiabilidadCellHtml(acc) {
    if (!acc || acc.pending) return '<span class="hint">Historial insuficiente todavía.</span>';
    return `
        <div class="stat-detail-row"><span class="stat-detail-label">${glossaryTerm('mape', 'MAPE')}</span><span class="num">${fmtPct(acc.mape)}</span></div>
        <div class="stat-detail-row"><span class="stat-detail-label">${glossaryTerm('acierto_direccion', 'Acierto direccional')}</span><span class="num">${acc.directional_hit_pct !== null ? fmtPct(acc.directional_hit_pct) : '—'}</span></div>
        <div class="stat-detail-row"><span class="stat-detail-label">${glossaryTerm('pronosticos_no_superpuestos', 'Pronósticos evaluados')}</span><span class="num">${acc.forecasts_evaluated}</span></div>
    `;
}

// Cache por instrumento -- antes cada "+ Agregar" releía y refeteaba TODAS las
// columnas ya cargadas (se sentía como que "se recargaba" con cada click, el
// usuario lo reportó como un bug). Ahora una columna ya resuelta nunca se
// vuelve a pedir; agregar una nueva solo trae la data de esa, el resto de la
// tabla ni se toca.
const columnCache = new Map();

async function fetchColumnData(instrument) {
    const [forecastRes, riskRes, backtestRes, fundRes, accRes] = await Promise.all([
        api.latestForecast({ instrument_id: instrument.id }).catch(() => ({ data: null })),
        api.instrumentRisk(instrument.id).catch(() => ({ data: null })),
        api.instrumentTechnicalBacktest(instrument.id).catch(() => ({ data: null })),
        api.instrumentFundamentals(instrument.id).catch(() => ({ data: null })),
        api.instrumentAccuracy(instrument.id).catch(() => ({ data: null })),
    ]);
    return {
        instrument, forecast: forecastRes.data, risk: riskRes.data,
        backtest: backtestRes.data, fundamentals: fundRes.data, accuracy: accRes.data,
    };
}

async function getColumnData(instrument) {
    if (columnCache.has(instrument.id)) return columnCache.get(instrument.id);
    const data = await fetchColumnData(instrument);
    columnCache.set(instrument.id, data);
    return data;
}

function addColumnOptionsHtml() {
    const selectedIds = new Set(selected.map((i) => i.id));
    return allInstruments
        .filter((i) => !selectedIds.has(i.id))
        .map((i) => `<option value="${i.id}">${i.symbol} — ${instrumentClase(i)}</option>`)
        .join('');
}

// El control de agregar vive como última columna de la tabla (no en un
// toolbar separado arriba) -- el pedido explícito del usuario fue "una
// columna por ticker, con la opción de ir agregando a la derecha".
function addColumnCellHtml() {
    if (selected.length >= MAX_INSTRUMENTS) return '';
    return `
        <th style="min-width:200px;">
            <div style="display:flex; flex-direction:column; gap:6px;">
                <select id="comparador-select">${addColumnOptionsHtml()}</select>
                <button type="button" class="btn btn-secondary" id="comparador-add-btn">+ Agregar</button>
            </div>
        </th>
    `;
}

function wireAddColumnControl() {
    const btn = $('comparador-add-btn');
    if (!btn) return;
    btn.addEventListener('click', () => {
        const select = $('comparador-select');
        const id = Number(select.value);
        if (!id || selected.length >= MAX_INSTRUMENTS) return;
        const instrument = allInstruments.find((i) => i.id === id);
        if (instrument) selected.push(instrument);
        renderComparador();
    });
}

const ROWS = [
    { label: 'Resumen', render: (c) => resumenCellHtml(c) },
    { label: 'Pronóstico Kronos', render: (c) => pronosticoCellHtml(c.forecast) },
    { label: 'Riesgo', render: (c) => riesgoCellHtml(c.risk) },
    { label: `Técnico (${glossaryTerm('edge_tecnico', 'edge')} vs. ${glossaryTerm('tasa_base', 'tasa base')})`, render: (c) => tecnicoCellHtml(c.backtest) },
    { label: 'Fundamental', render: (c) => fundamentalCellHtml(c.fundamentals, signalConfig) },
    { label: 'Confiabilidad histórica', render: (c) => confiabilidadCellHtml(c.accuracy) },
];

async function renderComparador() {
    const container = $('comparador-table-container');
    if (!selected.length) {
        container.innerHTML = `
            <p class="table-empty" style="margin-bottom:12px;">Elegí instrumentos para comparar — podés ir sumando de a uno.</p>
            <div style="display:flex; gap:8px; max-width:420px; padding:0 20px 16px;">
                <select id="comparador-select" style="flex:1;">${addColumnOptionsHtml()}</select>
                <button type="button" class="btn btn-secondary" id="comparador-add-btn">+ Agregar</button>
            </div>
        `;
        wireAddColumnControl();
        return;
    }

    // Nota: NO se pisa el contenido actual con un spinner de página completa
    // mientras se resuelve -- las columnas ya cargadas (cacheadas) no esperan
    // nada, solo la nueva tarda, y hacerlo así es lo que evitaba la sensación
    // de "recarga" completa en cada click de "+ Agregar".
    const columns = await Promise.all(selected.map(getColumnData));

    container.innerHTML = `
        <div class="table-scroll">
            <table>
                <thead>
                    <tr>
                        <th></th>
                        ${columns.map((c) => `
                            <th>
                                <a href="#detalle/${encodeURIComponent(c.instrument.symbol)}?market=${encodeURIComponent(c.instrument.market)}&assetType=${c.instrument.type}&instrumentId=${c.instrument.id}" class="table-link">${c.instrument.symbol}</a>
                                <button type="button" class="row-action-btn comparador-remove-btn" data-id="${c.instrument.id}" aria-label="Quitar" style="margin-left:6px;">&times;</button>
                            </th>
                        `).join('')}
                        ${addColumnCellHtml()}
                    </tr>
                </thead>
                <tbody>
                    ${ROWS.map((r, i) => `
                        <tr${i === 0 ? ' style="background:var(--accent-dim);"' : ''}>
                            <td class="stat-detail-label" style="vertical-align:top; white-space:nowrap;">${r.label}</td>
                            ${columns.map((c) => `<td style="vertical-align:top; min-width:220px;">${r.render(c)}</td>`).join('')}
                        </tr>
                    `).join('')}
                </tbody>
            </table>
        </div>
    `;

    container.querySelectorAll('.comparador-remove-btn').forEach((btn) => btn.addEventListener('click', () => {
        selected = selected.filter((i) => String(i.id) !== btn.dataset.id);
        renderComparador();
    }));
    wireAddColumnControl();
}

export function initComparadorView() {
    // El control de agregar se re-crea en cada render (vive adentro de la
    // tabla) -- wireAddColumnControl() se llama desde renderComparador() en
    // vez de una sola vez acá.
}

export async function loadComparador() {
    [allInstruments, signalConfig] = await Promise.all([
        fetchInstrumentsList(),
        api.signalConfig().then((r) => r.data).catch(() => null),
    ]);
    await renderComparador();
}
