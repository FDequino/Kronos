// Préstamos a terceros (Fase 10): mismo patrón Odoo-table que Plazos Fijos/
// Cauciones -- acá se diferencia en que el "devengado"/estado depende del
// plan de pago (bullet/francés/alemán/libre, ver portfolio/loans.py), no de
// una fórmula única de interés simple.
import { $ } from '../shared/dom.js';
import { showStatus } from '../shared/status.js';
import { fmt, fmtMoney, fmtDate, todayLocalISO } from '../shared/format.js';
import { loadingHtml } from '../shared/loading.js';
import { api } from './api.js';
import { createSearchBar, applySearchState, tableBodyRowsHtml, wireGroupToggles } from '../shared/search-bar.js';
import { mountColumnToggle, headerCellHtml, updateSortIndicators, wireColumnDrag, withMobileCardLayout, mobileExpandToggleHtml, wireMobileCardToggle } from '../shared/table-chrome.js';
import { createSorter, wireSortableHeaders } from '../shared/sortable-table.js';
import { createColumnManager } from '../shared/column-manager.js';
import { openModal, closeModal } from '../shared/modal.js';

const PLAN_LABEL = { bullet: 'Bullet', frances: 'Francés', aleman: 'Alemán', libre: 'Libre' };

let currentLoans = [];
let loansSearchState = { query: '', activeFilters: new Set(['vigente']), groupKey: null };
const loansSorter = createSorter();
const loansCollapsedGroups = new Set();

const DIRECCION_LABEL = { prestado: 'Prestaste', recibido: 'Te prestaron' };

const LOAN_COLUMNS = [
    { key: 'direccion', label: 'Dirección', numeric: false, sortable: false },
    { key: 'deudor', label: 'Contraparte', numeric: false },
    { key: 'moneda', label: 'Moneda', numeric: false, sortable: false },
    { key: 'capital', label: 'Capital original', numeric: true },
    { key: 'tasa_anual', label: 'TNA', numeric: true, term: 'tna' },
    { key: 'plan_pago', label: 'Plan', numeric: false, sortable: false },
    { key: 'capital_pendiente', label: 'Capital pendiente', numeric: true },
    { key: 'proximo', label: 'Próximo vencimiento', numeric: false, sortable: false },
    { key: 'estado', label: 'Estado', numeric: false, sortable: false },
];
const loansColumns = createColumnManager('kronos-loans-columns', LOAN_COLUMNS);

const LOANS_SEARCH_CONFIG = {
    searchFields: [{ key: 'deudor', label: 'Deudor', value: (r) => r.deudor }],
    favoritesKey: 'kronos-loans-favorites',
    filters: [
        { key: 'vigente', label: 'Vigentes', block: 'Estado', test: (r) => r.estado === 'vigente' },
        { key: 'pagado', label: 'Pagados', block: 'Estado', test: (r) => r.estado === 'pagado' },
        { key: 'incobrable', label: 'Incobrables', block: 'Estado', test: (r) => r.estado === 'incobrable' },
        { key: 'atrasado', label: 'Atrasados', block: 'Mora', test: (r) => !!r.vencido_hace_dias },
        { key: 'ars', label: 'ARS', block: 'Moneda', test: (r) => r.moneda === 'ARS' },
        { key: 'usd', label: 'USD', block: 'Moneda', test: (r) => r.moneda === 'USD' },
        { key: 'prestado', label: 'Prestaste', block: 'Dirección', test: (r) => r.direccion === 'prestado' },
        { key: 'recibido', label: 'Te prestaron', block: 'Dirección', test: (r) => r.direccion === 'recibido' },
    ],
    groupBy: [
        { key: 'deudor', label: 'Deudor', value: (r) => r.deudor },
        { key: 'plan_pago', label: 'Plan de pago', value: (r) => PLAN_LABEL[r.plan_pago] || r.plan_pago },
    ],
};

