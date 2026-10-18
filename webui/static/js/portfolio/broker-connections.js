// Subtab "Conexiones" de Ajustes (ver conversacion, Multi-Broker) -- tabla
// simple de cuentas de bróker + alta/edición vía modal. Deliberadamente SIN
// la maquinaria de loans.js (buscador/orden/column-manager): se espera un
// puñado de cuentas (una por bróker como mucho), no cientos de filas.
import { $ } from '../shared/dom.js';
import { showStatus } from '../shared/status.js';
import { openModal } from '../shared/modal.js';
import { api } from './api.js';

const BROKER_NAMES = ['PPI', 'IOL', 'Balanz', 'Matrix'];

// Por broker, que es REALMENTE cada campo generico api_key/api_secret de
// broker_accounts (ver conversacion: "no se de donde sacar mi clave api en
// iol" -- la etiqueta generica "API Key/Secret" confundia, IOL no tiene una
// API Key para generar, es el usuario/clave normal de la pagina). Fuente:
// https://api.invertironline.com/Help/Autenticacion (confirmado real, "POST
// especificando el nombre de usuario y contraseña", sin paso de alta de API
// aparte) y portfolio/broker_client.py::IOLAdapter/PPIAdapter.
const BROKER_CREDENTIAL_LABELS = {
    IOL: {
        key: 'Usuario de IOL', secret: 'Contraseña de IOL',
        hint: 'IOL no tiene una "API Key" para generar aparte — son tu usuario y contraseña normales de invertironline.com, los mismos con los que entrás a la web/app.',
    },
    PPI: {
        key: 'API Key', secret: 'API Secret',
        hint: 'Portfolio Personal sí tiene credenciales de API separadas de tu usuario web — se piden/gestionan directo con PPI, no son tu usuario/contraseña de la plataforma.',
    },
    Balanz: {
        key: 'API Key', secret: 'API Secret',
        hint: 'Balanz: sin documentación pública de API verificada todavía — integración pendiente, "Probar conexión" lo va a avisar.',
    },
    Matrix: {
        key: 'API Key', secret: 'API Secret',
        hint: 'Matrix: sin documentación pública de API verificada todavía — integración pendiente, "Probar conexión" lo va a avisar.',
    },
};

function brokerOptionsHtml(selected) {
    return BROKER_NAMES.map((name) => `<option value="${name}" ${name === selected ? 'selected' : ''}>${name}</option>`).join('');
}

function statusBadgeHtml(account) {
    return account.is_active
        ? '<span class="ajustes-role-badge badge-success">Activa</span>'
        : '<span class="ajustes-role-badge badge-muted">Inactiva</span>';
}

function accountRowHtml(account) {
    const alias = account.alias || '<span class="hint">(sin alias)</span>';
    const accountNumber = account.account_number || '—';
    return `
        <tr data-id="${account.id}">
            <td><b>${account.broker_name}</b></td>
            <td>${alias}</td>
            <td class="mono">${accountNumber}</td>
            <td class="mono">${account.api_key || '—'}</td>
            <td>${statusBadgeHtml(account)}</td>
            <td class="broker-account-test-result hint"></td>
            <td class="row-actions">
                <button type="button" class="row-action-btn broker-test-btn" data-id="${account.id}" aria-label="Probar conexión" data-tooltip="Probar conexión — pide un token real contra el bróker y avisa si funcionó">🔌</button>
                <button type="button" class="row-action-btn broker-edit-btn" data-id="${account.id}" aria-label="Editar" data-tooltip="Editar esta cuenta">✏️</button>
                <button type="button" class="row-action-btn broker-toggle-btn" data-id="${account.id}" aria-label="${account.is_active ? 'Desactivar' : 'Activar'}" data-tooltip="${account.is_active ? 'Desactivar — deja de incluirse en el saldo/tenencias unificadas' : 'Activar — vuelve a incluirse en el saldo/tenencias unificadas'}">${account.is_active ? '⏸' : '▶️'}</button>
                <button type="button" class="row-action-btn broker-delete-btn" data-id="${account.id}" aria-label="Eliminar" data-tooltip="Eliminar esta cuenta (no se puede deshacer)">🗑</button>
            </td>
        </tr>
    `;
}

function brokerAccountFormHtml(account) {
    const a = account || {};
    const labels = BROKER_CREDENTIAL_LABELS[a.broker_name || 'PPI'];
    return `
        <div class="field-row">
            <div class="field">
                <label for="broker-name-select">Bróker</label>
                <select id="broker-name-select">${brokerOptionsHtml(a.broker_name)}</select>
            </div>
            <div class="field"><label for="broker-alias-input">Alias</label><input type="text" id="broker-alias-input" value="${a.alias || ''}" placeholder="ej. Cuenta principal"></div>
            <div class="field"><label for="broker-account-number-input">Número de cuenta</label><input type="text" id="broker-account-number-input" value="${a.account_number || ''}"></div>
        </div>
        <div class="field-row">
            <div class="field"><label for="broker-api-key-input" id="broker-api-key-label">${labels.key}</label><input type="text" id="broker-api-key-input" autocomplete="off" placeholder="${a.id ? 'dejar vacío para no cambiar' : ''}"></div>
            <div class="field"><label for="broker-api-secret-input" id="broker-api-secret-label">${labels.secret}</label><input type="password" id="broker-api-secret-input" autocomplete="off" placeholder="${a.id ? 'dejar vacío para no cambiar' : ''}"></div>
        </div>
        <p class="hint" id="broker-credential-hint">${labels.hint}</p>
        <p class="modal-error"></p>
        <div class="modal-footer">
            <button type="submit" class="btn btn-primary">${a.id ? 'Guardar cambios' : 'Agregar cuenta'}</button>
        </div>
    `;
}

