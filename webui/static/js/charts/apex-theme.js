const DARK = {
    grid: '#242933', text: '#8a93a3', textStrong: '#e7e9ee',
    accent: '#4c7dff', accentAlt: '#759bff',
    positive: '#30c177', negative: '#ef5b5b', warning: '#e0a63c',
    surface: '#1a1e27',
};
const LIGHT = {
    grid: '#e3e5ea', text: '#585f6b', textStrong: '#14161a',
    accent: '#3b6fe0', accentAlt: '#2851b8',
    positive: '#1f9d57', negative: '#d63939', warning: '#b5790a',
    surface: '#ffffff',
};

export function isLightTheme() {
    return document.documentElement.dataset.theme === 'light';
}

export function currentPalette() {
    return isLightTheme() ? LIGHT : DARK;
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
