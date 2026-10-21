"""execute_legs -- orquestación secuencial de 2+ patas (ver docstring del
módulo: se detiene en la primera falla, nunca intenta un rollback
inventado). Mockea binance_execution.place_real_binance_order directo
(los gates de ESA función ya los cubre test_binance_execution.py)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from portfolio import db, multi_leg_execution


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_multi_leg_execution.db")
    monkeypatch.setattr(db, "DB_PATH", db_path)
    conn = db.get_connection(db_path)
    yield conn
    conn.close()


def _leg(market, symbol, side, quantity=0.1, price_usd=60000.0, broker="Binance"):
    return {"broker": broker, "market": market, "symbol": symbol, "side": side, "quantity": quantity, "price_usd": price_usd}


def test_ambas_patas_ok_devuelve_executed(temp_db, monkeypatch):
    calls = []

    def fake_place(conn, **kwargs):
        calls.append(kwargs)
        return {"id": len(calls), "estado": "confirmada"}

    monkeypatch.setattr(multi_leg_execution.binance_execution, "place_real_binance_order", fake_place)
    legs = [_leg("spot", "BTCUSDT", "buy"), _leg("futuros", "BTCUSDT", "sell")]

    result = multi_leg_execution.execute_legs(temp_db, legs=legs, patrimonio_usd=100_000)

    assert result["status"] == "executed"
    assert len(result["filled_legs"]) == 2
    assert result["error"] is None
    assert len(calls) == 2


def test_primera_pata_falla_nunca_intenta_la_segunda(temp_db, monkeypatch):
    calls = []

    def fake_place(conn, **kwargs):
        calls.append(kwargs)
        raise RuntimeError("Binance no responde")

    monkeypatch.setattr(multi_leg_execution.binance_execution, "place_real_binance_order", fake_place)
    legs = [_leg("spot", "BTCUSDT", "buy"), _leg("futuros", "BTCUSDT", "sell")]

    result = multi_leg_execution.execute_legs(temp_db, legs=legs, patrimonio_usd=100_000)

    assert result["status"] == "failed"  # ninguna pata se llego a ejecutar
    assert result["filled_legs"] == []
    assert "Binance no responde" in result["error"]
    assert len(calls) == 1  # nunca se intento la segunda pata


def test_segunda_pata_falla_queda_partially_filled(temp_db, monkeypatch):
    call_count = {"n": 0}

    def fake_place(conn, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return {"id": 1, "estado": "confirmada"}
        raise RuntimeError("timeout")

    monkeypatch.setattr(multi_leg_execution.binance_execution, "place_real_binance_order", fake_place)
    legs = [_leg("spot", "BTCUSDT", "buy"), _leg("futuros", "BTCUSDT", "sell")]

    result = multi_leg_execution.execute_legs(temp_db, legs=legs, patrimonio_usd=100_000)

    assert result["status"] == "partially_filled"
    assert len(result["filled_legs"]) == 1
    assert "DESPUÉS de 1 pata(s) ya ejecutada(s)" in result["error"]


def test_broker_no_soportado_falla_explicito(temp_db):
    legs = [_leg("spot", "AL29", "buy", broker="IOL")]
    result = multi_leg_execution.execute_legs(temp_db, legs=legs, patrimonio_usd=100_000)
    assert result["status"] == "failed"
    assert "no soportado" in result["error"]