function openBrokerAccountModal(account) {
    openModal({
        title: account ? 'Editar cuenta de bróker' : 'Agregar cuenta de bróker',
        bodyHtml: brokerAccountFormHtml(account),
        onMount: (form) => {
            form.querySelector('#broker-name-select').addEventListener('change', (e) => {
                const labels = BROKER_CREDENTIAL_LABELS[e.target.value];
                form.querySelector('#broker-api-key-label').textContent = labels.key;
                form.querySelector('#broker-api-secret-label').textContent = labels.secret;
                form.querySelector('#broker-credential-hint').textContent = labels.hint;
            });
        },
        onSubmit: async (form) => {
            const payload = {
                broker_name: form.querySelector('#broker-name-select').value,
                alias: form.querySelector('#broker-alias-input').value.trim() || null,
                account_number: form.querySelector('#broker-account-number-input').value.trim() || null,
            };
            const apiKey = form.querySelector('#broker-api-key-input').value;
            const apiSecret = form.querySelector('#broker-api-secret-input').value;
            // En edición, un campo de credencial vacío significa "no tocar" --
            // nunca se pisa una credencial ya guardada con un string vacío.
            if (!account || apiKey !== '') payload.api_key = apiKey;
            if (!account || apiSecret !== '') payload.api_secret = apiSecret;
            if (account) {
                await api.updateBrokerAccount(account.id, payload);
            } else {
                await api.addBrokerAccount(payload);
            }
            showStatus('status-banner', 'success', account ? 'Cuenta actualizada' : 'Cuenta agregada');
            await loadBrokerAccounts();
        },
    });
}

function connectionStatusLabel(result) {
    if (result.status === 'ok') return `<span class="ok">✓ ${result.detail}</span>`;
    if (result.status === 'pending_integration') return `<span class="hint">${result.detail}</span>`;
    return `<span class="fail">✗ ${result.detail}</span>`;
}

export async function loadBrokerAccounts() {
    const container = $('broker-accounts-table-container');
    if (!container) return;
    try {
        const { data: accounts } = await api.brokerAccounts();
        if (!accounts.length) {
            container.innerHTML = '<p class="table-empty">Todavía no agregaste ninguna cuenta de bróker.</p>';
            return;
        }
        container.innerHTML = `
            <table>
                <thead><tr><th>Bróker</th><th>Alias</th><th>N° de cuenta</th><th>API Key</th><th>Estado</th><th>Última prueba</th><th></th></tr></thead>
                <tbody>${accounts.map(accountRowHtml).join('')}</tbody>
            </table>
        `;
        container.querySelectorAll('.broker-test-btn').forEach((btn) => {
            btn.addEventListener('click', async () => {
                const cell = btn.closest('tr').querySelector('.broker-account-test-result');
                cell.textContent = 'Probando...';
                try {
                    const { data } = await api.testBrokerAccountConnection(btn.dataset.id);
                    cell.innerHTML = connectionStatusLabel(data);
                } catch (error) {
                    cell.innerHTML = `<span class="fail">${error.response?.data?.error || error.message}</span>`;
                }
            });
        });
        container.querySelectorAll('.broker-edit-btn').forEach((btn) => {
            btn.addEventListener('click', async () => {
                const { data: account } = await api.brokerAccounts().then((r) => ({
                    data: r.data.find((a) => String(a.id) === btn.dataset.id),
                }));
                if (account) openBrokerAccountModal(account);
            });
        });
        container.querySelectorAll('.broker-toggle-btn').forEach((btn) => {
            btn.addEventListener('click', async () => {
                try {
                    await api.toggleBrokerAccountActive(btn.dataset.id);
                    await loadBrokerAccounts();
                } catch (error) {
                    showStatus('status-banner', 'error', error.response?.data?.error || error.message);
                }
            });
        });
        container.querySelectorAll('.broker-delete-btn').forEach((btn) => {
            btn.addEventListener('click', async () => {
                if (!confirm('¿Eliminar esta cuenta de bróker?')) return;
                try {
                    await api.deleteBrokerAccount(btn.dataset.id);
                    showStatus('status-banner', 'success', 'Cuenta eliminada');
                    await loadBrokerAccounts();
                } catch (error) {
                    showStatus('status-banner', 'error', error.response?.data?.error || error.message);
                }
            });
        });
    } catch (error) {
        container.innerHTML = `<p class="table-empty">No se pudo cargar: ${error.response?.data?.error || error.message}</p>`;
    }
}

export function initBrokerConnectionsView() {
    const addBtn = $('new-broker-account-btn');
    if (addBtn) addBtn.addEventListener('click', () => openBrokerAccountModal(null));
}
