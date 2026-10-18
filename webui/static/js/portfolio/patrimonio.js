// Panel "Patrimonio" (Fase 11, rediseñado Fase 15 para calcar el mockup
// aprobado punto por punto): acá la pregunta es "cuánto tengo, dónde está y
// cuánto debo", no "qué instrumento conviene" (eso es Dashboard). Reusa el
// mismo /dashboard-bundle que ya consume holdings.js/resumen-v2.js.
import { $ } from '../shared/dom.js';
import { fmtMoney, fmtPct, fmtDate, todayLocalISO } from '../shared/format.js';
import { glossaryTerm } from '../shared/glossary.js';
import { buildAllocationSegments } from './resumen-v2.js';
import { api } from './api.js';
import { openModal } from '../shared/modal.js';
import { showStatus } from '../shared/status.js';
import { renderCashFlowChart } from '../charts/cash-flow-chart.js';
import { TYPE_COLOR, renderAllocationDonut } from '../charts/allocation-donut.js';
import { renderPortfolioRiskGauges, LABEL_COLOR as RISK_LABEL_COLOR } from '../charts/risk-gauge.js';
import { currentPalette } from '../charts/apex-theme.js';
import {
    benchmarkPeriodControlsHtml, wireBenchmarkPeriodControls,
    renderBenchmarkComparisonChart, benchmarkStatStripHtml, periodLabel,
} from '../charts/benchmark-comparison-chart.js';

const CUENTA_DEFAULT = 'NH Capital';
const PERIOD_NOUN = { '1m': 'el último mes', '3m': 'los últimos 3 meses', ytd: 'lo que va del año', '1a': 'el último año', inicio: 'el inicio' };

// Estado de UI persistido entre repintados (no entre recargas de página,
// igual que el mockup: toggle de moneda, período y tile activo sobreviven a
// un repintado liviano -- ej. cambiar de período no debe resetear la pestaña
// en la que estás parado).
let patrimonioCcy = 'USD';
let patrimonioCcyInitialized = false;
let patrimonioPeriod = 'inicio';
let patrimonioActiveTile = 'patrimonio';
let extrasCache = null;

// Preferencias -> Moneda dispara este evento (user-menu.js) y ya hace que
// holdings.js refetchee el bundle completo y vuelva a llamar a
// renderPatrimonio -- pero el guard de "solo inicializar una vez" de abajo
// (pensado para no resetear un click manual del toggle del hero en un
// repintado cualquiera) bloqueaba justo ESE caso: cambiar la preferencia
// mientras ya estás parado en Patrimonio no hacía nada visible hasta
// recargar la página. Acá se fuerza el valor nuevo sin esperar el guard.
document.addEventListener('currency-changed', (e) => {
    if (!e.detail) return;
    patrimonioCcy = e.detail;
    patrimonioCcyInitialized = true;
});

function nativeToDisplay(amount, currency, displayCurrency, usdRate) {
    if (amount === null || amount === undefined) return null;
    if (currency === displayCurrency) return amount;
    if (!usdRate) return null;
    return currency === 'USD' ? amount * usdRate : amount / usdRate;
}

function fmtCcy(arsValue, usdRate) {
    if (arsValue === null || arsValue === undefined) return '—';
    if (patrimonioCcy === 'ARS') return fmtMoney(arsValue, 'ARS');
    if (!usdRate) return '—';
    return fmtMoney(arsValue / usdRate, 'USD');
}

function buildCuentaSegments(holdings, cauciones, loans, usdRate) {
    const byCuenta = new Map();
    holdings.forEach((h) => {
        if (!h.valor_actual) return;
        const cuenta = h.instrument.cuenta || CUENTA_DEFAULT;
        byCuenta.set(cuenta, (byCuenta.get(cuenta) || 0) + h.valor_actual);
    });
    // Cauciones y préstamos 'prestado' no tienen dimensión de cuenta todavía
    // (son conceptos propios de NH Capital, Fase 11 no los extiende a
    // multi-cuenta) -- se suman a NH Capital explícitamente, no se pierden.
    const caucionesActivas = cauciones
        .filter((c) => !c.vencido)
        .reduce((sum, c) => sum + (nativeToDisplay(c.valor_actual, c.caucion.moneda, 'ARS', usdRate) || 0), 0);
    const prestamosActivos = loans
        .filter((l) => l.estado === 'vigente' && l.direccion === 'prestado')
        .reduce((sum, l) => sum + (nativeToDisplay(l.capital_pendiente, l.moneda, 'ARS', usdRate) || 0), 0);
    const extra = caucionesActivas + prestamosActivos;
    if (extra > 0) byCuenta.set(CUENTA_DEFAULT, (byCuenta.get(CUENTA_DEFAULT) || 0) + extra);

    return [...byCuenta.entries()]
        .filter(([, value]) => value > 0)
        .sort((a, b) => b[1] - a[1])
        .map(([cuenta, value]) => ({ cuenta, value }));
}

// Fase 17: árbol de descomposición estilo PowerBI, al lado del donut (33/66,
// como se había pedido antes) -- clase -> instrumento. Un solo nodo puede
// estar abierto a la vez (abrir otro cierra el anterior), la altura queda
// acotada con scroll, y abrir/cerrar anima con el truco de grid-template-rows
// (toggle real, sin re-renderizar toda la tabla cada vez que se abre algo).
const DRILL_PALETTE = Object.values(TYPE_COLOR);
let currentClaseCtx = null;
let currentTargetAllocation = {};
let decompOpenKey = null;

function claseDrillItems(type, holdings, cauciones, loans, usdRate) {
    if (type === 'caucion') {
        return cauciones.filter((c) => !c.vencido).map((c) => ({
            label: `Caución ${c.caucion.numero_boleto}`,
            value: nativeToDisplay(c.valor_actual, c.caucion.moneda, 'ARS', usdRate) || 0,
        }));
    }
    if (type === 'prestamo') {
        return loans.filter((l) => l.estado === 'vigente' && l.direccion === 'prestado').map((l) => ({
            label: l.deudor,
            value: nativeToDisplay(l.capital_pendiente, l.moneda, 'ARS', usdRate) || 0,
        }));
    }
    return holdings
        .filter((h) => h.instrument.type === type && h.valor_actual)
        .map((h) => ({
            label: h.instrument.cuenta && h.instrument.cuenta !== CUENTA_DEFAULT
                ? `${h.instrument.symbol} (${h.instrument.cuenta})` : h.instrument.symbol,
            value: h.valor_actual,
        }));
}

function buildDecompTree(ctx) {
    const { claseSegments, claseTotal, holdings, cauciones, loans, usdRate } = ctx;
    const children = [...claseSegments].sort((a, b) => b.value - a.value).map((s) => {
        const drillItems = claseDrillItems(s.type, holdings, cauciones, loans, usdRate);
        const expandable = drillItems.length > 1;
        const actualPct = claseTotal ? (s.value / claseTotal) * 100 : 0;
        const target = currentTargetAllocation?.[s.type];
        const hasTarget = target !== undefined && target !== null;
        return {
            key: s.type,
            label: s.label,
            value: s.value,
            pct: actualPct,
            target: hasTarget ? target : null,
            desvio: hasTarget ? actualPct - target : null,
            color: TYPE_COLOR[s.type] || '#8a93a3',
            expandable,
            children: expandable
                ? [...drillItems].sort((a, b) => b.value - a.value).map((it, i) => ({
                    key: `${s.type}:${it.label}`,
                    label: it.label,
                    value: it.value,
                    pct: s.value ? (it.value / s.value) * 100 : 0,
                    color: DRILL_PALETTE[i % DRILL_PALETTE.length],
                    expandable: false,
                    children: [],
                }))
                : [],
        };
    });
    return { key: 'root', label: 'Activos', value: claseTotal, pct: 100, children };
}

