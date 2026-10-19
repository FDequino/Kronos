"""Tests del bot de Telegram (ver conversacion) -- botones (CallbackQuery) y
restriccion de roles. Corre contra una DB temporal propia (monkeypatch de
portfolio.db.DB_PATH), nunca toca portfolio/portfolio.db real. Las llamadas
caras/con red (Chronos-Bolt, data912, resolve_usd_rate) se mockean para que
esto corra rapido y sin depender de servicios externos levantados -- lo que
se prueba aca es el RUTEO de botones y los permisos, no la logica de negocio
de cada pantalla (esa ya se probo manualmente contra servicios reales a lo
largo de la conversacion)."""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from portfolio import config as capital_config
from portfolio import db
from portfolio import telegram_bot


class FakeMessage:
    def __init__(self, text=None):
        self.text = text  # texto entrante (boton de ReplyKeyboard o texto libre)
        self.texts = []  # [(text, kwargs), ...] -- lo que el bot respondio

    async def reply_text(self, text, **kwargs):
        self.texts.append((text, kwargs))


class FakeChat:
    def __init__(self, chat_id):
        self.id = chat_id


class FakeCallbackQuery:
    def __init__(self, data, chat_id):
        self.data = data
        self._chat_id = chat_id
        self.answered = False
        self.edits = []  # [(text, kwargs), ...]

    async def answer(self):
        self.answered = True

    async def edit_message_text(self, text, **kwargs):
        self.edits.append((text, kwargs))


class FakeBot:
    def __init__(self):
        self.sent = []  # [(chat_id, text, kwargs), ...]

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text, kwargs))


class FakeUpdate:
    """Sirve tanto para un mensaje normal (update.message) como para un
    toque de boton (update.callback_query) -- un Update real trae los dos
    campos, solo uno no-None segun el tipo de evento."""
    def __init__(self, chat_id, message=None, callback_query=None):
        self.effective_chat = FakeChat(chat_id)
        self.message = message
        self.callback_query = callback_query


class FakeContext:
    def __init__(self, args=None, bot=None):
        self.args = args or []
        self.bot = bot or FakeBot()


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """DB temporal y aislada -- nunca portfolio/portfolio.db real. Cada test
    arranca de un esquema limpio (get_connection aplica schema.sql solo)."""
    db_path = str(tmp_path / "test_telegram.db")
    monkeypatch.setattr(db, "DB_PATH", db_path)
    conn = db.get_connection(db_path)
    yield conn
    conn.close()


@pytest.fixture
def admin_update(temp_db):
    """Un chat_id ya autorizado como admin, listo para testear botones."""
    db.add_telegram_allowed_user(temp_db, chat_id="111111", label="Admin", role="admin")
    return "111111"


@pytest.fixture
def viewer_update(temp_db):
    """Un chat_id ya autorizado como viewer (no admin)."""
    db.add_telegram_allowed_user(temp_db, chat_id="222222", label="Viewer", role="viewer")
    return "222222"


def run(coro):
    return asyncio.run(coro)


# --- Botones: menu principal -------------------------------------------------

def test_boton_capital_responde_con_info_de_cuenta(temp_db, viewer_update, monkeypatch):
    monkeypatch.setattr(telegram_bot, "_fetch_capital_text", lambda: "💰 texto de prueba de la cuenta")
    query = FakeCallbackQuery("menu:capital", viewer_update)
    update = FakeUpdate(viewer_update, callback_query=query)
    run(telegram_bot.on_callback_query(update, FakeContext()))

    assert query.answered, "answer() debe llamarse siempre, para sacar el spinner de 'cargando' del boton"
    assert len(query.edits) == 1
    text, kwargs = query.edits[0]
    assert "texto de prueba de la cuenta" in text
    assert kwargs.get("reply_markup") is not None


