import { $ } from '../shared/dom.js';
import { showStatus } from '../shared/status.js';
import { fmt } from '../shared/format.js';
import { api } from './api.js';
import { fetchInstrumentsList } from './instruments.js';
import { loadHoldings } from './holdings.js';
import { createSearchBar, applySearchState } from '../shared/search-bar.js';
import { rowActionsHtml, wireRowActions } from '../shared/row-actions.js';
import { openModal } from '../shared/modal.js';

let instrumentById = new Map();
let instrumentBySymbol = new Map();
let currentTransactions = [];
let currentRentas = [];
let txSearchState = { query: '', activeFilters: new Set(), groupKey: null };
let rentaSearchState = { query: '', activeFilters: new Set(), groupKey: null };

const TIPO_RENTA_LABEL = { dividendo: 'Dividendo', renta: 'Renta', amortizacion: 'Amortización' };

const TX_SEARCH_CONFIG = {
    searchFields: (tx) => [instrumentById.get(tx.instrument_id)?.symbol, tx.notas],
    filters: [
        { key: 'compra', label: 'Compra', test: (tx) => tx.tipo === 'compra' },
        { key: 'venta', label: 'Venta', test: (tx) => tx.tipo === 'venta' },
    ],
    groupBy: [
        { key: 'instrument', label: 'Instrumento', value: (tx) => instrumentById.get(tx.instrument_id)?.symbol || `#${tx.instrument_id}` },
        { key: 'tipo', label: 'Tipo', value: (tx) => (tx.tipo === 'compra' ? 'Compra' : 'Venta') },
    ],
};

const RENTA_SEARCH_CONFIG = {
    searchFields: (r) => [r.symbol],
    filters: [
        { key: 'dividendo', label: 'Dividendo', test: (r) => r.tipo === 'dividendo' },
        { key: 'renta', label: 'Renta', test: (r) => r.tipo === 'renta' },
        { key: 'amortizacion', label: 'Amortización', test: (r) => r.tipo === 'amortizacion' },
    ],
    groupBy: [
        { key: 'symbol', label: 'Símbolo', value: (r) => r.symbol },
    ],
};

export async function populateInstrumentSelect() {
    const instruments = await fetchInstrumentsList();
    instrumentById = new Map(instruments.map((i) => [i.id, i]));
    instrumentBySymbol = new Map(instruments.map((i) => [i.symbol, i]));
}

function instrumentOptionsHtml(selectedId) {
    return [...instrumentById.values()]
        .map((i) => `<option value="${i.id}" ${String(i.id) === String(selectedId) ? 'selected' : ''}>${i.symbol} (${i.type})</option>`)
        .join('');
}

function detailLinkHtml(inst, fallbackSymbol) {
    if (!inst) return fallbackSymbol;
    const params = new URLSearchParams({ market: inst.market, assetType: inst.type, instrumentId: inst.id });
    return `<a href="#detalle/${encodeURIComponent(inst.symbol)}?${params.toString()}" class="table-link">${inst.symbol}</a>`;
}

export async function loadTransactionHistory() {
    const { data } = await api.transactions();
    currentTransactions = [...data].reverse();
    renderHistory();
}

function renderHistory() {
    const tbody = $('transactions-tbody');
    if (!currentTransactions.length) {
        tbody.innerHTML = `<tr><td colspan="7" class="table-empty">Sin transacciones cargadas.</td></tr>`;
        return;
    }
    const result = applySearchState(currentTransactions, txSearchState, TX_SEARCH_CONFIG);
    if (result.grouped) {
        tbody.innerHTML = result.groups.map((g) => `
            <tr class="table-group-header"><td colspan="7">${g.label} (${g.items.length})</td></tr>
            ${g.items.map(rowHtml).join('')}
        `).join('');
    } else {
        tbody.innerHTML = result.items.length ? result.items.map(rowHtml).join('') : `<tr><td colspan="7" class="table-empty">Sin resultados para este filtro.</td></tr>`;
    }
}

