let uidCounter = 0;

const SEARCH_ICON = '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="8.5" cy="8.5" r="5.5"/><path d="m16 16-3.5-3.5"/></svg>';
const CARET_ICON = '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="m6 8 4 4 4-4"/></svg>';

export function createSearchBar(container, config) {
    const uid = ++uidCounter;
    container.classList.add('search-bar-slot');
    const filters = config.filters || [];
    const groupBy = config.groupBy || [];
    const state = { query: '', activeFilters: new Set(), groupKey: null };

    container.innerHTML = `
        <div class="search-bar">
            <div class="search-bar-input-wrap">
                <span class="search-bar-icon">${SEARCH_ICON}</span>
                <div class="search-bar-pills"></div>
                <input type="text" class="search-bar-input" placeholder="${config.searchPlaceholder || 'Buscar...'}">
                ${(filters.length || groupBy.length) ? `<button type="button" class="search-bar-caret" aria-label="Filtros y agrupamiento">${CARET_ICON}</button>` : ''}
            </div>
            ${(filters.length || groupBy.length) ? `<div class="search-bar-panel" hidden>
                ${filters.length ? `<div class="search-bar-section">
                    <div class="search-bar-section-title">Filtros</div>
                    ${filters.map((f) => `<label class="search-bar-option"><input type="checkbox" data-filter-key="${f.key}"> ${f.label}</label>`).join('')}
                </div>` : ''}
                ${groupBy.length ? `<div class="search-bar-section">
                    <div class="search-bar-section-title">Agrupar por</div>
                    <label class="search-bar-option"><input type="radio" name="groupby-${uid}" data-group-key="" checked> Ninguno</label>
                    ${groupBy.map((g) => `<label class="search-bar-option"><input type="radio" name="groupby-${uid}" data-group-key="${g.key}"> ${g.label}</label>`).join('')}
                </div>` : ''}
            </div>` : ''}
        </div>
    `;

    const input = container.querySelector('.search-bar-input');
    const caret = container.querySelector('.search-bar-caret');
    const panel = container.querySelector('.search-bar-panel');
    const pillsEl = container.querySelector('.search-bar-pills');

    function emitChange() {
        config.onChange?.({ query: state.query, activeFilters: new Set(state.activeFilters), groupKey: state.groupKey });
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
            <span class="search-bar-pill" data-kind="${p.kind}" data-key="${p.key}">${p.label}<button type="button" class="search-bar-pill-remove" aria-label="Quitar">&times;</button></span>
        `).join('');
    }

    input.addEventListener('input', () => {
        state.query = input.value;
        emitChange();
    });

    if (caret) {
        caret.addEventListener('click', (e) => {
            e.stopPropagation();
            panel.hidden = !panel.hidden;
        });
        document.addEventListener('click', (e) => {
            if (!container.contains(e.target)) panel.hidden = true;
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

    if (state.query) {
        const q = state.query.toLowerCase();
        result = result.filter((row) => (config.searchFields(row) || []).some((v) => String(v ?? '').toLowerCase().includes(q)));
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
