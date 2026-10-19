"""Tests de la Programacion Horaria Dinamica del Reporte PDF (ver conversacion)
-- portfolio/telegram_bot.py::send_pdf_report_now (envio manual/sincronico) y
::_pdf_report_schedule_check_job (chequeo periodico que lee pdf_report_schedule/
pdf_report_time/pdf_report_last_sent_* de SQLite EN CADA corrida). Corre contra
una DB temporal propia (mismo patron que tests/test_telegram_bot.py), nunca toca
portfolio/portfolio.db real ni pega a red real: requests.post, pdf_report.generate_portfolio_pdf
y now_ar/today_ar se monkeypatchean."""
import sys
from datetime import date, datetime, time as dt_time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from portfolio import db, monitor, telegram_bot
from portfolio.tz import ART_TZ


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_telegram_pdf_schedule.db")
    monkeypatch.setattr(db, "DB_PATH", db_path)
    # Todos los fixtures de fecha de este archivo (_fake_now) caen en un dia
    # habil real (15/16 de enero de 2026, jueves/viernes) -- is_trading_day()
    # sin embargo usa su propio now_ar (importado directo de portfolio.tz en
    # monitor.py, no el monkeypatcheado en telegram_bot mas abajo) y evaluaria
    # la fecha REAL del sistema en vez de la fingida por el test. Se fuerza
    # aca para que el gate de dia habil (ver conversacion,
    # pdf_report_weekdays_only) nunca dependa de que dia es hoy de verdad.
    monkeypatch.setattr(monitor, "is_trading_day", lambda: True)
    conn = db.get_connection(db_path)
    yield conn
    conn.close()


def _fake_now(hour, minute, day=15):
    return datetime(2026, 1, day, hour, minute, tzinfo=ART_TZ)


class FakeBot:
    def __init__(self):
        self.sent = []  # [(chat_id, filename, caption), ...]

    async def send_document(self, chat_id, document, caption=None):
        self.sent.append((chat_id, getattr(document, "filename", None), caption))


class FakeContext:
    def __init__(self, bot=None):
        self.bot = bot or FakeBot()


def run(coro):
    import asyncio
    return asyncio.run(coro)


# --- send_pdf_report_now (envio manual, HTTP directo) -----------------------

