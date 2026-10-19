import { $ } from '../shared/dom.js';
import { showStatus } from '../shared/status.js';
import { api } from './api.js';
import { openModal } from '../shared/modal.js';

// Catálogo unificado (ver conversación, limpieza de UI): este archivo dejó
// de tener vista propia -- Catálogo y Explorador BYMA se fusionaron en una
// sola tabla con filtro superior (ver webui/static/js/portfolio/
// instrumentos.js). Lo que queda acá es el núcleo reusable: la lista cruda
// de instrumentos y el modal de alta/edición, consumidos por esa vista y por
// cualquier otra pantalla que necesite elegir/crear un instrumento
// (Transacciones, Comparador, Alertas de precio, etc.).
let currentInstruments = [];
let currentBrokerAccounts = [];

export async function fetchInstrumentsList() {
    const { data } = await api.instruments();
    return data;
}

// Precio de EJECUCIÓN (ver conversación: "que tome los precios de la
// plataforma que usa" -- si opera en PPI, el de PPI; si en IOL, el de
// IOL). Solo cuentas ACTIVAS -- una desactivada no debería ser la fuente
// de precio de nada.
function brokerAccountOptionsHtml(selectedId) {
    const activas = currentBrokerAccounts.filter((a) => a.is_active);
    return `
        <option value="">Automático (como hoy)</option>
        ${activas.map((a) => `
            <option value="${a.id}" ${selectedId === a.id ? 'selected' : ''}>${a.alias || a.broker_name} (${a.broker_name})</option>
        `).join('')}
    `;
}

const CUENTA_DEFAULT = 'NH Capital';

// Antes era un <input type="text"> a mano -- cualquier typo ("NH capital" vs
// "NH Capital") partía en dos la misma cuenta en todos los agrupamientos por
// cuenta (Patrimonio -> Por cuenta, etc.). Ahora se elige entre las que ya
// existen en el catálogo; "+ Nueva cuenta" es la única puerta para un nombre
// nuevo, y ese nombre se escribe una sola vez.
function cuentaOptionsHtml(selected) {
    const existentes = [...new Set(currentInstruments.map((i) => i.cuenta || CUENTA_DEFAULT))].sort();
    if (!existentes.includes(CUENTA_DEFAULT)) existentes.unshift(CUENTA_DEFAULT);
    const value = selected || CUENTA_DEFAULT;
    return `
        ${existentes.map((c) => `<option value="${c}" ${c === value ? 'selected' : ''}>${c}</option>`).join('')}
        <option value="__nueva__">+ Nueva cuenta...</option>
    `;
}

