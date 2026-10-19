const STORAGE_KEY = 'kronos-sidebar-collapsed';

function updateAriaLabel(sidebar, button) {
    const collapsed = sidebar.classList.contains('collapsed');
    button.setAttribute('aria-label', collapsed ? 'Expandir menú' : 'Colapsar menú');
}

export function initSidebarCollapse(sidebarId, buttonId) {
    const sidebar = document.getElementById(sidebarId);
    const button = document.getElementById(buttonId);
    if (!sidebar || !button) return;

    sidebar.classList.toggle('collapsed', localStorage.getItem(STORAGE_KEY) === '1');
    updateAriaLabel(sidebar, button);

    button.addEventListener('click', () => {
        const collapsed = sidebar.classList.toggle('collapsed');
        localStorage.setItem(STORAGE_KEY, collapsed ? '1' : '0');
        updateAriaLabel(sidebar, button);
    });
}
