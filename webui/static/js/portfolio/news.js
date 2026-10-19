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
//
// Una TARJETA = UNA noticia (ver conversación: "al presionar la tarjeta no
// entra a ningún lado... quiero que al presionar la tarjeta me lleve a un
// detalle de la noticia con todo") -- el backend devuelve una fila por
// TICKER con una lista de titulares adentro (db.list_today_sentiment),
// pero acá se aplana a una fila/tarjeta por TITULAR antes de buscar/
// filtrar/agrupar/pintar, para que "la tarjeta que ves" y "la noticia que
// se abre al clickearla" sean la misma cosa, sin anidar una lista de
// titulares adentro de cada tarjeta de ticker.
import { $ } from '../shared/dom.js';
import { fmtDate, fmtDateTime } from '../shared/format.js';
import { loadingHtml } from '../shared/loading.js';
import { api } from './api.js';
import { createSearchBar, applySearchState, tableBodyRowsHtml, wireGroupToggles } from '../shared/search-bar.js';
import { openModal, closeModal } from '../shared/modal.js';

const NEWS_RISK_TAG_CLASS = { BAJO: 'tag-positive', MEDIO: 'tag-warning', ALTO: 'tag-negative' };
const IMPACT_TAG_CLASS = { positivo: 'tag-positive', negativo: 'tag-negative', neutral: 'tag-neutral' };

const NEWS_SEARCH_CONFIG = {
    searchFields: [
        { key: 'ticker', label: 'Ticker', value: (r) => r.ticker },
        { key: 'title', label: 'Título', value: (r) => r.title || '' },
    ],
    filters: [
        { key: 'bajo', label: 'Riesgo bajo', block: 'Riesgo', test: (r) => r.risk_level === 'BAJO' },
        { key: 'medio', label: 'Riesgo medio', block: 'Riesgo', test: (r) => r.risk_level === 'MEDIO' },
        { key: 'alto', label: 'Riesgo alto', block: 'Riesgo', test: (r) => r.risk_level === 'ALTO' },
        { key: 'con-evento', label: 'Con próximo balance', block: 'Evento', test: (r) => !!r.event_date },
    ],
    groupBy: [
        { key: 'ticker', label: 'Ticker', value: (r) => r.ticker },
        { key: 'risk_level', label: 'Nivel de riesgo', value: (r) => r.risk_level },
    ],
};

let currentEntries = [];
let newsSearchState = { query: '', activeFilters: new Set(), groupKey: null };
let newsView = 'cards'; // 'cards' | 'list', mismo patrón que Oportunidades del Día
const newsCollapsedGroups = new Set();

// Cada titular es {"title", "url", "summary", "source", "published_at",
// "impact_score", "impact_direction", "impact_magnitude"} desde el backend
// (headlines viejos ya persistidos como string antes de este cambio, o sin
// esos campos, degradan a "sin evaluar" en el modal, nunca rompen el
// render). Una fila por titular, con el ticker/riesgo/evento de su
// instrumento ya resueltos -- esto es lo que se busca/filtra/agrupa/pinta.
function flattenNewsItems(entries) {
    const items = [];
    entries.forEach((entry) => {
        if (!entry.headlines.length) {
            items.push({
                ticker: entry.ticker, risk_level: entry.risk_level, event_date: entry.event_date, headline: null,
            });
            return;
        }
        entry.headlines.forEach((h, idx) => {
            const title = typeof h === 'object' && h !== null ? h.title : h;
            items.push({
                ticker: entry.ticker, risk_level: entry.risk_level, event_date: entry.event_date,
                headline: h, headlineIdx: idx, title,
            });
        });
    });
    return items;
}

