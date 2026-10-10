import { $ } from '../shared/dom.js';
import { showStatus } from '../shared/status.js';
import { fmt, fmtPct, fmtMoney } from '../shared/format.js';
import { api } from './api.js';
import { infoTooltipHtml } from '../shared/info-tooltip.js';
import { createSearchBar, applySearchState } from '../shared/search-bar.js';
import { rowActionsHtml, wireRowActions } from '../shared/row-actions.js';
import { openModal } from '../shared/modal.js';

let currentPlazos = [];
let currentCauciones = [];
let pfSearchState = { query: '', activeFilters: new Set(), groupKey: null };
let caucionSearchState = { query: '', activeFilters: new Set(), groupKey: null };

const PF_SEARCH_CONFIG = {
    searchFields: (r) => [r.plazo_fijo.banco],
    filters: [
        { key: 'tradicional', label: 'Tradicional', test: (r) => r.plazo_fijo.tipo === 'tradicional' },
        { key: 'uva', label: 'UVA', test: (r) => r.plazo_fijo.tipo === 'uva' },
        { key: 'activo', label: 'Activo', test: (r) => !r.vencido },
        { key: 'vencido', label: 'Vencido', test: (r) => r.vencido },
    ],
    groupBy: [
        { key: 'banco', label: 'Banco', value: (r) => r.plazo_fijo.banco },
        { key: 'tipo', label: 'Tipo', value: (r) => r.plazo_fijo.tipo },
    ],
};

const CAUCION_SEARCH_CONFIG = {
    searchFields: (r) => [r.caucion.numero_boleto],
    filters: [
        { key: 'activa', label: 'Activa', test: (r) => !r.vencido },
        { key: 'vencida', label: 'Vencida', test: (r) => r.vencido },
        { key: 'ars', label: 'ARS', test: (r) => r.caucion.moneda === 'ARS' },
        { key: 'usd', label: 'USD', test: (r) => r.caucion.moneda === 'USD' },
    ],
    groupBy: [
        { key: 'moneda', label: 'Moneda', value: (r) => r.caucion.moneda },
    ],
};

export async function loadPlazosFijos() {
    const { data } = await api.plazosFijos();
    currentPlazos = data;
    renderTable();
}

export async function loadCauciones() {
    const { data } = await api.cauciones();
    currentCauciones = data;
    renderCaucionesTable();
}

function renderCaucionesTable() {
    const tbody = $('cauciones-tbody');
    if (!currentCauciones.length) {
        tbody.innerHTML = `<tr><td colspan="8" class="table-empty">Sin cauciones registradas.</td></tr>`;
        return;
    }
    const result = applySearchState(currentCauciones, caucionSearchState, CAUCION_SEARCH_CONFIG);
    const rows = result.grouped
        ? result.groups.map((g) => `
            <tr class="table-group-header"><td colspan="8">${g.label} (${g.items.length})</td></tr>
            ${g.items.map(caucionRowHtml).join('')}
        `).join('')
        : (result.items.length ? result.items.map(caucionRowHtml).join('') : `<tr><td colspan="8" class="table-empty">Sin resultados para este filtro.</td></tr>`);
    tbody.innerHTML = rows;
}

function caucionRowHtml(r) {
    const c = r.caucion;
    const estado = r.vencido
        ? '<span class="tag tag-negative">Vencida</span>'
        : '<span class="tag tag-positive">Activa</span>';
    const formula = [
        'devengado = interés_bruto × días_transcurridos / plazo_días',
        `= ${fmt(c.interes_bruto)} × ${r.dias_transcurridos} / ${c.plazo_dias}`,
        `= ${fmt(r.devengado)}`,
    ];
    const devengadoInfo = infoTooltipHtml(`<div class="info-tooltip-formula">${formula.join('\n')}</div>`);
    return `<tr>
        <td class="num">${fmtMoney(c.monto_contado, c.moneda)}</td><td class="num">${fmtPct(c.tasa)}</td>
        <td class="num">${r.dias_transcurridos}/${c.plazo_dias}</td>
        <td class="num cell-positive">${fmtMoney(r.devengado, c.moneda)}${devengadoInfo}</td>
        <td class="num">${fmtMoney(r.valor_actual, c.moneda)}</td>
        <td>${c.fecha_vencimiento}</td><td>${estado}</td>
        ${rowActionsHtml(c.id)}
    </tr>`;
}

function renderTable() {
    const tbody = $('pf-tbody');
    if (!currentPlazos.length) {
        tbody.innerHTML = `<tr><td colspan="9" class="table-empty">Sin plazos fijos cargados.</td></tr>`;
        return;
    }
    const result = applySearchState(currentPlazos, pfSearchState, PF_SEARCH_CONFIG);
    const rows = result.grouped
        ? result.groups.map((g) => `
            <tr class="table-group-header"><td colspan="9">${g.label} (${g.items.length})</td></tr>
            ${g.items.map(rowHtml).join('')}
        `).join('')
        : (result.items.length ? result.items.map(rowHtml).join('') : `<tr><td colspan="9" class="table-empty">Sin resultados para este filtro.</td></tr>`);
    tbody.innerHTML = rows;
}

