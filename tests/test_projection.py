"""Tests de portfolio/projection.py::_contractual_flows -- foco puntual en el
fix de la auditoria 19/10/2026 (hallazgo 2.1: "Flujo de fondos omite las
cuotas atrasadas"). DB temporal propia, nunca portfolio/portfolio.db real."""
import sys
from datetime import timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from portfolio import db, projection
from portfolio.tz import today_ar


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_projection.db")
    monkeypatch.setattr(db, "DB_PATH", db_path)
    conn = db.get_connection(db_path)
    yield conn
    conn.close()


def test_cuota_bullet_atrasada_aparece_en_el_flujo(temp_db):
    # Bullet a 1 mes, iniciado hace 60 dias -- vencio hace ~30 dias, sin
    # ningun pago registrado -- antes de este fix, "as_of < fecha_cuota"
    # la dejaba afuera del horizonte proyectado por completo.
    inicio = (today_ar() - timedelta(days=60)).isoformat()
    db.add_loan(
        temp_db, deudor="Néstor", moneda="ARS", capital=100000.0, fecha_inicio=inicio,
        tasa_anual=50.0, plan_pago="bullet", plazo_meses=1, direccion="prestado",
    )
    flows = projection._contractual_flows(temp_db, horizon_days=365, as_of=today_ar())
    atrasadas = [f for f in flows if f["origen"].startswith("Cuota de préstamo")]
    assert len(atrasadas) == 1
    assert "(atrasada)" in atrasadas[0]["origen"]
    assert atrasadas[0]["monto"] > 0  # prestado: entra plata


def test_cuota_bullet_futura_no_se_marca_atrasada(temp_db):
    inicio = today_ar().isoformat()
    db.add_loan(
        temp_db, deudor="Ana", moneda="ARS", capital=50000.0, fecha_inicio=inicio,
        tasa_anual=40.0, plan_pago="bullet", plazo_meses=6, direccion="prestado",
    )
    flows = projection._contractual_flows(temp_db, horizon_days=365, as_of=today_ar())
    assert len(flows) == 1
    assert "(atrasada)" not in flows[0]["origen"]


def test_cuota_ya_pagada_no_aparece_en_el_flujo(temp_db):
    inicio = (today_ar() - timedelta(days=60)).isoformat()
    loan_id = db.add_loan(
        temp_db, deudor="Pagador", moneda="ARS", capital=100000.0, fecha_inicio=inicio,
        tasa_anual=50.0, plan_pago="bullet", plazo_meses=1, direccion="prestado",
    )
    status_antes = projection.loans_module.compute_loan_status(temp_db, db.list_loans(temp_db)[0])
    db.add_loan_payment(
        temp_db, loan_id=loan_id, fecha=today_ar().isoformat(),
        capital=100000.0, interes=status_antes["interes_devengado_pendiente"],
    )
    flows = projection._contractual_flows(temp_db, horizon_days=365, as_of=today_ar())
    assert flows == []


def test_direccion_recibido_tiene_signo_negativo(temp_db):
    inicio = (today_ar() - timedelta(days=60)).isoformat()
    db.add_loan(
        temp_db, deudor="Banco", moneda="ARS", capital=100000.0, fecha_inicio=inicio,
        tasa_anual=50.0, plan_pago="bullet", plazo_meses=1, direccion="recibido",
    )
    flows = projection._contractual_flows(temp_db, horizon_days=365, as_of=today_ar())
    assert len(flows) == 1
    assert flows[0]["monto"] < 0  # recibido (te prestaron): sale plata


def test_prestamo_no_vigente_no_entra_al_flujo(temp_db):
    inicio = (today_ar() - timedelta(days=60)).isoformat()
    loan_id = db.add_loan(
        temp_db, deudor="Cerrado", moneda="ARS", capital=100000.0, fecha_inicio=inicio,
        tasa_anual=50.0, plan_pago="bullet", plazo_meses=1, direccion="prestado",
        estado="incobrable",
    )
    flows = projection._contractual_flows(temp_db, horizon_days=365, as_of=today_ar())
    assert flows == []
