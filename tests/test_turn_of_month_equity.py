"""portfolio/turn_of_month_equity.py -- cache con TTL (nunca pega a yfinance
en cada llamada, ver OBJETIVO_MARCO.md 2026-11-11: Yahoo rate-limita pedidos
seguidos) y shape de salida del motor (mismo contrato que
crypto_carry.py::simulate_funding_rate_carry_engine)."""
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from portfolio import db, turn_of_month_equity


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_turn_of_month_equity.db")
    monkeypatch.setattr(db, "DB_PATH", db_path)
    conn = db.get_connection(db_path)
    yield conn
    conn.close()


def test_is_turn_of_month_day_marca_ultimos_y_primeros_3_dias_habiles():
    # Definicion Ariel 1987, ver OBJETIVO_MARCO.md -- noviembre 2026 tiene 30 dias
    assert turn_of_month_equity._is_turn_of_month_day(pd.Timestamp("2026-11-28")) is True
    assert turn_of_month_equity._is_turn_of_month_day(pd.Timestamp("2026-11-03")) is True
    assert turn_of_month_equity._is_turn_of_month_day(pd.Timestamp("2026-11-15")) is False


def test_cache_vacio_dispara_descarga_y_persiste(temp_db, monkeypatch):
    fake_series = pd.Series(
        [0.5, -0.2, 0.3],
        index=pd.to_datetime(["2026-01-02", "2026-01-03", "2026-01-30"]),
    )
    monkeypatch.setattr(turn_of_month_equity, "_fetch_fresh_daily_returns", lambda: fake_series)

    result = turn_of_month_equity.simulate_turn_of_month_engine(temp_db)

    assert result["engine"] == "turn_of_month_sp500"
    assert list(result["daily_returns_pct"].values) == [0.5, -0.2, 0.3]
    assert (result["idle_weight"] == 0.0).all()
    assert result["trades"] == []

    rows = temp_db.execute("SELECT COUNT(*) AS n FROM equity_strategy_cache WHERE strategy_name = ?",
                            ("turn_of_month_sp500",)).fetchone()
    assert rows["n"] == 3


def test_cache_fresco_no_vuelve_a_descargar(temp_db, monkeypatch):
    fake_series = pd.Series([1.0], index=pd.to_datetime(["2026-01-02"]))
    monkeypatch.setattr(turn_of_month_equity, "_fetch_fresh_daily_returns", lambda: fake_series)
    turn_of_month_equity.simulate_turn_of_month_engine(temp_db)  # siembra el cache

    def _debe_no_llamarse():
        raise AssertionError("no deberia volver a descargar con el cache fresco")
    monkeypatch.setattr(turn_of_month_equity, "_fetch_fresh_daily_returns", _debe_no_llamarse)

    result = turn_of_month_equity.simulate_turn_of_month_engine(temp_db)
    assert list(result["daily_returns_pct"].values) == [1.0]


def test_cache_viejo_vuelve_a_descargar(temp_db, monkeypatch):
    temp_db.execute(
        """INSERT INTO equity_strategy_cache (strategy_name, trade_date, daily_return_pct, fetched_at)
           VALUES (?, ?, ?, ?)""",
        ("turn_of_month_sp500", "2020-01-02", 0.1,
         (datetime.now() - timedelta(hours=turn_of_month_equity.CACHE_TTL_HOURS + 1)).isoformat()),
    )
    temp_db.commit()

    fresh_series = pd.Series([2.0], index=pd.to_datetime(["2026-01-02"]))
    monkeypatch.setattr(turn_of_month_equity, "_fetch_fresh_daily_returns", lambda: fresh_series)

    result = turn_of_month_equity.simulate_turn_of_month_engine(temp_db)

    assert list(result["daily_returns_pct"].values) == [2.0]  # reemplazo el dato viejo, no lo acumulo


def test_descarga_vacia_conserva_el_cache_anterior_nunca_lo_borra(temp_db, monkeypatch):
    fake_series = pd.Series([1.0], index=pd.to_datetime(["2026-01-02"]))
    monkeypatch.setattr(turn_of_month_equity, "_fetch_fresh_daily_returns", lambda: fake_series)
    turn_of_month_equity.simulate_turn_of_month_engine(temp_db)  # siembra el cache

    temp_db.execute(
        "UPDATE equity_strategy_cache SET fetched_at = ? WHERE strategy_name = ?",
        ((datetime.now() - timedelta(hours=turn_of_month_equity.CACHE_TTL_HOURS + 1)).isoformat(), "turn_of_month_sp500"),
    )
    temp_db.commit()
    monkeypatch.setattr(turn_of_month_equity, "_fetch_fresh_daily_returns", lambda: pd.Series(dtype=float))

    result = turn_of_month_equity.simulate_turn_of_month_engine(temp_db)

    assert list(result["daily_returns_pct"].values) == [1.0]  # un fallo de red no borra lo que ya habia
