"""Tests de Paper Trading / dinero ficticio (ver conversacion) --
portfolio/paper_trading.py es logica pura (sin red, sin Telegram, sin
Chronos-Bolt): se prueba la simulacion de aperturas, salidas por Take
Profit/Stop Loss/tiempo maximo, y la generacion del reporte de rendimiento,
pasando precios "actuales" ya resueltos a mano en vez de pedirlos a
data912/Chronos (eso es responsabilidad de portfolio/monitor.py, no de este
modulo). Corre contra una DB temporal propia (mismo patron que
tests/test_telegram_bot.py), nunca toca portfolio/portfolio.db real."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from portfolio import config as capital_config
from portfolio import db
from portfolio import paper_trading
from portfolio.tz import today_ar


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_paper_trading.db")
    monkeypatch.setattr(db, "DB_PATH", db_path)
    conn = db.get_connection(db_path)
    yield conn
    conn.close()


def _core_signal(symbol="GGAL", entry_price_usd=10.0, position_weight=0.5):
    return {
        "symbol": symbol,
        "efficiency_score": 120.0,
        "position_weight": position_weight,
        "entry_price_usd": entry_price_usd, "entry_price_ars": entry_price_usd * 1000,
        "take_profit_price_usd": entry_price_usd * 1.08, "take_profit_price_ars": entry_price_usd * 1.08 * 1000,
        "initial_stop_price_usd": entry_price_usd * 0.90, "initial_stop_price_ars": entry_price_usd * 0.90 * 1000,
        "max_hold_days": 20,
    }


def _satellite_signal(symbol="YPFD", entry_price_usd=20.0, position_weight=0.33):
    return {
        "symbol": symbol,
        "tactics": ["mean_reversion"],
        "position_weight": position_weight,
        "entry_price_usd": entry_price_usd, "entry_price_ars": entry_price_usd * 1000,
        "take_profit_price_usd": entry_price_usd * 1.04, "take_profit_price_ars": entry_price_usd * 1.04 * 1000,
        "initial_stop_price_usd": entry_price_usd * 0.91, "initial_stop_price_ars": entry_price_usd * 0.91 * 1000,
        "max_hold_days": 3,
    }


# --- Flag de habilitacion / capital inicial ---------------------------------

def test_paper_trading_enabled_por_defecto(temp_db):
    assert paper_trading.is_enabled(temp_db) is True


def test_set_enabled_persiste(temp_db):
    paper_trading.set_enabled(temp_db, False)
    assert paper_trading.is_enabled(temp_db) is False
    paper_trading.set_enabled(temp_db, True)
    assert paper_trading.is_enabled(temp_db) is True


def test_initial_capital_default_y_set(temp_db):
    assert paper_trading.get_initial_capital_usd(temp_db) == paper_trading.DEFAULT_INITIAL_CAPITAL_USD
    paper_trading.set_initial_capital_usd(temp_db, 5000)
    assert paper_trading.get_initial_capital_usd(temp_db) == 5000.0


def test_initial_capital_invalido_lanza_value_error(temp_db):
    with pytest.raises(ValueError):
        paper_trading.set_initial_capital_usd(temp_db, 0)
    with pytest.raises(ValueError):
        paper_trading.set_initial_capital_usd(temp_db, "no-es-un-numero")


# --- Apertura de posiciones (compras) ----------------------------------------

def test_abrir_posicion_desde_senal_core(temp_db):
    paper_trading.set_initial_capital_usd(temp_db, 10000)
    capital_config.set_core_allocation_pct(temp_db, 20.0)  # satellite queda en 80

    signal = _core_signal(entry_price_usd=10.0, position_weight=0.5)
    position = paper_trading.open_from_signal(temp_db, signal, "core", "accion")

    assert position is not None
    assert position["symbol"] == "GGAL"
    assert position["engine"] == "core"
    assert position["asset_type"] == "accion"
    # 10000 * 20% * 0.5 = 1000 USD de monto -> 1000 / 10.0 = 100 titulos
    assert position["quantity"] == pytest.approx(100.0)
    assert position["entry_date"] == today_ar().isoformat()
    assert paper_trading.has_open_position(temp_db, "GGAL", "core")


def test_abrir_posicion_desde_senal_satelite_guarda_tactica(temp_db):
    signal = _satellite_signal()
    position = paper_trading.open_from_signal(temp_db, signal, "satellite", "accion")
    assert position["tactic"] == "mean_reversion"


def test_no_duplica_posicion_en_el_mismo_symbol_y_motor(temp_db):
    signal = _core_signal()
    first = paper_trading.open_from_signal(temp_db, signal, "core", "accion")
    second = paper_trading.open_from_signal(temp_db, signal, "core", "accion")
    assert first is not None
    assert second is None
    assert len(paper_trading.list_open_positions(temp_db)) == 1


def test_mismo_symbol_distinto_motor_si_puede_abrir_las_dos(temp_db):
    core_signal = _core_signal(symbol="GGAL")
    satellite_signal = _satellite_signal(symbol="GGAL")
    paper_trading.open_from_signal(temp_db, core_signal, "core", "accion")
    paper_trading.open_from_signal(temp_db, satellite_signal, "satellite", "accion")
    assert len(paper_trading.list_open_positions(temp_db)) == 2


# --- Unidades enteras para accion/cedear (ver conversacion/auditoria
# 19/10/2026, hallazgo 2.4: "simula CEDEARs fraccionados, ej. 20,32 QQQ" --
# el broker real jamas ejecutaria esa cantidad) ------------------------------

def test_cedear_trunca_a_unidades_enteras(temp_db):
    paper_trading.set_initial_capital_usd(temp_db, 10000)
    capital_config.set_core_allocation_pct(temp_db, 20.0)
    # 10000 * 20% * 0.5 = 1000 USD / 12.3 = 81.30 titulos -- deberia truncar a 81
    signal = _core_signal(entry_price_usd=12.3, position_weight=0.5)
    position = paper_trading.open_from_signal(temp_db, signal, "core", "cedear")
    assert position["quantity"] == 81.0


def test_accion_trunca_a_unidades_enteras(temp_db):
    paper_trading.set_initial_capital_usd(temp_db, 10000)
    capital_config.set_core_allocation_pct(temp_db, 20.0)
    signal = _core_signal(entry_price_usd=12.3, position_weight=0.5)
    position = paper_trading.open_from_signal(temp_db, signal, "core", "accion")
    assert position["quantity"] == 81.0


def test_bono_no_trunca_sigue_fraccionado(temp_db):
    # Bonos/ON se operan por nominales y SI admiten fracciones en la
    # practica -- quedan fuera de WHOLE_UNIT_ASSET_TYPES a proposito.
    paper_trading.set_initial_capital_usd(temp_db, 10000)
    capital_config.set_core_allocation_pct(temp_db, 20.0)
    signal = _core_signal(entry_price_usd=12.3, position_weight=0.5)
    position = paper_trading.open_from_signal(temp_db, signal, "core", "bono")
    assert position["quantity"] == pytest.approx(81.30, abs=0.01)


def test_cedear_trunca_a_cero_no_abre_posicion(temp_db):
    # Monto asignado no alcanza ni para 1 unidad entera -- no debe abrir
    # una posicion de 0 titulos.
    paper_trading.set_initial_capital_usd(temp_db, 100)
    capital_config.set_core_allocation_pct(temp_db, 1.0)
    signal = _core_signal(entry_price_usd=50.0, position_weight=0.5)
    position = paper_trading.open_from_signal(temp_db, signal, "core", "cedear")
    assert position is None


# --- Salidas: Stop Loss / Take Profit / tiempo maximo ------------------------

def test_salida_por_stop_loss(temp_db):
    signal = _core_signal(symbol="BMA", entry_price_usd=10.0)
    position = paper_trading.open_from_signal(temp_db, signal, "core", "accion")
    stop_price_usd = position["stop_loss_price_usd"]

    closed = paper_trading.check_exits(temp_db, {"BMA": (stop_price_usd - 0.01, (stop_price_usd - 0.01) * 1000)})

    assert len(closed) == 1
    trade = closed[0]
    assert trade["exit_reason"] == "stop_loss"
    assert trade["pnl_usd"] < 0
    assert not paper_trading.has_open_position(temp_db, "BMA", "core")
    assert len(paper_trading.list_closed_trades(temp_db)) == 1


def test_salida_por_take_profit(temp_db):
    signal = _core_signal(symbol="PAMP", entry_price_usd=10.0)
    position = paper_trading.open_from_signal(temp_db, signal, "core", "accion")
    tp_price_usd = position["take_profit_price_usd"]

    closed = paper_trading.check_exits(temp_db, {"PAMP": (tp_price_usd + 0.01, (tp_price_usd + 0.01) * 1000)})

    assert len(closed) == 1
    assert closed[0]["exit_reason"] == "take_profit"
    assert closed[0]["pnl_usd"] > 0


def test_posicion_que_no_toca_ningun_nivel_sigue_abierta(temp_db):
    signal = _core_signal(symbol="ALUA", entry_price_usd=10.0)
    paper_trading.open_from_signal(temp_db, signal, "core", "accion")

    closed = paper_trading.check_exits(temp_db, {"ALUA": (10.05, 10050.0)})

    assert closed == []
    assert paper_trading.has_open_position(temp_db, "ALUA", "core")


def test_salida_por_tiempo_maximo(temp_db):
    signal = _core_signal(symbol="CRES", entry_price_usd=10.0)
    position = paper_trading.open_from_signal(temp_db, signal, "core", "accion")
    # Fuerza una entry_date vieja (ya paso max_hold_days) sin tocar TP/SL.
    temp_db.execute("UPDATE paper_positions SET entry_date = '2000-01-01' WHERE id = ?", (position["id"],))
    temp_db.commit()

    closed = paper_trading.check_exits(temp_db, {"CRES": (10.02, 10020.0)})

    assert len(closed) == 1
    assert closed[0]["exit_reason"] == "max_hold"


def test_check_exits_ignora_simbolos_sin_precio_actual(temp_db):
    signal = _core_signal(symbol="MELI", entry_price_usd=10.0)
    paper_trading.open_from_signal(temp_db, signal, "core", "accion")
    closed = paper_trading.check_exits(temp_db, {})
    assert closed == []
    assert paper_trading.has_open_position(temp_db, "MELI", "core")


# --- Equity / caja virtual ----------------------------------------------------

def test_equity_antes_de_cualquier_operacion_es_el_capital_inicial(temp_db):
    paper_trading.set_initial_capital_usd(temp_db, 7777)
    assert paper_trading.get_equity_usd(temp_db) == pytest.approx(7777.0)
    assert paper_trading.get_cash_usd(temp_db) == pytest.approx(7777.0)


def test_equity_descuenta_costo_de_posicion_abierta_y_suma_valor_de_mercado(temp_db):
    paper_trading.set_initial_capital_usd(temp_db, 10000)
    capital_config.set_core_allocation_pct(temp_db, 20.0)
    signal = _core_signal(symbol="AAPL", entry_price_usd=10.0, position_weight=0.5)
    position = paper_trading.open_from_signal(temp_db, signal, "core", "accion")
    cost_usd = position["entry_price_usd"] * position["quantity"]

    # Caja = capital inicial - costo de lo abierto (nada realizado todavia).
    assert paper_trading.get_cash_usd(temp_db) == pytest.approx(10000 - cost_usd)

    # Equity con precio actual = entrada (sin cambios de mercado) debe ser
    # igual al capital inicial (nada se gano ni se perdio todavia).
    equity_flat = paper_trading.get_equity_usd(temp_db, {"AAPL": (10.0, 10000.0)})
    assert equity_flat == pytest.approx(10000.0)

    # Si el precio subio, el equity sube con el valor de mercado.
    equity_up = paper_trading.get_equity_usd(temp_db, {"AAPL": (11.0, 11000.0)})
    assert equity_up == pytest.approx(10000 - cost_usd + 11.0 * position["quantity"])


def test_equity_usa_precio_de_entrada_si_no_hay_precio_actual(temp_db):
    paper_trading.set_initial_capital_usd(temp_db, 10000)
    signal = _core_signal(symbol="TSLA", entry_price_usd=10.0, position_weight=0.5)
    paper_trading.open_from_signal(temp_db, signal, "core", "accion")
    # Sin current_prices: el equity no deberia cambiar vs. el capital inicial
    # (la posicion se valua a su propio precio de entrada).
    assert paper_trading.get_equity_usd(temp_db) == pytest.approx(10000.0)


def test_cierre_de_posicion_actualiza_caja(temp_db):
    paper_trading.set_initial_capital_usd(temp_db, 10000)
    capital_config.set_core_allocation_pct(temp_db, 20.0)
    signal = _core_signal(symbol="QQQ", entry_price_usd=10.0, position_weight=0.5)
    position = paper_trading.open_from_signal(temp_db, signal, "core", "accion")
    tp_price = position["take_profit_price_usd"]

    paper_trading.check_exits(temp_db, {"QQQ": (tp_price, tp_price * 1000)})

    # Neto de comision ida+vuelta (Fase 4, ver paper_trading.ROUND_TRIP_COMMISSION_PCT).
    commission_usd = position["entry_price_usd"] * position["quantity"] * (paper_trading.ROUND_TRIP_COMMISSION_PCT / 100)
    expected_pnl = (tp_price - position["entry_price_usd"]) * position["quantity"] - commission_usd
    assert paper_trading.get_cash_usd(temp_db) == pytest.approx(10000 + expected_pnl)
    assert paper_trading.get_equity_usd(temp_db) == pytest.approx(10000 + expected_pnl)


def test_close_position_comision_cero_reproduce_comportamiento_bruto_previo(temp_db):
    signal = _core_signal(symbol="QQQ", entry_price_usd=10.0, position_weight=0.5)
    position = paper_trading.open_from_signal(temp_db, signal, "core", "accion")
    tp_price = position["take_profit_price_usd"]

    trade = paper_trading.close_position(
        temp_db, position, exit_price_usd=tp_price, exit_price_ars=tp_price * 1000,
        exit_reason="take_profit", commission_pct=0.0,
    )

    gross_pnl_usd = (tp_price - position["entry_price_usd"]) * position["quantity"]
    assert trade["pnl_usd"] == pytest.approx(gross_pnl_usd)
    assert trade["pnl_pct"] == pytest.approx((tp_price / position["entry_price_usd"] - 1) * 100)


def test_close_position_comision_default_da_pnl_neto_menor_que_bruto(temp_db):
    signal = _core_signal(symbol="QQQ", entry_price_usd=10.0, position_weight=0.5)
    position = paper_trading.open_from_signal(temp_db, signal, "core", "accion")
    tp_price = position["take_profit_price_usd"]

    trade_bruto = paper_trading.close_position(
        temp_db, position, exit_price_usd=tp_price, exit_price_ars=tp_price * 1000,
        exit_reason="take_profit", commission_pct=0.0,
    )
    # Reabrir la misma posicion para cerrarla de nuevo, esta vez con comision
    # default -- mismo engine "core" que arriba (distinto engine tendria
    # distinta asignacion de capital/quantity, no serian comparables).
    position2 = paper_trading.open_from_signal(temp_db, signal, "core", "accion")
    trade_neto = paper_trading.close_position(
        temp_db, position2, exit_price_usd=tp_price, exit_price_ars=tp_price * 1000,
        exit_reason="take_profit",
    )

    assert trade_neto["pnl_usd"] < trade_bruto["pnl_usd"]
    assert trade_neto["pnl_pct"] == pytest.approx(trade_bruto["pnl_pct"] - paper_trading.ROUND_TRIP_COMMISSION_PCT)


# --- Reporte de rendimiento ----------------------------------------------------

def test_reporte_sin_actividad_previa_no_inventa_resultado_del_dia(temp_db):
    paper_trading.set_initial_capital_usd(temp_db, 10000)
    report = paper_trading.compute_daily_report(temp_db)
    assert report["equity_usd"] == pytest.approx(10000.0)
    assert report["resultado_dia_usd"] is None
    assert report["resultado_dia_pct"] is None
    assert report["trades_closed_today"] == []
    assert report["open_positions"] == []


def test_reporte_incluye_trades_cerrados_hoy_y_posiciones_abiertas(temp_db):
    paper_trading.set_initial_capital_usd(temp_db, 10000)
    capital_config.set_core_allocation_pct(temp_db, 20.0)

    closed_signal = _core_signal(symbol="GGAL", entry_price_usd=10.0, position_weight=0.3)
    open_signal = _core_signal(symbol="BMA", entry_price_usd=10.0, position_weight=0.3)
    closed_pos = paper_trading.open_from_signal(temp_db, closed_signal, "core", "accion")
    paper_trading.open_from_signal(temp_db, open_signal, "core", "accion")
    paper_trading.check_exits(temp_db, {"GGAL": (closed_pos["take_profit_price_usd"], closed_pos["take_profit_price_usd"] * 1000)})

    report = paper_trading.compute_daily_report(temp_db, {"BMA": (10.0, 10000.0)})
    assert report["trades_closed_today_count"] == 1
    assert report["trades_closed_today"][0]["symbol"] == "GGAL"
    assert report["open_positions_count"] == 1
    assert report["open_positions"][0]["symbol"] == "BMA"


def test_resultado_del_dia_contra_snapshot_de_ayer(temp_db):
    paper_trading.set_initial_capital_usd(temp_db, 10000)
    paper_trading.save_daily_snapshot(temp_db, 10000.0, date="2020-01-01")

    signal = _core_signal(symbol="NVDA", entry_price_usd=10.0, position_weight=0.2)
    position = paper_trading.open_from_signal(temp_db, signal, "core", "accion")
    paper_trading.check_exits(temp_db, {
        "NVDA": (position["take_profit_price_usd"], position["take_profit_price_usd"] * 1000),
    })

    report = paper_trading.compute_daily_report(temp_db)
    assert report["resultado_dia_usd"] == pytest.approx(report["equity_usd"] - 10000.0)
    assert report["resultado_dia_pct"] == pytest.approx(report["resultado_dia_usd"] / 10000.0 * 100)
