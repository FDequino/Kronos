"""Tests de portfolio/history.py::_ensure_history -- bug real encontrado
perfilando el dashboard en la VPS: instrumentos sin historico en data912
(LETRAS/bonos de muy corto plazo) reintentaban el fetch de red (timeout=20s)
en CADA carga de pagina porque nada recordaba que ya habia fallado. Estos
tests cubren el "negative cache" (instruments.history_backfill_failed_at)
que soluciona eso -- nunca pegan a la red real (fetchers.backfill_history
siempre monkeypatcheado)."""
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from portfolio import db, history


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_history_backfill.db")
    monkeypatch.setattr(db, "DB_PATH", db_path)
    conn = db.get_connection(db_path)
    yield conn
    conn.close()


def _add_bono(conn, symbol="S16O6"):
    return db.add_instrument(conn, symbol=symbol, type_="bono", market="byma", price_source="data912")


def _insert_price_history_row(conn, instrument_id, timestamp="2026-01-01T00:00:00"):
    conn.execute(
        """INSERT INTO price_history (instrument_id, timestamp, open, high, low, close, volume, source)
           VALUES (?, ?, 1, 1, 1, 1, 0, 'data912')""",
        (instrument_id, timestamp),
    )
    conn.commit()


def test_primer_intento_llama_al_fetch(temp_db, monkeypatch):
    iid = _add_bono(temp_db)
    backfill = MagicMock(side_effect=ValueError(f"S16O6 no encontrado en data912 /live/arg_notes"))
    monkeypatch.setattr(history.fetchers, "backfill_history", backfill)

    history._ensure_history(temp_db, db.get_instrument(temp_db, iid))

    backfill.assert_called_once()
    instrument = db.get_instrument(temp_db, iid)
    assert instrument["history_backfill_failed_at"] is not None


def test_segundo_intento_dentro_de_la_ventana_no_reintenta(temp_db, monkeypatch):
    """El bug real: sin esto, este segundo _ensure_history tambien hubiera
    pegado a la red (y esperado hasta el timeout de 20s) -- con N
    instrumentos asi, eso explicaba +60-100s por carga del dashboard."""
    iid = _add_bono(temp_db)
    backfill = MagicMock(side_effect=ValueError("no encontrado"))
    monkeypatch.setattr(history.fetchers, "backfill_history", backfill)

    history._ensure_history(temp_db, db.get_instrument(temp_db, iid))
    history._ensure_history(temp_db, db.get_instrument(temp_db, iid))
    history._ensure_history(temp_db, db.get_instrument(temp_db, iid))

    backfill.assert_called_once()


def test_reintenta_pasada_la_ventana_de_7_dias(temp_db, monkeypatch):
    iid = _add_bono(temp_db)
    old_failure = (datetime.utcnow() - timedelta(days=8)).isoformat()
    db.update_instrument(temp_db, iid, history_backfill_failed_at=old_failure)
    backfill = MagicMock(side_effect=ValueError("sigue sin estar"))
    monkeypatch.setattr(history.fetchers, "backfill_history", backfill)

    history._ensure_history(temp_db, db.get_instrument(temp_db, iid))

    backfill.assert_called_once()


def test_backfill_exitoso_limpia_la_marca_de_fallo(temp_db, monkeypatch):
    """Un instrumento que fallo ayer y hoy data912 ya lo cubre no debe
    quedar bloqueado 7 dias por una marca vieja."""
    iid = _add_bono(temp_db)
    old_failure = (datetime.utcnow() - timedelta(days=1)).isoformat()
    db.update_instrument(temp_db, iid, history_backfill_failed_at=old_failure)

    def fake_backfill(conn, instrument):
        _insert_price_history_row(conn, instrument["id"])

    monkeypatch.setattr(history.fetchers, "backfill_history", fake_backfill)

    # Forzamos el reintento inmediato simulando que la ventana ya paso --
    # ver test_reintenta_pasada_la_ventana_de_7_dias, acá lo que importa es
    # el resultado despues de un backfill que SI funciona.
    db.update_instrument(temp_db, iid, history_backfill_failed_at=(datetime.utcnow() - timedelta(days=8)).isoformat())
    history._ensure_history(temp_db, db.get_instrument(temp_db, iid))

    instrument = db.get_instrument(temp_db, iid)
    assert instrument["history_backfill_failed_at"] is None


def test_instrumento_con_historico_ya_cargado_nunca_llama_al_fetch(temp_db, monkeypatch):
    iid = _add_bono(temp_db)
    _insert_price_history_row(temp_db, iid)
    backfill = MagicMock()
    monkeypatch.setattr(history.fetchers, "backfill_history", backfill)

    history._ensure_history(temp_db, db.get_instrument(temp_db, iid))

    backfill.assert_not_called()


def test_instrumento_no_data912_nunca_llama_al_fetch(temp_db, monkeypatch):
    iid = db.add_instrument(temp_db, symbol="BTC", type_="cripto", market="binance", price_source="binance")
    backfill = MagicMock()
    monkeypatch.setattr(history.fetchers, "backfill_history", backfill)

    history._ensure_history(temp_db, db.get_instrument(temp_db, iid))

    backfill.assert_not_called()
