"""Arbitraje de Canje MEP/CCL (ver conversacion) -- 2 bonos soberanos de
distinta ley pero economicamente equivalentes (mismo flujo de fondos,
distinta jurisdiccion: ley argentina vs. ley Nueva York) son, por
construccion, el mismo activo visto desde 2 lados. Cada uno tiene ademas su
version "D" (ej. AL30D/GD30D), que es el mismo bono pero liquidado en dolar
cable en vez de pesos -- el cociente precio_ARS/precio_USD de un par
ley-A/ley-A-D ya es, por construccion, un dolar implicito (MEP via la pata
en pesos, CCL via la pata "D" segun donde se liquide la pata vendedora).

Al ser las 2 leyes economicamente equivalentes, sus 2 dolares implicitos
deberian moverse pegados. Cuando se separan mas de lo habitual es la
"brecha de canje": conviene dolarizarse por la pata que da el dolar
implicito mas barato y, si ya se tiene la tenencia cara, canjearla (swap)
hacia la barata.

PAR POR DEFECTO -- cambiado de AL30/GD30 a AL29/GD29 (ver conversacion/
auditoria 2026-10-20, "empujar Canje a edge genuino"): un backtest contra el
HISTORIAL COMPLETO disponible en data912 (1200+ ruedas desde 2021-09, no
solo las 300 que usa el backtest unificado) partido 70/30 in-sample/
out-of-sample, con la comision real del usuario (1.21%, ver
CANJE_ROUND_TRIP_COMMISSION_PCT), dio:

  Par         | Sharpe IS (2021-2025) | Sharpe OOS (~17 meses recientes) | Trades OOS
  AL29/GD29   | 1.80                  | 0.73 (sigue vivo)                | 19 (74% ganadores)
  AE38/GD38   | 1.51                  | 0.35                             | 20 (45%)
  AL41/GD41   | 1.18                  | 0.17                             | 13 (46%)
  AL30/GD30   | 0.92                  | -0.27 (murio)                    | 16 (19%)
  AL35/GD35   | 1.40                  | -0.84 (murio)                    | 23 (17%)

(log completo: .devlogs/canje_improvement_v1.log) AL30/GD30 -- el par mas
liquido y conocido, el default anterior -- es justo el que se murio: lectura
economica consistente con que el par mas transitado es el primero en ser
arbitrado por otros participantes del mercado hasta desaparecer el edge.
AL29/GD29 es el UNICO de los 5 que sostiene edge real y reciente con muestra
decente, por eso pasa a ser el default. El motor sigue soportando CUALQUIER
par (parametro `pair` en las 3 funciones publicas) -- nada fuerza AL29/GD29
a nivel de codigo, es una eleccion de default basada en evidencia, no un
hardcodeo nuevo en el lugar del viejo.

Fuente de precios: portfolio/fetchers.py::fetch_data912_historical (misma
fuente que el resto del proyecto, ya normaliza bonos a precio "por 1 unidad"
dividiendo por 100).

Z-score: reusa portfolio/features.py::_rolling_zscore (mismo mecanismo que
portfolio/pairs_trading.py), pero con ventana de 20 dias -- PEDIDO EXPLICITO
de este modulo ("promedio de 20 dias"), a diferencia de la ventana de 60
dias que es el default general del resto del proyecto (features.ROLLING_NORM_WINDOW).
No hace falta conexion a la base de datos para el calculo de la señal/brecha
-- a diferencia de pairs_trading.py (que arrastra historial propio via
portfolio/backtest.py::fetch_ticker_history para la serie en USD CCL de
CEDEARs/acciones), este modulo solo necesita las 4 series de precio de
bonos que data912 ya sirve en vivo, nunca pasa por SQLite (conn solo se usa
para leer signal_config.caucion_reference_tna y para loguear el snapshot
diario, ver log_canje_snapshot)."""
import pandas as pd

from portfolio import db, features, yield_engine
from portfolio.backtest import CORE_BACKTEST_LOOKBACK_DAYS, ROUND_TRIP_COMMISSION_PCT
from portfolio.fetchers import fetch_data912_historical

# Ver evidencia completa en el docstring del modulo arriba.
CANJE_DEFAULT_PAIR = ("AL29", "GD29")