// Nodos NO root: siempre se renderizan (abiertos o no), el toggle solo le
// agrega/saca la clase "open" vía JS -- así abrir/cerrar anima con CSS en vez
// de reconstruir el HTML (y de paso no hace falta re-pintar nada al togglear).
function decompNodeHtml(node, usdRate) {
    const isOpen = decompOpenKey === node.key;
    const hasChildren = node.children.length > 0;
    // Clickeable en cualquier parte de la tarjeta (no solo en el +) -- el
    // +/− queda como indicador visual nomás, no como el único blanco de click.
    const toggleAttr = node.expandable ? ` data-decomp-toggle="${node.key}" role="button" tabindex="0"` : '';
    // Fase 17: "esto creo que podemos unificarlo en la cascada" -- el
    // objetivo/desvío (antes una tabla aparte) se muestra acá, solo en los
    // nodos de clase que tienen un objetivo configurado (currentTargetAllocation),
    // para no ensuciar con "sin objetivo" las clases que no lo tienen.
    const desvioHtml = node.target !== undefined && node.target !== null
        ? `<div class="decomp-node-target">${glossaryTerm('objetivo_asignacion', 'Objetivo')} ${fmtPct(node.target)} · <span class="${node.desvio > 0 ? 'cell-negative' : 'cell-positive'}">${fmtPct(node.desvio, 2, { signed: true })}</span></div>`
        : '';
    return `
        <div class="decomp-branch">
            <div class="decomp-node${node.expandable ? ' decomp-node-expandable' : ''}" style="border-left-color:${node.color}"${toggleAttr}>
                <div class="decomp-node-label">${node.label}</div>
                <div class="decomp-node-right">
                    <div class="decomp-node-value num">${fmtCcy(node.value, usdRate)} · ${fmtPct(node.pct)}</div>
                    ${desvioHtml}
                </div>
                ${node.expandable ? `<span class="decomp-toggle" aria-hidden="true">${isOpen ? '−' : '+'}</span>` : ''}
            </div>
            ${hasChildren ? `
                <div class="decomp-children-wrap${isOpen ? ' open' : ''}" data-decomp-wrap="${node.key}">
                    <div class="decomp-children">${node.children.map((c) => decompNodeHtml(c, usdRate)).join('')}</div>
                </div>
            ` : ''}
        </div>
    `;
}

function decompTreeHtml(ctx) {
    if (!ctx || !ctx.claseTotal) return '<p class="table-empty">Sin tenencias todavía.</p>';
    const tree = buildDecompTree(ctx);
    return `
        <div class="decomp-tree">
            <div class="decomp-branch">
                <div class="decomp-node decomp-node-root">
                    <div class="decomp-node-label">${tree.label}</div>
                    <div class="decomp-node-right"><div class="decomp-node-value num">${fmtCcy(tree.value, ctx.usdRate)}</div></div>
                </div>
                <div class="decomp-children">${tree.children.map((c) => decompNodeHtml(c, ctx.usdRate)).join('')}</div>
            </div>
        </div>
    `;
}

function repaintClaseArea() {
    const area = $('patrimonio-clase-area');
    if (!area) return;
    area.innerHTML = decompTreeHtml(currentClaseCtx);
}

// Click en un "+"/"−": SOLO manipula clases (sin re-render), para que la
// animación de abrir/cerrar se vea -- reconstruir el HTML de nuevo perdería
// el estado inicial que la transición de CSS necesita para animar.
function toggleDecompNode(key) {
    const wrap = document.querySelector(`[data-decomp-wrap="${CSS.escape(key)}"]`);
    if (!wrap) return;
    const opening = decompOpenKey !== key;
    const setIndicator = (nodeKey, isOpen) => {
        const node = document.querySelector(`[data-decomp-toggle="${CSS.escape(nodeKey)}"]`);
        if (!node) return;
        node.setAttribute('aria-label', isOpen ? 'Colapsar' : 'Expandir');
        const indicator = node.querySelector('.decomp-toggle');
        if (indicator) indicator.textContent = isOpen ? '−' : '+';
    };
    if (decompOpenKey && decompOpenKey !== key) {
        const prevWrap = document.querySelector(`[data-decomp-wrap="${CSS.escape(decompOpenKey)}"]`);
        if (prevWrap) prevWrap.classList.remove('open');
        setIndicator(decompOpenKey, false);
    }
    wrap.classList.toggle('open', opening);
    decompOpenKey = opening ? key : null;
    setIndicator(key, opening);
}

function renderClaseDonutChart() {
    if (patrimonioActiveTile !== 'activos' || !currentClaseCtx) return;
    const el = $('patrimonio-clase-donut');
    if (!el) return;
    const { claseSegments, usdRate } = currentClaseCtx;
    const donutSegments = claseSegments.map((s) => ({ ...s, value: nativeToDisplay(s.value, 'ARS', patrimonioCcy, usdRate) || 0 }));
    renderAllocationDonut(el, donutSegments, null, patrimonioCcy);
}

const RISK_DIST_LABEL = { bajo: 'Riesgo bajo', medio: 'Riesgo medio', alto: 'Riesgo alto', sin_dato: 'Sin dato' };

// "Por clase de activo" ya tiene su propio donut -- "agregá otra torta de
// distribución por riesgo" pide una segunda lente sobre la MISMA cartera,
// agrupada por bajo/medio/alto en vez de por clase. Cauciones/préstamos no
// tienen volatilidad calculable (sin serie diaria) -- van a "Sin dato" en vez
// de inventarles una etiqueta, mismo criterio honesto de siempre.
function buildRiskDistribution(ctx) {
    const { holdings, cauciones, loans, usdRate } = ctx;
    const buckets = { bajo: 0, medio: 0, alto: 0, sin_dato: 0 };
    holdings.forEach((h) => {
        if (!h.valor_actual) return;
        const label = h.risk?.label;
        buckets[label === 'bajo' || label === 'medio' || label === 'alto' ? label : 'sin_dato'] += h.valor_actual;
    });
    cauciones.filter((c) => !c.vencido).forEach((c) => {
        buckets.sin_dato += nativeToDisplay(c.valor_actual, c.caucion.moneda, 'ARS', usdRate) || 0;
    });
    loans.filter((l) => l.estado === 'vigente' && l.direccion === 'prestado').forEach((l) => {
        buckets.sin_dato += nativeToDisplay(l.capital_pendiente, l.moneda, 'ARS', usdRate) || 0;
    });
    return Object.entries(buckets)
        .filter(([, value]) => value > 0)
        .map(([type, value]) => ({ type, label: RISK_DIST_LABEL[type], value, color: RISK_LABEL_COLOR[type] || '#8a93a3' }));
}

function renderRiskDonutChart() {
    if (patrimonioActiveTile !== 'activos' || !currentClaseCtx) return;
    const el = $('patrimonio-risk-donut');
    if (!el) return;
    const { usdRate } = currentClaseCtx;
    const segments = buildRiskDistribution(currentClaseCtx).map((s) => ({ ...s, value: nativeToDisplay(s.value, 'ARS', patrimonioCcy, usdRate) || 0 }));
    renderAllocationDonut(el, segments, null, patrimonioCcy);
}

// --- Selector de tiles (Fase 15.1): Patrimonio/Activos/Pasivos/Caja/Riesgo/
// Vencimientos, un solo panel de detalle visible por vez -- mismo patrón del
// mockup aprobado, ahora con datos reales (todos ya venían en el bundle).

function loansListHtml(loans, direccion, emptyMsg, usdRate) {
    const rows = loans.filter((l) => l.estado === 'vigente' && l.direccion === direccion);
    if (!rows.length) return `<p class="hint">${emptyMsg}</p>`;
    return `<div class="rebalance-list">${rows.map((l) => {
        const converted = nativeToDisplay(l.capital_pendiente, l.moneda, patrimonioCcy, usdRate);
        return `
        <div class="rebalance-row">
            <span>${l.deudor}${l.vencido_hace_dias ? ` <span class="tag tag-negative">Atrasado ${l.vencido_hace_dias}d</span>` : ''}</span>
            <span class="num ${direccion === 'prestado' ? 'cell-positive' : 'cell-negative'}">${converted === null ? '<span class="hint">sin cotización MEP</span>' : fmtMoney(converted, patrimonioCcy)}</span>
        </div>
    `;
    }).join('')}</div>`;
}

