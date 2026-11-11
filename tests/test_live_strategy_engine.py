"""live_strategy_engine -- intent builder de ejecución REAL para Carry
BTC/ETH (ver docstring del módulo: Canje NO tiene builder real a propósito,
el backtest de la posición real ejecutable da DSR~0, pierde plata)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from portfolio import broker_client, db, live_strategy_engine, strategy_registry


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_live_strategy_engine.db")
    monkeypatch.setattr(db, "DB_PATH", db_path)
    conn = db.get_connection(db_path)
    yield conn
    conn.close()


def _setup_binance_account(conn, *, is_active=True):
    manager = broker_client.MultiBrokerManager()
    account = manager.add_account(
        conn, broker_name="Binance", api_key="k", api_secret="s", is_active=is_active,
    )
    return account["id"]


def _make_live(conn, name, capital_pct=50.0):
    strategy_registry.record_validation_result(conn, name, dsr=1.0, annualized_return_pct=10.0, sharpe_ratio=2.0)
    strategy_registry.start_paper_trading(conn, name)
    strategy_registry.promote_to_live(conn, name)
    strategy_registry.set_capital_pct(conn, name, capital_pct)


# --- has_real_intent_builder / registro -----------------------------------

def test_canje_no_tiene_builder_real_a_proposito(temp_db):
    assert live_strategy_engine.has_real_intent_builder("canje_al29_gd29") is False
    assert live_strategy_engine.build_real_intent(temp_db, "canje_al29_gd29") is None


def test_carry_btc_eth_tienen_builder_real(temp_db):
    assert live_strategy_engine.has_real_intent_builder("crypto_carry_btcusdt") is True
    assert live_strategy_engine.has_real_intent_builder("crypto_carry_ethusdt") is True


def test_carry_ampliacion_2026_10_24_tiene_builder_real(temp_db):
    # ADA/DOGE/LINK/LTC pasaron el mismo gate DSR>=0.95 que BTC/ETH (ver
    # docstring de crypto_carry.py, "AMPLIACION 2026-10-24") -- mismo
    # mecanismo spot+futuro, mismo criterio de executabilidad real.
    for name in ("crypto_carry_adausdt", "crypto_carry_dogeusdt", "crypto_carry_linkusdt", "crypto_carry_ltcusdt"):
        assert live_strategy_engine.has_real_intent_builder(name) is True


# --- capital_usd_for_strategy ------------------------------------------------

def test_capital_sin_configurar_devuelve_none_nunca_inventa(temp_db):
    _make_live(temp_db, "crypto_carry_btcusdt")
    assert live_strategy_engine.capital_usd_for_strategy(temp_db, "crypto_carry_btcusdt") is None


def test_capital_configurado_se_reparte_por_capital_pct(temp_db):
    _make_live(temp_db, "crypto_carry_btcusdt", capital_pct=40.0)
    db.update_app_settings(temp_db, auto_trading_capital_usd=10_000)
    assert live_strategy_engine.capital_usd_for_strategy(temp_db, "crypto_carry_btcusdt") == 4_000.0


# --- _crypto_carry_intent -----------------------------------------------------

def test_sin_posicion_sin_señal_no_hace_nada(temp_db, monkeypatch):
    _make_live(temp_db, "crypto_carry_btcusdt")
    db.update_app_settings(temp_db, auto_trading_capital_usd=10_000)
    monkeypatch.setattr(live_strategy_engine.crypto_carry, "compute_funding_rate_carry_signal", lambda symbol: None)

    assert live_strategy_engine.build_real_intent(temp_db, "crypto_carry_btcusdt") is None


def test_sin_posicion_sin_capital_configurado_nunca_abre(temp_db, monkeypatch):
    _make_live(temp_db, "crypto_carry_btcusdt")
    monkeypatch.setattr(
        live_strategy_engine.crypto_carry, "compute_funding_rate_carry_signal",
        lambda symbol: {"note": "edge"},
    )
    assert live_strategy_engine.build_real_intent(temp_db, "crypto_carry_btcusdt") is None


def test_sin_posicion_con_señal_y_capital_abre_2_patas(temp_db, monkeypatch):
    _setup_binance_account(temp_db)
    monkeypatch.setattr(broker_client.BinanceAdapter, "get_lot_size_filters", lambda self, **kw: (0.00001, 5.0))
    _make_live(temp_db, "crypto_carry_btcusdt", capital_pct=100.0)
    db.update_app_settings(temp_db, auto_trading_capital_usd=6_000)
    monkeypatch.setattr(
        live_strategy_engine.crypto_carry, "compute_funding_rate_carry_signal",
        lambda symbol: {"note": "funding bueno"},
    )
    monkeypatch.setattr(live_strategy_engine, "_spot_price_usd", lambda symbol: 60_000.0)

    intent = live_strategy_engine.build_real_intent(temp_db, "crypto_carry_btcusdt")

    assert intent["action"] == "open"
    assert intent["capital_usd"] == 6_000.0
    assert len(intent["legs"]) == 2
    spot_leg = next(l for l in intent["legs"] if l["market"] == "spot")
    futures_leg = next(l for l in intent["legs"] if l["market"] == "futuros")
    assert spot_leg["side"] == "buy" and futures_leg["side"] == "sell"
    assert spot_leg["quantity"] == futures_leg["quantity"] == 0.1


def test_carry_no_abre_si_queda_por_debajo_del_notional_minimo_real(temp_db, monkeypatch):
    # BUG REAL 2026-11-11: con poco capital, el stepSize puede dar una
    # cantidad que pasa LOT_SIZE pero el notional (cantidad*precio) queda
    # por debajo del minimo real de alguna de las 2 patas (confirmado real:
    # futuros de Binance pide 50 USD, bastante mas que spot) -- antes esto
    # se descubria recien al intentar ejecutar, con la pata spot YA
    # confirmada y la de futuro rechazada (posicion sin cobertura). Ahora
    # _round_carry_quantity lo chequea ANTES y no abre nada.
    _setup_binance_account(temp_db)
    monkeypatch.setattr(broker_client.BinanceAdapter, "get_lot_size_filters", lambda self, **kw: (0.00001, 50.0))
    _make_live(temp_db, "crypto_carry_btcusdt", capital_pct=100.0)
    db.update_app_settings(temp_db, auto_trading_capital_usd=40)  # capital*price => notional << 50
    monkeypatch.setattr(
        live_strategy_engine.crypto_carry, "compute_funding_rate_carry_signal",
        lambda symbol: {"note": "funding bueno"},
    )
    monkeypatch.setattr(live_strategy_engine, "_spot_price_usd", lambda symbol: 60_000.0)

    assert live_strategy_engine.build_real_intent(temp_db, "crypto_carry_btcusdt") is None


def test_con_posicion_abierta_y_señal_viva_no_hace_nada(temp_db, monkeypatch):
    _make_live(temp_db, "crypto_carry_btcusdt")
    db.insert_strategy_position(
        temp_db, strategy_name="crypto_carry_btcusdt", mode="real",
        legs=[{"broker": "Binance", "market": "spot", "symbol": "BTCUSDT", "side": "buy", "quantity": 0.1, "price_usd": 60000.0}],
        capital_usd=6000.0,
    )
    monkeypatch.setattr(
        live_strategy_engine.crypto_carry, "compute_funding_rate_carry_signal",
        lambda symbol: {"note": "sigue rindiendo"},
    )
    assert live_strategy_engine.build_real_intent(temp_db, "crypto_carry_btcusdt") is None


def test_carry_ampliacion_abre_2_patas_con_el_symbol_correcto(temp_db, monkeypatch):
    _setup_binance_account(temp_db)
    monkeypatch.setattr(broker_client.BinanceAdapter, "get_lot_size_filters", lambda self, **kw: (0.00001, 5.0))
    _make_live(temp_db, "crypto_carry_adausdt", capital_pct=100.0)
    db.update_app_settings(temp_db, auto_trading_capital_usd=1_000)
    monkeypatch.setattr(
        live_strategy_engine.crypto_carry, "compute_funding_rate_carry_signal",
        lambda symbol: {"note": "funding bueno"},
    )
    monkeypatch.setattr(live_strategy_engine, "_spot_price_usd", lambda symbol: 1.0)

    intent = live_strategy_engine.build_real_intent(temp_db, "crypto_carry_adausdt")

    assert intent["action"] == "open"
    assert {leg["symbol"] for leg in intent["legs"]} == {"ADAUSDT"}


def test_con_posicion_abierta_y_señal_muerta_cierra(temp_db, monkeypatch):
    _make_live(temp_db, "crypto_carry_btcusdt")
    position = db.insert_strategy_position(
        temp_db, strategy_name="crypto_carry_btcusdt", mode="real",
        legs=[
            {"broker": "Binance", "market": "spot", "symbol": "BTCUSDT", "side": "buy", "quantity": 0.1, "price_usd": 60000.0},
            {"broker": "Binance", "market": "futuros", "symbol": "BTCUSDT", "side": "sell", "quantity": 0.1, "price_usd": 60000.0},
        ],
        capital_usd=6000.0,
    )
    monkeypatch.setattr(live_strategy_engine.crypto_carry, "compute_funding_rate_carry_signal", lambda symbol: None)

    intent = live_strategy_engine.build_real_intent(temp_db, "crypto_carry_btcusdt")

    assert intent["action"] == "close"
    assert intent["position_id"] == position["id"]
    sides = {leg["market"]: leg["side"] for leg in intent["legs"]}
    assert sides == {"spot": "sell", "futuros": "buy"}  # reversa exacta de como se abrio
