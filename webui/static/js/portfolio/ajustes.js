// Vista "Ajustes" (ver conversacion) -- 3 bloques en el MISMO lenguaje
// visual que Prestamos/Decisiones (paneles anchos apilados, nunca una
// tarjeta angosta y centrada -- ese fue el problema de la version anterior):
// Motor Cuantitativo y Estrategia, Telegram y Accesos, Preferencias
// Generales y Moneda.
import { $ } from '../shared/dom.js';
import { fmt, fmtDate } from '../shared/format.js';
import { showStatus } from '../shared/status.js';
import { openModal, closeModal } from '../shared/modal.js';
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
        renderSatelliteBreakeven();
    } catch { /* deja los inputs vacios si falla -- nunca inventa un valor */ }

    try {
        const { data } = await api.coreParams();
        $('ajustes-core-threshold-input').value = data.min_efficiency_score;
    } catch { /* idem */ }

    try {
        const { data } = await api.signalConfig();
        if (data.pairs_allocation_pct !== null && data.pairs_allocation_pct !== undefined) {
            $('ajustes-pairs-allocation-input').value = data.pairs_allocation_pct;
        }
        if (data.factor_allocation_pct !== null && data.factor_allocation_pct !== undefined) {
            $('ajustes-factor-allocation-input').value = data.factor_allocation_pct;
        }
    } catch { /* idem */ }
}

// Breakeven win rate (ver conversación/auditoría, hallazgo 2.10: "con TP 4%/
// SL 9% hace falta >69% de aciertos para empatar") -- se recalcula en vivo
// contra lo que haya tipeado en los 2 inputs en ESE momento, nunca un valor
// guardado: el usuario tiene que ver el trade-off ANTES de tocar "Guardar".
function renderSatelliteBreakeven() {
    const hint = $('ajustes-satellite-breakeven-hint');
    if (!hint) return;
    const tp = Number($('ajustes-satellite-tp-input').value);
    const sl = Number($('ajustes-satellite-sl-input').value);
    if (!tp || !sl || tp <= 0 || sl <= 0) {
        hint.textContent = '';
        return;
    }
    const breakeven = (sl / (tp + sl)) * 100;
    hint.textContent = `Con este TP/SL hace falta >${fmt(breakeven, 0)}% de aciertos para empatar en el largo plazo.`;
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

    $('ajustes-satellite-tp-input').addEventListener('input', renderSatelliteBreakeven);
    $('ajustes-satellite-sl-input').addEventListener('input', renderSatelliteBreakeven);

    $('ajustes-save-quant-btn').addEventListener('click', async () => {
        const take_profit_pct = Number($('ajustes-satellite-tp-input').value);
        const stop_loss_pct = Number($('ajustes-satellite-sl-input').value);
        const min_efficiency_score = Number($('ajustes-core-threshold-input').value);
        const pairs_allocation_pct = Number($('ajustes-pairs-allocation-input').value);
        const factor_allocation_pct = Number($('ajustes-factor-allocation-input').value);
        try {
            await Promise.all([
                api.updateSatelliteExitParams({ take_profit_pct, stop_loss_pct }),
                api.updateCoreParams({ min_efficiency_score }),
                api.updateSignalConfig({ pairs_allocation_pct, factor_allocation_pct }),
            ]);
            showStatus('status-banner', 'success', 'Configuración del motor actualizada');
        } catch (error) {
            showStatus('status-banner', 'error', error.response?.data?.error || error.message);
        }
    });
}

// --- Escaner de liquidez / "aduana de liquidez" (ver conversacion: "que los
// parametros del escaner... sean 100% configurables") -- mismo patron de
// carga + boton "Guardar" que el bloque de arriba (Controles de
// configuracion), ver portfolio/scanner.py para la logica real.

let scannerMode = 'curated';

function setScannerModeActive(mode) {
    scannerMode = mode;
    document.querySelectorAll('#ajustes-scanner-mode-toggle .segmented-toggle-option').forEach((btn) => {
        btn.classList.toggle('active', btn.dataset.value === mode);
    });
}

async function loadScannerControls() {
    try {
        const { data } = await api.signalConfig();
        setScannerModeActive(data.scanner_mode || 'curated');
        $('ajustes-scanner-volume-input').value = data.min_daily_volume_usd;
        $('ajustes-scanner-spread-input').value = data.max_bid_ask_spread_pct;
        $('ajustes-scanner-operating-days-input').value = data.min_operating_days_month;
    } catch { /* deja los inputs vacios si falla -- nunca inventa un valor */ }
}