function equationBarHtml(activosTotal, pasivosTotal, usdRate) {
    const neto = activosTotal - pasivosTotal;
    return `
        <div class="equation">
            <div class="term"><span class="t-label">Activos</span><span class="t-value num">${fmtCcy(activosTotal, usdRate)}</span></div>
            <span class="op">−</span>
            <div class="term"><span class="t-label">Pasivos</span><span class="t-value num cell-negative">${fmtCcy(pasivosTotal, usdRate)}</span></div>
            <span class="op">=</span>
            <div class="term"><span class="t-label">Patrimonio neto</span><span class="t-value num ${neto >= 0 ? 'cell-positive' : 'cell-negative'}">${fmtCcy(neto, usdRate)}</span></div>
        </div>
    `;
}

// --- Exposición por moneda + Asignación objetivo (Fase 16) ------------------
// Absorbidas del Dashboard viejo (eliminado, duplicaba el resto de Patrimonio
// casi entero) -- estas dos eran lo único genuinamente único que tenía.

// Fase 17: nada de gráfico acá -- un donut para 3 datos quedaba feo ("sacá a
// la mierda el donut"). Lista simple, color = dato, sin torta.
function exposureBuckets(exposure) {
    const palette = currentPalette();
    return [
        { label: glossaryTerm('ars_tasa_fija', 'ARS sin cobertura cambiaria'), bucket: exposure.ars_fija, currency: 'ARS', color: palette.accent },
        { label: glossaryTerm('ars_cer', 'ARS CER'), bucket: exposure.ars_cer, currency: 'ARS', color: palette.warning },
        { label: 'USD', bucket: exposure.usd, currency: 'USD', color: palette.positive },
    ];
}

function currencyExposureHtml(exposure) {
    if (!exposure) return '<p class="table-empty">Sin datos.</p>';
    const buckets = exposureBuckets(exposure);
    // Un balde da negativo cuando hay más préstamo RECIBIDO (deuda) que
    // tenencias en esa moneda -- el % sigue restando de los demás baldes, por
    // eso pueden sumar >100%. Sin esta nota el número no cierra a simple
    // vista ("la exposición no la entiendo" -- Fase 17).
    const hasNegative = buckets.some((b) => b.bucket.valor < 0);
    return `
        <div class="currency-bar-legend">
            ${buckets.map((b) => `
                <div class="currency-bar-legend-row">
                    <span class="currency-bar-dot" style="background:${b.color}"></span>
                    <span>${b.label}</span>
                    <span class="amount num ${b.bucket.valor < 0 ? 'cell-negative' : ''}">${b.bucket.pct !== null ? fmtPct(b.bucket.pct) : '—'} · ${fmtMoney(b.bucket.valor, b.currency)}</span>
                </div>
            `).join('')}
        </div>
        ${hasNegative ? '<p class="hint" style="margin-top:8px;">Un balde negativo significa más deuda (préstamos recibidos) que tenencias en esa moneda — resta del % de los demás, por eso pueden sumar más de 100%.</p>' : ''}
    `;
}

// Fase 17: "esto creo que podemos unificarlo en la cascada" -- la tabla
// Clase/Actual/Objetivo/Desvío se sacó (ver decompNodeHtml, que ahora
// muestra objetivo/desvío inline en cada nodo con objetivo configurado).
// Solo queda acá el flujo de EDICIÓN, movido a un modal (openModal) porque
// ya no tiene una tabla al lado donde vivir.
function openTargetAllocationModal() {
    const { claseSegments, claseTotal } = currentClaseCtx || { claseSegments: [], claseTotal: 0 };
    const sorted = [...claseSegments].sort((a, b) => b.value - a.value);
    openModal({
        title: 'Editar objetivo de asignación',
        bodyHtml: `
            <div class="rebalance-list">
                ${sorted.map((s) => `
                    <div class="rebalance-row">
                        <span>${s.label}</span>
                        <span class="num">${claseTotal ? fmtPct((s.value / claseTotal) * 100) : '—'}</span>
                        <input type="number" class="target-pct-input" data-type="${s.type}"
                            value="${currentTargetAllocation?.[s.type] ?? ''}" min="0" max="100" step="0.1" placeholder="—">
                    </div>
                `).join('')}
            </div>
            <div class="hint" style="margin-top: 8px;">Suma de objetivos: <span id="patrimonio-target-sum">0</span>%</div>
            <p class="modal-error"></p>
            <div class="modal-footer"><button type="submit" class="btn btn-primary">Guardar</button></div>
        `,
        onMount: (form) => {
            const sumEl = form.querySelector('#patrimonio-target-sum');
            const inputs = form.querySelectorAll('.target-pct-input');
            const updateSum = () => {
                const sum = [...inputs].reduce((acc, input) => acc + (Number(input.value) || 0), 0);
                sumEl.textContent = sum.toFixed(1);
            };
            inputs.forEach((input) => input.addEventListener('input', updateSum));
            updateSum();
        },
        onSubmit: async (form) => {
            const targets = {};
            form.querySelectorAll('.target-pct-input').forEach((input) => {
                const raw = input.value.trim();
                targets[input.dataset.type] = raw === '' ? null : Number(raw);
            });
            currentTargetAllocation = (await api.updateTargetAllocation(targets)).data;
            repaintClaseArea();
        },
    });
}

function activosDetailHtml(loans, activosTotal, pasivosTotal, usdRate, exposure) {
    return `
        <div style="display:flex; justify-content:space-between; align-items:flex-start; gap:10px;">
            <div>
                <h2>Por clase de activo</h2>
                <p class="panel-subtitle" style="margin:0;">Click en una clase para ver el detalle (una a la vez) — la tarjeta muestra el objetivo configurado y el desvío, si tiene.</p>
            </div>
            <button type="button" class="btn btn-secondary" id="patrimonio-target-edit-btn">Editar objetivo</button>
        </div>
        <div style="display:flex; gap:24px; align-items:stretch; flex-wrap:wrap; margin-top:12px;">
            <div class="decomp-panel-col" style="flex:0 0 300px; display:flex; flex-direction:column; gap:16px;">
                <div>
                    <p class="stat-detail-label">Por clase</p>
                    <div id="patrimonio-clase-donut"></div>
                </div>
                <div>
                    <p class="stat-detail-label">Por riesgo</p>
                    <div id="patrimonio-risk-donut"></div>
                </div>
            </div>
            <div class="decomp-panel-col" style="flex:1 1 420px; min-width:0;" id="patrimonio-clase-area">${decompTreeHtml(currentClaseCtx)}</div>
        </div>
        ${equationBarHtml(activosTotal, pasivosTotal, usdRate)}
        <div class="grid12" style="margin-top:20px;">
            <div class="col-6">
                <p class="panel-subtitle" style="margin-bottom:8px;">Préstamos otorgados (te deben)</p>
                ${loansListHtml(loans, 'prestado', 'Nada prestado a terceros todavía.', usdRate)}
            </div>
            <div class="col-6">
                <p class="panel-subtitle" style="margin-bottom:8px;">Préstamos recibidos (debés)</p>
                ${loansListHtml(loans, 'recibido', 'No pediste plata prestada.', usdRate)}
            </div>
        </div>
        <div style="margin-top:20px;">
            <p class="panel-subtitle" style="margin-bottom:8px;">Exposición por moneda</p>
            ${currencyExposureHtml(exposure)}
        </div>
    `;
}

function riesgoDetailHtml() {
    return `
        <h2>Riesgo de cartera</h2>
        <p class="panel-subtitle">Sobre la porción que cotiza en mercado, en USD — mismo cálculo que el Dashboard.</p>
        <div id="patrimonio-risk-container"></div>
    `;
}

