const EDIT_ICON = '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M13.5 3.5l3 3L7 16H4v-3L13.5 3.5Z"/></svg>';
const DELETE_ICON = '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M4 6h12M8 6V4h4v2M6 6l1 10h6l1-10"/></svg>';

export function rowActionsHtml(id) {
    return `<td class="row-actions">
        <button type="button" class="row-action-btn row-action-edit" data-id="${id}" aria-label="Editar">${EDIT_ICON}</button>
        <button type="button" class="row-action-btn row-action-delete" data-id="${id}" aria-label="Eliminar">${DELETE_ICON}</button>
    </td>`;
}

export function wireRowActions(container, { onEdit, onDelete }) {
    container.addEventListener('click', (e) => {
        const editBtn = e.target.closest('.row-action-edit');
        if (editBtn) { onEdit(editBtn.dataset.id); return; }
        const deleteBtn = e.target.closest('.row-action-delete');
        if (deleteBtn) onDelete(deleteBtn.dataset.id);
    });
}