def test_send_pdf_report_now_sin_token_configurado(temp_db, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    result = telegram_bot.send_pdf_report_now(temp_db)
    assert result["ok"] is False
    assert result["sent_to"] == 0
    assert "TELEGRAM_BOT_TOKEN" in result["errors"][0]


def test_send_pdf_report_now_sin_usuarios_autorizados(temp_db, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    result = telegram_bot.send_pdf_report_now(temp_db)
    assert result["ok"] is False
    assert result["sent_to"] == 0
    assert "telegram_allowed_users" in result["errors"][0]


def test_send_pdf_report_now_fallo_generando_pdf_no_rompe(temp_db, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    db.add_telegram_allowed_user(temp_db, chat_id="111", label="Admin", role="admin")
    monkeypatch.setattr(telegram_bot.pdf_report, "generate_portfolio_pdf", MagicMock(side_effect=RuntimeError("boom")))
    result = telegram_bot.send_pdf_report_now(temp_db)
    assert result["ok"] is False
    assert result["sent_to"] == 0
    assert "boom" in result["errors"][0]


def test_send_pdf_report_now_exitoso_a_todos_los_usuarios(temp_db, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    db.add_telegram_allowed_user(temp_db, chat_id="111", label="Admin", role="admin")
    db.add_telegram_allowed_user(temp_db, chat_id="222", label="Viewer", role="viewer")
    monkeypatch.setattr(telegram_bot.pdf_report, "generate_portfolio_pdf", MagicMock(return_value=b"%PDF-fake"))

    calls = []

    def fake_post(url, data=None, files=None, timeout=None):
        calls.append((url, data, files))
        resp = MagicMock()
        resp.json.return_value = {"ok": True}
        return resp

    monkeypatch.setattr(telegram_bot.requests, "post", fake_post)
    result = telegram_bot.send_pdf_report_now(temp_db)

    assert result == {"ok": True, "sent_to": 2, "errors": []}
    assert len(calls) == 2
    url, data, files = calls[0]
    assert url == "https://api.telegram.org/botfake-token/sendDocument"
    assert data["chat_id"] in ("111", "222")
    assert files["document"][2] == "application/pdf"


def test_send_pdf_report_now_un_usuario_falla_no_bloquea_a_los_demas(temp_db, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    db.add_telegram_allowed_user(temp_db, chat_id="111", label="Admin", role="admin")
    db.add_telegram_allowed_user(temp_db, chat_id="222", label="Viewer", role="viewer")
    monkeypatch.setattr(telegram_bot.pdf_report, "generate_portfolio_pdf", MagicMock(return_value=b"%PDF-fake"))

    def fake_post(url, data=None, files=None, timeout=None):
        resp = MagicMock()
        if data["chat_id"] == "111":
            resp.json.return_value = {"ok": False, "description": "bloqueado"}
        else:
            resp.json.return_value = {"ok": True}
        return resp

    monkeypatch.setattr(telegram_bot.requests, "post", fake_post)
    result = telegram_bot.send_pdf_report_now(temp_db)

    assert result["ok"] is True  # al menos uno salio bien
    assert result["sent_to"] == 1
    assert len(result["errors"]) == 1
    assert "111" in result["errors"][0] and "bloqueado" in result["errors"][0]


def test_send_pdf_report_now_no_actualiza_last_sent(temp_db, monkeypatch):
    """Un envio MANUAL de prueba nunca debe marcar la franja como enviada --
    eso bloquearia el envio automatico real de ese dia (ver docstring de
    pdf_report_last_sent_morning/afternoon en portfolio/db.py)."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    db.add_telegram_allowed_user(temp_db, chat_id="111", label="Admin", role="admin")
    monkeypatch.setattr(telegram_bot.pdf_report, "generate_portfolio_pdf", MagicMock(return_value=b"%PDF-fake"))
    monkeypatch.setattr(telegram_bot.requests, "post", lambda *a, **k: MagicMock(json=lambda: {"ok": True}))

    telegram_bot.send_pdf_report_now(temp_db)
    settings = db.get_app_settings(temp_db)
    assert settings["pdf_report_last_sent_morning"] is None
    assert settings["pdf_report_last_sent_afternoon"] is None


# --- _pdf_report_schedule_check_job (chequeo periodico) ---------------------

def test_schedule_check_no_manda_antes_de_la_hora_configurada(temp_db, monkeypatch):
    db.add_telegram_allowed_user(temp_db, chat_id="111", role="admin")
    db.update_app_settings(temp_db, pdf_report_schedule="morning", pdf_report_time="09:30")
    monkeypatch.setattr(telegram_bot, "now_ar", lambda: _fake_now(9, 0))
    monkeypatch.setattr(telegram_bot, "today_ar", lambda: date(2026, 1, 15))
    gen = MagicMock(return_value=b"%PDF-fake")
    monkeypatch.setattr(telegram_bot.pdf_report, "generate_portfolio_pdf", gen)

    context = FakeContext()
    run(telegram_bot._pdf_report_schedule_check_job(context))

    assert context.bot.sent == []
    gen.assert_not_called()


def test_schedule_check_manda_al_pasar_la_hora_y_marca_enviado(temp_db, monkeypatch):
    db.add_telegram_allowed_user(temp_db, chat_id="111", role="admin")
    db.update_app_settings(temp_db, pdf_report_schedule="morning", pdf_report_time="09:30")
    monkeypatch.setattr(telegram_bot, "now_ar", lambda: _fake_now(9, 31))
    monkeypatch.setattr(telegram_bot, "today_ar", lambda: date(2026, 1, 15))
    monkeypatch.setattr(telegram_bot.pdf_report, "generate_portfolio_pdf", MagicMock(return_value=b"%PDF-fake"))

    context = FakeContext()
    run(telegram_bot._pdf_report_schedule_check_job(context))

    assert len(context.bot.sent) == 1
    chat_id, filename, caption = context.bot.sent[0]
    assert chat_id == "111"
    assert "mañana" in caption
    settings = db.get_app_settings(temp_db)
    assert settings["pdf_report_last_sent_morning"] == "2026-01-15"
    assert settings["pdf_report_last_sent_afternoon"] is None


def test_schedule_check_no_repite_el_envio_el_mismo_dia(temp_db, monkeypatch):
    """Simula el chequeo periodico corriendo de nuevo unos minutos despues,
    todavia dentro de la misma franja -- no debe mandar una segunda vez."""
    db.add_telegram_allowed_user(temp_db, chat_id="111", role="admin")
    db.update_app_settings(temp_db, pdf_report_schedule="morning", pdf_report_time="09:30",
                            pdf_report_last_sent_morning="2026-01-15")
    monkeypatch.setattr(telegram_bot, "now_ar", lambda: _fake_now(9, 40))
    monkeypatch.setattr(telegram_bot, "today_ar", lambda: date(2026, 1, 15))
    gen = MagicMock(return_value=b"%PDF-fake")
    monkeypatch.setattr(telegram_bot.pdf_report, "generate_portfolio_pdf", gen)

    context = FakeContext()
    run(telegram_bot._pdf_report_schedule_check_job(context))

    assert context.bot.sent == []
    gen.assert_not_called()


def test_schedule_check_manda_de_nuevo_al_dia_siguiente(temp_db, monkeypatch):
    db.add_telegram_allowed_user(temp_db, chat_id="111", role="admin")
    db.update_app_settings(temp_db, pdf_report_schedule="morning", pdf_report_time="09:30",
                            pdf_report_last_sent_morning="2026-01-15")
    monkeypatch.setattr(telegram_bot, "now_ar", lambda: _fake_now(9, 40, day=16))
    monkeypatch.setattr(telegram_bot, "today_ar", lambda: date(2026, 1, 16))
    monkeypatch.setattr(telegram_bot.pdf_report, "generate_portfolio_pdf", MagicMock(return_value=b"%PDF-fake"))

    context = FakeContext()
    run(telegram_bot._pdf_report_schedule_check_job(context))

    assert len(context.bot.sent) == 1
    settings = db.get_app_settings(temp_db)
    assert settings["pdf_report_last_sent_morning"] == "2026-01-16"


def test_schedule_check_both_manda_mañana_y_tarde_por_separado(temp_db, monkeypatch):
    db.add_telegram_allowed_user(temp_db, chat_id="111", role="admin")
    db.update_app_settings(temp_db, pdf_report_schedule="both", pdf_report_time="09:30")
    # 18:00 -- ya paso tanto la mañana (09:30) como la tarde fija (17:30)
    monkeypatch.setattr(telegram_bot, "now_ar", lambda: _fake_now(18, 0))
    monkeypatch.setattr(telegram_bot, "today_ar", lambda: date(2026, 1, 15))
    monkeypatch.setattr(telegram_bot.pdf_report, "generate_portfolio_pdf", MagicMock(return_value=b"%PDF-fake"))

    context = FakeContext()
    run(telegram_bot._pdf_report_schedule_check_job(context))

    assert len(context.bot.sent) == 2
    captions = {c for _, _, c in context.bot.sent}
    assert any("mañana" in c for c in captions)
    assert any("tarde" in c for c in captions)
    settings = db.get_app_settings(temp_db)
    assert settings["pdf_report_last_sent_morning"] == "2026-01-15"
    assert settings["pdf_report_last_sent_afternoon"] == "2026-01-15"


def test_schedule_check_afternoon_scheduled_no_manda_en_la_mañana(temp_db, monkeypatch):
    db.add_telegram_allowed_user(temp_db, chat_id="111", role="admin")
    db.update_app_settings(temp_db, pdf_report_schedule="afternoon", pdf_report_time="09:30")
    monkeypatch.setattr(telegram_bot, "now_ar", lambda: _fake_now(9, 31))
    monkeypatch.setattr(telegram_bot, "today_ar", lambda: date(2026, 1, 15))
    gen = MagicMock(return_value=b"%PDF-fake")
    monkeypatch.setattr(telegram_bot.pdf_report, "generate_portfolio_pdf", gen)

    context = FakeContext()
    run(telegram_bot._pdf_report_schedule_check_job(context))

    assert context.bot.sent == []
    gen.assert_not_called()


def test_schedule_check_pdf_report_time_corrupto_degrada_a_default(temp_db, monkeypatch):
    db.add_telegram_allowed_user(temp_db, chat_id="111", role="admin")
    db.update_app_settings(temp_db, pdf_report_schedule="morning", pdf_report_time="no-es-una-hora")
    monkeypatch.setattr(telegram_bot, "now_ar", lambda: _fake_now(9, 31))
    monkeypatch.setattr(telegram_bot, "today_ar", lambda: date(2026, 1, 15))
    monkeypatch.setattr(telegram_bot.pdf_report, "generate_portfolio_pdf", MagicMock(return_value=b"%PDF-fake"))

    context = FakeContext()
    run(telegram_bot._pdf_report_schedule_check_job(context))

    # 09:31 ya paso el default de 09:30 -- confirma que no rompio y cayo al default
    assert len(context.bot.sent) == 1


def test_schedule_check_sin_usuarios_autorizados_no_manda_ni_marca(temp_db, monkeypatch):
    db.update_app_settings(temp_db, pdf_report_schedule="morning", pdf_report_time="09:30")
    monkeypatch.setattr(telegram_bot, "now_ar", lambda: _fake_now(9, 31))
    monkeypatch.setattr(telegram_bot, "today_ar", lambda: date(2026, 1, 15))
    gen = MagicMock(return_value=b"%PDF-fake")
    monkeypatch.setattr(telegram_bot.pdf_report, "generate_portfolio_pdf", gen)

    context = FakeContext()
    run(telegram_bot._pdf_report_schedule_check_job(context))

    assert context.bot.sent == []
    gen.assert_not_called()
    settings = db.get_app_settings(temp_db)
    assert settings["pdf_report_last_sent_morning"] is None


def test_schedule_check_fallo_generando_pdf_no_rompe_ni_marca_enviado(temp_db, monkeypatch):
    db.add_telegram_allowed_user(temp_db, chat_id="111", role="admin")
    db.update_app_settings(temp_db, pdf_report_schedule="morning", pdf_report_time="09:30")
    monkeypatch.setattr(telegram_bot, "now_ar", lambda: _fake_now(9, 31))
    monkeypatch.setattr(telegram_bot, "today_ar", lambda: date(2026, 1, 15))
    monkeypatch.setattr(telegram_bot.pdf_report, "generate_portfolio_pdf", MagicMock(side_effect=RuntimeError("boom")))

    context = FakeContext()
    run(telegram_bot._pdf_report_schedule_check_job(context))  # no debe levantar

    assert context.bot.sent == []
    settings = db.get_app_settings(temp_db)
    assert settings["pdf_report_last_sent_morning"] is None


def test_schedule_check_fallo_enviando_a_un_usuario_no_bloquea_el_marcado(temp_db, monkeypatch):
    db.add_telegram_allowed_user(temp_db, chat_id="111", role="admin")
    db.add_telegram_allowed_user(temp_db, chat_id="222", role="viewer")
    db.update_app_settings(temp_db, pdf_report_schedule="morning", pdf_report_time="09:30")
    monkeypatch.setattr(telegram_bot, "now_ar", lambda: _fake_now(9, 31))
    monkeypatch.setattr(telegram_bot, "today_ar", lambda: date(2026, 1, 15))
    monkeypatch.setattr(telegram_bot.pdf_report, "generate_portfolio_pdf", MagicMock(return_value=b"%PDF-fake"))

    class FlakyBot(FakeBot):
        async def send_document(self, chat_id, document, caption=None):
            if chat_id == "111":
                raise RuntimeError("chat bloqueado")
            await super().send_document(chat_id, document, caption=caption)

    context = FakeContext(bot=FlakyBot())
    run(telegram_bot._pdf_report_schedule_check_job(context))

    assert len(context.bot.sent) == 1
    assert context.bot.sent[0][0] == "222"
    # la franja se marca igual -- un fallo puntual no debe reintentar en bucle
    settings = db.get_app_settings(temp_db)
    assert settings["pdf_report_last_sent_morning"] == "2026-01-15"


# --- Hora de la tarde configurable (ver conversacion) ------------------------

def test_schedule_check_afternoon_time_configurable(temp_db, monkeypatch):
    """La tarde dejo de ser PDF_REPORT_AFTERNOON_TIME (17:30 fijo) -- ahora
    lee pdf_report_time_afternoon de settings, mismo criterio que la mañana."""
    db.add_telegram_allowed_user(temp_db, chat_id="111", role="admin")
    db.update_app_settings(temp_db, pdf_report_schedule="afternoon", pdf_report_time_afternoon="15:00")
    monkeypatch.setattr(telegram_bot, "now_ar", lambda: _fake_now(15, 1))
    monkeypatch.setattr(telegram_bot, "today_ar", lambda: date(2026, 1, 15))
    monkeypatch.setattr(telegram_bot.pdf_report, "generate_portfolio_pdf", MagicMock(return_value=b"%PDF-fake"))

    context = FakeContext()
    run(telegram_bot._pdf_report_schedule_check_job(context))

    assert len(context.bot.sent) == 1
    settings = db.get_app_settings(temp_db)
    assert settings["pdf_report_last_sent_afternoon"] == "2026-01-15"


def test_schedule_check_afternoon_time_corrupto_degrada_a_1730(temp_db, monkeypatch):
    db.add_telegram_allowed_user(temp_db, chat_id="111", role="admin")
    db.update_app_settings(temp_db, pdf_report_schedule="afternoon", pdf_report_time_afternoon="not-a-time")
    monkeypatch.setattr(telegram_bot, "now_ar", lambda: _fake_now(17, 31))
    monkeypatch.setattr(telegram_bot, "today_ar", lambda: date(2026, 1, 15))
    monkeypatch.setattr(telegram_bot.pdf_report, "generate_portfolio_pdf", MagicMock(return_value=b"%PDF-fake"))

    context = FakeContext()
    run(telegram_bot._pdf_report_schedule_check_job(context))

    assert len(context.bot.sent) == 1


# --- Gate de dia habil (pdf_report_weekdays_only, ver conversacion) ---------

def test_schedule_check_fin_de_semana_no_manda_por_default(temp_db, monkeypatch):
    """Antes este job no tenia NINGUN gate de dia habil -- default activado
    (1) para que una instalacion existente no mande un "Resultado del Dia"
    sabado/domingo sin rueda detras."""
    db.add_telegram_allowed_user(temp_db, chat_id="111", role="admin")
    db.update_app_settings(temp_db, pdf_report_schedule="morning", pdf_report_time="09:30")
    monkeypatch.setattr(telegram_bot, "now_ar", lambda: _fake_now(9, 31))
    monkeypatch.setattr(telegram_bot, "today_ar", lambda: date(2026, 1, 15))
    monkeypatch.setattr(monitor, "is_trading_day", lambda: False)  # pisa el True default de temp_db para este test
    gen = MagicMock(return_value=b"%PDF-fake")
    monkeypatch.setattr(telegram_bot.pdf_report, "generate_portfolio_pdf", gen)

    context = FakeContext()
    run(telegram_bot._pdf_report_schedule_check_job(context))

    assert context.bot.sent == []
    gen.assert_not_called()


def test_schedule_check_fin_de_semana_manda_si_weekdays_only_desactivado(temp_db, monkeypatch):
    db.add_telegram_allowed_user(temp_db, chat_id="111", role="admin")
    db.update_app_settings(temp_db, pdf_report_schedule="morning", pdf_report_time="09:30",
                            pdf_report_weekdays_only=0)
    monkeypatch.setattr(telegram_bot, "now_ar", lambda: _fake_now(9, 31))
    monkeypatch.setattr(telegram_bot, "today_ar", lambda: date(2026, 1, 15))
    monkeypatch.setattr(monitor, "is_trading_day", lambda: False)
    monkeypatch.setattr(telegram_bot.pdf_report, "generate_portfolio_pdf", MagicMock(return_value=b"%PDF-fake"))

    context = FakeContext()
    run(telegram_bot._pdf_report_schedule_check_job(context))

    assert len(context.bot.sent) == 1