function rowHtml(tx) {
    const inst = instrumentById.get(tx.instrument_id);
    const tipoClass = tx.tipo === 'compra' ? 'tag-positive' : 'tag-negative';
    return `<tr>
        <td>${tx.fecha}</td><td>${detailLinkHtml(inst, `#${tx.instrument_id}`)}</td>
        <td><span class="tag ${tipoClass}">${tx.tipo}</span></td>
        <td class="num">${fmt(tx.cantidad, 4)}</td><td class="num">${fmt(tx.precio)}</td>
        <td class="num">${fmt(tx.comision)}</td>
        ${rowActionsHtml(tx.id)}
    </tr>`;
}

export async function loadRentasDividendos() {
    const { data } = await api.rentasDividendos();
    currentRentas = data;
    renderRentasDividendos();
}

function renderRentasDividendos() {
    const tbody = $('rentas-dividendos-tbody');
    if (!currentRentas.length) {
        tbody.innerHTML = `<tr><td colspan="7" class="table-empty">Sin dividendos o rentas registrados.</td></tr>`;
        return;
    }
    const result = applySearchState(currentRentas, rentaSearchState, RENTA_SEARCH_CONFIG);
    if (result.grouped) {
        tbody.innerHTML = result.groups.map((g) => `
            <tr class="table-group-header"><td colspan="7">${g.label} (${g.items.length})</td></tr>
            ${g.items.map(rentaRowHtml).join('')}
        `).join('');
    } else {
        tbody.innerHTML = result.items.length ? result.items.map(rentaRowHtml).join('') : `<tr><td colspan="7" class="table-empty">Sin resultados para este filtro.</td></tr>`;
    }
}

function rentaRowHtml(r) {
    return `<tr>
        <td>${r.fecha}</td><td>${detailLinkHtml(instrumentBySymbol.get(r.symbol), r.symbol)}</td><td>${TIPO_RENTA_LABEL[r.tipo] || r.tipo}</td>
        <td class="num">${fmt(r.bruto, 2)}</td><td class="num">${fmt(r.gastos, 2)}</td>
        <td class="num cell-positive">${fmt(r.neto, 2)}</td><td>${r.moneda}</td>
    </tr>`;
}

function txFormFieldsHtml(tx = {}) {
    const isEdit = !!tx.id;
    return `
        <div class="field-row">
            <div class="field"><label for="tx-instrument">Instrumento</label><select id="tx-instrument">${instrumentOptionsHtml(tx.instrument_id)}</select></div>
            <div class="field">
                <label for="tx-tipo">Tipo</label>
                <select id="tx-tipo">
                    <option value="compra" ${tx.tipo === 'compra' ? 'selected' : ''}>Compra</option>
                    <option value="venta" ${tx.tipo === 'venta' ? 'selected' : ''}>Venta</option>
                </select>
            </div>
            <div class="field"><label for="tx-fecha">Fecha</label><input type="date" id="tx-fecha" value="${tx.fecha || ''}"></div>
            <div class="field"><label for="tx-cantidad">Cantidad</label><input type="number" step="any" id="tx-cantidad" value="${tx.cantidad ?? ''}"></div>
            <div class="field"><label for="tx-precio">Precio</label><input type="number" step="any" id="tx-precio" value="${tx.precio ?? ''}"></div>
            <div class="field"><label for="tx-comision">Comisi&oacute;n</label><input type="number" step="any" id="tx-comision" value="${tx.comision ?? 0}"></div>
        </div>
        <p class="modal-error"></p>
        <div class="modal-footer">
            <button type="submit" class="btn btn-primary">${isEdit ? 'Guardar' : 'Agregar'}</button>
        </div>
    `;
}

function openTransactionModal(tx = null) {
    openModal({
        title: tx ? 'Editar transacción' : 'Nueva transacción',
        bodyHtml: txFormFieldsHtml(tx || {}),
        onMount: (form) => {
            if (!tx) form.querySelector('#tx-fecha').valueAsDate = new Date();
        },
        onSubmit: async (form) => {
            const payload = {
                instrument_id: form.querySelector('#tx-instrument').value,
                tipo: form.querySelector('#tx-tipo').value,
                fecha: form.querySelector('#tx-fecha').value,
                cantidad: form.querySelector('#tx-cantidad').value,
                precio: form.querySelector('#tx-precio').value,
                comision: form.querySelector('#tx-comision').value,
            };
            if (tx) {
                await api.updateTransaction(tx.id, payload);
                showStatus('status-banner', 'success', 'Transacción actualizada');
            } else {
                await api.addTransaction(payload);
                showStatus('status-banner', 'success', 'Transacción agregada');
            }
            await Promise.all([loadTransactionHistory(), loadHoldings()]);
        },
    });
}

async function deleteTransaction(id) {
    if (!confirm('¿Eliminar esta transacción? Esta acción no se puede deshacer.')) return;
    try {
        await api.deleteTransaction(id);
        showStatus('status-banner', 'success', 'Transacción eliminada');
        await Promise.all([loadTransactionHistory(), loadHoldings()]);
    } catch (error) {
        showStatus('status-banner', 'error', error.response?.data?.error || error.message);
    }
}

export function initTransactionsView() {
    createSearchBar($('transactions-search-bar'), {
        ...TX_SEARCH_CONFIG,
        searchPlaceholder: 'Buscar por instrumento o nota...',
        onChange: (state) => { txSearchState = state; renderHistory(); },
    });
    createSearchBar($('rentas-search-bar'), {
        ...RENTA_SEARCH_CONFIG,
        searchPlaceholder: 'Buscar por símbolo...',
        onChange: (state) => { rentaSearchState = state; renderRentasDividendos(); },
    });
    $('new-transaction-btn').addEventListener('click', () => openTransactionModal());
    wireRowActions($('transactions-tbody'), {
        onEdit: (id) => openTransactionModal(currentTransactions.find((t) => String(t.id) === String(id))),
        onDelete: deleteTransaction,
    });
}
