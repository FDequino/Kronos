// Noticias y Sentimiento (ver conversación: "no hay sección de noticias" /
// "dentro de cada instrumento las que le afectan") -- subtab de Decisiones,
// agrega la última evaluación de HOY por símbolo del Agente NLP (ver
// portfolio/nlp_agent.py). Mismo dato que ya alimenta el filtro de riesgo
// preventivo del Motor Satélite y el badge de las tarjetas de
// Oportunidades/Propuestas -- acá se ve completo (titulares reales, no
// solo el badge). GET /api/portfolio/news-today.
//
// Vista de lista + buscador/filtro/agrupar (ver conversación: "tiene que
// tener vista de lista, buscador, filtro y agrupar = como las otras
// pestañas") -- mismo patrón createSearchBar/applySearchState que
// Instrumentos/Préstamos/Propuestas, con el mismo toggle Tarjetas/Lista
// que ya tenía Oportunidades del Día.
import { $ } from '../shared/dom.js';
import { fmtDate, fmtDateTime } from '../shared/format.js';
import { loadingHtml } from '../shared/loading.js';
import { api } from './api.js';
import { createSearchBar, applySearchState, tableBodyRowsHtml, wireGroupToggles } from '../shared/search-bar.js';
import { openModal, closeModal } from '../shared/modal.js';

const NEWS_RISK_TAG_CLASS = { BAJO: 'tag-positive', MEDIO: 'tag-warning', ALTO: 'tag-negative' };
const IMPACT_TAG_CLASS = { positivo: 'tag-positive', negativo: 'tag-negative', neutral: 'tag-neutral' };

const NEWS_SEARCH_CONFIG = {
    searchFields: [{ key: 'ticker', label: 'Ticker', value: (r) => r.ticker }],
    filters: [
        { key: 'bajo', label: 'Riesgo bajo', block: 'Riesgo', test: (r) => r.risk_level === 'BAJO' },
        { key: 'medio', label: 'Riesgo medio', block: 'Riesgo', test: (r) => r.risk_level === 'MEDIO' },
        { key: 'alto', label: 'Riesgo alto', block: 'Riesgo', test: (r) => r.risk_level === 'ALTO' },
        { key: 'con-evento', label: 'Con próximo balance', block: 'Evento', test: (r) => !!r.event_date },
    ],
    groupBy: [
        { key: 'risk_level', label: 'Nivel de riesgo', value: (r) => r.risk_level },
    ],
};

let currentEntries = [];
let newsSearchState = { query: '', activeFilters: new Set(), groupKey: null };
let newsView = 'cards'; // 'cards' | 'list', mismo patrón que Oportunidades del Día
const newsCollapsedGroups = new Set();

// Cada titular es {"title", "url", "summary", "source", "published_at",
// "impact_score", "impact_direction", "impact_magnitude"} desde el backend
// (ver conversación: "debo poder entrar a las mismas" / "no puedo entrar a
// la tarjeta... para ver un apartado de la noticia... quiero que esté el
// impacto"). El titular se muestra como texto (no como link), el link de
// fuente queda aparte y chico, y hacer click en la fila abre un detalle con
// resumen + impacto estimado -- headlines viejos ya persistidos como string
// antes de este cambio, o sin esos campos, degradan a "sin evaluar" en el
// modal, nunca rompen el render.
function headlineItemHtml(h, ticker, idx) {
    const title = typeof h === 'object' && h !== null ? h.title : h;
    const url = typeof h === 'object' && h !== null ? h.url : null;
    return `
        <li class="detail-news-item" data-ticker="${ticker}" data-headline-idx="${idx}">
            <span class="detail-news-title" title="${title}">${title}</span>
            ${url ? `<a href="${url}" target="_blank" rel="noopener noreferrer" class="detail-news-source-link">Fuente ↗</a>` : ''}
        </li>
    `;
}

function headlinesHtml(entry, limit) {
    return entry.headlines.length
        ? `<ul class="detail-news-list">${entry.headlines.slice(0, limit).map((h, idx) => headlineItemHtml(h, entry.ticker, idx)).join('')}</ul>`
        : '<p class="table-empty">Sin titulares disponibles hoy.</p>';
}

