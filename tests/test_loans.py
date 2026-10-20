"""Tests de portfolio/loans.py::explain_loan_status -- foco puntual en el
formato es-AR (ver conversación/auditoría: "'1,000,000.00 ARS' en el
detalle de préstamo" -- el ":,.2f" de Python es formato US, inconsistente
con el resto de la app)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from portfolio import loans


def _loan(direccion="prestado", moneda="ARS"):
    return {"deudor": "Néstor", "direccion": direccion, "moneda": moneda}


def test_formato_es_ar_sin_coma_de_miles_estilo_us():
    status = {
        "capital_pendiente": 1000000.0, "vencido_hace_dias": None,
        "cuotas_atrasadas": 0, "proximo_vencimiento": None, "proximo_monto": None,
    }
    text = loans.explain_loan_status(_loan(), status)
    assert "1,000,000.00" not in text  # formato US, nunca
    assert "1000000,00" in text  # fmt_es: coma decimal, sin separador de miles


def test_direccion_prestado_dice_te_debe():
    status = {"capital_pendiente": 500.0, "vencido_hace_dias": None, "cuotas_atrasadas": 0,
              "proximo_vencimiento": None, "proximo_monto": None}
    text = loans.explain_loan_status(_loan(direccion="prestado"), status)
    assert "te debe" in text


def test_direccion_recibido_dice_le_debes():
    status = {"capital_pendiente": 500.0, "vencido_hace_dias": None, "cuotas_atrasadas": 0,
              "proximo_vencimiento": None, "proximo_monto": None}
    text = loans.explain_loan_status(_loan(direccion="recibido"), status)
    assert "le debés" in text


def test_proximo_vencimiento_usa_formato_es_ar():
    status = {
        "capital_pendiente": 250000.0, "vencido_hace_dias": None, "cuotas_atrasadas": 0,
        "proximo_vencimiento": "2026-11-05", "proximo_monto": 1234567.89,
    }
    text = loans.explain_loan_status(_loan(), status)
    assert "1234567,89" in text
    assert "1,234,567.89" not in text


def test_atrasado_con_una_cuota_no_muestra_cantidad():
    status = {"capital_pendiente": 100.0, "vencido_hace_dias": 5, "cuotas_atrasadas": 1,
              "proximo_vencimiento": None, "proximo_monto": None}
    text = loans.explain_loan_status(_loan(), status)
    assert "Atrasado 5 día(s)." in text
    assert "cuotas impagas" not in text


def test_atrasado_con_varias_cuotas_muestra_cantidad():
    status = {"capital_pendiente": 100.0, "vencido_hace_dias": 40, "cuotas_atrasadas": 3,
              "proximo_vencimiento": None, "proximo_monto": None}
    text = loans.explain_loan_status(_loan(), status)
    assert "(3 cuotas impagas)" in text
