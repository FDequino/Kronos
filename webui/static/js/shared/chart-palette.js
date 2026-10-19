// Unica fuente de verdad para los colores de los charts (Lightweight Charts +
// ApexCharts). Antes cada archivo de chart (candlestick-chart.js,
// portfolio-history-chart.js, benchmark-comparison-chart.js, apex-theme.js)
// tenia su propio PALETTE/DARK/LIGHT hardcodeado en hex, copiado a mano de
// tokens.css/theme-light.css -- cuatro copias independientes que no se
// enteraban si tokens.css cambiaba, y que ademas quedaban CONGELADAS en el
// valor del tema activo al momento del import (nunca el tema actual), asi
// que cambiar a modo claro con un grafico ya pintado lo dejaba con grid/texto
// de modo oscuro sobre un panel ahora claro (ver DESIGN_SYSTEM.md, hallazgos
// 1 y 2).
//
// Este modulo no reimplementa el mapeo oscuro/claro en JS -- lee las custom
// properties ya resueltas por el navegador para el <html data-theme="..."> en
// este instante (ver shared/theme.js), asi que tokens.css/theme-light.css
// siguen siendo la UNICA copia real de cada color.
const TOKEN_MAP = {
    grid: '--border',
    text: '--text-secondary',
    textStrong: '--text-primary',
    accent: '--accent',
    accentStrong: '--accent-strong',
    positive: '--positive',
    negative: '--negative',
    warning: '--warning',
    surface: '--bg-elevated',
};

export function getChartPalette() {
    const styles = getComputedStyle(document.documentElement);
    const palette = {};
    for (const [key, cssVar] of Object.entries(TOKEN_MAP)) {
        palette[key] = styles.getPropertyValue(cssVar).trim();
    }
    return palette;
}

// Suscribe callback a cada cambio de tema (shared/theme.js::initThemeToggle
// dispara 'theme-changed' en document DESPUES de aplicar el nuevo
// [data-theme], asi que getChartPalette() ya lee los valores nuevos). El
// callback recibe la paleta recalculada, nunca el nombre del tema, para que
// el caller no tenga que volver a leer CSS el mismo. Devuelve una funcion de
// cleanup.
export function onThemeChange(callback) {
    const handler = () => callback(getChartPalette());
    document.addEventListener('theme-changed', handler);
    return () => document.removeEventListener('theme-changed', handler);
}

// Atajo para el caso comun: un chart ya creado sobre `container` que solo
// necesita reaplicar colores (no volver a pedir datos) cuando cambia el tema.
// container._themeCleanup?.() antes de suscribir de nuevo es el mismo patron
// que ya usa observeChartResize en charts/lw-chart-utils.js para no acumular
// listeners cuando un chart se vuelve a crear sobre el mismo elemento
// (cambio de instrumento, de periodo, etc.).
export function bindChartTheme(container, applyPalette) {
    container._themeCleanup?.();
    container._themeCleanup = onThemeChange(applyPalette);
}

// getComputedStyle siempre devuelve los colores de tokens.css como hex (no
// como rgb()), asi que una banda semitransparente (ver renderForecastChart)
// necesita convertir a mano -- no hay otra forma de pedirle alpha a un hex.
export function withAlpha(hex, alpha) {
    const clean = hex.replace('#', '');
    const full = clean.length === 3 ? clean.split('').map((c) => c + c).join('') : clean;
    const value = parseInt(full, 16);
    const r = (value >> 16) & 255;
    const g = (value >> 8) & 255;
    const b = value & 255;
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}
