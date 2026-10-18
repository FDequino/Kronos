// Vista "Ajustes" (ver conversacion) -- 3 bloques en el MISMO lenguaje
// visual que Prestamos/Decisiones (paneles anchos apilados, nunca una
// tarjeta angosta y centrada -- ese fue el problema de la version anterior):
// Motor Cuantitativo y Estrategia, Telegram y Accesos, Preferencias
// Generales y Moneda.
import { $ } from '../shared/dom.js';
import { fmt, fmtDate } from '../shared/format.js';
import { showStatus } from '../shared/status.js';
import { openModal } from '../shared/modal.js';
import { INDICATOR_TOOLTIPS } from './core-satellite.js';
import { api } from './api.js';

// --- Bloque 1: Motor Cuantitativo y Estrategia ------------------------------

let quantAllocation = { core_pct: 20, satellite_pct: 80 };

function renderQuantAllocationLabels() {
    $('ajustes-core-pct-label').textContent = fmt(quantAllocation.core_pct, 0);
    $('ajustes-satellite-pct-label').textContent = fmt(quantAllocation.satellite_pct, 0);
}

async function loadQuantControls() {
    try {
        const { data } = await api.capitalAllocation();
        quantAllocation = data;
    } catch { /* se queda con el default 20/80 si el endpoint falla */ }
    $('ajustes-core-allocation-slider').value = quantAllocation.core_pct;
    renderQuantAllocationLabels();

    try {
        const { data } = await api.satelliteExitParams();
        $('ajustes-satellite-tp-input').value = data.take_profit_pct;
        $('ajustes-satellite-sl-input').value = data.stop_loss_pct;
    } catch { /* deja los inputs vacios si falla -- nunca inventa un valor */ }

    try {
        const { data } = await api.coreParams();
        $('ajustes-core-threshold-input').value = data.min_efficiency_score;
    } catch { /* idem */ }
}

function initQuantControls() {
    const slider = $('ajustes-core-allocation-slider');
    slider.addEventListener('input', () => {
        const corePct = Number(slider.value);
        quantAllocation = { core_pct: corePct, satellite_pct: Math.round((100 - corePct) * 100) / 100 };
        renderQuantAllocationLabels();
    });
    slider.addEventListener('change', async () => {
        try {
            const { data } = await api.setCapitalAllocation(quantAllocation.core_pct);
            quantAllocation = data;
            renderQuantAllocationLabels();
            showStatus('status-banner', 'success', `Asignación actualizada: Core ${fmt(data.core_pct, 0)}% / Satélite ${fmt(data.satellite_pct, 0)}%`);
        } catch (error) {
            showStatus('status-banner', 'error', error.response?.data?.error || error.message);
        }
    });

    $('ajustes-save-quant-btn').addEventListener('click', async () => {
        const take_profit_pct = Number($('ajustes-satellite-tp-input').value);
        const stop_loss_pct = Number($('ajustes-satellite-sl-input').value);
        const min_efficiency_score = Number($('ajustes-core-threshold-input').value);
        try {
            await Promise.all([
                api.updateSatelliteExitParams({ take_profit_pct, stop_loss_pct }),
                api.updateCoreParams({ min_efficiency_score }),
            ]);
            showStatus('status-banner', 'success', 'Configuración del motor actualizada');
        } catch (error) {
            showStatus('status-banner', 'error', error.response?.data?.error || error.message);
        }
    });
}

// --- Perfiles de Inversion (ver conversacion) -------------------------------
//
// "Activo" se deriva en el backend de los parametros REALES (ver
// portfolio/config.py::get_active_risk_profile) -- nunca un flag separado
// que pueda desincronizarse si alguien toca los sliders de abajo a mano.

