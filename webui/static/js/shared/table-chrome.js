import { glossaryTerm } from './glossary.js';

// "Chrome" generica de tabla (boton+panel de columnas, drag de columnas,
// indicadores de orden) -- extraido de Tenencias (holdings.js) para que
// cualquier tabla nueva (Posiciones, y las que migre Fase 3) lo reutilice
// en vez de reimplementarlo cada vez. Todo queda scopeado via
// container.querySelector, nunca IDs globales, asi que conviven varias
// tablas con esta misma chrome en la misma pagina sin colisionar.

// Tildar una columna dispara onChange(), que reconstruye toda la tabla
// (innerHTML entero) -- sin esto, el panel recien armado siempre arrancaba
// "hidden" de nuevo, como si tildar una columna cerrara el menu solo (bug
// reportado). Se recuerda que panel estaba abierto por columnManager (la
// instancia sobrevive al re-render, el DOM no) para que columnToggleHtml lo
// arranque ya abierto la proxima vez.
const openPanels = new WeakSet();

export function columnToggleHtml(columnManager) {
    const open = openPanels.has(columnManager);
    return `
    <div class="column-toggle">
        <button type="button" class="column-toggle-btn">Columnas</button>
        <div class="column-toggle-panel" ${open ? '' : 'hidden'}>
            ${columnManager.allColumns().map((c) => `
                <label><input type="checkbox" data-col="${c.key}" ${columnManager.isHidden(c.key) ? '' : 'checked'}> ${c.label}</label>
            `).join('')}
        </div>
    </div>`;
}

// Monta el botón+panel de columnas en un slot FIJO de la fila del buscador
// (ver conversación, Fase 3: "mover el botón Columnas a la fila de
// búsqueda") en vez de adentro del contenedor de la tabla, que se reemplaza
// entero (innerHTML) en cada render -- antes esto hacía que "Columnas"
// apareciera en una fila propia, separada del buscador, en todas las tablas
// que lo usan (Tenencias, Transacciones, Rentas, Préstamos, Plazos Fijos,
// Cauciones). El slot se re-arma en cada llamada (mismo ciclo de vida que
// antes), solo cambia DÓNDE vive en el DOM.
export function mountColumnToggle(slot, columnManager, onChange) {
    if (!slot) return;
    slot.innerHTML = columnToggleHtml(columnManager);
    wireColumnToggle(slot, columnManager, onChange);
}

export function wireColumnToggle(container, columnManager, onChange) {
    const btn = container.querySelector('.column-toggle-btn');
    const panel = container.querySelector('.column-toggle-panel');
    panel._columnManager = columnManager; // ver nota del listener global mas abajo
    btn.addEventListener('click', (e) => {
        e.stopPropagation();
        panel.hidden = !panel.hidden;
        if (panel.hidden) openPanels.delete(columnManager); else openPanels.add(columnManager);
    });
    panel.querySelectorAll('input[type="checkbox"]').forEach((cb) => {
        cb.addEventListener('click', (e) => e.stopPropagation());
        cb.addEventListener('change', () => {
            columnManager.toggleVisible(cb.dataset.col);
            onChange();
        });
    });
}

// Fila de total SIEMPRE visible (no solo al agrupar) -- reusa la misma
// metadata aggregate/value/format que groupRowHtml ya usa para subtotales
// de grupo (ver search-bar.js), asi una columna no necesita declarar la
// logica dos veces. `rows`: las filas ya filtradas/visibles (post-busqueda),
// NUNCA el dataset completo sin filtrar -- un total que no refleja el
// filtro activo confundiria mas de lo que ayuda.
export function totalRowHtml(columns, rows, label = 'Total') {
    const cells = columns.map((c, i) => {
        if (i === 0) return `<td class="table-total-label">${label}</td>`;
        // aggregateFn: para agregados que no son una simple suma/promedio por
        // fila (ej. P&L % ponderado por costo, no un promedio simple de
        // porcentajes -- sumar/promediar cantidad/costo/precio entre
        // instrumentos distintos no tiene sentido económico y se deja en
        // blanco a propósito).
        if (c.aggregateFn) {
            const agg = c.aggregateFn(rows);
            if (agg === null || agg === undefined) return '<td></td>';
            return `<td class="num">${c.format ? c.format(agg) : agg}</td>`;
        }
        if (!c.aggregate || !c.value) return '<td></td>';
        const values = rows.map((r) => c.value(r)).filter((v) => v !== null && v !== undefined);
        if (!values.length) return '<td></td>';
        const sum = values.reduce((a, b) => a + b, 0);
        const agg = c.aggregate === 'avg' ? sum / values.length : sum;
        return `<td class="num">${c.format ? c.format(agg) : agg}</td>`;
    });
    return `<tr class="table-total-row">${cells.join('')}</tr>`;
}

export function headerCellHtml(c) {
    const sortable = c.sortable !== false;
    // c.term: clave de glossary.js -- subraya el label con el patron de
    // tooltip liviano (Fase 4) en vez de que cada tabla lo arme a mano.
    const labelHtml = c.term ? glossaryTerm(c.term, c.label) : c.label;
    return `<th class="${c.numeric ? 'num ' : ''}${sortable ? 'sortable' : ''}" data-sort="${c.key}" draggable="true">${labelHtml}${c.headerExtra || ''}${sortable ? '<span class="sort-indicator"></span>' : ''}</th>`;
}

export function updateSortIndicators(container, sorter) {
    container.querySelectorAll('th.sortable').forEach((th) => {
        const indicator = th.querySelector('.sort-indicator');
        if (!indicator) return;
        indicator.textContent = th.dataset.sort === sorter.key ? (sorter.dir > 0 ? ' ▲' : ' ▼') : '';
    });
}

// Un solo listener global (el modulo se evalua una sola vez aunque varias
// tablas lo importen) cierra el panel de columnas de CUALQUIER tabla al
// clickear afuera -- antes esto vivia por tabla con un ID fijo
// ("column-toggle-panel"), lo que se rompia apenas convivian dos tablas con
// esta misma chrome en la pagina (Tenencias + Posiciones, Fase 2).
document.addEventListener('click', (e) => {
    document.querySelectorAll('.column-toggle-panel').forEach((panel) => {
        if (!panel.hidden && !panel.closest('.column-toggle')?.contains(e.target)) {
            panel.hidden = true;
            if (panel._columnManager) openPanels.delete(panel._columnManager);
        }
    });
});

export function wireColumnDrag(theadRow, columnManager, onChange) {
    let dragKey = null;
    theadRow.querySelectorAll('th').forEach((th) => {
        th.addEventListener('dragstart', (e) => {
            dragKey = th.dataset.sort;
            e.dataTransfer.effectAllowed = 'move';
        });
        th.addEventListener('dragover', (e) => {
            e.preventDefault();
            e.dataTransfer.dropEffect = 'move';
        });
        th.addEventListener('drop', (e) => {
            e.preventDefault();
            const targetKey = th.dataset.sort;
            if (dragKey && dragKey !== targetKey) {
                columnManager.moveColumn(dragKey, targetKey);
                onChange();
            }
        });
    });
}
