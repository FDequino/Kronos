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
from portfolio.backtest import CORE_BACKTEST_LOOKBACK_DAYS, ROUND_TRIP_COMMISSION_PCT, _fix_price_discontinuities
from portfolio.fetchers import (
    ensure_underlying_ohlcv_history, fetch_data912_historical, get_underlying_ohlcv_df,
)

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
    df = _fix_price_discontinuities(df)
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


def simulate_canje_real_position_engine(conn=None, pair=CANJE_DEFAULT_PAIR,
                                         lookback_days=CORE_BACKTEST_LOOKBACK_DAYS,
                                         commission_pct=CANJE_ROUND_TRIP_COMMISSION_PCT):
    """Backtest de la posicion REAL que de verdad se puede ejecutar con
    plata real (ver conversacion 2026-10-21: "hagamos el backtest primero" --
    antes de construir ejecucion real de Canje, el usuario pidio confirmar
    que el modelo de posicion REAL, no el teorico, tiene edge por su cuenta).

    Distinto de simulate_canje_arbitrage_engine (la version que SI paso el
    gate DSR>=0.95 de OBJETIVO_MARCO.md): ESA mide la convergencia algebraica
    de brecha_pct como si se pudiera estar largo una pata y corto la otra al
    mismo tiempo (ver su docstring, "LIMITACION declarada") -- irreal para un
    usuario retail en IOL/PPI, que no puede shortear bonos soberanos. ESTA
    funcion modela lo unico que SI es ejecutable: comprar la pata barata
    (cheap_leg, variante "D" liquidada en dolar) y quedarse DOLARIZADO en
    ella de forma continua, rotando (vender la tenencia actual + comprar la
    nueva barata) solo cuando |z|>CANJE_ZSCORE_ENTRY_THRESHOLD y la pata
    barata cambio de lado. Nunca "sale a cash" -- una vez dolarizado, salir a
    pesos no tiene sentido economico para esta estrategia (el punto es estar
    en dolares, no apostar a la convergencia del spread). El retorno diario
    es el cambio real de precio (USD) del bono efectivamente tenido --
    expuesto a TODO el riesgo propio de ese bono (tasa, riesgo pais), no solo
    al spread -- a diferencia de la version teorica.

    commission_pct se cobra en cada rotacion (1 vez, vender+comprar) --
    default CANJE_ROUND_TRIP_COMMISSION_PCT (la tarifa real de "Canje de
    Titulos Valores" de IOL/PPI, ver arriba) ES optimista si en la practica
    se ejecuta como 2 ordenes sueltas de compra/venta en vez de un producto
    de canje nativo del broker (ver portfolio/multi_leg_execution.py,
    DIFERENCIA HONESTA) -- declarado explicito, nunca asumido sin mas.

    Mismo shape de salida que simulate_canje_arbitrage_engine, para poder
    pasar esto tal cual a backtest.py::run_strategy_cpcv_validation."""
    df = compute_canje_brecha_series(pair=pair)
    if df is None:
        empty = pd.Series(dtype=float)
        return {"engine": "canje_real_position", "daily_returns_pct": empty, "idle_weight": empty, "trades": []}

    master_dates = list(df.index)[-lookback_days:]
    if len(master_dates) < 2:
        empty = pd.Series(dtype=float)
        return {"engine": "canje_real_position", "daily_returns_pct": empty, "idle_weight": empty, "trades": []}

    hurdle_tna = float(db.get_signal_config(conn).get("caucion_reference_tna") or 0.0) if conn is not None else 0.0
    idle_daily_pct = yield_engine.daily_caucion_return_pct(hurdle_tna)

    contributions = {}
    idle_flags = {}
    held_leg = None  # "a" | "b" -- que pata (ley_a/ley_b) esta tenida HOY, None = todavia no se doloarizo
    held_since_date = None
    held_since_price = None
    trades = []

    for i, date in enumerate(master_dates):
        z_now = df.loc[date, "zscore"]
        brecha_now = df.loc[date, "brecha_pct"]
        cheap_leg_now = None
        if not pd.isna(z_now) and abs(float(z_now)) > CANJE_ZSCORE_ENTRY_THRESHOLD:
            cheap_leg_now = "a" if brecha_now > 0 else "b"  # mismo criterio que compute_canje_arbitrage_signal

        if held_leg is None:
            # Todavia no se doloarizo -- entra recien con una señal real (no
            # el primer dia a ciegas), mismo umbral de entrada que la version
            # teorica, para que "cuando entrar" sea comparable entre ambas.
            if cheap_leg_now is not None:
                held_leg = cheap_leg_now
                held_since_date = date
                held_since_price = float(df.loc[date, f"{held_leg}_usd"])
                contributions[date] = idle_daily_pct  # entra al cierre de hoy, rinde caucion el dia de entrada
                idle_flags[date] = 1.0
            else:
                contributions[date] = idle_daily_pct
                idle_flags[date] = 1.0
            continue

        price_prev = float(df.loc[master_dates[i - 1], f"{held_leg}_usd"])
        price_now = float(df.loc[date, f"{held_leg}_usd"])
        day_return_pct = (price_now / price_prev - 1) * 100
        idle_flags[date] = 0.0

        if cheap_leg_now is not None and cheap_leg_now != held_leg:
            # Rotacion: se cobra la comision de vender la tenencia actual +
            # comprar la nueva, UNA vez, el dia de la rotacion -- mismo
            # criterio contable (restar una vez al cerrar) que la version
            # teorica y que paper_trading.py/backtest.py.
            day_return_pct -= commission_pct
            cum_return_pct = (price_now / held_since_price - 1) * 100 - commission_pct
            trades.append({
                "engine": "canje_real_position", "pair": pair, "leg": held_leg,
                "entry_date": held_since_date.date().isoformat(), "exit_date": date.date().isoformat(),
                "hold_days": (date - held_since_date).days, "return_pct": round(cum_return_pct, 3),
                "exit_reason": "rotacion",
            })
            held_leg = cheap_leg_now
            held_since_date = date
            held_since_price = float(df.loc[date, f"{held_leg}_usd"])

        contributions[date] = day_return_pct

    # Posicion final (si quedo dolarizado al final de la ventana) -- se
    # reporta como trade "abierto" para que n_trades/metricas no pierdan esa
    # tenencia, mismo criterio que portfolio/pairs_trading.py con posiciones
    # vivas al final del backtest.
    if held_leg is not None:
        price_last = float(df.loc[master_dates[-1], f"{held_leg}_usd"])
        trades.append({
            "engine": "canje_real_position", "pair": pair, "leg": held_leg,
            "entry_date": held_since_date.date().isoformat(), "exit_date": master_dates[-1].date().isoformat(),
            "hold_days": (master_dates[-1] - held_since_date).days,
            "return_pct": round((price_last / held_since_price - 1) * 100, 3),
            "exit_reason": "fin_de_ventana",
        })

    returns = pd.Series(contributions, dtype=float).reindex(master_dates)
    idle_weight = pd.Series(idle_flags, dtype=float).reindex(master_dates)
    return {"engine": "canje_real_position", "daily_returns_pct": returns, "idle_weight": idle_weight, "trades": trades}


