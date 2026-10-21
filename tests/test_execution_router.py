"""execution_router -- enrutador full_auto/semi_auto/off + resolve_decision
(confirmación por Telegram/Web, lo que llegue primero gana). Mockea
live_strategy_engine.build_real_intent y multi_leg_execution.execute_legs
directo (sus propios tests ya cubren esa lógica)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from portfolio import db, execution_router, strategy_registry


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_execution_router.db")
    monkeypatch.setattr(db, "DB_PATH", db_path)
    conn = db.get_connection(db_path)
    yield conn
    conn.close()


def _make_live(conn, name, automation_mode="semi_auto", capital_pct=100.0):
    strategy_registry.record_validation_result(conn, name, dsr=1.0, annualized_return_pct=10.0, sharpe_ratio=2.0)
    strategy_registry.start_paper_trading(conn, name)
    strategy_registry.promote_to_live(conn, name)
    strategy_registry.set_capital_pct(conn, name, capital_pct)
    strategy_registry.set_automation_mode(conn, name, automation_mode)


_OPEN_INTENT = {
    "action": "open", "capital_usd": 6000.0, "reason": "edge real",
    "legs": [
        {"broker": "Binance", "market": "spot", "symbol": "BTCUSDT", "side": "buy", "quantity": 0.1, "price_usd": 60000.0},
        {"broker": "Binance", "market": "futuros", "symbol": "BTCUSDT", "side": "sell", "quantity": 0.1, "price_usd": 60000.0},
    ],
}


def _no_breaker(monkeypatch):
    monkeypatch.setattr(execution_router.risk_engine, "check_circuit_breaker", lambda conn: {"tripped": False, "drawdown_pct": None, "limit_pct": -20})


# --- run_daily_execution_cycle: ruteo por automation_mode --------------------

def test_off_no_genera_ninguna_decision(temp_db, monkeypatch):
    _make_live(temp_db, "crypto_carry_btcusdt", automation_mode="off")
    _no_breaker(monkeypatch)
    monkeypatch.setattr(execution_router.live_strategy_engine, "build_real_intent", lambda conn, name: _OPEN_INTENT)

    summary = execution_router.run_daily_execution_cycle(temp_db)

    assert summary["crypto_carry_btcusdt"]["action"] is None
    assert db.list_execution_decisions(temp_db) == []


def test_estrategia_sin_builder_real_se_loguea_sin_fallar(temp_db, monkeypatch):
    _make_live(temp_db, "canje_al29_gd29", automation_mode="full_auto")
    _no_breaker(monkeypatch)

    summary = execution_router.run_daily_execution_cycle(temp_db)

    assert summary["canje_al29_gd29"]["action"] is None
    assert "sin ejecucion real implementada" in summary["canje_al29_gd29"]["reason"]


def test_circuit_breaker_disparado_bloquea_incluso_full_auto(temp_db, monkeypatch):
    _make_live(temp_db, "crypto_carry_btcusdt", automation_mode="full_auto")
    monkeypatch.setattr(
        execution_router.risk_engine, "check_circuit_breaker",
        lambda conn: {"tripped": True, "drawdown_pct": -25.0, "limit_pct": -20.0},
    )
    monkeypatch.setattr(execution_router.live_strategy_engine, "build_real_intent", lambda conn, name: _OPEN_INTENT)

    summary = execution_router.run_daily_execution_cycle(temp_db)

    assert summary["crypto_carry_btcusdt"]["action"] is None
    assert "circuit breaker" in summary["crypto_carry_btcusdt"]["reason"]
    assert db.list_execution_decisions(temp_db) == []


def test_sin_intent_no_genera_decision(temp_db, monkeypatch):
    _make_live(temp_db, "crypto_carry_btcusdt", automation_mode="full_auto")
    _no_breaker(monkeypatch)
    monkeypatch.setattr(execution_router.live_strategy_engine, "build_real_intent", lambda conn, name: None)

    summary = execution_router.run_daily_execution_cycle(temp_db)

    assert summary["crypto_carry_btcusdt"]["action"] is None
    assert db.list_execution_decisions(temp_db) == []


def test_semi_auto_queda_pendiente_de_confirmacion(temp_db, monkeypatch):
    _make_live(temp_db, "crypto_carry_btcusdt", automation_mode="semi_auto")
    _no_breaker(monkeypatch)
    monkeypatch.setattr(execution_router.live_strategy_engine, "build_real_intent", lambda conn, name: _OPEN_INTENT)
    monkeypatch.setattr(
        execution_router.multi_leg_execution, "execute_legs",
        lambda conn, **kw: pytest.fail("semi_auto no deberia ejecutar nada todavia"),
    )

    summary = execution_router.run_daily_execution_cycle(temp_db)

    assert summary["crypto_carry_btcusdt"]["status"] == "pending_confirmation"
    decisions = db.list_execution_decisions(temp_db)
    assert len(decisions) == 1
    assert decisions[0]["status"] == "pending_confirmation"
    assert decisions[0]["mode"] == "real"


def test_full_auto_ejecuta_y_abre_posicion(temp_db, monkeypatch):
    _make_live(temp_db, "crypto_carry_btcusdt", automation_mode="full_auto")
    _no_breaker(monkeypatch)
    monkeypatch.setattr(execution_router.live_strategy_engine, "build_real_intent", lambda conn, name: _OPEN_INTENT)
    monkeypatch.setattr(
        execution_router.multi_leg_execution, "execute_legs",
        lambda conn, **kw: {"status": "executed", "filled_legs": [{"id": 1}, {"id": 2}], "error": None},
    )

    summary = execution_router.run_daily_execution_cycle(temp_db)

    assert summary["crypto_carry_btcusdt"]["status"] == "auto_executed"
    decisions = db.list_execution_decisions(temp_db)
    assert decisions[0]["status"] == "auto_executed"
    assert decisions[0]["resolved_via"] == "auto"
    positions = db.list_strategy_positions(temp_db, "crypto_carry_btcusdt")
    assert len(positions) == 1
    assert positions[0]["status"] == "open"


def test_full_auto_falla_marca_decision_como_failed(temp_db, monkeypatch):
    _make_live(temp_db, "crypto_carry_btcusdt", automation_mode="full_auto")
    _no_breaker(monkeypatch)
    monkeypatch.setattr(execution_router.live_strategy_engine, "build_real_intent", lambda conn, name: _OPEN_INTENT)
    monkeypatch.setattr(
        execution_router.multi_leg_execution, "execute_legs",
        lambda conn, **kw: {"status": "partially_filled", "filled_legs": [{"id": 1}], "error": "la pata 2 fallo"},
    )

    summary = execution_router.run_daily_execution_cycle(temp_db)

    assert summary["crypto_carry_btcusdt"]["status"] == "partially_filled"
    decisions = db.list_execution_decisions(temp_db)
    assert decisions[0]["status"] == "partially_filled"
    assert decisions[0]["error_detalle"] == "la pata 2 fallo"
    assert db.list_strategy_positions(temp_db, "crypto_carry_btcusdt") == []  # nunca se abre posicion sobre una ejecucion parcial


# --- resolve_decision ---------------------------------------------------------

def test_resolve_decision_aprobada_ejecuta(temp_db, monkeypatch):
    _make_live(temp_db, "crypto_carry_btcusdt", automation_mode="semi_auto")
    decision = db.insert_execution_decision(
        temp_db, strategy_name="crypto_carry_btcusdt", mode="real", action="open",
        legs=_OPEN_INTENT["legs"], reason="edge real", status="pending_confirmation",
    )
    monkeypatch.setattr(
        execution_router.multi_leg_execution, "execute_legs",
        lambda conn, **kw: {"status": "executed", "filled_legs": [{"id": 1}, {"id": 2}], "error": None},
    )

    result = execution_router.resolve_decision(temp_db, decision["id"], approved=True, via="telegram")

    assert result["already_resolved"] is False
    assert result["decision"]["status"] == "executed"
    assert result["decision"]["resolved_via"] == "telegram"


def test_resolve_decision_rechazada_no_ejecuta_nada(temp_db, monkeypatch):
    _make_live(temp_db, "crypto_carry_btcusdt", automation_mode="semi_auto")
    decision = db.insert_execution_decision(
        temp_db, strategy_name="crypto_carry_btcusdt", mode="real", action="open",
        legs=_OPEN_INTENT["legs"], reason="edge real", status="pending_confirmation",
    )
    monkeypatch.setattr(
        execution_router.multi_leg_execution, "execute_legs",
        lambda conn, **kw: pytest.fail("rechazada no deberia ejecutar nada"),
    )

    result = execution_router.resolve_decision(temp_db, decision["id"], approved=False, via="web")

    assert result["decision"]["status"] == "rejected"
    assert db.list_strategy_positions(temp_db) == []


def test_resolve_decision_ya_resuelta_no_hace_nada_de_nuevo(temp_db):
    _make_live(temp_db, "crypto_carry_btcusdt", automation_mode="semi_auto")
    decision = db.insert_execution_decision(
        temp_db, strategy_name="crypto_carry_btcusdt", mode="real", action="open",
        legs=_OPEN_INTENT["legs"], reason="edge real", status="rejected",
    )

    result = execution_router.resolve_decision(temp_db, decision["id"], approved=True, via="telegram")

    assert result["already_resolved"] is True
    assert result["decision"]["status"] == "rejected"  # el canal tardio nunca pisa lo que ya se resolvio
