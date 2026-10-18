// Fase 8: bandeja de propuestas -- el backend (portfolio/proposals.py) evalua
// 4 fuentes en orden de confianza y persiste cada una con un estado. Esta
// vista solo lista/filtra/acepta/descarta -- nunca ejecuta nada sola, "Aceptar"
// únicamente precarga el formulario de transacción (transactions.js).
import { $ } from '../shared/dom.js';
import { showStatus } from '../shared/status.js';
import { todayLocalISO, fmt } from '../shared/format.js';
import { api } from './api.js';
import { openTransactionModal, instrumentBySymbol, detailLinkHtml } from './transactions.js';
import { createSearchBar, applySearchState, tableBodyRowsHtml, wireGroupToggles } from '../shared/search-bar.js';
import { columnToggleHtml, wireColumnToggle, headerCellHtml, updateSortIndicators, wireColumnDrag } from '../shared/table-chrome.js';
import { createSorter, wireSortableHeaders } from '../shared/sortable-table.js';
import { createColumnManager } from '../shared/column-manager.js';
import { glossaryTerm } from '../shared/glossary.js';
import { infoTooltipHtml } from '../shared/info-tooltip.js';

const ORIGEN_LABEL = { sl_tp: 'Stop-loss/Take-profit', evento: 'Evento', rebalanceo: 'Rebalanceo', senal: 'Señal validada' };
// Cada origen tiene su propia entrada en el glosario -- "Origen" por sí solo
// no decía nada ("no sé qué mierda es origen", feedback real del usuario).
const ORIGEN_GLOSSARY_KEY = { sl_tp: 'origen_sl_tp', evento: 'origen_evento', rebalanceo: 'origen_rebalanceo', senal: 'origen_senal' };
const ESTADO_LABEL = { nueva: 'Nueva', vista: 'Vista', aceptada: 'Aceptada', descartada: 'Descartada', expirada: 'Expirada' };
const ESTADO_TAG = { nueva: 'tag-negative', vista: 'tag-neutral', aceptada: 'tag-positive', descartada: 'tag-neutral', expirada: 'tag-neutral' };

const PROPOSALS_COLUMNS = [
    { key: 'symbol', label: 'Activo', numeric: false },
    { key: 'origen', label: 'Origen', numeric: false, sortable: false, term: 'propuesta_origen' },
    { key: 'texto', label: 'Texto', numeric: false, sortable: false },
    { key: 'evidencia', label: 'Evidencia', numeric: false, sortable: false },
    { key: 'estado', label: 'Estado', numeric: false },
];
const columns = createColumnManager('kronos-propuestas-columns', PROPOSALS_COLUMNS);

// IMPORTANTE: los filtros activos se combinan con AND entre sí (ver
// search-bar.js::applySearchState -- `activeDefs.every(...)`), nunca OR, ni
// siquiera dentro del mismo "block" visual (el block solo agrupa la UI, no
// cambia la lógica). Por eso NO se puede expresar "nueva o vista o aceptada"
// con 3 filtros de estado individuales activados a la vez (la intersección
// de 3 estados mutuamente excluyentes siempre da vacío) -- se usa un único
// filtro compuesto en su lugar.
const SEARCH_CONFIG = {
    searchFields: [{ key: 'symbol', label: 'Activo', value: (p) => p.symbol }],
    favoritesKey: 'kronos-propuestas-favorites',
    filters: [
        { key: 'activas', label: 'Activas (ocultar descartadas/expiradas)', block: 'Estado', test: (p) => !['descartada', 'expirada'].includes(p.estado) },
        { key: 'nueva', label: 'Solo nuevas', block: 'Estado', test: (p) => p.estado === 'nueva' },
        { key: 'descartada', label: 'Solo descartadas', block: 'Estado', test: (p) => p.estado === 'descartada' },
        { key: 'expirada', label: 'Solo expiradas', block: 'Estado', test: (p) => p.estado === 'expirada' },
    ],
    groupBy: [
        { key: 'origen', label: 'Origen', value: (p) => ORIGEN_LABEL[p.origen] || p.origen },
    ],
};

let rows = [];
// Default: ocultar descartadas/expiradas -- son historial, no lo primero que
// hay que ver al entrar (mismo criterio que "Vigentes" en Plazos Fijos).
let searchState = { query: '', chips: [], activeFilters: new Set(['activas']), groupKey: null };
const sorter = createSorter();
const collapsedGroups = new Set();
let signalConfig = null;