# --- Arbitraje CEDEAR/subyacente real (ver conversacion/auditoria 2026-10-20,
# "profundizar en mas opciones de Canje") -------------------------------------
#
# Misma idea economica que el Canje de bonos de arriba (2 instrumentos que
# DEBERIAN implicar el mismo dolar, operados por separado, divergen y
# revierten), aplicada a un activo distinto: para los 10 CEDEARs con
# subyacente real verificado (ver forecasting.py::CEDEAR_DIRECT_UNDERLYING_SYMBOLS,
# migracion de esta misma sesion), el CEDEAR cotiza en ARS en BYMA y el
# subyacente cotiza en USD en EEUU -- el cociente precio_cedear_ARS /
# precio_subyacente_USD es, salvo un factor constante (el ratio real de
# conversion CEDEAR/accion, ej. "10 CEDEARs = 1 accion", que varia por
# simbolo y NO esta sourceado en este proyecto), un "CCL implicito por
# CEDEAR". Si ese CCL implicito se separa del CCL REAL de mercado
# (fx_rate_history, misma fuente que ya usa build_model_input) mas de lo
# habitual, es una brecha de arbitraje -- misma logica de reversion a la
# media que Canje de bonos.
#
# CLAVE: no hace falta conocer el ratio real de conversion para MEDIR la
# señal -- es una constante multiplicativa, invariante al z-score de la
# serie (ratio_pct = implied_ccl/ccl_real) contra su propio promedio
# rolling, igual que el signal de Canje no depende de saber el precio
# "correcto" de un bono, solo de su desvio relativo a su propia historia.
#
# EVIDENCIA (historial completo disponible via data912 + yfinance,
# split 70/30 in-sample/out-of-sample, comision real 1.21% como piso -- ver
# GAP DE EJECUCION abajo, este costo es una referencia, no esta confirmado
# para esta operatoria puntual): .devlogs/cedear_arb_explore_v1.log
#
#   Simbolo | Sharpe IS   | Sharpe OOS       | Trades OOS
#   AAPL    | 1.04        | 0.63 (vivo)      | 41 (59%)
#   MSFT    | 0.96        | 0.66 (vivo)      | 32 (66%)
#   GOOGL   | 0.75        | 0.73 (vivo)      | 43 (53%)
#   TSLA    | 0.68        | 0.42 (debil)     | 19 (37%)
#   MELI    | 0.67        | 0.36 (debil)     | 27 (59%)
#   AMZN    | 1.15        | 0.19 (debil)     | 19 (47%)
#   META    | 0.69        | 0.23 (debil)     | 24 (46%)
#   SPY     | 0.85        | -1.31 (murio)    | 13 (8%)
#   QQQ     | 0.86        | -1.20 (murio)    | 13 (8%)
#
# Mismo patron que Canje de bonos: los 2 instrumentos mas liquidos/arbitrados
# (SPY/QQQ, los ETFs mas transitados del universo) murieron OOS -- otros
# participantes ya arbitran esa brecha. AAPL/MSFT/GOOGL sostienen edge real y
# reciente con muestra decente. NVDA sin historial local suficiente
# (underlying_price_history vacia), no evaluado.
#
# GAP DE EJECUCION -- POR QUE ESTO ES UNA SEÑAL PARA SEGUIR INVESTIGANDO,
# NO UN MOTOR LISTO PARA OPERAR CON CAPITAL REAL COMO SI LO ES CANJE DE
# BONOS (declarado explicito, nunca ocultado):
#   1. El ratio real CEDEAR/accion (necesario para dimensionar una orden
#      real, aunque no para medir la señal) NO esta sourceado en este
#      proyecto -- haria falta confirmarlo contra una fuente real (BYMA/CNV
#      publica esta tabla) antes de operar.
#   2. A diferencia del Canje de bonos (AL30/GD30, ambas patas operables en
#      la MISMA cuenta/mercado, BYMA), esto requiere ejecutar 2 patas en 2
#      MERCADOS distintos (BYMA en ARS + NYSE/NASDAQ en USD) de forma
#      coordinada -- no esta confirmado que IOL o PPI permitan esto desde
#      la misma cuenta sin fricciones adicionales (PPI menciona una cuenta
#      "PPI Global" separada para mercado internacional, sin confirmar
#      todavia si opera coordinado con la cuenta local).
#   3. El costo real de este tipo de operacion (2 mercados, 2 monedas) no
#      esta confirmado -- 1.21% (la tarifa de Canje de bonos) es un PISO de
#      referencia, probablemente optimista para una operatoria mas compleja
#      con 2 patas en 2 paises.
# Hasta confirmar 1-3, esto se deja como funcion de backtest/investigacion
# (simulate_cedear_underlying_arbitrage_engine), nunca conectado a
# log_canje_snapshot/strategy_signal_log ni al backtest unificado.