// Fase 15: panel "Resumen" (grilla 2x2) al costado de la tabla -- montos
// demasiado dispares entre deudores (de USD 100 a $14M) para un gráfico de
// barras legible, el detalle real ya está en la tabla.
// Fase 16: "Te deben/Debés/En mora" mostraban ARS y USD sumados con un "+"
// (ej. "$14.273.432,00 + USD 2.178,93"), sin un número único para comparar
// de un vistazo -- se unifican vía MEP a la moneda elegida en Preferencias
// (nunca una moneda propia hardcodeada: "si está seleccionado USD, sólo veo
// USD en todos lados" -- ese toggle define la moneda en toda la plataforma,
// no cada pantalla la suya). Se mantiene el desglose nativo como detalle
// chico debajo (nunca se pierde de dónde sale cada parte).
let loansUsdRate = null;
let loansDisplayCcy = 'ARS';

function nativeToDisplay(amount, moneda) {
    if (moneda === loansDisplayCcy) return amount;
    if (!loansUsdRate) return null;
    return moneda === 'USD' ? amount * loansUsdRate : amount / loansUsdRate;
}

function unifiedStatHtml(label, dir, vigentes) {
    const rows = vigentes.filter((r) => r.direccion === dir);
    const porMoneda = ['ARS', 'USD']
        .map((m) => ({ moneda: m, valor: rows.filter((r) => r.moneda === m).reduce((acc, r) => acc + r.capital_pendiente, 0) }))
        .filter((t) => t.valor > 0);
    const sinCotizacion = porMoneda.some((t) => nativeToDisplay(t.valor, t.moneda) === null);
    const total = porMoneda.reduce((acc, t) => acc + (nativeToDisplay(t.valor, t.moneda) || 0), 0);
    const detalle = porMoneda.length > 1 ? porMoneda.map((t) => fmtMoney(t.valor, t.moneda)).join(' + ') : '';
    return `
        <div class="mini-stat">
            <div class="l">${label}</div>
            <div class="v num">${sinCotizacion ? '<span class="hint">sin cotización MEP</span>' : total ? fmtMoney(total, loansDisplayCcy) : '$0'}</div>
            ${detalle ? `<div class="hint" style="margin-top:2px;">${detalle}</div>` : ''}
        </div>
    `;
}

function renderSummary() {
    const el = $('loans-summary');
    if (!el) return;
    const vigentes = currentLoans.filter((r) => r.estado === 'vigente');
    const moraRows = vigentes.filter((r) => r.vencido_hace_dias);
    const moraCount = moraRows.length;
    const moraPorMoneda = ['ARS', 'USD']
        .map((m) => ({ moneda: m, valor: moraRows.filter((r) => r.moneda === m).reduce((acc, r) => acc + r.capital_pendiente, 0) }))
        .filter((t) => t.valor > 0);
    const moraSinCotizacion = moraPorMoneda.some((t) => nativeToDisplay(t.valor, t.moneda) === null);
    const moraTotal = moraPorMoneda.reduce((acc, t) => acc + (nativeToDisplay(t.valor, t.moneda) || 0), 0);
    const moraDetalle = moraPorMoneda.length > 1 ? moraPorMoneda.map((t) => fmtMoney(t.valor, t.moneda)).join(' + ') : '';
    el.innerHTML = `
        ${unifiedStatHtml('Te deben', 'prestado', vigentes)}
        ${unifiedStatHtml('Debés', 'recibido', vigentes)}
        <div class="mini-stat">
            <div class="l">En mora (${moraCount})</div>
            <div class="v num${moraCount ? ' cell-negative' : ''}">${moraSinCotizacion ? '<span class="hint">sin cotización MEP</span>' : moraCount ? fmtMoney(moraTotal, loansDisplayCcy) : 'Sin mora'}</div>
            ${moraDetalle ? `<div class="hint" style="margin-top:2px;">${moraDetalle}</div>` : ''}
        </div>
    `;
}

// --- Flujo de cuotas (Fase 17): antes vivía en Patrimonio como parte de
// "Proyección" (mezclado con el escenario Kronos y vencimientos de bonos) --
// el usuario pidió que esta vista sea solo préstamos y viva acá. Reusa el
// mismo endpoint /projection (ya trae las cuotas de TODOS los préstamos
// vigentes agrupadas por mes), filtrado a solo las filas cuyo origen es una
// cuota de préstamo (el resto -- Kronos, vencimientos de bonos -- se
// descarta a propósito, no le compete a esta pantalla).
const LOAN_MONTH_NAMES = ['Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun', 'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic'];

