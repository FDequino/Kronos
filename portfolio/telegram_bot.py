"""Bot de Telegram (Fase 14, botones Fase 19) -- consultas directas sobre los
mismos datos que la web (un solo origen de verdad, nunca un calculo aparte).
Interaccion PRINCIPAL por botones (InlineKeyboard, ver conversacion) -- los
comandos de texto (/signals, /capital, etc.) se mantienen como atajo para
quien prefiera escribir, pero /start y cualquier mensaje libre muestran el
menu de botones. NO hay interpretacion de lenguaje natural libre: eso
requeriria un LLM con su propia API key (Anthropic/OpenAI), que no fue
provista -- nunca se simula "entender" una pregunta con un match de palabras
clave disfrazado de inteligencia.

Lenguaje: criollo/simple en TODO lo que ve el usuario (ver conversacion) --
"Motor Core"/"Motor Satelite" (nombres tecnicos internos, siguen existiendo
en el codigo de portfolio/signals.py y portfolio/short_term_engine.py) se
muestran acá como "Fondo de Mediano Plazo"/"Estrategia Corto Plazo", y los
precios SIEMPRE en USD CCL con su equivalencia en ARS al lado (ver
_fmt_dual), nunca una sola moneda sola.

Seguridad: soporte multi-usuario -- la autorizacion real vive en
telegram_allowed_users (ver portfolio/db.py), no en un chat_id unico. El
primer /start que llega a una instalacion nueva se autoriza solo como admin
(ver _reject_if_unauthorized); cualquier otro chat_id nunca antes agregado
recibe un rechazo con su propio chat_id, para que pueda pedirselo al
administrador. Permisos por rol: los botones/comandos de SOLO CONSULTA
(Oportunidades del dia, Como viene la cuenta, /patrimonio, etc.) los puede
usar cualquier usuario autorizado (admin o viewer); los de Ajustes de Riesgo
(reparto, limite de perdida, meta de ganancia) estan reservados a rol
'admin' -- un viewer que los toca recibe un aviso claro, nunca un error
generico ni un cambio silencioso."""
import io
import json
import logging
import os
import threading
from datetime import date, datetime, time as dt_time

import requests
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputFile, ReplyKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from model.client import ChronosServiceClient
from portfolio import accuracy, db, fetchers, fixed_income, loans as loans_module, pdf_report, risk, routes
from portfolio import config as capital_config
from portfolio import live_signals, monitor, paper_trading, proposals, regime
from portfolio.formatting import fmt_es
from portfolio.tz import ART_TZ, now_ar, today_ar

logger = logging.getLogger(__name__)

_application = None
# Mismo patron que portfolio/forecasting.py::_predictor_client y
# webui/app.py::_dual_engine_client -- un unico cliente reusado, nunca abre
# conexion hasta el primer /signals (asi importar este modulo nunca falla
# aunque el microservicio todavia no este arriba).
_dual_engine_client = ChronosServiceClient()

# Nombres en criollo de los 2 motores (ver conversacion) -- el nombre tecnico
# interno (Core/Satelite) sigue siendo el real en el codigo de
# portfolio/signals.py y portfolio/short_term_engine.py, esto es SOLO la
# etiqueta que ve el usuario del bot.
LABEL_CORE = "🛡️ Fondo de Mediano Plazo"
LABEL_SATELLITE = "⚡ Estrategia Corto Plazo (Rápida 1-3 días)"

TACTIC_LABELS_CRIOLLO = {
    "mean_reversion": "rebote",
    "volume_breakout": "arranque con volumen",
    "atr_breakout": "arranque con volatilidad",
}


def _tactic_label(key):
    return TACTIC_LABELS_CRIOLLO.get(key, key)


def _fmt_dual(usd, ars):
    """'USD 5.13 (≈$8.290,00)' -- equivalencia de precio en USD CCL (moneda
    de analisis, ver conversacion) y ARS (moneda real del broker), siempre
    juntas, nunca una sola."""
    return f"USD {fmt_es(usd, 2)} (≈${fmt_es(ars, 2)})"


def _format_dual_engine_signals(result):
    """Arma el reporte en 2 bloques (Fondo/Estrategia, ver conversacion) --
    reusado por el boton "¿Qué compramos hoy?", por /signals y por el
    resumen diario proactivo, para que los tres muestren exactamente el
    mismo criterio de señal del dia. Precios en USD CCL (moneda de analisis)
    con equivalencia en ARS entre parentesis (moneda real en la que se opera
    en BYMA)."""
    lines = ["📊 *Oportunidades del día*", "", f"{LABEL_CORE}:"]
    if not result["spy_above_ema50"]:
        lines.append("El mercado global no acompaña hoy -- la plata se queda quieta en Caución (gana la tasa, no arriesga).")
    elif not result["core_signals"]:
        lines.append("Hoy no apareció nada que valga la pena -- la plata se queda en Caución.")
    else:
        event_risk_notes = result.get("event_risk_notes") or {}
        for s in result["core_signals"]:
            monto = f" · poné ${fmt_es(s['suggested_amount_ars'], 0)}" if s.get("suggested_amount_ars") else ""
            nota_evento = f"\n  {event_risk_notes[s['symbol']]}" if s["symbol"] in event_risk_notes else ""
            lines.append(
                f"• *{s['symbol']}*: entrá a {_fmt_dual(s['entry_price_usd'], s['entry_price_ars'])}{monto}\n"
                f"  Ganancia en {_fmt_dual(s['take_profit_price_usd'], s['take_profit_price_ars'])} (+{fmt_es(s['take_profit_pct'], 1)}%) · "
                f"cortá la pérdida en {_fmt_dual(s['initial_stop_price_usd'], s['initial_stop_price_ars'])} (-{fmt_es(s['initial_stop_pct'], 1)}%) · "
                f"aguantá hasta {s['max_hold_days']} ruedas{nota_evento}"
            )

    lines.append("")
    lines.append(f"{LABEL_SATELLITE}:")
    if regime.satellite_blocked(result.get("market_regime")):
        lines.append("Mercado en alta volatilidad (VIX disparado) -- hoy no se abren posiciones nuevas, solo se gestionan las que ya estaban abiertas.")
    elif not result["satellite_signals"]:
        lines.append("Ninguna oportunidad rápida hoy.")
    else:
        event_risk_notes = result.get("event_risk_notes") or {}
        for s in result["satellite_signals"]:
            monto = f" · poné ${fmt_es(s['suggested_amount_ars'], 0)}" if s.get("suggested_amount_ars") else ""
            tacticas = ", ".join(_tactic_label(t) for t in (s.get("tactics") or []))
            nota_evento = f"\n  {event_risk_notes[s['symbol']]}" if s["symbol"] in event_risk_notes else ""
            lines.append(
                f"• *{s['symbol']}* ({tacticas}): entrá a {_fmt_dual(s['entry_price_usd'], s['entry_price_ars'])}{monto}\n"
                f"  Ganancia en {_fmt_dual(s['take_profit_price_usd'], s['take_profit_price_ars'])} (+{fmt_es(s['take_profit_pct'], 1)}%) · "
                f"Stop en {_fmt_dual(s['initial_stop_price_usd'], s['initial_stop_price_ars'])} (-{fmt_es(s['initial_stop_pct'], 1)}%) · "
                f"máx {s['max_hold_days']} días{nota_evento}"
            )

    arbitrage_signals = result.get("arbitrage_signals") or []
    if arbitrage_signals:
        lines.append("")
        lines.append("🔀 *Arbitraje de Canje*:")
        for s in arbitrage_signals:
            lines.append(
                f"• {s['pair'][0]}/{s['pair'][1]}: z-score {fmt_es(s['zscore'], 2)}, brecha {fmt_es(s['brecha_pct'], 2)}% -- "
                f"dólar más barato vía {s['cheap_leg']}, más caro vía {s['expensive_leg']}.\n  {s['note']}"
            )

    synthetic_dollar_signals = result.get("synthetic_dollar_signals") or []
    if synthetic_dollar_signals:
        lines.append("")
        lines.append("💵 *Dólar Sintético (ROFEX)*:")
        for s in synthetic_dollar_signals:
            lines.append(
                f"• {s['contract']} (vto. {s['maturity_date']}): tasa implícita {fmt_es(s['implied_tna_pct'], 2)}% TNA "
                f"vs. Caución+margen {fmt_es(s['caucion_tna_pct'] + s['margin_pct'], 2)}% TNA -- "
                f"spread +{fmt_es(s['spread_pct'], 2)}%.\n  {s['note']}"
            )

    fixed_income_signals = result.get("fixed_income_signals") or []
    if fixed_income_signals:
        lines.append("")
        lines.append("🔁 *Renta Fija -- Carry Trade*:")
        for s in fixed_income_signals:
            lines.append(
                f"• {s['bucket_label']}: rotá hacia {s['better']} "
                f"({s['lecap_symbol']} TEM {fmt_es(s['lecap_tem_pct'], 2)}% vs. {s['cer_symbol']} TEM {fmt_es(s['cer_tem_nominal_pct'], 2)}%) -- "
                f"spread {fmt_es(s['spread_pct'], 2)}%.\n  {s['note']}"
            )

    return "\n".join(lines)


