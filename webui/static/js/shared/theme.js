const STORAGE_KEY = 'kronos-theme';

export function initThemeToggle(inputId) {
    const input = document.getElementById(inputId);
    if (!input) return;

    const sync = () => {
        input.checked = document.documentElement.dataset.theme === 'light';
    };

    input.addEventListener('change', () => {
        const next = input.checked ? 'light' : 'dark';
        document.documentElement.dataset.theme = next;
        localStorage.setItem(STORAGE_KEY, next);
        document.dispatchEvent(new CustomEvent('theme-changed', { detail: next }));
    });

    sync();
}