function loanMonthColumns(count = 12) {
    const now = new Date();
    const cols = [];
    for (let i = 0; i < count; i++) {
        const d = new Date(now.getFullYear(), now.getMonth() + i, 1);
        cols.push({ key: `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`, label: `${LOAN_MONTH_NAMES[d.getMonth()]} '${String(d.getFullYear()).slice(2)}` });
    }
    return cols;
}

function loanFlowCellHtml(row, col) {
    const amount = row.porMes[col.key];
    if (amount === undefined) return '<td class="num"><span class="hint">—</span></td>';
    const converted = nativeToDisplay(amount, row.moneda);
    if (converted === null) return '<td class="num"><span class="hint">sin cotización MEP</span></td>';
    if (!converted) return '<td class="num"><span class="cell-muted">$0</span></td>';
    return `<td class="num"><span class="${converted >= 0 ? 'cell-positive' : 'cell-negative'}">${fmtMoney(converted, loansDisplayCcy, 2, { signed: true })}</span></td>`;
}

function monthlyLoanFlowsHtml(flows) {
    const cols = loanMonthColumns(12);
    if (!flows.length) return '<p class="hint">Ningún préstamo vigente tiene cuotas en los próximos 12 meses.</p>';
    const rowsMap = new Map();
    flows.forEach((f) => {
        const key = `${f.origen}|${f.moneda}`;
        if (!rowsMap.has(key)) rowsMap.set(key, { origen: f.origen, moneda: f.moneda, firstFecha: f.fecha, porMes: {} });
        const row = rowsMap.get(key);
        if (f.fecha < row.firstFecha) row.firstFecha = f.fecha;
        const mk = f.fecha.slice(0, 7);
        row.porMes[mk] = (row.porMes[mk] || 0) + (f.monto || 0);
    });
    const rows = [...rowsMap.values()].sort((a, b) => a.firstFecha.localeCompare(b.firstFecha));
    const totalRow = `
        <tr class="table-total-row">
            <td class="table-total-label">Total (${loansDisplayCcy})</td>
            ${cols.map((col) => {
                let sinCotizacion = false;
                const total = rows.reduce((sum, r) => {
                    const converted = nativeToDisplay(r.porMes[col.key] || 0, r.moneda);
                    if (converted === null) { sinCotizacion = true; return sum; }
                    return sum + converted;
                }, 0);
                if (sinCotizacion) return '<td class="num"><span class="hint">sin cotización MEP</span></td>';
                return `<td class="num"><span class="${total >= 0 ? 'cell-positive' : 'cell-negative'}">${fmtMoney(total, loansDisplayCcy, 2, { signed: true })}</span></td>`;
            }).join('')}
        </tr>
    `;
    return `
        <div class="table-scroll">
            <table>
                <thead><tr><th style="min-width:220px;"></th>${cols.map((c) => `<th>${c.label}</th>`).join('')}</tr></thead>
                <tbody>
                    ${rows.map((r) => `
                        <tr>
                            <td>${r.origen}</td>
                            ${cols.map((col) => loanFlowCellHtml(r, col)).join('')}
                        </tr>
                    `).join('')}
                </tbody>
                <tfoot>${totalRow}</tfoot>
            </table>
        </div>
    `;
}

async function loadLoansProjection() {
    const container = $('loans-projection-container');
    if (!container) return;
    try {
        const { data: projection } = await api.projection();
        const flows = (projection.horizontes['12m'].contractual || []).filter((f) => f.origen.startsWith('Cuota de préstamo'));
        container.innerHTML = monthlyLoanFlowsHtml(flows);
    } catch (err) {
        container.innerHTML = `<p class="hint">Error: ${err.response?.data?.error || err.message}</p>`;
    }
}

export async function loadLoans() {
    const [{ data }, { data: usdMepRate }, { data: settings }] = await Promise.all([api.loans(), api.usdMepRate(), api.settings()]);
    currentLoans = data;
    loansUsdRate = usdMepRate?.rate || null;
    loansDisplayCcy = settings?.display_currency || 'ARS';
    renderTable();
    renderSummary();
    loadLoansProjection();
}