CEDEAR_ARBITRAGE_SYMBOLS = ("SPY", "QQQ", "AAPL", "TSLA", "MSFT", "AMZN", "GOOGL", "META", "MELI")
CEDEAR_ARBITRAGE_ZSCORE_WINDOW = 20
CEDEAR_ARBITRAGE_ZSCORE_MIN_PERIODS = 20
CEDEAR_ARBITRAGE_ENTRY_THRESHOLD = 2.0
CEDEAR_ARBITRAGE_EXIT_ZSCORE = 0.5
CEDEAR_ARBITRAGE_MAX_HOLD_DAYS = 30


def compute_cedear_underlying_brecha_series(conn, symbol):
    """DataFrame diario alineado (inner join por fecha entre CEDEAR, el
    subyacente real, y el CCL real) con columns: implied_ccl, ratio_pct
    (implied_ccl/ccl_real * 100), zscore. None si falta alguna serie o si,
    ya alineadas, no alcanzan CEDEAR_ARBITRAGE_ZSCORE_MIN_PERIODS + 1 filas.

    No requiere el ratio real CEDEAR/accion -- ver nota de GAP DE EJECUCION
    arriba del modulo, el z-score de ratio_pct contra su propio promedio
    rolling es invariante a esa constante."""
    try:
        cedear_df = fetch_data912_historical(symbol, "cedear")
    except Exception:
        return None
    cedear_df = _fix_price_discontinuities(cedear_df)
    cedear_close = cedear_df.set_index(cedear_df["timestamps"].dt.normalize())["close"]
    cedear_close = cedear_close[~cedear_close.index.duplicated(keep="last")].sort_index()

    ensure_underlying_ohlcv_history(conn, symbol)
    underlying_df = get_underlying_ohlcv_df(conn, symbol)
    if underlying_df.empty:
        return None
    underlying_close = underlying_df.set_index(underlying_df["timestamps"].dt.normalize())["close"]
    underlying_close = underlying_close[~underlying_close.index.duplicated(keep="last")].sort_index()

    ccl_rate_map = db.get_fx_rate_history(conn, "contadoconliqui")  # {fecha: venta}
    ccl_series = pd.Series({
        pd.Timestamp(fecha).normalize(): venta for fecha, venta in ccl_rate_map.items() if venta is not None
    }).sort_index()

    df = pd.DataFrame({
        "cedear_ars": cedear_close, "underlying_usd": underlying_close, "ccl_real": ccl_series,
    }).dropna(how="any")
    if len(df) < CEDEAR_ARBITRAGE_ZSCORE_MIN_PERIODS + 1:
        return None

    df["implied_ccl"] = df["cedear_ars"] / df["underlying_usd"]
    df["ratio_pct"] = (df["implied_ccl"] / df["ccl_real"]) * 100
    df["zscore"] = features._rolling_zscore(
        df["ratio_pct"], window=CEDEAR_ARBITRAGE_ZSCORE_WINDOW, min_periods=CEDEAR_ARBITRAGE_ZSCORE_MIN_PERIODS,
    )
    return df


