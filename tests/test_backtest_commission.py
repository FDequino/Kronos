"""Tests de costo de transaccion (Fase 4 del plan de unificacion, ver
conversacion: "ningun backtest/paper trading resta comision... todo el edge
medido es BRUTO, no neto") en portfolio/backtest.py::simulate_position_exit /
_trade_daily_returns. commission_pct=0.0 (el default de ambas funciones)
debe reproducir exactamente el comportamiento de antes de esta fase -- estos
tests fijan ese contrato explicitamente."""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from portfolio.backtest import ROUND_TRIP_COMMISSION_PCT, _trade_daily_returns, simulate_position_exit


def _future_rows(closes, high_mult=1.0, low_mult=1.0):
    dates = pd.date_range("2026-01-02", periods=len(closes))
    return pd.DataFrame({
        "date": dates,
        "high": [c * high_mult for c in closes],
        "low": [c * low_mult for c in closes],
        "close": closes,
    })


def test_commission_pct_default_es_cero_no_cambia_comportamiento_previo():
    rows = _future_rows([101, 102, 103])
    hold_days, exit_price, return_pct, reason = simulate_position_exit(
        rows, entry_price=100.0, tp_pct=50.0, initial_stop_pct=50.0, max_hold_days=3,
    )
    assert reason == "time_exit"
    assert return_pct == (103 - 100) / 100 * 100  # sin resta de comision


def test_commission_pct_resta_del_return_en_take_profit():
    rows = _future_rows([100, 110, 100], high_mult=1.0)
    gross_hold, gross_exit, gross_return, gross_reason = simulate_position_exit(
        rows, entry_price=100.0, tp_pct=5.0, initial_stop_pct=50.0, max_hold_days=3,
    )
    net_hold, net_exit, net_return, net_reason = simulate_position_exit(
        rows, entry_price=100.0, tp_pct=5.0, initial_stop_pct=50.0, max_hold_days=3,
        commission_pct=ROUND_TRIP_COMMISSION_PCT,
    )
    assert gross_reason == net_reason == "take_profit"
    assert gross_exit == net_exit  # el precio de salida no cambia, solo el % reportado
    assert net_return == gross_return - ROUND_TRIP_COMMISSION_PCT


def test_commission_pct_resta_del_return_en_stop_loss():
    rows = _future_rows([100, 85, 80])
    gross = simulate_position_exit(rows, entry_price=100.0, tp_pct=50.0, initial_stop_pct=10.0, max_hold_days=3)
    net = simulate_position_exit(
        rows, entry_price=100.0, tp_pct=50.0, initial_stop_pct=10.0, max_hold_days=3,
        commission_pct=ROUND_TRIP_COMMISSION_PCT,
    )
    assert gross[3] == net[3] == "stop_loss"
    assert net[2] == gross[2] - ROUND_TRIP_COMMISSION_PCT


def test_trade_daily_returns_default_sin_comision_reproduce_comportamiento_previo():
    df = _future_rows([100, 101, 102, 103])
    out = _trade_daily_returns(df, entry_pos=-1, entry_price=100.0, hold_days=3, exit_price=103.0)
    assert len(out) == 3
    total_pct = sum(r for _, r in out)
    # Suma de retornos diarios compuestos no es exactamente (103-100)/100*100,
    # pero sin comision el ultimo dia usa exit_price=103 limpio.
    last_date, last_ret = out[-1]
    assert last_ret == (103.0 - 101.0) / 101.0 * 100  # dia 3: de close[1]=101 a exit_price=103


def test_trade_daily_returns_resta_comision_solo_en_el_ultimo_dia():
    df = _future_rows([100, 101, 102, 103])
    sin_comision = _trade_daily_returns(df, entry_pos=-1, entry_price=100.0, hold_days=3, exit_price=103.0)
    con_comision = _trade_daily_returns(
        df, entry_pos=-1, entry_price=100.0, hold_days=3, exit_price=103.0,
        commission_pct=ROUND_TRIP_COMMISSION_PCT,
    )
    # Dias 1 y 2 (no son el ultimo) quedan identicos.
    assert con_comision[0] == sin_comision[0]
    assert con_comision[1] == sin_comision[1]
    # El ultimo dia difiere exactamente en ROUND_TRIP_COMMISSION_PCT puntos.
    assert con_comision[2][1] == sin_comision[2][1] - ROUND_TRIP_COMMISSION_PCT