document.addEventListener('currency-changed', loadLoans);

function sortValue(r, key) {
    switch (key) {
        case 'deudor': return r.deudor;
        case 'capital': return r.capital;
        case 'tasa_anual': return r.tasa_anual;
        case 'capital_pendiente': return r.capital_pendiente;
        default: return null;
    }
}

// Sumaba r.capital/r.capital_pendiente crudos sin mirar r.moneda -- un total
// que mezclaba pesos y dólares en un solo número sin símbolo de moneda
// (bug real, nunca mostrado como tal porque "fmt" no imprime la moneda).
// Se convierte cada fila a la moneda elegida en Preferencias antes de sumar.
function loanAggregateFn(key) {
    return (rows) => {
        let sinCotizacion = false;
        const total = rows.reduce((sum, r) => {
            const v = nativeToDisplay(r[key], r.moneda);
            if (v === null) { sinCotizacion = true; return sum; }
            return sum + v;
        }, 0);
        return sinCotizacion ? null : total;
    };
}

function columnsWithAggregates(baseColumns) {
    const AGG = {
        capital: { aggregateFn: loanAggregateFn('capital'), format: (n) => fmtMoney(n, loansDisplayCcy) },
        capital_pendiente: { aggregateFn: loanAggregateFn('capital_pendiente'), format: (n) => fmtMoney(n, loansDisplayCcy) },
    };
    return baseColumns.map((c) => ({ ...c, ...(AGG[c.key] || {}) }));
}

function estadoHtml(r) {
    if (r.estado === 'pagado') return '<span class="tag tag-neutral">Pagado</span>';
    if (r.estado === 'incobrable') return '<span class="tag tag-negative">Incobrable</span>';
    // cuotas_atrasadas (ver conversación, Fase 7): antes esto solo mostraba
    // la cuota impaga más vieja, ocultando en silencio si había más de una
    // (ej. Sep Y Oct impagas del mismo préstamo contaban igual que solo Sep).
    if (r.vencido_hace_dias) {
        const cuotas = r.cuotas_atrasadas > 1 ? ` (${r.cuotas_atrasadas} cuotas)` : '';
        return `<span class="tag tag-negative">Atrasado ${r.vencido_hace_dias}d${cuotas}</span>`;
    }
    return '<span class="tag tag-positive">Al día</span>';
}

function rowHtml(r, columns) {
    const cells = columns.map((c) => {
        switch (c.key) {
            case 'direccion': return `<td><span class="tag ${r.direccion === 'recibido' ? 'tag-negative' : 'tag-positive'}">${DIRECCION_LABEL[r.direccion] || r.direccion}</span></td>`;
            case 'deudor': return `<td>${r.deudor}</td>`;
            case 'moneda': return `<td><span class="tag tag-neutral">${r.moneda}</span></td>`;
            case 'capital': return `<td class="num">${fmtMoney(r.capital, r.moneda)}</td>`;
            case 'tasa_anual': return `<td class="num">${fmt(r.tasa_anual, 2)}%</td>`;
            case 'plan_pago': return `<td>${PLAN_LABEL[r.plan_pago] || r.plan_pago}${r.ajusta_cer ? ' + CER' : ''}</td>`;
            case 'capital_pendiente': return `<td class="num">${fmtMoney(r.capital_pendiente, r.moneda)}</td>`;
            case 'proximo': return `<td class="num">${r.proximo_vencimiento ? `${fmtDate(r.proximo_vencimiento)} · ${fmtMoney(r.proximo_monto, r.moneda)}` : '—'}</td>`;
            case 'estado': return `<td>${estadoHtml(r)}</td>`;
            default: return '<td></td>';
        }
    });
    const mobileCells = withMobileCardLayout(cells, columns, 2);
    return `<tr data-loan-id="${r.id}">${mobileCells.join('')}${mobileExpandToggleHtml(columns, 2)}
        <td class="row-actions">
            <button type="button" class="btn btn-secondary loan-detail-btn" data-id="${r.id}">Ver detalle</button>
            <button type="button" class="btn btn-secondary loan-pago-btn" data-id="${r.id}">Registrar pago</button>
            <button type="button" class="row-action-btn loan-edit-btn" data-id="${r.id}" aria-label="Editar">✎</button>
            <button type="button" class="row-action-btn loan-delete-btn" data-id="${r.id}" aria-label="Eliminar">🗑</button>
        </td>
    </tr>`;
}

