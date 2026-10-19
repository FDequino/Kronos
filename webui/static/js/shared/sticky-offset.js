// Fase 18: la fila de subtabs de cada vista (.view > .subtabs) quedó fija
// arriba de todo ("no es que el buscador esté quieto, es todo lo de arriba
// con las tabs") -- el título/KPIs/buscador de cada panel (.panel-sticky-head)
// tiene que acoplarse justo debajo, nunca superponerse. En vez de un pixel
// fijo a mano (se rompe con cualquier cambio de fuente/tamaño/tema), se mide
// el alto real de la fila de subtabs de la vista ACTIVA con ResizeObserver y
// se expone como --subtabs-h en :root -- .panel-sticky-head la usa como su
// `top`.
let observer = null;
let observedEl = null;

function setOffset(height) {
    document.documentElement.style.setProperty('--subtabs-h', `${height}px`);
}

function observe(el) {
    if (observedEl === el) return;
    if (observer && observedEl) observer.unobserve(observedEl);
    observedEl = el;
    if (!el) {
        setOffset(0);
        return;
    }
    // entries[0].target.getBoundingClientRect() (no entries[0].contentRect)
    // a propósito, por dos motivos: 1) contentRect excluye el padding
    // (.subtabs tiene padding-top/bottom) y necesitamos el alto TOTAL
    // ocupado en pantalla para que .panel-sticky-head no quede superpuesto;
    // 2) el observer es un singleton reusado entre vistas (observe() cambia
    // de target al navegar) -- leer target en vez de cerrar sobre `el`
    // evita medir el elemento de la vista VIEJA tras ese cambio.
    if (!observer) {
        observer = new ResizeObserver((entries) => setOffset(entries[0].target.getBoundingClientRect().height));
    }
    observer.observe(el);
    setOffset(el.getBoundingClientRect().height);
}

function syncActiveView() {
    observe(document.querySelector('.view.active > .subtabs'));
}

export function initStickyOffset() {
    syncActiveView();
    window.addEventListener('hashchange', syncActiveView);
}
