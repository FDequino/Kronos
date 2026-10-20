"""Tests de portfolio/db.py::compute_caucion_reference_tna -- auditoría
19/10/2026, hallazgo 2.2: "tasa de referencia fija en 38% TNA, cuando las
cauciones reales del usuario fueron de 20% a 26%". DB temporal propia."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from portfolio import db


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_caucion_reference_tna.db")
    monkeypatch.setattr(db, "DB_PATH", db_path)
    conn = db.get_connection(db_path)
    yield conn
    conn.close()


def _add_caucion(conn, tasa):
    db.add_caucion(
        conn, fecha_apertura="2026-09-01", fecha_vencimiento="2026-09-08", plazo_dias=7,
        monto_contado=100000.0, monto_futuro=101500.0, tasa=tasa, interes_bruto=1500.0,
    )


def test_sin_cauciones_devuelve_none(temp_db):
    assert db.compute_caucion_reference_tna(temp_db) is None


def test_promedio_simple_de_cauciones_reales(temp_db):
    _add_caucion(temp_db, 20.0)
    _add_caucion(temp_db, 26.0)
    assert db.compute_caucion_reference_tna(temp_db) == 23.0


def test_incluye_vigentes_y_finalizadas_por_igual(temp_db):
    # Vigente o finalizada, ambas son evidencia real de a que tasa opera
    # este usuario -- compute_caucion_reference_tna no filtra por vencido.
    _add_caucion(temp_db, 22.0)
    db.add_caucion(
        conn=temp_db, fecha_apertura="2020-01-01", fecha_vencimiento="2020-01-08", plazo_dias=7,
        monto_contado=50000.0, monto_futuro=50700.0, tasa=20.0, interes_bruto=700.0,
    )
    assert db.compute_caucion_reference_tna(temp_db) == 21.0


def test_redondea_a_2_decimales(temp_db):
    _add_caucion(temp_db, 20.0)
    _add_caucion(temp_db, 21.0)
    _add_caucion(temp_db, 22.0)
    # (20+21+22)/3 = 21.0 exacto -- probar un caso con resto
    _add_caucion(temp_db, 25.0)
    assert db.compute_caucion_reference_tna(temp_db) == round((20 + 21 + 22 + 25) / 4, 2)
