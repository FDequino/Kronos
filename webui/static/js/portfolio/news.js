// Noticias y Sentimiento (ver conversación: "no hay sección de noticias" /
// "dentro de cada instrumento las que le afectan") -- subtab de Decisiones,
// agrega la última evaluación de HOY por símbolo del Agente NLP (ver
// portfolio/nlp_agent.py). Mismo dato que ya alimenta el filtro de riesgo
// preventivo del Motor Satélite y el badge de las tarjetas de
// Oportunidades/Propuestas -- acá se ve completo (titulares reales, no
// solo el badge). GET /api/portfolio/news-today.
import { $ } from '../shared/dom.js';
import { fmtDate } from '../shared/format.js';
import { loadingHtml } from '../shared/loading.js';
import { api } from './api.js';

const NEWS_RISK_TAG_CLASS = { BAJO: 'tag-positive', MEDIO: 'tag-warning', ALTO: 'tag-negative' };

function entryHtml(entry) {
    const riskTag = `<span class="tag ${NEWS_RISK_TAG_CLASS[entry.risk_level] || 'tag-neutral'}">${entry.risk_level}</span>`;
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
    const headlinesHtml = entry.headlines.length
        ? `<ul class="detail-news-list">${entry.headlines.slice(0, 5).map((h) => `<li>${h}</li>`).join('')}</ul>`
        : '<p class="table-empty">Sin titulares disponibles hoy.</p>';
    return `
        <div class="opportunity-card">
            <div class="opportunity-card-head">
                <a href="${detailHref}" class="opportunity-card-symbol table-link">${entry.ticker}</a>
                ${riskTag}
            </div>
            <p class="hint">${entry.summary}</p>
            ${eventLine}
            ${headlinesHtml}
        </div>
    `;
}

function renderNewsToday(entries) {
    const container = $('news-today-container');
    if (!container) return;
    container.innerHTML = entries.length
        ? `<div class="opportunity-cards-grid">${entries.map(entryHtml).join('')}</div>`
        : '<p class="table-empty">Ningún instrumento fue evaluado todavía hoy — se llena a medida que el Motor Satélite evalúa señales o entrás al detalle de un instrumento.</p>';
}

let loaded = false;

async function loadNewsToday() {
    const container = $('news-today-container');
    if (!container) return;
    container.innerHTML = loadingHtml('Cargando noticias de hoy…');
    try {
        const { data } = await api.newsToday();
        renderNewsToday(data);
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

    const refreshBtn = $('refresh-news-today-btn');
    if (refreshBtn) {
        refreshBtn.addEventListener('click', () => {
            loaded = true;
            loadNewsToday();
        });
    }
}