function newsDetailModalHtml(h) {
    const title = typeof h === 'object' && h !== null ? h.title : h;
    const url = typeof h === 'object' && h !== null ? h.url : null;
    const summary = typeof h === 'object' && h !== null ? h.summary : null;
    const source = typeof h === 'object' && h !== null ? h.source : null;
    const publishedAt = typeof h === 'object' && h !== null ? h.published_at : null;
    const direction = typeof h === 'object' && h !== null ? h.impact_direction : null;
    const magnitude = typeof h === 'object' && h !== null ? h.impact_magnitude : null;
    const metaParts = [source, publishedAt ? fmtDateTime(publishedAt) : null].filter(Boolean);
    const impactHtml = direction
        ? `<p><span class="tag ${IMPACT_TAG_CLASS[direction] || 'tag-neutral'}">${direction.toUpperCase()}</span> <span class="hint">impacto estimado: ${magnitude}</span></p>`
        : '<p class="hint">Impacto no evaluado para este titular (guardado antes de este análisis).</p>';
    return `
        <h3 style="margin:0 0 8px;">${title}</h3>
        ${metaParts.length ? `<p class="hint">${metaParts.join(' · ')}</p>` : ''}
        ${impactHtml}
        ${summary ? `<p>${summary}</p>` : '<p class="hint">Sin resumen disponible para este titular.</p>'}
        <div class="modal-footer">
            ${url ? `<a href="${url}" target="_blank" rel="noopener noreferrer" class="btn btn-secondary">Ver fuente ↗</a>` : ''}
            <button type="button" class="btn btn-secondary modal-close-btn">Cerrar</button>
        </div>
    `;
}

function openNewsDetailModal(ticker, h) {
    openModal({
        title: `${ticker} — noticia`,
        bodyHtml: newsDetailModalHtml(h),
        onMount: (form) => form.querySelector('.modal-close-btn')?.addEventListener('click', () => closeModal()),
    });
}

function riskTagHtml(entry) {
    return `<span class="tag ${NEWS_RISK_TAG_CLASS[entry.risk_level] || 'tag-neutral'}">${entry.risk_level}</span>`;
}

function entryCardHtml(entry) {
    const detailHref = `#detalle/${encodeURIComponent(entry.ticker)}`;
    // Sin "en Xhs" acá (a diferencia del panel de instrumento, ver
    // instrument-detail.js::newsHtml) -- este dato viene de
    // db.list_today_sentiment, una fila cruda de news_sentiment_log, que
    // nunca persiste hours_until_event (es un valor que depende del
    // momento exacto en que se mira, no algo que tenga sentido guardar
    // fijo) -- el detalle de horas ya está adentro de `summary`, calculado
    // en el momento real de la corrida.
    const eventLine = entry.event_date
        ? `<p class="hint">Próximo balance: ${fmtDate(entry.event_date)}</p>`
        : '';
    return `
        <div class="opportunity-card">
            <div class="opportunity-card-head">
                <a href="${detailHref}" class="opportunity-card-symbol table-link">${entry.ticker}</a>
                ${riskTagHtml(entry)}
            </div>
            <p class="hint">${entry.summary}</p>
            ${eventLine}
            ${headlinesHtml(entry, 5)}
        </div>
    `;
}

const NEWS_LIST_COLUMNS = [{}, {}, {}, {}, {}]; // solo para colspan del "sin resultados", ver tableBodyRowsHtml

function entryRowHtml(entry) {
    const detailHref = `#detalle/${encodeURIComponent(entry.ticker)}`;
    return `
        <tr>
            <td><a href="${detailHref}" class="table-link">${entry.ticker}</a></td>
            <td>${riskTagHtml(entry)}</td>
            <td>${entry.summary}</td>
            <td>${entry.event_date ? fmtDate(entry.event_date) : '—'}</td>
            <td>${headlinesHtml(entry, 3)}</td>
        </tr>
    `;
}

