import { fmt } from '../shared/format.js';
import { observeChartResize } from './lw-chart-utils.js';
import { glossaryTerm } from '../shared/glossary.js';
import { getChartPalette, bindChartTheme, withAlpha } from '../shared/chart-palette.js';

// Ya no es una constante congelada en hex al momento del import (ver
// DESIGN_SYSTEM.md hallazgo 1/2) -- cada funcion de este archivo pide su
// propia paleta fresca via getChartPalette() en el momento en que corre.

const RANGE_PRESETS = [
    { key: '1m', label: '1M', months: 1 },
    { key: '3m', label: '3M', months: 3 },
    { key: '6m', label: '6M', months: 6 },
    { key: '1a', label: '1A', months: 12 },
    { key: 'todo', label: 'Todo', months: null },
];

const DAY_SECONDS = 24 * 3600;

function baseChartOptions(container, height, palette) {
    return {
        width: container.clientWidth,
        height,
        layout: { background: { color: 'transparent' }, textColor: palette.text, attributionLogo: false },
        grid: { vertLines: { color: palette.grid }, horzLines: { color: palette.grid } },
        timeScale: { borderColor: palette.grid },
        // minimumWidth fijo e igual en los 3 paneles (precio/RSI/MACD): sin
        // esto cada panel autoajusta el ancho de su eje de precio segun el
        // texto mas largo que tenga que mostrar ("120.00" vs "1.15" vs
        // "-0.02"), y el area de ploteo termina en un pixel distinto en cada
        // uno -- eso es lo que se veia "descentrado" entre paneles.
        rightPriceScale: { borderColor: palette.grid, minimumWidth: 70 },
    };
}

// Las opciones de layout/grid/timeScale/rightPriceScale son identicas entre
// los 3 paneles (precio/RSI/MACD) -- reusado tanto al crear el chart como al
// reaplicar colores en bindChartTheme, para no desincronizar ambos lugares.
function themeChartOptions(palette) {
    return {
        layout: { textColor: palette.text },
        grid: { vertLines: { color: palette.grid }, horzLines: { color: palette.grid } },
        timeScale: { borderColor: palette.grid },
        rightPriceScale: { borderColor: palette.grid },
    };
}

// Soporte/resistencia (Fase 8): un segmento de 2 puntos, mismo patron que el
// resto de los overlays de este archivo (LineSeries sin price-line/label
// propio para no competir con la vela actual).
function drawTrendline(chart, line, color, visible = true) {
    const series = chart.addSeries(LightweightCharts.LineSeries, {
        color, lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dotted,
        lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false, visible,
    });
    series.setData([
        { time: line.from.time, value: line.from.value },
        { time: line.to.time, value: line.to.value },
    ]);
    return series;
}

function zipSeries(candles, values) {
    return candles
        .map((c, i) => ({ time: c.time, value: values[i] }))
        .filter((p) => p.value !== null && p.value !== undefined && !Number.isNaN(p.value));
}

// Que overlay empieza prendido/apagado -- el gráfico con TODO encima a la vez
// (velas+SMA+EMA+Bollinger+Kronos+soporte+resistencia, todo superpuesto en el
// mismo panel) es justamente lo que se reportó como ilegible. SMA/EMA/Kronos
// son los que un usuario común quiere ver siempre; Bollinger y soporte/
// resistencia quedan apagados por default pero a un click de distancia (como
// en TradingView: los indicadores son opt-in, no todos prendidos a la vez).
const DEFAULT_OFF = new Set(['bollinger', 'support', 'resistance']);

