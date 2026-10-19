// Mismo horario que portfolio/quote_worker.py::is_market_open -- 11:00-17:00
// ART, lunes a viernes (rueda de BYMA). Duplicado deliberado del lado
// cliente (backend y frontend no comparten runtime), centralizado aca en vez
// de repetido en cada vista que necesita saber si el mercado esta abierto.
const ART_TZ = 'America/Argentina/Buenos_Aires';
const MARKET_OPEN_HOUR = 11;
const MARKET_CLOSE_HOUR = 17;
const WEEKDAY_INDEX = { Sun: 0, Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6 };

function artParts(date) {
    const parts = new Intl.DateTimeFormat('en-US', {
        timeZone: ART_TZ, hour: 'numeric', hour12: false, weekday: 'short',
    }).formatToParts(date);
    const rawHour = Number(parts.find((p) => p.type === 'hour').value);
    return {
        hour: rawHour === 24 ? 0 : rawHour, // algunos motores devuelven "24" para medianoche
        weekday: WEEKDAY_INDEX[parts.find((p) => p.type === 'weekday').value],
    };
}

export function isMarketOpen(date = new Date()) {
    const { hour, weekday } = artParts(date);
    return weekday >= 1 && weekday <= 5 && hour >= MARKET_OPEN_HOUR && hour < MARKET_CLOSE_HOUR;
}

// Etiqueta textual, no un timestamp exacto -- alcanza para el indicador de
// frescura ("próxima actualización ...") sin tener que resolver aritmetica
// de fechas cruzando husos horarios para un caso de uso puramente informativo.
export function nextMarketOpenLabel(date = new Date()) {
    const { hour, weekday } = artParts(date);
    if (weekday >= 1 && weekday <= 5 && hour < MARKET_OPEN_HOUR) return 'hoy a las 11:00';
    if (weekday === 5 && hour >= MARKET_CLOSE_HOUR) return 'el lunes a las 11:00';
    if (weekday === 6 || weekday === 0) return 'el lunes a las 11:00';
    return 'mañana a las 11:00';
}
