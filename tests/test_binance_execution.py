"""place_real_binance_order -- mismo criterio que test_order_execution.py:
probar los gates (interruptor maestro + tope de exposición en USD +
ambigüedad de cuenta), nunca Binance en sí (eso ya lo cubre
test_broker_client.py). NUNCA pega a una API real."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from portfolio import binance_execution, broker_client, db


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_binance_execution.db")
    monkeypatch.setattr(db, "DB_PATH", db_path)
    conn = db.get_connection(db_path)
    yield conn
    conn.close()


def _fake_response(json_data):
    return SimpleNamespace(json=lambda: json_data, raise_for_status=lambda: None)


def _setup_binance_account(conn, *, is_active=True):
    manager = broker_client.MultiBrokerManager()
    account = manager.add_account(
        conn, broker_name="Binance", api_key="k", api_secret="s", is_active=is_active,
    )
    return account["id"]


def _base_kwargs(**overrides):
    kwargs = dict(market="spot", symbol="BTCUSDT", side="buy", cantidad=0.1, precio=60000.0, patrimonio_usd=100_000)
    kwargs.update(overrides)
    return kwargs


def test_interruptor_apagado_bloquea_antes_de_todo(temp_db, monkeypatch):
    _setup_binance_account(temp_db)
    post_calls = []
    monkeypatch.setattr(broker_client.requests, "post", lambda *a, **kw: post_calls.append(1))

    with pytest.raises(broker_client.AutoTradingDisabledError):
        binance_execution.place_real_binance_order(temp_db, **_base_kwargs())

    assert post_calls == []
    assert db.list_binance_orders(temp_db) == []


def test_sin_cuenta_binance_activa_bloquea(temp_db):
    db.update_app_settings(temp_db, auto_trading_enabled=1, auto_trading_limit_mode="fixed", auto_trading_limit_usd=10**6)
    with pytest.raises(binance_execution.BinanceExecutionError, match="ninguna cuenta Binance activa"):
        binance_execution.place_real_binance_order(temp_db, **_base_kwargs())


def test_2_cuentas_binance_activas_es_ambiguo(temp_db):
    db.update_app_settings(temp_db, auto_trading_enabled=1, auto_trading_limit_mode="fixed", auto_trading_limit_usd=10**6)
    _setup_binance_account(temp_db)
    _setup_binance_account(temp_db)
    with pytest.raises(binance_execution.BinanceExecutionError, match="2 cuentas Binance activas"):
        binance_execution.place_real_binance_order(temp_db, **_base_kwargs())


def test_market_invalido_rechaza_explicito(temp_db):
    db.update_app_settings(temp_db, auto_trading_enabled=1, auto_trading_limit_mode="fixed", auto_trading_limit_usd=10**6)
    _setup_binance_account(temp_db)
    with pytest.raises(binance_execution.BinanceExecutionError, match="spot.*futuros"):
        binance_execution.place_real_binance_order(temp_db, **_base_kwargs(market="margen"))


def test_tope_modo_fixed_bloquea_sin_tocar_la_red(temp_db, monkeypatch):
    db.update_app_settings(temp_db, auto_trading_enabled=1, auto_trading_limit_mode="fixed", auto_trading_limit_usd=100)
    _setup_binance_account(temp_db)
    post_calls = []
    monkeypatch.setattr(broker_client.requests, "post", lambda *a, **kw: post_calls.append(1))

    # 0.1 * 60000 = 6000 USD > tope de 100
    with pytest.raises(binance_execution.BinanceExecutionError, match="superaría el tope"):
        binance_execution.place_real_binance_order(temp_db, **_base_kwargs())

    assert post_calls == []
    assert db.list_binance_orders(temp_db) == []


def test_exposicion_ya_confirmada_cuenta_para_el_tope(temp_db):
    db.update_app_settings(temp_db, auto_trading_enabled=1, auto_trading_limit_mode="fixed", auto_trading_limit_usd=6500)
    account_id = _setup_binance_account(temp_db)
    db.add_binance_order(
        temp_db, broker_account_id=account_id, market="spot", symbol="BTCUSDT", side="buy",
        cantidad=0.1, precio=60000.0, monto_usd=6000.0, estado="confirmada", external_id="prev-1",
    )
    with pytest.raises(binance_execution.BinanceExecutionError, match="superaría el tope"):
        binance_execution.place_real_binance_order(temp_db, **_base_kwargs(cantidad=0.01))


def test_orden_spot_exitosa_se_confirma_y_persiste(temp_db, monkeypatch):
    db.update_app_settings(temp_db, auto_trading_enabled=1, auto_trading_limit_mode="fixed", auto_trading_limit_usd=10**6)
    _setup_binance_account(temp_db)
    monkeypatch.setattr(broker_client.requests, "post", lambda url, **kw: _fake_response({"orderId": 123456}))

    order = binance_execution.place_real_binance_order(temp_db, **_base_kwargs())

    assert order["estado"] == "confirmada"
    assert order["market"] == "spot"
    assert order["monto_usd"] == 6000.0
    assert order["binance_order_id"] == "123456"
    assert db.sum_confirmed_binance_exposure_usd(temp_db) == 6000.0


def test_orden_futuros_exitosa_se_confirma(temp_db, monkeypatch):
    db.update_app_settings(temp_db, auto_trading_enabled=1, auto_trading_limit_mode="fixed", auto_trading_limit_usd=10**6)
    _setup_binance_account(temp_db)
    monkeypatch.setattr(broker_client.requests, "post", lambda url, **kw: _fake_response({"orderId": 999}))

    order = binance_execution.place_real_binance_order(temp_db, **_base_kwargs(market="futuros", side="sell"))

    assert order["estado"] == "confirmada"
    assert order["market"] == "futuros"
    assert order["side"] == "sell"


def test_falla_del_broker_persiste_error_y_relanza(temp_db, monkeypatch):
    db.update_app_settings(temp_db, auto_trading_enabled=1, auto_trading_limit_mode="fixed", auto_trading_limit_usd=10**6)
    _setup_binance_account(temp_db)
    monkeypatch.setattr(broker_client.requests, "post", lambda url, **kw: (_ for _ in ()).throw(RuntimeError("Binance no responde")))

    with pytest.raises(RuntimeError, match="Binance no responde"):
        binance_execution.place_real_binance_order(temp_db, **_base_kwargs())

    orders = db.list_binance_orders(temp_db)
    assert len(orders) == 1
    assert orders[0]["estado"] == "error"
    assert db.sum_confirmed_binance_exposure_usd(temp_db) == 0.0
