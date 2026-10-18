// Stop-loss / take-profit (Fase 8). Se verificó contra el bundle real de
// `lightweight-charts` (build open-source, standalone): no trae una opción
// "draggable" para IPriceLine (sólo pan/zoom del propio gráfico) -- eso es
// real, no hay una línea de código que lo prenda. Pero SÍ expone los
// primitivos para construirlo a mano (mismo patrón documentado por la propia
// librería para "custom drawing tools"): `coordinateToPrice`/
// `priceToCoordinate` para traducir pixel<->precio, y `chart.applyOptions({
// handleScroll:false, handleScale:false })` para apagar el pan nativo
// mientras se arrastra una línea propia -- ver wireDraggableAlerts más abajo.
import { fmt } from '../shared/format.js';
import { showStatus } from '../shared/status.js';
import { api } from './api.js';
import { glossaryTerm } from '../shared/glossary.js';

const TIPO_LABEL = { sl: 'Stop-loss', tp: 'Take-profit' };

// Arriba del gráfico (no en el panel de abajo) -- dos botones simples que
// arman el modo "click para fijar el nivel", en vez de un formulario con
// moneda/nivel tipeados a mano.
export function alertToolbarHtml() {
    return `
        <div class="price-alert-toolbar">
            <button type="button" class="btn btn-secondary btn-compact" data-tipo="sl">+ Stop-loss</button>
            <button type="button" class="btn btn-secondary btn-compact" data-tipo="tp">+ Take-profit</button>
            <span class="hint price-alert-hint" hidden>Click en el gráfico para fijar el nivel — click de nuevo en el botón para cancelar.</span>
        </div>
    `;
}

export function wireAlertToolbar(toolbarEl, chart, candleSeries, instrumentId, onChange, dragState) {
    if (!toolbarEl || !chart || !candleSeries) return;
    let placing = null;
    const buttons = [...toolbarEl.querySelectorAll('[data-tipo]')];
    const hint = toolbarEl.querySelector('.price-alert-hint');
    const setPlacing = (tipo) => {
        placing = tipo;
        buttons.forEach((b) => b.classList.toggle('active', b.dataset.tipo === tipo));
        if (hint) hint.hidden = !tipo;
    };
    buttons.forEach((btn) => {
        btn.addEventListener('click', () => setPlacing(placing === btn.dataset.tipo ? null : btn.dataset.tipo));
    });
    // Un solo subscribe para toda la vida de este chart (se recrea en cada
    // render del gráfico, mismo ciclo de vida que el resto de sus series) --
    // revisa "placing" en cada click en vez de suscribir/desuscribir.
    chart.subscribeClick(async (param) => {
        // dragState.dragging: el mouseup que termina un arrastre de una
        // alerta ya existente (ver wireDraggableAlerts) también dispara este
        // mismo evento de click -- sin este chequeo, soltar una línea
        // arrastrada creaba ADEMÁS una alerta nueva en ese mismo punto.
        if (!placing || !param.point || dragState?.dragging) return;
        const nivel = candleSeries.coordinateToPrice(param.point.y);
        if (nivel === null || nivel === undefined) return;
        const tipo = placing;
        setPlacing(null);
        await api.addPriceAlert({ instrument_id: instrumentId, tipo, nivel: Math.round(nivel * 100) / 100 });
        showStatus('status-banner', 'success', `${TIPO_LABEL[tipo]} agregado en ${fmt(nivel)}.`);
        onChange();
    });
}

const DRAG_PIXEL_TOLERANCE = 6;
const CLICK_VS_DRAG_PIXELS = 4;

// El chart se recrea entero en cada render (nuevo candleSeries, nuevas
// price-lines) -- sin sacar los listeners de window del chart ANTERIOR antes
// de poner los nuevos, quedan apilados apuntando a series ya destruidas
// (chartInstance.remove()). Solo hay un chart de detalle activo a la vez, así
// que alcanza con un solo "cleanup pendiente" a nivel módulo.
let cleanupPreviousDrag = null;