function profileCardHtml(profile, activeProfileId) {
    const isActive = profile.id === activeProfileId;
    const satellitePct = Math.round((100 - profile.core_pct) * 100) / 100;
    const sharpeRange = profile.sharpe_low === profile.sharpe_high
        ? fmt(profile.sharpe_low, 2)
        : `${fmt(profile.sharpe_low, 2)} a ${fmt(profile.sharpe_high, 2)}`;
    return `
        <div class="col-4">
            <div class="panel ajustes-profile-card ${isActive ? 'active' : ''}">
                <div class="panel-header">
                    <div class="panel-header-text"><h2>${profile.label}</h2></div>
                    ${isActive ? '<span class="tag tag-positive">Activo</span>' : ''}
                </div>
                <div class="panel-body">
                    <div class="mini-stat-grid">
                        <div class="mini-stat span2">
                            <div class="l">Asignación</div>
                            <div class="v">Core ${fmt(profile.core_pct, 0)}% / Satélite ${fmt(satellitePct, 0)}%</div>
                        </div>
                        <div class="mini-stat">
                            <div class="l">Retorno Est. USD${INDICATOR_TOOLTIPS.dispersionP10P90}</div>
                            <div class="v">+${fmt(profile.expected_return_usd_low_pct, 1)}% a +${fmt(profile.expected_return_usd_high_pct, 1)}%</div>
                        </div>
                        <div class="mini-stat">
                            <div class="l">Sharpe${INDICATOR_TOOLTIPS.sharpe}</div>
                            <div class="v">${sharpeRange}</div>
                        </div>
                        <div class="mini-stat">
                            <div class="l">Max Drawdown${INDICATOR_TOOLTIPS.maxDrawdown}</div>
                            <div class="v">${fmt(profile.max_drawdown_pct, 1)}%</div>
                        </div>
                        <div class="mini-stat">
                            <div class="l">Stop Loss / Take Profit</div>
                            <div class="v">-${fmt(profile.stop_loss_pct, 1)}% / +${fmt(profile.take_profit_pct, 1)}%</div>
                        </div>
                    </div>
                    <button type="button" class="btn ${isActive ? 'btn-secondary' : 'btn-primary'} ajustes-activate-profile-btn"
                            data-profile-id="${profile.id}" ${isActive ? 'disabled' : ''} style="width: 100%;">
                        ${isActive ? 'Perfil Activo' : 'Activar Perfil'}
                    </button>
                </div>
            </div>
        </div>
    `;
}

async function loadRiskProfiles() {
    const container = $('ajustes-risk-profiles-container');
    if (!container) return;
    try {
        const { data } = await api.riskProfiles();
        container.innerHTML = data.profiles.map((p) => profileCardHtml(p, data.active_profile)).join('');
        container.querySelectorAll('.ajustes-activate-profile-btn').forEach((btn) => {
            btn.addEventListener('click', async () => {
                btn.disabled = true;
                try {
                    await api.activateRiskProfile(btn.dataset.profileId);
                    showStatus('status-banner', 'success', 'Perfil activado');
                    await loadRiskProfiles();
                    await loadQuantControls();
                } catch (error) {
                    showStatus('status-banner', 'error', error.response?.data?.error || error.message);
                    btn.disabled = false;
                }
            });
        });
    } catch (error) {
        container.innerHTML = `<p class="table-empty">No se pudieron cargar los perfiles: ${error.response?.data?.error || error.message}</p>`;
    }
}

// --- Bloque 2: Telegram y Accesos -------------------------------------------

function fmtCreatedAt(createdAt) {
    // created_at sale de SQLite como "YYYY-MM-DD HH:MM:SS" (datetime('now')),
    // no una fecha pura -- fmtDate espera solo la parte de fecha.
    if (!createdAt) return '—';
    const [datePart, timePart] = createdAt.split(' ');
    const dateFmt = fmtDate(datePart);
    return timePart ? `${dateFmt} ${timePart.slice(0, 5)}` : dateFmt;
}

function roleBadgeHtml(role) {
    const label = role === 'admin' ? 'Admin' : 'Viewer';
    return `<span class="ajustes-role-badge ajustes-role-${role}">${label}</span>`;
}

