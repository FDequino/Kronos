export function observeChartResize(chart, container) {
    const resizeObserver = new ResizeObserver((entries) => {
        const width = entries[0]?.contentRect?.width;
        if (width) chart.applyOptions({ width });
    });
    resizeObserver.observe(container);
    return () => resizeObserver.disconnect();
}
