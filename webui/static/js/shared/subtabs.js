export function initSubtabs() {
    document.querySelectorAll('[data-subtabs]').forEach((group) => {
        const scope = group.closest('.view') || document;
        group.querySelectorAll('.subtab-btn').forEach((btn) => {
            btn.addEventListener('click', () => {
                group.querySelectorAll('.subtab-btn').forEach((b) => b.classList.toggle('active', b === btn));
                scope.querySelectorAll('[data-subtab-panel]').forEach((panel) => {
                    panel.classList.toggle('active', panel.dataset.subtabPanel === btn.dataset.subtabTarget);
                });
            });
        });
    });
}
