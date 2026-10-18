let uidCounter = 0;

const SEARCH_ICON = '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="8.5" cy="8.5" r="5.5"/><path d="m16 16-3.5-3.5"/></svg>';
const CARET_ICON = '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="m6 8 4 4 4-4"/></svg>';
const CLEAR_ICON = '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M6 6l8 8M14 6l-8 8"/></svg>';

const DEBOUNCE_MS = 180;

// Sin tilde/mayúsculas (ver conversación, Fase 4) -- "gonzalez" tiene que
// encontrar "González" y viceversa, sin que el usuario tenga que escribir el
// acento exacto.
function normalize(s) {
    return String(s ?? '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
}

// Distancia de edición <= 1 (insertar/borrar/sustituir un caracter), O(n) en
// vez de la DP completa de Levenshtein -- barato de correr contra cientos de
// filas en cada tecla. Nunca se usa sola: solo como fallback de fuzzyMatch
// cuando el substring exacto ya falló.
function isEditDistanceAtMost1(a, b) {
    if (a === b) return true;
    const la = a.length;
    const lb = b.length;
    if (Math.abs(la - lb) > 1) return false;
    if (la === lb) {
        let diff = 0;
        for (let i = 0; i < la; i++) {
            if (a[i] !== b[i] && ++diff > 1) return false;
        }
        return diff === 1;
    }
    const [shorter, longer] = la < lb ? [a, b] : [b, a];
    let i = 0;
    let j = 0;
    let skipped = false;
    while (i < shorter.length && j < longer.length) {
        if (shorter[i] === longer[j]) { i++; j++; continue; }
        if (skipped) return false;
        skipped = true;
        j++;
    }
    return true;
}

// Tolerante a UN error de tipeo (ver conversación, Fase 4: "typo-tolerant si
// sale barato") -- solo como fallback del substring exacto, y solo contra
// palabras sueltas del campo (nunca una oración entera tipo "evidencia" de
// una propuesta) para que el costo sea O(palabras), no O(caracteres^2).
// needles con espacios (poco común en estos buscadores: símbolo/deudor/
// banco) se quedan solo con el substring exacto, a propósito.
export function fuzzyMatch(haystack, needle) {
    const h = normalize(haystack);
    const n = normalize(needle);
    if (!n) return true;
    if (h.includes(n)) return true;
    if (n.length < 2 || n.includes(' ') || h.length > 60) return false;
    return h.split(/\s+/).some((word) => isEditDistanceAtMost1(word, n));
}

function loadFavorites(key) {
    if (!key) return [];
    try {
        const saved = JSON.parse(localStorage.getItem(key) || '[]');
        return Array.isArray(saved) ? saved : [];
    } catch {
        return [];
    }
}

function saveFavorites(key, favorites) {
    if (!key) return;
    localStorage.setItem(key, JSON.stringify(favorites));
}

export function createSearchBar(container, config) {
    const uid = ++uidCounter;
    container.classList.add('search-bar-slot');
    const filters = config.filters || [];
    const groupBy = config.groupBy || [];
    let favorites = loadFavorites(config.favoritesKey);

    // initialState: para tablas embebidas en un render que se reconstruye
    // entero por otros motivos (ej. Posiciones del Dashboard, que se repinta
    // al cambiar de período) -- sin esto, cada repintado resetea la búsqueda
    // del usuario en silencio porque el DOM del search-bar se recrea de cero.
    const init = config.initialState || {};
    const state = {
        query: init.query || '',
        activeFilters: new Set(init.activeFilters || []),
        groupKey: init.groupKey || null,
    };

    function filterBlocksHtml() {
        const blocks = new Map();
        filters.forEach((f) => {
            const blockKey = f.block || '';
            if (!blocks.has(blockKey)) blocks.set(blockKey, []);
            blocks.get(blockKey).push(f);
        });
        return [...blocks.entries()].map(([blockLabel, defs], i) => `
            ${i > 0 ? '<div class="search-bar-block-sep"></div>' : ''}
            ${blockLabel ? `<div class="search-bar-block-label">${blockLabel}</div>` : ''}
            ${defs.map((f) => `<label class="search-bar-option"><input type="checkbox" data-filter-key="${f.key}"> ${f.label}</label>`).join('')}
        `).join('');
    }

    function favoritesColumnHtml() {
        if (!config.favoritesKey) return '';
        return `
            <div class="search-bar-section search-bar-favorites">
                <div class="search-bar-section-title">Favoritos</div>
                <button type="button" class="search-bar-fav-save">&#9734; Guardar b&uacute;squeda actual</button>
                <div class="search-bar-fav-list">
                    ${favorites.length ? favorites.map((f, i) => `
                        <div class="search-bar-fav-item" data-idx="${i}">
                            <span class="search-bar-fav-name">${f.name}</span>
                            <button type="button" class="search-bar-fav-remove" data-idx="${i}" aria-label="Eliminar favorito">&times;</button>
                        </div>
                    `).join('') : '<p class="search-bar-fav-empty">Sin búsquedas guardadas.</p>'}
                </div>
            </div>
        `;
    }

    const panelNeeded = filters.length || groupBy.length || config.favoritesKey;

    container.innerHTML = `
        <div class="search-bar">
            <div class="search-bar-input-wrap">
                <span class="search-bar-icon">${SEARCH_ICON}</span>
                <div class="search-bar-pills"></div>
                <input type="text" class="search-bar-input" placeholder="${config.searchPlaceholder || 'Buscar...'}" autocomplete="off">
                <button type="button" class="search-bar-clear" hidden aria-label="Limpiar búsqueda">${CLEAR_ICON}</button>
            </div>
            ${panelNeeded ? `<button type="button" class="btn btn-secondary search-bar-filters-btn">${CARET_ICON} Filtros</button>` : ''}
            ${panelNeeded ? `<div class="search-bar-panel search-bar-panel-grid" hidden>
                ${filters.length ? `<div class="search-bar-section">
                    <div class="search-bar-section-title">Filtros</div>
                    ${filterBlocksHtml()}
                </div>` : ''}
                ${groupBy.length ? `<div class="search-bar-section">
                    <div class="search-bar-section-title">Agrupar por</div>
                    <label class="search-bar-option"><input type="radio" name="groupby-${uid}" data-group-key="" checked> Ninguno</label>
                    ${groupBy.map((g) => `<label class="search-bar-option"><input type="radio" name="groupby-${uid}" data-group-key="${g.key}"> ${g.label}</label>`).join('')}
                </div>` : ''}
                ${favoritesColumnHtml()}
            </div>` : ''}
        </div>
    `;

    const input = container.querySelector('.search-bar-input');
    const clearBtn = container.querySelector('.search-bar-clear');
    const filtersBtn = container.querySelector('.search-bar-filters-btn');
    const panel = container.querySelector('.search-bar-panel');
    const pillsEl = container.querySelector('.search-bar-pills');

    function emitChange() {
        config.onChange?.({
            query: state.query,
            activeFilters: new Set(state.activeFilters),
            groupKey: state.groupKey,
        });
    }

    function renderPills() {
        const pills = [];
        state.activeFilters.forEach((key) => {
            const f = filters.find((x) => x.key === key);
            if (f) pills.push({ kind: 'filter', key, label: f.label });
        });
        if (state.groupKey) {
            const g = groupBy.find((x) => x.key === state.groupKey);
            if (g) pills.push({ kind: 'group', key: g.key, label: `Agrupado por: ${g.label}` });
        }
        pillsEl.innerHTML = pills.map((p) => `
            <span class="search-bar-pill" data-kind="${p.kind}" data-key="${p.key ?? ''}"><span class="search-bar-pill-label">${p.label}</span><button type="button" class="search-bar-pill-remove" aria-label="Quitar">&times;</button></span>
        `).join('');
    }

    function updateClearButton() {
        clearBtn.hidden = !input.value;
    }

    // Filtra EN VIVO, sin tener que elegir un campo ni confirmar con Enter
    // (ver conversación, Fase 4: antes había que escribir, elegir "Buscar
    // <campo> por: texto" de un desplegable y recién ahí quedaba como chip).
    // El debounce es solo sobre el re-render de la tabla (puede ser pesado,
    // ej. Instrumentos con ~1300 filas) -- el input en sí responde a cada
    // tecla sin esperar nada.
    let debounceTimer = null;
    input.addEventListener('input', () => {
        updateClearButton();
        state.query = input.value;
        clearTimeout(debounceTimer);
        debounceTimer = setTimeout(emitChange, DEBOUNCE_MS);
    });
    input.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && input.value) {
            e.stopPropagation();
            input.value = '';
            state.query = '';
            updateClearButton();
            clearTimeout(debounceTimer);
            emitChange();
        }
    });
    clearBtn.addEventListener('click', () => {
        input.value = '';
        state.query = '';
        updateClearButton();
        clearTimeout(debounceTimer);
        emitChange();
        input.focus();
    });

    if (filtersBtn) {
        filtersBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            panel.hidden = !panel.hidden;
        });
        document.addEventListener('click', (e) => {
            if (!container.contains(e.target)) panel.hidden = true;
        });
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && !panel.hidden) panel.hidden = true;
        });
        panel.querySelectorAll('input[data-filter-key]').forEach((cb) => {
            cb.addEventListener('change', () => {
                if (cb.checked) state.activeFilters.add(cb.dataset.filterKey);
                else state.activeFilters.delete(cb.dataset.filterKey);
                renderPills();
                emitChange();
            });
        });
        panel.querySelectorAll('input[data-group-key]').forEach((radio) => {
            radio.addEventListener('change', () => {
                state.groupKey = radio.dataset.groupKey || null;
                renderPills();
                emitChange();
            });
        });

        function applyFavorite(fav) {
            state.query = fav.query || '';
            state.activeFilters = new Set(fav.activeFilters || []);
            state.groupKey = fav.groupKey || null;
            input.value = state.query;
            updateClearButton();
            panel.querySelectorAll('input[data-filter-key]').forEach((cb) => {
                cb.checked = state.activeFilters.has(cb.dataset.filterKey);
            });
            const groupRadio = panel.querySelector(`input[data-group-key="${state.groupKey || ''}"]`);
            if (groupRadio) groupRadio.checked = true;
            renderPills();
            emitChange();
        }

        const favSaveBtn = panel.querySelector('.search-bar-fav-save');
        if (favSaveBtn) {
            favSaveBtn.addEventListener('click', () => {
                const name = prompt('Nombre para esta búsqueda:');
                if (!name || !name.trim()) return;
                favorites = [...favorites, {
                    name: name.trim(),
                    query: state.query,
                    activeFilters: [...state.activeFilters],
                    groupKey: state.groupKey,
                }];
                saveFavorites(config.favoritesKey, favorites);
                const list = panel.querySelector('.search-bar-fav-list');
                list.innerHTML = favorites.map((f, i) => `
                    <div class="search-bar-fav-item" data-idx="${i}">
                        <span class="search-bar-fav-name">${f.name}</span>
                        <button type="button" class="search-bar-fav-remove" data-idx="${i}" aria-label="Eliminar favorito">&times;</button>
                    </div>
                `).join('');
            });
        }
        const favList = panel.querySelector('.search-bar-fav-list');
        if (favList) {
            favList.addEventListener('click', (e) => {
                const removeBtn = e.target.closest('.search-bar-fav-remove');
                if (removeBtn) {
                    e.stopPropagation();
                    favorites = favorites.filter((_, i) => i !== Number(removeBtn.dataset.idx));
                    saveFavorites(config.favoritesKey, favorites);
                    removeBtn.closest('.search-bar-fav-item').remove();
                    return;
                }
                const item = e.target.closest('.search-bar-fav-item');
                if (item) applyFavorite(favorites[Number(item.dataset.idx)]);
            });
        }
    }

    pillsEl.addEventListener('click', (e) => {
        const btn = e.target.closest('.search-bar-pill-remove');
        if (!btn) return;
        const pill = btn.closest('.search-bar-pill');
        const { kind, key } = pill.dataset;
        if (kind === 'filter') {
            state.activeFilters.delete(key);
            const cb = panel.querySelector(`input[data-filter-key="${key}"]`);
            if (cb) cb.checked = false;
        } else if (kind === 'group') {
            state.groupKey = null;
            const radio = panel.querySelector('input[data-group-key=""]');
            if (radio) radio.checked = true;
        }
        renderPills();
        emitChange();
    });

    // Reflejar el initialState en el DOM recien armado (checkboxes/radios/
    // input/pills) -- sin emitir change, el caller ya tiene ese estado.
    input.value = state.query;
    updateClearButton();
    if (panel) {
        panel.querySelectorAll('input[data-filter-key]').forEach((cb) => {
            cb.checked = state.activeFilters.has(cb.dataset.filterKey);
        });
        const groupRadio = panel.querySelector(`input[data-group-key="${state.groupKey || ''}"]`);
        if (groupRadio) groupRadio.checked = true;
    }
    renderPills();

    return {
        getState: () => ({ query: state.query, activeFilters: new Set(state.activeFilters), groupKey: state.groupKey }),
        setActiveFilters: (keys) => {
            state.activeFilters = new Set(keys);
            if (panel) {
                panel.querySelectorAll('input[data-filter-key]').forEach((cb) => {
                    cb.checked = state.activeFilters.has(cb.dataset.filterKey);
                });
            }
            renderPills();
            emitChange();
        },
    };
}