# Pedido explicito ("promedio de 20 dias") -- distinto del default de 60
# dias que usa el resto del proyecto (features.ROLLING_NORM_WINDOW), ver
# docstring del modulo.
CANJE_ZSCORE_WINDOW = 20
CANJE_ZSCORE_MIN_PERIODS = 20
CANJE_ZSCORE_ENTRY_THRESHOLD = 2.0
# Salida: mismo criterio declarado que portfolio/pairs_trading.py
# (PAIRS_EXIT_ZSCORE/PAIRS_MAX_HOLD_DAYS) -- reversion a zscore normal o
# time-stop, lo que pase primero.
CANJE_EXIT_ZSCORE = 0.5
CANJE_MAX_HOLD_DAYS = 30
# Corregido (ver conversacion/auditoria 2026-10-20, hallazgo del fork de
# auditoria de Canje): usar ROUND_TRIP_COMMISSION_PCT*2 (5.0%) trataba un
# canje de bonos como si fueran 2 operaciones normales de acciones -- con eso
# puesto, los 12 trades reales de la ventana de backtest (que sin comision
# ganaban los 12, +12.3% USD anual, Sharpe 1.72, Max DD -4.1%) perdian
# garantizado pase lo que pase (edge bruto de apenas 0.4%-1.5% por operacion).
# Tarifa real tomada de los tarifarios PUBLICOS de los 2 brokers de este
# usuario (IOL y PPI, consultados 2026-10-20):
#   - PPI: linea explicita "Canjes de Titulos Valores", hasta 1% + IVA (21%)
#     = ~1.21% -- UNA sola comision para todo el swap, no por pata
#     (portfoliopersonal.com/Contenido/comisiones).
#   - IOL: sin tarifa especial de canje -- se ejecuta como 2 operaciones de
#     bonos a la comision estandar del tramo GOLD: 0.50% + IVA por pata x 2
#     patas = ~1.21% (coincide con PPI)
#     (iol.invertironline.com/servicios/tarifas).
# CONFIRMADO con el usuario (2026-10-20): esta en el tramo mas bajo tanto en
# IOL como en PPI -- 1.21% es la tarifa REAL vigente, no una estimacion
# conservadora. Con AL30/GD30 y esta tarifa el motor quedaba en -0.60% USD
# anual / Sharpe -0.06 / 3 de 12 trades ganadores sobre la ventana estandar
# de 300 ruedas -- practicamente breakeven neto (ver arriba, ese par ya
# habia perdido su edge en ese tramo). Ajustar si el tramo de comision real
# cambia (ej. si el volumen mensual operado sube a Platinum/Black) --
# declarado explicito aca, nunca un numero inventado.
CANJE_ROUND_TRIP_COMMISSION_PCT = 1.21


def _bond_symbols_for_pair(pair):
    """pair: tupla (ley_a, ley_b), ej. ("AL29", "GD29"). Devuelve las 4
    series a pedirle a data912: cada ley en pesos y su version "D" (dolar
    cable) -- ver docstring del modulo."""
    law_a, law_b = pair
    return {"a_ars": law_a, "a_usd": f"{law_a}D", "b_ars": law_b, "b_usd": f"{law_b}D"}


def _fetch_bond_close_series(symbol):
    """Serie de cierres indexada por fecha (normalizada a dia, sin hora) --
    None si data912 no tiene este simbolo o la request falla (nunca
    propaga la excepcion, el caller decide que hacer con una pata faltante)."""
    try:
        df = fetch_data912_historical(symbol, "bono")
    except Exception:
        return None
    series = df.set_index(df["timestamps"].dt.normalize())["close"]
    return series[~series.index.duplicated(keep="last")].sort_index()


def compute_canje_brecha_series(pair=CANJE_DEFAULT_PAIR):
    """DataFrame diario alineado (inner join por fecha entre las 4 patas)
    con columns: implied_fx_a, implied_fx_b, brecha_pct, zscore. None si
    falta alguna de las 4 series o si, ya alineadas, no alcanzan
    CANJE_ZSCORE_MIN_PERIODS + 1 filas (el +1 es la fila de HOY que se
    quiere puntuar, no cuenta como parte de la ventana que la normaliza).

    pair: tupla (ley_a, ley_b) -- default CANJE_DEFAULT_PAIR, ver docstring
    del modulo para la evidencia detras de esa eleccion. Cualquier otro par
    con las 4 series disponibles en data912 (ley, ley+"D" de cada lado)
    funciona igual."""
    symbols = _bond_symbols_for_pair(pair)
    closes = {key: _fetch_bond_close_series(symbol) for key, symbol in symbols.items()}
    if any(series is None or series.empty for series in closes.values()):
        return None

    df = pd.DataFrame(closes).dropna(how="any")
    if len(df) < CANJE_ZSCORE_MIN_PERIODS + 1:
        return None

    df["implied_fx_a"] = df["a_ars"] / df["a_usd"]
    df["implied_fx_b"] = df["b_ars"] / df["b_usd"]
    # brecha_pct > 0 => el dolar implicito de la ley B es MAS CARO que el de
    # la ley A (la ley A da mas dolares por el mismo monto de pesos) => la
    # ley A es la pata barata hoy. brecha_pct < 0 es el caso inverso.
    df["brecha_pct"] = (df["implied_fx_b"] - df["implied_fx_a"]) / df["implied_fx_a"] * 100
    df["zscore"] = features._rolling_zscore(
        df["brecha_pct"], window=CANJE_ZSCORE_WINDOW, min_periods=CANJE_ZSCORE_MIN_PERIODS,
    )
    return df


