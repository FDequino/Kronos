"""Deteccion de soporte/resistencia (Fase 8).

Pivotes CONFIRMADOS: un maximo/minimo local en el indice i solo se declara
pivote una vez que hay PIVOT_WINDOW velas de dato real a la DERECHA de el
(ademas de a la izquierda). Esto es lo que garantiza que un pivote ya
mostrado nunca cambie cuando llega una vela nueva: agregar datos al final de
la serie nunca modifica los vecinos de un indice que ya quedo atras, asi que
la condicion "es el maximo/minimo de su ventana" tampoco cambia. No hay
ningun suavizado ni ajuste retroactivo sobre el pasado.

Sobre esos pivotes confirmados, se ajusta una recta por regresion Theil-Sen
(robusta a outliers, a diferencia de minimos cuadrados) y se exige un minimo
de toques dentro de una tolerancia de ATR(14) para aceptar la linea como
soporte/resistencia real -- 1 o 2 pivotes alineados por azar no cuentan.

Las rupturas se marcan como un evento informativo -- NUNCA generan una
propuesta (ver portfolio/proposals.py): no existe today un backtest propio
que mida si "romper una linea de tendencia" tiene edge real, mismo principio
ya aplicado al RSI (una heuristica sin medir no se trata como señal confiable
hasta tener la evidencia)."""
from scipy.stats import theilslopes

PIVOT_WINDOW = 5
MIN_TOUCHES = 3
MAX_LINES_PER_SIDE = 2
ATR_WINDOW = 14
BREAKOUT_MARGIN_ATR = 0.1  # evita marcar ruptura por 1 tick de ruido
# El soporte/resistencia real se mueve con el tiempo -- una sola recta
# ajustada sobre ~1.5 años de velas (default_lookback=400, la ventana que
# /overlays le pasaba a esta funcion) termina siendo un canal de largo plazo,
# no la zona que realmente importa HOY. Se recorta acá (no en el caller) a
# una ventana mas corta y realista para "soporte/resistencia vigente" --
# alrededor de 6 meses de ruedas -- para que la línea se recalcule y se
# corra sola a medida que entran velas nuevas y las viejas quedan afuera.
RECENT_WINDOW_CANDLES = 120


def _atr(candles, window=ATR_WINDOW):
    trs = []
    for i in range(1, len(candles)):
        high, low, prev_close = candles[i]["high"], candles[i]["low"], candles[i - 1]["close"]
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    if not trs:
        return 0.0
    recent = trs[-window:]
    return sum(recent) / len(recent)


def _confirmed_pivots(candles):
    """[(indice, time, precio, 'high'|'low')] -- solo hasta
    len(candles) - PIVOT_WINDOW - 1: las ultimas PIVOT_WINDOW velas todavia no
    tienen suficiente confirmacion a la derecha y no se incluyen (en vez de
    confirmarlas "a medias" y tener que corregirlas despues)."""
    pivots = []
    n = len(candles)
    for i in range(PIVOT_WINDOW, n - PIVOT_WINDOW):
        window = candles[i - PIVOT_WINDOW:i + PIVOT_WINDOW + 1]
        high, low = candles[i]["high"], candles[i]["low"]
        if high == max(c["high"] for c in window):
            pivots.append((i, candles[i]["time"], high, "high"))
        if low == min(c["low"] for c in window):
            pivots.append((i, candles[i]["time"], low, "low"))
    return pivots


def _fit_lines(pivots, candles, kind, atr, last_index):
    """Greedy: ajusta Theil-Sen con todos los pivotes del lado, si no alcanza
    el minimo de toques dentro de tolerancia descarta el mas alejado y
    reintenta; acepta la linea y saca sus toques, repite hasta
    MAX_LINES_PER_SIDE o quedarse sin pivotes suficientes."""
    remaining = [p for p in pivots if p[3] == kind]
    lines = []
    while len(remaining) >= MIN_TOUCHES and len(lines) < MAX_LINES_PER_SIDE:
        xs = [p[0] for p in remaining]
        ys = [p[2] for p in remaining]
        slope, intercept, _, _ = theilslopes(ys, xs)
        residuals = [(p, abs(p[2] - (slope * p[0] + intercept))) for p in remaining]
        touches = [p for p, r in residuals if r <= atr]
        if len(touches) < MIN_TOUCHES:
            worst = max(residuals, key=lambda pr: pr[1])[0]
            remaining = [p for p in remaining if p is not worst]
            continue
        first_i = min(p[0] for p in touches)
        lines.append({
            "kind": kind,
            "touches": len(touches),
            "slope": slope,
            "intercept": intercept,
            "from": {"time": candles[first_i]["time"], "value": slope * first_i + intercept},
            "to": {"time": candles[last_index]["time"], "value": slope * last_index + intercept},
        })
        touch_set = set(touches)
        remaining = [p for p in remaining if p not in touch_set]
    return lines


def _detect_breakout(resistance, support, candles, atr):
    last = candles[-1]
    last_index = len(candles) - 1
    for line in resistance:
        proj = line["slope"] * last_index + line["intercept"]
        if last["close"] > proj + atr * BREAKOUT_MARGIN_ATR:
            return {"kind": "resistencia", "direction": "arriba", "price": last["close"], "level": round(proj, 4)}
    for line in support:
        proj = line["slope"] * last_index + line["intercept"]
        if last["close"] < proj - atr * BREAKOUT_MARGIN_ATR:
            return {"kind": "soporte", "direction": "abajo", "price": last["close"], "level": round(proj, 4)}
    return None


def compute_trendlines(candles):
    """candles: lista de {time, open, high, low, close} ordenada ascendente
    (mismo shape que forecasting.py::_candles_json) -- puede venir con más
    historial del que realmente se usa, ver RECENT_WINDOW_CANDLES arriba.
    Devuelve {"resistance": [...], "support": [...], "breakout": None|{...}}."""
    candles = candles[-RECENT_WINDOW_CANDLES:]
    if len(candles) < PIVOT_WINDOW * 2 + MIN_TOUCHES:
        return {"resistance": [], "support": [], "breakout": None}

    atr = _atr(candles)
    if atr <= 0:
        return {"resistance": [], "support": [], "breakout": None}

    pivots = _confirmed_pivots(candles)
    last_index = len(candles) - 1
    resistance = _fit_lines(pivots, candles, "high", atr, last_index)
    support = _fit_lines(pivots, candles, "low", atr, last_index)
    breakout = _detect_breakout(resistance, support, candles, atr)

    # slope/intercept son un detalle interno de calculo (sirven para
    # proyectar la ruptura) -- el frontend solo dibuja los 2 puntos from/to.
    for line in resistance + support:
        line.pop("slope", None)
        line.pop("intercept", None)

    return {"resistance": resistance, "support": support, "breakout": breakout}
