const STORAGE_KEY = 'kronos-sidebar-collapsed';

export function initSidebarCollapse(sidebarId, buttonId) {
    const sidebar = document.getElementById(sidebarId);
    const button = document.getElementById(buttonId);
    if (!sidebar || !button) return;

    sidebar.classList.toggle('collapsed', localStorage.getItem(STORAGE_KEY) === '1');

    button.addEventListener('click', () => {
        const collapsed = sidebar.classList.toggle('collapsed');
        localStorage.setItem(STORAGE_KEY, collapsed ? '1' : '0');
    });
}
