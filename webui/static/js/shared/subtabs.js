// Fase 1 (ver conversacion, "sub-pestaña en la URL"): un click REAL en un
// subtab-btn de nivel superior (el nombre del grupo coincide con una vista
// de VIEWS en tabs.js, ej. "resumen"/"decisiones") escribe `#vista/subtab`
// en el hash via pushState -- asi queda en el link, sobrevive un reload y
// funciona con atras/adelante (pushState no dispara hashchange el mismo
// instante en que se llama, pero SI lo dispara el navegador al volver atras/
// adelante por esa entrada, que es exactamente cuando hace falta re-sincronizar).
//
// `suppressHashWrite` evita el loop: activateSubtab() (sync programatico
// desde tabs.js al parsear un hash entrante, o un redirect legacy) sigue
// disparando un click REAL a proposito (ver docstring original mas abajo),
// pero con el flag arriba no vuelve a escribir el mismo hash que lo origino.
let suppressHashWrite = false;

function activate(group, btn) {
    // Alcance = solo los paneles hijos DIRECTOS del contenedor del grupo, nunca
    // toda la .view -- así un grupo de subtabs anidado (ej. Kronos dentro de
    // Decisiones) no pisa los paneles del grupo que lo contiene.
    const scope = group.parentElement || document;
    group.querySelectorAll('.subtab-btn').forEach((b) => b.classList.toggle('active', b === btn));
    scope.querySelectorAll(':scope > [data-subtab-panel]').forEach((panel) => {
        panel.classList.toggle('active', panel.dataset.subtabPanel === btn.dataset.subtabTarget);
    });
}

function writeSubtabHash(groupName, target) {
    const [viewPart] = location.hash.slice(1).split('/');
    if (viewPart !== groupName) return; // defensivo: un grupo de subtabs que no es la vista actual (no deberia pasar en el uso real)
    const newHash = `#${groupName}/${target}`;
    if (location.hash !== newHash) history.pushState(null, '', newHash);
}

export function initSubtabs() {
    document.querySelectorAll('[data-subtabs]').forEach((group) => {
        group.querySelectorAll('.subtab-btn').forEach((btn) => {
            btn.addEventListener('click', () => {
                activate(group, btn);
                if (!suppressHashWrite) writeSubtabHash(group.dataset.subtabs, btn.dataset.subtabTarget);
            });
        });
    });
}

// Fuerza un subtab especifico desde codigo (ej. un link externo que siempre
// debe aterrizar en la misma subpestaña, sin importar cual quedo activa la
// ultima vez que se visito esta vista -- ver kronos-history.js::activateFromHash,
// y tabs.js::activateView al parsear un hash entrante #vista/subtab).
//
// Dispara un click REAL (no llama a activate() directo) a propósito: así
// cualquier listener propio que un módulo haya colgado del botón (ej. la
// carga perezosa de explorer.js, Fase 15) se ejecuta igual que si el usuario
// hubiera clickeado -- un redirect de hash viejo y un click manual terminan
// en el mismo camino de código, nunca dos caminos distintos para "lo mismo".
export function activateSubtab(groupName, target) {
    const group = document.querySelector(`[data-subtabs="${groupName}"]`);
    if (!group) return;
    const btn = group.querySelector(`.subtab-btn[data-subtab-target="${target}"]`);
    if (!btn) return;
    suppressHashWrite = true;
    btn.click();
    suppressHashWrite = false;
}
