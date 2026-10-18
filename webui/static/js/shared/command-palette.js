// Paleta de comandos global (Ctrl/Cmd+K, ver conversación, Fase 4) -- saltar
// directo a cualquier instrumento del universo BYMA buscable (mismo
// endpoint que ya alimenta Instrumentos, GET /explorer/search, sin cambios
// de backend) desde CUALQUIER pantalla, no solo con el buscador de
// Instrumentos ya filtrado a la vista actual. Usa axios global (cargado en
// base.html antes que los módulos) en vez de portfolio/api.js a propósito --
// este módulo se inicializa en base.html, que también sirve páginas fuera de
// /portfolio (ej. Predictor), y no tiene sentido importar el cliente API
// completo de Cartera solo para este único endpoint de lectura.
import { fmt, fmtPct } from './format.js';

const TYPE_LABEL = { accion: 'Acción', cedear: 'CEDEAR', bono: 'Bono' };
const RESULT_LIMIT = 8;
const DEBOUNCE_MS = 150;

let panelEl = null;
let inputEl = null;
let resultsEl = null;
let results = [];
let activeIndex = -1;
let requestToken = 0;
let debounceTimer = null;

function pctClass(pct) {
    if (pct === null || pct === undefined) return 'cell-muted';
    return pct > 0 ? 'cell-positive' : (pct < 0 ? 'cell-negative' : 'cell-muted');
}

function resultRowHtml(r, i) {
    return `
        <button type="button" class="command-palette-result${i === activeIndex ? ' active' : ''}" data-idx="${i}">
            <span class="command-palette-result-symbol">${r.symbol}</span>
            <span class="command-palette-result-type">${TYPE_LABEL[r.type] || r.type}</span>
            <span class="command-palette-result-price num">${r.price !== null && r.price !== undefined ? fmt(r.price) : '—'}</span>
            <span class="command-palette-result-pct num ${pctClass(r.pct_change)}">${r.pct_change !== null && r.pct_change !== undefined ? fmtPct(r.pct_change, 2, { signed: true }) : ''}</span>
        </button>
    `;
}

function renderResults() {
    if (!results.length) {
        resultsEl.innerHTML = inputEl.value.trim()
            ? '<p class="command-palette-empty">Sin coincidencias en el universo BYMA.</p>'
            : '<p class="command-palette-empty">Escribí un símbolo para buscar en todo BYMA (acciones, CEDEARs, bonos).</p>';
        return;
    }
    resultsEl.innerHTML = results.map(resultRowHtml).join('');
}

function goToResult(r) {
    const params = new URLSearchParams({ market: r.market || 'byma', assetType: r.type });
    const hash = `#detalle/${encodeURIComponent(r.symbol)}?${params.toString()}`;
    // Fuera de /portfolio (ej. Predictor, index.html) el router de hash de
    // esa página no existe -- hace falta una navegación completa.
    if (window.location.pathname === '/portfolio') {
        window.location.hash = hash;
    } else {
        window.location.href = `/portfolio${hash}`;
    }
}

async function runSearch(query) {
    const token = ++requestToken;
    if (!query.trim()) {
        results = [];
        activeIndex = -1;
        renderResults();
        return;
    }
    try {
        const { data } = await axios.get('/api/portfolio/explorer/search', { params: { q: query, limit: RESULT_LIMIT } });
        if (token !== requestToken) return; // respuesta vieja, el usuario ya siguió tipeando
        results = data;
        activeIndex = results.length ? 0 : -1;
        renderResults();
    } catch {
        if (token !== requestToken) return;
        results = [];
        activeIndex = -1;
        renderResults();
    }
}

function moveActive(delta) {
    if (!results.length) return;
    activeIndex = (activeIndex + delta + results.length) % results.length;
    renderResults();
    resultsEl.querySelector('.command-palette-result.active')?.scrollIntoView({ block: 'nearest' });
}

function selectActive() {
    if (activeIndex < 0 || !results[activeIndex]) return;
    goToResult(results[activeIndex]);
    close();
}

function open() {
    const root = document.getElementById('command-palette-root');
    if (!root || !root.hidden) return;
    // Nunca se abre encima de un modal de formulario ya en curso (ej. "Nueva
    // transacción") -- Ctrl/Cmd+K ahí no hace nada en vez de pisar lo que el
    // usuario ya estaba completando.
    const modalRoot = document.getElementById('modal-root');
    if (modalRoot && !modalRoot.hidden) return;

    root.hidden = false;
    root.innerHTML = `
        <div class="command-palette-backdrop"></div>
        <div class="command-palette-box" role="dialog" aria-modal="true" aria-label="Buscar instrumento">
            <input type="text" class="command-palette-input" placeholder="Buscar símbolo en todo BYMA..." autocomplete="off">
            <div class="command-palette-results"></div>
            <div class="command-palette-hint">↑↓ navegar &middot; Enter ir &middot; Esc cerrar</div>
        </div>
    `;
    panelEl = root;
    inputEl = root.querySelector('.command-palette-input');
    resultsEl = root.querySelector('.command-palette-results');
    results = [];
    activeIndex = -1;
    renderResults();
    inputEl.focus();

    root.querySelector('.command-palette-backdrop').addEventListener('click', close);
    inputEl.addEventListener('input', () => {
        clearTimeout(debounceTimer);
        debounceTimer = setTimeout(() => runSearch(inputEl.value), DEBOUNCE_MS);
    });
    inputEl.addEventListener('keydown', (e) => {
        if (e.key === 'ArrowDown') { e.preventDefault(); moveActive(1); }
        else if (e.key === 'ArrowUp') { e.preventDefault(); moveActive(-1); }
        else if (e.key === 'Enter') { e.preventDefault(); selectActive(); }
        else if (e.key === 'Escape') { e.preventDefault(); close(); }
    });
    resultsEl.addEventListener('click', (e) => {
        const btn = e.target.closest('.command-palette-result');
        if (!btn) return;
        activeIndex = Number(btn.dataset.idx);
        selectActive();
    });
}

function close() {
    const root = document.getElementById('command-palette-root');
    if (!root || root.hidden) return;
    root.hidden = true;
    root.innerHTML = '';
    clearTimeout(debounceTimer);
}

export function initCommandPalette() {
    document.addEventListener('keydown', (e) => {
        const key = e.key.toLowerCase();
        if ((e.metaKey || e.ctrlKey) && key === 'k') {
            e.preventDefault();
            const root = document.getElementById('command-palette-root');
            if (root && !root.hidden) close();
            else open();
        }
    });
    document.getElementById('command-palette-trigger')?.addEventListener('click', open);
    const hint = document.getElementById('command-palette-trigger-hint');
    if (hint && /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent)) hint.textContent = '⌘K';
}
