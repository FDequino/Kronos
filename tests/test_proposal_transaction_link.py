"""Tests de trazabilidad Propuesta->Transaccion (ver conversacion, Fase 5 del
plan de unificacion): "¿las propuestas que acepté realmente dieron plata?"
era imposible de auditar porque transactions no tenia ningun link a la
propuesta que la origino. proposal_id es NULLABLE y opcional -- una
transaccion cargada sin pasar por "Aceptar" una propuesta sigue funcionando
exactamente igual que antes."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from portfolio import db


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_proposal_transaction_link.db")
    monkeypatch.setattr(db, "DB_PATH", db_path)
    conn = db.get_connection(db_path)
    yield conn
    conn.close()


def _add_instrument_and_proposal(conn, symbol="GGAL"):
    instrument_id = db.add_instrument(conn, symbol, "accion", "byma", "data912")
    proposal_id = db.add_proposal(
        conn, origen="senal_kronos", symbol=symbol, texto="t", evidencia="e",
        tipo_sugerido="compra", cooldown_until="2099-01-01",
    )
    return instrument_id, proposal_id


def test_add_transaction_sin_proposal_id_sigue_funcionando_igual(temp_db):
    instrument_id = db.add_instrument(temp_db, "GGAL", "accion", "byma", "data912")
    tx_id = db.add_transaction(temp_db, instrument_id, "2026-01-01", "compra", 1, 100)

    tx = db.get_transaction(temp_db, tx_id)

    assert tx["proposal_id"] is None


def test_add_transaction_con_proposal_id_guarda_el_link(temp_db):
    instrument_id, proposal_id = _add_instrument_and_proposal(temp_db)
    tx_id = db.add_transaction(
        temp_db, instrument_id, "2026-01-01", "compra", 1, 100, proposal_id=proposal_id,
    )

    tx = db.get_transaction(temp_db, tx_id)

    assert tx["proposal_id"] == proposal_id


def test_borrar_la_propuesta_no_rompe_la_transaccion_ya_cargada(temp_db):
    """ON DELETE SET NULL -- la transaccion real (plata ya movida) nunca debe
    desaparecer ni romperse solo porque la propuesta que la origino se borre
    despues (sync_proposals purga propuestas viejas, ver proposals.py)."""
    instrument_id, proposal_id = _add_instrument_and_proposal(temp_db)
    tx_id = db.add_transaction(
        temp_db, instrument_id, "2026-01-01", "compra", 1, 100, proposal_id=proposal_id,
    )

    temp_db.execute("DELETE FROM proposals WHERE id = ?", (proposal_id,))
    temp_db.commit()

    tx = db.get_transaction(temp_db, tx_id)
    assert tx is not None
    assert tx["proposal_id"] is None
