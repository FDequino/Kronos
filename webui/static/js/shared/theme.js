const STORAGE_KEY = 'kronos-theme';

export function initThemeToggle(inputId) {
    const input = document.getElementById(inputId);
    if (!input) return;

    // El label es "Modo oscuro" -- el checkbox tiene que estar marcado
    // cuando el tema ES oscuro, no al reves (estaba invertido).
    const sync = () => {
        input.checked = document.documentElement.dataset.theme !== 'light';
    };

    input.addEventListener('change', () => {
        const next = input.checked ? 'dark' : 'light';
        document.documentElement.dataset.theme = next;
        localStorage.setItem(STORAGE_KEY, next);
        document.dispatchEvent(new CustomEvent('theme-changed', { detail: next }));
    });

    sync();
}
