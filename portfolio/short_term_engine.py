"""Motor SATELITE: trading de corto plazo (24-72hs / 1-3 ruedas), 100%
basado en reglas tecnicas sobre precio/volumen -- INDEPENDIENTE del Motor
Core (portfolio/signals.py, que usa Chronos-Bolt + ranking cross-sectional)
y de compute_signal() (la señal generica comprar/vender/mantener que sigue
usando el resto de la app). Nunca corre Chronos-Bolt: a 1-3 ruedas de
horizonte, un modelo entrenado para proyectar varios pasos no aporta nada
que una regla de reversion a la media/breakout no capture mas rapido y mas
barato (cero latencia de red al microservicio).

Tres tacticas, cada una evaluada de forma independiente por rueda -- ver
evaluate_short_term_signals(), que las combina:
  a) Mean Reversion: el precio se "estiro" demasiado para abajo (RSI bajo +
     toco la banda inferior de Bollinger) con volumen confirmando interes
     comprador -- apuesta a que vuelve hacia la media.
  b) Volume Breakout: un salto de volumen fuerte + ruptura del maximo
     reciente -- apuesta a que el impulso continua.
  c) ATR Volatility Breakout: el rango de precio estuvo comprimido las
     ultimas ruedas y HOY explota (true range >> el rango comprimido
     reciente) + ruptura del maximo reciente -- mismo apuesta que (b) pero
     disparada por expansion de volatilidad en vez de volumen (dos caminos
     independientes a la misma idea de "arranca un movimiento", ver
     conversacion: "Volume & Volatility Breakout").

Recalibracion (ver conversacion): umbrales b/c mas laxos y salida acelerada
respecto de la version original, para aumentar la tasa de disparo sin perder
la asimetria positiva (TP > |SL| y expiracion corta).

Garantia de cero look-ahead: toda funcion opera sobre un df ya cortado hasta
la rueda de evaluacion (inclusive) -- nunca mira una fila futura. Igual
criterio que portfolio/features.py y portfolio/signals.py."""
from portfolio import indicators

# a) Mean Reversion -- RSI y banda mas laxos que la version original (32 ->
# 36, 2.0std -> 1.8std) para disparar mas seguido sin dejar de exigir una
# sobreventa real.
RSI_OVERSOLD_THRESHOLD = 36.0
BOLLINGER_WINDOW = 20
BOLLINGER_NUM_STD = 1.8
VOLUME_CONFIRM_WINDOW = 5  # "volumen > promedio 5d"

# b) Volume Breakout -- multiplicador bajado de 2.5x a 1.8x (misma logica
# que (a): mas señales, sigue exigiendo ruptura de maximo reciente).
VOLUME_BREAKOUT_WINDOW = 20
VOLUME_BREAKOUT_MULTIPLIER = 1.8
BREAKOUT_LOOKBACK_DAYS = 5  # "rompe el maximo de las ultimas 5 ruedas", compartido por (b) y (c)

# c) ATR Volatility Breakout (NUEVO -- camino alternativo a (b), ver
# conversacion "Volume & Volatility Breakout")
ATR_COMPRESSION_WINDOW_DAYS = 3  # "rango comprimido de 3 dias"
ATR_COMPRESSION_BASELINE_WINDOW = 20  # contra que se compara si esos 3 dias fueron "angostos"
ATR_COMPRESSION_RATIO = 0.5  # rango medio de esos 3 dias <= 50% del true range medio de las 20 ruedas previas -> "comprimido"
ATR_EXPLOSION_MULTIPLIER = 2.0  # true range de HOY >= 2x el rango comprimido reciente -> "explosion"

