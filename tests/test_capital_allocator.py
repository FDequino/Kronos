"""Tests de portfolio/capital_allocator.py -- Fase 3 de OBJETIVO_MARCO.md
(plan "grado-fondo"): conecta risk.py::compute_dynamic_strategy_allocation
(ya existia, nunca conectado) a las estrategias 'validated'/'live' del
Registro. Nunca corre los simuladores reales aca (séria lento, red real) --
monkeypatcheados con series sinteticas, mismo criterio que
tests/test_suggested_allocation.py."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from portfolio import capital_allocator, db, strategy_registry


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_capital_allocator.db")
    monkeypatch.setattr(db, "DB_PATH", db_path)
    conn = db.get_connection(db_path)
    yield conn
    conn.close()


def _synthetic_result(seed, n=200, mean=0.1):
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2025-01-01", periods=n, freq="D")
    returns = pd.Series(rng.normal(mean, 0.5, n), index=dates)
    return {"daily_returns_pct": returns, "idle_weight": pd.Series(0.0, index=dates), "trades": []}


def test_necesita_al_menos_2_estrategias_validated(temp_db):
    strategy_registry.record_validation_result(temp_db, "canje_al29_gd29", dsr=0.99)
    strategy_registry.record_validation_result(temp_db, "core", dsr=0.0073)  # research -- no cuenta

    result = capital_allocator.run_dynamic_allocation(temp_db)

    assert result["available"] is False
    assert "1" in result["reason"]  # solo 1 elegible (canje)


def test_nunca_asigna_capital_a_una_estrategia_en_research(temp_db, monkeypatch):
    strategy_registry.record_validation_result(temp_db, "canje_al29_gd29", dsr=0.99)
    strategy_registry.record_validation_result(temp_db, "crypto_carry_btcusdt", dsr=1.0)
    strategy_registry.record_validation_result(temp_db, "core", dsr=0.0073)

    monkeypatch.setitem(capital_allocator._STRATEGY_SIMULATORS, "canje_al29_gd29", lambda conn: _synthetic_result(1))
    monkeypatch.setitem(capital_allocator._STRATEGY_SIMULATORS, "crypto_carry_btcusdt", lambda conn: _synthetic_result(2))

    result = capital_allocator.run_dynamic_allocation(temp_db)

    assert result["available"] is True
    assert set(result["weights"]) == {"canje_al29_gd29", "crypto_carry_btcusdt"}
    # 'core' nunca entra al calculo ni recibe capital -- sigue en 0.0 (default)
    assert strategy_registry.get_strategy(temp_db, "core")["capital_pct"] == 0.0


def test_persiste_los_pesos_calculados_en_el_registro(temp_db, monkeypatch):
    strategy_registry.record_validation_result(temp_db, "canje_al29_gd29", dsr=0.99)
    strategy_registry.record_validation_result(temp_db, "crypto_carry_btcusdt", dsr=1.0)
    monkeypatch.setitem(capital_allocator._STRATEGY_SIMULATORS, "canje_al29_gd29", lambda conn: _synthetic_result(1))
    monkeypatch.setitem(capital_allocator._STRATEGY_SIMULATORS, "crypto_carry_btcusdt", lambda conn: _synthetic_result(2))

    result = capital_allocator.run_dynamic_allocation(temp_db)

    for name, weight in result["weights"].items():
        persisted = strategy_registry.get_strategy(temp_db, name)["capital_pct"]
        assert persisted == pytest.approx(round(weight * 100, 2))
        assert persisted > 0.0


def test_sin_simulador_conocido_se_omite_sin_romper(temp_db, monkeypatch):
    strategy_registry.record_validation_result(temp_db, "canje_al29_gd29", dsr=0.99)
    strategy_registry.record_validation_result(temp_db, "crypto_carry_btcusdt", dsr=1.0)
    monkeypatch.setitem(capital_allocator._STRATEGY_SIMULATORS, "canje_al29_gd29", lambda conn: _synthetic_result(1))
    monkeypatch.delitem(capital_allocator._STRATEGY_SIMULATORS, "crypto_carry_btcusdt")

    result = capital_allocator.run_dynamic_allocation(temp_db)

    assert result["available"] is False  # solo 1 quedo con datos simulables