function formFieldsHtml(inst = {}) {
    const isEdit = !!inst.id;
    return `
        <div class="field-row">
            <div class="field"><label for="inst-symbol">S&iacute;mbolo</label><input type="text" id="inst-symbol" placeholder="Ej: GGAL" value="${inst.symbol || ''}"></div>
            <div class="field"><label for="inst-name">Nombre</label><input type="text" id="inst-name" placeholder="Ej: Grupo Financiero Galicia" value="${inst.name || ''}"></div>
            <div class="field">
                <label for="inst-type">Tipo</label>
                <select id="inst-type">
                    <option value="accion" ${inst.type === 'accion' ? 'selected' : ''}>Acci&oacute;n</option>
                    <option value="cedear" ${inst.type === 'cedear' ? 'selected' : ''}>CEDEAR</option>
                    <option value="bono" ${inst.type === 'bono' ? 'selected' : ''}>Bono</option>
                    <option value="on" ${inst.type === 'on' ? 'selected' : ''}>Obligaci&oacute;n Negociable</option>
                    <option value="moneda" ${inst.type === 'moneda' ? 'selected' : ''}>Moneda</option>
                    <option value="fondo" ${inst.type === 'fondo' ? 'selected' : ''}>Fondo</option>
                    <option value="cripto" ${inst.type === 'cripto' ? 'selected' : ''}>Cripto</option>
                </select>
            </div>
            <div class="field"><label for="inst-market">Mercado</label><input type="text" id="inst-market" placeholder="Ej: byma" value="${inst.market || ''}"></div>
            <div class="field">
                <label for="inst-cuenta">Cuenta/Broker</label>
                <select id="inst-cuenta">${cuentaOptionsHtml(inst.cuenta)}</select>
            </div>
            <div class="field" id="cuenta-nueva-field" style="display:none;">
                <label for="inst-cuenta-nueva">Nombre de la cuenta nueva</label>
                <input type="text" id="inst-cuenta-nueva" placeholder="Ej: Balanz">
            </div>
            <div class="field">
                <label for="inst-currency">Moneda</label>
                <select id="inst-currency">
                    <option value="ARS" ${inst.currency === 'ARS' ? 'selected' : ''}>ARS</option>
                    <option value="USD" ${inst.currency === 'USD' ? 'selected' : ''}>USD</option>
                    <option value="USDT" ${inst.currency === 'USDT' ? 'selected' : ''}>USDT</option>
                </select>
            </div>
            <div class="field">
                <label for="inst-source">Fuente de precio</label>
                <select id="inst-source">
                    <option value="data912" ${inst.price_source === 'data912' ? 'selected' : ''}>data912 (BYMA)</option>
                    <option value="dolarapi" ${inst.price_source === 'dolarapi' ? 'selected' : ''}>dolarapi (USD)</option>
                    <option value="binance" ${inst.price_source === 'binance' ? 'selected' : ''}>binance (cripto)</option>
                    <option value="manual" ${inst.price_source === 'manual' ? 'selected' : ''}>manual</option>
                </select>
            </div>
            <div class="field" id="dolarapi-fields" style="display: none;">
                <label for="inst-casa">Casa (dolarapi)</label>
                <select id="inst-casa">
                    <option value="oficial">Oficial</option>
                    <option value="blue">Blue</option>
                    <option value="bolsa">MEP (bolsa)</option>
                    <option value="contadoconliqui">CCL</option>
                    <option value="mayorista">Mayorista</option>
                    <option value="cripto">Cripto</option>
                    <option value="tarjeta">Tarjeta</option>
                </select>
            </div>
            <div class="field" id="binance-fields-inst" style="display: none;">
                <label for="inst-pair">Par Binance</label>
                <input type="text" id="inst-pair" placeholder="BTCUSDT">
            </div>
            <div class="field">
                <label for="inst-broker-account" class="glossary-term" tabindex="0">Cotiza según<span class="glossary-term-panel">Precio de EJECUCIÓN, no de visualización general -- si se elige una cuenta, este instrumento ignora "Fuente de precio" de arriba y pide la cotización directo a esa cuenta puntual (PPI/IOL), no a data912/etc. "Automático" es el comportamiento de siempre.</span></label>
                <select id="inst-broker-account">${brokerAccountOptionsHtml(inst.broker_account_id)}</select>
            </div>
        </div>
        <div class="field-row" id="bono-fields" style="display: none;">
            <div class="field">
                <label for="inst-maturity">Fecha de vencimiento</label>
                <input type="date" id="inst-maturity" value="${inst.maturity_date || ''}">
            </div>
            <div class="field">
                <label for="inst-coupon-rate">Cup&oacute;n anual %</label>
                <input type="number" id="inst-coupon-rate" step="0.01" min="0" placeholder="0 = cup&oacute;n cero" value="${inst.coupon_rate ?? ''}">
            </div>
            <div class="field">
                <label for="inst-coupon-freq">Frecuencia de pago</label>
                <select id="inst-coupon-freq">
                    <option value="" ${!inst.coupon_frequency ? 'selected' : ''}>Cup&oacute;n cero</option>
                    <option value="1" ${inst.coupon_frequency === 1 ? 'selected' : ''}>Anual</option>
                    <option value="2" ${inst.coupon_frequency === 2 ? 'selected' : ''}>Semestral</option>
                    <option value="4" ${inst.coupon_frequency === 4 ? 'selected' : ''}>Trimestral</option>
                    <option value="12" ${inst.coupon_frequency === 12 ? 'selected' : ''}>Mensual</option>
                </select>
            </div>
            <div class="field">
                <label for="inst-rate-type">Tipo de tasa</label>
                <select id="inst-rate-type">
                    <option value="" ${!inst.rate_type ? 'selected' : ''}>Sin clasificar</option>
                    <option value="fija" ${inst.rate_type === 'fija' ? 'selected' : ''}>Fija</option>
                    <option value="cer" ${inst.rate_type === 'cer' ? 'selected' : ''}>Ajustable por CER</option>
                </select>
            </div>
        </div>
        <p class="modal-error"></p>
        <div class="modal-footer">
            <button type="submit" class="btn btn-primary">${isEdit ? 'Guardar' : 'Agregar'}</button>
        </div>
    `;
}

function buildPriceSourceParams(source) {
    if (source === 'dolarapi') return { casa: $('inst-casa').value };
    if (source === 'binance') {
        const pair = $('inst-pair').value.trim();
        return pair ? { pair } : null;
    }
    return null;
}

