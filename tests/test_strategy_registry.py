"""Tests de portfolio/strategy_registry.py -- el Registro de Estrategias
(ver OBJETIVO_MARCO.md, plan "grado-fondo" del 2026-10-20): fuente de verdad
de que estrategia puede tocar plata real hoy."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from portfolio import db, strategy_registry


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_strategy_registry.db")
    monkeypatch.setattr(db, "DB_PATH", db_path)
    conn = db.get_connection(db_path)
    yield conn
    conn.close()


def test_dsr_alto_marca_validated(temp_db):
    strategy_registry.record_validation_result(temp_db, "canje_al29_gd29", dsr=0.9864)

    s = strategy_registry.get_strategy(temp_db, "canje_al29_gd29")
    assert s["status"] == "validated"
    assert s["last_dsr"] == 0.9864
    assert s["automation_mode"] == "off"  # nunca arranca automatizado solo


def test_dsr_bajo_marca_research(temp_db):
    strategy_registry.record_validation_result(temp_db, "core", dsr=0.0073)

    s = strategy_registry.get_strategy(temp_db, "core")
    assert s["status"] == "research"


def test_estrategia_live_no_se_degrada_por_una_corrida_puntual(temp_db):
    strategy_registry.record_validation_result(temp_db, "canje_al29_gd29", dsr=0.99)
    strategy_registry.set_automation_mode(temp_db, "canje_al29_gd29", "semi_auto")
    conn = temp_db
    conn.execute("UPDATE strategies SET status = 'live' WHERE name = ?", ("canje_al29_gd29",))
    conn.commit()

    # Una corrida nueva da un DSR mas bajo (ventana distinta) -- no debe
    # degradar sola una estrategia ya 'live' (eso es trabajo del Monitor de
    # Salud de Estrategia, Fase 4, no de este registro).
    strategy_registry.record_validation_result(temp_db, "canje_al29_gd29", dsr=0.40)

    s = strategy_registry.get_strategy(temp_db, "canje_al29_gd29")
    assert s["status"] == "live"
    assert s["last_dsr"] == 0.40  # el dato se actualiza, el status no


def test_no_se_puede_automatizar_una_estrategia_en_research(temp_db):
    strategy_registry.record_validation_result(temp_db, "core", dsr=0.0073)

    with pytest.raises(ValueError, match="research"):
        strategy_registry.set_automation_mode(temp_db, "core", "full_auto")


def test_apagar_una_estrategia_en_research_no_rompe(temp_db):
    """'off' siempre es valido, incluso sobre una estrategia en research --
    nunca hace falta pasar por el gate para DESACTIVAR algo."""
    strategy_registry.record_validation_result(temp_db, "core", dsr=0.0073)

    strategy_registry.set_automation_mode(temp_db, "core", "off")

    assert strategy_registry.get_strategy(temp_db, "core")["automation_mode"] == "off"


def test_circuit_breaker_apaga_solo_las_full_auto(temp_db):
    strategy_registry.record_validation_result(temp_db, "canje_al29_gd29", dsr=0.99)
    strategy_registry.record_validation_result(temp_db, "crypto_carry_btcusdt", dsr=0.99)
    strategy_registry.set_automation_mode(temp_db, "canje_al29_gd29", "full_auto")
    strategy_registry.set_automation_mode(temp_db, "crypto_carry_btcusdt", "semi_auto")

    strategy_registry.trip_circuit_breaker(temp_db)

    assert strategy_registry.get_strategy(temp_db, "canje_al29_gd29")["automation_mode"] == "off"
    assert strategy_registry.get_strategy(temp_db, "crypto_carry_btcusdt")["automation_mode"] == "semi_auto"


def test_estrategia_desconocida_lanza_error_al_intentar_automatizar(temp_db):
    with pytest.raises(ValueError, match="desconocida"):
        strategy_registry.set_automation_mode(temp_db, "no_existe", "full_auto")


def test_record_validation_guarda_baseline_y_no_la_pisa_una_vez_live(temp_db):
    strategy_registry.record_validation_result(temp_db, "canje_al29_gd29", dsr=0.99, annualized_return_pct=24.8, sharpe_ratio=1.54)
    strategy_registry.start_paper_trading(temp_db, "canje_al29_gd29")
    strategy_registry.promote_to_live(temp_db, "canje_al29_gd29")

    # Corrida nueva con numeros distintos (ventana distinta/deterioro real) --
    # la base NO debe cambiar, para que el Monitor de Salud (Fase 4) tenga
    # algo fijo contra que comparar.
    strategy_registry.record_validation_result(temp_db, "canje_al29_gd29", dsr=0.40, annualized_return_pct=-5.0, sharpe_ratio=-0.2)

    s = strategy_registry.get_strategy(temp_db, "canje_al29_gd29")
    assert s["baseline_sharpe"] == 1.54
    assert s["baseline_annualized_return_pct"] == 24.8
    assert s["status"] == "live"


def test_promote_to_live_requiere_haber_pasado_por_paper_trading(temp_db):
    strategy_registry.record_validation_result(temp_db, "canje_al29_gd29", dsr=0.99, sharpe_ratio=1.54)

    # Todavia 'validated', nunca paso por start_paper_trading -- no se puede
    # saltar directo a 'live' (correccion del 2026-10-21: el paso por papel
    # es obligatorio, no opcional).
    with pytest.raises(ValueError, match="paper_trading"):
        strategy_registry.promote_to_live(temp_db, "canje_al29_gd29")


def test_promote_to_live_requiere_baseline_sharpe(temp_db):
    strategy_registry.record_validation_result(temp_db, "canje_al29_gd29", dsr=0.99)  # sin sharpe_ratio
    strategy_registry.start_paper_trading(temp_db, "canje_al29_gd29")

    with pytest.raises(ValueError, match="baseline_sharpe"):
        strategy_registry.promote_to_live(temp_db, "canje_al29_gd29")


def test_start_paper_trading_requiere_estar_validated(temp_db):
    strategy_registry.record_validation_result(temp_db, "core", dsr=0.0073)

    with pytest.raises(ValueError, match="validated"):
        strategy_registry.start_paper_trading(temp_db, "core")


def test_start_paper_trading_pasa_a_ese_estado(temp_db):
    strategy_registry.record_validation_result(temp_db, "canje_al29_gd29", dsr=0.99)

    s = strategy_registry.start_paper_trading(temp_db, "canje_al29_gd29")

    assert s["status"] == "paper_trading"
    assert s["paper_trading_paused"] == 0


def test_pausar_y_continuar_paper_trading(temp_db):
    strategy_registry.record_validation_result(temp_db, "canje_al29_gd29", dsr=0.99)
    strategy_registry.start_paper_trading(temp_db, "canje_al29_gd29")

    strategy_registry.pause_paper_trading(temp_db, "canje_al29_gd29")
    s = strategy_registry.get_strategy(temp_db, "canje_al29_gd29")
    assert s["status"] == "paper_trading"  # pausar no es una regresion del gate
    assert s["paper_trading_paused"] == 1

    strategy_registry.resume_paper_trading(temp_db, "canje_al29_gd29")
    s = strategy_registry.get_strategy(temp_db, "canje_al29_gd29")
    assert s["paper_trading_paused"] == 0


def test_no_se_puede_pausar_paper_trading_fuera_de_ese_estado(temp_db):
    strategy_registry.record_validation_result(temp_db, "canje_al29_gd29", dsr=0.99)  # 'validated', nunca arranco paper trading

    with pytest.raises(ValueError, match="paper_trading"):
        strategy_registry.pause_paper_trading(temp_db, "canje_al29_gd29")


def test_list_strategies_devuelve_todas_ordenadas(temp_db):
    strategy_registry.record_validation_result(temp_db, "satellite", dsr=0.01)
    strategy_registry.record_validation_result(temp_db, "canje_al29_gd29", dsr=0.99)

    names = [s["name"] for s in strategy_registry.list_strategies(temp_db)]
    assert names == ["canje_al29_gd29", "satellite"]