export function applySearchState(rows, state, config) {
    let result = rows;
    const searchFields = config.searchFields || [];

    if (state.query) {
        result = result.filter((row) => searchFields.some((f) => fuzzyMatch(f.value(row), state.query)));
    }

    if (state.activeFilters.size) {
        const activeDefs = (config.filters || []).filter((f) => state.activeFilters.has(f.key));
        result = result.filter((row) => activeDefs.every((f) => f.test(row)));
    }

    if (state.groupKey) {
        const groupDef = (config.groupBy || []).find((g) => g.key === state.groupKey);
        if (groupDef) {
            const groups = new Map();
            result.forEach((row) => {
                const key = groupDef.value(row);
                if (!groups.has(key)) groups.set(key, []);
                groups.get(key).push(row);
            });
            return { grouped: true, groups: [...groups.entries()].map(([label, items]) => ({ label, items })) };
        }
    }

    return { grouped: false, items: result };
}

// --- Filas de grupo colapsables con subtotal -------------------------------
// Compartido entre Tenencias y Posiciones (las dos tablas de Fase 2): cada
// columna puede declarar `aggregate: 'sum'|'avg'` + `value(row)` numerico +
// `format(n)` para que la fila de grupo muestre un subtotal en esa columna,
// en vez de perder esa info al colapsar.