function telegramUserRowHtml(user) {
    const label = user.label ? user.label : '<span class="hint">(sin label)</span>';
    return `
        <tr data-id="${user.id}">
            <td>${label}</td>
            <td class="mono">${user.chat_id}</td>
            <td>${roleBadgeHtml(user.role)}</td>
            <td>${fmtCreatedAt(user.created_at)}</td>
            <td class="row-actions">
                <button type="button" class="row-action-btn telegram-user-delete-btn" data-id="${user.id}" aria-label="Revocar acceso">🗑</button>
            </td>
        </tr>
    `;
}

export async function loadTelegramUsers() {
    const container = $('telegram-users-table-container');
    if (!container) return;
    try {
        const { data: users } = await api.telegramUsers();
        if (!users.length) {
            container.innerHTML = '<p class="table-empty">Nadie autorizado todavía — el primer /start al bot se autoriza solo, como admin.</p>';
            return;
        }
        container.innerHTML = `
            <table>
                <thead><tr><th>Label</th><th>Chat ID</th><th>Rol</th><th>Fecha</th><th></th></tr></thead>
                <tbody>${users.map(telegramUserRowHtml).join('')}</tbody>
            </table>
        `;
        container.querySelectorAll('.telegram-user-delete-btn').forEach((btn) => {
            btn.addEventListener('click', async () => {
                if (!confirm('¿Revocar el acceso de este usuario al bot?')) return;
                try {
                    await api.deleteTelegramUser(btn.dataset.id);
                    showStatus('status-banner', 'success', 'Acceso revocado');
                    await loadTelegramUsers();
                } catch (error) {
                    showStatus('status-banner', 'error', error.response?.data?.error || error.message);
                }
            });
        });
    } catch (error) {
        container.innerHTML = `<p class="table-empty">No se pudo cargar: ${error.response?.data?.error || error.message}</p>`;
    }
}

async function loadTelegramBotStatus() {
    const container = $('telegram-bot-status');
    if (!container) return;
    try {
        const { data } = await api.telegramBotStatus();
        const tokenValue = data.configured ? `Configurado (···${data.token_suffix})` : 'No configurado';
        const runningValue = data.running ? 'Corriendo' : 'Detenido';
        container.innerHTML = `
            <div class="mini-stat"><div class="l">Token</div><div class="v">${tokenValue}</div></div>
            <div class="mini-stat"><div class="l">Estado de conexión</div><div class="v">${runningValue}</div></div>
        `;
        if (!data.configured) {
            container.innerHTML += '<p class="hint" style="margin: 8px 4px 0; flex-basis: 100%;">El token se configura por variable de entorno (TELEGRAM_BOT_TOKEN), no desde acá — es una credencial, nunca se edita vía web.</p>';
        }
    } catch (error) {
        container.innerHTML = `<p class="hint">No se pudo consultar el estado del bot: ${error.response?.data?.error || error.message}</p>`;
    }
}

function testMessageRowHtml(r) {
    const label = r.label || r.chat_id;
    const statusText = r.ok ? 'enviado' : r.detail;
    return `<div class="ajustes-test-message-row ${r.ok ? 'ok' : 'fail'}"><span>${label} (${r.chat_id})</span><span>${statusText}</span></div>`;
}

async function sendTelegramTestMessage() {
    const btn = $('telegram-test-message-btn');
    const result = $('telegram-test-message-result');
    btn.disabled = true;
    result.innerHTML = '<p class="hint">Enviando...</p>';
    try {
        const { data } = await api.telegramTestMessage();
        result.innerHTML = `<div class="ajustes-test-message-result">${data.results.map(testMessageRowHtml).join('')}</div>`;
    } catch (error) {
        result.innerHTML = `<p class="hint">${error.response?.data?.error || error.message}</p>`;
    } finally {
        btn.disabled = false;
    }
}

