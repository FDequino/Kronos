// Reposiciona paneles de tooltip (.info-tooltip-panel / .glossary-term-panel)
// que quedarían cortados contra el borde derecho de la ventana (ver
// conversación/auditoría 19/10/2026, hallazgo 3: "texto... cortado contra el
// borde derecho, genera scroll horizontal") -- CSS solo no alcanza porque el
// ancho del panel es fijo pero el trigger (ícono ⓘ / término subrayado) puede
// estar en cualquier punto horizontal de la pantalla, incluida la columna más
// a la derecha de una tabla. Ambos sistemas de tooltip son puramente
// :hover/:focus-within (nunca dispararon JS antes de esto) -- acá solo se
// decide, en el momento en que SE VA A MOSTRAR, si conviene abrir el panel
// hacia la izquierda del trigger en vez de hacia la derecha (el default).
function repositionIfOverflowing(panel) {
    panel.classList.remove('tooltip-panel-align-right');
    const rect = panel.getBoundingClientRect();
    if (rect.width && rect.right > window.innerWidth - 8) {
        panel.classList.add('tooltip-panel-align-right');
    }
}

function wire(wrapperSelector, panelClass) {
    const handler = (e) => {
        const wrapper = e.target.closest(wrapperSelector);
        if (!wrapper) return;
        const panel = wrapper.querySelector(`:scope > .${panelClass}`);
        if (panel) repositionIfOverflowing(panel);
    };
    document.addEventListener('mouseover', handler);
    document.addEventListener('focusin', handler);
}

wire('.info-tooltip', 'info-tooltip-panel');
wire('.glossary-term', 'glossary-term-panel');
