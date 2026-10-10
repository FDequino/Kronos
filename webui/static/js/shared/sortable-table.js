export function createSorter() {
    let sortKey = null;
    let sortDir = 1;

    return {
        toggle(key) {
            sortDir = sortKey === key ? -sortDir : 1;
            sortKey = key;
        },
        set(key, dir) {
            sortKey = key;
            sortDir = dir;
        },
        apply(rows, valueFn) {
            if (!sortKey) return rows;
            return [...rows].sort((a, b) => {
                const av = valueFn(a, sortKey);
                const bv = valueFn(b, sortKey);
                if (av === null || av === undefined) return 1;
                if (bv === null || bv === undefined) return -1;
                if (typeof av === 'string') return av.localeCompare(bv) * sortDir;
                return (av - bv) * sortDir;
            });
        },
        get key() { return sortKey; },
        get dir() { return sortDir; },
    };
}

export function wireSortableHeaders(root, sorter, onChange) {
    root.querySelectorAll('th.sortable').forEach((th) => {
        th.addEventListener('click', () => {
            sorter.toggle(th.dataset.sort);
            onChange();
        });
    });
}
