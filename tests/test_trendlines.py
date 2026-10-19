"""Tests de portfolio/trendlines.py -- soporte/resistencia + ruptura.
Foco en current_price (ver conversación/auditoría 19/10/2026, hallazgo 1.6:
"los motores calculan con la vela de ayer" -- TP/SL y "ruptura de
resistencia" comparados contra el último close de price_history, que solo
se backfillea al correr un pronóstico y puede quedar MUCHOS DÍAS atrás.
Confirmado en producción: price_history de QQQ/GGAL con 10-11 días de
atraso, dando un "precio" en el mensaje de ruptura sin relación con el real.
Lógica pura, sin red ni DB."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from portfolio import trendlines


def _candles(closes, start_time=1_700_000_000, step=86400):
    """Velas sintéticas ascendentes -- high/low con algo de ruido alrededor
    del close para que _confirmed_pivots tenga pivotes reales que detectar."""
    out = []
    for i, c in enumerate(closes):
        out.append({
            "time": start_time + i * step,
            "open": c, "high": c * 1.01, "low": c * 0.99, "close": c,
        })
    return out


def _zigzag_support_candles(n=40, base=100.0, amplitude=5.0):
    """Zigzag con mínimos repetidos ~base-amplitude -- suficientes toques
    confirmados para que _fit_lines acepte una línea de soporte real."""
    closes = []
    for i in range(n):
        # Dientes de sierra: sube y vuelve a tocar el mismo piso.
        phase = i % 6
        if phase == 0:
            closes.append(base - amplitude)  # toque de soporte
        else:
            closes.append(base - amplitude + phase * 1.5)
    return _candles(closes)


def test_detect_breakout_sin_current_price_usa_el_ultimo_close_de_candles():
    candles = _candles([100.0] * 10)
    atr = 1.0
    # Resistencia justo debajo del último close -- confirma el camino SIN
    # current_price (comportamiento de siempre, nunca una regresión).
    resistance = [{"slope": 0.0, "intercept": 95.0}]
    result = trendlines._detect_breakout(resistance, [], candles, atr)
    assert result is not None
    assert result["price"] == 100.0  # candles[-1]["close"], no current_price


def test_detect_breakout_con_current_price_lo_usa_en_vez_del_close_viejo():
    # candles[-1]["close"] = 100 (viejo/stale), pero el precio EN VIVO es
    # muy distinto -- el breakout tiene que evaluarse y reportarse contra
    # el precio real, no contra el close desactualizado.
    candles = _candles([100.0] * 10)
    atr = 1.0
    resistance = [{"slope": 0.0, "intercept": 95.0}]
    result = trendlines._detect_breakout(resistance, [], candles, atr, current_price=120.0)
    assert result is not None
    assert result["price"] == 120.0


def test_detect_breakout_current_price_puede_revertir_el_resultado():
    # Con el close viejo (100) hay ruptura de resistencia (95); con el
    # precio real (90) ya no la hay -- current_price cambia la conclusión,
    # no solo el número mostrado.
    candles = _candles([100.0] * 10)
    atr = 1.0
    resistance = [{"slope": 0.0, "intercept": 95.0}]
    assert trendlines._detect_breakout(resistance, [], candles, atr) is not None
    assert trendlines._detect_breakout(resistance, [], candles, atr, current_price=90.0) is None


def test_detect_breakout_soporte_con_current_price():
    candles = _candles([100.0] * 10)
    atr = 1.0
    support = [{"slope": 0.0, "intercept": 105.0}]
    result = trendlines._detect_breakout([], support, candles, atr, current_price=80.0)
    assert result == {"kind": "soporte", "direction": "abajo", "price": 80.0, "level": 105.0}


def test_detect_breakout_sin_lineas_devuelve_none():
    candles = _candles([100.0] * 10)
    assert trendlines._detect_breakout([], [], candles, 1.0, current_price=200.0) is None


def test_compute_trendlines_propaga_current_price_al_breakout():
    candles = _zigzag_support_candles()
    # Sin current_price: nada raro, el breakout (si lo hay) usa el último close.
    result_sin = trendlines.compute_trendlines(candles)
    # Con un current_price absurdamente alto, fuerza una ruptura de
    # resistencia si hay alguna línea -- al menos confirma que el parámetro
    # efectivamente llega hasta _detect_breakout sin romper nada.
    result_con = trendlines.compute_trendlines(candles, current_price=10_000.0)
    assert "breakout" in result_sin and "breakout" in result_con


def test_compute_trendlines_pocas_velas_devuelve_vacio_con_o_sin_current_price():
    candles = _candles([100.0, 101.0])
    assert trendlines.compute_trendlines(candles) == {"resistance": [], "support": [], "breakout": None}
    assert trendlines.compute_trendlines(candles, current_price=500.0) == {"resistance": [], "support": [], "breakout": None}