function vencimientosDetailHtml(events, risk, usdRate) {
    const atrasados = events.filter((e) => e.urgent);
    const proximoAlDia = events.find((e) => !e.urgent);
    const moraArs = risk?.mora_ars || 0;
    const moraUsd = risk?.mora_usd || 0;
    const moraTotal = (nativeToDisplay(moraArs, 'ARS', patrimonioCcy, usdRate) || 0) + (nativeToDisplay(moraUsd, 'USD', patrimonioCcy, usdRate) || 0);
    const moraSinCotizacion = (moraArs > 0 || moraUsd > 0) && (nativeToDisplay(moraArs, 'ARS', patrimonioCcy, usdRate) === null || nativeToDisplay(moraUsd, 'USD', patrimonioCcy, usdRate) === null);
    const tableHtml = !events.length
        ? '<p class="table-empty">Sin vencimientos próximos.</p>'
        : `<table>
            <thead><tr><th>Fecha</th><th>Evento</th><th class="num">Detalle</th></tr></thead>
            <tbody>${events.map((e) => `
                <tr>
                    <td class="${e.urgent ? 'cell-negative' : ''}">${e.fechaLabel}</td>
                    <td>${e.detalleLink || e.detalle}</td>
                    <td class="num">${e.monto || '—'}</td>
                </tr>
            `).join('')}</tbody>
        </table>`;
    return `
        <div class="grid12">
            <div class="col-8">
                <h2>Próximos vencimientos</h2>
                <p class="panel-subtitle">Los más urgentes primero — un préstamo atrasado pesa más que uno por vencer.</p>
                <div class="table-wrap">${tableHtml}</div>
            </div>
            <aside class="panel col-4">
                <div class="panel-header"><h2>Resumen</h2></div>
                <div class="panel-body">
                    <div class="mini-stat-grid">
                        <div class="mini-stat"><div class="l">Atrasados</div><div class="v num${atrasados.length ? ' cell-negative' : ''}">${atrasados.length}</div></div>
                        <div class="mini-stat"><div class="l">Próximo al día</div><div class="v num">${proximoAlDia ? proximoAlDia.fechaLabel : '—'}</div></div>
                        <div class="mini-stat span2"><div class="l">Monto en mora</div><div class="v num${moraTotal ? ' cell-negative' : ''}">${moraSinCotizacion ? '<span class="hint">sin cotización MEP</span>' : moraTotal ? fmtMoney(moraTotal, patrimonioCcy) : 'Sin mora'}</div></div>
                    </div>
                </div>
            </aside>
        </div>
    `;
}

function eventMontoHtml(amount, moneda, usdRate) {
    const converted = nativeToDisplay(amount, moneda, patrimonioCcy, usdRate);
    return converted === null ? '<span class="hint">sin cotización MEP</span>' : fmtMoney(converted, patrimonioCcy);
}

function buildVencimientosEvents(loans, cauciones, fixedIncomeEvents, usdRate) {
    const events = [];
    loans.filter((l) => l.estado === 'vigente').forEach((l) => {
        if (l.vencido_hace_dias) {
            events.push({
                urgent: true, sortKey: `0${String(1000 - l.vencido_hace_dias).padStart(5, '0')}`,
                fechaLabel: `hace ${l.vencido_hace_dias}d`,
                detalleLink: `Préstamo atrasado — ${l.deudor} (${l.moneda})`,
                monto: eventMontoHtml(l.capital_pendiente, l.moneda, usdRate),
            });
        } else if (l.proximo_vencimiento) {
            events.push({
                urgent: false, sortKey: `1${l.proximo_vencimiento}`,
                fechaLabel: fmtDate(l.proximo_vencimiento),
                detalleLink: `Cuota de préstamo — ${l.deudor}`,
                monto: eventMontoHtml(l.proximo_monto, l.moneda, usdRate),
            });
        }
    });
    cauciones.filter((c) => !c.vencido).forEach((c) => {
        events.push({
            urgent: false, sortKey: `1${c.caucion.fecha_vencimiento}`,
            fechaLabel: fmtDate(c.caucion.fecha_vencimiento),
            detalleLink: `Vencimiento de caución ${c.caucion.numero_boleto}`,
            monto: eventMontoHtml(c.valor_actual, c.caucion.moneda, usdRate),
        });
    });
    fixedIncomeEvents.forEach((e) => {
        events.push({
            urgent: false, sortKey: `1${e.date}`,
            fechaLabel: fmtDate(e.date),
            detalleLink: `${e.kind === 'vencimiento' ? 'Vencimiento' : 'Cupón'} de ${e.symbol}`,
            monto: null,
        });
    });
    return events.sort((a, b) => a.sortKey.localeCompare(b.sortKey));
}

function tileHtml(view, label, value, sub, alert) {
    return `
        <button type="button" class="tile-btn${view === patrimonioActiveTile ? ' active' : ''}" data-tile-view="${view}">
            <span class="t-label">${label}</span>
            <span class="t-value num">${value}</span>
            <span class="t-sub${alert ? ' alert' : ''}">${sub}</span>
        </button>
    `;
}

// Antes mostraba ARS como principal y "+ USD X" como texto suelto -- mismo
// patrón ya corregido en el resumen de Préstamos (un total único, nunca un
// "+" entre dos monedas).
function cajaUnificadaHtml(saldo, usdRate) {
    const ars = nativeToDisplay(saldo.ARS, 'ARS', patrimonioCcy, usdRate);
    const usd = nativeToDisplay(saldo.USD, 'USD', patrimonioCcy, usdRate);
    if (ars === null || usd === null) return 'sin cotización MEP';
    return fmtMoney(ars + usd, patrimonioCcy);
}

function tilesRowHtml({ twrPct, activosTotal, claseCount, pasivosTotal, cashSummary, risk, vencimientos, usdRate, period }) {
    const atrasados = vencimientos.filter((e) => e.urgent).length;
    const riskLabel = risk?.available ? `Vol. ${fmtPct(risk.annualized_volatility_pct)} anual` : 'Sin dato';
    const twrSub = `${glossaryTerm('twr', 'TWR')} — ${periodLabel(period)}`;
    return `
        <div class="tile-row">
            ${tileHtml('patrimonio', 'Patrimonio', fmtPct(twrPct, 2, { signed: true }), twrSub)}
            ${tileHtml('activos', 'Activos y Pasivos', fmtCcy(activosTotal, usdRate), `${claseCount} clase${claseCount === 1 ? '' : 's'} · ${fmtCcy(pasivosTotal, usdRate)} deuda`)}
            ${tileHtml('caja', 'Caja', cajaUnificadaHtml(cashSummary.saldo, usdRate), 'Saldo de caja')}
            ${tileHtml('riesgo', 'Riesgo', risk?.available ? (risk.annualized_volatility_pct < 25 ? 'Bajo' : risk.annualized_volatility_pct < 50 ? 'Medio' : 'Alto') : '—', riskLabel)}
            ${tileHtml('vencimientos', 'Vencimientos', atrasados > 0 ? `${atrasados} atrasados` : `${vencimientos.length} próximos`, atrasados > 0 ? 'Revisar ya' : 'Al día', atrasados > 0)}
        </div>
    `;
}

function applyTileSelection(container) {
    container.querySelectorAll('.tile-btn').forEach((b) => b.classList.toggle('active', b.dataset.tileView === patrimonioActiveTile));
    container.querySelectorAll('.tile-detail-panel').forEach((p) => p.classList.toggle('active', p.dataset.panel === patrimonioActiveTile));
    const isPatrimonio = patrimonioActiveTile === 'patrimonio';
    const detailWrap = $('patrimonio-detail-wrap');
    const gananciaAside = $('patrimonio-ganancia-aside');
    if (detailWrap) { detailWrap.classList.toggle('col-8', isPatrimonio); detailWrap.classList.toggle('col-12', !isPatrimonio); }
    if (gananciaAside) gananciaAside.style.display = isPatrimonio ? '' : 'none';
}

function wireTiles(container) {
    container.querySelectorAll('.tile-btn').forEach((btn) => {
        btn.addEventListener('click', () => {
            patrimonioActiveTile = btn.dataset.tileView;
            applyTileSelection(container);
            // Los donuts (ApexCharts) se pintan mientras el panel de Activos
            // está oculto (display:none) si no es la pestaña inicial --
            // ApexCharts mide 0px en un contenedor oculto, recién se pintan
            // de verdad al entrar a esta pestaña.
            if (patrimonioActiveTile === 'activos') { renderClaseDonutChart(); renderRiskDonutChart(); }
        });
    });
}