export function groupRowHtml(group, columns, collapsed) {
    const cells = columns.map((c, i) => {
        if (i === 0) {
            return `<td class="table-group-cell">
                <button type="button" class="group-toggle" data-group="${group.label}">${collapsed ? '▸' : '▾'}</button>
                ${group.label} <span class="table-group-count">(${group.items.length})</span>
            </td>`;
        }
        if (!c.aggregate || !c.value) return '<td></td>';
        const values = group.items.map((r) => c.value(r)).filter((v) => v !== null && v !== undefined);
        if (!values.length) return '<td></td>';
        const sum = values.reduce((a, b) => a + b, 0);
        const agg = c.aggregate === 'avg' ? sum / values.length : sum;
        return `<td class="num table-group-cell">${c.format ? c.format(agg) : agg}</td>`;
    });
    return `<tr class="table-group-header" data-group-row="${group.label}">${cells.join('')}</tr>`;
}

// Arma las filas de <tbody> para los 3 casos que toda tabla con esta barra
// tiene que resolver igual: sin datos, agrupado (con fallback si el filtro
// deja 0 grupos -- sin esto, agrupar + un filtro que no matchea nada
// renderizaba un tbody vacio de verdad, sin ningun mensaje) y plano.
export function tableBodyRowsHtml(result, columns, rowFn, { collapsedGroups, colspan, emptyMessage = 'Sin resultados para este filtro.' } = {}) {
    const span = colspan ?? columns.length;
    if (result.grouped) {
        if (!result.groups.length) return `<tr><td colspan="${span}" class="table-empty">${emptyMessage}</td></tr>`;
        return result.groups.map((g) => {
            const collapsed = collapsedGroups.has(g.label);
            return groupRowHtml(g, columns, collapsed) + (collapsed ? '' : g.items.map(rowFn).join(''));
        }).join('');
    }
    return result.items.length ? result.items.map(rowFn).join('') : `<tr><td colspan="${span}" class="table-empty">${emptyMessage}</td></tr>`;
}

export function wireGroupToggles(container, collapsedGroups, onToggle) {
    container.querySelectorAll('.group-toggle').forEach((btn) => {
        btn.addEventListener('click', () => {
            const label = btn.dataset.group;
            if (collapsedGroups.has(label)) collapsedGroups.delete(label);
            else collapsedGroups.add(label);
            onToggle();
        });
    });
}
