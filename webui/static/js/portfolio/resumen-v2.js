// Fase 16: este archivo era el Dashboard de la subtab Resumen -- se eliminó
// (duplicaba hero/evolución/riesgo/ganancia con Patrimonio, y lo único que
// no duplicaba se repartió a su lugar natural: Peso%/P&L USD -> Señales,
// Exposición por moneda + edición de objetivo -> tile Activos de Patrimonio,
// Real vs. Teórico Kronos -> Decisiones > Kronos > Cartera, "Para decidir
// esta semana" se eliminó por ser un resumen condensado de cosas que ya
// viven completas en otro lado). Queda solo `buildAllocationSegments`,
// importada por patrimonio.js para armar el treemap.
const TYPE_LABELS = {
    accion: 'Acciones', cedear: 'CEDEARs', bono: 'Bonos',
    on: 'Obligaciones Negociables', fondo: 'Fondos', moneda: 'Moneda',
    caucion: 'Cauciones', caucion_vencida: 'Cauciones vencidas', prestamo: 'Préstamos',
    cripto: 'Cripto',
};

// Para montos en moneda NATIVA (cauciones/préstamos) que hay que mezclar con
// holdings (ya en ARS) -- a diferencia de la versión vieja (que leía un
// `state.usdMepRate` de módulo), esta recibe la tasa como parámetro: función
// pura, sin estado oculto. Sin tasa disponible, se degrada a null en vez de
// inventar un valor (nunca mezclar ARS/USD crudo sin convertir -- bug real ya
// corregido una vez en risk.py::compute_portfolio_risk_metrics).
function nativeToDisplay(amount, currency, usdRate) {
    if (amount === null || amount === undefined) return null;
    if (currency === 'ARS') return amount;
    return usdRate ? amount * usdRate : null;
}

// Asume holdings ya normalizados a ARS por el caller (ver patrimonio.js::
// renderPatrimonio, que los convierte UNA sola vez antes de llamar a esta
// función y a buildCuentaSegments/claseDrillItems -- el bundle aplica
// settings.display_currency server-side, así que h.valor_actual NO siempre
// llega en ARS; normalizarlo en un solo lugar evita el bug real que esto
// tenía: con Preferencias en USD, los holdings quedaban en USD crudo
// sumados junto a cauciones/préstamos ya convertidos a ARS, distorsionando
// por completo la composición del treemap).
export function buildAllocationSegments(holdings, cauciones = [], loans = [], usdRate = null) {
    const byType = new Map();
    holdings.forEach((h) => {
        const type = h.instrument.type;
        byType.set(type, (byType.get(type) || 0) + (h.valor_actual || 0));
    });
    // Solo vigentes: una vencida ya no es capital en caución -- esa plata se
    // convirtió en otra cosa (retiro, reinversión, una nueva caución), y el
    // broker real tampoco la cuenta como posición una vez vencida (confirmado
    // contra el export real de la cuenta en datos_broker/ -- ver nota en
    // portfolio/routes.py::portfolio_risk).
    const caucionesActivas = cauciones
        .filter((c) => !c.vencido)
        .reduce((sum, c) => sum + (nativeToDisplay(c.valor_actual, c.caucion.moneda, usdRate) || 0), 0);
    if (caucionesActivas > 0) byType.set('caucion', caucionesActivas);
    // Solo 'prestado' (activo) -- 'recibido' es una deuda propia, no una clase
    // de activo para repartir en el treemap (mismo criterio que risk.py).
    const prestamosVigentes = loans
        .filter((l) => l.estado === 'vigente' && l.direccion === 'prestado')
        .reduce((sum, l) => sum + (nativeToDisplay(l.capital_pendiente, l.moneda, usdRate) || 0), 0);
    if (prestamosVigentes > 0) byType.set('prestamo', prestamosVigentes);
    return [...byType.entries()]
        .filter(([, value]) => value > 0)
        .map(([type, value]) => ({ type, label: TYPE_LABELS[type] || type, value }));
}
