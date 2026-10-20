// Fase 8: bandeja de propuestas -- el backend (portfolio/proposals.py) evalua
// 4 fuentes en orden de confianza y persiste cada una con un estado. Esta
// vista solo lista/filtra/acepta/descarta -- nunca ejecuta nada sola, "Aceptar"
// únicamente precarga el formulario de transacción (transactions.js).
import { $ } from '../shared/dom.js';
import { showStatus } from '../shared/status.js';
import { todayLocalISO, fmt } from '../shared/format.js';
import { api } from './api.js';
import { openTransactionModal, instrumentBySymbol, detailLinkHtml } from './transactions.js';
import { createSearchBar, applySearchState, wireGroupToggles } from '../shared/search-bar.js';
import { glossaryTerm } from '../shared/glossary.js';
import { infoTooltipHtml } from '../shared/info-tooltip.js';
import { getEventRiskNotes } from './core-satellite.js';

// senal_kronos/senal_tecnica (antes un único 'senal' -- ver auditoria
// 19/10/2026, hallazgo 1.3: compartir origen hacía que sync_proposals las
// tratara como la MISMA propuesta y se pisaran entre sí) -- 'senal' queda
// como fallback para filas ya expiradas de antes de este cambio, nunca se
// va a generar de nuevo.
const ORIGEN_LABEL = {
    sl_tp: 'Stop-loss/Take-profit', evento: 'Evento', rebalanceo: 'Rebalanceo',
    senal_kronos: 'Señal Kronos', senal_tecnica: 'Señal técnica (RSI)', senal: 'Señal validada',
};
// Cada origen tiene su propia entrada en el glosario -- "Origen" por sí solo
// no decía nada ("no sé qué mierda es origen", feedback real del usuario).
const ORIGEN_GLOSSARY_KEY = {
    sl_tp: 'origen_sl_tp', evento: 'origen_evento', rebalanceo: 'origen_rebalanceo',
    senal_kronos: 'origen_senal_kronos', senal_tecnica: 'origen_senal_tecnica', senal: 'origen_senal',
};
const ESTADO_LABEL = { nueva: 'Nueva', vista: 'Vista', aceptada: 'Aceptada', descartada: 'Descartada', expirada: 'Expirada' };
const ESTADO_TAG = { nueva: 'tag-negative', vista: 'tag-neutral', aceptada: 'tag-positive', descartada: 'tag-neutral', expirada: 'tag-neutral' };

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
let searchState = { query: '', activeFilters: new Set(['activas']), groupKey: null };
const collapsedGroups = new Set();
let signalConfig = null;

// El sync corre completo en cada GET (ver portfolio/proposals.py::sync_proposals
// -- no hay nada que cachear a nivel lista, solo las sub-consultas pesadas
// por tenencia), asi que "ultima actualizacion" es simplemente el momento en
// que ESTE fetch resolvio, no un timestamp que vuelva del backend.
function renderLastUpdated() {
    const el = $('proposals-last-updated');
    if (el) el.textContent = `Última actualización: ${new Date().toLocaleString('es-AR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })}`;
}

export async function loadProposals() {
    const [{ data }, { data: config }] = await Promise.all([api.proposals(), api.signalConfig()]);
    rows = data;
    signalConfig = config;
    renderTable();
    renderSummary();
    renderLastUpdated();
}

// Para que core-satellite.js (misma subtab "Decisiones", riesgo inverso de
// getEventRiskNotes importado arriba) marque una tarjeta de Oportunidades
// cuando ya existe una Propuesta activa para ese símbolo, sin pedirle de
// nuevo al servidor algo que esta vista ya tiene en memoria. Mapa vacío
// hasta que loadProposals() resuelva la primera vez en la sesión -- mismo
// límite ya aceptado por getEventRiskNotes en la dirección inversa. Nunca
// dice que la señal "generó" la propuesta -- son 2 cálculos independientes
// que solo pueden coincidir por símbolo (ver conversación/informe de diseño).
export function getActiveProposalsBySymbol() {
    const map = {};
    for (const p of rows) {
        if (p.estado === 'nueva' || p.estado === 'vista' || p.estado === 'aceptada') {
            map[p.symbol] = { estado: p.estado, tipo_sugerido: p.tipo_sugerido };
        }
    }
    return map;
}