function openAddTelegramUserModal() {
    openModal({
        title: 'Agregar usuario de Telegram',
        bodyHtml: `
            <div class="field-row">
                <div class="field"><label for="telegram-user-label-input">Label</label><input type="text" id="telegram-user-label-input" placeholder="ej. Socio 1"></div>
                <div class="field"><label for="telegram-user-chatid-input">Chat ID</label><input type="text" id="telegram-user-chatid-input" required></div>
                <div class="field">
                    <label for="telegram-user-role-select">Rol</label>
                    <select id="telegram-user-role-select">
                        <option value="viewer">Viewer (solo consulta)</option>
                        <option value="admin">Admin (puede cambiar configuración)</option>
                    </select>
                </div>
            </div>
            <p class="hint">El Chat ID se consigue mandándole cualquier mensaje al bot — si todavía no está autorizado, responde con su propio Chat ID para poder pedirlo acá.</p>
            <p class="modal-error"></p>
            <div class="modal-footer">
                <button type="submit" class="btn btn-primary">Agregar</button>
            </div>
        `,
        onSubmit: async (form) => {
            const chat_id = form.querySelector('#telegram-user-chatid-input').value.trim();
            const label = form.querySelector('#telegram-user-label-input').value.trim() || null;
            const role = form.querySelector('#telegram-user-role-select').value;
            await api.addTelegramUser({ chat_id, label, role });
            showStatus('status-banner', 'success', 'Usuario agregado');
            await loadTelegramUsers();
        },
    });
}

// --- Bloque 3: Preferencias Generales y Moneda ------------------------------

function setAjustesCurrencyActive(currency) {
    document.querySelectorAll('#ajustes-currency-toggle .segmented-toggle-option').forEach((btn) => {
        btn.classList.toggle('active', btn.dataset.value === currency);
    });
}

function setAjustesUsdSourceActive(source) {
    document.querySelectorAll('#ajustes-usd-source-toggle .segmented-toggle-option').forEach((btn) => {
        btn.classList.toggle('active', btn.dataset.value === source);
    });
    $('ajustes-bna-variant-field').hidden = source !== 'bna';
}

function notifyCurrencyChanged(currency) {
    document.dispatchEvent(new CustomEvent('currency-changed', { detail: currency }));
}

async function loadPreferencesControls() {
    const { data: settings } = await api.settings();
    setAjustesCurrencyActive(settings.display_currency);
    setAjustesUsdSourceActive(settings.usd_rate_source);
    $('ajustes-bna-variant-select').value = settings.bna_rate_variant;

    const { data: config } = await api.signalConfig();
    if (config.caucion_reference_tna !== null && config.caucion_reference_tna !== undefined) {
        $('ajustes-caucion-reference-input').value = config.caucion_reference_tna;
    }
}

function initPreferencesControls() {
    document.querySelectorAll('#ajustes-currency-toggle .segmented-toggle-option').forEach((btn) => {
        btn.addEventListener('click', async () => {
            const next = btn.dataset.value;
            setAjustesCurrencyActive(next);
            await api.updateSettings({ display_currency: next });
            notifyCurrencyChanged(next);
        });
    });
    document.querySelectorAll('#ajustes-usd-source-toggle .segmented-toggle-option').forEach((btn) => {
        btn.addEventListener('click', async () => {
            const next = btn.dataset.value;
            setAjustesUsdSourceActive(next);
            await api.updateSettings({ usd_rate_source: next });
            notifyCurrencyChanged($('ajustes-currency-toggle').querySelector('.active')?.dataset.value);
        });
    });
    $('ajustes-bna-variant-select').addEventListener('change', async (e) => {
        await api.updateSettings({ bna_rate_variant: e.target.value });
        notifyCurrencyChanged($('ajustes-currency-toggle').querySelector('.active')?.dataset.value);
    });
    let caucionSaveTimer = null;
    $('ajustes-caucion-reference-input').addEventListener('input', (e) => {
        clearTimeout(caucionSaveTimer);
        const raw = e.target.value.trim();
        const value = raw === '' ? null : Number(raw);
        caucionSaveTimer = setTimeout(() => {
            api.updateSignalConfig({ caucion_reference_tna: value });
        }, 500);
    });

    // Si la moneda/fuente se cambia desde el dropdown de Preferencias (u otra
    // pestaña), esta vista se mantiene sincronizada sin tener que recargar.
    document.addEventListener('currency-changed', () => { loadPreferencesControls(); });
}