export function chartLegendHtml(withPrediction = true) {
    const palette = getChartPalette();
    // Soporte/Resistencia ya se dibujan solas (pivotes confirmados, Theil-Sen,
    // ver portfolio/trendlines.py) pero no estaban en la leyenda -- sin esto
    // las líneas punteadas en el gráfico parecen ruido sin explicación, en
    // vez de una señal reconocible y ya automática. Cada item con
    // data-series es clickeable (ver wireChartLegend) para prender/apagar
    // ese overlay puntual, igual que en TradingView.
    const item = (key, color, label, opacity) => `
        <span class="chart-legend-item${DEFAULT_OFF.has(key) ? ' off' : ''}" data-series="${key}">
            <span class="chart-legend-swatch" style="background:${color}${opacity ? `; opacity:${opacity}` : ''}"></span>${label}
        </span>`;
    const overlayColors = technicalOverlayColors(palette);
    return `
        <div class="chart-legend">
            <span class="chart-legend-item"><span class="chart-legend-swatch" style="background:${palette.positive}"></span>Real</span>
            ${item('sma', overlayColors.sma, glossaryTerm('sma20', 'SMA 20'))}
            ${item('ema', overlayColors.ema, glossaryTerm('ema20', 'EMA 20'))}
            ${item('bollinger', overlayColors.bollinger, glossaryTerm('bollinger', 'Bandas de Bollinger'))}
            ${withPrediction ? item('predicted', palette.accent, 'Predicho (Kronos)') : ''}
            ${withPrediction ? item('banda', withAlpha(palette.accent, 0.4), glossaryTerm('rango_p10_p90', 'Rango p10-p90')) : ''}
            ${item('support', palette.positive, glossaryTerm('tendencia_soporte_resistencia', 'Soporte'), 0.6)}
            ${item('resistance', palette.negative, glossaryTerm('tendencia_soporte_resistencia', 'Resistencia'), 0.6)}
        </div>`;
}

// Click en un item de la leyenda prende/apaga ese overlay puntual (nunca las
// velas: "Real" no tiene data-series, siempre queda fijo). seriesMap viene de
// renderForecastChart -- cada key coincide 1 a 1 con las de chartLegendHtml.
export function wireChartLegend(legendEl, seriesMap) {
    if (!legendEl || !seriesMap) return;
    legendEl.querySelectorAll('.chart-legend-item[data-series]').forEach((item) => {
        const key = item.dataset.series;
        const series = seriesMap[key];
        if (!series) return;
        item.addEventListener('click', () => {
            const nowOff = !item.classList.contains('off');
            item.classList.toggle('off', nowOff);
            if (Array.isArray(series)) series.forEach((s) => s.applyOptions({ visible: !nowOff }));
            else series.applyOptions({ visible: !nowOff });
        });
    });
}

export function macdLegendHtml() {
    const palette = getChartPalette();
    return `
        <div class="chart-legend">
            <span class="chart-legend-item"><span class="chart-legend-swatch" style="background:${palette.accentStrong}"></span>MACD</span>
            <span class="chart-legend-item"><span class="chart-legend-swatch" style="background:${palette.warning}"></span>Señal</span>
            <span class="chart-legend-item"><span class="chart-legend-swatch" style="background:${palette.positive}"></span>Histograma +</span>
            <span class="chart-legend-item"><span class="chart-legend-swatch" style="background:${palette.negative}"></span>Histograma -</span>
        </div>`;
}

export function accuracyLegendHtml() {
    const palette = getChartPalette();
    return `
        <div class="chart-legend">
            <span class="chart-legend-item"><span class="chart-legend-swatch" style="background:${palette.positive}"></span>Real</span>
            <span class="chart-legend-item"><span class="chart-legend-swatch" style="background:${palette.accent}"></span>${glossaryTerm('predicho_en_su_momento', 'Predicho en su momento')}</span>
        </div>`;
}

// SMA/EMA/Bollinger son contexto, no el foco -- antes EMA usaba accentStrong,
// el mismo azul (una familia) que la linea "Predicho" y la banda p10-p90,
// compitiendo justo con lo que mas importa mirar. Ahora quedan en la familia
// gris/amber, mas tenues (withAlpha) que las velas/prediccion/banda, para
// que se lean como fondo y no como protagonistas (ver DESIGN_SYSTEM.md).
function technicalOverlayColors(palette) {
    return {
        sma: withAlpha(palette.warning, 0.7),
        ema: withAlpha(palette.text, 0.9),
        bollinger: withAlpha(palette.text, 0.45),
    };
}