// Fase 16: panel "Resumen" al costado, mismo patrón ya usado en el resto de
// la plataforma. Fase 18: cada estado explica qué lo genera/qué significa,
// con el parámetro real configurado donde aplica -- "no tienen explicación
// ni el parámetro de cada uno", feedback real del usuario.
function renderSummary() {
    const el = $('proposals-summary');
    if (!el) return;
    // Cuenta sobre lo que el usuario está VIENDO ahora mismo (búsqueda +
    // filtros aplicados), no sobre el total general -- antes mostraba
    // siempre el total general sin importar qué tan filtrada estuviera la
    // tabla de abajo (ver conversación/auditoría, hallazgo 2.5: "los KPIs
    // no se actualizan al buscar o filtrar").
    const result = applySearchState(rows, searchState, SEARCH_CONFIG);
    const visibleRows = result.grouped ? result.groups.flatMap((g) => g.items) : result.items;
    const count = (estado) => visibleRows.filter((p) => p.estado === estado).length;
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

// "Nueva" se transmite con el tinte de toda la tarjeta (ver .proposal-card-new
// en proposal-cards.css), no con un tag que compite por espacio con los
// botones -- antes Aceptar+Descartar se apilaban en dos líneas aunque sobrara
// espacio horizontal, solo porque el tag de "Nueva" ocupaba lugar al lado. El
// resto de los estados (vista/aceptada/descartada/expirada) no tiene ese
// problema (a lo sumo un solo botón junto al tag).
function actionsHtml(p) {
    if (p.estado === 'nueva') {
        return `
            ${isActionable(p) ? `<button type="button" class="btn btn-primary proposal-accept-btn" data-id="${p.id}">Aceptar</button>` : ''}
            <button type="button" class="btn btn-secondary proposal-discard-btn" data-id="${p.id}">Descartar</button>
        `;
    }
    return `
        <span class="tag ${ESTADO_TAG[p.estado]}">${ESTADO_LABEL[p.estado] || p.estado}</span>
        ${p.estado === 'vista' ? `<button type="button" class="btn btn-secondary proposal-discard-btn" data-id="${p.id}">Descartar</button>` : ''}
    `;
}

function proposalCardHtml(p) {
    // Rebalanceo no apunta a un ticker real (p.symbol es una etiqueta de
    // clase, ej. "Bonos") -- no hay detalle de instrumento al que linkear ahí,
    // y tampoco tiene sentido buscarlo en event_risk_notes (ese dict está
    // keyeado por símbolo real, ver portfolio/live_signals.py).
    const activo = p.origen === 'rebalanceo' ? p.symbol : detailLinkHtml(instrumentBySymbol.get(p.symbol), p.symbol);
    // tag-signal-buy/sell (acento), nunca tag-positive/tag-negative -- es una
    // señal sugerida, no P&L real (ver DESIGN_SYSTEM.md).
    const tipoTag = p.tipo_sugerido
        ? ` <span class="tag ${p.tipo_sugerido === 'compra' ? 'tag-signal-buy' : 'tag-signal-sell'}">${p.tipo_sugerido === 'compra' ? 'Comprar' : 'Vender'}</span>`
        : '';
    // Mismas notas de riesgo de evento que Oportunidades del Día (ver
    // core-satellite.js::getEventRiskNotes) -- reusa lo que esa vista ya pidió
    // en vez de otro fetch acá, igual que `eventRiskNotes` y el signalConfig
    // de más arriba.
    const eventNote = p.origen !== 'rebalanceo' ? getEventRiskNotes()[p.symbol] : null;
    return `
        <div class="proposal-card${p.estado === 'nueva' ? ' proposal-card-new' : ''}" data-id="${p.id}">
            <div class="proposal-card-head">
                <div class="proposal-card-head-left">
                    <span class="row-inline-tag">${activo}${tipoTag}</span>
                    <span class="proposal-card-origen">${glossaryTerm(ORIGEN_GLOSSARY_KEY[p.origen], ORIGEN_LABEL[p.origen] || p.origen)}</span>
                    ${eventNote ? `<span class="cell-warning">${eventNote}</span>` : ''}
                </div>
                <div class="proposal-card-actions">${actionsHtml(p)}</div>
            </div>
            <p class="proposal-card-text">${p.texto}</p>
            <p class="proposal-card-evidence hint">${p.evidencia}</p>
        </div>
    `;
}

function proposalCardsHtml(result) {
    if (result.grouped) {
        if (!result.groups.length) return '<p class="table-empty">Sin resultados para este filtro.</p>';
        return result.groups.map((g) => {
            const collapsed = collapsedGroups.has(g.label);
            const header = `
                <div class="proposal-group-header" data-group-row="${g.label}">
                    <button type="button" class="group-toggle" data-group="${g.label}">${collapsed ? '▸' : '▾'}</button>
                    ${g.label} <span class="table-group-count">(${g.items.length})</span>
                </div>
            `;
            return header + (collapsed ? '' : g.items.map(proposalCardHtml).join(''));
        }).join('');
    }
    return result.items.length ? result.items.map(proposalCardHtml).join('') : '<p class="table-empty">Sin resultados para este filtro.</p>';
}

function renderTable() {
    const container = $('proposals-table-container');
    if (!container) return;
    const result = applySearchState(rows, searchState, SEARCH_CONFIG);
    container.innerHTML = `<div class="proposal-cards">${proposalCardsHtml(result)}</div>`;

    const rerender = () => renderTable();
    wireGroupToggles(container, collapsedGroups, rerender);

    container.querySelectorAll('.proposal-accept-btn').forEach((btn) => {
        btn.addEventListener('click', () => accept(Number(btn.dataset.id)));
    });
    container.querySelectorAll('.proposal-discard-btn').forEach((btn) => {
        btn.addEventListener('click', () => discard(Number(btn.dataset.id)));
    });
}

// Los 4 filtros de "Estado" son mutuamente excluyentes por naturaleza (son
// 4 formas de cortar la MISMA dimensión `estado`, "activas" es un compuesto
// de nueva+vista) -- search-bar.js los renderiza como checkboxes
// independientes, así que nada impedía tildar "Activas" + "Solo expiradas"
// juntos, lo que da 0 resultados siempre (ver conversación/auditoría,
// hallazgo 2.5). Se fuerza la exclusividad acá en vez de en search-bar.js
// (cambiarlo ahí afectaría a todas las demás pestañas que lo usan).
const ESTADO_FILTER_KEYS = ['activas', 'nueva', 'descartada', 'expirada'];
let proposalsSearchBar = null;

export function initProposalsView() {
    proposalsSearchBar = createSearchBar($('proposals-search-bar'), {
        ...SEARCH_CONFIG,
        searchPlaceholder: 'Buscar por activo...',
        initialState: searchState,
        onChange: (state) => {
            const activeEstado = [...state.activeFilters].filter((k) => ESTADO_FILTER_KEYS.includes(k));
            if (activeEstado.length > 1) {
                const previouslyActive = [...searchState.activeFilters].filter((k) => ESTADO_FILTER_KEYS.includes(k));
                const justClicked = activeEstado.find((k) => !previouslyActive.includes(k)) || activeEstado[activeEstado.length - 1];
                const nextFilters = [...state.activeFilters].filter((k) => !ESTADO_FILTER_KEYS.includes(k) || k === justClicked);
                proposalsSearchBar.setActiveFilters(nextFilters); // dispara este mismo onChange de nuevo, ya corregido
                return;
            }
            searchState = state;
            renderTable();
            renderSummary();
        },
    });

    const refreshBtn = $('refresh-proposals-btn');
    if (refreshBtn) {
        refreshBtn.addEventListener('click', async () => {
            refreshBtn.disabled = true;
            refreshBtn.textContent = 'Actualizando…';
            try {
                await loadProposals();
            } finally {
                refreshBtn.disabled = false;
                refreshBtn.textContent = 'Actualizar';
            }
        });
    }
}
