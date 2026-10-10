export function createColumnManager(storageKey, defaultColumns) {
    const known = new Set(defaultColumns.map((c) => c.key));
    const byKey = new Map(defaultColumns.map((c) => [c.key, c]));
    let order = defaultColumns.map((c) => c.key);
    let hidden = new Set();

    try {
        const saved = JSON.parse(localStorage.getItem(storageKey) || 'null');
        if (saved && Array.isArray(saved.order)) {
            order = saved.order.filter((k) => known.has(k));
            defaultColumns.forEach((c) => { if (!order.includes(c.key)) order.push(c.key); });
            hidden = new Set((saved.hidden || []).filter((k) => known.has(k)));
        }
    } catch {
        // ignore corrupt storage, fall back to defaults
    }

    function persist() {
        localStorage.setItem(storageKey, JSON.stringify({ order, hidden: [...hidden] }));
    }

    return {
        visibleColumns() {
            return order.filter((k) => !hidden.has(k)).map((k) => byKey.get(k));
        },
        allColumns() {
            return order.map((k) => byKey.get(k));
        },
        isHidden(key) {
            return hidden.has(key);
        },
        toggleVisible(key) {
            if (hidden.has(key)) hidden.delete(key); else hidden.add(key);
            persist();
        },
        moveColumn(key, beforeKey) {
            if (key === beforeKey) return;
            order = order.filter((k) => k !== key);
            const idx = order.indexOf(beforeKey);
            order.splice(idx === -1 ? order.length : idx, 0, key);
            persist();
        },
    };
}
