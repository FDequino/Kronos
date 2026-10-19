export function observeChartResize(chart, container) {
    // Un mismo container puede recibir un segundo grafico mas adelante (ej.
    // el grafico tecnico rapido que despues se reemplaza por el resultado de
    // Kronos) -- si no se desconecta el observer viejo, sigue vivo mirando un
    // chart ya removido y revienta con "Object is disposed" al redimensionar.
    container._resizeCleanup?.();

    const resizeObserver = new ResizeObserver((entries) => {
        const width = entries[0]?.contentRect?.width;
        if (width) {
            try {
                chart.applyOptions({ width });
            } catch {
                resizeObserver.disconnect();
            }
        }
    });
    resizeObserver.observe(container);

    const cleanup = () => resizeObserver.disconnect();
    container._resizeCleanup = cleanup;

    // La rueda del mouse zoomeaba el grafico en vez de scrollear la pagina
    // (reportado en el plan de Fase 3/4.5) -- se desactiva el zoom nativo por
    // rueda y se reimplementa a mano, gateado a Ctrl/Cmd+rueda, para que la
    // rueda sola siga scrolleando la pagina como en cualquier otro elemento.
    chart.applyOptions({ handleScale: { mouseWheel: false } });
    const onWheel = (e) => {
        if (!e.ctrlKey && !e.metaKey) return;
        e.preventDefault();
        const ts = chart.timeScale();
        const range = ts.getVisibleLogicalRange();
        if (!range) return;
        const center = (range.from + range.to) / 2;
        const factor = e.deltaY > 0 ? 1.1 : 0.9;
        const halfWidth = ((range.to - range.from) / 2) * factor;
        ts.setVisibleLogicalRange({ from: center - halfWidth, to: center + halfWidth });
    };
    container.addEventListener('wheel', onWheel, { passive: false });
    container._wheelCleanup?.();
    container._wheelCleanup = () => container.removeEventListener('wheel', onWheel);

    return cleanup;
}

// Paneles multi-chart (precio + RSI + MACD): cada uno es una instancia de
// lightweight-charts separada, asi que sin esto ni el paneo/zoom ni el rango
// visible inicial quedan sincronizados entre si -- se ven "descentrados"
// horizontalmente entre paneles aunque compartan el mismo eje de tiempo.
export function syncTimeScales(charts) {
    let syncing = false;
    const timeScales = charts.map((c) => c.timeScale());
    timeScales.forEach((ts, i) => {
        ts.subscribeVisibleLogicalRangeChange((range) => {
            if (syncing || !range) return;
            syncing = true;
            timeScales.forEach((other, j) => {
                if (j !== i) other.setVisibleLogicalRange(range);
            });
            syncing = false;
        });
    });
}
