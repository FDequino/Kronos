"""Precio de EJECUCIÓN por cuenta de bróker específica (ver conversación:
"que tome los precios de la plataforma que usa" -- si opera en PPI, el de
PPI; si en IOL, el de IOL). Nunca pega a una API real -- requests.get/post
se monkeypatchean siempre, mismo criterio que test_broker_client.py."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from portfolio import broker_client, db, fetchers


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_broker_account_pricing.db")
    monkeypatch.setattr(db, "DB_PATH", db_path)
    conn = db.get_connection(db_path)
    yield conn
    conn.close()


def _fake_response(json_data):
    return SimpleNamespace(json=lambda: json_data, raise_for_status=lambda: None)


def _make_ppi_account(conn):
    manager = broker_client.MultiBrokerManager()
    account = manager.add_account(conn, broker_name="PPI", api_key="k", api_secret="s", account_number="1")
    return account["id"]


def test_update_quote_usa_broker_account_id_si_esta_seteado(temp_db, monkeypatch):
    monkeypatch.setattr(
        broker_client.requests, "post",
        lambda *a, **kw: _fake_response({"accessToken": "tok", "refreshToken": "ref"}),
    )
    monkeypatch.setattr(broker_client.requests, "get", lambda *a, **kw: _fake_response({"price": 999.0}))

    account_id = _make_ppi_account(temp_db)
    instrument_id = db.add_instrument(
        temp_db, symbol="GGAL", type_="accion", market="byma", price_source="data912",
        broker_account_id=account_id,
    )
    instrument = db.get_instrument(temp_db, instrument_id)

    quote = fetchers.update_quote(temp_db, instrument)

    assert quote["price"] == 999.0
    saved = db.get_quote(temp_db, instrument_id)
    assert saved["price"] == 999.0
    assert saved["source"] == "ppi"


def test_update_quote_sin_broker_account_id_sigue_usando_price_source(temp_db, monkeypatch):
    # data912 real jamas se llama en un test -- se monkeypatchea la funcion
    # de fetch, no requests, porque fetch_data912_quote no es tan trivial
    # de mockear a nivel HTTP como los adapters de broker.
    monkeypatch.setitem(fetchers._QUOTE_FETCHERS, "data912", lambda instrument: {"price": 111.0, "bid": None, "ask": None})

    instrument_id = db.add_instrument(temp_db, symbol="GGAL", type_="accion", market="byma", price_source="data912")
    instrument = db.get_instrument(temp_db, instrument_id)

    quote = fetchers.update_quote(temp_db, instrument)

    assert quote["price"] == 111.0
    saved = db.get_quote(temp_db, instrument_id)
    assert saved["source"] == "data912"


def test_instrument_broker_account_id_requiere_cuenta_existente(temp_db):
    # FK real (ON DELETE SET NULL, ver schema.sql) -- ni siquiera se puede
    # insertar un instrumento apuntando a una cuenta que no existe.
    with pytest.raises(Exception):
        db.add_instrument(
            temp_db, symbol="GGAL", type_="accion", market="byma", price_source="data912",
            broker_account_id=9999,
        )


def test_borrar_la_cuenta_de_broker_nunca_rompe_el_instrumento(temp_db, monkeypatch):
    # ON DELETE SET NULL (ver schema.sql): borrar la cuenta vuelve el
    # instrumento a su price_source de siempre, nunca lo deja apuntando a
    # una cuenta fantasma.
    monkeypatch.setitem(fetchers._QUOTE_FETCHERS, "data912", lambda instrument: {"price": 42.0, "bid": None, "ask": None})

    account_id = _make_ppi_account(temp_db)
    instrument_id = db.add_instrument(
        temp_db, symbol="GGAL", type_="accion", market="byma", price_source="data912",
        broker_account_id=account_id,
    )
    db.delete_broker_account(temp_db, account_id)

    instrument = db.get_instrument(temp_db, instrument_id)
    assert instrument["broker_account_id"] is None

    quote = fetchers.update_quote(temp_db, instrument)
    assert quote["price"] == 42.0