export async function loadProposals() {
    const [{ data }, { data: config }] = await Promise.all([api.proposals(), api.signalConfig()]);
    rows = data;
    signalConfig = config;
    renderTable();
    renderSummary();
}

// Fase 16: panel "Resumen" al costado, mismo patrón ya usado en el resto de
// la plataforma. Fase 18: cada estado explica qué lo genera/qué significa,
// con el parámetro real configurado donde aplica -- "no tienen explicación
// ni el parámetro de cada uno", feedback real del usuario.
function renderSummary() {
    const el = $('proposals-summary');
    if (!el) return;
    const count = (estado) => rows.filter((p) => p.estado === estado).length;
    const rebalanceThreshold = signalConfig?.proposals_rebalance_threshold_pct;
    const minHit = signalConfig?.min_directional_hit_pct;
    const minN = signalConfig?.min_non_overlapping_forecasts;
    const nuevaTooltip = infoTooltipHtml(`
        <div>Propuesta recién generada, todavía no la revisaste. Se arma a partir de 4 fuentes, en este orden de confianza:</div>
        <div class="info-tooltip-formula">1. Alerta de precio (SL/TP) tocada\n2. Evento determinístico (vencimiento de bono/caución)\n3. Rebalanceo: desvío >= ${fmt(rebalanceThreshold, 0)}pp vs. objetivo configurado\n4. Señal validada (Kronos: >= ${fmt(minN, 0)} pronósticos no superpuestos, acierto >= ${fmt(minHit, 0)}%)</div>
        <div class="hint" style="margin-top:6px;">Nunca se ejecuta nada solo — "Aceptar" sólo precarga el formulario de transacción.</div>
    `);
    const aceptadaTooltip = infoTooltipHtml('<div>Aceptaste la sugerencia y se precargó una transacción con esos datos — no implica que ya la hayas cargado/confirmado.</div>');
    const descartadaTooltip = infoTooltipHtml('<div>La descartaste a mano. La misma propuesta (mismo activo + mismo origen) no vuelve a aparecer hasta que termine su cooldown.</div>');
    const expiradaTooltip = infoTooltipHtml('<div>El motor volvió a correr y la condición que la generó ya no se cumple (ej. el desvío de rebalanceo volvió a estar dentro del umbral) — se cierra sola, no hace falta revisarla.</div>');
    el.innerHTML = `
        <div class="mini-stat"><div class="l">Nuevas${nuevaTooltip}</div><div class="v num cell-negative">${count('nueva')}</div></div>
        <div class="mini-stat"><div class="l">Aceptadas${aceptadaTooltip}</div><div class="v num cell-positive">${count('aceptada')}</div></div>
        <div class="mini-stat"><div class="l">Descartadas${descartadaTooltip}</div><div class="v num">${count('descartada')}</div></div>
        <div class="mini-stat"><div class="l">Expiradas${expiradaTooltip}</div><div class="v num">${count('expirada')}</div></div>
    `;
}

function isActionable(p) {
    return p.estado === 'nueva' && p.tipo_sugerido && instrumentBySymbol.has(p.symbol);
}

async function accept(id) {
    const p = rows.find((r) => r.id === id);
    if (!p) return;
    const instrument = instrumentBySymbol.get(p.symbol);
    await api.updateProposal(id, 'aceptada');
    await loadProposals();
    if (instrument) {
        // Precarga fecha (hoy) y precio (ultimo conocido, de /holdings si ya
        // esta en cartera) para no dejar el formulario vacio -- el usuario
        // igual revisa y confirma antes de enviar, esto es solo un punto de
        // partida razonable, no un dato que se envia sin que lo vea.
        let precio;
        try {
            const { data: holdings } = await api.holdings();
            precio = holdings.find((h) => h.instrument.id === instrument.id)?.precio_actual;
        } catch {
            precio = undefined;
        }
        openTransactionModal({
            instrument_id: instrument.id,
            tipo: p.tipo_sugerido === 'compra' ? 'compra' : 'venta',
            fecha: todayLocalISO(),
            precio,
        });
    }
    showStatus('status-banner', 'success', 'Propuesta aceptada — revisá y confirmá la transacción.');
}

async function discard(id) {
    await api.updateProposal(id, 'descartada');
    await loadProposals();
}

