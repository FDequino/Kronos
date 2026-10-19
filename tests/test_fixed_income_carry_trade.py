"""Tests del Carry Trade LECAPs vs Bonos CER (ver conversacion,
portfolio/fixed_income.py::compute_carry_trade_rotation_signals). Nunca pega a
red real: fetch_letras_market_data, fetch_cer_bonds_market_data y
fetch_inflation_history se monkeypatchean siempre."""
import sys
from datetime import timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from portfolio import fixed_income
from portfolio.tz import today_ar


def _iso(days_from_today):
    return (today_ar() + timedelta(days=days_from_today)).isoformat()


@pytest.fixture(autouse=True)
def clear_caches():
    """Los fetchers de este modulo cachean en memoria a nivel de proceso --
    sin esto, un test que corre despues de otro en el mismo proceso de pytest
    reusaria los datos mockeados del test anterior."""
    fixed_income._letras_cache = {"data": None, "fetched_at": 0.0}
    fixed_income._cer_bonds_cache = {"data": None, "fetched_at": 0.0}
    yield
    fixed_income._letras_cache = {"data": None, "fetched_at": 0.0}
    fixed_income._cer_bonds_cache = {"data": None, "fetched_at": 0.0}


def _mock_letras(monkeypatch, letras_dict):
    monkeypatch.setattr(fixed_income, "fetch_letras_market_data", lambda: letras_dict)


def _mock_cer_bonds(monkeypatch, cer_dict):
    monkeypatch.setattr(fixed_income, "fetch_cer_bonds_market_data", lambda: cer_dict)


def _mock_inflation(monkeypatch, monthly_pct):
    monkeypatch.setattr(fixed_income, "_latest_monthly_inflation_pct", lambda: monthly_pct)


# --- fetch_letras_market_data: parseo del endpoint real ---------------------

def test_fetch_letras_market_data_parsea_respuesta_real(monkeypatch):
    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "fechaActualizacion": "2026-10-17T00:32:23.556Z",
                "letras": [
                    {
                        "ticker": "S30O6", "precioArs": 134.52, "tnaPorcentaje": 18.7,
                        "teaPorcentaje": 20.5, "temPorcentaje": 1.57,
                        "fechaVencimiento": "2026-10-30", "diasAlVencimiento": 11, "volumen": 1774994588,
                    },
                ],
            }

    monkeypatch.setattr(fixed_income.requests, "get", lambda url, timeout: FakeResp())
    data = fixed_income.fetch_letras_market_data()
    assert data["S30O6"]["tem_pct"] == 1.57
    assert data["S30O6"]["dias_al_vencimiento"] == 11
    assert data["S30O6"]["fecha_vencimiento"] == "2026-10-30"


# --- Formula de conversion real anual -> nominal mensual (Fisher) -----------

def test_conversion_real_a_nominal_mensual_formula_exacta():
    # TIR real anual 10%, inflacion mensual proxy 2% -- valor de referencia
    # calculado a mano con la misma formula (Fisher mensualizado).
    tir_real_anual = 10.0
    inflacion_mensual = 2.0
    real_monthly = (1 + tir_real_anual / 100) ** (1 / 12) - 1
    expected_nominal_monthly_pct = ((1 + real_monthly) * (1 + inflacion_mensual / 100) - 1) * 100

    result = fixed_income._cer_real_annual_to_nominal_monthly_pct(tir_real_anual, inflacion_mensual)
    assert result == pytest.approx(expected_nominal_monthly_pct, rel=1e-9)
    # Sanity: la tasa nominal mensual debe superar tanto la real mensual sola
    # como la inflacion mensual sola (Fisher compone ambas).
    assert result > real_monthly * 100
    assert result > inflacion_mensual


def test_conversion_con_inflacion_cero_equivale_a_la_tasa_real():
    result = fixed_income._cer_real_annual_to_nominal_monthly_pct(12.0, 0.0)
    real_monthly_pct = ((1 + 12.0 / 100) ** (1 / 12) - 1) * 100
    assert result == pytest.approx(real_monthly_pct, rel=1e-9)