function renderNewsToday() {
    const container = $('news-today-container');
    if (!container) return;
    if (!currentEntries.length) {
        container.innerHTML = '<p class="table-empty">Ningún instrumento fue evaluado todavía hoy — se llena a medida que el Motor Satélite evalúa señales o entrás al detalle de un instrumento.</p>';
        return;
    }
    const result = applySearchState(currentEntries, newsSearchState, NEWS_SEARCH_CONFIG);

    if (newsView === 'list') {
        const bodyRows = tableBodyRowsHtml(result, NEWS_LIST_COLUMNS, entryRowHtml, {
            collapsedGroups: newsCollapsedGroups,
            colspan: 5,
            emptyMessage: 'Ningún resultado para este filtro.',
        });
        container.innerHTML = `
            <div class="table-scroll">
                <table>
                    <thead><tr><th>Ticker</th><th>Riesgo</th><th>Resumen</th><th>Próximo balance</th><th>Titulares</th></tr></thead>
                    <tbody>${bodyRows}</tbody>
                </table>
            </div>
        `;
        wireGroupToggles(container, newsCollapsedGroups, renderNewsToday);
        return;
    }

    if (result.grouped) {
        container.innerHTML = result.groups.length
            ? result.groups.map((g) => `
                <div class="table-group-cell" style="margin-bottom:8px;">
                    <button type="button" class="group-toggle" data-group="${g.label}">${newsCollapsedGroups.has(g.label) ? '▸' : '▾'}</button>
                    ${g.label} <span class="table-group-count">(${g.items.length})</span>
                </div>
                ${newsCollapsedGroups.has(g.label) ? '' : `<div class="opportunity-cards-grid" style="margin-bottom:16px;">${g.items.map(entryCardHtml).join('')}</div>`}
            `).join('')
            : '<p class="table-empty">Ningún resultado para este filtro.</p>';
        wireGroupToggles(container, newsCollapsedGroups, renderNewsToday);
        return;
    }

    container.innerHTML = result.items.length
        ? `<div class="opportunity-cards-grid">${result.items.map(entryCardHtml).join('')}</div>`
        : '<p class="table-empty">Ningún resultado para este filtro.</p>';
}

let loaded = false;

async function loadNewsToday() {
    const container = $('news-today-container');
    if (!container) return;
    container.innerHTML = loadingHtml('Cargando noticias de hoy…');
    try {
        const { data } = await api.newsToday();
        currentEntries = data;
        renderNewsToday();
    } catch (error) {
        container.innerHTML = `<p class="table-empty">No se pudieron cargar las noticias: ${error.response?.data?.error || error.message}</p>`;
    }
}

async function loadNewsTodayIfNeeded() {
    if (loaded) return;
    loaded = true;
    await loadNewsToday();
}

export function initNewsView() {
    const navBtn = document.querySelector('[data-subtabs="decisiones"] [data-subtab-target="noticias"]');
    if (navBtn) navBtn.addEventListener('click', loadNewsTodayIfNeeded);
    if (document.querySelector('[data-subtab-panel="noticias"]')?.classList.contains('active')) loadNewsTodayIfNeeded();

    createSearchBar($('news-search-bar'), {
        ...NEWS_SEARCH_CONFIG,
        searchPlaceholder: 'Buscar por ticker...',
        initialState: newsSearchState,
        onChange: (state) => { newsSearchState = state; renderNewsToday(); },
    });

    const viewToggle = $('news-view-toggle');
    if (viewToggle) {
        viewToggle.addEventListener('click', (e) => {
            const btn = e.target.closest('.segmented-toggle-option');
            if (!btn || btn.dataset.view === newsView) return;
            newsView = btn.dataset.view;
            viewToggle.querySelectorAll('.segmented-toggle-option').forEach((b) => b.classList.toggle('active', b === btn));
            renderNewsToday();
        });
    }

    const refreshBtn = $('refresh-news-today-btn');
    if (refreshBtn) {
        refreshBtn.addEventListener('click', () => {
            loaded = true;
            loadNewsToday();
        });
    }

    // Delegado sobre el contenedor (sobrevive a cada re-render de
    // renderNewsToday, que reemplaza el innerHTML entero) -- clickear la
    // fila/tarjeta de un titular abre su detalle; clickear el link de
    // "Fuente" sigue abriendo la fuente externa normal, sin disparar
    // también el modal (ver conversación: "no puedo entrar a la tarjeta o
    // fila para ver... un apartado de la noticia").
    const newsContainer = $('news-today-container');
    if (newsContainer) {
        newsContainer.addEventListener('click', (e) => {
            if (e.target.closest('.detail-news-source-link')) return;
            const item = e.target.closest('.detail-news-item');
            if (!item) return;
            const entry = currentEntries.find((x) => x.ticker === item.dataset.ticker);
            const headline = entry?.headlines[Number(item.dataset.headlineIdx)];
            if (headline) openNewsDetailModal(entry.ticker, headline);
        });
    }
}
