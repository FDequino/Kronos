"""Tests de portfolio/daily_backfill.py::run -- el job de cron que reemplaza
la dependencia de que corra un pronostico para refrescar price_history (ver
conversacion: "Backfill diario de price_history por cron, en vez de lazy al
correr un pronostico"). Nunca pega a la red real (fetchers.backfill_history
siempre monkeypatcheado, mismo criterio que test_history_backfill.py)."""
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from portfolio import daily_backfill, db


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_daily_backfill.db")
    monkeypatch.setattr(db, "DB_PATH", db_path)
    conn = db.get_connection(db_path)
    yield conn
    conn.close()


def test_corre_backfill_para_cada_instrumento_data912_historico(temp_db, monkeypatch):
    acc = db.add_instrument(temp_db, symbol="GGAL", type_="accion", market="byma", price_source="data912")
    bono = db.add_instrument(temp_db, symbol="TZX27", type_="bono", market="byma", price_source="data912")
    backfill = MagicMock(return_value=10)
    monkeypatch.setattr(daily_backfill.fetchers, "backfill_history", backfill)

    result = daily_backfill.run(temp_db)

    assert backfill.call_count == 2
    assert set(result["ok"]) == {"GGAL", "TZX27"}
    assert result["failed"] == []


def test_ignora_instrumentos_no_data912(temp_db, monkeypatch):
    db.add_instrument(temp_db, symbol="BTC", type_="cripto", market="binance", price_source="binance")
    backfill = MagicMock()
    monkeypatch.setattr(daily_backfill.fetchers, "backfill_history", backfill)

    result = daily_backfill.run(temp_db)

    backfill.assert_not_called()
    assert result == {"ok": [], "failed": []}


def test_ignora_tipos_sin_historico_data912(temp_db, monkeypatch):
    # fondo/on no estan en history.HISTORICAL_TYPES (ver portfolio/history.py).
    db.add_instrument(temp_db, symbol="BALANZ2", type_="fondo", market="fondo", price_source="data912")
    backfill = MagicMock()
    monkeypatch.setattr(daily_backfill.fetchers, "backfill_history", backfill)

    result = daily_backfill.run(temp_db)

    backfill.assert_not_called()
    assert result == {"ok": [], "failed": []}


def test_un_instrumento_que_falla_no_corta_el_resto_del_batch(temp_db, monkeypatch):
    bueno = db.add_instrument(temp_db, symbol="GGAL", type_="accion", market="byma", price_source="data912")
    malo = db.add_instrument(temp_db, symbol="S16O6", type_="bono", market="byma", price_source="data912")

    def fake_backfill(conn, instrument):
        if instrument["symbol"] == "S16O6":
            raise ValueError("S16O6 no encontrado en data912")
        return 5

    monkeypatch.setattr(daily_backfill.fetchers, "backfill_history", fake_backfill)

    result = daily_backfill.run(temp_db)

    assert result["ok"] == ["GGAL"]
    assert result["failed"] == ["S16O6"]


def test_corrida_exitosa_limpia_marca_de_fallo_previa(temp_db, monkeypatch):
    from datetime import datetime, timedelta
    iid = db.add_instrument(temp_db, symbol="GGAL", type_="accion", market="byma", price_source="data912")
    db.update_instrument(temp_db, iid, history_backfill_failed_at=(datetime.utcnow() - timedelta(days=3)).isoformat())
    monkeypatch.setattr(daily_backfill.fetchers, "backfill_history", MagicMock(return_value=1))

    daily_backfill.run(temp_db)

    assert db.get_instrument(temp_db, iid)["history_backfill_failed_at"] is None


def test_instrumento_que_falla_queda_marcado(temp_db, monkeypatch):
    iid = db.add_instrument(temp_db, symbol="S16O6", type_="bono", market="byma", price_source="data912")
    monkeypatch.setattr(daily_backfill.fetchers, "backfill_history", MagicMock(side_effect=ValueError("no encontrado")))

    daily_backfill.run(temp_db)

    assert db.get_instrument(temp_db, iid)["history_backfill_failed_at"] is not None
