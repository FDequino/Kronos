"""Tests de portfolio/backtest.py::_fix_price_discontinuities -- bug real
encontrado en una auditoria de referencia (2026-10-20): el historico de
precios de data912 tiene saltos de un dia para el otro que NO son
movimientos reales de mercado:

1. Glitches transitorios de la fuente que vuelven al nivel anterior 1-2
   ruedas despues (confirmado real: EWZ/MCD/TSLA/GGAL/GOOGL/IBM/MELI/QQQ/
   SPY/YPFD el 2023-08-03->04).
2. Cambios de ratio de CEDEAR/split reales pero NUNCA ajustados hacia atras
   por la fuente (confirmado real: SPY el 2026-05-29, ratio 0.335 ~= 1/3;
   YPFD el 2026-08-03, ratio ~0.098 ~= 1/10 -- ambos contaminaban cualquier
   backtest que usara esos simbolos en esa ventana, incluyendo Factor
   Investing, que mostro un drawdown de -35.9% causado enteramente por el
   salto falso de SPY).

Declarado a proposito lo que NO debe tocarse: una caida real de mercado
(confirmada: GGAL/CEPU el 12/08/2019, crisis PASO, ratio 0.529/0.528) no
cae cerca de una fraccion limpia y debe quedar intacta."""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from portfolio.backtest import _fix_price_discontinuities


def _df(closes):
    return pd.DataFrame({
        "open": closes, "high": closes, "low": closes, "close": closes,
    })


def test_crash_real_no_cercano_a_fraccion_limpia_queda_intacto():
    """GGAL 12/08/2019: 174.55 -> 92.40 (ratio 0.529), a 5.8% de 1/2 --
    fuera de la tolerancia de 3%, debe quedar sin tocar."""
    closes = [170.0, 172.0, 174.55, 92.40, 90.0, 91.5]
    fixed = _fix_price_discontinuities(_df(closes))
    assert list(fixed["close"]) == pytest.approx(closes)


def test_cambio_de_ratio_real_se_ajusta_hacia_atras():
    """SPY 29/05/2026: 56000 -> 18760 (ratio ~0.335, ~1/3) -- cambio de
    ratio de CEDEAR real, nunca vuelve al nivel anterior. El historial
    viejo se reescala para continuar sin salto; el nivel nuevo (y lo
    posterior) no se toca."""
    closes = [55800.0, 55650.0, 56000.0, 18760.0, 18940.0, 19000.0]
    fixed = _fix_price_discontinuities(_df(closes))
    ratio = 18760.0 / 56000.0
    assert fixed["close"].iloc[3] == pytest.approx(18760.0)
    assert fixed["close"].iloc[4] == pytest.approx(18940.0)
    assert fixed["close"].iloc[2] == pytest.approx(56000.0 * ratio)
    assert fixed["close"].iloc[0] == pytest.approx(55800.0 * ratio)
    # Sin salto falso dia a dia alrededor del punto de ajuste
    day_over_day = fixed["close"].pct_change().iloc[1:4].abs()
    assert (day_over_day < 0.05).all()


def test_glitch_transitorio_se_interpola_y_no_queda_salto():
    """Patron real (2023-08-03 -> 04): el precio cae fuerte un dia y vuelve
    casi exacto al nivel anterior al dia siguiente -- es la fuente
    reportando mal ESE dia puntual, no un evento real. Se interpola en vez
    de dejar la caida-y-vuelta falsa."""
    closes = [100.0, 101.0, 102.0, 54.0, 103.0, 104.0]  # dia index 3 es el glitch
    fixed = _fix_price_discontinuities(_df(closes))
    assert fixed["close"].iloc[3] == pytest.approx((102.0 + 103.0) / 2, rel=0.05)
    # Ya no hay un salto >40% en ningun lado
    day_over_day = fixed["close"].pct_change().abs()
    assert (day_over_day.dropna() < 0.4).all()


def test_serie_normal_sin_saltos_no_se_toca():
    closes = [100.0, 101.5, 99.8, 102.3, 103.1]
    fixed = _fix_price_discontinuities(_df(closes))
    assert list(fixed["close"]) == pytest.approx(closes)
