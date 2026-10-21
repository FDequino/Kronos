"""Tests de portfolio/risk_engine.py -- el Motor de Riesgo global (ver
OBJETIVO_MARCO.md, plan "grado-fondo" del 2026-10-20): unica puerta por la
que pasa cualquier señal antes de convertirse en propuesta accionable.
Nunca pega a datos reales (reconstruct_portfolio_value/get_ccl_series
monkeypatcheados) -- lo que se prueba es la logica del filtro/circuit
breaker, no la reconstruccion de historial en si (eso ya se prueba aparte)."""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from portfolio import config as capital_config
from portfolio import db, risk_engine, strategy_registry


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_risk_engine.db")
    monkeypatch.setattr(db, "DB_PATH", db_path)
    conn = db.get_connection(db_path)
    yield conn
    conn.close()


def _fake_series(values, start="2026-01-01"):
    dates = pd.date_range(start, periods=len(values), freq="D")
    return {"series": [{"date": d.strftime("%Y-%m-%d"), "valor_real": v} for d, v in zip(dates, values)],
            "missing_symbols": []}


def _fake_ccl(n, start="2026-01-01", rate=1000.0):
    dates = pd.date_range(start, periods=n, freq="D")
    return pd.Series([rate] * n, index=dates)


def test_sin_historial_no_calcula_drawdown(temp_db, monkeypatch):
    monkeypatch.setattr(risk_engine.history, "reconstruct_portfolio_value", lambda conn: {"series": [], "missing_symbols": []})

    assert risk_engine.compute_portfolio_drawdown_pct(temp_db) is None


def test_cartera_en_maximo_historico_da_drawdown_cero(temp_db, monkeypatch):
    # Siempre en ARS constantes / CCL constante -- en USD tambien sube monotono.
    monkeypatch.setattr(risk_engine.history, "reconstruct_portfolio_value",
                         lambda conn: _fake_series([1000, 1100, 1200, 1300]))
    monkeypatch.setattr(risk_engine.macro_data, "get_ccl_series", lambda conn: _fake_ccl(4))

    dd = risk_engine.compute_portfolio_drawdown_pct(temp_db)

    assert dd == pytest.approx(0.0, abs=1e-6)


def test_caida_real_desde_el_maximo_se_mide_en_usd(temp_db, monkeypatch):
    # Cartera cae de 1000 a 800 ARS, CCL constante -- 20% de caida en USD tambien.
    monkeypatch.setattr(risk_engine.history, "reconstruct_portfolio_value",
                         lambda conn: _fake_series([1000, 1200, 800]))
    monkeypatch.setattr(risk_engine.macro_data, "get_ccl_series", lambda conn: _fake_ccl(3))

    dd = risk_engine.compute_portfolio_drawdown_pct(temp_db)

    assert dd == pytest.approx(-33.33, abs=0.1)  # (800-1200)/1200


def test_circuit_breaker_dispara_y_apaga_full_auto(temp_db, monkeypatch):
    strategy_registry.record_validation_result(temp_db, "canje_al29_gd29", dsr=0.99)
    strategy_registry.set_automation_mode(temp_db, "canje_al29_gd29", "full_auto")
    # Perfil activo default (moderado): max_drawdown_pct=20.0 -- forzamos una caida del 50%.
    monkeypatch.setattr(risk_engine.history, "reconstruct_portfolio_value",
                         lambda conn: _fake_series([1000, 500]))
    monkeypatch.setattr(risk_engine.macro_data, "get_ccl_series", lambda conn: _fake_ccl(2))

    result = risk_engine.check_circuit_breaker(temp_db)

    assert result["tripped"] is True
    assert strategy_registry.get_strategy(temp_db, "canje_al29_gd29")["automation_mode"] == "off"


def test_circuit_breaker_no_dispara_dentro_del_limite(temp_db, monkeypatch):
    strategy_registry.record_validation_result(temp_db, "canje_al29_gd29", dsr=0.99)
    strategy_registry.set_automation_mode(temp_db, "canje_al29_gd29", "full_auto")
    # Caida chica (5%), muy por debajo del limite de cualquier perfil.
    monkeypatch.setattr(risk_engine.history, "reconstruct_portfolio_value",
                         lambda conn: _fake_series([1000, 950]))
    monkeypatch.setattr(risk_engine.macro_data, "get_ccl_series", lambda conn: _fake_ccl(2))

    result = risk_engine.check_circuit_breaker(temp_db)

    assert result["tripped"] is False
    assert strategy_registry.get_strategy(temp_db, "canje_al29_gd29")["automation_mode"] == "full_auto"


def test_is_strategy_approved(temp_db):
    strategy_registry.record_validation_result(temp_db, "canje_al29_gd29", dsr=0.99)
    strategy_registry.record_validation_result(temp_db, "core", dsr=0.0073)
    strategy_registry.set_automation_mode(temp_db, "canje_al29_gd29", "semi_auto")

    assert risk_engine.is_strategy_approved(temp_db, "canje_al29_gd29") is True
    assert risk_engine.is_strategy_approved(temp_db, "core") is False  # research, nunca aprobado
    assert risk_engine.is_strategy_approved(temp_db, "nunca_registrada") is False


def test_filter_signal_candidates_descarta_senal_de_estrategia_no_aprobada(temp_db, monkeypatch):
    strategy_registry.record_validation_result(temp_db, "core", dsr=0.0073)  # research -- nunca aprobada
    monkeypatch.setattr(risk_engine.history, "reconstruct_portfolio_value", lambda conn: {"series": [], "missing_symbols": []})
    candidates = [
        {"origen": "senal_kronos", "symbol": "GGAL"},
        {"origen": "rebalanceo", "symbol": "AL30"},  # origen no mapeado -- pasa siempre
    ]

    kept = risk_engine.filter_signal_candidates(temp_db, candidates)

    assert kept == [{"origen": "rebalanceo", "symbol": "AL30"}]


def test_filter_signal_candidates_deja_pasar_senal_de_estrategia_aprobada(temp_db, monkeypatch):
    strategy_registry.record_validation_result(temp_db, "canje_al29_gd29", dsr=0.99)
    strategy_registry.set_automation_mode(temp_db, "canje_al29_gd29", "semi_auto")
    monkeypatch.setitem(risk_engine.SIGNAL_ORIGIN_TO_STRATEGY, "senal_canje", "canje_al29_gd29")
    monkeypatch.setattr(risk_engine.history, "reconstruct_portfolio_value", lambda conn: {"series": [], "missing_symbols": []})
    candidates = [{"origen": "senal_canje", "symbol": "AL29"}]

    kept = risk_engine.filter_signal_candidates(temp_db, candidates)

    assert kept == candidates
