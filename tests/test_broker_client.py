"""Tests del soporte Multi-Broker (ver conversacion, portfolio/broker_client.py).
Corre contra una DB temporal propia (mismo patron que tests/test_paper_trading.py),
NUNCA pega a una API de bróker real -- requests.post/requests.get se
monkeypatchean siempre, incluso en los tests de los adapters "best effort"
(IOL/PPI): esos adapters estan implementados contra documentacion publica
pero nunca probados con credenciales reales, asi que un test real contra
internet no demostraria nada y violaria la regla de nunca pegarle a una API
externa desde un test."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from portfolio import broker_client, db


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_broker_client.db")
    monkeypatch.setattr(db, "DB_PATH", db_path)
    conn = db.get_connection(db_path)
    yield conn
    conn.close()


@pytest.fixture
def manager():
    return broker_client.MultiBrokerManager()


def _fake_response(json_data=None, status_ok=True):
    def _raise():
        if not status_ok:
            raise RuntimeError("HTTP error simulado")
    return SimpleNamespace(json=lambda: json_data or {}, raise_for_status=_raise)


# --- CRUD + masking -----------------------------------------------------------

def test_add_account_devuelve_credenciales_enmascaradas(manager, temp_db):
    account = manager.add_account(
        temp_db, broker_name="IOL", alias="Principal", api_key="usuario123", api_secret="superSecreto123",
    )
    assert account["api_key"] == "****o123"
    assert account["api_secret"] == "****o123"
    assert account["broker_name"] == "IOL"
    assert account["is_active"] == 1


def test_add_account_broker_invalido_rechaza(manager, temp_db):
    with pytest.raises(ValueError):
        manager.add_account(temp_db, broker_name="Binance", api_key="x", api_secret="y")


def test_list_accounts_nunca_expone_secreto_completo(manager, temp_db):
    manager.add_account(temp_db, broker_name="PPI", api_key="clave-larga-123", api_secret="secreto-largo-456")
    accounts = manager.list_accounts(temp_db)
    assert len(accounts) == 1
    assert "clave-larga-123" not in accounts[0]["api_key"]
    assert "secreto-largo-456" not in accounts[0]["api_secret"]


def test_mask_secret_corto_se_enmascara_completo():
    assert broker_client._mask_secret("ab") == "****"
    assert broker_client._mask_secret(None) is None
    assert broker_client._mask_secret("") is None


def test_update_account_is_active_toggle(manager, temp_db):
    account = manager.add_account(temp_db, broker_name="Balanz", api_key="k", api_secret="s")
    updated = manager.update_account(temp_db, account["id"], is_active=False)
    assert updated["is_active"] == 0
    reactivated = manager.set_active(temp_db, account["id"], True)
    assert reactivated["is_active"] == 1


def test_update_account_broker_invalido_rechaza(manager, temp_db):
    account = manager.add_account(temp_db, broker_name="Matrix", api_key="k", api_secret="s")
    with pytest.raises(ValueError):
        manager.update_account(temp_db, account["id"], broker_name="NoExiste")


def test_delete_account(manager, temp_db):
    account = manager.add_account(temp_db, broker_name="IOL", api_key="k", api_secret="s")
    manager.delete_account(temp_db, account["id"])
    assert manager.list_accounts(temp_db) == []


# --- test_connection: honestidad del status para real vs. stub ---------------

def test_test_connection_cuenta_inexistente(manager, temp_db):
    result = manager.test_connection(temp_db, 9999)
    assert result["status"] == broker_client.CONNECTION_FAILED


def test_test_connection_balanz_es_pending_integration(manager, temp_db):
    account = manager.add_account(temp_db, broker_name="Balanz", api_key="k", api_secret="s")
    result = manager.test_connection(temp_db, account["id"])
    assert result["status"] == broker_client.CONNECTION_PENDING
    assert "Balanz" in result["detail"]


def test_test_connection_matrix_es_pending_integration(manager, temp_db):
    account = manager.add_account(temp_db, broker_name="Matrix", api_key="k", api_secret="s")
    result = manager.test_connection(temp_db, account["id"])
    assert result["status"] == broker_client.CONNECTION_PENDING


def test_test_connection_iol_ok_con_token_mockeado(manager, temp_db, monkeypatch):
    monkeypatch.setattr(
        broker_client.requests, "post",
        lambda *a, **kw: _fake_response({"access_token": "tok-123"}),
    )
    account = manager.add_account(temp_db, broker_name="IOL", api_key="usuario", api_secret="pass")
    result = manager.test_connection(temp_db, account["id"])
    assert result["status"] == broker_client.CONNECTION_OK


def test_test_connection_iol_sin_access_token_reconocido_falla(manager, temp_db, monkeypatch):
    monkeypatch.setattr(
        broker_client.requests, "post",
        lambda *a, **kw: _fake_response({"algo_inesperado": "x"}),
    )
    account = manager.add_account(temp_db, broker_name="IOL", api_key="usuario", api_secret="pass")
    result = manager.test_connection(temp_db, account["id"])
    assert result["status"] == broker_client.CONNECTION_FAILED


def test_test_connection_iol_http_error_falla_sin_romper(manager, temp_db, monkeypatch):
    monkeypatch.setattr(
        broker_client.requests, "post",
        lambda *a, **kw: _fake_response(status_ok=False),
    )
    account = manager.add_account(temp_db, broker_name="IOL", api_key="usuario", api_secret="pass")
    result = manager.test_connection(temp_db, account["id"])
    assert result["status"] == broker_client.CONNECTION_FAILED


def test_test_connection_ppi_ok_con_login_mockeado(manager, temp_db, monkeypatch):
    monkeypatch.setattr(
        broker_client.requests, "post",
        lambda *a, **kw: _fake_response({"accessToken": "tok-123", "refreshToken": "ref-456"}),
    )
    account = manager.add_account(temp_db, broker_name="PPI", api_key="k", api_secret="s")
    result = manager.test_connection(temp_db, account["id"])
    assert result["status"] == broker_client.CONNECTION_OK


def test_test_connection_ppi_sin_access_token_reconocido_falla(manager, temp_db, monkeypatch):
    monkeypatch.setattr(broker_client.requests, "post", lambda *a, **kw: _fake_response({"algo_inesperado": "x"}))
    account = manager.add_account(temp_db, broker_name="PPI", api_key="k", api_secret="s")
    result = manager.test_connection(temp_db, account["id"])
    assert result["status"] == broker_client.CONNECTION_FAILED


def test_ppi_get_balance_sin_account_number_falla_explicito(temp_db, manager):
    # account_number (numero de cuenta en PPI, no la ApiKey) es obligatorio
    # para balance/tenencias -- nunca se asume ni se inventa.
    account = manager.add_account(temp_db, broker_name="PPI", api_key="k", api_secret="s")
    adapter = broker_client.PPIAdapter(db.get_broker_account(temp_db, account["id"]))
    with pytest.raises(ValueError, match="Número de cuenta"):
        adapter.get_balance()


def test_ppi_get_balance_ok_con_login_y_balance_mockeados(temp_db, manager, monkeypatch):
    monkeypatch.setattr(
        broker_client.requests, "post",
        lambda *a, **kw: _fake_response({"accessToken": "tok-123", "refreshToken": "ref-456"}),
    )
    monkeypatch.setattr(broker_client.requests, "get", lambda *a, **kw: _fake_response({"saldo": 500}))
    account = manager.add_account(temp_db, broker_name="PPI", api_key="k", api_secret="s", account_number="12345")
    adapter = broker_client.PPIAdapter(db.get_broker_account(temp_db, account["id"]))
    assert adapter.get_balance() == {"saldo": 500}
    assert adapter.get_holdings() == {"saldo": 500}


# --- agregacion unificada: resiliencia ante fallos parciales ------------------

def test_unified_balance_agrega_solo_cuentas_activas(manager, temp_db, monkeypatch):
    monkeypatch.setattr(broker_client.requests, "post", lambda *a, **kw: _fake_response({"access_token": "tok"}))
    monkeypatch.setattr(broker_client.requests, "get", lambda *a, **kw: _fake_response({"saldo": 1000}))
    active = manager.add_account(temp_db, broker_name="IOL", api_key="u", api_secret="p")
    inactive = manager.add_account(temp_db, broker_name="IOL", api_key="u2", api_secret="p2", is_active=False)
    result = manager.get_unified_balance(temp_db)
    ids = [a["account_id"] for a in result["accounts"]]
    assert active["id"] in ids
    assert inactive["id"] not in ids


def test_unified_balance_una_cuenta_rota_no_rompe_la_agregacion(manager, temp_db, monkeypatch):
    # IOL: ok. PPI: sin account_number configurado -- debe quedar como
    # "error" (ver test_ppi_get_balance_sin_account_number_falla_explicito),
    # nunca levantar la excepcion hasta el caller.
    monkeypatch.setattr(broker_client.requests, "post", lambda *a, **kw: _fake_response({"access_token": "tok"}))
    monkeypatch.setattr(broker_client.requests, "get", lambda *a, **kw: _fake_response({"saldo": 1000}))
    manager.add_account(temp_db, broker_name="IOL", api_key="u", api_secret="p")
    manager.add_account(temp_db, broker_name="PPI", api_key="u2", api_secret="p2")

    result = manager.get_unified_balance(temp_db)
    by_broker = {a["broker_name"]: a for a in result["accounts"]}
    assert by_broker["IOL"]["error"] is None
    assert by_broker["IOL"]["balance"] == {"saldo": 1000}
    assert by_broker["PPI"]["error"] is not None
    assert by_broker["PPI"]["balance"] is None


def test_unified_holdings_broker_sin_documentacion_queda_como_error(manager, temp_db):
    manager.add_account(temp_db, broker_name="Balanz", api_key="k", api_secret="s")
    result = manager.get_unified_holdings(temp_db)
    assert result["accounts"][0]["error"] is not None
    assert result["accounts"][0]["holdings"] is None


def test_unified_balance_sin_ninguna_cuenta_devuelve_lista_vacia(manager, temp_db):
    result = manager.get_unified_balance(temp_db)
    assert result == {"accounts": []}


# --- interruptor maestro de ejecución real (ver conversación) -----------------

def test_auto_trading_arranca_deshabilitado_por_default(temp_db):
    assert db.get_app_settings(temp_db)["auto_trading_enabled"] == 0
    with pytest.raises(broker_client.AutoTradingDisabledError):
        broker_client.ensure_auto_trading_enabled(temp_db)


def test_auto_trading_no_tira_una_vez_habilitado(temp_db):
    db.update_app_settings(temp_db, auto_trading_enabled=1)
    broker_client.ensure_auto_trading_enabled(temp_db)  # no debe levantar nada


def test_auto_trading_se_puede_volver_a_apagar(temp_db):
    db.update_app_settings(temp_db, auto_trading_enabled=1)
    db.update_app_settings(temp_db, auto_trading_enabled=0)
    with pytest.raises(broker_client.AutoTradingDisabledError):
        broker_client.ensure_auto_trading_enabled(temp_db)


# --- tope general de exposición (ver conversación: "tope general, no por orden") --

def test_limite_modo_pct_por_default(temp_db):
    db.update_app_settings(temp_db, auto_trading_limit_pct=20)
    assert broker_client.compute_auto_trading_limit_ars(temp_db, 1_000_000) == 200_000


def test_limite_modo_fixed(temp_db):
    db.update_app_settings(temp_db, auto_trading_limit_mode="fixed", auto_trading_limit_ars=500_000)
    assert broker_client.compute_auto_trading_limit_ars(temp_db, 999_999_999) == 500_000


def test_limite_sin_configurar_da_cero_nunca_un_numero_inventado(temp_db):
    assert broker_client.compute_auto_trading_limit_ars(temp_db, 1_000_000) == 0.0
    db.update_app_settings(temp_db, auto_trading_limit_mode="fixed")
    assert broker_client.compute_auto_trading_limit_ars(temp_db, 1_000_000) == 0.0


# --- precio de ejecución por cuenta de bróker específica (ver conversación) ---

def test_ppi_get_current_price_ok_mockeado(temp_db, manager, monkeypatch):
    monkeypatch.setattr(
        broker_client.requests, "post",
        lambda *a, **kw: _fake_response({"accessToken": "tok", "refreshToken": "ref"}),
    )
    monkeypatch.setattr(broker_client.requests, "get", lambda *a, **kw: _fake_response({"price": 123.45}))
    account = manager.add_account(temp_db, broker_name="PPI", api_key="k", api_secret="s", account_number="1")
    adapter = broker_client.PPIAdapter(db.get_broker_account(temp_db, account["id"]))
    result = adapter.get_current_price("GGAL", "accion")
    assert result == {"price": 123.45, "bid": None, "ask": None}


def test_ppi_get_current_price_tipo_no_mapeado_falla_explicito(temp_db, manager):
    account = manager.add_account(temp_db, broker_name="PPI", api_key="k", api_secret="s", account_number="1")
    adapter = broker_client.PPIAdapter(db.get_broker_account(temp_db, account["id"]))
    with pytest.raises(ValueError, match="sin mapeo"):
        adapter.get_current_price("USD", "moneda")


def test_iol_get_current_price_ok_mockeado(temp_db, manager, monkeypatch):
    monkeypatch.setattr(
        broker_client.requests, "post",
        lambda *a, **kw: _fake_response({"access_token": "tok"}),
    )
    monkeypatch.setattr(
        broker_client.requests, "get",
        lambda *a, **kw: _fake_response({
            "ultimoPrecio": 50.5,
            "puntas": [{"precioCompra": 50.0, "precioVenta": 51.0}],
        }),
    )
    account = manager.add_account(temp_db, broker_name="IOL", api_key="usuario", api_secret="pass")
    adapter = broker_client.IOLAdapter(db.get_broker_account(temp_db, account["id"]))
    result = adapter.get_current_price("GGAL", "accion")
    assert result == {"price": 50.5, "bid": 50.0, "ask": 51.0}