// --- Bloque 4: Reporte PDF y Notificaciones ---------------------------------

async function loadPdfReportControls() {
    const { data: settings } = await api.settings();
    $('ajustes-pdf-currency-select').value = settings.pdf_currency;
    $('ajustes-pdf-include-risk').checked = !!settings.pdf_include_risk_metrics;
    $('ajustes-pdf-include-paper').checked = !!settings.pdf_include_paper_trading;
    $('ajustes-pdf-include-caucion').checked = !!settings.pdf_include_yield_caucion;
    $('ajustes-pdf-include-opportunities').checked = !!settings.pdf_include_opportunities;
    $('ajustes-pdf-schedule-select').value = settings.pdf_report_schedule;
    $('ajustes-pdf-time-input').value = settings.pdf_report_time;
}

function initPdfReportControls() {
    $('ajustes-pdf-currency-select').addEventListener('change', async (e) => {
        await api.updateSettings({ pdf_currency: e.target.value });
        showStatus('status-banner', 'success', 'Moneda del reporte actualizada');
    });
    $('ajustes-pdf-include-risk').addEventListener('change', async (e) => {
        await api.updateSettings({ pdf_include_risk_metrics: e.target.checked ? 1 : 0 });
    });
    $('ajustes-pdf-include-paper').addEventListener('change', async (e) => {
        await api.updateSettings({ pdf_include_paper_trading: e.target.checked ? 1 : 0 });
    });
    $('ajustes-pdf-include-caucion').addEventListener('change', async (e) => {
        await api.updateSettings({ pdf_include_yield_caucion: e.target.checked ? 1 : 0 });
    });
    $('ajustes-pdf-include-opportunities').addEventListener('change', async (e) => {
        await api.updateSettings({ pdf_include_opportunities: e.target.checked ? 1 : 0 });
    });
    $('ajustes-pdf-schedule-select').addEventListener('change', async (e) => {
        await api.updateSettings({ pdf_report_schedule: e.target.value });
        showStatus('status-banner', 'success', 'Horario de envío actualizado');
    });
    $('ajustes-pdf-time-input').addEventListener('change', async (e) => {
        if (!e.target.value) return;
        await api.updateSettings({ pdf_report_time: e.target.value });
        showStatus('status-banner', 'success', 'Hora de envío actualizada');
    });

    $('ajustes-pdf-test-send-btn').addEventListener('click', async () => {
        const btn = $('ajustes-pdf-test-send-btn');
        const result = $('ajustes-pdf-test-send-result');
        btn.disabled = true;
        result.innerHTML = '<p class="hint">Generando y enviando...</p>';
        try {
            const { data } = await api.telegramTestPdfReport();
            if (data.ok) {
                result.innerHTML = `<p class="hint">Enviado a ${data.sent_to} usuario(s).</p>`;
            } else {
                result.innerHTML = `<p class="hint">No se pudo enviar: ${(data.errors || []).join('; ') || 'error desconocido'}</p>`;
            }
        } catch (error) {
            result.innerHTML = `<p class="hint">${error.response?.data?.error || error.message}</p>`;
        } finally {
            btn.disabled = false;
        }
    });
}

export function initAjustesView() {
    initQuantControls();
    loadQuantControls();
    loadRiskProfiles();

    const addUserBtn = $('new-telegram-user-btn');
    if (addUserBtn) addUserBtn.addEventListener('click', () => openAddTelegramUserModal());
    $('telegram-test-message-btn').addEventListener('click', sendTelegramTestMessage);
    loadTelegramBotStatus();

    initPreferencesControls();
    loadPreferencesControls();

    initPdfReportControls();
    loadPdfReportControls();
}