// Reusado por renderAccuracyChart y renderForecastChart -- las unicas 2
// series de velas del archivo, mismo esquema de color en ambas.
function candleColors(palette) {
    return {
        upColor: palette.positive, downColor: palette.negative,
        borderUpColor: palette.positive, borderDownColor: palette.negative,
        wickUpColor: palette.positive, wickDownColor: palette.negative,
    };
}

export function renderAccuracyChart(container, data) {
    container.innerHTML = '';
    container.style.position = 'relative';

    const palette = getChartPalette();
    const chart = LightweightCharts.createChart(container, baseChartOptions(container, 320, palette));

    const candles = chart.addSeries(LightweightCharts.CandlestickSeries, candleColors(palette));
    candles.setData(data.historical);

    const predicted = chart.addSeries(LightweightCharts.LineSeries, {
        lastValueVisible: false, priceLineVisible: false,
        color: palette.accent, lineWidth: 2, lineStyle: LightweightCharts.LineStyle.Dashed,
    });
    predicted.setData(data.predicted_points.map((p) => ({ time: p.time, value: p.predicted })));

    chart.timeScale().fitContent();
    observeChartResize(chart, container);

    bindChartTheme(container, (next) => {
        chart.applyOptions(themeChartOptions(next));
        candles.applyOptions(candleColors(next));
        predicted.applyOptions({ color: next.accent });
    });

    return chart;
}

// Fase 2 (ver conversación, sistema de diseño unificado): filtro/selección
// de rango visible del gráfico (no navegación) -- mismo componente
// segmented-toggle que el resto de los selectores de período, ver
// benchmark-comparison-chart.js::benchmarkPeriodControlsHtml.
export function rangeControlsHtml() {
    return `<div class="segmented-toggle">${RANGE_PRESETS.map((r, i) => `
        <button class="segmented-toggle-option${i === RANGE_PRESETS.length - 1 ? ' active' : ''}" data-range="${r.key}" type="button">${r.label}</button>
    `).join('')}</div>`;
}

// root (#detail-chart-range) es parte del skeleton HTML armado UNA sola vez
// -- a diferencia del chart, estos <button> nunca se recrean entre un render
// "rapido" (sin prediccion) y el definitivo (con Kronos ya resuelto), asi que
// sin limpiar el listener antes de agregar uno nuevo, cada repintado del
// chart (paintChartAndWireAlerts corre 2+ veces por pagina) apila otro
// listener sobre los MISMOS botones -- el viejo sigue cerrado sobre un
// `chart` ya invalido (el actual se reemplaza por uno nuevo en cada
// render), y explota con "Object is disposed" al clickear un rango (bug
// real, reproducido con Playwright). Mismo patron de limpieza que
// container._resizeCleanup en lw-chart-utils.js.
export function wireRangeControls(root, chart, forecast) {
    root._rangeCleanup?.();

    const buttons = [...root.querySelectorAll('.segmented-toggle-option')];
    if (!forecast.historical.length) return;
    const lastHistorical = forecast.historical[forecast.historical.length - 1].time;
    const firstHistorical = forecast.historical[0].time;
    const lastPredicted = forecast.predicted.length
        ? forecast.predicted[forecast.predicted.length - 1].time
        : lastHistorical;

    const handlers = buttons.map((btn) => {
        const handler = () => {
            buttons.forEach((b) => b.classList.toggle('active', b === btn));
            const preset = RANGE_PRESETS.find((r) => r.key === btn.dataset.range);
            if (preset.months === null) {
                chart.timeScale().fitContent();
                return;
            }
            const from = Math.max(firstHistorical, lastHistorical - preset.months * 30 * DAY_SECONDS);
            chart.timeScale().setVisibleRange({ from, to: lastPredicted });
        };
        btn.addEventListener('click', handler);
        return [btn, handler];
    });

    root._rangeCleanup = () => handlers.forEach(([btn, handler]) => btn.removeEventListener('click', handler));
}