def _fetch_signals_text():
    """Unico lugar que arma el texto de 'Oportunidades del día' -- ver
    _format_dual_engine_signals. Sincrono (compute_today_signals ya lo es),
    nunca 2 implementaciones distintas entre el boton y /signals."""
    conn = db.get_connection()
    try:
        account_value = live_signals.total_portfolio_value_ars(conn)
        result = live_signals.compute_today_signals(conn, _dual_engine_client, account_value=account_value)
        return _format_dual_engine_signals(result)
    finally:
        conn.close()


def _fetch_capital_text():
    """Unico lugar que arma el texto de 'Como viene la cuenta' -- reusado
    por el boton y por /capital."""
    conn = db.get_connection()
    try:
        allocation = capital_config.get_capital_allocation(conn)
        account_value = live_signals.total_portfolio_value_ars(conn)
        core_ars = account_value * allocation["core_pct"] / 100
        satellite_ars = account_value * allocation["satellite_pct"] / 100
        lines = [
            "💰 *¿Cómo viene la cuenta?*",
            "",
            f"{LABEL_CORE}: {fmt_es(allocation['core_pct'], 0)}% -> ${fmt_es(core_ars, 2)}",
            f"{LABEL_SATELLITE}: {fmt_es(allocation['satellite_pct'], 0)}% -> ${fmt_es(satellite_ars, 2)}",
            "",
            f"Total de la cuenta: ${fmt_es(account_value, 2)}",
        ]
        return "\n".join(lines)
    finally:
        conn.close()


def _help_text():
    return (
        "❓ *Ayuda*\n\n"
        "Tocá los botones del menú, no hace falta escribir nada:\n"
        "📊 *¿Qué compramos hoy?* -- te digo si conviene entrar a algo ahora.\n"
        "💰 *¿Cómo viene la cuenta?* -- cuánta plata tenés en cada estrategia.\n"
        "⚙️ *Ajustes de Riesgo* -- (solo el administrador) cambiar cuánto arriesgar.\n\n"
        "Si preferís escribir, también funcionan los comandos de siempre: "
        "/patrimonio /ganancia /prestamos /vencimientos /riesgo /senales /capital /signals "
        "/set_core /set_tp /set_sl /set_profile.\n\n"
        "Todavía no entiendo preguntas escritas libres -- usá los botones o los comandos de arriba."
    )


# --- Teclados (ver conversacion) --------------------------------------------

MAIN_MENU_TEXT = "¿Qué querés hacer?"

RISK_MENU_TEXT = (
    "⚙️ *Ajustes de Riesgo*\n\n"
    "💰 *Reparto*: cuánto de la cuenta va a cada estrategia.\n"
    "🛑 *Límite de Pérdida*: lo máximo que se puede perder en una operación de la Estrategia Corto Plazo.\n"
    "🎯 *Meta de Ganancia*: con cuánto la Estrategia Corto Plazo vende y toma la ganancia.\n\n"
    "Tocá un botón para cambiar -- se aplica al toque, sin escribir nada."
)


# Botonera persistente (ReplyKeyboardMarkup, ver conversacion) -- a
# diferencia del InlineKeyboardMarkup de abajo (pegado a UN mensaje puntual,
# se va con el scroll del chat), esta queda clavada abajo del chat todo el
# tiempo, para cualquier mensaje. El texto de cada boton es EXACTO al que
# Telegram manda de vuelta como mensaje de texto normal cuando se lo toca
# (no hay callback_data en un ReplyKeyboardMarkup) -- ver on_any_text, que
# compara contra estas mismas constantes.
REPLY_BTN_SIGNALS = "📊 Oportunidades del día"
REPLY_BTN_CAPITAL = "💰 Estado de la cuenta"
REPLY_BTN_AJUSTES = "⚙️ Ajustes de Riesgo"
REPLY_BTN_AYUDA = "❓ Ayuda"


def _get_main_reply_keyboard():
    """IMPORTANTE: un ReplyKeyboardMarkup solo se puede mandar con
    reply_text/send_message, NUNCA con edit_message_text (la API de Telegram
    no lo permite en una edicion) -- por eso los botones INLINE de abajo
    (menu:main, etc., que SI editan el mensaje en el lugar) nunca intentan
    reattachar esta botonera: una vez que /start la mando una vez, queda
    clavada sola gracias a is_persistent=True, no hace falta reenviarla en
    cada interaccion."""
    return ReplyKeyboardMarkup(
        [
            [REPLY_BTN_SIGNALS, REPLY_BTN_CAPITAL],
            [REPLY_BTN_AJUSTES, REPLY_BTN_AYUDA],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def _main_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 ¿Qué compramos hoy?", callback_data="menu:signals")],
        [InlineKeyboardButton("💰 ¿Cómo viene la cuenta?", callback_data="menu:capital")],
        [InlineKeyboardButton("⚙️ Ajustes de Riesgo", callback_data="menu:ajustes")],
        [InlineKeyboardButton("❓ Ayuda", callback_data="menu:ayuda")],
    ])


def _back_to_menu_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Menú principal", callback_data="menu:main")]])


def _risk_menu_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("80% Corto / 20% Fondo", callback_data="risk:core:20"),
            InlineKeyboardButton("50% / 50%", callback_data="risk:core:50"),
        ],
        [
            InlineKeyboardButton("Pérdida máx. -5%", callback_data="risk:sl:5"),
            InlineKeyboardButton("-9%", callback_data="risk:sl:9"),
            InlineKeyboardButton("-12%", callback_data="risk:sl:12"),
        ],
        [
            InlineKeyboardButton("Ganancia meta +4%", callback_data="risk:tp:4"),
            InlineKeyboardButton("+6%", callback_data="risk:tp:6"),
            InlineKeyboardButton("+8%", callback_data="risk:tp:8"),
        ],
        [InlineKeyboardButton("⬅️ Menú principal", callback_data="menu:main")],
    ])