# d) Gestion de salida corta (ver portfolio/backtest.py::simulate_position_exit,
# mismo simulador que usa el Motor Core con otros parametros). "Salida Rapida
# Acelerada": TP generoso (3.0% -> 3.5% -> 4.0%, ver conversacion) + trailing
# stop por ATR (antes un stop fijo sin trailing) -- el multiplo de ATR se
# amplio de 1.0x a 1.8x: con 1.0x el trailing se pisaba con el ruido normal
# de precio intradiario (salida prematura antes de que el movimiento se
# desarrolle), 1.8x le da mas margen sin perder la naturaleza de "salida
# rapida" (sigue expirando a los 3 dias de maximo).
#
# Refactorizacion financiera (ver conversacion): SATELLITE_TAKE_PROFIT_PCT/
# SATELLITE_STOP_LOSS_PCT ahora son solo el DEFAULT de referencia -- el valor
# REAL que opera el backtest, las señales en vivo y Telegram es
# configurable por el usuario, persistido en signal_config (ver
# portfolio/config.py::get_satellite_exit_params/set_satellite_exit_params),
# nunca estas constantes directamente. satellite_stop_loss_pct es un TECHO,
# no un piso: el stop real por operacion es
# min(satellite_stop_loss_pct, SATELLITE_TRAILING_STOP_ATR_MULT * ATR%)
# (ver simulate_position_exit(..., stop_is_ceiling=True)) -- nunca se pierde
# mas que el % configurado en una sola operacion, aunque el activo sea muy
# volatil (antes era un piso/max, podia dejar stops de hasta ~10% en
# nombres volatiles sin ningun techo).
SATELLITE_TAKE_PROFIT_PCT = 4.0
SATELLITE_STOP_LOSS_PCT = 9.0
SATELLITE_TRAILING_STOP_ATR_MULT = 1.8
SATELLITE_MAX_HOLD_DAYS = 3


def detect_mean_reversion_signal(df):
    """a) COMPRA RAPIDA si RSI(14) < 32, el cierre toca (<=) la banda inferior
    de Bollinger (20, 2std) y el volumen de la rueda supera el promedio de
    las ultimas 5 -- df: columnas close/volume, orden cronologico, YA
    cortado hasta la rueda a evaluar (ultima fila = hoy). Devuelve
    (bool, detalle dict) -- nunca solo un booleano a secas, para poder
    explicar la señal aguas arriba."""
    if len(df) < max(BOLLINGER_WINDOW, VOLUME_CONFIRM_WINDOW) + 1:
        return False, {"reason": "historial insuficiente"}

    close = df["close"]
    rsi_value = float(indicators.rsi(close, 14).iloc[-1])
    bb = indicators.bollinger_bands(close, BOLLINGER_WINDOW, BOLLINGER_NUM_STD)
    lower_band = float(bb["lower"].iloc[-1])
    last_close = float(close.iloc[-1])

    volume = df["volume"].astype(float)
    avg_volume_5d = float(volume.iloc[-(VOLUME_CONFIRM_WINDOW + 1):-1].mean())
    last_volume = float(volume.iloc[-1])

    oversold = rsi_value < RSI_OVERSOLD_THRESHOLD
    touched_lower_band = last_close <= lower_band
    volume_confirms = avg_volume_5d > 0 and last_volume > avg_volume_5d

    triggered = oversold and touched_lower_band and volume_confirms
    return triggered, {
        "tactic": "mean_reversion",
        "rsi_14": round(rsi_value, 2),
        "close": last_close,
        "bb_lower": round(lower_band, 4),
        "volume": last_volume,
        "avg_volume_5d": round(avg_volume_5d, 2),
        "oversold": oversold,
        "touched_lower_band": touched_lower_band,
        "volume_confirms": volume_confirms,
    }


