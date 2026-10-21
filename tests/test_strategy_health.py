"""Tests de portfolio/strategy_health.py -- Fase 4 de OBJETIVO_MARCO.md (plan
"grado-fondo"): compara el rendimiento RECIENTE real de una estrategia 'live'
contra su linea de base y la baja a semi_auto si se desvia -- esto es lo que
hubiera detectado solo el bug real de Kelly del Satelite en vez de
encontrarse a mano. Nunca manda un Telegram real (send_test_message
monkeypatcheado) ni corre un simulador real (series sinteticas)."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from portfolio import capital_allocator, db, strategy_health, strategy_registry


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_strategy_health.db")
    monkeypatch.setattr(db, "DB_PATH", db_path)
    conn = db.get_connection(db_path)
    yield conn
    conn.close()


def _synthetic_result(mean, n=120, seed=1):
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2026-01-01", periods=n, freq="D")
    returns = pd.Series(rng.normal(mean, 0.3, n), index=dates)
    return {"daily_returns_pct": returns, "idle_weight": pd.Series(0.0, index=dates), "trades": []}


def _make_live(conn, name, baseline_sharpe=6.0):
    strategy_registry.record_validation_result(conn, name, dsr=0.99, sharpe_ratio=baseline_sharpe)
    strategy_registry.start_paper_trading(conn, name)
    strategy_registry.promote_to_live(conn, name)
    strategy_registry.set_automation_mode(conn, name, "full_auto")


def test_estrategia_no_live_no_se_chequea(temp_db):
    strategy_registry.record_validation_result(temp_db, "canje_al29_gd29", dsr=0.99, sharpe_ratio=1.54)

    r = strategy_health.check_strategy_health(temp_db, "canje_al29_gd29")

    assert r["checked"] is False


def test_sin_baseline_no_se_chequea(temp_db):
    strategy_registry.record_validation_result(temp_db, "canje_al29_gd29", dsr=0.99)  # sin sharpe_ratio
    conn = temp_db
    conn.execute("UPDATE strategies SET status='live' WHERE name=?", ("canje_al29_gd29",))
    conn.commit()

    r = strategy_health.check_strategy_health(temp_db, "canje_al29_gd29")

    assert r["checked"] is False
    assert "baseline" in r["reason"]


def test_rendimiento_reciente_consistente_no_degrada(temp_db, monkeypatch):
    _make_live(temp_db, "canje_al29_gd29", baseline_sharpe=1.5)
    monkeypatch.setitem(capital_allocator._STRATEGY_SIMULATORS, "canje_al29_gd29",
                         lambda conn: _synthetic_result(mean=0.15, seed=1))  # edge fuerte y consistente

    r = strategy_health.check_strategy_health(temp_db, "canje_al29_gd29")

    assert r["checked"] is True
    assert r["degraded"] is False
    assert strategy_registry.get_strategy(temp_db, "canje_al29_gd29")["automation_mode"] == "full_auto"


def test_rendimiento_reciente_degradado_baja_a_semi_auto_y_notifica(temp_db, monkeypatch):
    """Mismo espiritu que el bug real del Satelite: una estrategia que
    validó fuerte pero cuyo rendimiento RECIENTE se derrumbo -- se auto-baja,
    nunca se apaga del todo sola."""
    _make_live(temp_db, "canje_al29_gd29", baseline_sharpe=6.0)
    monkeypatch.setitem(capital_allocator._STRATEGY_SIMULATORS, "canje_al29_gd29",
                         lambda conn: _synthetic_result(mean=-0.1, seed=2))  # edge reciente negativo

    sent = []
    monkeypatch.setattr(strategy_health.telegram_bot, "send_test_message",
                         lambda chat_id, text=None: (sent.append((chat_id, text)), (True, "enviado"))[1])
    strategy_registry.db.add_telegram_allowed_user(temp_db, chat_id="111", label="Test", role="admin")

    r = strategy_health.check_strategy_health(temp_db, "canje_al29_gd29")

    assert r["checked"] is True
    assert r["degraded"] is True
    assert strategy_registry.get_strategy(temp_db, "canje_al29_gd29")["automation_mode"] == "semi_auto"
    assert len(sent) == 1
    assert "canje_al29_gd29" in sent[0][1]


def test_estrategia_ya_en_off_degradada_no_vuelve_a_notificar(temp_db, monkeypatch):
    _make_live(temp_db, "canje_al29_gd29", baseline_sharpe=6.0)
    strategy_registry.set_automation_mode(temp_db, "canje_al29_gd29", "off")
    monkeypatch.setitem(capital_allocator._STRATEGY_SIMULATORS, "canje_al29_gd29",
                         lambda conn: _synthetic_result(mean=-0.1, seed=2))
    sent = []
    monkeypatch.setattr(strategy_health.telegram_bot, "send_test_message",
                         lambda chat_id, text=None: (sent.append((chat_id, text)), (True, "enviado"))[1])

    r = strategy_health.check_strategy_health(temp_db, "canje_al29_gd29")

    assert r["degraded"] is True
    assert strategy_registry.get_strategy(temp_db, "canje_al29_gd29")["automation_mode"] == "off"
    assert sent == []


def test_run_health_check_solo_mira_estrategias_live(temp_db, monkeypatch):
    _make_live(temp_db, "canje_al29_gd29", baseline_sharpe=1.5)
    strategy_registry.record_validation_result(temp_db, "crypto_carry_btcusdt", dsr=1.0, sharpe_ratio=6.0)  # validated, no live
    monkeypatch.setitem(capital_allocator._STRATEGY_SIMULATORS, "canje_al29_gd29",
                         lambda conn: _synthetic_result(mean=0.15, seed=1))

    results = strategy_health.run_health_check(temp_db)

    assert [r["name"] for r in results] == ["canje_al29_gd29"]
