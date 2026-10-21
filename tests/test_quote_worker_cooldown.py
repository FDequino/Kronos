"""Tests de portfolio/quote_worker.py::_tick -- bug real encontrado en
produccion (VPS, 2026-10-20): S16O6/S30S6/TO26 (LETRAS/bonos que ya no
aparecen en ningun endpoint live de data912, probablemente vencidos)
reintentaban el fetch cada MARKET_HOURS_INTERVAL_SECONDS (60s) para siempre,
tirando un traceback completo al log en cada ciclo. Mismo "negative cache"
que portfolio/history.py::_ensure_history (ver test_history_backfill.py),
aplicado aca a instruments.quote_failed_at. Nunca pegan a la red real
(fetchers.update_quote siempre monkeypatcheado)."""
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from portfolio import db, quote_worker


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_quote_worker_cooldown.db")
    monkeypatch.setattr(db, "DB_PATH", db_path)
    conn = db.get_connection(db_path)
    yield conn
    conn.close()


def _add_bono(conn, symbol="S16O6"):
    return db.add_instrument(conn, symbol=symbol, type_="bono", market="byma", price_source="data912")


def test_primer_fallo_marca_quote_failed_at(temp_db, monkeypatch):
    _add_bono(temp_db)
    update_quote = MagicMock(side_effect=ValueError("S16O6 no encontrado en data912 /live/arg_notes"))
    monkeypatch.setattr(quote_worker.fetchers, "update_quote", update_quote)

    quote_worker._tick()

    update_quote.assert_called_once()
    instrument = db.list_instruments(temp_db)[0]
    assert instrument["quote_failed_at"] is not None


def test_segundo_tick_dentro_de_la_ventana_no_reintenta(temp_db, monkeypatch):
    """El bug real: sin el cooldown, cada tick (cada 60s en horario de
    mercado) volvia a pegarle a data912 y a loggear un traceback completo
    para un instrumento que nunca va a resolver."""
    _add_bono(temp_db)
    update_quote = MagicMock(side_effect=ValueError("no encontrado"))
    monkeypatch.setattr(quote_worker.fetchers, "update_quote", update_quote)

    quote_worker._tick()
    quote_worker._tick()
    quote_worker._tick()

    update_quote.assert_called_once()


def test_reintenta_pasada_la_ventana_de_cooldown(temp_db, monkeypatch):
    iid = _add_bono(temp_db)
    old_failure = (datetime.utcnow() - timedelta(seconds=quote_worker.QUOTE_RETRY_COOLDOWN_SECONDS + 1)).isoformat()
    db.update_instrument(temp_db, iid, quote_failed_at=old_failure)
    update_quote = MagicMock(side_effect=ValueError("sigue sin estar"))
    monkeypatch.setattr(quote_worker.fetchers, "update_quote", update_quote)

    quote_worker._tick()

    update_quote.assert_called_once()


def test_fetch_exitoso_limpia_la_marca_de_fallo(temp_db, monkeypatch):
    iid = _add_bono(temp_db)
    old_failure = (datetime.utcnow() - timedelta(seconds=quote_worker.QUOTE_RETRY_COOLDOWN_SECONDS + 1)).isoformat()
    db.update_instrument(temp_db, iid, quote_failed_at=old_failure)
    update_quote = MagicMock(return_value={"price": 123.0, "bid": None, "ask": None})
    monkeypatch.setattr(quote_worker.fetchers, "update_quote", update_quote)

    quote_worker._tick()

    instrument = db.get_instrument(temp_db, iid)
    assert instrument["quote_failed_at"] is None