// --- Caja (Fase 15): columnas apiladas por mes + saldo + recientes --------

const FLOW_PERIOD_DAYS = 365;

function withinLastDays(dateStr, days) {
    if (!dateStr) return false;
    const d = new Date(`${dateStr}T00:00:00Z`);
    const cutoff = new Date();
    cutoff.setUTCDate(cutoff.getUTCDate() - days);
    return d >= cutoff;
}

// "Ingresos y costos del período" (últimos 12 meses, mismo criterio que el
// KPI de Dividendos/Rentas, Fase 4.5) -- igual que el mockup, pero con datos
// reales del bundle, nunca inventados. Intereses de caución: suma el
// devengado COMPLETO de cada caución que venció en el período (ya calculado
// por get_cauciones_resumen, no se recalcula). Comisiones: se asume ARS
// (este broker las cobra en pesos sea cual sea la moneda del instrumento) --
// transactions no trae un campo de moneda propio, es la única forma honesta
// de sumarlas sin inventar una conversión.
function flowBreakdownItems(bundle) {
    const items = [];

    const rentasPorMoneda = {};
    (bundle.rentas_dividendos || []).forEach((r) => {
        if (r.tipo === 'amortizacion' || !withinLastDays(r.fecha, FLOW_PERIOD_DAYS)) return;
        rentasPorMoneda[r.moneda] = (rentasPorMoneda[r.moneda] || 0) + r.neto;
    });
    items.push({ label: 'Dividendos y rentas', parts: toParts(rentasPorMoneda) });

    const caucionPorMoneda = {};
    (bundle.cauciones || []).forEach((c) => {
        if (!c.vencido || !withinLastDays(c.caucion.fecha_vencimiento, FLOW_PERIOD_DAYS)) return;
        caucionPorMoneda[c.caucion.moneda] = (caucionPorMoneda[c.caucion.moneda] || 0) + (c.devengado || 0);
    });
    items.push({ label: 'Intereses de caución', parts: toParts(caucionPorMoneda) });

    const prestamosPorMoneda = {};
    (bundle.loan_payments || []).forEach((p) => {
        if (!withinLastDays(p.fecha, FLOW_PERIOD_DAYS)) return;
        const signed = p.direccion === 'recibido' ? -(p.interes || 0) : (p.interes || 0);
        prestamosPorMoneda[p.moneda] = (prestamosPorMoneda[p.moneda] || 0) + signed;
    });
    items.push({ label: 'Intereses de préstamos', parts: toParts(prestamosPorMoneda) });

    const comisionTotal = (bundle.transactions || [])
        .filter((t) => withinLastDays(t.fecha, FLOW_PERIOD_DAYS))
        .reduce((sum, t) => sum + (t.comision || 0), 0);
    items.push({ label: 'Comisiones', parts: comisionTotal > 0 ? toParts({ ARS: -comisionTotal }) : null });

    return items;
}

function toParts(byMoneda) {
    const entries = Object.entries(byMoneda).filter(([, v]) => v !== 0);
    return entries.length ? entries.map(([m, v]) => ({ m, v })) : null;
}

function flowBreakdownHtml(bundle, usdRate) {
    const items = flowBreakdownItems(bundle);
    return `<div class="rebalance-list">${items.map((it) => {
        if (!it.parts) return `<div class="rebalance-row"><span>${it.label}</span><span class="num cell-muted">—</span></div>`;
        let sinCotizacion = false;
        const total = it.parts.reduce((sum, { v, m }) => {
            const converted = nativeToDisplay(v, m, patrimonioCcy, usdRate);
            if (converted === null) { sinCotizacion = true; return sum; }
            return sum + converted;
        }, 0);
        if (sinCotizacion) return `<div class="rebalance-row"><span>${it.label}</span><span class="num hint">sin cotización MEP</span></div>`;
        const colorClass = total >= 0 ? 'cell-positive' : 'cell-negative';
        return `<div class="rebalance-row"><span>${it.label}</span><span class="num ${colorClass}">${fmtMoney(total, patrimonioCcy, 2, { signed: true })}</span></div>`;
    }).join('')}</div>`;
}

function cajaSectionHtml(cashSummary, bundle, usdRate) {
    const { saldo, recientes } = cashSummary;
    const recientesHtml = recientes.length
        ? `<div class="rebalance-list">${recientes.map((m) => {
            const converted = nativeToDisplay(m.monto, m.moneda, patrimonioCcy, usdRate);
            return `
            <div class="rebalance-row">
                <span>${fmtDate(m.fecha)} — ${m.notas || m.origen}</span>
                <span class="num ${m.monto >= 0 ? 'cell-positive' : 'cell-negative'}">${converted === null ? '<span class="hint">sin cotización MEP</span>' : fmtMoney(converted, patrimonioCcy, 2, { signed: true })}</span>
            </div>
        `;
        }).join('')}</div>`
        : '<p class="hint">Sin movimientos todavía.</p>';

    return `
        <h2>Caja — flujo real</h2>
        <p class="panel-subtitle">Ingresos y egresos de caja por mes (ARS) — esto es el flujo de caja de verdad, no la cascada de ingresos/costos del período que ya se ve en el Dashboard.</p>
        <p class="hint" style="margin-top:-4px;">Son montos BRUTOS: cada compra es un egreso y cada venta un ingreso, aunque la plata nunca quede parada — por eso estas barras pueden ser mucho más grandes que el saldo que queda abajo (que es lo que sobra después de que todo se cancela entre sí).</p>
        <div class="grid12">
            <div class="col-8"><div id="caja-flow-chart"></div></div>
            <div class="col-4">
                <div class="stat-detail-label">Saldo actual</div>
                <div class="detail-position-grid" style="margin-bottom:14px;">
                    <div class="detail-position-item"><div class="label">ARS</div><div class="value num">${fmtMoney(saldo.ARS, 'ARS')}</div></div>
                    <div class="detail-position-item"><div class="label">USD</div><div class="value num">${fmtMoney(saldo.USD, 'USD')}</div></div>
                </div>
                <div class="stat-detail-label">Movimientos recientes</div>
                ${recientesHtml}
                <div class="stat-detail-label" style="margin-top:16px;">Ingresos y costos del período</div>
                <p class="hint" style="margin:-4px 0 8px;">Últimos 12 meses — no incluido en el TWR de Evolución.</p>
                ${flowBreakdownHtml(bundle, usdRate)}
            </div>
        </div>
    `;
}

function heroDeltaHtml(benchmarks, period) {
    const twr = benchmarks?.cartera?.periods?.[period]?.twr_pct;
    if (twr === null || twr === undefined) return '<span class="hero-delta" id="patrimonio-hero-delta" style="display:none;"></span>';
    const noun = PERIOD_NOUN[period] || 'el período';
    return `<span class="hero-delta" id="patrimonio-hero-delta">${fmtPct(twr, 2, { signed: true })} en ${noun}</span>`;
}

function headerHtml(exposure, usdMepRate, benchmarks) {
    const rate = usdMepRate?.rate;
    let patrimonioUsd = null;
    let patrimonioArs = null;
    if (exposure && rate) {
        patrimonioUsd = (exposure.ars_fija.valor + exposure.ars_cer.valor) / rate + exposure.usd.valor;
        patrimonioArs = patrimonioUsd * rate;
    }
    const heroValue = patrimonioUsd === null ? '—' : (patrimonioCcy === 'ARS' ? fmtMoney(patrimonioArs, 'ARS') : fmtMoney(patrimonioUsd, 'USD'));
    return `
        <div class="hero-card">
            <div class="hero-top">
                <div>
                    <div class="hero-eyebrow">
                        ${glossaryTerm('patrimonio_mep', 'Patrimonio neto')}
                    </div>
                    <div style="display:flex; align-items:baseline; gap:14px; flex-wrap:wrap;">
                        <span class="hero-value num">${heroValue}</span>
                        <span class="hero-sub num">${patrimonioArs !== null
                            // Siempre el complemento del valor principal (si arriba
                            // es ARS, acá va USD y viceversa) -- antes mostraba ARS
                            // fijo sin importar cuál era el principal, duplicando el
                            // mismo número cuando ARS ya era el de arriba.
                            ? `${patrimonioCcy === 'ARS' ? fmtMoney(patrimonioUsd, 'USD') : fmtMoney(patrimonioArs, 'ARS')} · ${glossaryTerm('dolar_mep', 'dólar')} ${fmtMoney(rate, 'ARS')}`
                            : 'No se pudo obtener la cotización MEP'}</span>
                        ${heroDeltaHtml(benchmarks, patrimonioPeriod)}
                    </div>
                </div>
            </div>
        </div>
    `;
}