// Arrastrar una alerta YA PUESTA para reposicionarla -- lightweight-charts no
// trae esto (ver nota arriba), se arma a mano: mousedown cerca de una
// price-line existente agarra esa línea, mousemove la mueve en vivo
// (feedback visual inmediato, sin pegarle al backend en cada pixel), mouseup
// recién ahí persiste el nivel nuevo vía PUT /price-alerts/<id> (ya existía,
// nunca se había conectado a nada en el frontend).
export function wireDraggableAlerts(container, chart, candleSeries, alertLines, onChange, dragState) {
    if (cleanupPreviousDrag) { cleanupPreviousDrag(); cleanupPreviousDrag = null; }
    if (!container || !chart || !candleSeries || !alertLines?.length) return;
    let dragging = null;

    const getY = (e) => e.clientY - container.getBoundingClientRect().top;
    const findLineAt = (y) => alertLines.find((l) => {
        const coord = candleSeries.priceToCoordinate(l.nivel);
        return coord !== null && Math.abs(coord - y) <= DRAG_PIXEL_TOLERANCE;
    });

    const onContainerMouseMove = (e) => {
        if (dragging) return;
        container.style.cursor = findLineAt(getY(e)) ? 'ns-resize' : '';
    };
    // Captura (3er arg `true`): tiene que correr ANTES que el handler interno
    // del propio gráfico (que vive en el canvas, adentro de este container),
    // para apagar handleScroll/handleScale a tiempo y que el gráfico no
    // arranque a hacer pan con el mismo mousedown que agarra la línea.
    let startY = null;
    // Tocar la línea sin moverla NO debe recargar nada -- antes cualquier
    // click (sin arrastre real) ya disparaba un update + recarga completa de
    // la página, justo cuando la intención era solo tocarla (ej. para después
    // ir a borrarla de la lista de abajo). Un mousedown+mouseup con menos de
    // CLICK_VS_DRAG_PIXELS de movimiento se trata como selección, no arrastre.
    const highlightAlertRow = (id) => {
        const row = document.querySelector(`.price-alert-row[data-id="${id}"]`);
        if (!row) return;
        row.scrollIntoView({ behavior: 'smooth', block: 'center' });
        row.classList.add('price-alert-row-highlight');
        setTimeout(() => row.classList.remove('price-alert-row-highlight'), 1500);
    };
    const onContainerMouseDown = (e) => {
        const hit = findLineAt(getY(e));
        if (!hit) return;
        e.preventDefault();
        dragging = hit;
        startY = getY(e);
        if (dragState) dragState.dragging = true;
        chart.applyOptions({ handleScroll: false, handleScale: false });
    };
    const onWindowMouseMove = (e) => {
        if (!dragging) return;
        const price = candleSeries.coordinateToPrice(getY(e));
        if (price === null || price === undefined) return;
        dragging.priceLine.applyOptions({ price });
    };
    const onWindowMouseUp = async (e) => {
        if (!dragging) return;
        const line = dragging;
        const moved = Math.abs(getY(e) - startY) > CLICK_VS_DRAG_PIXELS;
        dragging = null;
        chart.applyOptions({ handleScroll: true, handleScale: true });
        // El click sintetico que sigue a este mouseup (subscribeClick de
        // wireAlertToolbar) se descarta mientras dragState.dragging siga en
        // true -- se libera recien en el proximo tick, despues de que ese
        // click ya paso.
        const releaseDragState = () => setTimeout(() => { if (dragState) dragState.dragging = false; }, 0);
        if (!moved) {
            // Solo tocarla: no se actualiza nada ni se recarga la página --
            // se resalta la fila de abajo para que sea fácil ir a "Eliminar".
            line.priceLine.applyOptions({ price: line.nivel });
            highlightAlertRow(line.id);
            releaseDragState();
            return;
        }
        const price = candleSeries.coordinateToPrice(getY(e));
        if (price === null || price === undefined) { releaseDragState(); return; }
        const nivel = Math.round(price * 100) / 100;
        line.priceLine.applyOptions({ price: nivel });
        line.nivel = nivel;
        await api.updatePriceAlert(line.id, { nivel });
        showStatus('status-banner', 'success', `Nivel actualizado a ${fmt(nivel)}.`);
        onChange();
        releaseDragState();
    };

    container.addEventListener('mousemove', onContainerMouseMove);
    container.addEventListener('mousedown', onContainerMouseDown, true);
    window.addEventListener('mousemove', onWindowMouseMove);
    window.addEventListener('mouseup', onWindowMouseUp);

    cleanupPreviousDrag = () => {
        container.removeEventListener('mousemove', onContainerMouseMove);
        container.removeEventListener('mousedown', onContainerMouseDown, true);
        window.removeEventListener('mousemove', onWindowMouseMove);
        window.removeEventListener('mouseup', onWindowMouseUp);
    };
}

// Rediseño mas compacto (ver conversación, pase de UX/UI): badge chico +
// nivel + botón "Eliminar" chico, todos alineados en una sola línea corta --
// antes el botón "Eliminar" de tamaño normal desbalanceaba filas que son,
// en esencia, un dato de una sola línea.
export function priceAlertsPanelHtml(alerts) {
    const rows = alerts.map((a) => `
        <div class="price-alert-row" data-id="${a.id}">
            <span class="tag price-alert-tag ${a.tipo === 'sl' ? 'tag-negative' : 'tag-positive'}">${TIPO_LABEL[a.tipo]}</span>
            <span class="price-alert-level num">${fmt(a.nivel)}</span>
            <button type="button" class="btn btn-secondary btn-compact price-alert-delete-btn" data-id="${a.id}">Eliminar</button>
        </div>
    `).join('');
    return `
        <div class="panel-header"><h2>Alertas de precio</h2></div>
        <div class="panel-body">
            <p class="panel-subtitle">${glossaryTerm('stop_loss', 'Stop-loss')} / ${glossaryTerm('take_profit', 'take-profit')}, en la moneda nativa del instrumento — se agregan con los botones "Fijar TP/SL" del Análisis Cuantitativo, con los botones arriba del gráfico, o haciendo click directo en el nivel exacto.</p>
            <div class="price-alert-list">${rows || '<p class="hint">Sin alertas configuradas para este activo.</p>'}</div>
        </div>
    `;
}

export function wirePriceAlertsPanel(root, onChange) {
    root.querySelectorAll('.price-alert-delete-btn').forEach((btn) => {
        btn.addEventListener('click', async () => {
            await api.deletePriceAlert(Number(btn.dataset.id));
            onChange();
        });
    });
}

// Dibuja las alertas activas de este instrumento como price-lines estáticas
// sobre la serie de velas ya creada -- se llama después de renderForecastChart
// (ver instrument-detail.js), nunca antes (necesita la serie ya existir).
export function drawPriceAlertLines(candleSeries, alerts) {
    return alerts.map((a) => candleSeries.createPriceLine({
        price: a.nivel,
        color: a.tipo === 'sl' ? '#ef5b5b' : '#30c177',
        lineWidth: 1,
        lineStyle: 2, // Dashed
        axisLabelVisible: true,
        title: TIPO_LABEL[a.tipo],
    }));
}
