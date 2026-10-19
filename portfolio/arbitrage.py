"""Arbitraje de Canje MEP/CCL (ver conversacion) -- AL30 y GD30 son el MISMO
bono soberano, solo que de distinta ley: AL30 es ley argentina y GD30 es ley
Nueva York. Cada uno tiene ademas su version "D" (AL30D/GD30D), que es el
mismo bono pero liquidado en dolar cable en vez de pesos. Como AL30 y AL30D
son literalmente el mismo titulo, el cociente precio_ARS/precio_USD de ese
par ya es, por construccion, un dolar implicito (MEP via AL30, CCL via
AL30D segun donde se liquide la pata vendedora) -- lo mismo para GD30/GD30D.

Al ser AL30 y GD30 economicamente equivalentes (mismo flujo de fondos,
distinta jurisdiccion), sus 2 dolares implicitos deberian moverse pegados.
Cuando se separan mas de lo habitual es la "brecha de canje": conviene
dolarizarse por la pata que da el dolar implicito mas barato y, si ya se
tiene la tenencia cara, canjearla (swap) hacia la barata.

Fuente de precios: portfolio/fetchers.py::fetch_data912_historical (misma
fuente que el resto del proyecto, ya normaliza bonos a precio "por 1 unidad"
dividiendo por 100). Simbolos confirmados reales contra data912 EN VIVO en
esta conversacion (fetch_data912_live("arg_bonds")): AL30, AL30D, GD30,
GD30D existen tal cual, sin sufijo adicional.

Z-score: reusa portfolio/features.py::_rolling_zscore (mismo mecanismo que
portfolio/pairs_trading.py), pero con ventana de 20 dias -- PEDIDO EXPLICITO
de este modulo ("promedio de 20 dias"), a diferencia de la ventana de 60
dias que es el default general del resto del proyecto (features.ROLLING_NORM_WINDOW).
No hace falta conexion a la base de datos: a diferencia de pairs_trading.py
(que arrastra historial propio via portfolio/backtest.py::fetch_ticker_history
para la serie en USD CCL de CEDEARs/acciones), este modulo solo necesita las
4 series de precio de bonos que data912 ya sirve en vivo, nunca pasa por
SQLite."""
import pandas as pd

from portfolio import db, features, yield_engine
from portfolio.backtest import CORE_BACKTEST_LOOKBACK_DAYS
from portfolio.fetchers import fetch_data912_historical

CANJE_PAIR = ("AL30", "GD30")
BOND_SYMBOLS = {"al30_ars": "AL30", "al30_usd": "AL30D", "gd30_ars": "GD30", "gd30_usd": "GD30D"}

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


def compute_canje_brecha_series():
    """DataFrame diario alineado (inner join por fecha entre las 4 patas)
    con columns: implied_fx_al30, implied_fx_gd30, brecha_pct, zscore.
    None si falta alguna de las 4 series o si, ya alineadas, no alcanzan
    CANJE_ZSCORE_MIN_PERIODS + 1 filas (el +1 es la fila de HOY que se
    quiere puntuar, no cuenta como parte de la ventana que la normaliza)."""
    closes = {key: _fetch_bond_close_series(symbol) for key, symbol in BOND_SYMBOLS.items()}
    if any(series is None or series.empty for series in closes.values()):
        return None

    df = pd.DataFrame(closes).dropna(how="any")
    if len(df) < CANJE_ZSCORE_MIN_PERIODS + 1:
        return None

    df["implied_fx_al30"] = df["al30_ars"] / df["al30_usd"]
    df["implied_fx_gd30"] = df["gd30_ars"] / df["gd30_usd"]
    # brecha_pct > 0 => el dolar implicito de GD30 es MAS CARO que el de
    # AL30 (AL30 da mas dolares por el mismo monto de pesos) => AL30 es la
    # pata barata hoy. brecha_pct < 0 es el caso inverso.
    df["brecha_pct"] = (df["implied_fx_gd30"] - df["implied_fx_al30"]) / df["implied_fx_al30"] * 100
    df["zscore"] = features._rolling_zscore(
        df["brecha_pct"], window=CANJE_ZSCORE_WINDOW, min_periods=CANJE_ZSCORE_MIN_PERIODS,
    )
    return df


def compute_canje_arbitrage_signal():
    """Punto de entrada unico (ver portfolio/live_signals.py) -- None si no
    hay datos suficientes o si el z-score de hoy esta dentro de
    +-CANJE_ZSCORE_ENTRY_THRESHOLD (sin oportunidad), dict de señal si lo
    supera. Sin parametro `conn`: a diferencia de pairs_trading.scan_pairs,
    esto no necesita la base de datos (ver docstring del modulo)."""
    df = compute_canje_brecha_series()
    if df is None:
        return None

    last = df.iloc[-1]
    z = last["zscore"]
    if pd.isna(z) or abs(z) <= CANJE_ZSCORE_ENTRY_THRESHOLD:
        return None

    cheap_leg, expensive_leg = CANJE_PAIR if last["brecha_pct"] > 0 else CANJE_PAIR[::-1]
    return {
        "pair": CANJE_PAIR,
        "zscore": round(float(z), 3),
        "brecha_pct": round(float(last["brecha_pct"]), 3),
        "cheap_leg": cheap_leg,
        "expensive_leg": expensive_leg,
        "implied_fx_al30": round(float(last["implied_fx_al30"]), 2),
        "implied_fx_gd30": round(float(last["implied_fx_gd30"]), 2),
        "as_of": df.index[-1].date().isoformat(),
        "note": (
            f"La brecha de canje {CANJE_PAIR[0]}/{CANJE_PAIR[1]} se desvio "
            f"{round(float(last['brecha_pct']), 2)}% (z={round(float(z), 2)}) de su promedio de "
            f"{CANJE_ZSCORE_WINDOW} dias -- el dolar implicito es mas barato via {cheap_leg}. "
            f"Si ya se tiene tenencia en {expensive_leg}, evaluar canjearla hacia {cheap_leg}; "
            f"si no, dolarizarse hoy via {cheap_leg}/{cheap_leg}D conviene mas que via {expensive_leg}."
        ),
    }


def simulate_canje_arbitrage_engine(conn=None, lookback_days=CORE_BACKTEST_LOOKBACK_DAYS):
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
    comparable con los demas motores del backtest unificado."""
    df = compute_canje_brecha_series()
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
                trades.append({
                    "engine": "canje", "pair": CANJE_PAIR, "direction": position["direction"],
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
