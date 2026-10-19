// Navegación inferior mobile (ver conversación, Fase 8) -- "Más" abre una
// hoja con el resto de las secciones (Plazos Fijos/Préstamos/Ajustes/
// Predictor), mismo criterio de cierre que cualquier popover de la app
// (click afuera, Escape, o elegir una opción).
export function initBottomNav() {
    const moreBtn = document.getElementById('bottom-nav-more-btn');
    const sheet = document.getElementById('bottom-nav-sheet');
    const backdrop = document.getElementById('bottom-nav-sheet-backdrop');
    if (!moreBtn || !sheet || !backdrop) return;

    function open() {
        sheet.hidden = false;
        moreBtn.setAttribute('aria-expanded', 'true');
    }

    function close() {
        sheet.hidden = true;
        moreBtn.setAttribute('aria-expanded', 'false');
    }

    moreBtn.addEventListener('click', () => (sheet.hidden ? open() : close()));
    backdrop.addEventListener('click', close);
    sheet.querySelectorAll('.bottom-nav-sheet-item').forEach((item) => {
        item.addEventListener('click', close);
    });
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && !sheet.hidden) close();
    });
}