# --- compute_carry_trade_rotation_signals: casos de rotacion ----------------

def test_lecap_gana_cuando_su_tem_supera_a_la_nominal_equivalente_del_cer(monkeypatch):
    _mock_letras(monkeypatch, {
        "S13N6": {"tem_pct": 5.0, "fecha_vencimiento": _iso(25), "dias_al_vencimiento": 25},
    })
    _mock_cer_bonds(monkeypatch, {
        "TX26": {"tir_pct": 10.0, "precio_ars": 758.3, "fecha_vencimiento": _iso(23), "volumen": 100},
    })
    # TEM nominal equivalente de un CER al 10% real anual ronda ~0.9-1%,
    # muy por debajo del 5% de la LECAP mockeada -- LECAP debe ganar.
    _mock_inflation(monkeypatch, 2.0)

    signals = fixed_income.compute_carry_trade_rotation_signals()
    assert len(signals) == 1
    sig = signals[0]
    assert sig["better"] == "LECAP"
    assert sig["lecap_symbol"] == "S13N6"
    assert sig["cer_symbol"] == "TX26"
    assert sig["lecap_tem_pct"] == 5.0
    assert sig["spread_pct"] > 0
    assert sig["dias_diff"] == 2


def test_cer_gana_cuando_su_tem_nominal_equivalente_supera_a_la_lecap(monkeypatch):
    _mock_letras(monkeypatch, {
        "S13N6": {"tem_pct": 0.5, "fecha_vencimiento": _iso(25), "dias_al_vencimiento": 25},
    })
    _mock_cer_bonds(monkeypatch, {
        "TX26": {"tir_pct": 30.0, "precio_ars": 758.3, "fecha_vencimiento": _iso(23), "volumen": 100},
    })
    # Con 30% real anual + 5% de inflacion mensual proxy, la TEM nominal
    # equivalente del CER queda bien por encima del 0.5% de la LECAP.
    _mock_inflation(monkeypatch, 5.0)

    signals = fixed_income.compute_carry_trade_rotation_signals()
    assert len(signals) == 1
    assert signals[0]["better"] == "CER"
    assert signals[0]["cer_tem_nominal_pct"] > signals[0]["lecap_tem_pct"]


# --- Emparejamiento por vencimiento mas cercano -----------------------------

def test_cada_cer_se_empareja_con_la_lecap_de_vencimiento_mas_cercano(monkeypatch):
    _mock_letras(monkeypatch, {
        "CORTA": {"tem_pct": 2.0, "fecha_vencimiento": _iso(10), "dias_al_vencimiento": 10},
        "LARGA": {"tem_pct": 2.5, "fecha_vencimiento": _iso(300), "dias_al_vencimiento": 300},
    })
    _mock_cer_bonds(monkeypatch, {
        "TX26": {"tir_pct": 10.0, "precio_ars": 100, "fecha_vencimiento": _iso(15), "volumen": 1},
        "TZX27": {"tir_pct": 10.0, "precio_ars": 100, "fecha_vencimiento": _iso(310), "volumen": 1},
    })
    _mock_inflation(monkeypatch, 2.0)

    signals = {s["cer_symbol"]: s for s in fixed_income.compute_carry_trade_rotation_signals()}
    assert signals["TX26"]["lecap_symbol"] == "CORTA"
    assert signals["TZX27"]["lecap_symbol"] == "LARGA"


def test_resultados_ordenados_por_spread_descendente(monkeypatch):
    _mock_letras(monkeypatch, {
        "L1": {"tem_pct": 1.0, "fecha_vencimiento": _iso(30), "dias_al_vencimiento": 30},
        "L2": {"tem_pct": 10.0, "fecha_vencimiento": _iso(180), "dias_al_vencimiento": 180},
    })
    _mock_cer_bonds(monkeypatch, {
        "A": {"tir_pct": 10.0, "precio_ars": 100, "fecha_vencimiento": _iso(31), "volumen": 1},
        "B": {"tir_pct": 10.0, "precio_ars": 100, "fecha_vencimiento": _iso(181), "volumen": 1},
    })
    _mock_inflation(monkeypatch, 2.0)

    signals = fixed_income.compute_carry_trade_rotation_signals()
    spreads = [s["spread_pct"] for s in signals]
    assert spreads == sorted(spreads, reverse=True)


