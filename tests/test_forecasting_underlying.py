"""Tests de portfolio/forecasting.py::build_model_input -- precio REAL del
subyacente en USD (yfinance, underlying_price_history) en vez del "USD
sintetico" (CEDEAR en ARS / CCL historico) para los simbolos ya mapeados en
CEDEAR_DIRECT_UNDERLYING_SYMBOLS (ver conversacion: medido que el sintetico
mete 2-3x mas ruido que señal real para estos tickers).

Nunca pega a yfinance real -- ensure_underlying_ohlcv_history se monkeypatchea
a no-op, los datos del subyacente se insertan directo via db.upsert_underlying_ohlcv
(mismo criterio que tests/test_history_backfill.py para fetchers.backfill_history)."""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from portfolio import db, fetchers
from portfolio.forecasting import (
    CEDEAR_DIRECT_UNDERLYING_SYMBOLS, PRICE_COLS, UNDERLYING_MODEL_MIN_ROWS, build_model_input,
)


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_forecasting_underlying.db")
    monkeypatch.setattr(db, "DB_PATH", db_path)
    conn = db.get_connection(db_path)
    monkeypatch.setattr(fetchers, "ensure_underlying_ohlcv_history", lambda *a, **k: None)
    yield conn
    conn.close()


def _lookback_df(n=80, start_price=200.0, daily_step=1.0):
    dates = pd.date_range("2026-01-01", periods=n, freq="D")
    closes = [start_price + i * daily_step for i in range(n)]
    return pd.DataFrame({
        "timestamps": dates,
        "open": closes, "high": [c * 1.01 for c in closes], "low": [c * 0.99 for c in closes],
        "close": closes, "volume": [1000.0] * n, "amount": [0.0] * n,
    })


def _seed_underlying(conn, symbol, dates, start_price=150.0, daily_step=0.5):
    rows = [
        {
            "fecha": d.strftime("%Y-%m-%d"),
            "open": start_price + i * daily_step, "high": (start_price + i * daily_step) * 1.01,
            "low": (start_price + i * daily_step) * 0.99, "close": start_price + i * daily_step,
            "volume": 500.0,
        }
        for i, d in enumerate(dates)
    ]
    db.upsert_underlying_ohlcv(conn, symbol, rows)


def test_simbolo_mapeado_con_cobertura_completa_usa_subyacente_real(temp_db):
    assert "AAPL" in CEDEAR_DIRECT_UNDERLYING_SYMBOLS
    lookback = _lookback_df(n=UNDERLYING_MODEL_MIN_ROWS + 10, start_price=5000.0)
    _seed_underlying(temp_db, "AAPL", lookback["timestamps"], start_price=150.0, daily_step=0.5)

    model_df, reconversion_ref, model_currency = build_model_input(temp_db, lookback, "cedear", symbol="AAPL")

    assert model_currency == "USD_REAL_UNDERLYING"
    # reconversion_ref es el ultimo cierre REAL EN ARS del CEDEAR, no un CCL.
    assert reconversion_ref == pytest.approx(lookback["close"].iloc[-1])
    # El modelo ve el precio real del subyacente (~150+), no el sintetico ARS/CCL.
    expected_last_close = 150.0 + (UNDERLYING_MODEL_MIN_ROWS + 10 - 1) * 0.5
    assert model_df["close"].iloc[-1] == pytest.approx(expected_last_close)


def test_simbolo_mapeado_sin_cobertura_suficiente_degrada_a_sintetico(temp_db):
    lookback = _lookback_df(n=UNDERLYING_MODEL_MIN_ROWS + 10, start_price=5000.0)
    # Menos filas que UNDERLYING_MODEL_MIN_ROWS -- cobertura insuficiente.
    _seed_underlying(temp_db, "AAPL", lookback["timestamps"][:10], start_price=150.0)
    _seed_ccl(temp_db, lookback["timestamps"])

    model_df, reconversion_ref, model_currency = build_model_input(temp_db, lookback, "cedear", symbol="AAPL")

    assert model_currency == "USD"  # sintetico, no USD_REAL_UNDERLYING
    assert reconversion_ref is not None


def test_simbolo_mapeado_con_hueco_de_fecha_degrada_a_sintetico(temp_db):
    """Si el subyacente no cubre las fechas MAS VIEJAS de la ventana (el
    ffill no puede tapar un hueco anterior al primer dato real), se degrada
    -- nunca se inventa un precio."""
    lookback = _lookback_df(n=UNDERLYING_MODEL_MIN_ROWS + 10, start_price=5000.0)
    # El subyacente arranca MUCHO despues que la ventana del lookback.
    _seed_underlying(temp_db, "AAPL", lookback["timestamps"][-(UNDERLYING_MODEL_MIN_ROWS + 5):], start_price=150.0)
    _seed_ccl(temp_db, lookback["timestamps"])

    model_df, reconversion_ref, model_currency = build_model_input(temp_db, lookback, "cedear", symbol="AAPL")

    assert model_currency == "USD"


def test_simbolo_no_mapeado_se_comporta_igual_que_antes(temp_db):
    """Regresion: un cedear SIN subyacente verificado (o accion, que nunca
    tiene subyacente directo) no cambia de comportamiento -- sigue yendo al
    camino sintetico ARS/CCL de siempre, pasar symbol=None o un symbol no
    mapeado da exactamente el mismo resultado."""
    lookback = _lookback_df(n=50, start_price=5000.0)
    _seed_ccl(temp_db, lookback["timestamps"])

    sin_symbol = build_model_input(temp_db, lookback, "cedear")
    no_mapeado = build_model_input(temp_db, lookback, "cedear", symbol="IBM")

    assert sin_symbol[2] == "USD"
    assert no_mapeado[2] == "USD"
    pd.testing.assert_frame_equal(sin_symbol[0], no_mapeado[0])
    assert sin_symbol[1] == no_mapeado[1]


def test_accion_no_usa_subyacente_aunque_el_symbol_este_mapeado(temp_db):
    """CEDEAR_DIRECT_UNDERLYING_SYMBOLS solo aplica a asset_type == 'cedear'
    -- una 'accion' con el mismo ticker (coincidencia de simbolo entre
    mercados) nunca debe tomar este camino."""
    lookback = _lookback_df(n=UNDERLYING_MODEL_MIN_ROWS + 10, start_price=5000.0)
    _seed_underlying(temp_db, "AAPL", lookback["timestamps"], start_price=150.0)
    _seed_ccl(temp_db, lookback["timestamps"])

    _, _, model_currency = build_model_input(temp_db, lookback, "accion", symbol="AAPL")

    assert model_currency == "USD"


def _seed_ccl(conn, dates, rate=1000.0):
    rows = [{"fecha": d.strftime("%Y-%m-%d"), "compra": rate, "venta": rate} for d in dates]
    db.upsert_fx_rates(conn, fetchers.CCL_CASA, rows)