function buildTimeIndexes(forecast) {
    const historicalIndex = new Map();
    forecast.historical.forEach((c, i) => historicalIndex.set(c.time, i));
    const predictedIndex = new Map();
    forecast.predicted.forEach((c, i) => predictedIndex.set(c.time, i));
    return { historicalIndex, predictedIndex };
}

function formatTooltipDate(time) {
    return new Date(time * 1000).toLocaleDateString('es-AR', { day: '2-digit', month: 'short', year: 'numeric' });
}

// Compacto a proposito (1 fila de OHLC + 1 linea de indicadores, en vez de
// hasta 9 filas apiladas) -- ver DESIGN_SYSTEM.md: un tooltip alto tapaba la
// vela bajo el cursor justo cuando el usuario queria mirarla.
function tooltipHtml(forecast, indexes, time) {
    if (indexes.historicalIndex.has(time)) {
        const i = indexes.historicalIndex.get(time);
        const c = forecast.historical[i];
        const { sma_20: sma, ema_20: ema, rsi_14: rsi, macd, bb_upper: bbUpper, bb_lower: bbLower } = forecast.indicators;
        const extra = [
            sma[i] != null ? `SMA <b class="num">${fmt(sma[i])}</b>` : null,
            ema[i] != null ? `EMA <b class="num">${fmt(ema[i])}</b>` : null,
            bbUpper[i] != null ? `BB <b class="num">${fmt(bbUpper[i])}/${fmt(bbLower[i])}</b>` : null,
            rsi[i] != null ? `RSI <b class="num">${fmt(rsi[i])}</b>` : null,
            macd[i] != null ? `MACD <b class="num">${fmt(macd[i])}</b>` : null,
        ].filter(Boolean);
        return `
            <div class="chart-tooltip-date">${formatTooltipDate(time)}</div>
            <div class="chart-tooltip-ohlc">
                <span>O <b class="num">${fmt(c.open)}</b></span>
                <span>H <b class="num">${fmt(c.high)}</b></span>
                <span>L <b class="num">${fmt(c.low)}</b></span>
                <span>C <b class="num">${fmt(c.close)}</b></span>
            </div>
            ${extra.length ? `<div class="chart-tooltip-extra">${extra.join(' · ')}</div>` : ''}
        `;
    }
    if (indexes.predictedIndex.has(time)) {
        const i = indexes.predictedIndex.get(time);
        return `
            <div class="chart-tooltip-date">${formatTooltipDate(time)}</div>
            <div class="chart-tooltip-extra">Predicho <b class="num">${fmt(forecast.predicted[i].close)}</b></div>
        `;
    }
    return '';
}

// Anclado FIJO en la esquina superior izquierda (como el legend de
// TradingView), nunca siguiendo al cursor -- antes se posicionaba pegado al
// mouse y, por mas compacto que fuera, en paneles angostos terminaba
// tapando la vela justo debajo del cursor (ver DESIGN_SYSTEM.md). La
// posicion la resuelve el CSS (.chart-tooltip), acá solo se llena/vacia el
// contenido.
function attachTooltip(container, chart, forecast) {
    const tooltip = document.createElement('div');
    tooltip.className = 'chart-tooltip';
    container.appendChild(tooltip);
    const indexes = buildTimeIndexes(forecast);

    chart.subscribeCrosshairMove((param) => {
        if (!param.point || param.time === undefined || param.point.x < 0 || param.point.y < 0) {
            tooltip.classList.remove('visible');
            return;
        }
        const html = tooltipHtml(forecast, indexes, param.time);
        if (!html) {
            tooltip.classList.remove('visible');
            return;
        }
        tooltip.innerHTML = html;
        tooltip.classList.add('visible');
    });
}