function renderTable() {
    const container = $('loans-table-container');
    const columns = columnsWithAggregates(loansColumns.visibleColumns());
    const colspan = columns.length + 1;
    if (!currentLoans.length) {
        container.innerHTML = `<table><tbody><tr><td colspan="${colspan}" class="table-empty">
            Todavía no cargáste ningún préstamo a terceros.</td></tr></tbody></table>`;
        const slot = $('loans-columns-slot');
        if (slot) slot.innerHTML = '';
        return;
    }
    const sorted = loansSorter.key ? loansSorter.apply(currentLoans, sortValue) : currentLoans;
    const result = applySearchState(sorted, loansSearchState, LOANS_SEARCH_CONFIG);
    const bodyRows = tableBodyRowsHtml(result, columns, (r) => rowHtml(r, columns), { collapsedGroups: loansCollapsedGroups, colspan });

    container.innerHTML = `
        <div class="table-scroll">
            <table>
                <thead><tr>${columns.map((c) => headerCellHtml(c)).join('')}<th></th></tr></thead>
                <tbody>${bodyRows}</tbody>
            </table>
        </div>
    `;
    const rerender = () => renderTable();
    mountColumnToggle($('loans-columns-slot'), loansColumns, rerender);
    wireColumnDrag(container.querySelector('thead tr'), loansColumns, rerender);
    wireSortableHeaders(container, loansSorter, rerender);
    wireGroupToggles(container, loansCollapsedGroups, rerender);
    updateSortIndicators(container, loansSorter);

    container.querySelectorAll('.loan-edit-btn').forEach((btn) => btn.addEventListener('click', () => {
        openLoanModal(currentLoans.find((r) => String(r.id) === btn.dataset.id));
    }));
    container.querySelectorAll('.loan-delete-btn').forEach((btn) => btn.addEventListener('click', () => deleteLoan(btn.dataset.id)));
    container.querySelectorAll('.loan-pago-btn').forEach((btn) => btn.addEventListener('click', () => {
        openPaymentModal(currentLoans.find((r) => String(r.id) === btn.dataset.id));
    }));
    container.querySelectorAll('.loan-detail-btn').forEach((btn) => btn.addEventListener('click', () => {
        openLoanDetailModal(currentLoans.find((r) => String(r.id) === btn.dataset.id));
    }));
}

