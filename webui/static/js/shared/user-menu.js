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

function setUsdSourceActive(source) {
    document.querySelectorAll('#usd-source-toggle .segmented-toggle-option').forEach((btn) => {
        btn.classList.toggle('active', btn.dataset.value === source);
    });
    // Las 4 variantes (Divisa/Billete x venta/compra) solo existen para BNA --
    // con MEP/CCL el chevron no tiene nada que mostrar (bug real: antes vivía
    // pegado al toggle de Moneda, sin relación visible con "Fuente USD", y
    // aparecía siempre aunque la fuente no fuera BNA).
    const isBna = source === 'bna';
    $('bna-variant-chevron').hidden = !isBna;
    if (!isBna) {
        $('bna-variant-panel').hidden = true;
        $('bna-variant-chevron').classList.remove('open');
    }
}

function notifyCurrencyChanged(currency) {
    document.dispatchEvent(new CustomEvent('currency-changed', { detail: currency }));
}

export async function initUserMenu() {
    const { data: settings } = await axios.get(`${BASE}/settings`);
    setCurrencyActive(settings.display_currency);
    setBnaRadios(settings.bna_rate_variant);
    setUsdSourceActive(settings.usd_rate_source);
    $('inflation-adjusted-switch').checked = !!settings.ars_inflation_adjusted;

    const { data: config } = await axios.get(`${BASE}/signal-config`);
    if (config.caucion_reference_tna !== null && config.caucion_reference_tna !== undefined) {
        $('caucion-reference-input').value = config.caucion_reference_tna;
    }

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
    document.querySelectorAll('#usd-source-toggle .segmented-toggle-option').forEach((btn) => {
        btn.addEventListener('click', async () => {
            const next = btn.dataset.value;
            setUsdSourceActive(next);
            await axios.put(`${BASE}/settings`, { usd_rate_source: next });
            notifyCurrencyChanged($('currency-toggle').querySelector('.active')?.dataset.value);
        });
    });
    $('inflation-adjusted-switch').addEventListener('change', async (e) => {
        await axios.put(`${BASE}/settings`, { ars_inflation_adjusted: e.target.checked ? 1 : 0 });
        notifyCurrencyChanged($('currency-toggle').querySelector('.active')?.dataset.value);
    });
    let caucionSaveTimer = null;
    $('caucion-reference-input').addEventListener('input', (e) => {
        clearTimeout(caucionSaveTimer);
        const raw = e.target.value.trim();
        const value = raw === '' ? null : Number(raw);
        caucionSaveTimer = setTimeout(() => {
            axios.put(`${BASE}/signal-config`, { caucion_reference_tna: value });
        }, 500);
    });
    document.addEventListener('click', (e) => {
        if (!e.target.closest('.user-menu')) closePanels();
    });
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') closePanels();
    });
}
