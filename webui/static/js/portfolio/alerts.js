import { fmt, fmtDate } from '../shared/format.js';
import { api } from './api.js';

const DAY_MS = 86400000;
const TIPO_RENTA_LABEL = { dividendo: 'dividendo', renta: 'renta', amortizacion: 'amortización' };

function daysUntil(dateStr) {
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const target = new Date(`${dateStr}T00:00:00`);
    return Math.round((target - today) / DAY_MS);
}

function plazoFijoAlerts(resumen) {
    return resumen
        .filter((r) => !r.vencido)
        .map((r) => ({ ...r, dias: daysUntil(r.plazo_fijo.fecha_vencimiento) }))
        .filter((r) => r.dias <= 7)
        .sort((a, b) => a.dias - b.dias)
        .map((r) => ({
            id: `pf:${r.plazo_fijo.id}`,
            kind: 'vencimiento',
            text: `Plazo fijo en ${r.plazo_fijo.banco} por ${fmt(r.plazo_fijo.monto)} vence en ${r.dias} día${r.dias === 1 ? '' : 's'}.`,
        }));
}

function caucionAlerts(resumen) {
    return resumen
        .filter((r) => !r.vencido)
        .map((r) => ({ ...r, dias: daysUntil(r.caucion.fecha_vencimiento) }))
        .filter((r) => r.dias <= 7)
        .sort((a, b) => a.dias - b.dias)
        .map((r) => ({
            id: `caucion:${r.caucion.id}`,
            kind: 'vencimiento',
            text: `Caución por ${fmt(r.caucion.monto_contado)} vence en ${r.dias} día${r.dias === 1 ? '' : 's'}.`,
        }));
}

// href a #prestamos (ver conversación, Fase 7) -- no hay una URL propia por
// préstamo individual ("Ver detalle" abre un modal, no una ruta), así que el
// mejor destino disponible es la vista que lista todos. cuotas_atrasadas
// (ver portfolio/loans.py::compute_loan_status) se menciona en el texto
// cuando hay más de una impaga -- antes esto se reportaba igual con 1 o con
// varias cuotas vencidas del mismo préstamo.
function prestamoAlerts(loans) {
    const vigentes = loans.filter((l) => l.estado === 'vigente');
    const atrasados = vigentes
        .filter((l) => l.vencido_hace_dias != null)
        .map((l) => {
            const cuotasTxt = l.cuotas_atrasadas > 1 ? ` (${l.cuotas_atrasadas} cuotas impagas)` : '';
            return {
                id: `prestamo-mora:${l.id}`,
                kind: 'vencido',
                href: '#prestamos',
                text: `Préstamo ${l.direccion === 'recibido' ? 'de' : 'a'} ${l.deudor} atrasado hace ${l.vencido_hace_dias} día${l.vencido_hace_dias === 1 ? '' : 's'}${cuotasTxt}.`,
            };
        });
    const proximos = vigentes
        .filter((l) => l.vencido_hace_dias == null && l.proximo_vencimiento)
        .map((l) => ({ ...l, dias: daysUntil(l.proximo_vencimiento) }))
        .filter((l) => l.dias <= 7)
        .sort((a, b) => a.dias - b.dias)
        .map((l) => ({
            id: `prestamo:${l.id}`,
            kind: 'vencimiento',
            href: '#prestamos',
            text: `Cuota de préstamo ${l.direccion === 'recibido' ? 'de' : 'a'} ${l.deudor} por ${fmt(l.proximo_monto)} ${l.moneda} vence en ${l.dias} día${l.dias === 1 ? '' : 's'}.`,
        }));
    return [...atrasados, ...proximos];
}

const EVENT_KIND_LABEL = { vencimiento: 'Vencimiento', cupon: 'Cupón' };