function wireSourceToggle(form) {
    const sourceSelect = form.querySelector('#inst-source');
    const sync = () => {
        form.querySelector('#dolarapi-fields').style.display = sourceSelect.value === 'dolarapi' ? 'flex' : 'none';
        form.querySelector('#binance-fields-inst').style.display = sourceSelect.value === 'binance' ? 'flex' : 'none';
    };
    sourceSelect.addEventListener('change', sync);
    sync();
}

function wireTypeToggle(form) {
    const typeSelect = form.querySelector('#inst-type');
    const sync = () => {
        form.querySelector('#bono-fields').style.display = ['bono', 'on'].includes(typeSelect.value) ? 'flex' : 'none';
    };
    typeSelect.addEventListener('change', sync);
    sync();
}

function wireCuentaToggle(form) {
    const cuentaSelect = form.querySelector('#inst-cuenta');
    const sync = () => {
        form.querySelector('#cuenta-nueva-field').style.display = cuentaSelect.value === '__nueva__' ? 'flex' : 'none';
    };
    cuentaSelect.addEventListener('change', sync);
    sync();
}

function resolveCuenta(form) {
    const value = form.querySelector('#inst-cuenta').value;
    if (value === '__nueva__') return form.querySelector('#inst-cuenta-nueva').value.trim() || null;
    return value === CUENTA_DEFAULT ? null : value;
}

// inst=null (nuevo) o un objeto YA conocido por el llamador (editar, o un
// resultado de explorerSearch prellenado para "agregar a cartera") -- este
// modulo ya no mantiene su propia tabla, asi que refresca currentInstruments
// (para las opciones de "Cuenta/Broker" del formulario) antes de abrir.
// Al guardar, dispara 'instruments-changed' en vez de recargar una vista
// propia -- quien este mostrando la lista (instrumentos.js) se re-renderiza
// solo, desacoplado de este modulo.
export async function openInstrumentModal(inst = null) {
    const [instruments, brokerAccounts] = await Promise.all([fetchInstrumentsList(), api.brokerAccounts()]);
    currentInstruments = instruments;
    currentBrokerAccounts = brokerAccounts.data;
    openModal({
        title: inst?.id ? `Editar ${inst.symbol}` : 'Nuevo instrumento',
        bodyHtml: formFieldsHtml(inst || {}),
        onMount: (form) => { wireSourceToggle(form); wireTypeToggle(form); wireCuentaToggle(form); },
        onSubmit: async (form) => {
            const source = form.querySelector('#inst-source').value;
            const type = form.querySelector('#inst-type').value;
            const payload = {
                symbol: form.querySelector('#inst-symbol').value,
                name: form.querySelector('#inst-name').value,
                type,
                market: form.querySelector('#inst-market').value,
                cuenta: resolveCuenta(form),
                currency: form.querySelector('#inst-currency').value,
                price_source: source,
                price_source_params: buildPriceSourceParams(source),
                broker_account_id: form.querySelector('#inst-broker-account').value
                    ? Number(form.querySelector('#inst-broker-account').value) : null,
            };
            if (['bono', 'on'].includes(type)) {
                const maturity = form.querySelector('#inst-maturity').value;
                const couponRate = form.querySelector('#inst-coupon-rate').value;
                const couponFreq = form.querySelector('#inst-coupon-freq').value;
                const rateType = form.querySelector('#inst-rate-type').value;
                payload.maturity_date = maturity || null;
                payload.coupon_rate = couponRate === '' ? null : Number(couponRate);
                payload.coupon_frequency = couponFreq === '' ? null : Number(couponFreq);
                payload.rate_type = rateType || null;
            }
            if (inst?.id) {
                await api.updateInstrument(inst.id, payload);
                showStatus('status-banner', 'success', 'Instrumento actualizado');
            } else {
                await api.addInstrument(payload);
                showStatus('status-banner', 'success', 'Instrumento agregado');
            }
            currentInstruments = await fetchInstrumentsList();
            document.dispatchEvent(new CustomEvent('instruments-changed'));
        },
    });
}

// Devuelve true si se borro de verdad -- el llamador decide como refrescar
// su propia vista (ver instrumentos.js).
export async function deleteInstrument(id, symbol) {
    if (!confirm(`¿Eliminar el instrumento ${symbol}? Esta acción no se puede deshacer.`)) return false;
    try {
        await api.deleteInstrument(id);
        showStatus('status-banner', 'success', 'Instrumento eliminado');
        document.dispatchEvent(new CustomEvent('instruments-changed'));
        return true;
    } catch (error) {
        showStatus('status-banner', 'error', error.response?.data?.error || error.message);
        return false;
    }
}
