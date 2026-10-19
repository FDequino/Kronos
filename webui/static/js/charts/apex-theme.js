import { getChartPalette } from '../shared/chart-palette.js';

export function isLightTheme() {
    return document.documentElement.dataset.theme === 'light';
}

// Antes esto era un mapa DARK/LIGHT hardcodeado en hex, una cuarta copia de
// los mismos valores que ya viven en tokens.css/theme-light.css (ver
// DESIGN_SYSTEM.md) -- ahora delega en chart-palette.js, que los lee en vivo
// via getComputedStyle. Se mantiene la forma {grid, text, textStrong, accent,
// accentAlt, positive, negative, warning, surface} para no tocar a los 4
// callers existentes (pnl-bar-chart.js, cash-flow-chart.js,
// allocation-donut.js, patrimonio.js).
export function currentPalette() {
    const p = getChartPalette();
    return { ...p, accentAlt: p.accentStrong };
}

export function baseApexOptions() {
    const p = currentPalette();
    return {
        chart: {
            background: 'transparent',
            fontFamily: "'IBM Plex Sans', -apple-system, sans-serif",
            foreColor: p.text,
            toolbar: { show: false },
            animations: { speed: 300 },
        },
        theme: { mode: isLightTheme() ? 'light' : 'dark' },
        grid: {
            borderColor: p.grid,
            strokeDashArray: 0,
            xaxis: { lines: { show: false } },
            yaxis: { lines: { show: true } },
            padding: { left: 8, right: 8 },
        },
        tooltip: { theme: isLightTheme() ? 'light' : 'dark' },
        legend: {
            labels: { colors: p.text },
            fontSize: '12.5px',
            fontWeight: 500,
            markers: { shape: 'circle', size: 5, offsetX: -2 },
            itemMargin: { horizontal: 10, vertical: 4 },
        },
        dataLabels: { enabled: false },
        states: {
            hover: { filter: { type: 'darken', value: 0.92 } },
            active: { filter: { type: 'darken', value: 0.85 } },
        },
    };
}