async def _edit_or_send(query, text, **kwargs):
    """edit_message_text, ignorando el unico error inofensivo de Telegram
    ('Message is not modified' -- el admin toco un valor que ya estaba
    puesto, el texto de confirmacion puede coincidir con el anterior) --
    cualquier otro error se propaga, nunca se traga silenciosamente."""
    try:
        await query.edit_message_text(text, **kwargs)
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            raise


def _get_token():
    return os.environ.get("TELEGRAM_BOT_TOKEN")


def send_test_message(chat_id, text=None):
    """Manda un mensaje suelto via la API HTTP de Telegram directo (requests),
    NUNCA a traves del Application/event loop de polling (ver conversacion:
    ese loop corre en su propio hilo de fondo -- puentear una llamada sincrona
    de Flask hacia ahi requeriria asyncio.run_coroutine_threadsafe contra un
    loop concreto, bastante mas riesgo de romper el polling real que ya esta
    corriendo para ganar algo que la API HTTP de Telegram ya resuelve sola:
    mandar UN mensaje no necesita el Application, es un POST comun).
    Devuelve (ok: bool, motivo: str)."""
    token = _get_token()
    if not token:
        return False, "TELEGRAM_BOT_TOKEN no configurado"
    text = text or "🧪 Mensaje de prueba desde Kronos -- si ves esto, el bot está funcionando correctamente."
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=10,
        )
        data = resp.json()
        if not data.get("ok"):
            return False, data.get("description", "error desconocido de la API de Telegram")
        return True, "enviado"
    except Exception as e:
        return False, str(e)


def send_pdf_report_now(conn=None):
    """Envio MANUAL/sincronico del reporte PDF (ver conversacion, boton
    "Enviar Reporte de Prueba Ahora" de la Web UI) -- MISMO criterio que
    send_test_message de arriba: POST directo a la API HTTP de Telegram
    (sendDocument, multipart), NUNCA bridgeando hacia el event loop
    async del Application que ya esta corriendo su propio polling en otro
    hilo (ver docstring de send_test_message, mismo riesgo declarado ahi).

    Genera el PDF con la config ACTUAL de app_settings (portfolio/pdf_report.py)
    y lo manda a TODOS los usuarios autorizados (telegram_allowed_users) --
    mismo destinatario que el envio automatico programado, para que "probar"
    muestre exactamente lo que llegaria de verdad. Devuelve
    {"ok": bool, "sent_to": int, "errors": [...]}-- nunca levanta una
    excepcion hacia el caller (ruta Flask), el error queda en el dict."""
    token = _get_token()
    if not token:
        return {"ok": False, "sent_to": 0, "errors": ["TELEGRAM_BOT_TOKEN no configurado"]}

    own_conn = conn is None
    conn = conn or db.get_connection()
    try:
        allowed_users = db.list_telegram_allowed_users(conn)
        if not allowed_users:
            return {"ok": False, "sent_to": 0, "errors": ["Sin usuarios autorizados en telegram_allowed_users"]}
        try:
            pdf_bytes = pdf_report.generate_portfolio_pdf(conn)
        except Exception as e:
            return {"ok": False, "sent_to": 0, "errors": [f"Fallo la generacion del PDF: {e}"]}

        filename = f"kronos_reporte_{today_ar().isoformat()}.pdf"
        sent_to, errors = 0, []
        for user in allowed_users:
            try:
                resp = requests.post(
                    f"https://api.telegram.org/bot{token}/sendDocument",
                    data={"chat_id": user["chat_id"], "caption": "📄 Reporte de cartera -- prueba manual"},
                    files={"document": (filename, pdf_bytes, "application/pdf")},
                    timeout=30,
                )
                data = resp.json()
                if data.get("ok"):
                    sent_to += 1
                else:
                    errors.append(f"chat_id={user['chat_id']}: {data.get('description', 'error desconocido')}")
            except Exception as e:
                errors.append(f"chat_id={user['chat_id']}: {e}")
        return {"ok": sent_to > 0, "sent_to": sent_to, "errors": errors}
    finally:
        if own_conn:
            conn.close()


async def _reject_if_unauthorized(update: Update) -> bool:
    """True si el mensaje NO esta autorizado (ya se le respondio el
    rechazo). Soporte multi-usuario (ver conversacion): la fuente de
    autorizacion real es telegram_allowed_users, no
    app_settings.telegram_chat_id -- esa columna queda solo como semilla de
    migracion para instalaciones de ANTES de este cambio (si ya habia un
    admin unico autorizado, se migra a la tabla nueva la primera vez que
    alguien escribe despues de actualizar, nunca se vuelve a leer despues).
    Bootstrap de instalacion nueva: si la tabla esta vacia y no hay nada que
    migrar, el primer /start se autoriza como admin (mismo comportamiento de
    siempre, ahora persistido en la tabla nueva en vez de en app_settings)."""
    conn = db.get_connection()
    try:
        chat_id = str(update.effective_chat.id)
        if db.get_telegram_allowed_user_by_chat_id(conn, chat_id):
            return False

        if not db.list_telegram_allowed_users(conn):
            legacy_chat_id = db.get_app_settings(conn).get("telegram_chat_id")
            if legacy_chat_id:
                db.add_telegram_allowed_user(conn, chat_id=legacy_chat_id, label="Admin", role="admin")
                if legacy_chat_id == chat_id:
                    return False
            else:
                db.add_telegram_allowed_user(conn, chat_id=chat_id, label="Admin", role="admin")
                return False

        await update.message.reply_text(
            f"Acceso no autorizado. Tu Telegram Chat ID es {chat_id}. Solicita acceso al administrador."
        )
        return True
    finally:
        conn.close()


async def _reject_if_not_admin(update: Update) -> bool:
    """Permisos por rol (ver conversacion): True si el chat_id NO tiene rol
    'admin' en telegram_allowed_users (ya se le respondio el rechazo) --
    SIEMPRE se llama DESPUES de _reject_if_unauthorized (da por sentado que
    el chat_id ya esta autorizado; si no lo estuviera,
    get_telegram_allowed_user_by_chat_id da None y esto igual rechaza, nunca
    deja pasar a alguien sin fila en la tabla). Uso: comandos que MUTAN
    configuracion (/set_core, /set_tp, /set_sl) -- las consultas (/signals,
    /capital, /help, /start, etc.) siguen abiertas a cualquier usuario
    autorizado, sea admin o viewer."""
    conn = db.get_connection()
    try:
        chat_id = str(update.effective_chat.id)
        user = db.get_telegram_allowed_user_by_chat_id(conn, chat_id)
        if user is None or user["role"] != "admin":
            await update.message.reply_text(
                "⚠️ Acción no permitida. Los cambios de configuración están reservados para administradores."
            )
            return True
        return False
    finally:
        conn.close()


# --- Comandos de texto (atajo -- la via principal son los botones) ---------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await _reject_if_unauthorized(update):
        return
    await update.message.reply_text(MAIN_MENU_TEXT, reply_markup=_get_main_reply_keyboard())