def detect_volume_breakout_signal(df):
    """b) COMPRA RAPIDA si el volumen de hoy supera 2.5x el promedio de 20
    ruedas Y el cierre rompe el maximo de las ultimas 5 ruedas (sin contar
    hoy) -- df: columnas high/close/volume, orden cronologico, ya cortado
    hasta la rueda a evaluar."""
    if len(df) < VOLUME_BREAKOUT_WINDOW + 1:
        return False, {"reason": "historial insuficiente"}

    volume = df["volume"].astype(float)
    avg_volume_20d = float(volume.iloc[-(VOLUME_BREAKOUT_WINDOW + 1):-1].mean())
    last_volume = float(volume.iloc[-1])
    volume_spike = avg_volume_20d > 0 and last_volume > VOLUME_BREAKOUT_MULTIPLIER * avg_volume_20d

    last_close = float(df["close"].iloc[-1])
    # Maximo de las ultimas N ruedas SIN contar hoy -- comparar el cierre de
    # hoy contra un maximo que incluyera a hoy nunca podria "romperlo" por
    # definicion (estaria comparandose consigo mismo).
    prior_high = float(df["high"].iloc[-(BREAKOUT_LOOKBACK_DAYS + 1):-1].max())
    breaks_high = last_close > prior_high

    triggered = volume_spike and breaks_high
    return triggered, {
        "tactic": "volume_breakout",
        "close": last_close,
        "prior_high_5d": round(prior_high, 4),
        "volume": last_volume,
        "avg_volume_20d": round(avg_volume_20d, 2),
        "volume_spike": volume_spike,
        "breaks_high": breaks_high,
    }


def detect_atr_breakout_signal(df):
    """c) COMPRA RAPIDA si el rango de precio de las ultimas
    ATR_COMPRESSION_WINDOW_DAYS ruedas (SIN contar hoy) estuvo comprimido
    (promedio <= ATR_COMPRESSION_RATIO del true range promedio de las
    ATR_COMPRESSION_BASELINE_WINDOW ruedas anteriores a esa compresion) Y hoy
    el true range explota (>= ATR_EXPLOSION_MULTIPLIER veces ese rango
    comprimido) Y el cierre de hoy rompe el maximo de las ultimas
    BREAKOUT_LOOKBACK_DAYS ruedas (misma confirmacion direccional que (b) --
    sin esto, una vela de panico bajista tambien calificaria como
    "explosion"). df: columnas high/low/close, orden cronologico, ya cortado
    hasta la rueda a evaluar."""
    min_rows = ATR_COMPRESSION_BASELINE_WINDOW + ATR_COMPRESSION_WINDOW_DAYS + max(BREAKOUT_LOOKBACK_DAYS, 1) + 1
    if len(df) < min_rows:
        return False, {"reason": "historial insuficiente"}

    tr = indicators.true_range(df)
    baseline_window = tr.iloc[-(ATR_COMPRESSION_BASELINE_WINDOW + ATR_COMPRESSION_WINDOW_DAYS + 1):-(ATR_COMPRESSION_WINDOW_DAYS + 1)]
    compression_window = tr.iloc[-(ATR_COMPRESSION_WINDOW_DAYS + 1):-1]
    today_tr = float(tr.iloc[-1])

    baseline_atr = float(baseline_window.mean())
    compression_atr = float(compression_window.mean())
    compressed = baseline_atr > 0 and compression_atr < ATR_COMPRESSION_RATIO * baseline_atr
    exploded = compression_atr > 0 and today_tr > ATR_EXPLOSION_MULTIPLIER * compression_atr

    last_close = float(df["close"].iloc[-1])
    prior_high = float(df["high"].iloc[-(BREAKOUT_LOOKBACK_DAYS + 1):-1].max())
    breaks_high = last_close > prior_high

    triggered = compressed and exploded and breaks_high
    return triggered, {
        "tactic": "atr_breakout",
        "close": last_close,
        "prior_high_5d": round(prior_high, 4),
        "today_true_range": round(today_tr, 4),
        "compression_atr_3d": round(compression_atr, 4),
        "baseline_atr_20d": round(baseline_atr, 4),
        "compressed": compressed,
        "exploded": exploded,
        "breaks_high": breaks_high,
    }


# --- Position Sizing dinámico (ver conversación, evolución del motor
# cuantitativo) -- reemplaza el equal-weight fijo (1/SATELLITE_MAX_POSITIONS)
# que usaban portfolio/backtest.py::simulate_satellite_engine y
# portfolio/live_signals.py::compute_satellite_signals. SATELLITE_MAX_POSITIONS
# vive en portfolio/backtest.py (que a su vez importa ESTE módulo) -- se
# recibe como parámetro en vez de importarlo, para no crear un ciclo. ---