// Interpolacion lineal desde 0 (en el ultimo cierre real) hasta pct (en el
// paso final) -- mismo criterio ya declarado y aceptado en
// renderForecastFanChart: distribution.p10_pct/p90_pct es un unico numero
// agregado para TODO el horizonte (no un cuantil por dia), asi que el ancho
// de la banda se interpola en vez de insinuar una precision dia a dia que el
// modelo no tiene.
function bandPath(predicted, lastClose, pct) {
    const steps = predicted.length;
    return predicted.map((p, i) => ({
        time: p.time,
        value: lastClose * (1 + (pct * (i + 1) / steps) / 100),
    }));
}

// Banda de incertidumbre p10-p90 alrededor de la prediccion -- lightweight-charts
// no trae un series type nativo para "area entre 2 curvas" (misma limitacion
// que documenta renderForecastFanChart), asi que se simula con la tecnica
// estandar de la comunidad: un AreaSeries con el p90 como borde superior,
// relleno con el color de acento hasta el fondo del panel, y ENCIMA (agregado
// despues, mayor z-order) un segundo AreaSeries con el p10 como borde,
// relleno del color REAL del panel (--bg-elevated, opaco) que "borra" todo
// por debajo de p10 -- lo que queda visible entre ambos bordes es la banda.
function drawForecastBand(chart, forecast, palette) {
    const dist = forecast.distribution;
    const lastClose = forecast.historical.length ? forecast.historical[forecast.historical.length - 1].close : null;
    if (!dist || lastClose == null || !forecast.predicted?.length) return null;

    const upper = chart.addSeries(LightweightCharts.AreaSeries, {
        lineColor: 'transparent', lineWidth: 1,
        topColor: withAlpha(palette.accent, 0.22), bottomColor: withAlpha(palette.accent, 0.22),
        lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false,
    });
    upper.setData(bandPath(forecast.predicted, lastClose, dist.p90_pct));

    const mask = chart.addSeries(LightweightCharts.AreaSeries, {
        lineColor: 'transparent', lineWidth: 1,
        topColor: palette.surface, bottomColor: palette.surface,
        lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false,
    });
    mask.setData(bandPath(forecast.predicted, lastClose, dist.p10_pct));

    return { upper, mask };
}

