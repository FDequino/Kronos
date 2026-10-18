// Fase 1 (ver conversación, "atrás desde el detalle"): saber si hay un paso
// de navegación INTERNO (dentro de esta pestaña) al que volver, sin acoplar
// portfolio/tabs.js <-> portfolio/instrument-detail.js entre sí (ambos
// importan este módulo, ninguno importa al otro para esto).
let navigationCount = 0;

export function recordNavigation() {
    navigationCount += 1;
}

// true solo si activateView ya corrió al menos 2 veces en esta pestaña (la
// carga inicial + al menos una navegación real) -- la única garantía barata
// de que history.back() va a quedarse DENTRO de la app en vez de salir de
// ella o a una página en blanco (link compartido, recarga, pestaña nueva).
export function hasInternalHistory() {
    return navigationCount > 1;
}

export function goBack(fallbackHash) {
    if (hasInternalHistory()) {
        history.back();
    } else {
        location.hash = fallbackHash;
    }
}
