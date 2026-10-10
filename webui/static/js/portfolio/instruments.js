import { $ } from '../shared/dom.js';
import { showStatus } from '../shared/status.js';
import { api } from './api.js';
import { createSearchBar, applySearchState } from '../shared/search-bar.js';
import { rowActionsHtml, wireRowActions } from '../shared/row-actions.js';
import { openModal } from '../shared/modal.js';

const TYPE_LABELS = {
    accion: 'Acción', cedear: 'CEDEAR', bono: 'Bono',
    on: 'Obligación Negociable', moneda: 'Moneda', fondo: 'Fondo',
};

let currentInstruments = [];
let searchState = { query: '', activeFilters: new Set(), groupKey: null };

const SEARCH_CONFIG = {
    searchFields: (inst) => [inst.symbol, inst.name],
    filters: [
        { key: 'accion', label: 'Acción', test: (i) => i.type === 'accion' },
        { key: 'cedear', label: 'CEDEAR', test: (i) => i.type === 'cedear' },
        { key: 'bono', label: 'Bono', test: (i) => i.type === 'bono' },
        { key: 'on', label: 'Obligación Negociable', test: (i) => i.type === 'on' },
        { key: 'moneda', label: 'Moneda', test: (i) => i.type === 'moneda' },
        { key: 'fondo', label: 'Fondo', test: (i) => i.type === 'fondo' },
        { key: 'ars', label: 'ARS', test: (i) => i.currency === 'ARS' },
        { key: 'usd', label: 'USD', test: (i) => i.currency === 'USD' },
    ],
    groupBy: [
        { key: 'type', label: 'Tipo', value: (i) => TYPE_LABELS[i.type] || i.type },
        { key: 'price_source', label: 'Fuente de precio', value: (i) => i.price_source },
    ],
};

export async function fetchInstrumentsList() {
    const { data } = await api.instruments();
    return data;
}

export async function loadInstruments() {
    currentInstruments = await fetchInstrumentsList();
    renderTable();
    return currentInstruments;
}

function renderTable() {
    const tbody = $('instruments-tbody');
    if (!currentInstruments.length) {
        tbody.innerHTML = `<tr><td colspan="7" class="table-empty">Todav&iacute;a no carg&aacute;ste ning&uacute;n instrumento.</td></tr>`;
        return;
    }

    const result = applySearchState(currentInstruments, searchState, SEARCH_CONFIG);
    if (result.grouped) {
        tbody.innerHTML = result.groups.map((g) => `
            <tr class="table-group-header"><td colspan="7">${g.label} (${g.items.length})</td></tr>
            ${g.items.map(rowHtml).join('')}
        `).join('');
    } else {
        tbody.innerHTML = result.items.length
            ? result.items.map(rowHtml).join('')
            : `<tr><td colspan="7" class="table-empty">Sin resultados para este filtro.</td></tr>`;
    }
}

function rowHtml(inst) {
    const params = new URLSearchParams({ market: inst.market, assetType: inst.type, instrumentId: inst.id });
    return `<tr>
        <td><a href="#detalle/${encodeURIComponent(inst.symbol)}?${params.toString()}" class="table-link">${inst.symbol}</a></td>
        <td>${inst.name || '—'}</td>
        <td><span class="tag tag-neutral">${TYPE_LABELS[inst.type] || inst.type}</span></td>
        <td><span class="tag tag-neutral">${inst.market}</span></td>
        <td><span class="tag tag-neutral">${inst.currency}</span></td>
        <td><span class="tag tag-neutral">${inst.price_source}</span></td>
        ${rowActionsHtml(inst.id)}
    </tr>`;
}

function formFieldsHtml(inst = {}) {
    const isEdit = !!inst.id;
    return `
        <div class="field-row">
            <div class="field"><label for="inst-symbol">S&iacute;mbolo</label><input type="text" id="inst-symbol" placeholder="GGAL" value="${inst.symbol || ''}"></div>
            <div class="field"><label for="inst-name">Nombre</label><input type="text" id="inst-name" placeholder="Grupo Financiero Galicia" value="${inst.name || ''}"></div>
            <div class="field">
                <label for="inst-type">Tipo</label>
                <select id="inst-type">
                    <option value="accion" ${inst.type === 'accion' ? 'selected' : ''}>Acci&oacute;n</option>
                    <option value="cedear" ${inst.type === 'cedear' ? 'selected' : ''}>CEDEAR</option>
                    <option value="bono" ${inst.type === 'bono' ? 'selected' : ''}>Bono</option>
                    <option value="on" ${inst.type === 'on' ? 'selected' : ''}>Obligaci&oacute;n Negociable</option>
                    <option value="moneda" ${inst.type === 'moneda' ? 'selected' : ''}>Moneda</option>
                    <option value="fondo" ${inst.type === 'fondo' ? 'selected' : ''}>Fondo</option>
                </select>
            </div>
            <div class="field"><label for="inst-market">Mercado</label><input type="text" id="inst-market" placeholder="byma" value="${inst.market || ''}"></div>
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

function openInstrumentModal(inst = null) {
    openModal({
        title: inst ? `Editar ${inst.symbol}` : 'Nuevo instrumento',
        bodyHtml: formFieldsHtml(inst || {}),
        onMount: wireSourceToggle,
        onSubmit: async (form) => {
            const source = form.querySelector('#inst-source').value;
            const payload = {
                symbol: form.querySelector('#inst-symbol').value,
                name: form.querySelector('#inst-name').value,
                type: form.querySelector('#inst-type').value,
                market: form.querySelector('#inst-market').value,
                currency: form.querySelector('#inst-currency').value,
                price_source: source,
                price_source_params: buildPriceSourceParams(source),
            };
            if (inst) {
                await api.updateInstrument(inst.id, payload);
                showStatus('status-banner', 'success', 'Instrumento actualizado');
            } else {
                await api.addInstrument(payload);
                showStatus('status-banner', 'success', 'Instrumento agregado');
            }
            await loadInstruments();
        },
    });
}

async function deleteInstrument(id) {
    const inst = currentInstruments.find((i) => String(i.id) === String(id));
    if (!inst) return;
    if (!confirm(`¿Eliminar el instrumento ${inst.symbol}? Esta acción no se puede deshacer.`)) return;
    try {
        await api.deleteInstrument(id);
        showStatus('status-banner', 'success', 'Instrumento eliminado');
        await loadInstruments();
    } catch (error) {
        showStatus('status-banner', 'error', error.response?.data?.error || error.message);
    }
}

export function initInstrumentsView() {
    createSearchBar($('instruments-search-bar'), {
        ...SEARCH_CONFIG,
        searchPlaceholder: 'Buscar por símbolo o nombre...',
        onChange: (state) => { searchState = state; renderTable(); },
    });
    $('new-instrument-btn').addEventListener('click', () => openInstrumentModal());
    wireRowActions($('instruments-tbody'), {
        onEdit: (id) => openInstrumentModal(currentInstruments.find((i) => String(i.id) === String(id))),
        onDelete: deleteInstrument,
    });
}
