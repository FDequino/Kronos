"""Tests de portfolio/routes.py::_compute_technical_cache_row -- el edge
tecnico/RSI de un CEDEAR ya mapeado a un subyacente real (ver
forecasting.CEDEAR_DIRECT_UNDERLYING_SYMBOLS) ahora se calcula sobre esa
serie real en vez de price_history del CEDEAR (misma muestra 5-30x mas chica
que ya se encontro para el forecasting de Kronos). Nunca pega a yfinance
real -- fetchers.ensure_underlying_ohlcv_history se monkeypatchea a no-op.

build_chart_payload (la ruta "de siempre", sin tocar en esta fase) se
monkeypatchea directo en vez de sembrar price_history real -- su propia
ventana de lookback depende de la fecha de HOY y de dias habiles, fragil
para un test con fechas sinteticas fijas; lo que este archivo testea es el
RUTEO en _compute_technical_cache_row (que serie elige), no build_chart_payload
en si (ya existente, sin cambios)."""
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from portfolio import db, fetchers, forecasting, indicators, routes


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_technical_cache_underlying.db")
    monkeypatch.setattr(db, "DB_PATH", db_path)
    conn = db.get_connection(db_path)
    monkeypatch.setattr(fetchers, "ensure_underlying_ohlcv_history", lambda *a, **k: None)
    yield conn
    conn.close()


def _trend_closes(n, up):
    """Serie con drift fuerte (sube o baja) pero con reversiones ocasionales
    -- un RSI de drift puro (sin ninguna reversion) da division por cero y
    cae al neutro 50 (ver portfolio/indicators.py::rsi, loss/gain en 0 ->
    NaN -> fillna(50)), por eso cada 5 velas se mete una reversion chica."""
    step_main = 3 if up else -3
    step_rev = -1 if up else 1
    vals = [200.0]
    for i in range(n - 1):
        vals.append(vals[-1] + (step_rev if i % 5 == 0 else step_main))
    return vals


def _seed_underlying(conn, symbol, n, up):
    base = date(2024, 1, 1)
    closes = _trend_closes(n, up)
    rows = [
        {"fecha": (base + timedelta(days=i)).isoformat(), "open": c, "high": c, "low": c, "close": c, "volume": 500}
        for i, c in enumerate(closes)
    ]
    db.upsert_underlying_ohlcv(conn, symbol, rows)


def _mock_build_chart_payload(monkeypatch, n=100, up=False):
    """Fuerza la ruta "de siempre" (CEDEAR, sin subyacente) a una serie
    sintetica controlada -- ver docstring del modulo."""
    closes = _trend_closes(n, up)
    indicators_dict = indicators.compute_all(pd.DataFrame({"close": closes}))
    monkeypatch.setattr(
        forecasting, "build_chart_payload",
        lambda *a, **k: {"indicators": indicators_dict},
    )
    return closes


def _seed_price_history_for_edge(conn, instrument_id, n, up):
    """Solo alimenta indicators.backtest_rsi_signal (via db.get_price_history),
    que SI sigue leyendo price_history real para la ruta sin subyacente --
    independiente de la ventana de build_chart_payload (mockeada arriba)."""
    base = date(2024, 1, 1)
    for i, c in enumerate(_trend_closes(n, up)):
        ts = (base + timedelta(days=i)).isoformat() + "T00:00:00"
        conn.execute(
            "INSERT INTO price_history (instrument_id, timestamp, open, high, low, close, volume) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (instrument_id, ts, c, c, c, c, 1000),
        )
    conn.commit()


def _config(conn):
    return db.get_signal_config(conn)


def test_cedear_mapeado_con_cobertura_usa_subyacente_real(temp_db, monkeypatch):
    assert "AAPL" in forecasting.CEDEAR_DIRECT_UNDERLYING_SYMBOLS
    iid = db.add_instrument(temp_db, "AAPL", "cedear", "byma", "data912")
    # CEDEAR (ruta de siempre, mockeada): bajando -> "comprar".
    _mock_build_chart_payload(monkeypatch, n=100, up=False)
    _seed_price_history_for_edge(temp_db, iid, n=100, up=False)
    # Subyacente real: subiendo -> "vender".
    _seed_underlying(temp_db, "AAPL", n=100, up=True)

    instrument = db.get_instrument(temp_db, iid)
    row = routes._compute_technical_cache_row(temp_db, instrument, _config(temp_db))

    # Si hubiera usado la serie del CEDEAR (mockeada, bajando) daria
    # "comprar" -- "vender" confirma que tomo el subyacente real (subiendo).
    assert row["tecnico"] == "vender"
    assert row["n"] is not None and row["n"] > 0


def test_cedear_mapeado_sin_cobertura_suficiente_degrada_a_cedear(temp_db, monkeypatch):
    iid = db.add_instrument(temp_db, "AAPL", "cedear", "byma", "data912")
    _mock_build_chart_payload(monkeypatch, n=100, up=False)
    _seed_price_history_for_edge(temp_db, iid, n=100, up=False)
    # Menos filas que UNDERLYING_MODEL_MIN_ROWS -- cobertura insuficiente.
    _seed_underlying(temp_db, "AAPL", n=10, up=True)

    instrument = db.get_instrument(temp_db, iid)
    row = routes._compute_technical_cache_row(temp_db, instrument, _config(temp_db))

    # Degrada a la ruta de siempre (CEDEAR bajando -> "comprar").
    assert row["tecnico"] == "comprar"


def test_simbolo_no_mapeado_se_comporta_igual_que_antes(temp_db, monkeypatch):
    """Regresion: IBM no esta en CEDEAR_DIRECT_UNDERLYING_SYMBOLS -- nunca
    deberia intentar leer underlying_price_history."""
    assert "IBM" not in forecasting.CEDEAR_DIRECT_UNDERLYING_SYMBOLS
    iid = db.add_instrument(temp_db, "IBM", "cedear", "byma", "data912")
    _mock_build_chart_payload(monkeypatch, n=100, up=False)
    _seed_price_history_for_edge(temp_db, iid, n=100, up=False)

    def _boom(*a, **k):
        raise AssertionError("no deberia llamarse para un simbolo no mapeado")
    monkeypatch.setattr(forecasting, "get_underlying_price_rows_for_technical", _boom)

    instrument = db.get_instrument(temp_db, iid)
    row = routes._compute_technical_cache_row(temp_db, instrument, _config(temp_db))

    assert row["tecnico"] == "comprar"


def test_accion_no_usa_subyacente_aunque_simbolo_mapeado(temp_db, monkeypatch):
    """CEDEAR_DIRECT_UNDERLYING_SYMBOLS solo aplica a type == 'cedear' --
    una 'accion' con el mismo ticker nunca debe tomar este camino."""
    iid = db.add_instrument(temp_db, "AAPL", "accion", "byma", "data912")
    _mock_build_chart_payload(monkeypatch, n=100, up=False)
    _seed_price_history_for_edge(temp_db, iid, n=100, up=False)
    _seed_underlying(temp_db, "AAPL", n=100, up=True)

    def _boom(*a, **k):
        raise AssertionError("no deberia llamarse para asset_type != 'cedear'")
    monkeypatch.setattr(forecasting, "get_underlying_price_rows_for_technical", _boom)

    instrument = db.get_instrument(temp_db, iid)
    row = routes._compute_technical_cache_row(temp_db, instrument, _config(temp_db))

    assert row["tecnico"] == "comprar"