def test_boton_signals_responde_con_oportunidades(temp_db, viewer_update, monkeypatch):
    monkeypatch.setattr(telegram_bot, "_fetch_signals_text", lambda: "📊 oportunidades de prueba")
    bot = FakeBot()
    query = FakeCallbackQuery("menu:signals", viewer_update)
    update = FakeUpdate(viewer_update, callback_query=query)
    run(telegram_bot.on_callback_query(update, FakeContext(bot=bot)))

    assert query.answered
    # primero un "mirando..." editado en el mismo mensaje, despues el
    # resultado real mandado aparte (puede tardar, ver docstring del handler)
    assert any("Mirando" in t for t, _ in query.edits)
    assert len(bot.sent) == 1
    chat_id, text, kwargs = bot.sent[0]
    assert chat_id == viewer_update
    assert "oportunidades de prueba" in text


def test_boton_ayuda_responde_con_menu_principal(temp_db, viewer_update):
    query = FakeCallbackQuery("menu:ayuda", viewer_update)
    update = FakeUpdate(viewer_update, callback_query=query)
    run(telegram_bot.on_callback_query(update, FakeContext()))

    assert query.answered
    text, kwargs = query.edits[-1]
    assert "Ayuda" in text
    assert kwargs.get("reply_markup") is not None


def test_boton_desconocido_sin_romper(temp_db, viewer_update):
    query = FakeCallbackQuery("algo:que:no:existe", viewer_update)
    update = FakeUpdate(viewer_update, callback_query=query)
    run(telegram_bot.on_callback_query(update, FakeContext()))
    assert query.answered
    assert "No entendí" in query.edits[-1][0]


def test_chat_id_no_autorizado_es_rechazado_en_boton(temp_db):
    query = FakeCallbackQuery("menu:capital", "999999999")
    update = FakeUpdate("999999999", callback_query=query)
    run(telegram_bot.on_callback_query(update, FakeContext()))

    assert query.answered
    text, _ = query.edits[-1]
    assert "Acceso no autorizado" in text
    assert "999999999" in text


# --- Restriccion de roles: Ajustes de Riesgo --------------------------------

def test_admin_puede_abrir_ajustes_de_riesgo(temp_db, admin_update):
    query = FakeCallbackQuery("menu:ajustes", admin_update)
    update = FakeUpdate(admin_update, callback_query=query)
    run(telegram_bot.on_callback_query(update, FakeContext()))

    text, kwargs = query.edits[-1]
    assert "Ajustes de Riesgo" in text
    assert kwargs.get("reply_markup") is not None


def test_viewer_no_puede_abrir_ajustes_de_riesgo(temp_db, viewer_update):
    query = FakeCallbackQuery("menu:ajustes", viewer_update)
    update = FakeUpdate(viewer_update, callback_query=query)
    run(telegram_bot.on_callback_query(update, FakeContext()))

    text, _ = query.edits[-1]
    assert "solo los puede tocar el administrador" in text


def test_admin_puede_cambiar_reparto_con_boton(temp_db, admin_update):
    query = FakeCallbackQuery("risk:core:50", admin_update)
    update = FakeUpdate(admin_update, callback_query=query)
    run(telegram_bot.on_callback_query(update, FakeContext()))

    allocation = capital_config.get_capital_allocation(temp_db)
    assert allocation["core_pct"] == 50.0
    text, _ = query.edits[-1]
    assert "Listo" in text


def test_admin_puede_cambiar_stop_loss_con_boton(temp_db, admin_update):
    query = FakeCallbackQuery("risk:sl:12", admin_update)
    update = FakeUpdate(admin_update, callback_query=query)
    run(telegram_bot.on_callback_query(update, FakeContext()))

    params = capital_config.get_satellite_exit_params(temp_db)
    assert params["stop_loss_pct"] == 12.0


def test_admin_puede_cambiar_take_profit_con_boton(temp_db, admin_update):
    query = FakeCallbackQuery("risk:tp:8", admin_update)
    update = FakeUpdate(admin_update, callback_query=query)
    run(telegram_bot.on_callback_query(update, FakeContext()))

    params = capital_config.get_satellite_exit_params(temp_db)
    assert params["take_profit_pct"] == 8.0


