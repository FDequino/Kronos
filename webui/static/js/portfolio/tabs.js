import { renderInstrumentDetail, parseDetailRoute } from './instrument-detail.js';
import { activateSubtab } from '../shared/subtabs.js';
import { recordNavigation } from '../shared/nav-history.js';

const VIEWS = ['resumen', 'decisiones', 'instrumentos', 'transacciones', 'plazos-fijos', 'prestamos', 'ajustes'];
const DETAIL_PREFIX = 'detalle/';

// Fase 1 (ver conversacion, "título por sección"): título + subtítulo de
// #page-title/#page-subtitle (base.html) y de <title> por vista -- antes
// era un bloque Jinja único para las 7 vistas de portfolio.html, siempre
// "Mi Cartera". El de detalle de instrumento se arma aparte (symbol real),
// ver setPageMeta más abajo.
const VIEW_META = {
    resumen: { title: 'Resumen', subtitle: 'Patrimonio, evolución y proyección de tu cartera' },
    decisiones: { title: 'Decisiones', subtitle: 'Oportunidades del día y seguimiento de propuestas' },
    instrumentos: { title: 'Instrumentos', subtitle: 'Catálogo completo y comparador de activos' },
    transacciones: { title: 'Transacciones', subtitle: 'Historial de operaciones, dividendos y rentas' },
    'plazos-fijos': { title: 'Plazos Fijos', subtitle: 'Plazos fijos y cauciones' },
    prestamos: { title: 'Préstamos', subtitle: 'Préstamos otorgados y recibidos' },
    ajustes: { title: 'Ajustes', subtitle: 'Estrategia, reporte PDF, conexiones y preferencias' },
};

function setPageMeta(target, hash, isDetail) {
    const titleEl = document.getElementById('page-title');
    const subtitleEl = document.getElementById('page-subtitle');
    if (isDetail) {
        const { symbol } = parseDetailRoute(hash);
        if (titleEl) titleEl.textContent = symbol;
        if (subtitleEl) subtitleEl.textContent = 'Detalle del instrumento';
        document.title = `${symbol} · Kronos`;
        return;
    }
    const meta = VIEW_META[target] || VIEW_META[VIEWS[0]];
    if (titleEl) titleEl.textContent = meta.title;
    if (subtitleEl) subtitleEl.textContent = meta.subtitle;
    document.title = `${meta.title} · Kronos`;
}

// Fase 1 ("scroll"): resetear a top en cada cambio de vista/subtab, pero
// conservar el de la lista al volver con "atrás" desde un detalle de
// instrumento. Se guarda el scroll de la ÚLTIMA vista no-detalle justo antes
// de entrar a un detalle; al volver a ESE mismo hash exacto se restaura una
// sola vez (one-shot) -- cualquier otra navegación resetea a top, que es el
// comportamiento pedido como default.
let currentHash = null;
let savedScroll = null; // { hash, y }

// Captura el scroll de la vista ACTUAL antes de tocar el DOM -- si se lee
// DESPUÉS de togglear .active/inyectar el detalle, el browser ya pudo haber
// clampeado window.scrollY a la nueva altura (más corta) del contenido
// recién insertado, perdiendo el valor real (bug real encontrado armando
// este fix: siempre daba 0 al volver).
function captureScrollBeforeMutation(hash, isDetail) {
    if (isDetail && currentHash !== null && !currentHash.startsWith(DETAIL_PREFIX)) {
        savedScroll = { hash: currentHash, y: window.scrollY };
    }
}

function applyScrollAfterMutation(hash, isDetail) {
    if (!isDetail && savedScroll && savedScroll.hash === hash) {
        window.scrollTo(0, savedScroll.y);
        savedScroll = null;
    } else {
        window.scrollTo(0, 0);
    }
}