# --- Degradacion honesta si falta alguna fuente -----------------------------

def test_sin_letras_disponibles_devuelve_lista_vacia(monkeypatch):
    _mock_letras(monkeypatch, {})
    _mock_cer_bonds(monkeypatch, {"TX26": {"tir_pct": 10.0, "fecha_vencimiento": _iso(30)}})
    _mock_inflation(monkeypatch, 2.0)
    assert fixed_income.compute_carry_trade_rotation_signals() == []


def test_sin_bonos_cer_disponibles_devuelve_lista_vacia(monkeypatch):
    _mock_letras(monkeypatch, {"S13N6": {"tem_pct": 2.0, "fecha_vencimiento": _iso(30), "dias_al_vencimiento": 30}})
    _mock_cer_bonds(monkeypatch, {})
    _mock_inflation(monkeypatch, 2.0)
    assert fixed_income.compute_carry_trade_rotation_signals() == []


def test_sin_dato_de_inflacion_devuelve_lista_vacia_en_vez_de_inventar_un_proxy(monkeypatch):
    _mock_letras(monkeypatch, {"S13N6": {"tem_pct": 2.0, "fecha_vencimiento": _iso(30), "dias_al_vencimiento": 30}})
    _mock_cer_bonds(monkeypatch, {"TX26": {"tir_pct": 10.0, "fecha_vencimiento": _iso(30)}})
    _mock_inflation(monkeypatch, None)
    assert fixed_income.compute_carry_trade_rotation_signals() == []


def test_fuente_de_letras_cae_con_excepcion_no_rompe_el_dispatcher(monkeypatch):
    def boom():
        raise RuntimeError("red caida")

    monkeypatch.setattr(fixed_income, "fetch_letras_market_data", boom)
    _mock_cer_bonds(monkeypatch, {"TX26": {"tir_pct": 10.0, "fecha_vencimiento": _iso(30)}})
    _mock_inflation(monkeypatch, 2.0)
    assert fixed_income.compute_carry_trade_rotation_signals() == []


def test_bono_cer_vencido_o_sin_fecha_se_descarta_sin_romper(monkeypatch):
    _mock_letras(monkeypatch, {"S13N6": {"tem_pct": 2.0, "fecha_vencimiento": _iso(30), "dias_al_vencimiento": 30}})
    _mock_cer_bonds(monkeypatch, {
        "VENCIDO": {"tir_pct": 10.0, "fecha_vencimiento": _iso(-5)},
        "SIN_FECHA": {"tir_pct": 10.0, "fecha_vencimiento": None},
        "TX26": {"tir_pct": 10.0, "fecha_vencimiento": _iso(31)},
    })
    _mock_inflation(monkeypatch, 2.0)

    signals = fixed_income.compute_carry_trade_rotation_signals()
    assert {s["cer_symbol"] for s in signals} == {"TX26"}


def test_fetch_inflation_history_vacia_da_none(monkeypatch):
    monkeypatch.setattr(fixed_income, "fetch_inflation_history", lambda: [])
    assert fixed_income._latest_monthly_inflation_pct() is None


def test_latest_monthly_inflation_toma_el_ultimo_dato_de_la_serie(monkeypatch):
    monkeypatch.setattr(fixed_income, "fetch_inflation_history", lambda: [
        {"fecha": "2026-08-01", "valor_mensual_pct": 3.0},
        {"fecha": "2026-09-01", "valor_mensual_pct": 2.1},
    ])
    assert fixed_income._latest_monthly_inflation_pct() == 2.1