function loanFormFieldsHtml(loan = {}) {
    const isEdit = !!loan.id;
    return `
        <div class="field-row">
            <div class="field">
                <label for="loan-direccion">Dirección</label>
                <select id="loan-direccion">
                    <option value="prestado" ${(loan.direccion || 'prestado') === 'prestado' ? 'selected' : ''}>Vos le prestaste a...</option>
                    <option value="recibido" ${loan.direccion === 'recibido' ? 'selected' : ''}>Te prestaron a vos (pediste prestado)</option>
                </select>
            </div>
            <div class="field"><label for="loan-deudor">Contraparte</label><input type="text" id="loan-deudor" value="${loan.deudor || ''}"></div>
            <div class="field">
                <label for="loan-moneda">Moneda</label>
                <select id="loan-moneda">
                    <option value="ARS" ${loan.moneda === 'ARS' ? 'selected' : ''}>ARS</option>
                    <option value="USD" ${loan.moneda === 'USD' ? 'selected' : ''}>USD</option>
                </select>
            </div>
            <div class="field"><label for="loan-capital">Capital</label><input type="number" step="any" id="loan-capital" value="${loan.capital ?? ''}"></div>
            <div class="field"><label for="loan-fecha-inicio">Fecha inicio</label><input type="date" id="loan-fecha-inicio" value="${loan.fecha_inicio || ''}"></div>
            <div class="field"><label for="loan-tasa">TNA (%)</label><input type="number" step="any" id="loan-tasa" value="${loan.tasa_anual ?? ''}"></div>
            <div class="field">
                <label for="loan-plan">Plan de pago</label>
                <select id="loan-plan">
                    <option value="bullet" ${loan.plan_pago === 'bullet' ? 'selected' : ''}>Bullet (todo al vencimiento)</option>
                    <option value="frances" ${loan.plan_pago === 'frances' ? 'selected' : ''}>Francés (cuota fija)</option>
                    <option value="aleman" ${loan.plan_pago === 'aleman' ? 'selected' : ''}>Alemán (capital fijo)</option>
                    <option value="libre" ${loan.plan_pago === 'libre' ? 'selected' : ''}>Libre (sin cronograma)</option>
                </select>
            </div>
            <div class="field"><label for="loan-plazo">Plazo (meses)</label><input type="number" id="loan-plazo" placeholder="12" value="${loan.plazo_meses ?? ''}"></div>
            <div class="field">
                <label for="loan-cer"><input type="checkbox" id="loan-cer" ${loan.ajusta_cer ? 'checked' : ''}> Ajusta por CER/UVA</label>
            </div>
        </div>
        ${isEdit ? `
            <div class="field" style="margin-top: 8px;">
                <label>Documentos (pagarés, comprobantes)</label>
                <div id="loan-documents-list">${loadingHtml('Cargando documentos…')}</div>
                <input type="file" id="loan-document-input" style="margin-top: 6px;">
            </div>
        ` : '<p class="hint">Guardá el préstamo primero para poder adjuntar documentos.</p>'}
        <p class="modal-error"></p>
        <div class="modal-footer">
            <button type="submit" class="btn btn-primary">${isEdit ? 'Guardar' : 'Agregar'}</button>
        </div>
    `;
}

function documentRowHtml(doc, loanId) {
    const sizeKb = doc.size_bytes ? ` (${Math.round(doc.size_bytes / 1024)} KB)` : '';
    return `
        <div class="loan-document-row" data-doc-id="${doc.id}" style="display:flex; align-items:center; gap:8px; padding:4px 0;">
            <a href="${api.loanDocumentDownloadUrl(loanId, doc.id)}" target="_blank" rel="noopener">${doc.nombre_original}</a>
            <span class="hint">${sizeKb}</span>
            <button type="button" class="row-action-btn loan-document-delete-btn" data-doc-id="${doc.id}" aria-label="Eliminar">🗑</button>
        </div>
    `;
}

async function renderDocumentsList(loanId, container) {
    const { data: docs } = await api.loanDocuments(loanId);
    container.innerHTML = docs.length
        ? docs.map((d) => documentRowHtml(d, loanId)).join('')
        : '<span class="hint">Todavía no subiste ningún documento.</span>';
    container.querySelectorAll('.loan-document-delete-btn').forEach((btn) => btn.addEventListener('click', async () => {
        if (!confirm('¿Eliminar este documento?')) return;
        await api.deleteLoanDocument(loanId, btn.dataset.docId);
        await renderDocumentsList(loanId, container);
    }));
}

function openLoanModal(loan = null) {
    openModal({
        title: loan?.id ? `Editar préstamo (${loan.deudor})` : 'Nuevo préstamo',
        bodyHtml: loanFormFieldsHtml(loan || {}),
        onMount: (form) => {
            if (!loan?.id) { form.querySelector('#loan-fecha-inicio').value = todayLocalISO(); return; }
            const list = form.querySelector('#loan-documents-list');
            renderDocumentsList(loan.id, list);
            form.querySelector('#loan-document-input').addEventListener('change', async (e) => {
                const file = e.target.files[0];
                if (!file) return;
                await api.uploadLoanDocument(loan.id, file);
                e.target.value = '';
                await renderDocumentsList(loan.id, list);
            });
        },
        onSubmit: async (form) => {
            const payload = {
                direccion: form.querySelector('#loan-direccion').value,
                deudor: form.querySelector('#loan-deudor').value,
                moneda: form.querySelector('#loan-moneda').value,
                capital: form.querySelector('#loan-capital').value,
                fecha_inicio: form.querySelector('#loan-fecha-inicio').value,
                tasa_anual: form.querySelector('#loan-tasa').value,
                plan_pago: form.querySelector('#loan-plan').value,
                plazo_meses: form.querySelector('#loan-plazo').value || null,
                ajusta_cer: form.querySelector('#loan-cer').checked,
            };
            if (loan?.id) {
                await api.updateLoan(loan.id, payload);
                showStatus('status-banner', 'success', 'Préstamo actualizado');
            } else {
                await api.addLoan(payload);
                showStatus('status-banner', 'success', 'Préstamo agregado');
            }
            await loadLoans();
        },
    });
}

