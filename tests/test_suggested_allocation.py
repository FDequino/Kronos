"""Tests de la Fase 0 del plan de unificacion (ver conversacion: "la
asignacion dinamica de capital por motor, ya construida en
risk.py::compute_dynamic_strategy_allocation, nunca se conecto a nada").
portfolio/backtest.py::run_suggested_allocation_report nunca corre de verdad
simulate_unified_backtest acá (séria lento, llamadas a Chronos-Bolt) --
monkeypatcheado con series sinteticas, mismo criterio que
tests/test_risk_allocation.py."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from portfolio import backtest, db


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_suggested_allocation.db")
    monkeypatch.setattr(db, "DB_PATH", db_path)
    conn = db.get_connection(db_path)
    yield conn
    conn.close()


def _synthetic_engine_result(seed, n=200):
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2025-01-01", periods=n, freq="D")
    returns = pd.Series(rng.normal(0.1, 0.5, n), index=dates)
    return {"daily_returns_pct": returns, "idle_weight": pd.Series(0.0, index=dates), "trades": []}


def _fake_unified_backtest(conn=None):
    return {
        "daily_returns_pct": pd.Series(dtype=float),
        "idle_weight": pd.Series(dtype=float),
        "trades": [],
        "per_engine": {
            "core": _synthetic_engine_result(1),
            "satellite": _synthetic_engine_result(2),
            "pairs": _synthetic_engine_result(3),
            "factor": _synthetic_engine_result(4),
            "canje": _synthetic_engine_result(5),
            # "yield" (sleeve de Caucion pura) -- debe excluirse de la
            # optimizacion, ver docstring de run_suggested_allocation_report.
            "yield": _synthetic_engine_result(6),
        },
        "weights": {"core": 15.0, "satellite": 60.0, "pairs": 5.0, "factor": 10.0, "canje": 5.0, "yield": 5.0},
    }


def test_run_suggested_allocation_report_excluye_yield_y_guarda_resultado(temp_db, monkeypatch):
    monkeypatch.setattr(backtest, "simulate_unified_backtest", _fake_unified_backtest)

    result = backtest.run_suggested_allocation_report(temp_db, method="risk_parity")

    assert result["available"] is True
    assert "yield" not in result["weights"]
    assert set(result["weights"]) == {"core", "satellite", "pairs", "factor", "canje"}

    saved = db.get_suggested_allocation(temp_db)
    assert saved["method"] == "risk_parity"
    assert saved["result"] == result
    # La asignacion configurada se guarda SIN "yield" tambien (no es un motor
    # con el que competir por capital, ver docstring).
    assert "yield" not in saved["configured_weights_at_run"]
    assert saved["configured_weights_at_run"] == {"core": 15.0, "satellite": 60.0, "pairs": 5.0, "factor": 10.0, "canje": 5.0}


def test_get_suggested_allocation_sin_corridas_devuelve_none(temp_db):
    assert db.get_suggested_allocation(temp_db) is None


def test_save_suggested_allocation_sobreescribe_la_fila_unica(temp_db):
    db.save_suggested_allocation(
        temp_db, run_at="2026-01-01T00:00:00", method="max_sharpe",
        result={"available": False, "reason": "poca historia"}, configured_weights={"core": 20.0},
    )
    db.save_suggested_allocation(
        temp_db, run_at="2026-01-02T00:00:00", method="risk_parity",
        result={"available": True, "weights": {"core": 0.5}}, configured_weights={"core": 20.0},
    )

    saved = db.get_suggested_allocation(temp_db)
    assert saved["method"] == "risk_parity"
    assert saved["result"]["available"] is True
