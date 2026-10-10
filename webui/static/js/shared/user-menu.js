import { $ } from './dom.js';

const BASE = '/api/portfolio';

function closePanels() {
    $('user-menu-panel').hidden = true;
    $('bna-variant-panel').hidden = true;
    $('bna-variant-chevron').classList.remove('open');
}

function setCurrencyActive(currency) {
    document.querySelectorAll('#currency-toggle .segmented-toggle-option').forEach((btn) => {
        btn.classList.toggle('active', btn.dataset.value === currency);
    });
}

function setBnaRadios(variant) {
    document.querySelectorAll('input[name="bna-variant"]').forEach((r) => { r.checked = r.value === variant; });
}

function notifyCurrencyChanged(currency) {
    document.dispatchEvent(new CustomEvent('currency-changed', { detail: currency }));
}

export async function initUserMenu() {
    const { data: settings } = await axios.get(`${BASE}/settings`);
    setCurrencyActive(settings.display_currency);
    setBnaRadios(settings.bna_rate_variant);

    $('user-menu-trigger').addEventListener('click', (e) => {
        e.stopPropagation();
        $('notifications-panel').hidden = true;
        $('user-menu-panel').hidden = !$('user-menu-panel').hidden;
    });
    $('bna-variant-chevron').addEventListener('click', (e) => {
        e.stopPropagation();
        const nowHidden = !$('bna-variant-panel').hidden;
        $('bna-variant-panel').hidden = nowHidden;
        $('bna-variant-chevron').classList.toggle('open', !nowHidden);
    });
    document.querySelectorAll('#currency-toggle .segmented-toggle-option').forEach((btn) => {
        btn.addEventListener('click', async () => {
            const next = btn.dataset.value;
            setCurrencyActive(next);
            await axios.put(`${BASE}/settings`, { display_currency: next });
            notifyCurrencyChanged(next);
        });
    });
    document.querySelectorAll('input[name="bna-variant"]').forEach((radio) => {
        radio.addEventListener('change', async (e) => {
            await axios.put(`${BASE}/settings`, { bna_rate_variant: e.target.value });
            notifyCurrencyChanged($('currency-toggle').querySelector('.active')?.dataset.value);
        });
    });
    document.addEventListener('click', (e) => {
        if (!e.target.closest('.user-menu')) closePanels();
    });
}
