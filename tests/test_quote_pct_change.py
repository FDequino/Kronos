"""Tests de pct_change en quotes_latest (ver conversación/auditoría 19/10/2026,
hallazgo 1.7: "la variación del día no coincide entre pantallas" -- el
detalle de instrumento calculaba el % contra el último close de
price_history, que puede quedar días atrás, ya que solo se backfillea al
correr un pronóstico. Confirmado en producción: price_history de QQQ/GGAL
con 10-11 días de atraso. Ahora se persiste el pct_change REAL que ya trae
data912 (mismo que usa el catálogo de Instrumentos), evitando ese cálculo
contra un cierre viejo. Nunca red real -- requests.get siempre mockeado."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from portfolio import db, fetchers


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_quote_pct_change.db")
    monkeypatch.setattr(db, "DB_PATH", db_path)
    conn = db.get_connection(db_path)
    yield conn
    conn.close()


def _fake_live_response(rows):
    return SimpleNamespace(json=lambda: rows, raise_for_status=lambda: None)


# --- db.upsert_quote/get_quote: pct_change persiste y se puede omitir -------

def test_upsert_quote_persiste_pct_change(temp_db):
    iid = db.add_instrument(temp_db, symbol="GGAL", type_="accion", market="byma", price_source="data912")
    db.upsert_quote(temp_db, iid, 5690.0, source="data912", bid=5680.0, ask=5685.0, pct_change=-4.37)
    quote = db.get_quote(temp_db, iid)
    assert quote["pct_change"] == -4.37


def test_upsert_quote_sin_pct_change_queda_none(temp_db):
    iid = db.add_instrument(temp_db, symbol="GGAL", type_="accion", market="byma", price_source="data912")
    db.upsert_quote(temp_db, iid, 5690.0, source="data912")
    quote = db.get_quote(temp_db, iid)
    assert quote["pct_change"] is None


def test_upsert_quote_actualiza_pct_change_en_conflict(temp_db):
    iid = db.add_instrument(temp_db, symbol="GGAL", type_="accion", market="byma", price_source="data912")
    db.upsert_quote(temp_db, iid, 5690.0, source="data912", pct_change=-4.37)
    db.upsert_quote(temp_db, iid, 5700.0, source="data912", pct_change=1.2)
    quote = db.get_quote(temp_db, iid)
    assert quote["pct_change"] == 1.2


# --- fetch_data912_quote: extrae pct_change del payload live ----------------

def test_fetch_data912_quote_extrae_pct_change(monkeypatch):
    instrument = {"symbol": "GGAL", "type": "accion", "price_source_params": None}
    monkeypatch.setattr(
        fetchers, "fetch_data912_live",
        lambda endpoint: [{"symbol": "GGAL", "c": 5690.0, "px_bid": 5680.0, "px_ask": 5685.0, "pct_change": -4.37}],
    )
    quote = fetchers.fetch_data912_quote(instrument)
    assert quote["pct_change"] == -4.37


def test_fetch_data912_quote_sin_pct_change_en_la_fuente_da_none(monkeypatch):
    instrument = {"symbol": "GGAL", "type": "accion", "price_source_params": None}
    monkeypatch.setattr(
        fetchers, "fetch_data912_live",
        lambda endpoint: [{"symbol": "GGAL", "c": 5690.0, "px_bid": 5680.0, "px_ask": 5685.0}],
    )
    quote = fetchers.fetch_data912_quote(instrument)
    assert quote["pct_change"] is None


def test_fetch_data912_quote_bono_no_divide_el_pct_change(monkeypatch):
    instrument = {"symbol": "AL30D", "type": "bono", "price_source_params": None}
    monkeypatch.setattr(
        fetchers, "fetch_data912_live",
        lambda endpoint: [{"symbol": "AL30D", "c": 141000.0, "px_bid": 140900.0, "px_ask": 141100.0, "pct_change": -2.5}],
    )
    quote = fetchers.fetch_data912_quote(instrument)
    assert quote["price"] == 1410.0  # precio SÍ dividido /100 (bono)
    assert quote["pct_change"] == -2.5  # % NUNCA dividido


# --- update_quote: pct_change llega end-to-end hasta quotes_latest ----------

def test_update_quote_propaga_pct_change_hasta_la_db(temp_db, monkeypatch):
    iid = db.add_instrument(temp_db, symbol="GGAL", type_="accion", market="byma", price_source="data912")
    instrument = db.get_instrument(temp_db, iid)
    monkeypatch.setattr(
        fetchers, "fetch_data912_live",
        lambda endpoint: [{"symbol": "GGAL", "c": 5690.0, "px_bid": 5680.0, "px_ask": 5685.0, "pct_change": -4.37}],
    )
    fetchers.update_quote(temp_db, instrument)
    quote = db.get_quote(temp_db, iid)
    assert quote["pct_change"] == -4.37
    assert quote["price"] == 5690.0