// --- Evolución patrimonial (Gap A/B): mismo chart de benchmarks del Dashboard
// (TWR cartera vs. caución/SPY) -- el período se controla desde el topbar.
function evolucionSectionHtml() {
    // Los pills de período se mudan acá (ver conversación, Fase 5) -- antes
    // vivían en el hero, lejos del gráfico que en realidad controlan; el
    // hero solo muestra el delta resultante (heroDeltaHtml), no el selector.
    return `
        <div class="evolucion-section-header">
            <h2>Evolución patrimonial</h2>
            <div class="segmented-toggle" id="patrimonio-period-controls">${benchmarkPeriodControlsHtml(patrimonioPeriod)}</div>
        </div>
        <p class="panel-subtitle">${glossaryTerm('twr', 'TWR')} de la cartera vs. ${glossaryTerm('benchmark_caucion', 'caución')} y ${glossaryTerm('spy_benchmark', 'SPY')}.</p>
        <div id="patrimonio-benchmark-container"></div>
        <div id="patrimonio-benchmark-stats" style="margin-top:14px;"></div>
    `;
}

// --- "¿Cuánto gané?" -- aside siempre visible junto al tile-switcher, solo
// en el tile Patrimonio (igual que el mockup: gananciaAside).
function gananciaColHtml(moneda, data) {
    const xirr = data.xirr;
    const xirrHtml = !xirr?.available
        ? `<span class="muted-cell" title="${xirr?.reason || ''}">—</span>`
        : `<span class="${xirr.xirr_pct >= 0 ? 'cell-positive' : 'cell-negative'}">${fmtPct(xirr.xirr_pct, 2, { signed: true })}</span>`;
    return `
        <div class="ganancia-col">
            <h3>${moneda}</h3>
            <div class="ganancia-row"><span class="l">Patrimonio</span><span class="v num">${fmtMoney(data.patrimonio_actual, moneda, 0)}</span></div>
            <div class="ganancia-row"><span class="l">Aportes netos</span><span class="v num">${fmtMoney(data.aportes_netos, moneda, 0)}</span></div>
            <div class="ganancia-row"><span class="l">Ganancia</span><span class="v num ${data.ganancia_total >= 0 ? 'cell-positive' : 'cell-negative'}">${fmtMoney(data.ganancia_total, moneda, 0, { signed: true })}</span></div>
            <div class="ganancia-row"><span class="l">XIRR</span><span class="v num">${xirrHtml}</span></div>
        </div>
    `;
}

// Fase 17: "si lo veo en pesos quiero ver todo en pesos, si es en dólares
// todo en dólares" -- antes esta tarjeta era la única excepción al toggle de
// Preferencias (mostraba ARS y USD siempre juntos). Ahora lidera con UN total
// unificado en la moneda elegida (Patrimonio/Aportes/Ganancia convertidos con
// la MEP actual, igual criterio que el resto de la plataforma) y el detalle
// nativo por moneda queda como sub-sección de apoyo, no como lo principal.
//
// El XIRR NO se unifica: es una tasa anualizada calculada sobre el historial
// real de flujos de CADA moneda (fechas y montos nativos, ver xirr.py) --
// convertir el % con la cotización de HOY sería un número inventado, no una
// tasa real. Por eso el detalle nativo de abajo sigue mostrando el XIRR de
// ARS y de USD por separado, con una nota explicando por qué no hay uno solo.
function gananciaAsideHtml(ganancia, usdRate) {
    if (!ganancia) return '<aside class="panel col-4" id="patrimonio-ganancia-aside"></aside>';
    const arsToDisplay = (v) => nativeToDisplay(v, 'ARS', patrimonioCcy, usdRate);
    const usdToDisplay = (v) => nativeToDisplay(v, 'USD', patrimonioCcy, usdRate);
    const patrimonioArs = arsToDisplay(ganancia.ARS.patrimonio_actual);
    const patrimonioUsd = usdToDisplay(ganancia.USD.patrimonio_actual);
    const aportesArs = arsToDisplay(ganancia.ARS.aportes_netos);
    const aportesUsd = usdToDisplay(ganancia.USD.aportes_netos);
    const sinCotizacion = [patrimonioArs, patrimonioUsd, aportesArs, aportesUsd].some((v) => v === null);
    const patrimonioU = sinCotizacion ? null : patrimonioArs + patrimonioUsd;
    const aportesU = sinCotizacion ? null : aportesArs + aportesUsd;
    const gananciaU = sinCotizacion ? null : patrimonioU - aportesU;
    return `
        <aside class="panel col-4" id="patrimonio-ganancia-aside">
            <div class="panel-header">
                <div class="panel-header-text">
                    <h2>¿Cuánto gané?</h2>
                    <p class="panel-subtitle">Patrimonio − aportes netos, unificado en ${patrimonioCcy}.</p>
                </div>
            </div>
            <div class="panel-body">
                ${sinCotizacion ? '<p class="hint">Sin cotización MEP disponible para unificar.</p>' : `
                    <div class="ganancia-row"><span class="l">Patrimonio</span><span class="v num">${fmtMoney(patrimonioU, patrimonioCcy, 0)}</span></div>
                    <div class="ganancia-row"><span class="l">Aportes netos</span><span class="v num">${fmtMoney(aportesU, patrimonioCcy, 0)}</span></div>
                    <div class="ganancia-row"><span class="l">Ganancia</span><span class="v num ${gananciaU >= 0 ? 'cell-positive' : 'cell-negative'}">${fmtMoney(gananciaU, patrimonioCcy, 0, { signed: true })}</span></div>
                `}
                <div class="stat-detail-label" style="margin-top:14px;">Detalle nativo por moneda</div>
                <p class="hint" style="margin:-2px 0 8px;">El XIRR no se unifica — es una tasa calculada sobre el historial real de aportes/retiros de cada moneda, no un monto que se pueda convertir.</p>
                <div class="ganancia-cols" style="flex-direction:column;">
                    ${gananciaColHtml('ARS', ganancia.ARS)}
                    ${gananciaColHtml('USD', ganancia.USD)}
                </div>
            </div>
        </aside>
    `;
}

// --- Proyección (Fase 12, acotada en Fase 17) -- no está en el mockup (que
// no contemplaba este concepto), se deja como sección adicional debajo, sin
// reemplazar nada del mockup ni competir con su layout. La tabla "Flujos por
// mes" (mes a mes, por origen) se mudó a Préstamos -- ver
// loans.js::monthlyLoanFlowsHtml -- porque era casi enteramente cuotas de
// préstamos; acá queda sólo el escenario Kronos (genuinamente de toda la
// cartera) y los movimientos futuros cargados a mano.
const HORIZON_KEYS = ['1m', '3m', '6m', '12m'];
const TIPO_MOVIMIENTO_LABEL = { aporte: 'Aporte', retiro: 'Retiro', gasto: 'Gasto', otro: 'Otro' };