async def cmd_ajustes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Equivalente de texto/boton-reply al 'menu:ajustes' inline -- mismo
    mensaje de rechazo para no-admin que esa rama (ver on_callback_query)."""
    if await _reject_if_unauthorized(update):
        return
    conn = db.get_connection()
    try:
        chat_id = str(update.effective_chat.id)
        user = db.get_telegram_allowed_user_by_chat_id(conn, chat_id)
    finally:
        conn.close()
    if user is None or user["role"] != "admin":
        await update.message.reply_text("⚠️ Los cambios de riesgo solo los puede tocar el administrador.")
        return
    await update.message.reply_text(RISK_MENU_TEXT, parse_mode="Markdown", reply_markup=_risk_menu_keyboard())


async def on_any_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Boton de la botonera persistente (ReplyKeyboardMarkup, ver
    _get_main_reply_keyboard) tocado o tipeado a mano -- se despacha a la
    misma logica que su comando/boton inline equivalente. Cualquier otro
    texto libre (no reconocido) muestra el menu principal -- no hay
    interpretacion de lenguaje natural libre (ver docstring del modulo)."""
    if await _reject_if_unauthorized(update):
        return
    text = (update.message.text or "").strip()

    if text == REPLY_BTN_SIGNALS:
        await cmd_signals(update, context)
        return
    if text == REPLY_BTN_CAPITAL:
        await cmd_capital(update, context)
        return
    if text == REPLY_BTN_AYUDA:
        await cmd_help(update, context)
        return
    if text == REPLY_BTN_AJUSTES:
        await cmd_ajustes(update, context)
        return

    await update.message.reply_text(MAIN_MENU_TEXT, reply_markup=_get_main_reply_keyboard())


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await _reject_if_unauthorized(update):
        return
    await update.message.reply_text(_help_text(), parse_mode="Markdown", reply_markup=_main_menu_keyboard())