export function renderForecastChart(container, forecast, overlays = {}) {
    container.innerHTML = '';
    container.style.position = 'relative';

    const palette = getChartPalette();
    const chart = LightweightCharts.createChart(container, baseChartOptions(container, 360, palette));

    // Agregada primero a proposito: en lightweight-charts una serie agregada
    // despues se dibuja ENCIMA de las anteriores -- la banda tiene que quedar
    // debajo de velas/predicho/medias, nunca taparlas.
    const band = drawForecastBand(chart, forecast, palette);

    const candles = chart.addSeries(LightweightCharts.CandlestickSeries, candleColors(palette));
    candles.setData(forecast.historical);

    // Lineas de tendencia + SL/TP (Fase 8) -- se dibujan sobre la MISMA serie
    // de velas, nunca una serie aparte, para que compartan escala de precio.
    // Arrancan ocultas (ver DEFAULT_OFF/chartLegendHtml) -- opt-in desde la
    // leyenda, no todo prendido a la vez.
    const resistanceLines = (overlays.trendlines?.resistance || []).map((line) => drawTrendline(chart, line, palette.negative, false));
    const supportLines = (overlays.trendlines?.support || []).map((line) => drawTrendline(chart, line, palette.positive, false));
    // Se guarda la IPriceLine junto con el id/nivel real del alert (no solo
    // se dibuja y se olvida) -- wireDraggableAlerts la necesita para saber
    // qué línea hay bajo el mouse y qué alerta actualizar al soltar.
    const alertLines = (overlays.priceAlerts || []).filter((a) => a.activa).map((a) => ({
        id: a.id,
        tipo: a.tipo,
        nivel: a.nivel,
        priceLine: candles.createPriceLine({
            price: a.nivel,
            color: a.tipo === 'sl' ? palette.negative : palette.positive,
            lineWidth: 1,
            lineStyle: LightweightCharts.LineStyle.Dashed,
            axisLabelVisible: true,
            title: a.tipo === 'sl' ? 'Stop-loss' : 'Take-profit',
        }),
    }));

    // Solo las velas llevan price-line/etiqueta de ultimo valor; el resto
    // informa por color para que las etiquetas no se amontonen unas con otras.
    const seriesOptions = { lastValueVisible: false, priceLineVisible: false };

    const predicted = chart.addSeries(LightweightCharts.LineSeries, {
        ...seriesOptions, color: palette.accent, lineWidth: 2, lineStyle: LightweightCharts.LineStyle.Dashed,
    });
    predicted.setData(forecast.predicted.map((c) => ({ time: c.time, value: c.close })));

    const overlayColors = technicalOverlayColors(palette);
    const sma = chart.addSeries(LightweightCharts.LineSeries, { ...seriesOptions, color: overlayColors.sma, lineWidth: 1 });
    sma.setData(zipSeries(forecast.historical, forecast.indicators.sma_20));

    const ema = chart.addSeries(LightweightCharts.LineSeries, { ...seriesOptions, color: overlayColors.ema, lineWidth: 1 });
    ema.setData(zipSeries(forecast.historical, forecast.indicators.ema_20));

    const bbStyle = { ...seriesOptions, color: overlayColors.bollinger, lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dotted, visible: false };
    const bbUpper = chart.addSeries(LightweightCharts.LineSeries, bbStyle);
    bbUpper.setData(zipSeries(forecast.historical, forecast.indicators.bb_upper));
    const bbLower = chart.addSeries(LightweightCharts.LineSeries, bbStyle);
    bbLower.setData(zipSeries(forecast.historical, forecast.indicators.bb_lower));

    chart.timeScale().fitContent();
    attachTooltip(container, chart, forecast);
    observeChartResize(chart, container);

    bindChartTheme(container, (next) => {
        chart.applyOptions(themeChartOptions(next));
        candles.applyOptions(candleColors(next));
        predicted.applyOptions({ color: next.accent });
        const nextOverlayColors = technicalOverlayColors(next);
        sma.applyOptions({ color: nextOverlayColors.sma });
        ema.applyOptions({ color: nextOverlayColors.ema });
        bbUpper.applyOptions({ color: nextOverlayColors.bollinger });
        bbLower.applyOptions({ color: nextOverlayColors.bollinger });
        resistanceLines.forEach((s) => s.applyOptions({ color: next.negative }));
        supportLines.forEach((s) => s.applyOptions({ color: next.positive }));
        alertLines.forEach((a) => a.priceLine.applyOptions({ color: a.tipo === 'sl' ? next.negative : next.positive }));
        if (band) {
            band.upper.applyOptions({ topColor: withAlpha(next.accent, 0.22), bottomColor: withAlpha(next.accent, 0.22) });
            band.mask.applyOptions({ topColor: next.surface, bottomColor: next.surface });
        }
    });

    // Mapa de series para wireChartLegend -- las keys coinciden 1 a 1 con
    // chartLegendHtml. Guardado en el propio objeto del chart para no tener
    // que cambiar la firma de retorno en cada call site existente.
    chart._legendSeries = {
        sma, ema, bollinger: [bbUpper, bbLower], predicted, support: supportLines, resistance: resistanceLines,
        ...(band ? { banda: [band.upper, band.mask] } : {}),
    };
    // Para el modo "click para agregar alerta" (ver price-alerts.js::
    // wireAlertToolbar) -- necesita la serie de velas para convertir el pixel
    // clickeado a un precio real (coordinateToPrice).
    chart._candleSeries = candles;
    // Para arrastrar una alerta ya puesta (ver price-alerts.js::wireDraggableAlerts).
    chart._alertLines = alertLines;
    return chart;
}

