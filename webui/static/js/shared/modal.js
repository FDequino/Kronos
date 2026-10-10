const CLOSE_ANIMATION_MS = 140;
let activeOnClose = null;
let closing = false;

function root() {
    return document.getElementById('modal-root');
}

export function closeModal() {
    const el = root();
    if (!el || el.hidden || closing) return;
    closing = true;
    el.classList.add('closing');
    document.removeEventListener('keydown', onKeydown);
    setTimeout(() => {
        el.hidden = true;
        el.classList.remove('closing');
        el.innerHTML = '';
        closing = false;
        if (activeOnClose) { activeOnClose(); activeOnClose = null; }
    }, CLOSE_ANIMATION_MS);
}

function onKeydown(e) {
    if (e.key === 'Escape') closeModal();
}

export function openModal({ title, bodyHtml, onMount, onSubmit, onClose }) {
    const el = root();
    if (!el) return;
    closing = false;
    el.classList.remove('closing');
    activeOnClose = onClose || null;

    el.innerHTML = `
        <div class="modal-backdrop"></div>
        <div class="modal-box" role="dialog" aria-modal="true" aria-label="${title}">
            <div class="modal-header">
                <h2>${title}</h2>
                <button type="button" class="modal-close" aria-label="Cerrar">&times;</button>
            </div>
            <form class="modal-body">${bodyHtml}</form>
        </div>
    `;
    el.hidden = false;

    const form = el.querySelector('form.modal-body');
    el.querySelector('.modal-backdrop').addEventListener('click', closeModal);
    el.querySelector('.modal-close').addEventListener('click', closeModal);
    document.addEventListener('keydown', onKeydown);

    if (onMount) onMount(form);

    if (onSubmit) {
        form.addEventListener('submit', async (e) => {
            e.preventDefault();
            const submitBtn = form.querySelector('[type="submit"]');
            if (submitBtn) submitBtn.disabled = true;
            try {
                await onSubmit(form);
                closeModal();
            } catch (error) {
                const errEl = form.querySelector('.modal-error');
                if (errEl) errEl.textContent = error.response?.data?.error || error.message || 'Error inesperado';
            } finally {
                if (submitBtn) submitBtn.disabled = false;
            }
        });
    }

    return form;
}