function rowHtml(r) {
    const pf = r.plazo_fijo;
    const estado = r.vencido
        ? '<span class="tag tag-negative">Vencido</span>'
        : '<span class="tag tag-positive">Activo</span>';
    const formula = [
        'devengado = monto × tna × días_transcurridos / 365',
        `= ${fmt(pf.monto)} × ${pf.tna} × ${r.dias_transcurridos} / 365`,
        `= ${fmt(r.devengado)}`,
    ];
    const devengadoInfo = infoTooltipHtml(`<div class="info-tooltip-formula">${formula.join('\n')}</div>`);
    return `<tr>
        <td>${pf.banco}</td><td>${pf.tipo}</td><td class="num">${fmtMoney(pf.monto)}</td>
        <td class="num">${fmtPct(pf.tna * 100)}</td><td class="num">${r.dias_transcurridos}/${pf.plazo_dias}</td>
        <td class="num cell-positive">${fmtMoney(r.devengado)}${devengadoInfo}</td>
        <td class="num">${fmtMoney(r.valor_actual)}</td>
        <td>${estado}</td>
        ${rowActionsHtml(pf.id)}
    </tr>`;
}

function pfFormFieldsHtml(pf = {}) {
    const isEdit = !!pf.id;
    return `
        <div class="field-row">
            <div class="field"><label for="pf-banco">Banco</label><input type="text" id="pf-banco" placeholder="Banco Naci&oacute;n" value="${pf.banco || ''}"></div>
            <div class="field">
                <label for="pf-tipo">Tipo</label>
                <select id="pf-tipo">
                    <option value="tradicional" ${pf.tipo === 'tradicional' ? 'selected' : ''}>Tradicional</option>
                    <option value="uva" ${pf.tipo === 'uva' ? 'selected' : ''}>UVA</option>
                </select>
            </div>
            <div class="field"><label for="pf-monto">Monto</label><input type="number" id="pf-monto" placeholder="500000" value="${pf.monto ?? ''}"></div>
            <div class="field"><label for="pf-tna">TNA (ej 0.40)</label><input type="number" step="0.01" id="pf-tna" placeholder="0.40" value="${pf.tna ?? ''}"></div>
            <div class="field"><label for="pf-fecha-inicio">Fecha inicio</label><input type="date" id="pf-fecha-inicio" value="${pf.fecha_inicio || ''}"></div>
            <div class="field"><label for="pf-plazo-dias">Plazo (d&iacute;as)</label><input type="number" id="pf-plazo-dias" placeholder="90" value="${pf.plazo_dias ?? ''}"></div>
        </div>
        <p class="modal-error"></p>
        <div class="modal-footer">
            <button type="submit" class="btn btn-primary">${isEdit ? 'Guardar' : 'Agregar'}</button>
        </div>
    `;
}

function openPlazoFijoModal(pf = null) {
    openModal({
        title: pf ? `Editar plazo fijo (${pf.banco})` : 'Nuevo plazo fijo',
        bodyHtml: pfFormFieldsHtml(pf || {}),
        onMount: (form) => { if (!pf) form.querySelector('#pf-fecha-inicio').valueAsDate = new Date(); },
        onSubmit: async (form) => {
            const payload = {
                banco: form.querySelector('#pf-banco').value,
                tipo: form.querySelector('#pf-tipo').value,
                monto: form.querySelector('#pf-monto').value,
                tna: form.querySelector('#pf-tna').value,
                fecha_inicio: form.querySelector('#pf-fecha-inicio').value,
                plazo_dias: form.querySelector('#pf-plazo-dias').value,
            };
            if (pf) {
                await api.updatePlazoFijo(pf.id, payload);
                showStatus('status-banner', 'success', 'Plazo fijo actualizado');
            } else {
                await api.addPlazoFijo(payload);
                showStatus('status-banner', 'success', 'Plazo fijo agregado');
            }
            await loadPlazosFijos();
        },
    });
}

async function deletePlazoFijo(id) {
    if (!confirm('¿Eliminar este plazo fijo? Esta acción no se puede deshacer.')) return;
    try {
        await api.deletePlazoFijo(id);
        showStatus('status-banner', 'success', 'Plazo fijo eliminado');
        await loadPlazosFijos();
    } catch (error) {
        showStatus('status-banner', 'error', error.response?.data?.error || error.message);
    }
}

