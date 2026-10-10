import { renderInstrumentDetail } from './instrument-detail.js';

const VIEWS = ['resumen', 'senales', 'instrumentos', 'transacciones', 'plazos-fijos', 'explorador'];
const DETAIL_PREFIX = 'detalle/';

function activateView(hash) {
    const isDetail = hash.startsWith(DETAIL_PREFIX);
    const target = isDetail ? 'detalle' : (VIEWS.includes(hash) ? hash : VIEWS[0]);
    document.querySelectorAll('.view').forEach((el) => {
        el.classList.toggle('active', el.id === `view-${target}`);
    });
    if (isDetail) renderInstrumentDetail(hash);
}

export function initTabs() {
    if (!window.location.hash) {
        history.replaceState(null, '', `#${VIEWS[0]}`);
    }
    activateView(window.location.hash.slice(1));
    window.addEventListener('hashchange', () => activateView(window.location.hash.slice(1)));
}
