// `signed`: antepone "+" a valores >= 0 (ej. variaciones/ganancias, "+4,27%")
// -- antes cada call site armaba esto a mano con `valor >= 0 ? '+' : ''`
// pegado adelante de fmt/fmtPct/fmtMoney, lo que en varios lugares terminaba
// concatenando ese "+" con un string que, si el valor era negativo, YA traía
// su propio "-" puesto por toLocaleString -- de ahí bugs tipo "+-4,27%". Con
// el signo resuelto ADENTRO del helper (en base al mismo valor que se
// formatea, nunca una variable aparte) ese doble signo deja de ser posible.
export function fmt(n, decimals = 2, { signed = false } = {}) {
    if (n === null || n === undefined || Number.isNaN(n)) return '—';
    const sign = signed && n >= 0 ? '+' : '';
    return sign + Number(n).toLocaleString('es-AR', { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}

export function fmtPct(n, decimals = 2, opts) {
    return n === null || n === undefined ? '—' : `${fmt(n, decimals, opts)}%`;
}

export function fmtMoney(n, currency = 'ARS', decimals = 2, { signed = false } = {}) {
    if (n === null || n === undefined || Number.isNaN(n)) return '—';
    const prefix = currency === 'USD' ? 'USD ' : '$';
    // El signo va antes del prefijo (-$1.234,56), no en el medio ($-1.234,56)
    // -- toLocaleString ya mete el signo pegado al primer dígito.
    const sign = n < 0 ? '-' : (signed ? '+' : '');
    return `${sign}${prefix}${fmt(Math.abs(n), decimals)}`;
}

// "Hoy" en fecha LOCAL del navegador (YYYY-MM-DD) -- a proposito NO usa
// `new Date().toISOString()` ni `input.valueAsDate = new Date()`: ambos
// extraen el dia/mes/año en UTC, no en la zona horaria local, asi que
// despues de las 21hs ART (UTC-3) ya quedaba un dia adelantado (mismo bug
// que se corrigio del lado servidor en portfolio/tz.py).
export function todayLocalISO(d = new Date()) {
    const mm = String(d.getMonth() + 1).padStart(2, '0');
    const dd = String(d.getDate()).padStart(2, '0');
    return `${d.getFullYear()}-${mm}-${dd}`;
}

export function fmtDate(iso) {
    if (!iso) return '—';
    const d = new Date(`${iso}T00:00:00`);
    if (Number.isNaN(d.getTime())) return iso;
    const dd = String(d.getDate()).padStart(2, '0');
    const mm = String(d.getMonth() + 1).padStart(2, '0');
    return `${dd}-${mm}-${d.getFullYear()}`;
}
