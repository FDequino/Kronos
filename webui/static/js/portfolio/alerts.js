import { fmt } from '../shared/format.js';
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
            kind: 'vencimiento',
            text: `Caución por ${fmt(r.caucion.monto_contado)} vence en ${r.dias} día${r.dias === 1 ? '' : 's'}.`,
        }));
}

function dividendoAlerts(rentas) {
    return rentas
        .map((r) => ({ ...r, dias: daysUntil(r.fecha) }))
        .filter((r) => r.dias >= -7 && r.dias <= 0)
        .sort((a, b) => b.dias - a.dias)
        .map((r) => ({
            kind: 'cobro',
            symbol: r.symbol,
            text: `Cobraste ${TIPO_RENTA_LABEL[r.tipo] || r.tipo} de ${r.symbol} por ${fmt(r.neto)} ${r.moneda} el ${r.fecha}.`,
        }));
}

async function ventaAlerts(holdings) {
    const results = await Promise.all(holdings.map((h) =>
        api.latestForecast({ instrument_id: h.instrument.id }).catch(() => ({ data: null }))
    ));
    return results
        .map((res, i) => ({ holding: holdings[i], forecast: res.data }))
        .filter(({ forecast }) => forecast?.status === 'done' && forecast.result.signal === 'vender')
        .map(({ holding }) => ({
            kind: 'senal',
            symbol: holding.instrument.symbol,
            text: `${holding.instrument.symbol} tiene señal de venta activa.`,
        }));
}

export async function computeAlerts() {
    const [{ data: holdings }, { data: plazos }, { data: cauciones }, { data: rentas }] = await Promise.all([
        api.holdings(), api.plazosFijos(), api.cauciones(), api.rentasDividendos(),
    ]);
    const ventas = await ventaAlerts(holdings);
    return [
        ...plazoFijoAlerts(plazos),
        ...caucionAlerts(cauciones),
        ...dividendoAlerts(rentas),
        ...ventas,
    ];
}