function bonoAlerts(events) {
    return events
        .filter((e) => e.dias <= 7)
        .map((e) => ({
            id: `bono:${e.symbol}:${e.kind}:${e.date}`,
            kind: 'vencimiento',
            symbol: e.symbol,
            text: `${EVENT_KIND_LABEL[e.kind] || e.kind} de ${e.symbol} en ${e.dias} día${e.dias === 1 ? '' : 's'} (${e.date}).`,
        }));
}

function dividendoAlerts(rentas) {
    return rentas
        .map((r) => ({ ...r, dias: daysUntil(r.fecha) }))
        .filter((r) => r.dias >= -7 && r.dias <= 0)
        .sort((a, b) => b.dias - a.dias)
        .map((r) => ({
            id: `renta:${r.id}`,
            kind: 'cobro',
            symbol: r.symbol,
            text: `Cobraste ${TIPO_RENTA_LABEL[r.tipo] || r.tipo} de ${r.symbol} por ${fmt(r.neto)} ${r.moneda} el ${fmtDate(r.fecha)}.`,
        }));
}

// Fase 1 (ver conversación): ANTES pedía 1 /forecasts/latest POR TENENCIA en
// paralelo (Promise.all) -- con una cartera de ~25 tenencias eso saturaba el
// pool de conexiones por-origen del browser y frenaba el resto de la página
// (computeAlerts corre en CUALQUIER vista, no solo Resumen, porque la llama
// la campana de notificaciones global). Ahora es 1 sola request batch (ver
// portfolio/routes.py::latest_forecast_batch), misma lógica exacta del lado
// del backend, cero cambio de criterio de qué cuenta como "señal de venta".
async function ventaAlerts(holdings) {
    if (!holdings.length) return [];
    const instrumentIds = holdings.map((h) => h.instrument.id);
    let forecastsById = {};
    try {
        ({ data: forecastsById } = await api.latestForecastBatch(instrumentIds));
    } catch {
        return []; // el batch no respondió -- degrada a "sin alertas de señal" en vez de romper el resto de las notificaciones
    }
    return holdings
        .map((holding) => ({ holding, forecast: forecastsById[String(holding.instrument.id)] }))
        .filter(({ forecast }) => forecast?.status === 'done' && forecast.result.signal === 'vender'
            && forecast.validation?.validated)
        .map(({ holding }) => ({
            id: `senal:${holding.instrument.id}`,
            kind: 'senal',
            symbol: holding.instrument.symbol,
            text: `${holding.instrument.symbol} tiene señal de venta activa.`,
        }));
}

// SL/TP ya tocado (Fase 8): se reusa la propuesta 'sl_tp' que ya calculó el
// backend contra el precio NATIVO del instrumento (ver proposals.py) en vez
// de recalcular la comparación acá con el precio de /holdings -- ese endpoint
// devuelve el precio YA CONVERTIDO a la moneda de exhibición elegida en
// Preferencias, así que compararlo contra un nivel cargado en moneda nativa
// daría un resultado falso (mismo tipo de bug que ya se detectó en Fase 7).
function sltpAlerts(proposalsNuevas) {
    return proposalsNuevas
        .filter((p) => p.origen === 'sl_tp')
        .map((p) => ({ id: `sltp:${p.id}`, kind: 'sltp', symbol: p.symbol, text: p.texto }));
}

export async function computeAlerts() {
    const [{ data: holdings }, { data: plazos }, { data: cauciones }, { data: rentas }, { data: eventosRentaFija }, { data: proposalsNuevas }, { data: loans }] = await Promise.all([
        api.holdings(), api.plazosFijos(), api.cauciones(), api.rentasDividendos(), api.fixedIncomeCalendar(),
        api.proposals('nueva').catch(() => ({ data: [] })), api.loans(),
    ]);
    const ventas = await ventaAlerts(holdings);
    return [
        ...plazoFijoAlerts(plazos),
        ...caucionAlerts(cauciones),
        ...dividendoAlerts(rentas),
        ...bonoAlerts(eventosRentaFija),
        ...ventas,
        ...sltpAlerts(proposalsNuevas),
        ...prestamoAlerts(loans),
    ];
}
