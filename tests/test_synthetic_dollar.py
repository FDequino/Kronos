"""Tests del modulo de Dolar Sintetico y Futuros ROFEX (ver conversacion,
portfolio/synthetic_dollar.py). Todas las llamadas de red (A3 Mercados HTTP
+ websocket, dolarapi) se mockean -- nunca se pega a red real en un test.
DB temporal propia (mismo patron que tests/test_telegram_bot.py)."""
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from portfolio import db
from portfolio import synthetic_dollar as sd


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_synthetic_dollar.db")
    monkeypatch.setattr(db, "DB_PATH", db_path)
    conn = db.get_connection(db_path)
    yield conn
    conn.close()


# --- _implied_annual_rate_pct: formula contra un valor a mano ----------------

def test_implied_annual_rate_contra_calculo_a_mano():
    # F=1100, S=1000, d=90 -> (1100/1000 - 1) * (365/90) * 100 = 10 * 4.0555... = 40.555...
    rate = sd._implied_annual_rate_pct(1100.0, 1000.0, 90)
    assert rate == pytest.approx(40.5556, abs=1e-3)


def test_implied_annual_rate_dias_o_spot_invalidos_devuelve_none():
    assert sd._implied_annual_rate_pct(1100.0, 1000.0, 0) is None
    assert sd._implied_annual_rate_pct(1100.0, 1000.0, -5) is None
    assert sd._implied_annual_rate_pct(1100.0, 0, 90) is None


# --- _discover_dollar_futures_topics: filtro de outrights vs spreads/mini ----

def _fake_refdata_response(securities):
    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"securities": securities}

    return FakeResp()


def test_discover_topics_filtra_spreads_y_variantes_mini(monkeypatch):
    securities = [
        {"id": "rx_DDF_DLR_OCT26", "underlying": "rx_DDF_BCRA_A3500", "maturityDate": "20261030", "description": "Futuro de Dólar Octubre/2026"},
        {"id": "rx_DDF_DLR_OCT26M", "underlying": "rx_DDF_BCRA_A3500", "maturityDate": "20261030", "description": "Futuro Mini"},
        {"id": "rx_DDF_DLR_OCT26_NOV26", "underlying": "rx_DDF_BCRA_A3500", "maturityDate": "20261030", "description": "DLR/OCT26/NOV26"},
        {"id": "rx_DDF_DLR_NOV26", "underlying": "rx_DDF_BCRA_A3500", "maturityDate": "20261130", "description": "Futuro de Dólar Noviembre/2026"},
        {"id": "rx_DUAL_CER_ABC", "underlying": "rx_DUAL_CER", "maturityDate": "20261130", "description": "otro instrumento, no es dolar"},
        {"id": "rx_DDF_DLR_SPOT", "underlying": "rx_DDF_DLR_SPOT", "maturityDate": None, "description": "DLR/SPOT"},
    ]
    monkeypatch.setattr(sd.requests, "get", lambda url, timeout=None: _fake_refdata_response(securities))

    topics = sd._discover_dollar_futures_topics()

    ids = [t["id"] for t in topics]
    assert ids == ["rx_DDF_DLR_OCT26", "rx_DDF_DLR_NOV26"]  # orden por vencimiento, sin mini/spreads/otros underlyings/sin vencimiento


def test_discover_topics_falla_de_red_propaga_para_que_fetch_degrade(monkeypatch):
    def boom(url, timeout=None):
        raise ConnectionError("sin red")
    monkeypatch.setattr(sd.requests, "get", boom)
    with pytest.raises(ConnectionError):
        sd._discover_dollar_futures_topics()


# --- fetch_dollar_futures: degrada con gracia -------------------------------

def test_fetch_dollar_futures_sin_contratos_devuelve_vacio(monkeypatch):
    monkeypatch.setattr(sd, "_discover_dollar_futures_topics", lambda: [])
    assert sd.fetch_dollar_futures() == {}


def test_fetch_dollar_futures_fallo_de_discover_devuelve_vacio_sin_romper(monkeypatch):
    monkeypatch.setattr(sd, "_discover_dollar_futures_topics", lambda: (_ for _ in ()).throw(ConnectionError("sin red")))
    assert sd.fetch_dollar_futures() == {}


def test_fetch_dollar_futures_fallo_de_websocket_devuelve_vacio_sin_romper(monkeypatch):
    monkeypatch.setattr(sd, "_discover_dollar_futures_topics", lambda: [
        {"id": "rx_DDF_DLR_OCT26", "maturity_date": "2026-10-30", "description": "Futuro de Dólar Octubre/2026"},
    ])

    async def boom(topic_ids, timeout):
        raise ConnectionError("sin websocket")
    monkeypatch.setattr(sd, "_collect_futures_async", boom)
    assert sd.fetch_dollar_futures() == {}


def test_fetch_dollar_futures_combina_topics_y_precios(monkeypatch):
    monkeypatch.setattr(sd, "_discover_dollar_futures_topics", lambda: [
        {"id": "rx_DDF_DLR_OCT26", "maturity_date": "2026-10-30", "description": "Futuro de Dólar Octubre/2026"},
        {"id": "rx_DDF_DLR_NOV26", "maturity_date": "2026-11-30", "description": "Futuro de Dólar Noviembre/2026"},
    ])

    async def fake_collect(topic_ids, timeout):
        return {"rx_DDF_DLR_OCT26": 1550.0}  # NOV26 sin operaciones -- precio None

    monkeypatch.setattr(sd, "_collect_futures_async", fake_collect)
    result = sd.fetch_dollar_futures()

    assert result["rx_DDF_DLR_OCT26"]["price"] == 1550.0
    assert result["rx_DDF_DLR_NOV26"]["price"] is None


