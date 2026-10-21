"""Test de portfolio/history.py::_price_series -- bug real encontrado en
produccion (auditoria 2026-10-20): el reconstructor de valor de cartera REAL
(el que alimenta Resumen y portfolio/risk_engine.py::compute_portfolio_
drawdown_pct) leia el precio crudo guardado sin corregir el mismo salto falso
de SPY (cambio de ratio de CEDEAR, ~2026-05-29, confirmado real en
price_history: 56000 -> 18760 ARS) que ya se corrige en los backtests de
investigacion -- mostraba una caida de ~27% de la cartera real que nunca
paso. Este test confirma que _price_series aplica el mismo fix."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from portfolio import history


def _rows(closes, start="2026-05-26"):
    import pandas as pd
    dates = pd.date_range(start, periods=len(closes), freq="D")
    return [{"timestamp": d.isoformat(), "close": c} for d, c in zip(dates, closes)]


def test_salto_falso_de_ratio_se_corrige_en_la_reconstruccion_real():
    # Mismos valores reales confirmados de SPY (CEDEAR) en produccion.
    rows = _rows([55800.0, 55650.0, 56000.0, 18760.0, 18940.0, 19000.0])

    series = history._price_series(rows)

    dates = sorted(series)
    closes = [series[d] for d in dates]
    # El nivel reciente (post-salto) queda intacto -- es el real vigente.
    assert closes[-1] == 19000.0
    assert closes[3] == 18760.0
    # El historial viejo se reescala, ya no hay un salto >50% dia a dia.
    day_over_day = [abs(closes[i] / closes[i - 1] - 1) for i in range(1, len(closes))]
    assert max(day_over_day) < 0.1


def test_serie_vacia_no_rompe():
    assert history._price_series([]) == {}


def test_serie_normal_sin_saltos_se_mantiene_igual():
    rows = _rows([100.0, 101.5, 99.8, 102.3])

    series = history._price_series(rows)

    assert list(series.values()) == [100.0, 101.5, 99.8, 102.3]