function riskTagHtml(item) {
    return `<span class="tag ${NEWS_RISK_TAG_CLASS[item.risk_level] || 'tag-neutral'}">${item.risk_level}</span>`;
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

// Resumen corto para la vista previa de la tarjeta/fila (el detalle
// completo vive en el modal, ver newsDetailModalHtml) -- recortado para que
// la tarjeta no crezca de más con resúmenes largos (ver conversación,
// "agrandaste las tarjetas").
function summaryPreview(summary, limit = 140) {
    if (!summary) return '';
    return summary.length > limit ? `${summary.slice(0, limit).trimEnd()}…` : summary;
}

function newsItemCardHtml(item) {
    const detailHref = `#detalle/${encodeURIComponent(item.ticker)}`;
    if (!item.headline) {
        return `
            <div class="opportunity-card">
                <div class="opportunity-card-head">
                    <a href="${detailHref}" class="opportunity-card-symbol table-link">${item.ticker}</a>
                    ${riskTagHtml(item)}
                </div>
                <p class="table-empty">Sin titulares disponibles hoy.</p>
            </div>
        `;
    }
    const h = item.headline;
    const url = typeof h === 'object' && h !== null ? h.url : null;
    const source = typeof h === 'object' && h !== null ? h.source : null;
    const publishedAt = typeof h === 'object' && h !== null ? h.published_at : null;
    const direction = typeof h === 'object' && h !== null ? h.impact_direction : null;
    const summary = typeof h === 'object' && h !== null ? h.summary : null;
    const metaParts = [
        source, publishedAt ? fmtDateTime(publishedAt) : null,
        item.event_date ? `Próximo balance: ${fmtDate(item.event_date)}` : null,
    ].filter(Boolean);
    const impactTag = direction ? `<span class="tag ${IMPACT_TAG_CLASS[direction] || 'tag-neutral'}">${direction.toUpperCase()}</span>` : '';
    return `
        <div class="opportunity-card news-item-card" data-ticker="${item.ticker}" data-headline-idx="${item.headlineIdx}">
            <div class="opportunity-card-head">
                <a href="${detailHref}" class="opportunity-card-symbol table-link" data-skip-card-click>${item.ticker}</a>
                ${riskTagHtml(item)} ${impactTag}
            </div>
            <p class="news-item-title">${item.title}</p>
            ${metaParts.length ? `<p class="hint">${metaParts.join(' · ')}</p>` : ''}
            ${summary ? `<p class="hint">${summaryPreview(summary)}</p>` : ''}
            ${url ? `<a href="${url}" target="_blank" rel="noopener noreferrer" class="detail-news-source-link" data-skip-card-click>Fuente ↗</a>` : ''}
        </div>
    `;
}

const NEWS_LIST_COLUMNS = [{}, {}, {}, {}]; // solo para colspan del "sin resultados", ver tableBodyRowsHtml

function newsItemRowHtml(item) {
    const detailHref = `#detalle/${encodeURIComponent(item.ticker)}`;
    if (!item.headline) {
        return `
            <tr>
                <td><a href="${detailHref}" class="table-link">${item.ticker}</a></td>
                <td>${riskTagHtml(item)}</td>
                <td colspan="2" class="table-empty">Sin titulares disponibles hoy.</td>
            </tr>
        `;
    }
    const h = item.headline;
    const url = typeof h === 'object' && h !== null ? h.url : null;
    const direction = typeof h === 'object' && h !== null ? h.impact_direction : null;
    const impactTag = direction ? `<span class="tag ${IMPACT_TAG_CLASS[direction] || 'tag-neutral'}">${direction.toUpperCase()}</span>` : '<span class="hint">—</span>';
    return `
        <tr class="news-item-row" data-ticker="${item.ticker}" data-headline-idx="${item.headlineIdx}">
            <td><a href="${detailHref}" class="table-link" data-skip-card-click>${item.ticker}</a></td>
            <td>${riskTagHtml(item)}</td>
            <td><span title="${item.title}">${item.title}</span></td>
            <td>${impactTag} ${url ? `<a href="${url}" target="_blank" rel="noopener noreferrer" class="detail-news-source-link" data-skip-card-click>Fuente ↗</a>` : ''}</td>
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
    const flatItems = flattenNewsItems(currentEntries);
    const result = applySearchState(flatItems, newsSearchState, NEWS_SEARCH_CONFIG);

    if (newsView === 'list') {
        const bodyRows = tableBodyRowsHtml(result, NEWS_LIST_COLUMNS, newsItemRowHtml, {
            collapsedGroups: newsCollapsedGroups,
            colspan: 4,
            emptyMessage: 'Ningún resultado para este filtro.',
        });
        container.innerHTML = `
            <div class="table-scroll">
                <table>
                    <thead><tr><th>Ticker</th><th>Riesgo</th><th>Titular</th><th>Impacto / Fuente</th></tr></thead>
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
                ${newsCollapsedGroups.has(g.label) ? '' : `<div class="opportunity-cards-grid" style="margin-bottom:16px;">${g.items.map(newsItemCardHtml).join('')}</div>`}
            `).join('')
            : '<p class="table-empty">Ningún resultado para este filtro.</p>';
        wireGroupToggles(container, newsCollapsedGroups, renderNewsToday);
        return;
    }

    container.innerHTML = result.items.length
        ? `<div class="opportunity-cards-grid">${result.items.map(newsItemCardHtml).join('')}</div>`
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
        searchPlaceholder: 'Buscar por ticker o título...',
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
    // renderNewsToday, que reemplaza el innerHTML entero) -- clickear
    // CUALQUIER PARTE de la tarjeta/fila de una noticia abre su detalle
    // completo; el ticker y el link de "Fuente" (data-skip-card-click)
    // siguen su propio comportamiento sin disparar también el modal (ver
    // conversación: "al presionar la tarjeta no entra a ningún lado...
    // quiero que al presionar la tarjeta me lleve a un detalle de la
    // noticia con todo").
    const newsContainer = $('news-today-container');
    if (newsContainer) {
        newsContainer.addEventListener('click', (e) => {
            if (e.target.closest('[data-skip-card-click]')) return;
            const item = e.target.closest('.news-item-card, .news-item-row');
            if (!item || item.dataset.headlineIdx === undefined) return;
            const entry = currentEntries.find((x) => x.ticker === item.dataset.ticker);
            const headline = entry?.headlines[Number(item.dataset.headlineIdx)];
            if (headline) openNewsDetailModal(entry.ticker, headline);
        });
    }
}
