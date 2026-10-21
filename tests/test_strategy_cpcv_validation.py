"""Tests de portfolio/backtest.py::run_strategy_cpcv_validation -- el gate
CPCV+DSR de OBJETIVO_MARCO.md ("listo para plata real" == DSR>=0.95) aplicado
a UNA sola estrategia, no al blend unificado. Reusa _cpcv_blended_paths/
deflated_sharpe_ratio tal cual (ya testeados vía run_unified_cpcv_backtest) --
estos tests solo cubren el ensamblado nuevo (per-engine en vez de blend)."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from portfolio import backtest


def _engine_result(returns_pct, idle=None):
    dates = pd.date_range("2024-01-01", periods=len(returns_pct), freq="D")
    return {
        "daily_returns_pct": pd.Series(returns_pct, index=dates, dtype=float),
        "idle_weight": pd.Series(idle if idle is not None else [0.0] * len(returns_pct), index=dates, dtype=float),
        "trades": [],
    }


def test_historial_insuficiente_para_cpcv_no_rompe():
    result = _engine_result([0.1, -0.2, 0.3])  # menos dias que UNIFIED_CPCV_GROUPS (6)

    r = backtest.run_strategy_cpcv_validation("juguete", result)

    assert r["full_metrics"] is None
    assert "insuficiente" in r["reason"]


def test_edge_real_fuerte_y_consistente_da_dsr_alto():
    """Una estrategia con un edge GENUINO y estable en todo el periodo (no
    concentrado en un solo tramo de suerte) deberia pasar el gate -- mismo
    criterio que ya usa run_unified_cpcv_backtest para el blend.

    Bug real encontrado (auditoria 2026-10-20, BTCUSDT carry: Sharpe anual
    6.64 pero DSR=0.0000): deflated_sharpe_ratio compara su sr_hat (siempre
    SIN anualizar) contra sr0 -- si sr0 se construye con el Sharpe por-camino
    YA anualizado (sharpe_ratio en vez de sharpe_ratio_raw), la comparacion
    queda en escalas ~sqrt(252) distintas y el DSR sale pegado a 0 sin
    importar que tan fuerte sea el edge real. Este test falla si ese bug
    reaparece."""
    rng = np.random.default_rng(7)
    n = 500
    returns = rng.normal(0.15, 0.3, n)  # media positiva fuerte, poca volatilidad relativa
    result = _engine_result(returns)

    r = backtest.run_strategy_cpcv_validation("edge_fuerte", result)

    assert r["full_metrics"] is not None
    assert r["dsr_result"] is not None
    assert r["n_evaluable"] > 0
    assert r["dsr_result"]["dsr"] >= 0.95, (
        f"DSR={r['dsr_result']['dsr']} para un edge fuerte y consistente -- "
        "revisar si sharpe_ratio_raw (sin anualizar) se sigue usando para sr0"
    )


def test_ruido_puro_sin_edge_da_dsr_bajo():
    """Retornos centrados en cero (sin edge real) -- el DSR no deberia marcar
    esto como significativo, mismo espiritu que el Core real (DSR=0.0000)."""
    rng = np.random.default_rng(13)
    n = 500
    returns = rng.normal(0.0, 1.0, n)
    result = _engine_result(returns)

    r = backtest.run_strategy_cpcv_validation("ruido", result)

    assert r["dsr_result"] is not None
    assert r["dsr_result"]["dsr"] < 0.95