// Fase 17: "esto sigue sin tener sentido, además no hay gráfico" -- el
// problema real era de alcance, no de redacción: "Hoy" se mostraba como una
// tarjeta grande igual a las de Patrimonio/Activos de arriba, pero es el
// valor de SOLO los activos con señal validada (acá, nada más que IBM) --
// al lado de un patrimonio de millones, $150.840 parece un número roto en
// vez de "esto es un subconjunto chico a propósito". Se aclara en la propia
// etiqueta, no solo en el párrafo de arriba, y se agrega la única pieza que
// realmente faltaba: una visualización (antes eran 4 números sueltos sin
// ninguna relación visual entre sí).
function kronosScenarioHtml(kronos, usdRate) {
    if (!kronos?.available) {
        return `<p class="hint">${kronos?.reason || 'Sin datos de Kronos.'}</p>`;
    }
    // kronos.* viene siempre en ARS del backend -- se convierte acá a la
    // moneda elegida en Preferencias, igual que el resto de la página.
    const conv = (v) => nativeToDisplay(v, 'ARS', patrimonioCcy, usdRate);
    const hoy = conv(kronos.valor_hoy);
    const conservador = conv(kronos.conservador);
    const base = conv(kronos.base);
    const optimista = conv(kronos.optimista);
    if (hoy === null) {
        return '<p class="hint">Sin cotización MEP disponible para convertir a la moneda elegida.</p>';
    }
    const domainMin = Math.min(conservador, hoy, optimista);
    const domainMax = Math.max(conservador, hoy, optimista);
    const span = (domainMax - domainMin) || 1;
    const pct = (v) => ((v - domainMin) / span) * 100;
    const subida = base >= hoy;
    return `
        <p class="hint">Sobre ${kronos.instrumentos_incluidos.length} tenencia${kronos.instrumentos_incluidos.length === 1 ? '' : 's'} con señal validada (${kronos.instrumentos_incluidos.join(', ')}) — NO sobre toda la cartera — a ~${kronos.horizonte_dias} días. Nunca se extrapola Kronos a plazos más largos que su propio horizonte de pronóstico.</p>
        <div class="kronos-scenario">
            <div class="kronos-scenario-track">
                <div class="kronos-scenario-fill ${subida ? 'up' : 'down'}" style="left:${pct(conservador)}%; width:${pct(optimista) - pct(conservador)}%;"></div>
                <div class="kronos-scenario-zero" style="left:${pct(hoy)}%;" title="Hoy: ${fmtMoney(hoy, patrimonioCcy)}"></div>
                <div class="kronos-scenario-marker" style="left:${pct(base)}%;" title="Base (mediana): ${fmtMoney(base, patrimonioCcy)}"></div>
            </div>
            <div class="kronos-scenario-endlabels">
                <span>${fmtMoney(conservador, patrimonioCcy)}<br><span class="hint">Conservador</span></span>
                <span style="margin-left:auto; text-align:right;">${fmtMoney(optimista, patrimonioCcy)}<br><span class="hint">Optimista</span></span>
            </div>
        </div>
        <div class="risk-metrics-strip">
            <div class="stat"><div class="stat-label">Valor hoy (solo activos incluidos)</div><div class="stat-value num">${fmtMoney(hoy, patrimonioCcy)}</div></div>
            <div class="stat"><div class="stat-label">Conservador (p10)</div><div class="stat-value num ${conservador >= hoy ? 'cell-positive' : 'cell-negative'}">${fmtMoney(conservador, patrimonioCcy)}</div></div>
            <div class="stat"><div class="stat-label">Base (mediana)</div><div class="stat-value num ${base >= hoy ? 'cell-positive' : 'cell-negative'}">${fmtMoney(base, patrimonioCcy)}</div></div>
            <div class="stat"><div class="stat-label">Optimista (p90)</div><div class="stat-value num ${optimista >= hoy ? 'cell-positive' : 'cell-negative'}">${fmtMoney(optimista, patrimonioCcy)}</div></div>
        </div>
    `;
}

// Fase 17: "Flujos por mes" (la tabla mes a mes) se mudó a Préstamos -- ahí
// es donde el usuario la quiere, y hoy esa tabla era casi enteramente cuotas
// de préstamos de cualquier forma (ver loans.js::monthlyLoanFlowsHtml). Acá
// queda sólo lo que es genuinamente de toda la cartera: el escenario
// probabilístico de Kronos y los movimientos futuros cargados a mano.
function projectionSectionHtml(projection, plannedMovements, usdRate) {
    return `
        <div class="panel">
            <div class="panel-header">
                <div class="panel-header-text">
                    <h2>Kronos y movimientos futuros</h2>
                    <p class="panel-subtitle">Escenario probabilístico de Kronos sobre la cartera, y lo que cargás vos a mano — las cuotas de préstamos viven en Préstamos.</p>
                </div>
                <button type="button" class="btn btn-secondary" id="new-planned-movement-btn">+ Movimiento futuro</button>
            </div>
            <div class="panel-body">
                <div class="stat-detail-label">Kronos (probabilístico)</div>
                ${kronosScenarioHtml(projection.kronos, usdRate)}
                <div class="stat-detail-label" style="margin-top:16px;">Movimientos futuros cargados</div>
                ${plannedMovements.length ? `<div class="rebalance-list">${plannedMovements.map((m) => `
                    <div class="rebalance-row">
                        <span>${fmtDate(m.fecha)} — ${TIPO_MOVIMIENTO_LABEL[m.tipo]}${m.notas ? ` (${m.notas})` : ''}</span>
                        <span class="num">${fmtMoney(m.monto, m.moneda)}</span>
                        <button type="button" class="row-action-btn planned-movement-delete-btn" data-id="${m.id}" aria-label="Eliminar">🗑</button>
                    </div>
                `).join('')}</div>` : '<p class="hint">Todavía no cargaste ningún movimiento futuro.</p>'}
            </div>
        </div>
    `;
}

function openPlannedMovementModal(onSaved) {
    openModal({
        title: 'Nuevo movimiento futuro',
        bodyHtml: `
            <div class="field-row">
                <div class="field"><label for="pm-fecha">Fecha</label><input type="date" id="pm-fecha" value="${todayLocalISO()}"></div>
                <div class="field">
                    <label for="pm-tipo">Tipo</label>
                    <select id="pm-tipo">
                        <option value="aporte">Aporte</option>
                        <option value="retiro">Retiro</option>
                        <option value="gasto">Gasto</option>
                        <option value="otro">Otro</option>
                    </select>
                </div>
                <div class="field"><label for="pm-monto">Monto</label><input type="number" step="any" id="pm-monto" value=""></div>
                <div class="field">
                    <label for="pm-moneda">Moneda</label>
                    <select id="pm-moneda">
                        <option value="ARS">ARS</option>
                        <option value="USD">USD</option>
                    </select>
                </div>
                <div class="field"><label for="pm-notas">Notas</label><input type="text" id="pm-notas" placeholder="Opcional"></div>
            </div>
            <p class="modal-error"></p>
            <div class="modal-footer"><button type="submit" class="btn btn-primary">Agregar</button></div>
        `,
        onSubmit: async (form) => {
            await api.addPlannedMovement({
                fecha: form.querySelector('#pm-fecha').value,
                tipo: form.querySelector('#pm-tipo').value,
                monto: form.querySelector('#pm-monto').value,
                moneda: form.querySelector('#pm-moneda').value,
                notas: form.querySelector('#pm-notas').value || null,
            });
            showStatus('status-banner', 'success', 'Movimiento agregado');
            await onSaved();
        },
    });
}

let wired = false;

function wirePatrimonioDelegation(reload) {
    if (wired) return;
    wired = true;
    // Ancestro común de Patrimonio Y Proyección (ahora pestañas separadas,
    // Fase 17) -- antes bastaba con patrimonio-panel porque todo vivía ahí
    // adentro, pero "+ Movimiento futuro"/su borrado ahora están en otro
    // subtab-panel hermano.
    const container = document.getElementById('view-resumen');
    container.addEventListener('click', async (e) => {
        const decompToggle = e.target.closest('[data-decomp-toggle]');
        if (decompToggle) {
            toggleDecompNode(decompToggle.dataset.decompToggle);
            return;
        }
        if (e.target.id === 'patrimonio-target-edit-btn') {
            openTargetAllocationModal();
            return;
        }
        if (e.target.id === 'new-planned-movement-btn') {
            openPlannedMovementModal(() => reload({ forceRefresh: true }));
            return;
        }
        const deleteBtn = e.target.closest('.planned-movement-delete-btn');
        if (deleteBtn) {
            await api.deletePlannedMovement(deleteBtn.dataset.id);
            showStatus('status-banner', 'success', 'Movimiento eliminado');
            await reload({ forceRefresh: true });
        }
    });
    // Las tarjetas del árbol son clickeables enteras (role="button"), asi
    // que tambien responden a teclado -- Enter/Space, igual que un botón real.
    container.addEventListener('keydown', (e) => {
        if (e.key !== 'Enter' && e.key !== ' ') return;
        const decompToggle = e.target.closest('[data-decomp-toggle]');
        if (!decompToggle) return;
        e.preventDefault();
        toggleDecompNode(decompToggle.dataset.decompToggle);
    });
}