// Fase 15: Señales/Kronos/Propuestas se fusionaron en "Decisiones", y
// Explorador/Comparar en "Instrumentos" -- un link/favorito viejo sigue
// funcionando, redirige a la vista+subtab correctos en vez de caer al default.
// Fase 18: Propuestas y Señales son subtabs propios de vuelta (dejaron de
// convivir en "Bandeja" -- eran dos cosas distintas, mezclarlas confundía).
// Pase de limpieza de UI (ver conversacion): Decisiones es ahora un layout
// PLANO de 4 pestañas hermanas (Oportunidades del Día / Propuestas / Paper
// Trading / Rendimiento Histórico -- "Configuración del Motor" se retiró
// del todo más tarde, ver LEGACY_SUBTAB_REDIRECTS) -- se eliminó
// la barra de subtabs anidada que existía antes, así que todos estos
// redirects son de 2 niveles [vista, subtab] (nunca una subtab anidada
// dentro de otra). "senales" y "alertas" (vistas globales retiradas -- ese
// mismo dato vive en el detalle de cada instrumento y en Instrumentos)
// aterrizan en Oportunidades del Día como fallback razonable. "kronos"
// (vista vieja de precisión por instrumento, con selector) tampoco existe
// más -- ese detalle vive en la página de cada instrumento, el link legado
// aterriza en el resumen comparativo de "Rendimiento Histórico".
// Instrumentos unificó Catálogo+Explorador en una sola vista sin subtabs
// propias.
const LEGACY_HASH_REDIRECTS = {
    senales: ['decisiones', 'oportunidades'],
    'core-satelite': ['decisiones', 'oportunidades'],
    alertas: ['decisiones', 'oportunidades'],
    kronos: ['decisiones', 'rendimiento'],
    propuestas: ['decisiones', 'propuestas'],
    bandeja: ['decisiones', 'propuestas'],
    explorador: ['instrumentos'],
    comparar: ['instrumentos', 'comparar'],
};

// Subtabs que dejaron de existir pero la VISTA que las contiene sigue tal
// cual (a diferencia de LEGACY_HASH_REDIRECTS de arriba, que redirige por
// VISTA, primer segmento del hash) -- por eso es un mapa aparte, keyeado por
// "vista/subtab". Un bookmark viejo a una de estas rutas ya caía en la
// subtab default de esa vista (activateSubtab() no hace nada si el botón no
// existe), pero no depender de esa casualidad: dejarlo explícito.
// - decisiones/motor: "Motor" dejó de ser subtab propia (ver portfolio.html).
// - ajustes/telegram, ajustes/conexiones: se fusionaron en "Integraciones".
const LEGACY_SUBTAB_REDIRECTS = {
    'decisiones/motor': 'oportunidades',
    'ajustes/telegram': 'integraciones',
    'ajustes/conexiones': 'integraciones',
};

function activateView(hash) {
    const isDetail = hash.startsWith(DETAIL_PREFIX);
    let viewName;
    let topSubtab;
    if (isDetail) {
        // detalle/SYMBOL?query -- el segmento despues de "detalle/" es el
        // simbolo, no una subtab (parseDetailRoute de instrument-detail.js
        // es quien de verdad lo interpreta); nunca lo partimos por "/" aca.
        viewName = 'detalle';
        topSubtab = null;
    } else {
        [viewName, topSubtab] = hash.split('?')[0].split('/');
    }
    let nestedSubtab = null;
    if (LEGACY_HASH_REDIRECTS[viewName]) {
        [viewName, topSubtab, nestedSubtab] = LEGACY_HASH_REDIRECTS[viewName];
    }
    const target = isDetail ? 'detalle' : (VIEWS.includes(viewName) ? viewName : VIEWS[0]);
    if (!isDetail && LEGACY_SUBTAB_REDIRECTS[`${target}/${topSubtab}`]) {
        topSubtab = LEGACY_SUBTAB_REDIRECTS[`${target}/${topSubtab}`];
    }
    captureScrollBeforeMutation(hash, isDetail);
    document.querySelectorAll('.view').forEach((el) => {
        el.classList.toggle('active', el.id === `view-${target}`);
    });
    if (isDetail) {
        renderInstrumentDetail(hash);
    } else {
        if (topSubtab) activateSubtab(target, topSubtab);
        if (nestedSubtab) activateSubtab(topSubtab, nestedSubtab);
    }
    setPageMeta(target, hash, isDetail);
    applyScrollAfterMutation(hash, isDetail);
    currentHash = hash;
    recordNavigation();
}

export function initTabs() {
    if (!window.location.hash) {
        history.replaceState(null, '', `#${VIEWS[0]}`);
    }
    activateView(window.location.hash.slice(1));
    window.addEventListener('hashchange', () => activateView(window.location.hash.slice(1)));
}