function caucionFormFieldsHtml(c = {}) {
    const isEdit = !!c.id;
    return `
        <div class="field-row">
            <div class="field"><label for="ca-boleto">N&uacute;mero de boleto</label><input type="text" id="ca-boleto" value="${c.numero_boleto || ''}"></div>
            <div class="field"><label for="ca-fecha-apertura">Fecha apertura</label><input type="date" id="ca-fecha-apertura" value="${c.fecha_apertura || ''}"></div>
            <div class="field"><label for="ca-fecha-vencimiento">Fecha vencimiento</label><input type="date" id="ca-fecha-vencimiento" value="${c.fecha_vencimiento || ''}"></div>
            <div class="field"><label for="ca-plazo-dias">Plazo (d&iacute;as)</label><input type="number" id="ca-plazo-dias" placeholder="7" value="${c.plazo_dias ?? ''}"></div>
            <div class="field"><label for="ca-monto-contado">Monto contado</label><input type="number" step="any" id="ca-monto-contado" value="${c.monto_contado ?? ''}"></div>
            <div class="field"><label for="ca-monto-futuro">Monto futuro</label><input type="number" step="any" id="ca-monto-futuro" value="${c.monto_futuro ?? ''}"></div>
            <div class="field"><label for="ca-tasa">Tasa (% anual)</label><input type="number" step="any" id="ca-tasa" value="${c.tasa ?? ''}"></div>
            <div class="field"><label for="ca-interes-bruto">Inter&eacute;s bruto</label><input type="number" step="any" id="ca-interes-bruto" value="${c.interes_bruto ?? ''}"></div>
            <div class="field">
                <label for="ca-moneda">Moneda</label>
                <select id="ca-moneda">
                    <option value="ARS" ${c.moneda === 'ARS' ? 'selected' : ''}>ARS</option>
                    <option value="USD" ${c.moneda === 'USD' ? 'selected' : ''}>USD</option>
                </select>
            </div>
        </div>
        <p class="modal-error"></p>
        <div class="modal-footer">
            <button type="submit" class="btn btn-primary">${isEdit ? 'Guardar' : 'Agregar'}</button>
        </div>
    `;
}

function openCaucionModal(c = null) {
    openModal({
        title: c ? 'Editar caución' : 'Nueva caución',
        bodyHtml: caucionFormFieldsHtml(c || {}),
        onMount: (form) => { if (!c) form.querySelector('#ca-fecha-apertura').valueAsDate = new Date(); },
        onSubmit: async (form) => {
            const payload = {
                numero_boleto: form.querySelector('#ca-boleto').value || null,
                fecha_apertura: form.querySelector('#ca-fecha-apertura').value,
                fecha_vencimiento: form.querySelector('#ca-fecha-vencimiento').value,
                plazo_dias: form.querySelector('#ca-plazo-dias').value,
                monto_contado: form.querySelector('#ca-monto-contado').value,
                monto_futuro: form.querySelector('#ca-monto-futuro').value,
                tasa: form.querySelector('#ca-tasa').value,
                interes_bruto: form.querySelector('#ca-interes-bruto').value,
                moneda: form.querySelector('#ca-moneda').value,
            };
            if (c) {
                await api.updateCaucion(c.id, payload);
                showStatus('status-banner', 'success', 'Caución actualizada');
            } else {
                await api.addCaucion(payload);
                showStatus('status-banner', 'success', 'Caución agregada');
            }
            await loadCauciones();
        },
    });
}

async function deleteCaucion(id) {
    if (!confirm('¿Eliminar esta caución? Esta acción no se puede deshacer.')) return;
    try {
        await api.deleteCaucion(id);
        showStatus('status-banner', 'success', 'Caución eliminada');
        await loadCauciones();
    } catch (error) {
        showStatus('status-banner', 'error', error.response?.data?.error || error.message);
    }
}

export function initPlazosFijosView() {
    createSearchBar($('pf-search-bar'), {
        ...PF_SEARCH_CONFIG,
        searchPlaceholder: 'Buscar por banco...',
        onChange: (state) => { pfSearchState = state; renderTable(); },
    });
    createSearchBar($('cauciones-search-bar'), {
        ...CAUCION_SEARCH_CONFIG,
        searchPlaceholder: 'Buscar por número de boleto...',
        onChange: (state) => { caucionSearchState = state; renderCaucionesTable(); },
    });
    $('new-pf-btn').addEventListener('click', () => openPlazoFijoModal());
    $('new-caucion-btn').addEventListener('click', () => openCaucionModal());
    wireRowActions($('pf-tbody'), {
        onEdit: (id) => openPlazoFijoModal(currentPlazos.find((r) => String(r.plazo_fijo.id) === String(id))?.plazo_fijo),
        onDelete: deletePlazoFijo,
    });
    wireRowActions($('cauciones-tbody'), {
        onEdit: (id) => openCaucionModal(currentCauciones.find((r) => String(r.caucion.id) === String(id))?.caucion),
        onDelete: deleteCaucion,
    });
}
