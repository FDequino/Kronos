import { $ } from './dom.js';
import { computeAlerts } from '../portfolio/alerts.js';
import { glossaryText } from './glossary.js';

const KIND_TAG = { vencido: 'tag-negative', bono_vencido: 'tag-negative', vencimiento: 'tag-warning', cobro: 'tag-positive', senal: 'tag-negative', sltp: 'tag-negative' };
const KIND_LABEL = { vencido: 'Vencido', bono_vencido: 'Vencido', vencimiento: 'Vence pronto', cobro: 'Cobro', senal: 'Señal', sltp: 'SL/TP' };
const KIND_TERM = { vencimiento: 'notif_vence', vencido: 'notif_vencido', bono_vencido: 'notif_bono_vencido', cobro: 'notif_cobro', senal: 'notif_senal', sltp: 'notif_sltp' };

// Estado de leido/no-leido, solo en este navegador -- mismo patron ya usado
// para favoritos/columnas de las tablas, no hace falta backend para esto.
// Antes no existia ningun concepto de "visto": el badge siempre mostraba el
// total de alertas vigentes, nunca bajaba aunque ya las hubieras revisado.
const SEEN_KEY = 'kronos-notifications-seen';

function loadSeen() {
    try {
        return new Set(JSON.parse(localStorage.getItem(SEEN_KEY)) || []);
    } catch {
        return new Set();
    }
}

function saveSeen(seen) {
    try {
        localStorage.setItem(SEEN_KEY, JSON.stringify([...seen]));
    } catch {
        // localStorage no disponible (privado/bloqueado) -- se pierde la persistencia, no rompe nada
    }
}

let currentItems = [];
let seen = loadSeen();

// href explícito (ver conversación, Fase 7) en vez de inferirlo siempre de
// `symbol` -- las alertas de préstamos no tienen un instrumento al que
// linkear (#detalle/SYMBOL no existe para un préstamo), pero sí tienen una
// vista propia (#prestamos) a la que antes no llevaban a ningún lado.
function alertRowHtml(a) {
    const href = a.href || (a.symbol ? `#detalle/${a.symbol}` : null);
    const text = href ? `<a href="${href}" class="table-link">${a.text}</a>` : a.text;
    const isSeen = seen.has(a.id);
    return `<div class="alert-row${isSeen ? ' alert-row-seen' : ''}" data-alert-id="${a.id}">
        <span class="tag ${KIND_TAG[a.kind]}" title="${glossaryText(KIND_TERM[a.kind])}">${KIND_LABEL[a.kind]}</span>
        <span class="alert-text">${text}</span>
    </div>`;
}

function updateBadge() {
    const unseenCount = currentItems.filter((a) => !seen.has(a.id)).length;
    const badge = $('notifications-badge');
    if (unseenCount) {
        badge.hidden = false;
        badge.textContent = unseenCount;
    } else {
        badge.hidden = true;
    }
}

function renderList() {
    $('notifications-list').innerHTML = currentItems.length
        ? currentItems.map(alertRowHtml).join('')
        : '<p class="table-empty">Nada necesita tu atención ahora.</p>';
}

async function refresh() {
    try {
        currentItems = await computeAlerts();
    } catch {
        currentItems = [];
    }
    // Las alertas vistas que ya no aparecen en la corrida actual (ej. el
    // vencimiento paso o se renovo) no tiene sentido seguir arrastrandolas
    // en el set guardado -- se poda a las que siguen vigentes.
    const stillPresent = new Set(currentItems.map((a) => a.id));
    seen = new Set([...seen].filter((id) => stillPresent.has(id)));
    saveSeen(seen);
    updateBadge();
    renderList();
}

export function initNotifications() {
    $('notifications-trigger').addEventListener('click', (e) => {
        e.stopPropagation();
        const panel = $('notifications-panel');
        $('user-menu-panel').hidden = true;
        panel.hidden = !panel.hidden;
    });
    $('notifications-list').addEventListener('click', (e) => {
        const row = e.target.closest('.alert-row');
        if (!row) return;
        seen.add(row.dataset.alertId);
        saveSeen(seen);
        row.classList.add('alert-row-seen');
        updateBadge();
        // El panel se quedaba abierto tapando la pantalla de destino al
        // navegar desde un aviso (ver conversación/auditoría, hallazgo
        // 2.11) -- se cierra apenas el click toca el link de la fila.
        if (e.target.closest('a')) $('notifications-panel').hidden = true;
    });
    document.addEventListener('click', (e) => {
        if (!e.target.closest('.header-notifications')) $('notifications-panel').hidden = true;
    });
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') $('notifications-panel').hidden = true;
    });
    refresh();
}