def test_viewer_NO_puede_cambiar_reparto_con_boton(temp_db, viewer_update):
    original = capital_config.get_capital_allocation(temp_db)
    query = FakeCallbackQuery("risk:core:50", viewer_update)
    update = FakeUpdate(viewer_update, callback_query=query)
    run(telegram_bot.on_callback_query(update, FakeContext()))

    # el valor NUNCA debe cambiar -- el boton se rechaza antes de tocar nada
    after = capital_config.get_capital_allocation(temp_db)
    assert after == original
    text, _ = query.edits[-1]
    assert "solo los puede tocar el administrador" in text


def test_viewer_NO_puede_cambiar_stop_loss_con_boton(temp_db, viewer_update):
    original = capital_config.get_satellite_exit_params(temp_db)
    query = FakeCallbackQuery("risk:sl:5", viewer_update)
    update = FakeUpdate(viewer_update, callback_query=query)
    run(telegram_bot.on_callback_query(update, FakeContext()))

    after = capital_config.get_satellite_exit_params(temp_db)
    assert after == original


# --- Restriccion de roles: comandos de texto (regresion) -------------------

def test_viewer_rechazado_en_comando_set_core(temp_db, viewer_update):
    original = capital_config.get_capital_allocation(temp_db)
    message = FakeMessage()
    update = FakeUpdate(viewer_update, message=message)
    run(telegram_bot.cmd_set_core(update, FakeContext(args=["50"])))

    after = capital_config.get_capital_allocation(temp_db)
    assert after == original, "un viewer nunca debe poder cambiar la asignacion via /set_core"
    assert "reservados para administradores" in message.texts[-1][0]


def test_admin_autorizado_en_comando_set_core(temp_db, admin_update):
    message = FakeMessage()
    update = FakeUpdate(admin_update, message=message)
    run(telegram_bot.cmd_set_core(update, FakeContext(args=["50"])))

    allocation = capital_config.get_capital_allocation(temp_db)
    assert allocation["core_pct"] == 50.0
    assert "Listo" in message.texts[-1][0]


def test_viewer_puede_pedir_capital_por_comando(temp_db, viewer_update, monkeypatch):
    monkeypatch.setattr(telegram_bot, "_fetch_capital_text", lambda: "💰 texto de prueba")
    message = FakeMessage()
    update = FakeUpdate(viewer_update, message=message)
    run(telegram_bot.cmd_capital(update, FakeContext()))
    assert "texto de prueba" in message.texts[-1][0]


# --- Perfiles de Inversion: /set_profile (ver conversacion) -----------------

def test_viewer_rechazado_en_comando_set_profile(temp_db, viewer_update):
    original = capital_config.get_capital_allocation(temp_db)
    message = FakeMessage()
    update = FakeUpdate(viewer_update, message=message)
    run(telegram_bot.cmd_set_profile(update, FakeContext(args=["conservador"])))

    after = capital_config.get_capital_allocation(temp_db)
    assert after == original, "un viewer nunca debe poder activar un perfil via /set_profile"
    assert "reservados para administradores" in message.texts[-1][0]


def test_admin_puede_activar_perfil_conservador(temp_db, admin_update):
    message = FakeMessage()
    update = FakeUpdate(admin_update, message=message)
    run(telegram_bot.cmd_set_profile(update, FakeContext(args=["conservador"])))

    allocation = capital_config.get_capital_allocation(temp_db)
    assert allocation["core_pct"] == 70.0
    exit_params = capital_config.get_satellite_exit_params(temp_db)
    assert exit_params == {"stop_loss_pct": 4.0, "take_profit_pct": 3.5}
    assert "Conservador" in message.texts[-1][0]


def test_admin_set_profile_sin_argumentos_muestra_uso(temp_db, admin_update):
    message = FakeMessage()
    update = FakeUpdate(admin_update, message=message)
    run(telegram_bot.cmd_set_profile(update, FakeContext(args=[])))

    assert "Uso: /set_profile" in message.texts[-1][0]
    assert "conservador" in message.texts[-1][0] and "agresivo" in message.texts[-1][0]


