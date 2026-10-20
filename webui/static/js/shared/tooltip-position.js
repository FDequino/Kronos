// Reposiciona paneles de tooltip (.info-tooltip-panel / .glossary-term-panel)
// que quedarían cortados contra un borde (ver conversación/auditoría, hallazgo
// 3: "texto... cortado contra el borde derecho" y, después, "se corta el
// tooltip" -- este segundo caso resultó ser un corte VERTICAL, contra el
// borde inferior de un contenedor con scroll propio como .table-scroll, no
// contra la ventana). CSS solo no alcanza por dos motivos: el trigger (ícono
// ⓘ / término subrayado) puede estar en cualquier punto de la pantalla, Y
// cualquier ancestro con overflow-x distinto de "visible" (como
// .table-scroll) hace que el navegador calcule overflow-y como "auto"
// también (regla real del spec de CSS, no un bug de esta app) -- un panel
// position:absolute relativo a ESE ancestro queda clippeado por su scroll
// interno sin importar qué tan bien se calculen left/top relativos a él.
//
// La solución: al mostrarse, el panel pasa a position:fixed con
// top/left calculados en coordenadas de VENTANA (no del wrapper) -- eso lo
// saca por completo de cualquier contenedor con scroll/overflow propio, sea
// cual sea. Ambos sistemas de tooltip son puramente :hover/:focus-visible en
// CSS (nunca dispararon JS antes de esto) -- acá solo se decide, en el
// momento en que SE VA A MOSTRAR, dónde ponerlo.
function positionPanel(wrapper, panel, opensAbove) {
    panel.style.position = '';
    panel.style.top = '';
    panel.style.left = '';

    const wrapperRect = wrapper.getBoundingClientRect();
    const panelRect = panel.getBoundingClientRect();
    if (!panelRect.width) return; // CSS todavía no le dio display:block -- no debería pasar en mouseover/focusin real

    let left = wrapperRect.left;
    if (left + panelRect.width > window.innerWidth - 8) {
        left = Math.max(8, wrapperRect.right - panelRect.width);
    }

    let top;
    if (opensAbove) {
        top = wrapperRect.top - panelRect.height - 6;
        if (top < 8) top = wrapperRect.bottom + 6; // no entra arriba -- abre abajo
    } else {
        top = wrapperRect.bottom + 6;
        if (top + panelRect.height > window.innerHeight - 8) {
            top = Math.max(8, wrapperRect.top - panelRect.height - 6); // no entra abajo -- abre arriba
        }
    }

    panel.style.position = 'fixed';
    panel.style.left = `${left}px`;
    panel.style.top = `${top}px`;
}

function wire(wrapperSelector, panelClass, opensAbove) {
    const handler = (e) => {
        const wrapper = e.target.closest(wrapperSelector);
        if (!wrapper) return;
        const panel = wrapper.querySelector(`:scope > .${panelClass}`);
        if (panel) positionPanel(wrapper, panel, opensAbove);
    };
    document.addEventListener('mouseover', handler);
    document.addEventListener('focusin', handler);
}

wire('.info-tooltip', 'info-tooltip-panel', false);
wire('.glossary-term', 'glossary-term-panel', true);