export function renderRsiPane(container, forecast) {
    container.innerHTML = '';

    const palette = getChartPalette();
    const chart = LightweightCharts.createChart(container, {
        ...baseChartOptions(container, 120, palette),
        timeScale: { visible: false },
    });

    const rsi = chart.addSeries(LightweightCharts.LineSeries, { color: palette.accentStrong, lineWidth: 1 });
    rsi.setData(zipSeries(forecast.historical, forecast.indicators.rsi_14));
    // Eje fijo 0-100 (ver conversación/auditoría, hallazgo 4: "el eje del
    // RSI llega hasta 120 cuando el RSI va de 0 a 100") -- el
    // autoscaleInfoProvider por si solo solo fija el RANGO DE DATOS que usa
    // el autoscale; la priceScale igual le suma su scaleMargins default
    // (top/bottom, pensado para dejar aire arriba de las velas) ENCIMA de
    // ese rango antes de dibujar el eje -- por eso el 100 de arriba se
    // inflaba a ~120. Sin margen en ninguno de los 2 extremos, el eje queda
    // exactamente en 0-100.
    rsi.applyOptions({ autoscaleInfoProvider: () => ({ priceRange: { minValue: 0, maxValue: 100 } }) });
    rsi.priceScale().applyOptions({ scaleMargins: { top: 0, bottom: 0 } });

    chart.timeScale().fitContent();
    observeChartResize(chart, container);

    bindChartTheme(container, (next) => {
        chart.applyOptions(themeChartOptions(next));
        rsi.applyOptions({ color: next.accentStrong });
    });

    return chart;
}

export function renderMacdPane(container, forecast) {
    container.innerHTML = '';

    const palette = getChartPalette();
    const chart = LightweightCharts.createChart(container, {
        ...baseChartOptions(container, 120, palette),
        timeScale: { visible: false },
    });

    const histData = zipSeries(forecast.historical, forecast.indicators.macd_hist);
    const hist = chart.addSeries(LightweightCharts.HistogramSeries, { priceLineVisible: false, lastValueVisible: false });
    const histBars = (p) => histData.map((d) => ({ ...d, color: d.value >= 0 ? p.positive : p.negative }));
    hist.setData(histBars(palette));

    const macd = chart.addSeries(LightweightCharts.LineSeries, {
        color: palette.accentStrong, lineWidth: 1, lastValueVisible: false, priceLineVisible: false,
    });
    macd.setData(zipSeries(forecast.historical, forecast.indicators.macd));

    const signal = chart.addSeries(LightweightCharts.LineSeries, {
        color: palette.warning, lineWidth: 1, lastValueVisible: false, priceLineVisible: false,
    });
    signal.setData(zipSeries(forecast.historical, forecast.indicators.macd_signal));

    chart.timeScale().fitContent();
    observeChartResize(chart, container);

    bindChartTheme(container, (next) => {
        chart.applyOptions(themeChartOptions(next));
        hist.setData(histBars(next));
        macd.applyOptions({ color: next.accentStrong });
        signal.applyOptions({ color: next.warning });
    });

    return chart;
}

export function forecastFanLegendHtml() {
    const palette = getChartPalette();
    return `
        <div class="chart-legend">
            <span class="chart-legend-item"><span class="chart-legend-swatch" style="background:${palette.positive}"></span>Real</span>
            <span class="chart-legend-item"><span class="chart-legend-swatch" style="background:${palette.accent}"></span>Mediana pronosticada (abanico seleccionado)</span>
            <span class="chart-legend-item"><span class="chart-legend-swatch" style="background:${palette.text}"></span>Rango p10-p90</span>
        </div>`;
}