async def cmd_capital(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await _reject_if_unauthorized(update):
        return
    await update.message.reply_text(_fetch_capital_text(), parse_mode="Markdown", reply_markup=_back_to_menu_keyboard())


async def cmd_set_core(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await _reject_if_unauthorized(update):
        return
    if await _reject_if_not_admin(update):
        return
    if not context.args:
        await update.message.reply_text("Uso: /set_core <porcentaje> (ej. /set_core 80)")
        return
    conn = db.get_connection()
    try:
        try:
            allocation = capital_config.set_core_allocation_pct(conn, context.args[0])
        except ValueError as e:
            await update.message.reply_text(str(e))
            return
    finally:
        conn.close()
    await update.message.reply_text(
        f"✅ Listo. Ahora el {fmt_es(allocation['satellite_pct'], 0)}% de la cuenta va a la Estrategia Corto Plazo "
        f"y el {fmt_es(allocation['core_pct'], 0)}% al Fondo de Mediano Plazo."
    )


async def cmd_set_tp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await _reject_if_unauthorized(update):
        return
    if await _reject_if_not_admin(update):
        return
    if not context.args:
        await update.message.reply_text("Uso: /set_tp <porcentaje> (ej. /set_tp 5.0)")
        return
    conn = db.get_connection()
    try:
        try:
            params = capital_config.set_satellite_exit_params(conn, take_profit_pct=context.args[0])
        except ValueError as e:
            await update.message.reply_text(str(e))
            return
    finally:
        conn.close()
    await update.message.reply_text(
        f"✅ Listo. La Estrategia Corto Plazo va a vender y tomar ganancia apenas llegue a +{fmt_es(params['take_profit_pct'], 1)}%."
    )


async def cmd_set_sl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await _reject_if_unauthorized(update):
        return
    if await _reject_if_not_admin(update):
        return
    if not context.args:
        await update.message.reply_text("Uso: /set_sl <porcentaje> (ej. /set_sl 9.0)")
        return
    conn = db.get_connection()
    try:
        try:
            params = capital_config.set_satellite_exit_params(conn, stop_loss_pct=context.args[0])
        except ValueError as e:
            await update.message.reply_text(str(e))
            return
    finally:
        conn.close()
    await update.message.reply_text(
        f"✅ Listo. La Estrategia Corto Plazo nunca va a perder más de -{fmt_es(params['stop_loss_pct'], 1)}% en una operación."
    )


async def cmd_set_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/set_profile <conservador|moderado|agresivo> -- aplica el preset de
    Core/Satelite + Take Profit/Stop Loss de ese perfil (ver
    portfolio/config.py::activate_risk_profile), MISMO criterio de
    autorizacion que /set_core, /set_tp, /set_sl (solo admin)."""
    if await _reject_if_unauthorized(update):
        return
    if await _reject_if_not_admin(update):
        return
    if not context.args:
        opciones = "/".join(capital_config.RISK_PROFILE_ORDER)
        await update.message.reply_text(f"Uso: /set_profile <perfil> (ej. /set_profile moderado -- opciones: {opciones})")
        return
    conn = db.get_connection()
    try:
        try:
            result = capital_config.activate_risk_profile(conn, context.args[0].lower())
        except ValueError as e:
            await update.message.reply_text(str(e))
            return
    finally:
        conn.close()
    label = capital_config.RISK_PROFILES[result["active_profile"]]["label"]
    await update.message.reply_text(
        f"✅ Perfil *{label}* activado. Fondo de Mediano Plazo {fmt_es(result['core_pct'], 0)}% / "
        f"Estrategia Corto Plazo {fmt_es(result['satellite_pct'], 0)}% -- "
        f"Take Profit +{fmt_es(result['take_profit_pct'], 1)}% / Stop Loss -{fmt_es(result['stop_loss_pct'], 1)}%.",
        parse_mode="Markdown",
    )


async def cmd_signals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await _reject_if_unauthorized(update):
        return
    await update.message.reply_text("Mirando qué conviene comprar hoy... 👀")
    try:
        text = _fetch_signals_text()
    except Exception as e:
        await update.message.reply_text(f"No pude mirar las oportunidades de hoy: {e}")
        return
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=_back_to_menu_keyboard())


async def cmd_patrimonio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await _reject_if_unauthorized(update):
        return
    conn = db.get_connection()
    try:
        holdings = db.get_holdings_with_quotes(conn)
        cauciones = [c for c in db.get_cauciones_resumen(conn) if not c["vencido"]]
        exposure = routes._compute_currency_exposure(conn, holdings, cauciones)
        try:
            rate = fetchers.fetch_dolarapi_casa("bolsa")["venta"]
        except Exception:
            rate = None
        lines = ["*Patrimonio*"]
        if rate:
            usd_total = (exposure["ars_fija"]["valor"] + exposure["ars_cer"]["valor"]) / rate + exposure["usd"]["valor"]
            lines.append(f"Total: USD {fmt_es(usd_total, 2)} (MEP ${fmt_es(rate, 2)})")
            lines.append(f"≈ ${fmt_es(usd_total * rate, 2)} ARS")
        lines.append(f"ARS tasa fija: ${fmt_es(exposure['ars_fija']['valor'], 2)}")
        lines.append(f"ARS CER: ${fmt_es(exposure['ars_cer']['valor'], 2)}")
        lines.append(f"USD: USD {fmt_es(exposure['usd']['valor'], 2)}")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    finally:
        conn.close()


async def cmd_ganancia(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await _reject_if_unauthorized(update):
        return
    conn = db.get_connection()
    try:
        g = routes._compute_ganancia(conn)
        lines = ["*¿Cuánto gané?*"]
        for moneda in ("ARS", "USD"):
            d = g[moneda]
            xirr_txt = f"{fmt_es(d['xirr']['xirr_pct'], 2, signed=True)}%" if d["xirr"]["available"] else "sin datos"
            lines.append(
                f"\n{moneda}: patrimonio {fmt_es(d['patrimonio_actual'], 2)}, aportes netos {fmt_es(d['aportes_netos'], 2)}\n"
                f"Ganancia: {fmt_es(d['ganancia_total'], 2, signed=True)} -- XIRR {xirr_txt}"
            )
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    finally:
        conn.close()


async def cmd_prestamos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await _reject_if_unauthorized(update):
        return
    conn = db.get_connection()
    try:
        prestamos = [l for l in db.list_loans(conn) if l["estado"] == "vigente"]
        if not prestamos:
            await update.message.reply_text("No tenés préstamos vigentes.")
            return
        lines = ["*Préstamos vigentes*"]
        for loan in prestamos:
            status = loans_module.compute_loan_status(conn, loan)
            verbo = "te debe" if loan["direccion"] == "prestado" else "le debés"
            atraso = f" -- ATRASADO {status['vencido_hace_dias']}d" if status["vencido_hace_dias"] else ""
            lines.append(f"{loan['deudor']} {verbo} {fmt_es(status['capital_pendiente'], 2)} {loan['moneda']}{atraso}")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    finally:
        conn.close()


async def cmd_vencimientos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await _reject_if_unauthorized(update):
        return
    conn = db.get_connection()
    try:
        lines = ["*Próximos 7 días*"]
        found = False

        instrumentos = [i for i in db.list_instruments(conn) if i["type"] in ("bono", "on")]
        for e in fixed_income.upcoming_fixed_income_events(instrumentos, horizon_days=7):
            found = True
            lines.append(f"{e['date']}: {'Vencimiento' if e['kind'] == 'vencimiento' else 'Cupón'} de {e['symbol']}")

        for c in db.get_cauciones_resumen(conn):
            if c["vencido"]:
                continue
            dias = (date.fromisoformat(c["caucion"]["fecha_vencimiento"]) - today_ar()).days
            if 0 <= dias <= 7:
                found = True
                lines.append(f"{c['caucion']['fecha_vencimiento']}: Caución por {fmt_es(c['caucion']['monto_contado'], 2)}")

        for p in db.get_plazos_fijos_resumen(conn):
            if p["vencido"]:
                continue
            dias = (date.fromisoformat(p["plazo_fijo"]["fecha_vencimiento"]) - today_ar()).days
            if 0 <= dias <= 7:
                found = True
                lines.append(f"{p['plazo_fijo']['fecha_vencimiento']}: Plazo fijo en {p['plazo_fijo']['banco']}")

        for loan in db.list_loans(conn):
            if loan["estado"] != "vigente":
                continue
            status = loans_module.compute_loan_status(conn, loan)
            if status["vencido_hace_dias"]:
                found = True
                lines.append(f"ATRASADO hace {status['vencido_hace_dias']}d: préstamo {loan['deudor']}")
            elif status["proximo_vencimiento"]:
                dias = (date.fromisoformat(status["proximo_vencimiento"]) - today_ar()).days
                if 0 <= dias <= 7:
                    found = True
                    lines.append(f"{status['proximo_vencimiento']}: cuota de préstamo {loan['deudor']}")

        if not found:
            lines.append("Nada en los próximos 7 días.")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    finally:
        conn.close()


async def cmd_riesgo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await _reject_if_unauthorized(update):
        return
    conn = db.get_connection()
    try:
        config = db.get_signal_config(conn)
        holdings = [h for h in db.get_holdings_with_quotes(conn) if h["valor_actual"] is not None]
        cauciones = [c for c in db.get_cauciones_resumen(conn) if not c["vencido"]]
        metrics = risk.compute_portfolio_risk_metrics(conn, holdings, cauciones, config)
        if not metrics.get("available"):
            await update.message.reply_text(metrics.get("reason") or "Riesgo no disponible.")
            return
        lines = ["*Riesgo de cartera*",
                 f"Volatilidad anualizada: {fmt_es(metrics['annualized_volatility_pct'])}%",
                 f"Caída máxima: {fmt_es(metrics['max_drawdown_pct'])}%"]
        if metrics["top5_concentration_pct"] is not None:
            lines.append(f"Top 5 concentración: {fmt_es(metrics['top5_concentration_pct'])}%")
        if metrics.get("mora_ars") or metrics.get("mora_usd"):
            lines.append(f"Mora: ${fmt_es(metrics.get('mora_ars') or 0)} / USD {fmt_es(metrics.get('mora_usd') or 0)}")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    finally:
        conn.close()


async def cmd_senales(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await _reject_if_unauthorized(update):
        return
    conn = db.get_connection()
    try:
        config = db.get_signal_config(conn)
        holdings = [h for h in db.get_holdings_with_quotes(conn) if h["valor_actual"]]
        lines = ["*Señales validadas*"]
        found = False
        for h in holdings:
            instrument_id = h["instrument"]["id"]
            row = db.get_latest_forecast(conn, instrument_id=instrument_id)
            if not row or row["status"] != "done":
                continue
            forecasts = db.list_forecasts_for_instrument(conn, instrument_id)
            price_history = db.get_price_history(conn, instrument_id)
            acc = accuracy.compute_accuracy(forecasts, price_history, conn=conn)
            validation = accuracy.compute_signal_validation(acc, config)
            if validation["validated"]:
                result = json.loads(row["result_json"])
                found = True
                lines.append(f"{h['instrument']['symbol']}: {result.get('signal', '—')} ({validation['reason']})")
        if not found:
            lines.append("Ninguna tenencia tiene señal validada hoy.")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    finally:
        conn.close()


# --- Botones (CallbackQuery) -------------------------------------------------

async def _handle_risk_callback(query, data):
    """Procesa un toque de boton del submenu de Ajustes de Riesgo -- ya se
    verifico admin antes de llamar a esto (ver on_callback_query)."""
    _, kind, raw_value = data.split(":")
    conn = db.get_connection()
    try:
        if kind == "core":
            allocation = capital_config.set_core_allocation_pct(conn, float(raw_value))
            text = (
                f"✅ Listo. Ahora el {fmt_es(allocation['satellite_pct'], 0)}% de la cuenta va a la "
                f"Estrategia Corto Plazo y el {fmt_es(allocation['core_pct'], 0)}% al Fondo de Mediano Plazo."
            )
        elif kind == "sl":
            params = capital_config.set_satellite_exit_params(conn, stop_loss_pct=float(raw_value))
            text = (
                f"✅ Listo. La Estrategia Corto Plazo nunca va a perder más de "
                f"-{fmt_es(params['stop_loss_pct'], 0)}% en una sola operación."
            )
        elif kind == "tp":
            params = capital_config.set_satellite_exit_params(conn, take_profit_pct=float(raw_value))
            text = (
                f"✅ Listo. La Estrategia Corto Plazo va a vender y tomar ganancia apenas llegue a "
                f"+{fmt_es(params['take_profit_pct'], 0)}%."
            )
        else:
            text = "No reconocí ese ajuste."
    except ValueError as e:
        text = f"⚠️ {e}"
    finally:
        conn.close()
    await _edit_or_send(query, f"{text}\n\n{RISK_MENU_TEXT}", parse_mode="Markdown", reply_markup=_risk_menu_keyboard())


async def on_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Despacha todos los botones (ver conversacion) -- un UNICO handler,
    el callback_data decide que hacer (prefijo 'menu:' = navegacion,
    'risk:<campo>:<valor>' = un ajuste de riesgo). answer() SIEMPRE primero:
    sin eso, Telegram deja el boton del cliente con el spinner de "cargando"
    hasta que expire el timeout, aunque la respuesta real tarde unos
    segundos (ej. el boton de Oportunidades del día, que consulta
    Chronos-Bolt)."""
    query = update.callback_query
    await query.answer()
    chat_id = str(update.effective_chat.id)

    conn = db.get_connection()
    try:
        user = db.get_telegram_allowed_user_by_chat_id(conn, chat_id)
    finally:
        conn.close()
    if user is None:
        await _edit_or_send(
            query, f"Acceso no autorizado. Tu Telegram Chat ID es {chat_id}. Solicita acceso al administrador."
        )
        return

    data = query.data

    if data == "menu:main":
        await _edit_or_send(query, MAIN_MENU_TEXT, reply_markup=_main_menu_keyboard())
        return

    if data == "menu:ayuda":
        await _edit_or_send(query, _help_text(), parse_mode="Markdown", reply_markup=_main_menu_keyboard())
        return

    if data == "menu:capital":
        await _edit_or_send(query, _fetch_capital_text(), parse_mode="Markdown", reply_markup=_back_to_menu_keyboard())
        return

    if data == "menu:signals":
        await _edit_or_send(query, "Mirando qué conviene comprar hoy... 👀")
        try:
            text = _fetch_signals_text()
        except Exception as e:
            await context.bot.send_message(chat_id=chat_id, text=f"No pude mirar las oportunidades de hoy: {e}")
            return
        await context.bot.send_message(
            chat_id=chat_id, text=text, parse_mode="Markdown", reply_markup=_back_to_menu_keyboard()
        )
        return

    if data == "menu:ajustes":
        if user["role"] != "admin":
            await _edit_or_send(
                query, "⚠️ Los cambios de riesgo solo los puede tocar el administrador.",
                reply_markup=_back_to_menu_keyboard(),
            )
            return
        await _edit_or_send(query, RISK_MENU_TEXT, parse_mode="Markdown", reply_markup=_risk_menu_keyboard())
        return

    if data.startswith("risk:"):
        if user["role"] != "admin":
            await _edit_or_send(
                query, "⚠️ Los cambios de riesgo solo los puede tocar el administrador.",
                reply_markup=_back_to_menu_keyboard(),
            )
            return
        await _handle_risk_callback(query, data)
        return

    await _edit_or_send(query, "No entendí ese botón -- volvé al menú.", reply_markup=_back_to_menu_keyboard())


async def _daily_summary_job(context: ContextTypes.DEFAULT_TYPE):
    """Resumen diario proactivo (§14 del prompt) -- reporta sobre el estado YA
    calculado (no dispara pronosticos nuevos de Kronos, eso corre por su
    cuenta via forecast_worker; mezclar los dos séria una ejecucion pesada e
    impredecible disparada por un scheduler que nadie esta mirando)."""
    conn = db.get_connection()
    try:
        # Multi-usuario (ver conversacion): se manda a TODOS los autorizados
        # en telegram_allowed_users, no a un chat_id unico -- el contenido
        # es el mismo para todos (no hay vistas diferenciadas por rol
        # todavia), solo cambia a quienes se les manda.
        allowed_users = db.list_telegram_allowed_users(conn)
        if not allowed_users:
            return
        g = routes._compute_ganancia(conn)
        lines = [f"*Resumen diario -- {today_ar().isoformat()}*",
                 f"Patrimonio ARS: ${fmt_es(g['ARS']['patrimonio_actual'], 2)} (ganancia {fmt_es(g['ARS']['ganancia_total'], 2, signed=True)})",
                 f"Patrimonio USD: USD {fmt_es(g['USD']['patrimonio_actual'], 2)} (ganancia {fmt_es(g['USD']['ganancia_total'], 2, signed=True)})"]

        atrasados = []
        for loan in db.list_loans(conn):
            if loan["estado"] != "vigente":
                continue
            status = loans_module.compute_loan_status(conn, loan)
            if status["vencido_hace_dias"]:
                atrasados.append(f"{loan['deudor']} ({status['vencido_hace_dias']}d)")
        if atrasados:
            lines.append(f"\n⚠️ Préstamos atrasados: {', '.join(atrasados)}")

        instrumentos = [i for i in db.list_instruments(conn) if i["type"] in ("bono", "on")]
        proximos = fixed_income.upcoming_fixed_income_events(instrumentos, horizon_days=7)
        if proximos:
            lines.append(f"\nVencimientos en 7 días: {', '.join(e['symbol'] for e in proximos)}")

        # Bloque Core/Satelite en su propio try -- si Chronos-Bolt esta caido
        # u otra cosa falla aca, el resto del resumen diario (patrimonio,
        # prestamos, vencimientos) se manda igual, nunca se pierde todo por
        # esto (ver conversacion).
        try:
            lines.append("\n" + _fetch_signals_text())
        except Exception:
            logger.exception("Fallo el bloque de oportunidades del resumen diario de Telegram")
            lines.append("\n⚠️ No se pudieron evaluar las oportunidades de hoy -- probá el botón 📊 más tarde.")

        text = "\n".join(lines)
        for user in allowed_users:
            try:
                await context.bot.send_message(
                    chat_id=user["chat_id"], text=text, parse_mode="Markdown", reply_markup=_main_menu_keyboard()
                )
            except Exception:
                # Un chat_id roto/bloqueado (ej. el usuario borro el chat con
                # el bot) no debe cortar el envio a los demas autorizados.
                logger.exception("Fallo el resumen diario de Telegram para chat_id=%s", user["chat_id"])
    except Exception:
        logger.exception("Fallo el resumen diario de Telegram")
    finally:
        conn.close()


# --- Paper Trading: scanner intradia + reporte de cierre de rueda ----------
#
# Dinero ficticio (ver conversacion, portfolio/paper_trading.py +
# portfolio/monitor.py) -- este modulo solo formatea texto y manda mensajes,
# toda la logica de apertura/cierre/dimensionamiento vive en esos dos
# modulos (sin dependencia de Telegram, reusable desde la API web).

# Cada ciclo recorre TODO el universo via data912 (misma fuente que
# /signals) para chequear salidas y evaluar señales nuevas -- un intervalo
# mas corto que 15 min multiplicaria las llamadas de red sin aportar mucha
# mas resolucion intradia real (declarado, ajustable si hiciera falta).
PAPER_TRADING_SCAN_INTERVAL_SECONDS = 15 * 60

EXIT_REASON_LABELS_CRIOLLO = {
    "take_profit": "🎯 Meta de Ganancia alcanzada",
    "stop_loss": "🛑 Límite de Pérdida activado",
    "max_hold": "⏱️ Cierre por tiempo máximo",
    "manual": "Cierre manual",
}


def _format_new_entry(entry):
    engine_label = LABEL_CORE if entry["engine"] == "core" else LABEL_SATELLITE
    tactica = f" ({_tactic_label(entry['tactic'])})" if entry.get("tactic") else ""
    return (
        f"🎯 *Nueva oportunidad táctica (Paper Trading)*\n"
        f"*{entry['symbol']}*{tactica} -- {engine_label}\n"
        f"Entrada simulada: {_fmt_dual(entry['entry_price_usd'], entry['entry_price_ars'])} · "
        f"cantidad ficticia: {fmt_es(entry['quantity'], 4)}"
    )


def _format_closed_trade(trade):
    label = EXIT_REASON_LABELS_CRIOLLO.get(trade["exit_reason"], trade["exit_reason"])
    engine_label = LABEL_CORE if trade["engine"] == "core" else LABEL_SATELLITE
    return (
        f"{label} (Paper Trading)\n"
        f"*{trade['symbol']}* -- {engine_label}: resultado {fmt_es(trade['pnl_usd'], 2, signed=True)} USD "
        f"({fmt_es(trade['pnl_pct'], 1, signed=True)}%)"
    )


async def _paper_trading_scan_job(context: ContextTypes.DEFAULT_TYPE):
    """Un ciclo del scanner intradia (ver portfolio/monitor.py::scan_once) --
    no hace nada fuera del horario de rueda declarado (11:00-17:00 ART, ni
    fines de semana). Notifica proactivamente cada entrada nueva y cada
    posicion virtual cerrada a TODOS los usuarios autorizados."""
    if not monitor.is_market_hours():
        return
    conn = db.get_connection()
    try:
        allowed_users = db.list_telegram_allowed_users(conn)
        if not allowed_users:
            return
        try:
            result = monitor.scan_once(conn, _dual_engine_client)
        except Exception:
            logger.exception("Fallo el scan intradia de Paper Trading")
            return
        texts = [_format_new_entry(e) for e in result["new_entries"]]
        texts += [_format_closed_trade(t) for t in result["closed_trades"]]
        for text in texts:
            for user in allowed_users:
                try:
                    await context.bot.send_message(chat_id=user["chat_id"], text=text, parse_mode="Markdown")
                except Exception:
                    logger.exception("Fallo una alerta de Paper Trading para chat_id=%s", user["chat_id"])
    finally:
        conn.close()


def _format_closing_report(report):
    lines = [
        f"*📊 Reporte de Cierre de Rueda -- {report['date']}*",
        "",
        f"Patrimonio virtual (USD CCL): USD {fmt_es(report['equity_usd'], 2)}",
    ]
    if report["resultado_dia_usd"] is None:
        lines.append("Resultado del día: sin dato de referencia todavía (primer reporte).")
    else:
        lines.append(
            f"Resultado del día: {fmt_es(report['resultado_dia_usd'], 2, signed=True)} USD "
            f"({fmt_es(report['resultado_dia_pct'], 2, signed=True)}%)"
        )
    lines.append("")
    if not report["trades_closed_today"]:
        lines.append("Trades cerrados hoy: ninguno.")
    else:
        lines.append(f"Trades cerrados hoy ({report['trades_closed_today_count']}):")
        for t in report["trades_closed_today"]:
            lines.append(f"  • {t['symbol']}: {fmt_es(t['pnl_pct'], 1, signed=True)}% ({EXIT_REASON_LABELS_CRIOLLO.get(t['exit_reason'], t['exit_reason'])})")
    lines.append("")
    if not report["open_positions"]:
        lines.append("Posiciones abiertas: ninguna.")
    else:
        lines.append(f"Posiciones abiertas ({report['open_positions_count']}):")
        for p in report["open_positions"]:
            engine_label = LABEL_CORE if p["engine"] == "core" else LABEL_SATELLITE
            lines.append(f"  • {p['symbol']} -- {engine_label}")
    return "\n".join(lines)


async def _paper_trading_closing_report_job(context: ContextTypes.DEFAULT_TYPE):
    """Reporte de Cierre de Rueda (ver conversacion), 17:05 ART -- se salta
    fines de semana (no hubo rueda que cerrar)."""
    if not monitor.is_trading_day():
        return
    conn = db.get_connection()
    try:
        allowed_users = db.list_telegram_allowed_users(conn)
        if not allowed_users:
            return
        try:
            report = monitor.build_closing_report(conn)
        except Exception:
            logger.exception("Fallo el reporte de cierre de Paper Trading")
            return
        text = _format_closing_report(report)
        for user in allowed_users:
            try:
                await context.bot.send_message(
                    chat_id=user["chat_id"], text=text, parse_mode="Markdown", reply_markup=_get_main_reply_keyboard()
                )
            except Exception:
                logger.exception("Fallo el envio del reporte de cierre a chat_id=%s", user["chat_id"])
    finally:
        conn.close()


# Reporte PDF programado (ver conversacion, "Programación horaria dinámica")
# -- chequeo PERIODICO en vez de un horario fijado una sola vez al arrancar
# el bot (ver _daily_summary_job/run_daily mas abajo, que SI fija la hora al
# arranque -- insuficiente para lo pedido explicito: "consultar
# dinámicamente el horario... en SQLite", un cambio desde la Web UI debe
# surtir efecto sin reiniciar el proceso). Con un chequeo cada
# PDF_REPORT_SCHEDULE_CHECK_INTERVAL_SECONDS, un cambio de horario tarda a
# lo sumo ese intervalo en reflejarse -- declarado, no instantaneo.
PDF_REPORT_SCHEDULE_CHECK_INTERVAL_SECONDS = 5 * 60


def _parse_hhmm(value, default_hh, default_mm):
    """Parsea "HH:MM" a dt_time -- degrada al default declarado ante
    cualquier dato corrupto/con otro formato, nunca rompe el job que llama
    esto por un setting mal guardado."""
    try:
        hh, mm = (int(p) for p in (value or "").split(":"))
        return dt_time(hh, mm)
    except (ValueError, AttributeError):
        return dt_time(default_hh, default_mm)


async def _pdf_report_schedule_check_job(context: ContextTypes.DEFAULT_TYPE):
    """Corre cada PDF_REPORT_SCHEDULE_CHECK_INTERVAL_SECONDS -- lee
    pdf_report_schedule/pdf_report_time/pdf_report_time_afternoon de SQLite
    EN CADA corrida (nunca cacheado desde el arranque del bot), decide si ya
    paso la hora de alguna franja habilitada ('morning'/'afternoon'/'both') y
    todavia no se mando hoy (pdf_report_last_sent_morning/afternoon, ver
    schema.sql) -- de ser asi, genera y manda el PDF, y marca esa franja
    como enviada hoy para no repetir en el proximo chequeo (unos minutos
    despues, todavia dentro de la misma franja horaria).

    pdf_report_time_afternoon (ver conversacion) reemplaza la constante fija
    que tenia esto antes -- la tarde ahora es tan configurable como la
    mañana, mismo campo de Ajustes, mismo default ("17:30") para que una
    instalacion ya migrada no vea cambiar el horario de un dia para el otro
    solo por este agregado. pdf_report_weekdays_only (default activado) evita
    mandar un "Resultado del Día" sabado/domingo sin rueda detras -- antes
    este job no tenia NINGUN gate de dia habil, a diferencia del scanner de
    Paper Trading (ver monitor.is_trading_day, reusada aca mismo)."""
    conn = db.get_connection()
    try:
        settings = db.get_app_settings(conn)
        if settings.get("pdf_report_weekdays_only", 1) and not monitor.is_trading_day():
            return
        schedule = settings.get("pdf_report_schedule") or "morning"
        today_str = today_ar().isoformat()
        now = now_ar().time()

        morning_time = _parse_hhmm(settings.get("pdf_report_time"), 9, 30)
        afternoon_time = _parse_hhmm(settings.get("pdf_report_time_afternoon"), 17, 30)

        slots = []
        if schedule in ("morning", "both"):
            slots.append(("morning", morning_time, settings.get("pdf_report_last_sent_morning")))
        if schedule in ("afternoon", "both"):
            slots.append(("afternoon", afternoon_time, settings.get("pdf_report_last_sent_afternoon")))

        due_slots = [(name, label) for name, slot_time, last_sent in slots
                     for label in [("mañana (pre-rueda)" if name == "morning" else "tarde")]
                     if last_sent != today_str and now >= slot_time]
        if not due_slots:
            return

        allowed_users = db.list_telegram_allowed_users(conn)
        if not allowed_users:
            return
        try:
            pdf_bytes = pdf_report.generate_portfolio_pdf(conn)
        except Exception:
            logger.exception("Fallo la generacion del reporte PDF programado")
            return
        filename = f"kronos_reporte_{today_str}.pdf"

        for slot_name, label in due_slots:
            for user in allowed_users:
                try:
                    await context.bot.send_document(
                        chat_id=user["chat_id"],
                        document=InputFile(io.BytesIO(pdf_bytes), filename=filename),
                        caption=f"📄 Reporte de cartera -- {label}",
                    )
                except Exception:
                    logger.exception("Fallo el envio del reporte PDF programado a chat_id=%s", user["chat_id"])
            db.update_app_settings(conn, **{f"pdf_report_last_sent_{slot_name}": today_str})
    except Exception:
        logger.exception("Fallo el chequeo de horario del reporte PDF")
    finally:
        conn.close()


# Sincronizacion autonoma de Propuestas (ver conversacion) -- antes
# proposals.sync_proposals() solo corria adentro del handler de GET
# /api/portfolio/proposals (portfolio/routes.py), es decir, solo cuando
# alguien entraba a esa pestaña. Mismo intervalo que el scanner de Paper
# Trading (15 min) y mismo gate de horario de rueda (monitor.is_market_hours,
# reusada aca) -- no tiene sentido recalcular propuestas fuera de rueda, el
# universo de precios no cambia.
PROPOSALS_SYNC_INTERVAL_SECONDS = 15 * 60


def _format_new_proposal(p):
    origen_label = {
        "sl_tp": "Stop-loss/Take-profit", "evento": "Evento",
        "rebalanceo": "Rebalanceo", "senal": "Señal validada",
    }.get(p["origen"], p["origen"])
    activo = p["symbol"] if p["origen"] == "rebalanceo" else f"*{p['symbol']}*"
    return f"💡 *Nueva propuesta* ({origen_label})\n{activo}: {p['texto']}"


async def _proposals_sync_job(context: ContextTypes.DEFAULT_TYPE):
    """Un ciclo de sincronizacion de Propuestas -- no hace nada fuera del
    horario de rueda. Notifica por Telegram solo las GENUINAMENTE nuevas
    desde la corrida anterior (telegram_notified_at IS NULL, ver schema.sql)
    -- una propuesta en estado='nueva' sigue asi hasta que el usuario la ve/
    actua desde la Web UI, asi que esta columna aparte es la unica forma de
    no re-notificar la misma propuesta en cada corrida de este job solo
    porque nadie la miro todavia."""
    if not monitor.is_market_hours():
        return
    conn = db.get_connection()
    try:
        try:
            proposals.sync_proposals(conn)
        except Exception:
            logger.exception("Fallo la sincronizacion autonoma de Propuestas")
            return
        pendientes = [p for p in db.list_proposals(conn, estado="nueva") if not p.get("telegram_notified_at")]
        if not pendientes:
            return
        allowed_users = db.list_telegram_allowed_users(conn)
        if not allowed_users:
            return
        for p in pendientes:
            text = _format_new_proposal(p)
            for user in allowed_users:
                try:
                    await context.bot.send_message(chat_id=user["chat_id"], text=text, parse_mode="Markdown")
                except Exception:
                    logger.exception("Fallo una notificacion de Propuesta nueva para chat_id=%s", user["chat_id"])
            db.update_proposal(conn, p["id"], telegram_notified_at=now_ar().isoformat())
    finally:
        conn.close()


def _build_application():
    token = _get_token()
    if not token:
        return None
    application = Application.builder().token(token).build()
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("help", cmd_help))
    application.add_handler(CommandHandler("patrimonio", cmd_patrimonio))
    application.add_handler(CommandHandler("ganancia", cmd_ganancia))
    application.add_handler(CommandHandler("prestamos", cmd_prestamos))
    application.add_handler(CommandHandler("vencimientos", cmd_vencimientos))
    application.add_handler(CommandHandler("riesgo", cmd_riesgo))
    application.add_handler(CommandHandler("senales", cmd_senales))
    application.add_handler(CommandHandler("capital", cmd_capital))
    application.add_handler(CommandHandler("set_core", cmd_set_core))
    application.add_handler(CommandHandler("set_tp", cmd_set_tp))
    application.add_handler(CommandHandler("set_sl", cmd_set_sl))
    application.add_handler(CommandHandler("set_profile", cmd_set_profile))
    application.add_handler(CommandHandler("signals", cmd_signals))
    application.add_handler(CommandHandler("ajustes", cmd_ajustes))
    application.add_handler(CallbackQueryHandler(on_callback_query))
    # Cualquier texto libre que no matchee un comando de arriba -- muestra el
    # menu principal en vez de ignorarlo (ver conversacion, "cualquier
    # mensaje" debe desplegar el menu).
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_any_text))

    conn = db.get_connection()
    try:
        hour = db.get_app_settings(conn)["telegram_daily_summary_hour"]
    finally:
        conn.close()
    application.job_queue.run_daily(_daily_summary_job, time=dt_time(hour=hour, minute=0))
    # Paper Trading (ver conversacion) -- tzinfo=ART_TZ EXPLICITO en los dos
    # jobs de abajo: a diferencia de _daily_summary_job de arriba (hora
    # naive, interpretada como UTC por el JobQueue/APScheduler), el horario
    # de rueda (11:00-17:00 ART) y el cierre (17:05 ART) los definio el
    # usuario en hora Argentina -- aca si importa no desfasarse 3hs.
    application.job_queue.run_repeating(
        _paper_trading_scan_job, interval=PAPER_TRADING_SCAN_INTERVAL_SECONDS, first=10,
    )
    application.job_queue.run_daily(
        _paper_trading_closing_report_job, time=dt_time(hour=17, minute=5, tzinfo=ART_TZ),
    )
    application.job_queue.run_repeating(
        _pdf_report_schedule_check_job, interval=PDF_REPORT_SCHEDULE_CHECK_INTERVAL_SECONDS, first=15,
    )
    application.job_queue.run_repeating(
        _proposals_sync_job, interval=PROPOSALS_SYNC_INTERVAL_SECONDS, first=20,
    )
    return application


def start():
    """Arranca el bot en un hilo de fondo con su propio polling -- mismo
    patron que quote_worker.py (hilo daemon, no un proceso aparte)."""
    global _application
    token = _get_token()
    if not token:
        logger.info("TELEGRAM_BOT_TOKEN no configurado -- bot de Telegram deshabilitado.")
        return

    def _run():
        global _application
        _application = _build_application()
        _application.run_polling(stop_signals=None, close_loop=False)

    thread = threading.Thread(target=_run, daemon=True, name="telegram-bot")
    thread.start()
    logger.info("Bot de Telegram iniciado (polling).")