def test_admin_set_profile_invalido_responde_con_el_motivo(temp_db, admin_update):
    original = capital_config.get_capital_allocation(temp_db)
    message = FakeMessage()
    update = FakeUpdate(admin_update, message=message)
    run(telegram_bot.cmd_set_profile(update, FakeContext(args=["ultra_agresivo"])))

    after = capital_config.get_capital_allocation(temp_db)
    assert after == original
    assert "ultra_agresivo" in message.texts[-1][0]


# --- /start y texto libre muestran el menu ----------------------------------

def test_start_muestra_menu_principal(temp_db):
    """Primer /start de una instalacion nueva: se autoboostrapea como admin
    Y muestra el menu."""
    message = FakeMessage()
    update = FakeUpdate("333333", message=message)
    run(telegram_bot.cmd_start(update, FakeContext()))

    assert len(message.texts) == 1
    text, kwargs = message.texts[0]
    assert text == telegram_bot.MAIN_MENU_TEXT
    assert kwargs.get("reply_markup") is not None

    user = db.get_telegram_allowed_user_by_chat_id(temp_db, "333333")
    assert user is not None and user["role"] == "admin"


def test_texto_libre_muestra_menu_principal(temp_db, viewer_update):
    message = FakeMessage()
    update = FakeUpdate(viewer_update, message=message)
    run(telegram_bot.on_any_text(update, FakeContext()))

    text, kwargs = message.texts[-1]
    assert text == telegram_bot.MAIN_MENU_TEXT
    assert kwargs.get("reply_markup") is not None


# --- Botonera persistente (ReplyKeyboardMarkup): texto de boton -------------

def test_reply_boton_oportunidades_invoca_senales(temp_db, viewer_update, monkeypatch):
    monkeypatch.setattr(telegram_bot, "_fetch_signals_text", lambda: "📊 oportunidades de prueba")
    message = FakeMessage(text=telegram_bot.REPLY_BTN_SIGNALS)
    update = FakeUpdate(viewer_update, message=message)
    run(telegram_bot.on_any_text(update, FakeContext()))

    assert any("oportunidades de prueba" in t for t, _ in message.texts)


def test_reply_boton_estado_cuenta_invoca_capital(temp_db, viewer_update, monkeypatch):
    monkeypatch.setattr(telegram_bot, "_fetch_capital_text", lambda: "💰 texto de prueba de la cuenta")
    message = FakeMessage(text=telegram_bot.REPLY_BTN_CAPITAL)
    update = FakeUpdate(viewer_update, message=message)
    run(telegram_bot.on_any_text(update, FakeContext()))

    text, _ = message.texts[-1]
    assert "texto de prueba de la cuenta" in text


def test_reply_boton_ayuda_invoca_ayuda(temp_db, viewer_update):
    message = FakeMessage(text=telegram_bot.REPLY_BTN_AYUDA)
    update = FakeUpdate(viewer_update, message=message)
    run(telegram_bot.on_any_text(update, FakeContext()))

    text, _ = message.texts[-1]
    assert "Ayuda" in text or "ayuda" in text.lower()


def test_reply_boton_ajustes_admin_abre_menu_de_riesgo(temp_db, admin_update):
    message = FakeMessage(text=telegram_bot.REPLY_BTN_AJUSTES)
    update = FakeUpdate(admin_update, message=message)
    run(telegram_bot.on_any_text(update, FakeContext()))

    text, kwargs = message.texts[-1]
    assert "Ajustes de Riesgo" in text
    assert kwargs.get("reply_markup") is not None


def test_reply_boton_ajustes_viewer_es_rechazado(temp_db, viewer_update):
    message = FakeMessage(text=telegram_bot.REPLY_BTN_AJUSTES)
    update = FakeUpdate(viewer_update, message=message)
    run(telegram_bot.on_any_text(update, FakeContext()))

    text, _ = message.texts[-1]
    assert "solo los puede tocar el administrador" in text or "reservados para administradores" in text
