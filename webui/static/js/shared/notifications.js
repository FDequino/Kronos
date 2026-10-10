import { $ } from './dom.js';
import { computeAlerts } from '../portfolio/alerts.js';

const KIND_TAG = { vencido: 'tag-negative', vencimiento: 'tag-neutral', cobro: 'tag-positive', senal: 'tag-negative' };
const KIND_LABEL = { vencido: 'Vencido', vencimiento: 'Vence pronto', cobro: 'Cobro', senal: 'Señal' };

function alertRowHtml(a) {
    const text = a.symbol ? `<a href="#detalle/${a.symbol}" class="table-link">${a.text}</a>` : a.text;
    return `<div class="alert-row">
        <span class="tag ${KIND_TAG[a.kind]}">${KIND_LABEL[a.kind]}</span>
        <span class="alert-text">${text}</span>
    </div>`;
}

async function refresh() {
    let items = [];
    try {
        items = await computeAlerts();
    } catch {
        items = [];
    }
    const badge = $('notifications-badge');
    if (items.length) {
        badge.hidden = false;
        badge.textContent = items.length;
    } else {
        badge.hidden = true;
    }
    $('notifications-list').innerHTML = items.length
        ? items.map(alertRowHtml).join('')
        : '<p class="table-empty">Nada necesita tu atención ahora.</p>';
}

export function initNotifications() {
    $('notifications-trigger').addEventListener('click', (e) => {
        e.stopPropagation();
        const panel = $('notifications-panel');
        $('user-menu-panel').hidden = true;
        panel.hidden = !panel.hidden;
    });
    document.addEventListener('click', (e) => {
        if (!e.target.closest('.header-notifications')) $('notifications-panel').hidden = true;
    });
    refresh();
}