# --- compute_synthetic_dollar_signal: casos de negocio ----------------------

def test_sin_caucion_configurada_no_hay_señal(temp_db, monkeypatch):
    # caucion_reference_tna arranca None por default -- nunca se inventa un hurdle
    monkeypatch.setattr(sd, "get_dollar_spot_rate", lambda: 1500.0)
    monkeypatch.setattr(sd, "fetch_dollar_futures", lambda: {
        "rx_DDF_DLR_OCT26": {"maturity_date": "2026-10-30", "price": 1700.0, "description": "x"},
    })
    assert sd.compute_synthetic_dollar_signal(temp_db, as_of=date(2026, 10, 17)) is None


def test_tasa_implicita_supera_caucion_mas_margen_dispara_señal(temp_db, monkeypatch):
    db.update_signal_config(temp_db, caucion_reference_tna=35.0)
    monkeypatch.setattr(sd, "get_dollar_spot_rate", lambda: 1500.0)
    # (1700/1500 - 1) * (365/90) * 100 ≈ 54.07% TNA implicita -- bien por encima de 35+5=40
    monkeypatch.setattr(sd, "fetch_dollar_futures", lambda: {
        "rx_DDF_DLR_ENE27": {"maturity_date": "2027-01-15", "price": 1700.0, "description": "x"},
    })
    signal = sd.compute_synthetic_dollar_signal(temp_db, as_of=date(2026, 10, 17))

    assert signal is not None
    assert signal["contract"] == "rx_DDF_DLR_ENE27"
    assert signal["caucion_tna_pct"] == 35.0
    assert signal["margin_pct"] == 5.0
    assert signal["implied_tna_pct"] > 40.0
    assert signal["spread_pct"] == pytest.approx(signal["implied_tna_pct"] - 40.0, abs=0.01)
    assert "Cobertura" in signal["note"]


def test_tasa_implicita_no_supera_caucion_mas_margen_no_hay_señal(temp_db, monkeypatch):
    db.update_signal_config(temp_db, caucion_reference_tna=60.0)
    monkeypatch.setattr(sd, "get_dollar_spot_rate", lambda: 1500.0)
    # misma tasa implicita ~54% que el test anterior, pero ahora el hurdle es 60+5=65
    monkeypatch.setattr(sd, "fetch_dollar_futures", lambda: {
        "rx_DDF_DLR_ENE27": {"maturity_date": "2027-01-15", "price": 1700.0, "description": "x"},
    })
    assert sd.compute_synthetic_dollar_signal(temp_db, as_of=date(2026, 10, 17)) is None


def test_sin_datos_de_futuros_no_rompe_y_no_hay_señal(temp_db, monkeypatch):
    db.update_signal_config(temp_db, caucion_reference_tna=35.0)
    monkeypatch.setattr(sd, "get_dollar_spot_rate", lambda: 1500.0)
    monkeypatch.setattr(sd, "fetch_dollar_futures", lambda: {})
    assert sd.compute_synthetic_dollar_signal(temp_db, as_of=date(2026, 10, 17)) is None


def test_fallo_obteniendo_spot_no_rompe_y_no_hay_señal(temp_db, monkeypatch):
    db.update_signal_config(temp_db, caucion_reference_tna=35.0)

    def boom():
        raise ConnectionError("sin red")
    monkeypatch.setattr(sd, "get_dollar_spot_rate", boom)
    monkeypatch.setattr(sd, "fetch_dollar_futures", lambda: {
        "rx_DDF_DLR_OCT26": {"maturity_date": "2026-10-30", "price": 1700.0, "description": "x"},
    })
    assert sd.compute_synthetic_dollar_signal(temp_db, as_of=date(2026, 10, 17)) is None


def test_elige_el_vencimiento_mas_cercano_entre_varios_con_señal(temp_db, monkeypatch):
    db.update_signal_config(temp_db, caucion_reference_tna=10.0)
    monkeypatch.setattr(sd, "get_dollar_spot_rate", lambda: 1500.0)
    monkeypatch.setattr(sd, "fetch_dollar_futures", lambda: {
        "rx_DDF_DLR_NOV26": {"maturity_date": "2026-11-30", "price": 1600.0, "description": "x"},
        "rx_DDF_DLR_OCT26": {"maturity_date": "2026-10-30", "price": 1560.0, "description": "x"},
    })
    signal = sd.compute_synthetic_dollar_signal(temp_db, as_of=date(2026, 10, 17))
    assert signal["contract"] == "rx_DDF_DLR_OCT26"


def test_precio_none_en_un_contrato_se_ignora_sin_romper(temp_db, monkeypatch):
    db.update_signal_config(temp_db, caucion_reference_tna=10.0)
    monkeypatch.setattr(sd, "get_dollar_spot_rate", lambda: 1500.0)
    monkeypatch.setattr(sd, "fetch_dollar_futures", lambda: {
        "rx_DDF_DLR_OCT26": {"maturity_date": "2026-10-30", "price": None, "description": "x"},
        "rx_DDF_DLR_NOV26": {"maturity_date": "2026-11-30", "price": 1700.0, "description": "x"},
    })
    signal = sd.compute_synthetic_dollar_signal(temp_db, as_of=date(2026, 10, 17))
    assert signal["contract"] == "rx_DDF_DLR_NOV26"
