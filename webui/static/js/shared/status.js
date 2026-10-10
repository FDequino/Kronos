import { $ } from './dom.js';

export function showStatus(elId, type, message, autoHideMs = 5000) {
    const el = $(elId);
    el.className = `status show ${type}`;
    el.textContent = message;
    if (autoHideMs) {
        setTimeout(() => el.classList.remove('show'), autoHideMs);
    }
}

export function setLoading(elId, show) {
    $(elId).classList.toggle('show', show);
}
