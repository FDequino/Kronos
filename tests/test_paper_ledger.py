"""Tests de portfolio/paper_ledger.py -- Paper Trading diario para
estrategias (Canje/Carry cripto) que no encajan en el modelo de posiciones
de portfolio/paper_trading.py (ver docstring del modulo). Nunca corre un
simulador real (series sinteticas via monkeypatch de
capital_allocator._STRATEGY_SIMULATORS, mismo criterio que
tests/test_capital_allocator.py)."""
import sys
from datetime import timedelta
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from portfolio import capital_allocator, db, paper_ledger, strategy_registry


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_paper_ledger.db")
    monkeypatch.setattr(db, "DB_PATH", db_path)
    conn = db.get_connection(db_path)
    yield conn
    conn.close()


def _synthetic_result(returns_pct, start="2026-01-01"):
    dates = pd.date_range(start, periods=len(returns_pct), freq="D")
    return {"daily_returns_pct": pd.Series(returns_pct, index=dates), "idle_weight": pd.Series(0.0, index=dates), "trades": []}


def test_primera_corrida_arranca_del_capital_inicial_default(temp_db, monkeypatch):
    monkeypatch.setitem(capital_allocator._STRATEGY_SIMULATORS, "canje_al29_gd29",
                         lambda conn: _synthetic_result([1.0, -0.5, 2.0]))

    inserted = paper_ledger.record_daily_return(temp_db, "canje_al29_gd29")

    assert len(inserted) == 3
    expected_equity = paper_ledger.DEFAULT_INITIAL_CAPITAL_USD * 1.01 * 0.995 * 1.02
    assert inserted[-1]["equity_usd"] == pytest.approx(expected_equity)


def test_correr_2_veces_el_mismo_dia_no_duplica(temp_db, monkeypatch):
    monkeypatch.setitem(capital_allocator._STRATEGY_SIMULATORS, "canje_al29_gd29",
                         lambda conn: _synthetic_result([1.0, -0.5, 2.0]))

    paper_ledger.record_daily_return(temp_db, "canje_al29_gd29")
    second = paper_ledger.record_daily_return(temp_db, "canje_al29_gd29")

    assert second == []  # ya estaba al dia, nada nuevo que agregar
    curve = paper_ledger.get_equity_curve(temp_db, "canje_al29_gd29")
    assert len(curve) == 3  # nunca se duplico ninguna fila


def test_se_pone_al_dia_si_faltaron_dias_nuevos(temp_db, monkeypatch):
    monkeypatch.setitem(capital_allocator._STRATEGY_SIMULATORS, "canje_al29_gd29",
                         lambda conn: _synthetic_result([1.0, -0.5]))
    paper_ledger.record_daily_return(temp_db, "canje_al29_gd29")

    # El simulador ahora devuelve 2 dias MAS (el cron "estuvo caido" y se
    # pone al dia) -- solo se agregan los dias posteriores al ultimo ya guardado.
    monkeypatch.setitem(capital_allocator._STRATEGY_SIMULATORS, "canje_al29_gd29",
                         lambda conn: _synthetic_result([1.0, -0.5, 3.0, -1.0]))

    inserted = paper_ledger.record_daily_return(temp_db, "canje_al29_gd29")

    assert len(inserted) == 2
    curve = paper_ledger.get_equity_curve(temp_db, "canje_al29_gd29")
    assert len(curve) == 4


def test_no_backfillea_historia_anterior_a_paper_trading_started_at(temp_db, monkeypatch):
    """Regresion del bug encontrado 2026-10-21: la primera corrida de una
    estrategia recien pasada a 'paper_trading' NO debe contar anios de
    backtest ya conocido como si fueran dias de paper trading reales."""
    strategy_registry.record_validation_result(temp_db, "canje_al29_gd29", dsr=0.99)
    strategy_registry.start_paper_trading(temp_db, "canje_al29_gd29")
    strategy = strategy_registry.get_strategy(temp_db, "canje_al29_gd29")
    started_at = pd.Timestamp(strategy["paper_trading_started_at"])

    # El simulador real devuelve AÑOS de historia (como el real), con fechas
    # muy anteriores a hoy -- nada de eso debe terminar en el ledger.
    dates = pd.date_range("2020-01-01", periods=1233, freq="D")
    monkeypatch.setitem(capital_allocator._STRATEGY_SIMULATORS, "canje_al29_gd29",
                         lambda conn: {"daily_returns_pct": pd.Series(0.1, index=dates),
                                       "idle_weight": pd.Series(0.0, index=dates), "trades": []})

    inserted = paper_ledger.record_daily_return(temp_db, "canje_al29_gd29")

    assert all(pd.Timestamp(row["date"]) > started_at for row in inserted)
    assert len(inserted) == 0  # ningun dia del simulador es posterior a hoy


def test_estrategia_sin_simulador_conocido_no_rompe(temp_db):
    inserted = paper_ledger.record_daily_return(temp_db, "estrategia_inventada")
    assert inserted == []


def test_run_all_solo_toca_paper_trading_o_live_sin_pausar(temp_db, monkeypatch):
    strategy_registry.record_validation_result(temp_db, "canje_al29_gd29", dsr=0.99)
    strategy_registry.start_paper_trading(temp_db, "canje_al29_gd29")  # 'paper_trading', elegible

    strategy_registry.record_validation_result(temp_db, "crypto_carry_btcusdt", dsr=1.0)
    strategy_registry.start_paper_trading(temp_db, "crypto_carry_btcusdt")
    strategy_registry.pause_paper_trading(temp_db, "crypto_carry_btcusdt")  # pausada, NO elegible

    strategy_registry.record_validation_result(temp_db, "core", dsr=0.0073)  # 'research', NO elegible

    # Fechas DESPUES de paper_trading_started_at (hoy) -- si no, el fix del
    # bug de backfill (ver test_no_backfillea_historia_...) las filtraria.
    tomorrow = (pd.Timestamp.now().normalize() + timedelta(days=1)).strftime("%Y-%m-%d")
    monkeypatch.setitem(capital_allocator._STRATEGY_SIMULATORS, "canje_al29_gd29",
                         lambda conn: _synthetic_result([1.0], start=tomorrow))
    monkeypatch.setitem(capital_allocator._STRATEGY_SIMULATORS, "crypto_carry_btcusdt",
                         lambda conn: _synthetic_result([1.0], start=tomorrow))

    results = paper_ledger.run_all(temp_db)

    assert set(results.keys()) == {"canje_al29_gd29"}
    assert len(results["canje_al29_gd29"]) == 1