def compute_canje_arbitrage_signal(pair=CANJE_DEFAULT_PAIR):
    """Punto de entrada unico (ver portfolio/live_signals.py) -- None si no
    hay datos suficientes o si el z-score de hoy esta dentro de
    +-CANJE_ZSCORE_ENTRY_THRESHOLD (sin oportunidad), dict de señal si lo
    supera. Sin parametro `conn`: a diferencia de pairs_trading.scan_pairs,
    esto no necesita la base de datos (ver docstring del modulo)."""
    df = compute_canje_brecha_series(pair=pair)
    if df is None:
        return None

    last = df.iloc[-1]
    z = last["zscore"]
    if pd.isna(z) or abs(z) <= CANJE_ZSCORE_ENTRY_THRESHOLD:
        return None

    cheap_leg, expensive_leg = pair if last["brecha_pct"] > 0 else pair[::-1]
    return {
        "pair": pair,
        "zscore": round(float(z), 3),
        "brecha_pct": round(float(last["brecha_pct"]), 3),
        "cheap_leg": cheap_leg,
        "expensive_leg": expensive_leg,
        "implied_fx_a": round(float(last["implied_fx_a"]), 2),
        "implied_fx_b": round(float(last["implied_fx_b"]), 2),
        "as_of": df.index[-1].date().isoformat(),
        "note": (
            f"La brecha de canje {pair[0]}/{pair[1]} se desvio "
            f"{round(float(last['brecha_pct']), 2)}% (z={round(float(z), 2)}) de su promedio de "
            f"{CANJE_ZSCORE_WINDOW} dias -- el dolar implicito es mas barato via {cheap_leg}. "
            f"Si ya se tiene tenencia en {expensive_leg}, evaluar canjearla hacia {cheap_leg}; "
            f"si no, dolarizarse hoy via {cheap_leg}/{cheap_leg}D conviene mas que via {expensive_leg}."
        ),
    }


def log_canje_snapshot(conn, pair=CANJE_DEFAULT_PAIR):
    """Log diario en vivo (ver conversacion/auditoria 2026-10-20, mismo
    patron ya usado por portfolio/fixed_income.py::log_carry_trade_snapshot
    y portfolio/synthetic_dollar.py::log_synthetic_dollar_snapshot) --
    persiste el snapshot de HOY en strategy_signal_log con
    strategy='canje'. El backtest con historial completo ya mostro que
    AL29/GD29 sostiene edge out-of-sample (ver docstring del modulo), pero
    eso es evidencia RETROSPECTIVA -- este log junta evidencia hacia
    ADELANTE (walk-forward real) para confirmar que el edge sigue vivo HOY,
    no solo en el promedio historico, antes de confiar en el con mas
    capital o ejecucion automatica.

    best_spread_pct=None (no 0.0) si no hay señal hoy (z-score dentro del
    umbral, o falta algun dato) -- "sin oportunidad hoy" es un dato real,
    se loguea igual, nunca se omite la corrida."""
    signal = compute_canje_arbitrage_signal(pair=pair)
    best = signal["brecha_pct"] if signal else None
    return db.insert_strategy_signal_log(conn, strategy="canje", payload=signal, best_spread_pct=best)