// Abanico de pronosticos pasados contra el precio real -- un pronostico solo
// trae una distribucion (p10/p90/mediana) para el CAMBIO TOTAL del horizonte,
// no un rango por dia, asi que el ancho del abanico se interpola linealmente
// desde 0 en la fecha de emision hasta ese p10/p90 en la fecha final. Se
// declara la aproximacion en vez de insinuar una precision dia a dia que el
// modelo no tiene. lightweight-charts no trae una serie de "banda rellena
// entre dos curvas" -- se dibujan los 2 bordes + la mediana como 3 lineas
// (mismo tipo de serie, LineSeries, que ya se usa en todo el resto del archivo)
// en vez de forzar un relleno con area series que no se comporta como banda.
export function renderForecastFanChart(container, priceHistory, forecasts, selectedId) {
    container.innerHTML = '';
    container.style.position = 'relative';
    const palette = getChartPalette();
    const chart = LightweightCharts.createChart(container, baseChartOptions(container, 340, palette));

    const real = chart.addSeries(LightweightCharts.LineSeries, {
        color: palette.positive, lineWidth: 2, lastValueVisible: false, priceLineVisible: false,
    });
    real.setData(priceHistory.map((row) => ({ time: row.time, value: row.close })));

    // band: las 2 lineas p10/p90 (siempre palette.text); median: la mediana
    // pronosticada (accent si es el abanico seleccionado, accentStrong si
    // no) -- guardadas para poder recolorear todo el abanico en bindChartTheme
    // sin tener que reconstruir las series (los datos no cambian con el tema).
    const bandLines = [];
    const medianLines = [];
    forecasts.forEach((f) => {
        if (!f.distribution || !f.predicted?.length) return;
        const selected = f.id === selectedId;
        const steps = f.predicted.length;
        const fanLine = (pct, opts) => {
            const series = chart.addSeries(LightweightCharts.LineSeries, {
                lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: selected, ...opts,
            });
            series.setData(f.predicted.map((p, i) => ({
                time: p.time,
                value: f.last_close * (1 + (pct * (i + 1) / steps) / 100),
            })));
            return series;
        };
        bandLines.push(fanLine(f.distribution.p90_pct, { color: palette.text, lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dotted }));
        bandLines.push(fanLine(f.distribution.p10_pct, { color: palette.text, lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dotted }));
        medianLines.push({
            series: fanLine(f.distribution.median_pct, {
                color: selected ? palette.accent : palette.accentStrong,
                lineWidth: selected ? 2 : 1,
                lineStyle: LightweightCharts.LineStyle.Dashed,
            }),
            selected,
        });
    });

    // fitContent() a secas dejaria el abanico (unos pocos dias, al final de la
    // serie) invisible contra años de historico real -- se arranca centrado en
    // el tramo donde hay pronosticos, con un margen de contexto antes.
    // Solo los que efectivamente se dibujan como abanico (con distribucion) --
    // un backtest viejo sin distribucion no debe forzar el zoom hacia atras.
    const overlaysWithPoints = forecasts.filter((f) => f.distribution && f.predicted?.length);
    const lastRealTime = priceHistory.length ? priceHistory[priceHistory.length - 1].time : null;
    if (overlaysWithPoints.length && lastRealTime) {
        const earliestOverlay = Math.min(...overlaysWithPoints.map((f) => f.predicted[0].time));
        // El "to" tiene que llegar hasta el final del pronostico mas largo
        // mostrado, no solo hasta el ultimo precio real -- un pronostico
        // pendiente predice mas alla del dato real disponible.
        const latestOverlayEnd = Math.max(...overlaysWithPoints.map((f) => f.predicted[f.predicted.length - 1].time));
        const from = Math.max(priceHistory[0].time, earliestOverlay - 60 * DAY_SECONDS);
        chart.timeScale().setVisibleRange({ from, to: Math.max(lastRealTime, latestOverlayEnd) });
    } else {
        chart.timeScale().fitContent();
    }
    observeChartResize(chart, container);

    bindChartTheme(container, (next) => {
        chart.applyOptions(themeChartOptions(next));
        real.applyOptions({ color: next.positive });
        bandLines.forEach((s) => s.applyOptions({ color: next.text }));
        medianLines.forEach(({ series, selected }) => series.applyOptions({ color: selected ? next.accent : next.accentStrong }));
    });

    return chart;
}
