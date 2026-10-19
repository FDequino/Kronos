"""Tests del Modulo de Pruebas de Estres Sinteticas (ver conversacion,
portfolio/stress_test.py). Logica pura sobre series sinteticas construidas
a mano -- sin red, sin DB, sin Chronos-Bolt (mismo patron que
tests/test_pairs_trading.py/tests/test_model_v2.py): _asset_returns_matrix
(que si pega a data912/yfinance) no se prueba aca, solo las funciones de
shock/metricas que reciben una serie ya resuelta."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from portfolio import stress_test as st


# --- metricas de resiliencia --------------------------------------------------

def test_max_drawdown_pct_caso_conocido():
    cum = np.array([1.0, 1.1, 1.05, 0.9, 0.95, 1.2])
    # peor caida: desde el pico 1.1 hasta 0.9 -> (1.1-0.9)/1.1
    assert st._max_drawdown_pct(cum) == pytest.approx((1.1 - 0.9) / 1.1 * 100)


def test_max_drawdown_pct_serie_siempre_creciente_es_cero():
    cum = np.array([1.0, 1.05, 1.1, 1.2])
    assert st._max_drawdown_pct(cum) == pytest.approx(0.0)


def test_annualized_vol_pct_series_corta_devuelve_cero():
    assert st._annualized_vol_pct([0.01]) == 0.0


def test_annualized_vol_pct_escala_con_la_dispersion():
    low = st._annualized_vol_pct(np.array([0.001, -0.001, 0.001, -0.001] * 10))
    high = st._annualized_vol_pct(np.array([0.05, -0.05, 0.05, -0.05] * 10))
    assert high > low


def test_cvar_pct_pocos_datos_devuelve_none():
    assert st._cvar_pct([0.01, -0.02, 0.03]) is None


def test_cvar_pct_es_peor_que_el_percentil_puntual():
    rng = np.random.default_rng(0)
    returns = rng.normal(0, 0.02, 500)
    cvar = st._cvar_pct(returns, alpha=0.05)
    var_threshold = np.percentile(returns, 5) * 100
    # CVaR = promedio de la cola MAS ALLA del umbral -- siempre <= el umbral mismo
    assert cvar <= var_threshold


def test_time_to_recovery_caso_con_recuperacion():
    # shock en idx=1 (nivel pre-shock = cum[0]=1.0) -- recupera >=1.0 recien en idx=5
    cum = np.array([1.0, 0.7, 0.8, 0.9, 0.95, 1.2])
    assert st._time_to_recovery(cum, shock_idx=1) == 4


def test_time_to_recovery_sin_recuperacion_dentro_de_la_ventana():
    cum = np.array([1.0, 0.5, 0.6, 0.7, 0.8])
    assert st._time_to_recovery(cum, shock_idx=1, window=2) is None


def test_time_to_recovery_shock_en_idx_cero_usa_el_propio_valor_inicial():
    cum = np.array([1.0, 0.8, 1.1])
    assert st._time_to_recovery(cum, shock_idx=0) == 2


# --- apply_ccl_gap_shock -------------------------------------------------------

def _toy_returns_df(n=10):
    return pd.DataFrame({
        "ACCION1": [0.01] * n, "CEDEAR1": [0.02] * n, "BONO1": [0.0] * n,
    })


def test_ccl_gap_shock_solo_afecta_accion():
    df = _toy_returns_df()
    asset_types = {"ACCION1": "accion", "CEDEAR1": "cedear", "BONO1": "bono"}
    shocked = st.apply_ccl_gap_shock(df, asset_types, shock_idx=3, magnitude_pct=10.0)
    assert shocked["ACCION1"].iloc[3] == pytest.approx(0.01 - 0.10)
    assert shocked["CEDEAR1"].iloc[3] == pytest.approx(0.02)  # sin cambios
    assert shocked["BONO1"].iloc[3] == pytest.approx(0.0)  # sin cambios


def test_ccl_gap_shock_no_afecta_otras_filas():
    df = _toy_returns_df()
    asset_types = {"ACCION1": "accion", "CEDEAR1": "cedear", "BONO1": "bono"}
    shocked = st.apply_ccl_gap_shock(df, asset_types, shock_idx=3, magnitude_pct=10.0)
    for i in [0, 1, 2, 4, 5]:
        assert shocked["ACCION1"].iloc[i] == pytest.approx(0.01)


def test_ccl_gap_shock_magnitud_cero_es_identidad():
    df = _toy_returns_df()
    asset_types = {"ACCION1": "accion"}
    shocked = st.apply_ccl_gap_shock(df, asset_types, shock_idx=2, magnitude_pct=0.0)
    pd.testing.assert_frame_equal(shocked, df)


def test_ccl_gap_shock_no_tira_error_sin_columnas_accion():
    df = _toy_returns_df()
    shocked = st.apply_ccl_gap_shock(df, {"CEDEAR1": "cedear"}, shock_idx=2, magnitude_pct=10.0)
    pd.testing.assert_frame_equal(shocked, df)


# --- apply_vix_regime_shock -----------------------------------------------------

def test_vix_regime_shock_infla_la_desviacion_preservando_la_media():
    values = [0.01, -0.01, 0.02, -0.02, 0.01, 0.0, 0.01, -0.01]
    df = pd.DataFrame({"A": values})
    shocked = st.apply_vix_regime_shock(df, shock_idx=2, duration_days=3, vol_multiplier=2.0)
    mean = df["A"].mean()
    for i in range(2, 5):
        expected = mean + (df["A"].iloc[i] - mean) * 2.0
        assert shocked["A"].iloc[i] == pytest.approx(expected)
    # fuera de la ventana, sin cambios
    assert shocked["A"].iloc[0] == pytest.approx(values[0])
    assert shocked["A"].iloc[6] == pytest.approx(values[6])


def test_vix_regime_shock_multiplicador_uno_es_identidad():
    df = pd.DataFrame({"A": [0.01, -0.02, 0.03, -0.01, 0.0]})
    shocked = st.apply_vix_regime_shock(df, shock_idx=1, duration_days=2, vol_multiplier=1.0)
    pd.testing.assert_frame_equal(shocked, df)


def test_vix_regime_shock_afecta_todas_las_columnas_por_igual():
    df = pd.DataFrame({"A": [0.01, 0.02, 0.03], "B": [-0.01, -0.02, -0.03]})
    shocked = st.apply_vix_regime_shock(df, shock_idx=0, duration_days=3, vol_multiplier=3.0)
    assert not shocked["A"].equals(df["A"])
    assert not shocked["B"].equals(df["B"])


# --- apply_crash_shock ----------------------------------------------------------

def test_crash_shock_aplica_la_caida_inicial_a_todos_los_simbolos():
    df = pd.DataFrame({"A": [0.0] * 10, "B": [0.0] * 10})
    rng = np.random.default_rng(1)
    shocked = st.apply_crash_shock(df, shock_idx=3, initial_drop_pct=-7.0, decay_days=0,
                                    decay_prob=0.0, decay_magnitude_pct=2.0, rng=rng)
    assert shocked["A"].iloc[3] == pytest.approx(-0.07)
    assert shocked["B"].iloc[3] == pytest.approx(-0.07)


def test_crash_shock_decay_prob_cero_nunca_continua():
    df = pd.DataFrame({"A": [0.0] * 10})
    rng = np.random.default_rng(2)
    shocked = st.apply_crash_shock(df, shock_idx=2, initial_drop_pct=-5.0, decay_days=5,
                                    decay_prob=0.0, decay_magnitude_pct=1.0, rng=rng)
    for i in range(3, 8):
        assert shocked["A"].iloc[i] == pytest.approx(0.0)


def test_crash_shock_decay_prob_uno_siempre_continua():
    df = pd.DataFrame({"A": [0.0] * 10})
    rng = np.random.default_rng(3)
    shocked = st.apply_crash_shock(df, shock_idx=2, initial_drop_pct=-5.0, decay_days=3,
                                    decay_prob=1.0, decay_magnitude_pct=1.5, rng=rng)
    for i in range(3, 6):
        assert shocked["A"].iloc[i] == pytest.approx(-0.015)


def test_crash_shock_es_reproducible_con_la_misma_semilla():
    df = pd.DataFrame({"A": [0.0] * 10, "B": [0.0] * 10})
    rng1 = np.random.default_rng((42, 0))
    rng2 = np.random.default_rng((42, 0))
    s1 = st.apply_crash_shock(df, 2, -7.0, 4, 0.5, 2.0, rng1)
    s2 = st.apply_crash_shock(df, 2, -7.0, 4, 0.5, 2.0, rng2)
    pd.testing.assert_frame_equal(s1, s2)


def test_crash_shock_semillas_distintas_pueden_diferir():
    # No es una garantia matematica, pero con decay_prob=0.5 y varias
    # semillas distintas es extremadamente improbable que TODAS coincidan.
    df = pd.DataFrame({"A": [0.0] * 20})
    results = []
    for seed in range(10):
        rng = np.random.default_rng((seed, 0))
        results.append(tuple(st.apply_crash_shock(df, 2, -7.0, 10, 0.5, 2.0, rng)["A"].tolist()))
    assert len(set(results)) > 1


# --- portfolio sintetico (logica pura, sin red) --------------------------------

def test_portfolio_index_from_returns_equal_weight_es_el_promedio():
    df = pd.DataFrame({"A": [0.01, 0.02], "B": [0.03, -0.01]})
    combined = st._portfolio_index_from_returns(df)
    assert combined.iloc[0] == pytest.approx((0.01 + 0.03) / 2)
    assert combined.iloc[1] == pytest.approx((0.02 - 0.01) / 2)


def test_portfolio_index_from_returns_ignora_nan_puntuales():
    df = pd.DataFrame({"A": [0.01, np.nan], "B": [0.03, -0.01]})
    combined = st._portfolio_index_from_returns(df)
    # fila 1: solo B tiene dato -- el promedio debe ser -0.01, no (nan + -0.01)/2
    assert combined.iloc[1] == pytest.approx(-0.01)


def test_cumulative_index_compone_correctamente():
    returns = pd.Series([0.10, -0.10])
    cum = st._cumulative_index(returns, base=1.0)
    assert cum[0] == pytest.approx(1.10)
    assert cum[1] == pytest.approx(1.10 * 0.90)


# --- summarize_trials ------------------------------------------------------------

def test_summarize_trials_cuenta_los_que_no_se_recuperan():
    trials = [
        {"max_drawdown_pct": 10.0, "cvar_5pct": -2.0, "recovery_days": 5},
        {"max_drawdown_pct": 20.0, "cvar_5pct": -3.0, "recovery_days": None},
        {"max_drawdown_pct": 15.0, "cvar_5pct": None, "recovery_days": 8},
    ]
    summary = st.summarize_trials(trials)
    assert summary["n_no_recovery"] == 1
    assert summary["n_trials"] == 3
    assert summary["max_drawdown_worst_pct"] == 20.0
    assert summary["max_drawdown_mean_pct"] == pytest.approx((10.0 + 20.0 + 15.0) / 3)
    assert summary["recovery_days_mean"] == pytest.approx((5 + 8) / 2)


def test_summarize_trials_lista_vacia_devuelve_none():
    assert st.summarize_trials([]) is None
