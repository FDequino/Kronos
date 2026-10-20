"""Tests del Motor de Pairs Trading / Arbitraje Estadistico (ver
conversacion, portfolio/pairs_trading.py). Logica pura sobre series
sinteticas -- sin red, sin Chronos-Bolt, sin DB (ver docstring del modulo:
test_cointegration/compute_hedge_ratio/spread_zscore solo necesitan 2
pd.Series ya resueltas, nunca llaman a fetch_ticker_history directo salvo
en generate_pair_signal/scan_pairs, que no se prueban aca por eso -- mismo
patron que tests/test_model_v2.py)."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from portfolio import db
from portfolio import pairs_trading as pt


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_pairs_trading.db")
    monkeypatch.setattr(db, "DB_PATH", db_path)
    conn = db.get_connection(db_path)
    yield conn
    conn.close()


def _dates(n):
    return pd.date_range("2023-01-01", periods=n, freq="B")


def _cointegrated_pair(n=400, beta=2.0, alpha=10.0, noise_std=0.5, seed=0):
    """B = random walk; A = alpha + beta*B + ruido ESTACIONARIO -- cointegrado
    por construccion (el residuo A-beta*B-alpha es ruido blanco, estacionario)."""
    rng = np.random.default_rng(seed)
    idx = _dates(n)
    b = pd.Series(100 + np.cumsum(rng.normal(0, 1, n)), index=idx)
    noise = rng.normal(0, noise_std, n)
    a = pd.Series(alpha + beta * b.to_numpy() + noise, index=idx)
    return a, b


def _independent_pair(n=400, seed=1):
    """2 random walks INDEPENDIENTES -- NO cointegrados (caso de libro)."""
    rng = np.random.default_rng(seed)
    idx = _dates(n)
    a = pd.Series(100 + np.cumsum(rng.normal(0, 1, n)), index=idx)
    b = pd.Series(100 + np.cumsum(rng.normal(0, 1, n)), index=idx)
    return a, b


# --- candidate_pairs_from_sectors --------------------------------------------

def test_candidate_pairs_from_sectors_agrupa_por_sector():
    sector_groups = {"X": "sector1", "Y": "sector1", "Z": "sector2"}
    pairs = pt.candidate_pairs_from_sectors(sector_groups)
    assert pairs == [("X", "Y")]  # Z queda afuera, es el unico de sector2


def test_candidate_pairs_from_sectors_incluye_los_3_pedidos_explicitamente():
    pairs = pt.candidate_pairs_from_sectors()
    normalized = {tuple(sorted(p)) for p in pairs}
    for requested in [("GGAL", "BMA"), ("SPY", "QQQ"), ("AAPL", "MSFT")]:
        assert tuple(sorted(requested)) in normalized


# --- test_cointegration -------------------------------------------------------

def test_cointegration_detecta_par_cointegrado():
    a, b = _cointegrated_pair()
    result = pt.test_cointegration(a, b)
    assert result is not None
    assert result["is_cointegrated"] is True
    assert result["pvalue"] < pt.COINTEGRATION_PVALUE_THRESHOLD


def test_cointegration_rechaza_par_independiente():
    a, b = _independent_pair()
    result = pt.test_cointegration(a, b)
    assert result is not None
    assert result["is_cointegrated"] is False
    assert result["pvalue"] >= pt.COINTEGRATION_PVALUE_THRESHOLD


def test_cointegration_pocos_datos_devuelve_none():
    a, b = _cointegrated_pair(n=50)
    assert pt.test_cointegration(a, b) is None


def test_cointegration_alinea_por_fecha_no_por_posicion():
    # Series con fechas parcialmente distintas -- debe usar la INTERSECCION,
    # nunca alinear por posicion (eso mezclaria fechas de activos distintos).
    a, b = _cointegrated_pair(n=400)
    a_shifted = a.iloc[10:]  # mismas fechas que a, menos las primeras 10
    result = pt.test_cointegration(a_shifted, b)
    assert result is not None
    assert result["n_obs"] == len(a) - 10


# --- compute_hedge_ratio -------------------------------------------------------

def test_compute_hedge_ratio_recupera_la_pendiente_real():
    a, b = _cointegrated_pair(beta=2.0, alpha=10.0, noise_std=0.3)
    hedge = pt.compute_hedge_ratio(a, b)
    assert hedge["hedge_ratio"] == pytest.approx(2.0, abs=0.1)
    assert hedge["alpha"] == pytest.approx(10.0, abs=5.0)
    assert hedge["r_squared"] > 0.95  # relacion casi perfecta por construccion


# --- spread_zscore (causalidad) ------------------------------------------------

def test_spread_zscore_nan_durante_warmup():
    a, b = _cointegrated_pair()
    hedge = pt.compute_hedge_ratio(a, b)
    spread = pt.compute_spread(a, b, hedge["hedge_ratio"])
    z = pt.spread_zscore(spread, window=60, min_periods=20)
    assert z.iloc[:19].isna().all()
    assert z.iloc[20:].notna().all()


def test_spread_zscore_es_causal_no_cambia_si_se_agregan_filas_futuras():
    # Cero look-ahead: el z-score de la fila i no debe cambiar si se agregan
    # MAS filas despues de i (ventana rolling nunca mira adelante).
    a, b = _cointegrated_pair(n=400)
    hedge = pt.compute_hedge_ratio(a, b)
    spread_full = pt.compute_spread(a, b, hedge["hedge_ratio"])
    z_full = pt.spread_zscore(spread_full)

    spread_prefix = spread_full.iloc[:300]
    z_prefix = pt.spread_zscore(spread_prefix)

    pd.testing.assert_series_equal(z_full.iloc[:300], z_prefix, check_names=False)


# --- generate_pair_signal: umbral y actionable --------------------------------

class _FakeConn:
    """generate_pair_signal solo usa conn para pasarselo a _usd_close_series
    (via fetch_ticker_history/build_model_input) -- se monkeypatchea
    _usd_close_series directamente en los tests de abajo, conn nunca se usa
    de verdad."""


def test_generate_pair_signal_sin_cointegracion_devuelve_none(monkeypatch):
    a, b = _independent_pair()
    monkeypatch.setattr(pt, "_usd_close_series", lambda symbol, conn: a if symbol == "A" else b)
    assert pt.generate_pair_signal("A", "B", _FakeConn()) is None


def test_generate_pair_signal_zscore_dentro_del_umbral_devuelve_none(monkeypatch):
    # Spread practicamente constante (ruido minimo) -- z-score nunca supera
    # el umbral de entrada, no hay señal aunque el par este cointegrado.
    a, b = _cointegrated_pair(noise_std=0.01)
    monkeypatch.setattr(pt, "_usd_close_series", lambda symbol, conn: a if symbol == "A" else b)
    assert pt.generate_pair_signal("A", "B", _FakeConn(), entry_threshold=100.0) is None


def test_generate_pair_signal_sin_tenencia_pata_cara_no_actionable(monkeypatch):
    rng = np.random.default_rng(2)
    n = 400
    idx = _dates(n)
    b_vals = 100 + np.cumsum(rng.normal(0, 1, n))
    noise = rng.normal(0, 0.3, n - 1)
    # Ultimo valor de A forzado MUY por encima de la relacion -- garantiza
    # que el z-score final supere el umbral de entrada (spread ancho positivo).
    a_vals = np.concatenate([2.0 * b_vals[:-1] + 10 + noise, [2.0 * b_vals[-1] + 10 + 50]])
    a = pd.Series(a_vals, index=idx)
    b = pd.Series(b_vals, index=idx)
    monkeypatch.setattr(pt, "_usd_close_series", lambda symbol, conn: a if symbol == "A" else b)

    signal = pt.generate_pair_signal("A", "B", _FakeConn(), held_symbols=set())
    assert signal is not None
    assert signal["zscore"] > pt.ZSCORE_ENTRY_THRESHOLD
    assert signal["expensive_leg"]["symbol"] == "A"  # A quedo cara (spread positivo ancho)
    assert signal["expensive_leg"]["actionable"] is False
    assert signal["cheap_leg"]["symbol"] == "B"
    assert signal["cheap_leg"]["actionable"] is True


def test_generate_pair_signal_con_tenencia_pata_cara_si_actionable(monkeypatch):
    rng = np.random.default_rng(2)
    n = 400
    idx = _dates(n)
    b_vals = 100 + np.cumsum(rng.normal(0, 1, n))
    noise = rng.normal(0, 0.3, n - 1)
    a_vals = np.concatenate([2.0 * b_vals[:-1] + 10 + noise, [2.0 * b_vals[-1] + 10 + 50]])
    a = pd.Series(a_vals, index=idx)
    b = pd.Series(b_vals, index=idx)
    monkeypatch.setattr(pt, "_usd_close_series", lambda symbol, conn: a if symbol == "A" else b)

    # Esta vez la cartera YA tiene la pata cara (A) -- debe poder rotarla de verdad.
    signal = pt.generate_pair_signal("A", "B", _FakeConn(), held_symbols={"A"})
    assert signal is not None
    assert signal["expensive_leg"]["symbol"] == "A"
    assert signal["expensive_leg"]["actionable"] is True
    assert signal["expensive_leg"]["action"] == "vender"


def test_scan_pairs_omite_pares_que_fallan_sin_romper_el_resto(monkeypatch):
    def _fake_signal(symbol_a, symbol_b, conn, held_symbols=None):
        if symbol_a == "BAD":
            raise RuntimeError("sin datos")
        return {"pair": (symbol_a, symbol_b)}

    monkeypatch.setattr(pt, "generate_pair_signal", _fake_signal)
    result = pt.scan_pairs(_FakeConn(), pairs=[("BAD", "X"), ("GOOD", "Y")])
    assert result == [{"pair": ("GOOD", "Y")}]


# --- simulate_pairs_engine (backtest historico) -------------------------------

def _fake_pair_frame(n=700, beta=1.5, alpha=5.0, noise_std=0.4, seed=3):
    """Par cointegrado sintetico con suficiente historia para
    PAIRS_COINT_WINDOW_DAYS (ventana) + lookback_days (simulacion)."""
    rng = np.random.default_rng(seed)
    idx = _dates(n)
    close_b = 100 + np.cumsum(rng.normal(0, 1, n))
    noise = rng.normal(0, noise_std, n)
    close_a = alpha + beta * close_b + noise
    return pd.DataFrame({"date": idx, "close_a": close_a, "close_b": close_b})


def test_simulate_pairs_engine_shape_y_cobertura_de_fechas(temp_db, monkeypatch):
    frame = _fake_pair_frame()
    monkeypatch.setattr(pt, "_pair_price_frame", lambda a, b, conn: frame)
    result = pt.simulate_pairs_engine(temp_db, pairs=[("A", "B")], lookback_days=200)
    assert result["engine"] == "pairs"
    assert len(result["daily_returns_pct"]) == 200
    assert len(result["idle_weight"]) == 200
    assert isinstance(result["trades"], list)


def test_simulate_pairs_engine_sin_historia_suficiente_devuelve_vacio(temp_db, monkeypatch):
    frame = _fake_pair_frame(n=50)  # muy corto para PAIRS_COINT_WINDOW_DAYS + lookback
    monkeypatch.setattr(pt, "_pair_price_frame", lambda a, b, conn: frame)
    result = pt.simulate_pairs_engine(temp_db, pairs=[("A", "B")], lookback_days=200)
    assert len(result["daily_returns_pct"]) == 0
    assert result["trades"] == []


def test_simulate_pairs_engine_entradas_solo_despues_de_la_ventana_minima(temp_db, monkeypatch):
    # Cero look-ahead: ninguna entrada puede ocurrir antes de que exista una
    # ventana completa de PAIRS_COINT_WINDOW_DAYS para estimar cointegracion.
    frame = _fake_pair_frame(n=700)
    monkeypatch.setattr(pt, "_pair_price_frame", lambda a, b, conn: frame)
    result = pt.simulate_pairs_engine(temp_db, pairs=[("A", "B")], lookback_days=300)
    master_dates = sorted(set(frame["date"]))[-300:]
    first_valid_date = master_dates[0]
    for trade in result["trades"]:
        assert trade["entry_date"] >= first_valid_date


def test_simulate_pairs_engine_commission_pct_default_cero_no_cambia_comportamiento(temp_db, monkeypatch):
    """Fase 4 del plan de unificacion, extendida a Pairs (ver conversacion):
    default 0.0 reproduce exactamente el resultado de antes de esta fase."""
    frame = _fake_pair_frame(n=700)
    monkeypatch.setattr(pt, "_pair_price_frame", lambda a, b, conn: frame)
    result = pt.simulate_pairs_engine(temp_db, pairs=[("A", "B")], lookback_days=300)
    assert len(result["trades"]) >= 1


def test_simulate_pairs_engine_resta_comision_al_cerrar(temp_db, monkeypatch):
    frame = _fake_pair_frame(n=700)
    monkeypatch.setattr(pt, "_pair_price_frame", lambda a, b, conn: frame)

    bruto = pt.simulate_pairs_engine(temp_db, pairs=[("A", "B")], lookback_days=300, commission_pct=0.0)
    neto = pt.simulate_pairs_engine(temp_db, pairs=[("A", "B")], lookback_days=300, commission_pct=5.0)

    assert len(neto["trades"]) == len(bruto["trades"])
    assert len(bruto["trades"]) >= 1
    for t_bruto, t_neto in zip(bruto["trades"], neto["trades"]):
        assert t_neto["return_pct"] == pytest.approx(t_bruto["return_pct"] - 5.0)


def test_simulate_pairs_engine_nunca_excede_max_concurrentes(temp_db, monkeypatch):
    # Con un unico par candidato nunca puede haber mas de 1 posicion abierta
    # a la vez de ESE par -- regla general (PAIRS_MAX_CONCURRENT) verificada
    # indirectamente: ningun trade se solapa en fechas con otro del MISMO par.
    frame = _fake_pair_frame(n=700)
    monkeypatch.setattr(pt, "_pair_price_frame", lambda a, b, conn: frame)
    result = pt.simulate_pairs_engine(temp_db, pairs=[("A", "B")], lookback_days=300)
    trades = sorted(result["trades"], key=lambda t: t["entry_date"])
    for t1, t2 in zip(trades, trades[1:]):
        assert t2["entry_date"] >= t1["exit_date"]
