// Taxonomia "fina" de clase de activo, compartida entre el Dashboard
// (Posiciones), Instrumentos y Tenencias -- antes cada pantalla mostraba el
// tipo crudo ("BONO") sin distinguir LECAP / Bono CER / Bono tasa fija, una
// inconsistencia real entre pantallas (ver plan de Fase 3).
export function instrumentClase(instrument) {
    if (instrument.type === 'bono' && instrument.rate_type === 'cer') return 'Bono CER';
    if (instrument.type === 'bono' && !instrument.coupon_rate) return 'LECAP';
    if (instrument.type === 'bono') return 'Bono tasa fija';
    if (instrument.type === 'on') return 'ON';
    if (instrument.type === 'cedear') return 'CEDEAR';
    if (instrument.type === 'accion') return 'Acción';
    if (instrument.type === 'fondo') return 'Fondo';
    if (instrument.type === 'moneda') return 'Moneda';
    if (instrument.type === 'cripto') return 'Cripto';
    return instrument.type;
}

// Un solo acento (azul) para lo que cotiza en mercado, gris para
// efectivo/cuotapartes -- no un color distinto por clase, para no romper la
// regla de "color solo para P&L y clases amplias" del resto del rediseño.
// Cripto cotiza en mercado (Binance, en vivo) -- mismo acento que acciones/
// cedears/bonos/on, no el gris de "efectivo/cuotapartes".
const MARKET_TYPES = new Set(['accion', 'cedear', 'bono', 'on', 'cripto']);

export function claseDotHtml(instrument) {
    const color = MARKET_TYPES.has(instrument.type) ? 'var(--accent)' : 'var(--text-tertiary)';
    return `<span class="clase-dot" style="background:${color}"></span>`;
}