function initScannerControls() {
    document.querySelectorAll('#ajustes-scanner-mode-toggle .segmented-toggle-option').forEach((btn) => {
        btn.addEventListener('click', () => setScannerModeActive(btn.dataset.value));
    });

    $('ajustes-save-scanner-btn').addEventListener('click', async () => {
        const payload = {
            scanner_mode: scannerMode,
            min_daily_volume_usd: Number($('ajustes-scanner-volume-input').value),
            max_bid_ask_spread_pct: Number($('ajustes-scanner-spread-input').value),
            min_operating_days_month: Number($('ajustes-scanner-operating-days-input').value),
        };
        try {
            await api.updateSignalConfig(payload);
            showStatus('status-banner', 'success', 'Configuración del escáner actualizada');
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

// FIX (ver auditoria 19/10/2026, hallazgo 1.4): antes esta tarjeta mostraba
// un rango "expected_return_usd_low/high_pct" + "sharpe_low/high"
// HARDCODEADO, nunca atado al backtest real -- Moderado prometía +23,6% a
// +26,7% cuando el backtest real a esa misma asignación daba +5,3%. Ahora
// backend/config.py::list_risk_profiles resuelve las métricas reales contra
// el backtest guardado (dual_engine_backtest_summary); si no hay un
// backtest corrido EXACTO a la asignación de este perfil,
// backtest_validated viene en false y se muestra "Sin backtest a esta
// mezcla" en vez de inventar un número.
function profileCardHtml(profile, activeProfileId) {
    const isActive = profile.id === activeProfileId;
    const satellitePct = Math.round((100 - profile.core_pct) * 100) / 100;
    const resultMetricsHtml = profile.backtest_validated
        ? `
            <div class="mini-stat">
                <div class="l">Retorno Anual USD (backtest)${INDICATOR_TOOLTIPS.retornoUsd}</div>
                <div class="v">${profile.annualized_return_pct >= 0 ? '+' : ''}${fmt(profile.annualized_return_pct, 1)}%</div>
            </div>
            <div class="mini-stat">
                <div class="l">Sharpe (backtest)${INDICATOR_TOOLTIPS.sharpe}</div>
                <div class="v">${fmt(profile.sharpe_ratio, 2)}</div>
            </div>
            <div class="mini-stat">
                <div class="l">Max Drawdown (backtest)${INDICATOR_TOOLTIPS.maxDrawdown}</div>
                <div class="v">${fmt(profile.max_drawdown_pct, 1)}%</div>
            </div>
        `
        : `
            <div class="mini-stat span2">
                <div class="l">Resultado esperado</div>
                <div class="v"><span class="hint">Sin backtest corrido a esta asignación exacta — ver "Rendimiento Histórico" en Decisiones para las combinaciones ya validadas.</span></div>
            </div>
        `;
    // Un solo nivel (col-4 + panel en el MISMO div, ver conversación, Fase 5)
    // -- antes .col-4 envolvía un .panel aparte, lo que lo dejaba afuera de
    // la regla ".grid12 > .panel" (flex-column, alto parejo con el resto de
    // la fila) porque esa regla pide hijo DIRECTO de .grid12. Mismo patrón
    // que gananciaAsideHtml ya usa (<aside class="panel col-4">).
    // El badge "Activo" ya dice que este es el perfil activo -- un botón
    // deshabilitado "Perfil Activo" debajo repetía la misma info sin ninguna
    // acción posible, solo ruido visual.
    return `
        <div class="col-4 panel ajustes-profile-card ${isActive ? 'active' : ''}">
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
                    ${resultMetricsHtml}
                    <div class="mini-stat">
                        <div class="l">Stop Loss / Take Profit</div>
                        <div class="v">-${fmt(profile.stop_loss_pct, 1)}% / +${fmt(profile.take_profit_pct, 1)}%</div>
                    </div>
                </div>
                ${isActive ? '' : `
                    <button type="button" class="btn btn-primary ajustes-activate-profile-btn"
                            data-profile-id="${profile.id}" style="width: 100%;">
                        Activar Perfil
                    </button>
                `}
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

// Interruptor maestro de ejecución real (ver conversación, "diseñemos el
// interruptor" -- portfolio/broker_client.py::ensure_auto_trading_enabled
// es el único punto que lo chequea; esto solo lo prende/apaga). Vive en
// app_settings (api.settings/updateSettings), no en signal_config -- es un
// gate de "qué puede hacer esta instalación", no un parámetro de estrategia.
function setAutoTradingLimitModeActive(mode) {
    document.querySelectorAll('#auto-trading-limit-mode-toggle .segmented-toggle-option').forEach((btn) => {
        btn.classList.toggle('active', btn.dataset.value === mode);
    });
    $('auto-trading-limit-pct-field').hidden = mode !== 'pct';
    $('auto-trading-limit-ars-field').hidden = mode !== 'fixed';
}

function renderAutoTradingLimitResolved(settings) {
    const el = $('auto-trading-limit-resolved');
    if (!el) return;
    if (settings.auto_trading_limit_mode === 'fixed') {
        el.textContent = settings.auto_trading_limit_ars
            ? `Tope configurado: ${fmt(settings.auto_trading_limit_ars, 0)} ARS fijos.`
            : 'Sin monto fijo configurado todavía — el tope efectivo es $0 (bloquea todo) hasta que cargues uno.';
    } else {
        el.textContent = settings.auto_trading_limit_pct
            ? `Tope configurado: ${fmt(settings.auto_trading_limit_pct, 0)}% del patrimonio total.`
            : 'Sin % configurado todavía — el tope efectivo es $0 (bloquea todo) hasta que cargues uno.';
    }
}

async function loadAutoTradingControl() {
    const { data: settings } = await api.settings();
    const enabled = !!settings.auto_trading_enabled;
    $('auto-trading-enabled-switch').checked = enabled;
    $('auto-trading-status-label').textContent = enabled
        ? 'Ejecución real: ENCENDIDA'
        : 'Ejecución real: Apagada — hoy no hay ningún código que la use todavía.';

    setAutoTradingLimitModeActive(settings.auto_trading_limit_mode || 'pct');
    if (settings.auto_trading_limit_pct !== null && settings.auto_trading_limit_pct !== undefined) {
        $('auto-trading-limit-pct-input').value = settings.auto_trading_limit_pct;
    }
    if (settings.auto_trading_limit_ars !== null && settings.auto_trading_limit_ars !== undefined) {
        $('auto-trading-limit-ars-input').value = settings.auto_trading_limit_ars;
    }
    renderAutoTradingLimitResolved(settings);
}

function initAutoTradingControl() {
    document.querySelectorAll('#auto-trading-limit-mode-toggle .segmented-toggle-option').forEach((btn) => {
        btn.addEventListener('click', async () => {
            setAutoTradingLimitModeActive(btn.dataset.value);
            await api.updateSettings({ auto_trading_limit_mode: btn.dataset.value });
            await loadAutoTradingControl();
        });
    });
    let limitSaveTimer = null;
    $('auto-trading-limit-pct-input').addEventListener('input', (e) => {
        clearTimeout(limitSaveTimer);
        const raw = e.target.value.trim();
        limitSaveTimer = setTimeout(async () => {
            await api.updateSettings({ auto_trading_limit_pct: raw === '' ? null : Number(raw) });
            await loadAutoTradingControl();
        }, 500);
    });
    $('auto-trading-limit-ars-input').addEventListener('input', (e) => {
        clearTimeout(limitSaveTimer);
        const raw = e.target.value.trim();
        limitSaveTimer = setTimeout(async () => {
            await api.updateSettings({ auto_trading_limit_ars: raw === '' ? null : Number(raw) });
            await loadAutoTradingControl();
        }, 500);
    });

    $('auto-trading-enabled-switch').addEventListener('change', async (e) => {
        if (!e.target.checked) {
            // Apagar nunca pide confirmación -- siempre es seguro apagar de una.
            await api.updateSettings({ auto_trading_enabled: 0 });
            await loadAutoTradingControl();
            showStatus('status-banner', 'success', 'Ejecución real desactivada.');
            return;
        }
        // Encender SÍ pide confirmación explícita -- este interruptor no
        // hace nada todavía, pero la fricción tiene que estar desde el día
        // uno, no agregarse recién cuando haya código real del otro lado.
        // Modal propio, NUNCA confirm() nativo del navegador (ver
        // conversación: "ese popup no pertenece a la app" -- se ve
        // completamente ajeno al resto de la UI).
        let confirmed = false;
        openModal({
            title: 'Encender ejecución real de órdenes',
            bodyHtml: `
                <p>Vas a encender el interruptor maestro de ejecución real de órdenes.</p>
                <p class="hint">Hoy ningún código de Kronos lo usa todavía (no hay ejecución automática construida), pero esto es exactamente lo que cualquier función futura de "mandar una orden real" va a chequear antes de operar.</p>
                <div class="modal-footer">
                    <button type="button" class="btn btn-secondary modal-close-btn">Cancelar</button>
                    <button type="submit" class="btn btn-primary">Confirmar</button>
                </div>
            `,
            onMount: (form) => {
                form.querySelector('.modal-close-btn')?.addEventListener('click', () => closeModal());
            },
            onSubmit: async () => { confirmed = true; },
            onClose: async () => {
                if (!confirmed) {
                    e.target.checked = false;
                    return;
                }
                await api.updateSettings({ auto_trading_enabled: 1 });
                await loadAutoTradingControl();
                showStatus('status-banner', 'success', 'Ejecución real ACTIVADA.');
            },
        });
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
    $('ajustes-inflation-adjusted-switch').checked = !!settings.ars_inflation_adjusted;

    const { data: config } = await api.signalConfig();
    if (config.caucion_reference_tna !== null && config.caucion_reference_tna !== undefined) {
        $('ajustes-caucion-reference-input').value = config.caucion_reference_tna;
    }
    // Sugerencia desde cauciones reales (ver conversación/auditoría,
    // hallazgo 2.2) -- solo se muestra si difiere de lo cargado a mano (si
    // ya coinciden, no hay nada que sugerir).
    const suggestedHint = $('ajustes-caucion-suggested-hint');
    if (suggestedHint) {
        const suggested = config.caucion_reference_tna_suggested;
        const current = config.caucion_reference_tna;
        if (suggested !== null && suggested !== undefined && suggested !== current) {
            suggestedHint.hidden = false;
            suggestedHint.innerHTML = `Según tus cauciones reales: ${fmt(suggested, 1)}% <a href="#" id="ajustes-caucion-use-suggested">usar</a>`;
            suggestedHint.querySelector('#ajustes-caucion-use-suggested').addEventListener('click', (e) => {
                e.preventDefault();
                const input = $('ajustes-caucion-reference-input');
                input.value = suggested;
                input.dispatchEvent(new Event('input'));
            });
        } else {
            suggestedHint.hidden = true;
        }
    }

    // API keys de IA (ver conversación, "APIs de IA" en Integraciones) --
    // el input SIEMPRE arranca vacío (nunca se re-llena con la key real ni
    // con la enmascarada, mismo criterio que broker-connections.js) -- el
    // label y el placeholder son los únicos que reflejan "ya configurada".
    $('ajustes-finnhub-key-label').textContent = config.finnhub_api_key_set
        ? `Finnhub API Key (configurada: ${config.finnhub_api_key})` : 'Finnhub API Key';
    $('ajustes-finnhub-key-input').placeholder = config.finnhub_api_key_set ? 'dejar vacío para no cambiar' : 'sin costo en finnhub.io';
    $('ajustes-openai-key-label').textContent = config.openai_api_key_set
        ? `OpenAI API Key (configurada: ${config.openai_api_key})` : 'OpenAI API Key';
    $('ajustes-openai-key-input').placeholder = config.openai_api_key_set ? 'dejar vacío para no cambiar' : 'sk-...';
    $('ajustes-anthropic-key-label').textContent = config.anthropic_api_key_set
        ? `Anthropic API Key (configurada: ${config.anthropic_api_key})` : 'Anthropic API Key';
    $('ajustes-anthropic-key-input').placeholder = config.anthropic_api_key_set ? 'dejar vacío para no cambiar' : 'sk-ant-...';
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
    $('ajustes-inflation-adjusted-switch').addEventListener('change', async (e) => {
        await api.updateSettings({ ars_inflation_adjusted: e.target.checked ? 1 : 0 });
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

    // Solo se manda al backend la key que el usuario de verdad tipeó --
    // dejar un campo vacío NUNCA borra una key ya configurada (mismo
    // criterio que broker-connections.js: "dejar vacío para no cambiar").
    $('ajustes-save-llm-keys-btn').addEventListener('click', async () => {
        const finnhubKey = $('ajustes-finnhub-key-input').value.trim();
        const openaiKey = $('ajustes-openai-key-input').value.trim();
        const anthropicKey = $('ajustes-anthropic-key-input').value.trim();
        const payload = {};
        if (finnhubKey !== '') payload.finnhub_api_key = finnhubKey;
        if (openaiKey !== '') payload.openai_api_key = openaiKey;
        if (anthropicKey !== '') payload.anthropic_api_key = anthropicKey;
        if (!Object.keys(payload).length) return;
        await api.updateSignalConfig(payload);
        await loadPreferencesControls();
        showStatus('status-banner', 'success', 'API keys guardadas.');
    });
}

// --- Bloque 4: Reporte PDF ---------------------------------------------------
// Renombrado de "Notificaciones" (ver conversación, Fase 6) -- colisionaba
// con la campana de la barra lateral (esa sí son notificaciones de verdad:
// vencimientos, señales). Esto siempre fue, nada más, la configuración del
// PDF y su envío por Telegram -- nunca hubo acá un ajuste de qué dispara una
// alerta o no.

async function loadPdfReportControls() {
    const { data: settings } = await api.settings();
    $('ajustes-pdf-currency-select').value = settings.pdf_currency;
    $('ajustes-pdf-include-risk').checked = !!settings.pdf_include_risk_metrics;
    $('ajustes-pdf-include-paper').checked = !!settings.pdf_include_paper_trading;
    $('ajustes-pdf-include-caucion').checked = !!settings.pdf_include_yield_caucion;
    $('ajustes-pdf-include-opportunities').checked = !!settings.pdf_include_opportunities;
    $('ajustes-pdf-schedule-select').value = settings.pdf_report_schedule;
    $('ajustes-pdf-time-input').value = settings.pdf_report_time;
    $('ajustes-pdf-time-afternoon-input').value = settings.pdf_report_time_afternoon;
    $('ajustes-pdf-weekdays-only-switch').checked = !!settings.pdf_report_weekdays_only;
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
    $('ajustes-pdf-time-afternoon-input').addEventListener('change', async (e) => {
        if (!e.target.value) return;
        await api.updateSettings({ pdf_report_time_afternoon: e.target.value });
        showStatus('status-banner', 'success', 'Hora de envío actualizada');
    });
    $('ajustes-pdf-weekdays-only-switch').addEventListener('change', async (e) => {
        await api.updateSettings({ pdf_report_weekdays_only: e.target.checked ? 1 : 0 });
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

    // Descarga directa (ver conversación/auditoría, hallazgo 2.6) -- misma
    // generación lenta (~1 min, arma gráficos + tablas con reportlab) que el
    // envío por Telegram, así que se avisa igual mientras se espera.
    $('ajustes-pdf-download-btn').addEventListener('click', async () => {
        const btn = $('ajustes-pdf-download-btn');
        const result = $('ajustes-pdf-test-send-result');
        btn.disabled = true;
        result.innerHTML = '<p class="hint">Generando el PDF — puede tardar hasta 1 minuto...</p>';
        try {
            const response = await api.downloadPdfReport();
            const blobUrl = URL.createObjectURL(response.data);
            const link = document.createElement('a');
            link.href = blobUrl;
            link.download = `kronos_reporte_${new Date().toISOString().slice(0, 10)}.pdf`;
            document.body.appendChild(link);
            link.click();
            link.remove();
            URL.revokeObjectURL(blobUrl);
            result.innerHTML = '<p class="hint">PDF descargado.</p>';
        } catch (error) {
            // responseType 'blob' hace que un error 500 (JSON) tambien
            // llegue como Blob -- hay que leerlo como texto para mostrar el
            // motivo real en vez de "[object Blob]".
            let message = error.message;
            if (error.response?.data instanceof Blob) {
                try {
                    const text = await error.response.data.text();
                    message = JSON.parse(text).error || message;
                } catch { /* si ni eso se puede parsear, se queda con error.message */ }
            }
            result.innerHTML = `<p class="hint">${message}</p>`;
        } finally {
            btn.disabled = false;
        }
    });
}

export function initAjustesView() {
    initQuantControls();
    loadQuantControls();
    initScannerControls();
    loadScannerControls();
    loadRiskProfiles();

    const addUserBtn = $('new-telegram-user-btn');
    if (addUserBtn) addUserBtn.addEventListener('click', () => openAddTelegramUserModal());
    $('telegram-test-message-btn').addEventListener('click', sendTelegramTestMessage);
    loadTelegramBotStatus();

    initAutoTradingControl();
    loadAutoTradingControl();

    initPreferencesControls();
    loadPreferencesControls();

    initPdfReportControls();
    loadPdfReportControls();
}
