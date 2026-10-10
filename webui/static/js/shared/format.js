export function fmt(n, decimals = 2) {
    if (n === null || n === undefined || Number.isNaN(n)) return '—';
    return Number(n).toLocaleString('es-AR', { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}

export function fmtPct(n, decimals = 2) {
    return n === null || n === undefined ? '—' : `${fmt(n, decimals)}%`;
}

export function fmtMoney(n, currency = 'ARS', decimals = 2) {
    if (n === null || n === undefined || Number.isNaN(n)) return '—';
    const prefix = currency === 'USD' ? 'USD ' : '$';
    return `${prefix}${fmt(n, decimals)}`;
}