async function deleteLoan(id) {
    if (!confirm('¿Eliminar este préstamo? Esta acción no se puede deshacer.')) return;
    try {
        await api.deleteLoan(id);
        showStatus('status-banner', 'success', 'Préstamo eliminado');
        await loadLoans();
    } catch (error) {
        showStatus('status-banner', 'error', error.response?.data?.error || error.message);
    }
}

function openPaymentModal(loan) {
    // Prefill con la próxima cuota teórica cuando el plan tiene cronograma --
    // deja claro cuál sería "el monto exacto", pero el campo sigue siendo
    // editable libremente: no hace falta que coincida (ver hint abajo).
    const sugeridoCapital = loan.proximo_monto != null
        ? Math.max(0, (loan.proximo_monto - (loan.interes_devengado_pendiente || 0)))
        : 0;
    const planConCronograma = loan.plan_pago === 'frances' || loan.plan_pago === 'aleman';
    openModal({
        title: `Registrar pago — ${loan.deudor}`,
        bodyHtml: `
            <div class="field-row">
                <div class="field"><label for="pago-fecha">Fecha</label><input type="date" id="pago-fecha" value="${todayLocalISO()}"></div>
                <div class="field"><label for="pago-capital">Capital</label><input type="number" step="any" id="pago-capital" value="${sugeridoCapital ? sugeridoCapital.toFixed(2) : 0}"></div>
                <div class="field"><label for="pago-interes">Interés</label><input type="number" step="any" id="pago-interes" value="${loan.interes_devengado_pendiente ? loan.interes_devengado_pendiente.toFixed(2) : 0}"></div>
            </div>
            <p class="hint">Capital pendiente: ${fmtMoney(loan.capital_pendiente, loan.moneda)}${loan.interes_devengado_pendiente != null ? ` · Interés devengado sin cobrar: ${fmtMoney(loan.interes_devengado_pendiente, loan.moneda)}` : ''}</p>
            <p class="hint">No hace falta que coincida con la cuota exacta — lo que cargues acá se descuenta tal cual del capital pendiente (un pago de más o de menos queda reflejado la próxima vez que mires el préstamo).${planConCronograma ? ' En planes Francés/Alemán, eso sí: si pagás menos de lo que corresponde, igual cuenta como "esa cuota" pagada a los efectos de detectar atraso (se compara cantidad de pagos contra cantidad de cuotas vencidas, no el monto).' : ''}</p>
            <p class="modal-error"></p>
            <div class="modal-footer"><button type="submit" class="btn btn-primary">Registrar</button></div>
        `,
        onSubmit: async (form) => {
            await api.addLoanPayment(loan.id, {
                fecha: form.querySelector('#pago-fecha').value,
                capital: form.querySelector('#pago-capital').value || 0,
                interes: form.querySelector('#pago-interes').value || 0,
            });
            showStatus('status-banner', 'success', 'Pago registrado');
            await loadLoans();
        },
    });
}

function cronogramaRowHtml(c, index, pagosCount, moneda) {
    const pagada = index < pagosCount;
    return `
        <tr>
            <td class="num">${index + 1}</td>
            <td>${fmtDate(c.fecha)}</td>
            <td class="num">${fmtMoney(c.capital, moneda)}</td>
            <td class="num">${fmtMoney(c.interes, moneda)}</td>
            <td class="num">${fmtMoney(c.cuota, moneda)}</td>
            <td>${pagada ? '<span class="tag tag-positive">Pagada</span>' : '<span class="tag tag-neutral">Pendiente</span>'}</td>
        </tr>
    `;
}

