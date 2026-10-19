"""Tests de la optimizacion dinamica de asignacion entre ESTRATEGIAS (ver
conversacion, "Black-Litterman / Risk Parity" -- portfolio/risk.py::
compute_dynamic_strategy_allocation y las 3 variantes: risk_parity,
min_variance, max_sharpe). Logica pura sobre series sinteticas -- sin red,
sin Chronos-Bolt, sin DB, mismo patron que tests/test_model_v2.py."""
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from portfolio import risk


def _synthetic_returns(seed=1, n=200):
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2025-01-01", periods=n, freq="D")
    a = pd.Series(rng.normal(0.08, 0.5, n), index=dates)
    b = pd.Series(rng.normal(0.15, 2.0, n), index=dates)
    c = 0.6 * a + pd.Series(rng.normal(0.02, 0.4, n), index=dates)
    return {"A": a, "B": b, "C": c}


# --- estimate_strategy_covariance --------------------------------------------

def test_estimate_strategy_covariance_pocos_motores_devuelve_none():
    names, cov, n_obs = risk.estimate_strategy_covariance({"A": pd.Series([1.0] * 50)})
    assert names is None and cov is None and n_obs == 0


def test_estimate_strategy_covariance_pocas_fechas_en_comun_devuelve_none():
    short_series = {
        "A": pd.Series(range(10), index=pd.date_range("2025-01-01", periods=10)),
        "B": pd.Series(range(10), index=pd.date_range("2025-01-01", periods=10)),
    }
    names, cov, n_obs = risk.estimate_strategy_covariance(short_series)
    assert names is None and n_obs == 10  # por debajo de MIN_STRATEGY_OVERLAP_DAYS=30


def test_estimate_strategy_covariance_alinea_por_interseccion_de_fechas():
    # B arranca 20 dias mas tarde -- solo las fechas EN COMUN deben contar.
    full_dates = pd.date_range("2025-01-01", periods=100)
    a = pd.Series(np.random.default_rng(2).normal(0, 1, 100), index=full_dates)
    b = pd.Series(np.random.default_rng(3).normal(0, 1, 80), index=full_dates[20:])
    names, cov, n_obs = risk.estimate_strategy_covariance({"A": a, "B": b})
    assert n_obs == 80
    assert cov.shape == (2, 2)


# --- risk_parity_weights / min_variance_weights / max_sharpe_weights -------

def test_risk_parity_pesos_suman_uno_y_son_no_negativos():
    returns = _synthetic_returns()
    names, cov, _ = risk.estimate_strategy_covariance(returns)
    weights = risk.risk_parity_weights(names, cov)
    assert sum(weights.values()) == pytest.approx(1.0, abs=1e-4)
    assert all(w >= 0 for w in weights.values())


def test_risk_parity_penaliza_al_activo_mas_volatil():
    # B tiene 4x la volatilidad de A en _synthetic_returns -- risk parity
    # debe asignarle MENOS peso que a A (misma contribucion de riesgo, no de capital).
    returns = _synthetic_returns()
    names, cov, _ = risk.estimate_strategy_covariance(returns)
    weights = risk.risk_parity_weights(names, cov)
    assert weights["B"] < weights["A"]


def test_min_variance_pesos_validos_y_menor_varianza_que_equal_weight():
    returns = _synthetic_returns()
    names, cov, _ = risk.estimate_strategy_covariance(returns)
    weights = risk.min_variance_weights(names, cov)
    assert sum(weights.values()) == pytest.approx(1.0, abs=1e-4)
    w = np.array([weights[n] for n in names])
    w_eq = np.full(len(names), 1.0 / len(names))
    min_var = float(w @ cov @ w)
    eq_var = float(w_eq @ cov @ w_eq)
    assert min_var <= eq_var + 1e-9


def test_max_sharpe_mejora_el_sharpe_sobre_equal_weight():
    returns = _synthetic_returns()
    names, cov, _ = risk.estimate_strategy_covariance(returns)
    aligned = pd.concat(returns, axis=1, join="inner").dropna()
    mean_returns = aligned.mean().to_numpy()

    weights = risk.max_sharpe_weights(names, mean_returns, cov)
    w = np.array([weights[n] for n in names])
    w_eq = np.full(len(names), 1.0 / len(names))

    def sharpe(w):
        ret = float(w @ mean_returns)
        vol = math.sqrt(float(w @ cov @ w))
        return ret / vol if vol > 0 else 0.0

    assert sharpe(w) >= sharpe(w_eq) - 1e-9


# --- compute_dynamic_strategy_allocation -------------------------------------

def test_compute_dynamic_strategy_allocation_insuficiente_devuelve_available_false():
    result = risk.compute_dynamic_strategy_allocation({"A": pd.Series([1.0] * 5)})
    assert result["available"] is False
    assert "reason" in result


def test_compute_dynamic_strategy_allocation_metodo_invalido_lanza_valueerror():
    returns = _synthetic_returns()
    with pytest.raises(ValueError):
        risk.compute_dynamic_strategy_allocation(returns, method="black_litterman_completo")


@pytest.mark.parametrize("method", ["risk_parity", "min_variance", "max_sharpe"])
def test_compute_dynamic_strategy_allocation_metodos_validos(method):
    returns = _synthetic_returns()
    result = risk.compute_dynamic_strategy_allocation(returns, method=method)
    assert result["available"] is True
    assert result["method"] == method
    assert sum(result["weights"].values()) == pytest.approx(1.0, abs=1e-3)
    assert result["n_obs"] == 200
    assert result["expected_sharpe_annualized"] is not None