def simulate_canje_arbitrage_engine(conn=None, pair=CANJE_DEFAULT_PAIR, lookback_days=CORE_BACKTEST_LOOKBACK_DAYS, commission_pct=0.0):
    """Backtest historico del Motor de Arbitraje de Canje (ver conversacion,
    'backtest unificado') -- mismo shape de salida que simulate_pairs_engine/
    simulate_factor_engine: {"engine": "canje", "daily_returns_pct": pd.Series,
    "idle_weight": pd.Series, "trades": [...]}.

    LIMITACION declarada (mismo espiritu que pairs_trading.simulate_pairs_engine
    para su pata "cara" teorica): sin modelar el canje fisico real de bonos en
    custodia (que tiene costos/tiempos de transferencia no capturados aca), el
    retorno se mide como la CAPTURA TEORICA de la convergencia de brecha_pct
    hacia 0 -- al abrir con |z|>CANJE_ZSCORE_ENTRY_THRESHOLD, la posicion
    apuesta a que brecha_pct se reduzca en magnitud (direction = -sign(brecha_pct
    al momento de abrir)); el retorno diario es direction * (brecha_pct[t] -
    brecha_pct[t-1]) -- brecha_pct ya esta en puntos porcentuales, se usa
    directo como "retorno capturado" de ese dia, no una medicion de ejecucion
    1:1. Solo 1 posicion a la vez (una sola pata de canje posible), capital
    ocioso (sin posicion abierta) rinde Caucion, mismo criterio que el resto
    de los motores.

    Usa SIEMPRE el historial COMPLETO disponible de data912 para calcular el
    z-score (warmup correcto desde el primer dia), y recorta la SIMULACION
    (no el calculo del z-score) a los ultimos `lookback_days` para ser
    comparable con los demas motores del backtest unificado.

    pair: tupla (ley_a, ley_b) -- default CANJE_DEFAULT_PAIR (ver docstring
    del modulo). commission_pct (ver CANJE_ROUND_TRIP_COMMISSION_PCT, Fase 4
    del plan de unificacion): costo de transaccion restado UNA VEZ al cerrar
    la posicion -- default 0.0 para no romper ningun llamador que todavia no
    lo pase explicito."""
    df = compute_canje_brecha_series(pair=pair)
    if df is None:
        empty = pd.Series(dtype=float)
        return {"engine": "canje", "daily_returns_pct": empty, "idle_weight": empty, "trades": []}

    master_dates = list(df.index)[-lookback_days:]
    if len(master_dates) < 2:
        empty = pd.Series(dtype=float)
        return {"engine": "canje", "daily_returns_pct": empty, "idle_weight": empty, "trades": []}

    hurdle_tna = float(db.get_signal_config(conn).get("caucion_reference_tna") or 0.0) if conn is not None else 0.0
    idle_daily_pct = yield_engine.daily_caucion_return_pct(hurdle_tna)

    contributions = {d: idle_daily_pct for d in master_dates}
    idle_flags = {d: 1.0 for d in master_dates}
    position = None  # {"direction": +-1, "entry_date": ..., "hold_days": int, "cum_return_pct": float}
    trades = []

    for i, date in enumerate(master_dates):
        brecha_now = float(df.loc[date, "brecha_pct"])
        z_now = df.loc[date, "zscore"]

        if position is not None:
            brecha_prev = float(df.loc[master_dates[i - 1], "brecha_pct"])
            day_return_pct = position["direction"] * (brecha_now - brecha_prev)
            contributions[date] = day_return_pct
            idle_flags[date] = 0.0
            position["hold_days"] += 1
            position["cum_return_pct"] = (1 + position["cum_return_pct"] / 100) * (1 + day_return_pct / 100) * 100 - 100

            should_exit = (
                position["hold_days"] >= CANJE_MAX_HOLD_DAYS
                or (not pd.isna(z_now) and abs(float(z_now)) <= CANJE_EXIT_ZSCORE)
            )
            if should_exit:
                # Comision ida+vuelta de las 2 patas, restada UNA VEZ al
                # cerrar (ver commission_pct arriba) -- mismo criterio
                # contable que pairs_trading.simulate_pairs_engine.
                position["cum_return_pct"] -= commission_pct
                contributions[date] -= commission_pct
                trades.append({
                    "engine": "canje", "pair": pair, "direction": position["direction"],
                    "entry_date": position["entry_date"], "exit_date": date.date().isoformat(),
                    "hold_days": position["hold_days"], "return_pct": round(position["cum_return_pct"], 3),
                    "exit_reason": "time_stop" if position["hold_days"] >= CANJE_MAX_HOLD_DAYS else "reversion",
                })
                position = None
            continue

        if not pd.isna(z_now) and abs(float(z_now)) > CANJE_ZSCORE_ENTRY_THRESHOLD:
            position = {
                "direction": -1.0 if brecha_now > 0 else 1.0,
                "entry_date": date.date().isoformat(), "hold_days": 0, "cum_return_pct": 0.0,
            }

    returns = pd.Series(contributions, dtype=float).reindex(master_dates)
    idle_weight = pd.Series(idle_flags, dtype=float).reindex(master_dates)
    return {"engine": "canje", "daily_returns_pct": returns, "idle_weight": idle_weight, "trades": trades}