function paymentRowHtml(p, loanId, moneda) {
    return `
        <tr data-payment-id="${p.id}">
            <td>${fmtDate(p.fecha)}</td>
            <td class="num">${fmtMoney(p.capital, moneda)}</td>
            <td class="num">${fmtMoney(p.interes, moneda)}</td>
            <td class="num">${fmtMoney(p.capital + p.interes, moneda)}</td>
            <td class="row-actions"><button type="button" class="row-action-btn loan-payment-delete-btn" data-loan-id="${loanId}" data-payment-id="${p.id}" aria-label="Eliminar">🗑</button></td>
        </tr>
    `;
}

async function renderLoanDetailPayments(loan, container) {
    const { data: payments } = await api.loanPayments(loan.id);
    container.innerHTML = payments.length ? `
        <div class="table-scroll">
            <table>
                <thead><tr><th>Fecha</th><th class="num">Capital</th><th class="num">Interés</th><th class="num">Total</th><th></th></tr></thead>
                <tbody>${payments.map((p) => paymentRowHtml(p, loan.id, loan.moneda)).join('')}</tbody>
            </table>
        </div>
    ` : '<p class="hint">Todavía no se registró ningún pago.</p>';
    container.querySelectorAll('.loan-payment-delete-btn').forEach((btn) => btn.addEventListener('click', async () => {
        if (!confirm('¿Eliminar este pago?')) return;
        await api.deleteLoanPayment(btn.dataset.loanId, btn.dataset.paymentId);
        showStatus('status-banner', 'success', 'Pago eliminado');
        await loadLoans();
        const refreshed = currentLoans.find((r) => String(r.id) === String(loan.id));
        if (refreshed) await renderLoanDetailPayments(refreshed, container);
    }));
}

function openLoanDetailModal(loan) {
    const cronograma = loan.cronograma || [];
    const planConCronograma = loan.plan_pago === 'frances' || loan.plan_pago === 'aleman';
    openModal({
        title: `Detalle — ${loan.deudor}`,
        bodyHtml: `
            <p class="hint">${loan.explanation}</p>
            ${cronograma.length ? `
                <div class="stat-detail-label" style="margin-top:12px;">Cronograma (teórico)</div>
                ${planConCronograma ? '<p class="hint">"Pagada" cuenta por cantidad de pagos registrados en orden, no por monto exacto — un pago parcial igual marca la cuota de ese turno como cubierta a los efectos de esta lista.</p>' : ''}
                <div class="table-scroll">
                    <table>
                        <thead><tr><th>#</th><th>Fecha</th><th class="num">Capital</th><th class="num">Interés</th><th class="num">Cuota</th><th>Estado</th></tr></thead>
                        <tbody id="loan-detail-cronograma"></tbody>
                    </table>
                </div>
            ` : '<p class="hint">Este préstamo no tiene cronograma fijo (plan libre) — el devengado se calcula día a día sobre el capital pendiente, sin cuotas con fecha.</p>'}
            <div class="stat-detail-label" style="margin-top:16px;">Pagos registrados</div>
            <div id="loan-detail-payments">${loadingHtml('Cargando pagos…')}</div>
            <div class="modal-footer"><button type="button" class="btn btn-secondary modal-close-btn">Cerrar</button></div>
        `,
        onMount: async (form) => {
            form.querySelector('.modal-close-btn')?.addEventListener('click', () => closeModal());
            const paymentsContainer = $('loan-detail-payments');
            const cronogramaBody = $('loan-detail-cronograma');
            const { data: payments } = await api.loanPayments(loan.id);
            if (cronogramaBody) {
                cronogramaBody.innerHTML = cronograma.map((c, i) => cronogramaRowHtml(c, i, payments.length, loan.moneda)).join('');
            }
            await renderLoanDetailPayments(loan, paymentsContainer);
        },
    });
}

export function initLoansView() {
    createSearchBar($('loans-search-bar'), {
        ...LOANS_SEARCH_CONFIG,
        searchPlaceholder: 'Buscar por deudor...',
        initialState: loansSearchState,
        onChange: (state) => { loansSearchState = state; renderTable(); },
    });
    $('new-loan-btn').addEventListener('click', () => openLoanModal());
    wireMobileCardToggle($('loans-table-container'));
}