# Analogo a signals.py::CORE_TARGET_RISK_PCT (15.0) pero mas chico: Satelite
# ya tiene TP/SL mas ajustados y mayor frecuencia de entrada que Core, asi
# que un target de riesgo mas bajo por posicion individual es razonable --
# declarado como punto de partida, no calibrado empiricamente todavia
# (mismo criterio que cualquier otro "default de referencia" del proyecto).
SATELLITE_TARGET_RISK_PCT = 8.0


def compute_satellite_position_weight(atr_pct_value, max_slot_weight, target_risk_pct=SATELLITE_TARGET_RISK_PCT):
    """Volatility Targeting (ver conversación) -- mismo criterio que
    signals.py::compute_core_position_weight: mientras mas volatil (ATR alto)
    el activo, MENOR la posicion dentro del slot que le toca, nunca mas
    grande que max_slot_weight (1/SATELLITE_MAX_POSITIONS -- nunca se
    "libera" espacio de otro slot por tener baja volatilidad, solo se puede
    quedar CORTO del tamaño parejo, nunca superarlo)."""
    if not atr_pct_value or atr_pct_value <= 0:
        return max_slot_weight
    return min(max_slot_weight, target_risk_pct / atr_pct_value)


def kelly_fraction(win_rate_pct, profit_factor):
    """Kelly Criterium (ver conversación) -- f* = p - q/b, con p=prob. de
    ganar (win_rate YA medido), q=1-p, b=odds REALES (ganancia promedio /
    perdida promedio por operacion). b se deriva del profit_factor YA medido
    (profit_factor = ganancias_totales/perdidas_totales =
    (avg_win*n_wins)/(avg_loss*n_losses) => b = avg_win/avg_loss =
    profit_factor * q/p), NUNCA del ratio configurado take_profit_pct/
    stop_loss_pct -- version anterior de esta funcion, descartada tras
    probarla: con el techo de stop (9%) bien mas ancho que el take-profit
    (4%), esa aproximacion daba un Kelly NEGATIVO (position sizing en CERO
    para todo el Satelite) aun con mas de 50% de aciertos, porque ignora que
    la mayoria de los trades reales no llegan a tocar ni el TP ni el SL
    completo -- salen antes por trailing stop o vencimiento, con
    ganancias/perdidas tipicamente bien mas chicas que esos techos
    configurados. profit_factor, al venir de los RETORNOS REALES de cada
    trade (ver _compute_engine_metrics), no tiene ese problema.

    HALF-KELLY (f*/2), practica estandar para reducir el riesgo de
    sobre-apostar por error de estimacion sobre una muestra finita de trades
    pasados. None si no hay win_rate/profit_factor todavia (primera corrida,
    nunca se asume un edge a ciegas) -- el llamador debe tratar None como
    "sin tope adicional de Kelly, solo el de volatilidad"."""
    if win_rate_pct is None or not profit_factor or profit_factor <= 0:
        return None
    p = win_rate_pct / 100.0
    if p <= 0:
        return None
    q = 1.0 - p
    b = profit_factor * q / p
    if b <= 0:
        return None
    f = p - q / b
    return max(0.0, f / 2.0)  # negativo = "el borde esta en contra", nunca una fraccion negativa


def evaluate_short_term_signals(df):
    """Corre las 3 tacticas sobre el mismo df (ya cortado hasta hoy) y
    devuelve la lista de las que dispararon (puede ser 0 a 3 -- no son
    mutuamente excluyentes, aunque en la practica rara vez coinciden todas:
    mean reversion busca sobreventa, los 2 breakouts buscan fortaleza por
    caminos distintos -- volumen o expansion de volatilidad)."""
    triggered = []
    hit, detail = detect_mean_reversion_signal(df)
    if hit:
        triggered.append(detail)
    hit, detail = detect_volume_breakout_signal(df)
    if hit:
        triggered.append(detail)
    hit, detail = detect_atr_breakout_signal(df)
    if hit:
        triggered.append(detail)
    return triggered