def simulate_cedear_underlying_arbitrage_engine(conn, symbol, lookback_days=CORE_BACKTEST_LOOKBACK_DAYS, commission_pct=0.0):
    """Backtest historico -- mismo shape de salida y misma mecanica de
    entrada/salida por z-score que simulate_canje_arbitrage_engine (ver esa
    funcion para el detalle de la logica compartida). direction apuesta a
    que ratio_pct REVIERTE hacia su media (z>0 => se asume que va a bajar,
    direction=-1 sobre el NIVEL de ratio_pct; z<0 => direction=+1).

    El retorno diario se mide como variacion PORCENTUAL de ratio_pct (no en
    puntos absolutos como brecha_pct de Canje de bonos) -- ratio_pct no esta
    acotado al mismo rango entre simbolos (depende del ratio real de
    conversion de cada CEDEAR, ver GAP DE EJECUCION arriba), asi que medirlo
    en puntos no seria comparable entre simbolos; en % si lo es.

    Capital ocioso (sin posicion abierta) NO rinde Caucion aca a proposito
    (a diferencia de Canje de bonos) -- esta funcion todavia no se conecta a
    ningun blend de capital real (ver GAP DE EJECUCION), mezclar con
    Caucion fingiria una asignacion de capital que no existe."""
    df = compute_cedear_underlying_brecha_series(conn, symbol)
    if df is None:
        empty = pd.Series(dtype=float)
        return {"engine": "cedear_arb", "symbol": symbol, "daily_returns_pct": empty, "idle_weight": empty, "trades": []}

    master_dates = list(df.index)[-lookback_days:]
    if len(master_dates) < 2:
        empty = pd.Series(dtype=float)
        return {"engine": "cedear_arb", "symbol": symbol, "daily_returns_pct": empty, "idle_weight": empty, "trades": []}

    contributions = {d: 0.0 for d in master_dates}
    idle_flags = {d: 1.0 for d in master_dates}
    position = None
    trades = []

    for i, date in enumerate(master_dates):
        ratio_now = float(df.loc[date, "ratio_pct"])
        z_now = df.loc[date, "zscore"]

        if position is not None:
            ratio_prev = float(df.loc[master_dates[i - 1], "ratio_pct"])
            day_return_pct = position["direction"] * (ratio_now - ratio_prev) / ratio_prev * 100
            contributions[date] = day_return_pct
            idle_flags[date] = 0.0
            position["hold_days"] += 1
            position["cum_return_pct"] = (1 + position["cum_return_pct"] / 100) * (1 + day_return_pct / 100) * 100 - 100

            should_exit = (
                position["hold_days"] >= CEDEAR_ARBITRAGE_MAX_HOLD_DAYS
                or (not pd.isna(z_now) and abs(float(z_now)) <= CEDEAR_ARBITRAGE_EXIT_ZSCORE)
            )
            if should_exit:
                position["cum_return_pct"] -= commission_pct
                contributions[date] -= commission_pct
                trades.append({
                    "engine": "cedear_arb", "symbol": symbol, "direction": position["direction"],
                    "entry_date": position["entry_date"], "exit_date": date.date().isoformat(),
                    "hold_days": position["hold_days"], "return_pct": round(position["cum_return_pct"], 3),
                    "exit_reason": "time_stop" if position["hold_days"] >= CEDEAR_ARBITRAGE_MAX_HOLD_DAYS else "reversion",
                })
                position = None
            continue

        if not pd.isna(z_now) and abs(float(z_now)) > CEDEAR_ARBITRAGE_ENTRY_THRESHOLD:
            position = {
                "direction": -1.0 if z_now > 0 else 1.0,
                "entry_date": date.date().isoformat(), "hold_days": 0, "cum_return_pct": 0.0,
            }

    returns = pd.Series(contributions, dtype=float).reindex(master_dates)
    idle_weight = pd.Series(idle_flags, dtype=float).reindex(master_dates)
    return {"engine": "cedear_arb", "symbol": symbol, "daily_returns_pct": returns, "idle_weight": idle_weight, "trades": trades}
