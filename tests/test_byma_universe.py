"""Tests de portfolio/byma_universe.py::augment_with_catalog_name_matches
(ver auditoria 19/10/2026, hallazgo 2.5: "galicia" no encontraba GGAL en el
buscador global ⌘K). DB temporal propia, nunca toca portfolio/portfolio.db
real -- search() en si (contra data912) no se testea aca, ya tiene su propio
cache/red real, este modulo solo agrega el merge con el catalogo local."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from portfolio import byma_universe, db


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_byma_universe.db")
    monkeypatch.setattr(db, "DB_PATH", db_path)
    conn = db.get_connection(db_path)
    yield conn
    conn.close()


def test_matchea_por_nombre_cuando_el_simbolo_no_aparecio(temp_db):
    db.add_instrument(temp_db, "GGAL", "accion", "byma", "data912", name='GRUPO FINANC. GALICIA "B"')
    augmented = byma_universe.augment_with_catalog_name_matches(temp_db, [], "galicia")
    assert [r["symbol"] for r in augmented] == ["GGAL"]
    assert augmented[0]["price"] is None
    assert augmented[0]["pct_change"] is None


def test_no_duplica_si_el_simbolo_ya_vino_de_search(temp_db):
    db.add_instrument(temp_db, "GGAL", "accion", "byma", "data912", name="GRUPO FINANC. GALICIA")
    existing = [{"symbol": "GGAL", "type": "accion", "market": "byma", "price": 5685.0, "pct_change": -2.06}]
    augmented = byma_universe.augment_with_catalog_name_matches(temp_db, existing, "galicia")
    assert len(augmented) == 1
    assert augmented[0]["price"] == 5685.0  # el de `search`, no pisado por el match de nombre


def test_case_insensitive(temp_db):
    db.add_instrument(temp_db, "GGAL", "accion", "byma", "data912", name="Grupo Financiero Galicia")
    assert [r["symbol"] for r in byma_universe.augment_with_catalog_name_matches(temp_db, [], "GALICIA")] == ["GGAL"]
    assert [r["symbol"] for r in byma_universe.augment_with_catalog_name_matches(temp_db, [], "galicia")] == ["GGAL"]


def test_respeta_type_filter(temp_db):
    db.add_instrument(temp_db, "GGAL", "accion", "byma", "data912", name="Grupo Financiero Galicia")
    assert byma_universe.augment_with_catalog_name_matches(temp_db, [], "galicia", type_filter="cedear") == []
    assert len(byma_universe.augment_with_catalog_name_matches(temp_db, [], "galicia", type_filter="accion")) == 1


def test_query_corta_no_busca_nada(temp_db):
    db.add_instrument(temp_db, "YPFD", "accion", "byma", "data912", name="Y")
    assert byma_universe.augment_with_catalog_name_matches(temp_db, [], "y") == []
    assert byma_universe.augment_with_catalog_name_matches(temp_db, [], "") == []


def test_respeta_limit(temp_db):
    for i in range(5):
        db.add_instrument(temp_db, f"SYM{i}", "accion", "byma", "data912", name="Banco de prueba")
    augmented = byma_universe.augment_with_catalog_name_matches(temp_db, [], "banco", limit=3)
    assert len(augmented) == 3


def test_sin_instrumentos_en_catalogo_no_rompe(temp_db):
    assert byma_universe.augment_with_catalog_name_matches(temp_db, [], "galicia") == []
