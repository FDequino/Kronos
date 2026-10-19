// Spinner inline reusable (ver conversación, pase de UX/UI) -- reemplaza
// cualquier `<p class="hint">Cargando…</p>` suelto por el mismo spinner
// visual que ya usaba holdings.js/instrument-detail.js (.loading.show +
// .spinner, ver webui/static/css/components/loading.css), para que "algo
// está pasando" se vea igual en toda la app. SIEMPRE incluye la clase "show"
// -- .loading sin ella queda con display:none (bug real encontrado en
// core-satellite.js: el spinner nunca se veía porque faltaba esa clase).
export function loadingHtml(text) {
    return `<div class="loading loading-panel show"><div class="spinner"></div><span>${text}</span></div>`;
}
