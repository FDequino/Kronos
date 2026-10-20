"""Tests del Arbitraje de Canje MEP/CCL (ver conversacion, portfolio/arbitrage.py).
Logica pura sobre series sinteticas de precios de bonos -- sin red real
(fetch_data912_historical se monkeypatchea, mismo patron que
tests/test_pairs_trading.py)."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from portfolio import arbitrage


def _dates(n):
    return pd.date_range("2026-01-01", periods=n, freq="B")


def _bond_df(dates, closes):
    """DataFrame minimo con las 2 columnas que arbitrage.py realmente lee
    (timestamps, close) -- mismo shape relevante que
    fetchers.fetch_data912_historical, sin las columnas que este modulo no usa."""
    return pd.DataFrame({"timestamps": dates, "close": closes})


def _mock_fetch(monkeypatch, al30_ars, al30_usd, gd30_ars, gd30_usd, dates=None, missing=None):
    """Parchea fetch_data912_historical (importado directo en arbitrage.py)
    para devolver las 4 series sinteticas por simbolo. `missing` es un set de
    simbolos que deben levantar excepcion (pata sin datos)."""
    missing = missing or set()
    n = len(al30_ars)
    dates = dates if dates is not None else _dates(n)
    series_by_symbol = {
        "AL30": al30_ars, "AL30D": al30_usd, "GD30": gd30_ars, "GD30D": gd30_usd,
    }

    def fake_fetch(symbol, asset_type):
        assert asset_type == "bono"
        if symbol in missing:
            raise ValueError(f"{symbol} no encontrado")
        return _bond_df(dates, series_by_symbol[symbol])

    monkeypatch.setattr(arbitrage, "fetch_data912_historical", fake_fetch)


# --- caso normal: brecha chica, sin señal --------------------------------------

def test_brecha_series_normal_case_sin_senal(monkeypatch):
    n = 25
    rng = np.random.default_rng(42)
    al30_ars = np.full(n, 1000.0)
    al30_usd = np.full(n, 1.0)
    gd30_ars = np.full(n, 1000.0)
    # ruido chico simetrico -- brecha oscila cerca de 0, sin desvio fuerte
    gd30_usd = 1.0 + rng.normal(0, 0.0005, n)
    _mock_fetch(monkeypatch, al30_ars, al30_usd, gd30_ars, gd30_usd)

    df = arbitrage.compute_canje_brecha_series()
    assert df is not None
    assert len(df) == n
    assert arbitrage.compute_canje_arbitrage_signal() is None


# --- caso con señal: brecha se dispara el ultimo dia ---------------------------

def test_signal_fires_cuando_brecha_se_dispara_al30_barato(monkeypatch):
    n = 25
    al30_ars = np.full(n, 1000.0)
    al30_usd = np.full(n, 1.0)  # implied_fx_al30 = 1000 siempre
    gd30_ars = np.full(n, 1000.0)
    gd30_usd = np.full(n, 1.0)
    # ruido chico los primeros n-1 dias para tener desvio > 0 (sin eso el
    # z-score es NaN por division por cero), y un salto grande el ultimo dia
    rng = np.random.default_rng(7)
    gd30_usd[:-1] += rng.normal(0, 0.0003, n - 1)
    gd30_usd[-1] = 0.90  # implied_fx_gd30 sube fuerte (1000/0.90 ~ 1111) => brecha positiva grande
    _mock_fetch(monkeypatch, al30_ars, al30_usd, gd30_ars, gd30_usd)

    signal = arbitrage.compute_canje_arbitrage_signal()
    assert signal is not None
    assert signal["pair"] == ("AL30", "GD30")
    assert abs(signal["zscore"]) > arbitrage.CANJE_ZSCORE_ENTRY_THRESHOLD
    assert signal["brecha_pct"] > 0
    # brecha_pct > 0 => GD30 da el dolar mas caro => AL30 es la pata barata
    assert signal["cheap_leg"] == "AL30"
    assert signal["expensive_leg"] == "GD30"
    assert "AL30" in signal["note"] and "GD30" in signal["note"]


def test_signal_fires_cuando_brecha_se_dispara_gd30_barato(monkeypatch):
    n = 25
    al30_ars = np.full(n, 1000.0)
    al30_usd = np.full(n, 1.0)
    gd30_ars = np.full(n, 1000.0)
    gd30_usd = np.full(n, 1.0)
    rng = np.random.default_rng(11)
    gd30_usd[:-1] += rng.normal(0, 0.0003, n - 1)
    gd30_usd[-1] = 1.12  # implied_fx_gd30 baja (1000/1.12 ~ 893) => brecha negativa grande
    _mock_fetch(monkeypatch, al30_ars, al30_usd, gd30_ars, gd30_usd)

    signal = arbitrage.compute_canje_arbitrage_signal()
    assert signal is not None
    assert signal["brecha_pct"] < 0
    assert signal["cheap_leg"] == "GD30"
    assert signal["expensive_leg"] == "AL30"


# --- datos insuficientes: degrada con gracia, nunca rompe ----------------------

def test_falta_una_pata_degrada_con_gracia(monkeypatch):
    n = 25
    al30_ars = np.full(n, 1000.0)
    al30_usd = np.full(n, 1.0)
    gd30_ars = np.full(n, 1000.0)
    gd30_usd = np.full(n, 1.0)
    _mock_fetch(monkeypatch, al30_ars, al30_usd, gd30_ars, gd30_usd, missing={"GD30D"})

    assert arbitrage.compute_canje_brecha_series() is None
    assert arbitrage.compute_canje_arbitrage_signal() is None


def test_historial_insuficiente_degrada_con_gracia(monkeypatch):
    # menos filas que CANJE_ZSCORE_MIN_PERIODS + 1
    n = arbitrage.CANJE_ZSCORE_MIN_PERIODS
    al30_ars = np.full(n, 1000.0)
    al30_usd = np.full(n, 1.0)
    gd30_ars = np.full(n, 1000.0)
    gd30_usd = np.full(n, 1.0)
    _mock_fetch(monkeypatch, al30_ars, al30_usd, gd30_ars, gd30_usd)

    assert arbitrage.compute_canje_brecha_series() is None
    assert arbitrage.compute_canje_arbitrage_signal() is None


def test_fechas_no_alineadas_hace_inner_join(monkeypatch):
    """Si una pata tiene menos fechas en comun que las otras, solo las fechas
    presentes en las 4 series cuentan -- igual que un inner join real."""
    n = 35
    dates_full = _dates(n)
    al30_ars = np.full(n, 1000.0)
    al30_usd = np.full(n, 1.0)
    gd30_ars = np.full(n, 1000.0)
    gd30_usd = np.full(n, 1.0)
    rng = np.random.default_rng(3)
    gd30_usd += rng.normal(0, 0.0003, n)

    def fake_fetch(symbol, asset_type):
        if symbol == "GD30D":
            # a esta pata le faltan los primeros 5 dias
            return _bond_df(dates_full[5:], gd30_usd[5:])
        series_by_symbol = {"AL30": al30_ars, "AL30D": al30_usd, "GD30": gd30_ars}
        return _bond_df(dates_full, series_by_symbol[symbol])

    monkeypatch.setattr(arbitrage, "fetch_data912_historical", fake_fetch)

    df = arbitrage.compute_canje_brecha_series()
    assert df is not None
    assert len(df) == n - 5


# --- formula del z-score contra un calculo manual ------------------------------

def test_zscore_coincide_con_calculo_manual():
    window = arbitrage.CANJE_ZSCORE_WINDOW
    brecha = pd.Series([float(i) for i in range(1, window + 1)])  # 1..20
    z = arbitrage.features._rolling_zscore(
        brecha, window=window, min_periods=arbitrage.CANJE_ZSCORE_MIN_PERIODS,
    )
    expected_mean = brecha.mean()
    expected_std = brecha.std(ddof=1)  # mismo ddof que pandas .rolling().std()
    expected_z = (brecha.iloc[-1] - expected_mean) / expected_std
    assert z.iloc[-1] == pytest.approx(expected_z)


def test_zscore_nan_antes_de_min_periods():
    brecha = pd.Series([1.0, 2.0, 3.0])  # menos que CANJE_ZSCORE_MIN_PERIODS
    z = arbitrage.features._rolling_zscore(
        brecha, window=arbitrage.CANJE_ZSCORE_WINDOW, min_periods=arbitrage.CANJE_ZSCORE_MIN_PERIODS,
    )
    assert z.isna().all()


# --- simulate_canje_arbitrage_engine (backtest historico) ----------------------

def _spiked_gd30_usd_series(n=70, seed=7):
    """Serie sintetica con un salto sostenido (indices 25-29, implied_fx_gd30
    sube fuerte => brecha positiva grande) seguido de reversion a baseline --
    disenada para que la ventana de 20 dias eventualmente deje de contener el
    salto y el z-score vuelva a |z|<=0.5 (cierre por reversion, ver
    simulate_canje_arbitrage_engine), reproducible con el seed fijo."""
    rng = np.random.default_rng(seed)
    gd30_usd = 1.0 + rng.normal(0, 0.0003, n)
    gd30_usd[25:30] = 0.90
    gd30_usd[30:] = 1.0 + rng.normal(0, 0.0003, n - 30)
    return gd30_usd


def test_simulate_canje_arbitrage_engine_shape_basico(monkeypatch):
    n = 70
    al30_ars = np.full(n, 1000.0)
    al30_usd = np.full(n, 1.0)
    gd30_ars = np.full(n, 1000.0)
    gd30_usd = _spiked_gd30_usd_series(n)
    _mock_fetch(monkeypatch, al30_ars, al30_usd, gd30_ars, gd30_usd)

    result = arbitrage.simulate_canje_arbitrage_engine(conn=None, lookback_days=n)
    assert result["engine"] == "canje"
    assert len(result["daily_returns_pct"]) == n
    assert len(result["idle_weight"]) == n
    assert not result["daily_returns_pct"].isna().any()


def test_simulate_canje_arbitrage_engine_sin_datos_devuelve_vacio(monkeypatch):
    monkeypatch.setattr(arbitrage, "compute_canje_brecha_series", lambda: None)
    result = arbitrage.simulate_canje_arbitrage_engine(conn=None, lookback_days=70)
    assert result["engine"] == "canje"
    assert result["daily_returns_pct"].empty
    assert result["idle_weight"].empty
    assert result["trades"] == []


def test_simulate_canje_arbitrage_engine_abre_y_cierra_posicion_con_direccion_correcta(monkeypatch):
    n = 70
    al30_ars = np.full(n, 1000.0)
    al30_usd = np.full(n, 1.0)
    gd30_ars = np.full(n, 1000.0)
    gd30_usd = _spiked_gd30_usd_series(n)
    _mock_fetch(monkeypatch, al30_ars, al30_usd, gd30_ars, gd30_usd)

    result = arbitrage.simulate_canje_arbitrage_engine(conn=None, lookback_days=n)
    assert len(result["trades"]) >= 1
    first_trade = result["trades"][0]
    assert first_trade["pair"] == arbitrage.CANJE_PAIR
    # brecha_pct > 0 al abrir (GD30 caro) => se apuesta a que la brecha
    # se achique => direction = -1 (ver docstring del motor).
    assert first_trade["direction"] == -1.0
    assert first_trade["hold_days"] <= arbitrage.CANJE_MAX_HOLD_DAYS
    assert first_trade["exit_reason"] in ("reversion", "time_stop")
    # el salto fue grande (implied_fx_gd30 paso de ~1000 a ~1111) -- la
    # convergencia capturada debe ser una ganancia marcada, no un numero chico.
    assert first_trade["return_pct"] > 1.0


def test_simulate_canje_arbitrage_engine_commission_pct_default_cero_no_cambia_comportamiento(monkeypatch):
    """Fase 4 del plan de unificacion, extendida a Canje (ver conversacion):
    default 0.0 reproduce exactamente el resultado de antes de esta fase."""
    n = 70
    al30_ars, al30_usd, gd30_ars = np.full(n, 1000.0), np.full(n, 1.0), np.full(n, 1000.0)
    gd30_usd = _spiked_gd30_usd_series(n)
    _mock_fetch(monkeypatch, al30_ars, al30_usd, gd30_ars, gd30_usd)

    result = arbitrage.simulate_canje_arbitrage_engine(conn=None, lookback_days=n)
    assert result["trades"][0]["return_pct"] > 1.0  # mismo assert que el test de arriba


def test_simulate_canje_arbitrage_engine_resta_comision_al_cerrar(monkeypatch):
    n = 70
    al30_ars, al30_usd, gd30_ars = np.full(n, 1000.0), np.full(n, 1.0), np.full(n, 1000.0)
    gd30_usd = _spiked_gd30_usd_series(n)
    _mock_fetch(monkeypatch, al30_ars, al30_usd, gd30_ars, gd30_usd)

    bruto = arbitrage.simulate_canje_arbitrage_engine(conn=None, lookback_days=n, commission_pct=0.0)
    neto = arbitrage.simulate_canje_arbitrage_engine(conn=None, lookback_days=n, commission_pct=5.0)

    assert len(neto["trades"]) == len(bruto["trades"])
    for t_bruto, t_neto in zip(bruto["trades"], neto["trades"]):
        assert t_neto["return_pct"] == pytest.approx(t_bruto["return_pct"] - 5.0)


def test_simulate_canje_arbitrage_engine_sin_senal_nunca_abre_posicion(monkeypatch):
    # brecha constante en 0 (sin ruido) -- std=0, z-score NaN todo el tiempo,
    # nunca cruza el umbral (determinista, a diferencia de agregar ruido
    # aleatorio que por puro azar puede superar 2 sigma alguna vez en la
    # ventana de 20 dias in-sample).
    n = 40
    al30_ars = np.full(n, 1000.0)
    al30_usd = np.full(n, 1.0)
    gd30_ars = np.full(n, 1000.0)
    gd30_usd = np.full(n, 1.0)
    _mock_fetch(monkeypatch, al30_ars, al30_usd, gd30_ars, gd30_usd)

    result = arbitrage.simulate_canje_arbitrage_engine(conn=None, lookback_days=n)
    assert result["trades"] == []
    assert (result["idle_weight"] == 1.0).all()  # 100% del tiempo en Caucion, nunca abrio nada


def test_simulate_canje_arbitrage_engine_usa_caucion_configurada_para_dias_ociosos(monkeypatch):
    n = 40
    al30_ars = np.full(n, 1000.0)
    al30_usd = np.full(n, 1.0)
    gd30_ars = np.full(n, 1000.0)
    gd30_usd = np.full(n, 1.0)
    _mock_fetch(monkeypatch, al30_ars, al30_usd, gd30_ars, gd30_usd)

    class FakeConn:
        pass

    monkeypatch.setattr(arbitrage.db, "get_signal_config", lambda conn: {"caucion_reference_tna": 38.0})
    result = arbitrage.simulate_canje_arbitrage_engine(conn=FakeConn(), lookback_days=n)
    expected_daily = arbitrage.yield_engine.daily_caucion_return_pct(38.0)
    assert result["daily_returns_pct"].iloc[0] == pytest.approx(expected_daily)
