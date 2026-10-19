"""Ejecución REAL de órdenes (ver conversación: "empezá a desarrollar la
ejecución real") -- place_real_order es el único punto de entrada, y estos
tests existen sobre todo para probar que los 2 gates (interruptor maestro +
tope de exposición) son imposibles de saltear, no para probar PPI en sí
(eso ya lo cubre test_broker_client.py). NUNCA pega a una API real --
requests.post/get se monkeypatchean siempre."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from portfolio import broker_client, db, order_execution


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_order_execution.db")
    monkeypatch.setattr(db, "DB_PATH", db_path)
    conn = db.get_connection(db_path)
    yield conn
    conn.close()


def _fake_response(json_data):
    return SimpleNamespace(json=lambda: json_data, raise_for_status=lambda: None)


def _setup_ppi_instrument(conn, *, account_number="1", is_active=True):
    manager = broker_client.MultiBrokerManager()
    account = manager.add_account(
        conn, broker_name="PPI", api_key="k", api_secret="s", account_number=account_number, is_active=is_active,
    )
    instrument_id = db.add_instrument(
        conn, symbol="GGAL", type_="accion", market="byma", price_source="data912",
        broker_account_id=account["id"],
    )
    return instrument_id, account["id"]


def _base_kwargs(instrument_id, **overrides):
    kwargs = dict(
        instrument_id=instrument_id, operacion="compra", cantidad=10, precio=100.0, patrimonio_ars=1_000_000,
    )
    kwargs.update(overrides)
    return kwargs


# --- Gate 1: interruptor maestro ----------------------------------------------

def test_interruptor_apagado_bloquea_antes_de_todo(temp_db, monkeypatch):
    instrument_id, _ = _setup_ppi_instrument(temp_db)
    post_calls = []
    monkeypatch.setattr(broker_client.requests, "post", lambda *a, **kw: post_calls.append(1))

    with pytest.raises(broker_client.AutoTradingDisabledError):
        order_execution.place_real_order(temp_db, **_base_kwargs(instrument_id))

    assert post_calls == []  # nunca llego a tocar la red
    assert db.list_auto_trading_orders(temp_db) == []  # ni se persistio nada


# --- Gate 2: instrumento sin cuenta de broker ---------------------------------

def test_sin_broker_account_id_bloquea(temp_db):
    db.update_app_settings(temp_db, auto_trading_enabled=1, auto_trading_limit_mode="fixed", auto_trading_limit_ars=10**9)
    instrument_id = db.add_instrument(temp_db, symbol="GGAL", type_="accion", market="byma", price_source="data912")

    with pytest.raises(order_execution.OrderExecutionError, match="sin cuenta de bróker"):
        order_execution.place_real_order(temp_db, **_base_kwargs(instrument_id))

    assert db.list_auto_trading_orders(temp_db) == []


def test_cuenta_de_broker_inactiva_bloquea(temp_db):
    db.update_app_settings(temp_db, auto_trading_enabled=1, auto_trading_limit_mode="fixed", auto_trading_limit_ars=10**9)
    instrument_id, _ = _setup_ppi_instrument(temp_db, is_active=False)

    with pytest.raises(order_execution.OrderExecutionError, match="inactiva"):
        order_execution.place_real_order(temp_db, **_base_kwargs(instrument_id))


# --- Gate 3: tope de exposicion ------------------------------------------------

def test_tope_modo_fixed_bloquea_sin_tocar_la_red(temp_db, monkeypatch):
    db.update_app_settings(temp_db, auto_trading_enabled=1, auto_trading_limit_mode="fixed", auto_trading_limit_ars=500)
    instrument_id, _ = _setup_ppi_instrument(temp_db)
    post_calls = []
    monkeypatch.setattr(broker_client.requests, "post", lambda *a, **kw: post_calls.append(1))

    # 10 * 100 = 1000 ARS > tope de 500
    with pytest.raises(order_execution.OrderExecutionError, match="superaría el tope"):
        order_execution.place_real_order(temp_db, **_base_kwargs(instrument_id))

    assert post_calls == []
    assert db.list_auto_trading_orders(temp_db) == []


def test_tope_modo_pct_bloquea_por_debajo_del_1_por_ciento(temp_db, monkeypatch):
    # 1% de 1.000.000 (patrimonio_ars pasado en _base_kwargs) = 10.000 ARS de
    # tope -- la orden de 10*100=1000 ARS entra, pero una de 10*1500=15.000
    # ARS no.
    db.update_app_settings(temp_db, auto_trading_enabled=1, auto_trading_limit_mode="pct", auto_trading_limit_pct=1)
    instrument_id, _ = _setup_ppi_instrument(temp_db)
    post_calls = []
    monkeypatch.setattr(broker_client.requests, "post", lambda *a, **kw: post_calls.append(1))

    with pytest.raises(order_execution.OrderExecutionError, match="superaría el tope"):
        order_execution.place_real_order(temp_db, **_base_kwargs(instrument_id, precio=1500.0))

    assert post_calls == []


def test_exposicion_ya_confirmada_cuenta_para_el_tope(temp_db):
    db.update_app_settings(temp_db, auto_trading_enabled=1, auto_trading_limit_mode="fixed", auto_trading_limit_ars=1500)
    instrument_id, account_id = _setup_ppi_instrument(temp_db)
    # Simula una orden ya confirmada anteriormente por 1000 ARS -- una nueva
    # de 1000 ARS mas haria 2000, por encima del tope de 1500.
    db.add_auto_trading_order(
        temp_db, instrument_id=instrument_id, broker_account_id=account_id, symbol="GGAL",
        operacion="compra", cantidad=10, precio=100.0, monto_ars=1000.0,
        estado="confirmada", external_id="prev-1",
    )

    with pytest.raises(order_execution.OrderExecutionError, match="superaría el tope"):
        order_execution.place_real_order(temp_db, **_base_kwargs(instrument_id))


def test_ordenes_rechazadas_o_con_error_no_cuentan_para_el_tope(temp_db):
    db.update_app_settings(temp_db, auto_trading_enabled=1, auto_trading_limit_mode="fixed", auto_trading_limit_ars=1500)
    instrument_id, account_id = _setup_ppi_instrument(temp_db)
    db.add_auto_trading_order(
        temp_db, instrument_id=instrument_id, broker_account_id=account_id, symbol="GGAL",
        operacion="compra", cantidad=10, precio=100.0, monto_ars=5000.0,
        estado="error", external_id="prev-error", error_detalle="timeout",
    )
    assert db.sum_confirmed_auto_trading_exposure_ars(temp_db) == 0.0


# --- Flujo completo (mockeado) -------------------------------------------------

def test_orden_exitosa_se_confirma_y_persiste(temp_db, monkeypatch):
    db.update_app_settings(temp_db, auto_trading_enabled=1, auto_trading_limit_mode="fixed", auto_trading_limit_ars=10**6)
    instrument_id, account_id = _setup_ppi_instrument(temp_db)
    monkeypatch.setattr(
        broker_client.requests, "post",
        lambda url, **kw: (
            _fake_response({"accessToken": "tok", "refreshToken": "ref"}) if "LoginApi" in url
            else _fake_response({"disclaimers": [{"code": "D1"}]}) if "Budget" in url
            else _fake_response({"orderId": "ABC123"})
        ),
    )

    order = order_execution.place_real_order(temp_db, **_base_kwargs(instrument_id))

    assert order["estado"] == "confirmada"
    assert order["symbol"] == "GGAL"
    assert order["monto_ars"] == 1000.0
    assert order["ppi_order_id"] == "ABC123"
    assert db.sum_confirmed_auto_trading_exposure_ars(temp_db) == 1000.0


def test_falla_del_broker_persiste_error_y_relanza(temp_db, monkeypatch):
    db.update_app_settings(temp_db, auto_trading_enabled=1, auto_trading_limit_mode="fixed", auto_trading_limit_ars=10**6)
    instrument_id, account_id = _setup_ppi_instrument(temp_db)

    def fake_post(url, **kw):
        if "LoginApi" in url:
            return _fake_response({"accessToken": "tok", "refreshToken": "ref"})
        raise RuntimeError("PPI no responde")

    monkeypatch.setattr(broker_client.requests, "post", fake_post)

    with pytest.raises(RuntimeError, match="PPI no responde"):
        order_execution.place_real_order(temp_db, **_base_kwargs(instrument_id))

    orders = db.list_auto_trading_orders(temp_db)
    assert len(orders) == 1
    assert orders[0]["estado"] == "error"
    assert "PPI no responde" in orders[0]["error_detalle"]
    assert db.sum_confirmed_auto_trading_exposure_ars(temp_db) == 0.0  # un error nunca cuenta como exposicion


def test_broker_sin_ejecucion_implementada_persiste_error(temp_db):
    # IOL no tiene get_order_budget/confirm_order implementados -- debe
    # heredar el NotImplementedError de la base, no romper distinto.
    db.update_app_settings(temp_db, auto_trading_enabled=1, auto_trading_limit_mode="fixed", auto_trading_limit_ars=10**6)
    manager = broker_client.MultiBrokerManager()
    account = manager.add_account(temp_db, broker_name="IOL", api_key="u", api_secret="p")
    instrument_id = db.add_instrument(
        temp_db, symbol="GGAL", type_="accion", market="byma", price_source="data912",
        broker_account_id=account["id"],
    )

    with pytest.raises(NotImplementedError, match="IOL"):
        order_execution.place_real_order(temp_db, **_base_kwargs(instrument_id))

    orders = db.list_auto_trading_orders(temp_db)
    assert len(orders) == 1
    assert orders[0]["estado"] == "error"


def test_operacion_invalida_rechaza_explicito(temp_db):
    db.update_app_settings(temp_db, auto_trading_enabled=1, auto_trading_limit_mode="fixed", auto_trading_limit_ars=10**6)
    instrument_id, _ = _setup_ppi_instrument(temp_db)
    with pytest.raises(order_execution.OrderExecutionError, match="compra.*venta"):
        order_execution.place_real_order(temp_db, **_base_kwargs(instrument_id, operacion="mantener"))