async function getExtras(forceRefresh) {
    if (extrasCache && !forceRefresh) return extrasCache;
    const emptyProjection = {
        horizontes: Object.fromEntries(HORIZON_KEYS.map((k) => (
            [k, { contractual_total: {}, estimado_total: {}, contractual: [], estimado: [] }]
        ))),
        kronos: { available: false, reason: 'No se pudo cargar la proyección.' },
    };
    const [{ data: projection }, { data: plannedMovements }, { data: cashSummary }] = await Promise.all([
        api.projection().catch(() => ({ data: emptyProjection })),
        api.plannedMovements().catch(() => ({ data: [] })),
        api.cashSummary('ARS').catch(() => ({ data: { saldo: { ARS: 0, USD: 0 }, recientes: [], flow: null } })),
    ]);
    extrasCache = { projection, plannedMovements, cashSummary };
    return extrasCache;
}

export async function renderPatrimonio(bundle, { forceRefresh = false } = {}) {
    const container = $('patrimonio-panel');
    if (!container) return;
    const {
        holdings: holdingsRaw, cauciones, loans, currency_exposure: exposure, usd_mep_rate: usdMepRate,
        portfolio_risk: risk, fixed_income_events: fixedIncomeEvents, benchmarks, ganancia,
        target_allocation: targetAllocation, settings,
    } = bundle;
    const usdRate = usdMepRate?.rate;
    // El backend muta valor_actual in-place a settings.display_currency antes
    // de armar el bundle (_apply_display_currency) -- NO siempre es ARS.
    // Todo lo de acá para abajo (treemap por clase, por cuenta, drill-down)
    // asume ARS, así que se normaliza una sola vez acá (bug real: con
    // Preferencias en USD, sumar holdings crudos junto a cauciones/préstamos
    // ya convertidos a ARS rompía por completo la composición mostrada).
    const holdings = holdingsRaw.map((h) => ({
        ...h,
        valor_actual: nativeToDisplay(h.valor_actual, settings?.display_currency || 'ARS', 'ARS', usdRate),
    }));
    currentTargetAllocation = targetAllocation || {};
    // Arranca desde la preferencia global (Preferencias -> Moneda), no
    // hardcodeado a USD -- antes ignoraba por completo lo que el usuario
    // había guardado ahí (bug real: "no impacta el cambio de moneda").
    // Solo en el primer render: un click manual del toggle durante la
    // sesión no debe resetearse cada vez que se repinta por otro motivo
    // (cambio de período, edición de objetivo, etc.).
    if (!patrimonioCcyInitialized && settings?.display_currency) {
        patrimonioCcy = settings.display_currency;
        patrimonioCcyInitialized = true;
    }

    const cuentaSegments = buildCuentaSegments(holdings, cauciones, loans, usdRate);
    const cuentaTotal = cuentaSegments.reduce((sum, s) => sum + s.value, 0);

    const claseSegments = buildAllocationSegments(holdings, cauciones, loans, usdRate);
    const claseTotal = claseSegments.reduce((sum, s) => sum + s.value, 0);
    currentClaseCtx = { claseSegments, claseTotal, holdings, cauciones, loans, usdRate };

    const { projection, plannedMovements, cashSummary } = await getExtras(forceRefresh);

    const pasivosLoans = loans.filter((l) => l.estado === 'vigente' && l.direccion === 'recibido');
    const pasivosTotal = pasivosLoans.reduce((sum, l) => sum + (nativeToDisplay(l.capital_pendiente, l.moneda, 'ARS', usdRate) || 0), 0);
    const vencimientos = buildVencimientosEvents(loans, cauciones, fixedIncomeEvents || [], usdRate);
    const twrPct = benchmarks?.cartera?.periods?.[patrimonioPeriod]?.twr_pct ?? null;

    const reload = async (opts) => { await renderPatrimonio(bundle, opts); };

    container.innerHTML = `
        <div id="patrimonio-hero-sentinel"></div>
        <div class="page-sticky-head">
            ${headerHtml(exposure, usdMepRate, benchmarks)}
            ${tilesRowHtml({
                twrPct, activosTotal: cuentaTotal, claseCount: claseSegments.length,
                pasivosTotal, cashSummary, risk, vencimientos, usdRate,
                period: patrimonioPeriod,
            })}
        </div>
        <div class="grid12">
            <div class="panel col-8" id="patrimonio-detail-wrap">
                <div class="panel-body">
                    <div class="tile-detail-panel" data-panel="patrimonio">${evolucionSectionHtml()}</div>
                    <div class="tile-detail-panel" data-panel="activos">${activosDetailHtml(loans, cuentaTotal, pasivosTotal, usdRate, exposure)}</div>
                    <div class="tile-detail-panel" data-panel="caja">${cajaSectionHtml(cashSummary, bundle, usdRate)}</div>
                    <div class="tile-detail-panel" data-panel="riesgo">${riesgoDetailHtml()}</div>
                    <div class="tile-detail-panel" data-panel="vencimientos">${vencimientosDetailHtml(vencimientos, risk, usdRate)}</div>
                </div>
            </div>
            ${gananciaAsideHtml(ganancia, usdRate)}
        </div>
    `;
    // Pestaña propia, al lado de Patrimonio/Tabla (Fase 17) -- no va mezclado
    // al final de todo Patrimonio, el usuario lo pidió separado.
    const proyeccionEl = $('proyeccion-panel');
    if (proyeccionEl) proyeccionEl.innerHTML = projectionSectionHtml(projection, plannedMovements, usdRate);
    renderCashFlowChart($('caja-flow-chart'), cashSummary.flow);
    renderClaseDonutChart();
    renderRiskDonutChart();
    renderPortfolioRiskGauges($('patrimonio-risk-container'), risk);
    renderBenchmarkComparisonChart($('patrimonio-benchmark-container'), benchmarks, patrimonioPeriod);
    $('patrimonio-benchmark-stats').innerHTML = benchmarkStatStripHtml(benchmarks, patrimonioPeriod);
    wireBenchmarkPeriodControls($('patrimonio-period-controls'), (p) => {
        patrimonioPeriod = p;
        reload({ forceRefresh: false });
    });
    applyTileSelection(container);
    wireTiles(container);
    wirePatrimonioDelegation(reload);
    wireHeroCollapse();
}

// Colapso del hero al scrollear (ver conversación, Fase 5): un sentinel sin
// alto, pegado JUSTO antes de .page-sticky-head, deja de intersectar apenas
// el scroll lo empuja por encima del punto donde el header se "engancha" --
// ahí recién se agrega .collapsed al hero-card. Se re-crea en cada render
// (el contenedor entero se reemplaza via innerHTML) -- el observer viejo se
// desconecta primero para no acumular uno por cada cambio de período/moneda.
let heroCollapseObserver = null;

function wireHeroCollapse() {
    heroCollapseObserver?.disconnect();
    const sentinel = $('patrimonio-hero-sentinel');
    const hero = document.querySelector('#patrimonio-panel .hero-card');
    if (!sentinel || !hero) return;
    const subtabsH = getComputedStyle(document.documentElement).getPropertyValue('--subtabs-h').trim() || '0px';
    heroCollapseObserver = new IntersectionObserver(
        ([entry]) => hero.classList.toggle('collapsed', !entry.isIntersecting),
        { rootMargin: `-${subtabsH} 0px 0px 0px`, threshold: 0 },
    );
    heroCollapseObserver.observe(sentinel);
}
