"""Tests del entorno Gymnasium de Asignacion Dinamica de Capital (ver
conversacion, "Aprendizaje por Refuerzo" -- portfolio/rl_env.py). Logica
pura sobre datos SINTETICOS armados a mano -- sin red, sin Chronos-Bolt, sin
DB (mismo patron que tests/test_model_v2.py): valida la mecanica de
reset()/step() (API Gymnasium moderna, 5-tuplas) y la normalizacion por
softmax del espacio de acciones, no un entrenamiento real de PPO (eso
corrio y se reporto a mano en la conversacion, demasiado lento/con red para
este suite)."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from portfolio.rl_env import OBS_MARKET_COLUMNS, RLCapitalAllocationEnv


def _synthetic_market_df(n=20, seed=0):
    rng = np.random.default_rng(seed)
    return pd.DataFrame({
        "chronos_p50_return_pct": rng.normal(0, 1, n),
        "chronos_dispersion_ratio": rng.uniform(0, 1, n),
        "vix_level": rng.uniform(12, 30, n),
        "brecha_cambiaria_pct": rng.uniform(0, 20, n),
        "atr_pct": rng.uniform(1, 5, n),
    })


def _synthetic_env(n=20, seed=0):
    rng = np.random.default_rng(seed)
    market_df = _synthetic_market_df(n, seed)
    core_return = pd.Series(rng.normal(0.1, 1.0, n))
    satellite_return = pd.Series(rng.normal(0.2, 1.5, n))
    caucion_return = pd.Series(np.full(n, 0.05))
    return RLCapitalAllocationEnv(market_df, core_return, satellite_return, caucion_return)


# --- construccion / validacion de longitudes ---------------------------------

def test_env_rechaza_series_de_longitud_distinta():
    market_df = _synthetic_market_df(10)
    with pytest.raises(ValueError):
        RLCapitalAllocationEnv(market_df, pd.Series([0.0] * 10), pd.Series([0.0] * 9), pd.Series([0.0] * 10))


def test_env_rechaza_ventana_demasiado_corta():
    market_df = _synthetic_market_df(1)
    with pytest.raises(ValueError):
        RLCapitalAllocationEnv(market_df, pd.Series([0.0]), pd.Series([0.0]), pd.Series([0.0]))


# --- reset() -------------------------------------------------------------------

def test_reset_devuelve_observacion_y_dict_info():
    env = _synthetic_env()
    obs, info = env.reset()
    assert obs.shape == env.observation_space.shape
    assert obs.dtype == np.float32
    assert isinstance(info, dict)
    # current_position_pct (ultima columna) arranca en 0 -- el agente no
    # tiene ninguna posicion tomada todavia al inicio del episodio.
    assert obs[-1] == 0.0


def test_reset_reinicia_estado_interno_entre_episodios():
    env = _synthetic_env()
    env.reset()
    env.step(np.array([1.0, 0.0, 0.0]))
    assert env._idx == 1
    obs, _info = env.reset()
    assert env._idx == 0
    assert obs[-1] == 0.0  # current_position_pct vuelve a 0


# --- step() ----------------------------------------------------------------

def test_step_devuelve_5_tupla_con_tipos_correctos():
    env = _synthetic_env()
    env.reset()
    obs, reward, terminated, truncated, info = env.step(np.array([0.5, 0.3, 0.2]))
    assert obs.shape == env.observation_space.shape
    assert obs.dtype == np.float32
    assert isinstance(reward, float)
    assert np.isfinite(reward)
    assert isinstance(terminated, bool)
    assert isinstance(truncated, bool)
    assert isinstance(info, dict)
    assert "weights" in info and "daily_return_pct" in info


def test_step_termina_al_final_de_la_ventana():
    n = 5
    env = _synthetic_env(n=n)
    env.reset()
    for i in range(n):
        _obs, _reward, terminated, truncated, _info = env.step(np.array([0.34, 0.33, 0.33]))
        if i < n - 1:
            assert terminated is False
        else:
            assert terminated is True
    assert truncated is False


def test_step_sin_reset_previo_lanza_error():
    n = 3
    env = _synthetic_env(n=n)
    env.reset()
    for _ in range(n):
        env.step(np.array([0.34, 0.33, 0.33]))
    with pytest.raises(RuntimeError):
        env.step(np.array([0.34, 0.33, 0.33]))


def test_step_reward_incluye_bonus_de_sharpe_solo_al_final():
    # Retornos SIEMPRE positivos (drawdown constante en 0, ningun castigo de
    # por medio) pero variando dia a dia (std > 0, para que el Sharpe del
    # episodio no sea cero) -- el reward del ULTIMO step debe superar
    # ampliamente el mayor retorno diario individual, porque ahi se suma el
    # bonus de Sharpe anualizado (ver SHARPE_BONUS_SCALE/ANNUALIZATION_PERIODS
    # en portfolio/rl_env.py -- el factor sqrt(252) hace que incluso un
    # Sharpe diario modesto se vuelva un numero grande anualizado).
    n = 10
    market_df = _synthetic_market_df(n)
    core_return = pd.Series([0.1, 0.3] * (n // 2))
    satellite_return = pd.Series(np.full(n, 0.0))
    caucion_return = pd.Series(np.full(n, 0.0))
    env = RLCapitalAllocationEnv(market_df, core_return, satellite_return, caucion_return)
    env.reset()
    daily_rewards = []
    for _ in range(n):
        _obs, reward, terminated, _truncated, _info = env.step(np.array([10.0, -10.0, -10.0]))  # softmax -> ~100% Core
        daily_rewards.append(reward)
    assert terminated is True
    assert daily_rewards[-1] > max(daily_rewards[:-1]) + 1.0  # el bonus de Sharpe domina el ultimo reward


# --- normalize_action (softmax) ---------------------------------------------

def test_normalize_action_siempre_suma_uno():
    for raw in ([0.0, 0.0, 0.0], [5.0, -3.0, 0.2], [-100.0, 100.0, 0.0], [1e6, 1e6, 1e6]):
        weights = RLCapitalAllocationEnv.normalize_action(np.array(raw))
        assert weights.sum() == pytest.approx(1.0, abs=1e-6)
        assert np.all(weights >= 0.0)


def test_normalize_action_mayor_logit_da_mayor_peso():
    weights = RLCapitalAllocationEnv.normalize_action(np.array([5.0, 0.0, 0.0]))
    assert weights[0] > weights[1]
    assert weights[0] > weights[2]


def test_step_pesos_en_info_siempre_suman_uno():
    env = _synthetic_env()
    env.reset()
    for raw_action in ([10.0, -10.0, 0.0], [0.0, 0.0, 0.0], [-5.0, -5.0, 10.0]):
        _obs, _reward, terminated, _truncated, info = env.step(np.array(raw_action))
        assert sum(info["weights"]) == pytest.approx(1.0, abs=1e-6)
        if terminated:
            break
