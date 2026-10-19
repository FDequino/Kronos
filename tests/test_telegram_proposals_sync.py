"""Tests del job autonomo de sincronizacion de Propuestas (ver conversacion)
-- portfolio/telegram_bot.py::_proposals_sync_job. Antes proposals.sync_proposals()
solo corria adentro del handler de GET /api/portfolio/proposals (dependia de que
alguien entrara a esa pestaña); este job la corre sola en segundo plano y notifica
por Telegram las propuestas GENUINAMENTE nuevas (telegram_notified_at IS NULL).
proposals.sync_proposals se monkeypatchea siempre (evaluar candidatos reales
requiere holdings/señales de mercado completas, fuera del alcance de este test --
ver tests/test_proposals.py para eso); acá solo se prueba la logica propia del
job: gate de horario, que notifica y marca, y que no repite."""
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from portfolio import db, monitor, telegram_bot


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_telegram_proposals_sync.db")
    monkeypatch.setattr(db, "DB_PATH", db_path)
    monkeypatch.setattr(monitor, "is_market_hours", lambda: True)
    monkeypatch.setattr(telegram_bot.proposals, "sync_proposals", MagicMock())
    conn = db.get_connection(db_path)
    yield conn
    conn.close()


class FakeBot:
    def __init__(self):
        self.sent = []  # [(chat_id, text), ...]

    async def send_message(self, chat_id, text, parse_mode=None):
        self.sent.append((chat_id, text))


class FakeContext:
    def __init__(self, bot=None):
        self.bot = bot or FakeBot()


def run(coro):
    import asyncio
    return asyncio.run(coro)


def test_fuera_de_horario_de_rueda_no_hace_nada(temp_db, monkeypatch):
    monkeypatch.setattr(monitor, "is_market_hours", lambda: False)
    db.add_telegram_allowed_user(temp_db, chat_id="111", role="admin")
    db.add_proposal(temp_db, origen="sl_tp", symbol="GGAL", texto="Tocó el stop-loss", evidencia="precio -8.2%")

    context = FakeContext()
    run(telegram_bot._proposals_sync_job(context))

    assert context.bot.sent == []
    telegram_bot.proposals.sync_proposals.assert_not_called()


def test_propuesta_nueva_se_notifica_y_se_marca(temp_db):
    db.add_telegram_allowed_user(temp_db, chat_id="111", role="admin")
    pid = db.add_proposal(temp_db, origen="sl_tp", symbol="GGAL", texto="Tocó el stop-loss", evidencia="precio -8.2%")

    context = FakeContext()
    run(telegram_bot._proposals_sync_job(context))

    telegram_bot.proposals.sync_proposals.assert_called_once()
    assert len(context.bot.sent) == 1
    chat_id, text = context.bot.sent[0]
    assert chat_id == "111"
    assert "GGAL" in text
    proposal = db.get_proposal(temp_db, pid)
    assert proposal["telegram_notified_at"] is not None


def test_propuesta_ya_notificada_no_se_repite(temp_db):
    db.add_telegram_allowed_user(temp_db, chat_id="111", role="admin")
    pid = db.add_proposal(temp_db, origen="sl_tp", symbol="GGAL", texto="Tocó el stop-loss", evidencia="precio -8.2%")
    db.update_proposal(temp_db, pid, telegram_notified_at="2026-01-15T09:00:00")

    context = FakeContext()
    run(telegram_bot._proposals_sync_job(context))

    assert context.bot.sent == []


def test_propuesta_no_nueva_no_se_notifica(temp_db):
    """vista/aceptada/descartada/expirada -- el usuario ya la vio o actuo
    sobre ella desde la Web UI, el job de background no debe insistir."""
    db.add_telegram_allowed_user(temp_db, chat_id="111", role="admin")
    db.add_proposal(temp_db, origen="sl_tp", symbol="GGAL", texto="Tocó el stop-loss",
                     evidencia="precio -8.2%", estado="vista")

    context = FakeContext()
    run(telegram_bot._proposals_sync_job(context))

    assert context.bot.sent == []


def test_sin_usuarios_autorizados_no_notifica_ni_marca(temp_db):
    pid = db.add_proposal(temp_db, origen="sl_tp", symbol="GGAL", texto="Tocó el stop-loss", evidencia="precio -8.2%")

    context = FakeContext()
    run(telegram_bot._proposals_sync_job(context))

    assert context.bot.sent == []
    proposal = db.get_proposal(temp_db, pid)
    assert proposal["telegram_notified_at"] is None


def test_fallo_enviando_a_un_usuario_no_bloquea_el_marcado(temp_db):
    db.add_telegram_allowed_user(temp_db, chat_id="111", role="admin")
    db.add_telegram_allowed_user(temp_db, chat_id="222", role="viewer")
    pid = db.add_proposal(temp_db, origen="sl_tp", symbol="GGAL", texto="Tocó el stop-loss", evidencia="precio -8.2%")

    class FlakyBot(FakeBot):
        async def send_message(self, chat_id, text, parse_mode=None):
            if chat_id == "111":
                raise RuntimeError("chat bloqueado")
            await super().send_message(chat_id, text, parse_mode=parse_mode)

    context = FakeContext(bot=FlakyBot())
    run(telegram_bot._proposals_sync_job(context))

    assert len(context.bot.sent) == 1
    assert context.bot.sent[0][0] == "222"
    proposal = db.get_proposal(temp_db, pid)
    assert proposal["telegram_notified_at"] is not None


def test_rebalanceo_no_lleva_el_simbolo_en_negrita(temp_db):
    """ORIGEN_LABEL/activo (ver webui/static/js/portfolio/proposals.js): una
    propuesta de rebalanceo no referencia un ticker real, mismo criterio
    tambien aplicado al texto del mensaje de Telegram."""
    db.add_telegram_allowed_user(temp_db, chat_id="111", role="admin")
    db.add_proposal(temp_db, origen="rebalanceo", symbol="Core/Satélite", texto="Desvío del objetivo: +6.4pp",
                     evidencia="Core 74% vs objetivo 70%")

    context = FakeContext()
    run(telegram_bot._proposals_sync_job(context))

    assert len(context.bot.sent) == 1
    _, text = context.bot.sent[0]
    assert "*Core/Satélite*" not in text
    assert "Core/Satélite" in text
