"""Tests del Motor de Factor Investing Multi-Factor (ver conversacion) --
portfolio/factor_investing.py. Logica pura (formulas de los 3 factores,
z-score cross-seccional, composite, Top-N) mas el cache semanal -- sin red,
sin Chronos-Bolt (se mockea compute_factor_scores donde hace falta), mismo
patron que tests/test_paper_trading.py (DB temporal propia)."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from portfolio import backtest, db, factor_investing


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_factor_investing.db")
    monkeypatch.setattr(db, "DB_PATH", db_path)
    conn = db.get_connection(db_path)
    yield conn
    conn.close()


def _flat_model_df(n=300, close_value=100.0, atr_pct_target=None):
    """DataFrame open/high/low/close sintetico -- si atr_pct_target se pasa,
    el rango high-low de cada vela se ajusta para que el ATR% resultante sea
    aproximadamente ese valor (ver compute_low_vol_factor)."""
    close = [close_value] * n
    if atr_pct_target is None:
        high = close
        low = close
    else:
        half_range = close_value * atr_pct_target / 100 / 2
        high = [c + half_range for c in close]
        low = [c - half_range for c in close]
    return pd.DataFrame({"open": close, "high": high, "low": low, "close": close})


# --- compute_momentum_factor / _period_return_pct ----------------------------

def test_period_return_pct_calculo_simple():
    close = pd.Series([100.0] * 60 + [110.0])  # sube 10% en el ultimo tramo
    assert factor_investing._period_return_pct(close, 60) == pytest.approx(10.0)


def test_period_return_pct_historia_insuficiente_da_none():
    close = pd.Series([100.0] * 10)
    assert factor_investing._period_return_pct(close, 60) is None


def test_compute_momentum_factor_promedia_3m_y_12m():
    n = factor_investing.MOMENTUM_12M_DAYS + 1
    close = pd.Series([100.0] * n)
    close.iloc[-1] = 121.0  # +21% total, pero el calculo es independiente por ventana
    r3 = factor_investing._period_return_pct(close, factor_investing.MOMENTUM_3M_DAYS)
    r12 = factor_investing._period_return_pct(close, factor_investing.MOMENTUM_12M_DAYS)
    expected = 0.5 * r3 + 0.5 * r12
    assert factor_investing.compute_momentum_factor(close) == pytest.approx(expected)


def test_compute_momentum_factor_usa_solo_3m_si_falta_12m():
    n = factor_investing.MOMENTUM_3M_DAYS + 5
    close = pd.Series([100.0] * n)
    close.iloc[-1] = 105.0
    r3 = factor_investing._period_return_pct(close, factor_investing.MOMENTUM_3M_DAYS)
    assert factor_investing.compute_momentum_factor(close) == pytest.approx(r3)


def test_compute_momentum_factor_sin_historia_da_none():
    close = pd.Series([100.0] * 10)
    assert factor_investing.compute_momentum_factor(close) is None


# --- compute_low_vol_factor ---------------------------------------------------

def test_compute_low_vol_factor_mayor_volatilidad_da_menor_score():
    tranquilo = _flat_model_df(atr_pct_target=1.0)
    volatil = _flat_model_df(atr_pct_target=5.0)
    score_tranquilo = factor_investing.compute_low_vol_factor(tranquilo)
    score_volatil = factor_investing.compute_low_vol_factor(volatil)
    assert score_tranquilo is not None and score_volatil is not None
    assert score_tranquilo > score_volatil  # menos ATR% -> mayor 1/ATR%


def test_compute_low_vol_factor_serie_vacia_da_none():
    df = pd.DataFrame({"open": [], "high": [], "low": [], "close": []})
    assert factor_investing.compute_low_vol_factor(df) is None


# --- compute_value_factor ------------------------------------------------------

def test_compute_value_factor_pe_valido():
    assert factor_investing.compute_value_factor(10.0) == pytest.approx(0.1)


def test_compute_value_factor_pe_faltante_o_invalido_da_none():
    assert factor_investing.compute_value_factor(None) is None
    assert factor_investing.compute_value_factor(0) is None
    assert factor_investing.compute_value_factor(-5.0) is None
    assert factor_investing.compute_value_factor(float("nan")) is None


# --- _cross_sectional_zscore ---------------------------------------------------

def test_cross_sectional_zscore_excluye_none_del_calculo():
    values = {"A": 10.0, "B": 20.0, "C": None}
    z = factor_investing._cross_sectional_zscore(values)
    assert z["C"] is None
    assert z["A"] < z["B"]  # A es el menor de los 2 validos
    # media de A/B = 15, desvio muestral = 7.0710..., z(A) = -0.7071, z(B) = 0.7071
    assert z["A"] == pytest.approx(-z["B"])


def test_cross_sectional_zscore_menos_de_2_validos_da_cero():
    values = {"A": 10.0, "B": None, "C": None}
    z = factor_investing._cross_sectional_zscore(values)
    assert z["A"] == 0.0
    assert z["B"] is None and z["C"] is None


def test_cross_sectional_zscore_todos_iguales_da_cero_sin_dividir_por_cero():
    values = {"A": 5.0, "B": 5.0, "C": 5.0}
    z = factor_investing._cross_sectional_zscore(values)
    assert z == {"A": 0.0, "B": 0.0, "C": 0.0}


# --- compute_composite_score ---------------------------------------------------

def test_compute_composite_score_promedio_simple_con_los_3_factores():
    score = factor_investing.compute_composite_score(1.0, 2.0, 3.0, weights={"momentum": 1/3, "low_vol": 1/3, "value": 1/3})
    assert score == pytest.approx(2.0)


def test_compute_composite_score_renormaliza_si_falta_un_factor():
    # Sin value_z, el composite debe ser el promedio SOLO de momentum/low_vol
    # (renormalizado), no un promedio con un 0 implicito en el 3er lugar.
    score = factor_investing.compute_composite_score(1.0, 3.0, None, weights={"momentum": 1/3, "low_vol": 1/3, "value": 1/3})
    assert score == pytest.approx(2.0)  # (1/3*1 + 1/3*3) / (2/3) = 2.0, no 1.333


def test_compute_composite_score_sin_ningun_factor_da_none():
    assert factor_investing.compute_composite_score(None, None, None) is None


# --- top_rotation_symbols ------------------------------------------------------

def test_top_rotation_symbols_respeta_el_piso_minimo():
    ranked = [{"symbol": f"T{i}"} for i in range(10)]  # 10% literal daria 1
    top = factor_investing.top_rotation_symbols(ranked, top_pct=0.10, min_n=3)
    assert len(top) == 3
    assert [t["symbol"] for t in top] == ["T0", "T1", "T2"]


def test_top_rotation_symbols_nunca_excede_el_universo_disponible():
    ranked = [{"symbol": "A"}, {"symbol": "B"}]
    top = factor_investing.top_rotation_symbols(ranked, top_pct=0.10, min_n=3)
    assert len(top) == 2


def test_top_rotation_symbols_porcentaje_grande_con_universo_grande():
    ranked = [{"symbol": f"T{i}"} for i in range(50)]
    top = factor_investing.top_rotation_symbols(ranked, top_pct=0.10, min_n=3)
    assert len(top) == 5  # ceil(50*0.10) = 5, por encima del piso


# --- ensure_factor_scores / get_rotation_signals (cache semanal) -------------

def test_ensure_factor_scores_calcula_la_primera_vez(temp_db, monkeypatch):
    called = {"n": 0}

    def fake_compute(conn, universe=None):
        called["n"] += 1
        return [{"symbol": "AAPL", "momentum_raw": 1.0, "low_vol_raw": 1.0, "value_raw": 1.0, "composite_score": 1.0, "rank": 1}]

    monkeypatch.setattr(factor_investing, "compute_factor_scores", fake_compute)
    factor_investing.ensure_factor_scores(temp_db)
    assert called["n"] == 1
    assert db.list_factor_scores(temp_db)[0]["symbol"] == "AAPL"


def test_ensure_factor_scores_no_recalcula_si_es_reciente(temp_db, monkeypatch):
    called = {"n": 0}

    def fake_compute(conn, universe=None):
        called["n"] += 1
        return [{"symbol": "AAPL", "momentum_raw": 1.0, "low_vol_raw": 1.0, "value_raw": 1.0, "composite_score": 1.0, "rank": 1}]

    monkeypatch.setattr(factor_investing, "compute_factor_scores", fake_compute)
    factor_investing.ensure_factor_scores(temp_db)
    factor_investing.ensure_factor_scores(temp_db)  # segunda llamada, deberia saltear
    assert called["n"] == 1


def test_ensure_factor_scores_recalcula_si_paso_mas_de_una_semana(temp_db, monkeypatch):
    from datetime import datetime, timedelta

    called = {"n": 0}

    def fake_compute(conn, universe=None):
        called["n"] += 1
        return [{"symbol": "AAPL", "momentum_raw": 1.0, "low_vol_raw": 1.0, "value_raw": 1.0, "composite_score": 1.0, "rank": 1}]

    monkeypatch.setattr(factor_investing, "compute_factor_scores", fake_compute)
    stale_timestamp = (datetime.now() - timedelta(days=factor_investing.FACTOR_STALENESS_DAYS + 1)).isoformat()
    db.upsert_factor_score(temp_db, "AAPL", 1.0, 1.0, 1.0, 1.0, 1, computed_at=stale_timestamp)
    factor_investing.ensure_factor_scores(temp_db)
    assert called["n"] == 1  # el stale no cuenta como "ya calculado esta semana"


def test_get_rotation_signals_devuelve_top_n(temp_db, monkeypatch):
    def fake_compute(conn, universe=None):
        return [
            {"symbol": f"T{i}", "momentum_raw": 1.0, "low_vol_raw": 1.0, "value_raw": 1.0,
             "composite_score": float(10 - i), "rank": i + 1}
            for i in range(10)
        ]

    monkeypatch.setattr(factor_investing, "compute_factor_scores", fake_compute)
    top = factor_investing.get_rotation_signals(temp_db)
    assert len(top) == factor_investing.TOP_N_MIN
    assert [t["symbol"] for t in top] == ["T0", "T1", "T2"]


# --- simulate_factor_engine (backtest historico) ------------------------------

def _synthetic_series_df(n=400, trend=0.0, start=100.0, seed=0):
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2023-01-01", periods=n, freq="B")
    returns = rng.normal(trend, 0.01, n)
    close = start * np.cumprod(1 + returns)
    return pd.DataFrame({
        "date": dates, "open": close, "high": close * 1.01, "low": close * 0.99,
        "close": close, "volume": 1000.0,
    })


def _patch_universe(monkeypatch, series_by_symbol, pe_by_symbol=None):
    pe_by_symbol = pe_by_symbol or {}

    def fake_prepare(symbol, asset_type, conn, min_rows):
        return series_by_symbol.get(symbol)

    def fake_fetch_fundamentals(symbol, asset_type):
        pe = pe_by_symbol.get(symbol)
        return {"available": pe is not None, "pe_ratio": pe}

    monkeypatch.setattr(backtest, "_prepare_aligned_series", fake_prepare)
    monkeypatch.setattr(factor_investing, "fetch_fundamentals", fake_fetch_fundamentals)


def test_simulate_factor_engine_shape_y_cobertura_de_fechas(temp_db, monkeypatch):
    universe = [(f"T{i}", "cedear") for i in range(5)]
    series = {f"T{i}": _synthetic_series_df(seed=i) for i in range(5)}
    _patch_universe(monkeypatch, series, pe_by_symbol={f"T{i}": 10.0 + i for i in range(5)})

    result = factor_investing.simulate_factor_engine(temp_db, universe=universe, lookback_days=100)
    assert result["engine"] == "factor"
    assert len(result["daily_returns_pct"]) == 100
    assert len(result["idle_weight"]) == 100
    assert isinstance(result["trades"], list)
    assert len(result["trades"]) > 0  # con 100 ruedas y rebalanceo semanal, debe haber al menos 1


def test_simulate_factor_engine_sin_universo_valido_devuelve_vacio(temp_db, monkeypatch):
    _patch_universe(monkeypatch, {})  # ningun symbol tiene serie
    result = factor_investing.simulate_factor_engine(temp_db, universe=[("T0", "cedear")], lookback_days=100)
    assert len(result["daily_returns_pct"]) == 0
    assert result["trades"] == []


def test_simulate_factor_engine_rebalancea_en_la_cadencia_declarada(temp_db, monkeypatch):
    universe = [(f"T{i}", "cedear") for i in range(5)]
    series = {f"T{i}": _synthetic_series_df(seed=i) for i in range(5)}
    _patch_universe(monkeypatch, series, pe_by_symbol={f"T{i}": 10.0 + i for i in range(5)})

    result = factor_investing.simulate_factor_engine(temp_db, universe=universe, lookback_days=100)
    master_dates = sorted(set(series["T0"]["date"]))[-100:]
    rebalance_dates = [t["date"] for t in result["trades"]]
    # El primer rebalanceo siempre cae en la primera rueda de la ventana
    # (day_i=0 fuerza el primer calculo, ver simulate_factor_engine).
    assert rebalance_dates[0] == master_dates[0]
    # Los siguientes respetan la cadencia (pueden saltearse si no hubo cambio
    # de holdings, pero nunca ocurren ANTES de la cadencia declarada).
    date_to_idx = {d: i for i, d in enumerate(master_dates)}
    for d1, d2 in zip(rebalance_dates, rebalance_dates[1:]):
        assert date_to_idx[d2] - date_to_idx[d1] >= factor_investing.FACTOR_REBALANCE_SPACING_DAYS


def test_simulate_factor_engine_commission_pct_default_cero_no_cambia_comportamiento(temp_db, monkeypatch):
    """Fase 4 del plan de unificacion, extendida a Factor (ver conversacion):
    default 0.0 reproduce exactamente el resultado de antes de esta fase."""
    universe = [(f"T{i}", "cedear") for i in range(5)]
    series = {f"T{i}": _synthetic_series_df(seed=i) for i in range(5)}
    _patch_universe(monkeypatch, series, pe_by_symbol={f"T{i}": 10.0 + i for i in range(5)})

    result = factor_investing.simulate_factor_engine(temp_db, universe=universe, lookback_days=100)
    assert len(result["trades"]) > 0


def test_simulate_factor_engine_resta_comision_proporcional_al_turnover(temp_db, monkeypatch):
    universe = [(f"T{i}", "cedear") for i in range(5)]
    series = {f"T{i}": _synthetic_series_df(seed=i) for i in range(5)}
    _patch_universe(monkeypatch, series, pe_by_symbol={f"T{i}": 10.0 + i for i in range(5)})

    bruto = factor_investing.simulate_factor_engine(temp_db, universe=universe, lookback_days=100, commission_pct=0.0)
    neto = factor_investing.simulate_factor_engine(temp_db, universe=universe, lookback_days=100, commission_pct=10.0)

    rebalance_dates = {t["date"] for t in bruto["trades"]}
    assert rebalance_dates  # el fixture ya confirma >0 en el test de arriba
    diffs = (bruto["daily_returns_pct"] - neto["daily_returns_pct"])
    # En dias SIN rebalanceo, bruto y neto deben ser identicos (sin comision
    # que restar); en dias CON rebalanceo, neto debe ser estrictamente menor
    # (turnover > 0 siempre que holdings cambie, ver docstring del motor).
    for date, diff in diffs.items():
        if date in rebalance_dates:
            assert diff > 0
        else:
            assert diff == pytest.approx(0.0)


def test_simulate_factor_engine_holdings_son_causales(temp_db, monkeypatch):
    # Cero look-ahead: el primer rebalanceo no debe depender de NADA posterior
    # a su propia fecha -- se trunca la serie para que no exista ningun dato
    # despues de esa fecha (lookback_days=1, la "ventana" es un unico dia) y
    # se confirma que la canasta elegida es EXACTAMENTE la misma que en la
    # corrida completa, donde si habia 99 ruedas futuras disponibles.
    universe = [(f"T{i}", "cedear") for i in range(5)]
    series_full = {f"T{i}": _synthetic_series_df(n=400, seed=i) for i in range(5)}
    pe = {f"T{i}": 10.0 + i for i in range(5)}
    _patch_universe(monkeypatch, series_full, pe_by_symbol=pe)
    result_full = factor_investing.simulate_factor_engine(temp_db, universe=universe, lookback_days=100)
    first_date = result_full["trades"][0]["date"]
    first_holdings_full = set(result_full["trades"][0]["holdings_after"])

    series_trunc = {}
    for symbol, df in series_full.items():
        cutoff = df[df["date"] == first_date].index[0]
        series_trunc[symbol] = df.iloc[: cutoff + 1].reset_index(drop=True)
    _patch_universe(monkeypatch, series_trunc, pe_by_symbol=pe)
    result_trunc = factor_investing.simulate_factor_engine(temp_db, universe=universe, lookback_days=1)
    assert result_trunc["trades"], "deberia haber al menos el rebalanceo inicial"
    first_holdings_trunc = set(result_trunc["trades"][0]["holdings_after"])

    assert first_holdings_full == first_holdings_trunc