function rowHtml(p, visibleColumns) {
    const cells = visibleColumns.map((c) => {
        switch (c.key) {
            case 'symbol': {
                // Rebalanceo no apunta a un ticker real (p.symbol es una
                // etiqueta de clase, ej. "Bonos") -- no hay detalle de
                // instrumento al que linkear ahí.
                const activo = p.origen === 'rebalanceo' ? p.symbol : detailLinkHtml(instrumentBySymbol.get(p.symbol), p.symbol);
                // tag-signal-buy/sell (acento), nunca tag-positive/tag-negative
                // -- es una señal sugerida, no P&L real (ver DESIGN_SYSTEM.md).
                return `<td><span class="row-inline-tag">${activo}${p.tipo_sugerido ? ` <span class="tag ${p.tipo_sugerido === 'compra' ? 'tag-signal-buy' : 'tag-signal-sell'}">${p.tipo_sugerido === 'compra' ? 'Comprar' : 'Vender'}</span>` : ''}</span></td>`;
            }
            case 'origen': return `<td>${glossaryTerm(ORIGEN_GLOSSARY_KEY[p.origen], ORIGEN_LABEL[p.origen] || p.origen)}</td>`;
            case 'texto': return `<td>${p.texto}</td>`;
            case 'evidencia': return `<td class="hint">${p.evidencia}</td>`;
            // "Nueva" se transmite con el tinte de la fila entera (ver <tr> abajo),
            // no con un tag que compite por ancho con los botones -- antes
            // Aceptar+Descartar se apilaban en dos líneas aunque sobrara espacio
            // horizontal, solo porque el tag de "Nueva" ocupaba lugar al lado.
            // El resto de los estados (vista/aceptada/descartada/expirada) no
            // tienen ese problema (a lo sumo un solo botón junto al tag).
            case 'estado': return p.estado === 'nueva' ? `<td>
                <div style="display:flex; gap:8px; flex-wrap:nowrap; white-space:nowrap;">
                    ${isActionable(p) ? `<button type="button" class="btn btn-primary proposal-accept-btn" data-id="${p.id}">Aceptar</button>` : ''}
                    <button type="button" class="btn btn-secondary proposal-discard-btn" data-id="${p.id}">Descartar</button>
                </div>
            </td>` : `<td>
                <div style="display:flex; align-items:center; gap:8px; flex-wrap:nowrap; white-space:nowrap;">
                    <span class="tag ${ESTADO_TAG[p.estado]}">${ESTADO_LABEL[p.estado] || p.estado}</span>
                    ${p.estado === 'vista' ? `<button type="button" class="btn btn-secondary proposal-discard-btn" data-id="${p.id}">Descartar</button>` : ''}
                </div>
            </td>`;
            default: return '<td></td>';
        }
    });
    return `<tr${p.estado === 'nueva' ? ' class="proposal-row-new"' : ''}>${cells.join('')}</tr>`;
}

function renderTable() {
    const container = $('proposals-table-container');
    if (!container) return;
    const visibleColumns = columns.visibleColumns();
    const sorted = sorter.key ? sorter.apply(rows, (p, key) => p[key]) : rows;
    const result = applySearchState(sorted, searchState, SEARCH_CONFIG);
    const bodyRows = tableBodyRowsHtml(result, visibleColumns, (p) => rowHtml(p, visibleColumns), { collapsedGroups });

    container.innerHTML = `
        <div class="table-toolbar">${columnToggleHtml(columns)}</div>
        <div class="table-scroll">
            <table>
                <thead><tr>${visibleColumns.map((c) => headerCellHtml(c)).join('')}</tr></thead>
                <tbody>${bodyRows}</tbody>
            </table>
        </div>
    `;

    const rerender = () => renderTable();
    wireColumnToggle(container, columns, rerender);
    wireColumnDrag(container.querySelector('thead tr'), columns, rerender);
    wireSortableHeaders(container, sorter, rerender);
    wireGroupToggles(container, collapsedGroups, rerender);
    updateSortIndicators(container, sorter);

    container.querySelectorAll('.proposal-accept-btn').forEach((btn) => {
        btn.addEventListener('click', () => accept(Number(btn.dataset.id)));
    });
    container.querySelectorAll('.proposal-discard-btn').forEach((btn) => {
        btn.addEventListener('click', () => discard(Number(btn.dataset.id)));
    });
}

export function initProposalsView() {
    createSearchBar($('proposals-search-bar'), {
        ...SEARCH_CONFIG,
        searchPlaceholder: 'Buscar por activo...',
        initialState: searchState,
        onChange: (state) => { searchState = state; renderTable(); },
    });
}
