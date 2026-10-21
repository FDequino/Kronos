"""Motor de Factor Investing Multi-Factor (ver conversacion) -- puntua
semanalmente el universo de CEDEARs (portfolio/backtest.py::UNIVERSE, solo
asset_type='cedear': el pedido es puntual sobre CEDEARs, no sobre acciones
locales/bonos) combinando 3 factores clasicos:

  - Momentum: combinacion de retorno 3 meses (63 ruedas) y 12 meses (252
    ruedas) -- activos que vienen subiendo tienden a seguir subiendo en el
    corto/mediano plazo (efecto bien documentado en la literatura de factor
    investing, aunque la combinacion/ponderacion exacta usada aca es una
    eleccion declarada, no LA formula "correcta" de ningun paper puntual).
  - Low Volatility: 1/ATR% -- activos MENOS volatiles puntuan MAS alto (el
    "low vol anomaly": activos de baja volatilidad historicamente no rinden
    peor ajustado por riesgo que los de alta volatilidad, pese a la
    intuicion de que mas riesgo = mas retorno esperado).
  - Value: 1/PE -- activos "baratos" relativo a sus ganancias puntuan mas
    alto. Reusa portfolio/fundamentals.py::fetch_fundamentals (yfinance) tal
    cual, no se reimplementa el fetch de PE. Dato faltante (yfinance sin
    cobertura, PE negativo/nulo) excluye al ticker de la normalizacion de
    ESE factor puntual -- nunca se fabrica un PE, ver compute_value_factor.

Los 3 factores viven en escalas completamente distintas (un % de retorno, un
inverso de ATR%, un inverso de PE) -- se normalizan con un Z-SCORE
CROSS-SECCIONAL (entre TICKERS del mismo dia, ver _cross_sectional_zscore)
antes de combinarse. Esto es DISTINTO de portfolio/features.py::_rolling_zscore
(esa es temporal, de UN activo contra su propia historia) -- no confundir ni
reusar una por la otra.

Cache SEMANAL (ver ensure_factor_scores/FACTOR_STALENESS_DAYS) en la tabla
factor_scores (ver schema.sql) -- mismo patron de frescura que
portfolio/fetchers.py::ensure_benchmark_history, nunca recalcula en cada
request.

Scope explicito: este motor emite una señal de ROTACION (que activos
conviene sobreponderar), no una orden de compra/venta con TP/SL como
Core/Satelite -- la integracion a portfolio/live_signals.py (ver
conversacion) la consume como una sugerencia de rebalanceo, no como un
trade con salida automatica."""
import math
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from portfolio import backtest, db, indicators, yield_engine
from portfolio.forecasting import build_model_input
from portfolio.fundamentals import fetch_fundamentals

CEDEAR_UNIVERSE = [(symbol, asset_type) for symbol, asset_type in backtest.UNIVERSE if asset_type == "cedear"]

# Momentum: combinacion declarada (promedio simple, no un peso optimizado) de
# 3 meses (reaccion reciente) y 12 meses (tendencia de fondo) -- "N ruedas"
# aproxima meses calendario a ruedas habiles (21/mes), mismo criterio usado
# en el resto del proyecto (ver HORIZON_DAYS/LOOKBACK de portfolio/backtest.py).
MOMENTUM_3M_DAYS = 63
MOMENTUM_12M_DAYS = 252
MOMENTUM_WEIGHT_3M = 0.5
MOMENTUM_WEIGHT_12M = 0.5

ATR_WINDOW = 14  # mismo default que portfolio/indicators.py::atr_pct en el resto del proyecto

# Composite score: promedio de los 3 z-scores, peso IGUAL declarado (no
# optimizado/backtesteado) -- punto de partida razonable sin evidencia que
# justifique sobreponderar un factor sobre otro.
FACTOR_WEIGHTS = {"momentum": 1 / 3, "low_vol": 1 / 3, "value": 1 / 3}

# Top 10% pedido explicitamente, PERO el universo de CEDEARs de este proyecto
# tiene ~10 tickers -- un 10% literal (1 ticker) seria una apuesta de un solo
# nombre, no una "rotacion" real. TOP_N_MIN declara un piso minimo (una
# canasta de al menos 3, eleccion arbitraria mas razonable que 1, no
# calibrada) para que la señal tenga sentido como diversificacion dentro del
# propio factor.
TOP_PCT = 0.10
TOP_N_MIN = 3

FACTOR_STALENESS_DAYS = 7  # "semanalmente" pedido explicitamente
MIN_HISTORY_ROWS = MOMENTUM_12M_DAYS // 2  # declarado -- sin esto ni el momentum de 12m tiene sentido


def _period_return_pct(close, days):
    """Retorno % entre el cierre de hace `days` ruedas y el ultimo cierre --
    None (no 0.0) si no hay suficiente historia, nunca se fabrica un retorno
    con menos datos de los pedidos."""
    if len(close) <= days:
        return None
    start = float(close.iloc[-days - 1])
    end = float(close.iloc[-1])
    if not start:
        return None
    return (end - start) / start * 100


def compute_momentum_factor(close, days_3m=MOMENTUM_3M_DAYS, days_12m=MOMENTUM_12M_DAYS,
                             weight_3m=MOMENTUM_WEIGHT_3M, weight_12m=MOMENTUM_WEIGHT_12M):
    """close: serie de cierres (USD CCL) cronologica. Si falta uno de los 2
    horizontes (historia insuficiente para 12m pero alcanza para 3m) se usa
    el que SI esta disponible solo, en vez de descartar todo el factor --
    declarado, no es lo mismo que promediar con un 0 implicito."""
    r3 = _period_return_pct(close, days_3m)
    r12 = _period_return_pct(close, days_12m)
    if r3 is None and r12 is None:
        return None
    if r12 is None:
        return r3
    if r3 is None:
        return r12
    return weight_3m * r3 + weight_12m * r12


def compute_low_vol_factor(model_df, window=ATR_WINDOW):
    """1/ATR% -- MENOR volatilidad = MAYOR score (ver modulo: "low vol
    anomaly"). None si ATR% no es positivo/finito (serie degenerada), nunca
    se divide por cero ni se fabrica un score."""
    atr_pct_series = indicators.atr_pct(model_df, window)
    if not len(atr_pct_series):
        return None
    latest_atr_pct = float(atr_pct_series.iloc[-1])
    if not np.isfinite(latest_atr_pct) or latest_atr_pct <= 0:
        return None
    return 1.0 / latest_atr_pct


def compute_value_factor(pe_ratio):
    """1/PE -- MENOR PE (mas "barato" relativo a ganancias) = MAYOR score.
    None si el PE no esta disponible o no es positivo (una empresa con
    ganancias negativas tiene PE negativo, que NO es comparable con un PE
    positivo en este factor) -- nunca se fabrica un PE que yfinance no
    devolvio (ver portfolio/fundamentals.py::fetch_fundamentals)."""
    if pe_ratio is None or not np.isfinite(pe_ratio) or pe_ratio <= 0:
        return None
    return 1.0 / pe_ratio


def _cross_sectional_zscore(values):
    """values: {symbol: float|None}. Z-score ENTRE TICKERS del mismo corte
    (no temporal, ver docstring del modulo) -- los None se EXCLUYEN del
    calculo de media/desvio (nunca se imputan con 0, que sesgaria el
    z-score de los demas tickers) y quedan None en el resultado (ese factor
    no participa en el composite de ese ticker puntual, ver
    compute_composite_score). Con menos de 2 valores validos, o desvio
    estandar 0 (todos los validos iguales), devuelve 0.0 para los validos
    -- no hay dispersion real que medir, pero tampoco se descarta el factor
    entero por eso."""
    valid = {k: v for k, v in values.items() if v is not None and np.isfinite(v)}
    if len(valid) < 2:
        return {k: (0.0 if k in valid else None) for k in values}
    arr = np.array(list(valid.values()), dtype=float)
    mean, std = arr.mean(), arr.std(ddof=1)
    if std <= 0:
        return {k: (0.0 if k in valid else None) for k in values}
    return {k: ((v - mean) / std if v is not None else None) for k, v in values.items()}


def compute_composite_score(momentum_z, low_vol_z, value_z, weights=FACTOR_WEIGHTS):
    """Promedio ponderado de los z-scores DISPONIBLES -- si un ticker no
    tiene, por ejemplo, PE (value_z=None), el composite se calcula SOLO con
    momentum+low_vol, renormalizando por el peso efectivamente disponible
    (en vez de tratar el factor faltante como 0, que penalizaria
    injustamente a un ticker solo por falta de dato, no por mal
    fundamento). None si NINGUN factor esta disponible."""
    parts, total_weight = [], 0.0
    for name, z in (("momentum", momentum_z), ("low_vol", low_vol_z), ("value", value_z)):
        if z is None:
            continue
        w = weights[name]
        parts.append(w * z)
        total_weight += w
    if total_weight <= 0:
        return None
    return sum(parts) / total_weight


def top_rotation_symbols(ranked_results, top_pct=TOP_PCT, min_n=TOP_N_MIN):
    """ranked_results: lista YA ordenada de mejor a peor composite_score.
    Top top_pct%, redondeado HACIA ARRIBA, con un piso de min_n (ver
    TOP_N_MIN) -- nunca mas elementos de los que hay disponibles."""
    n = max(min_n, math.ceil(len(ranked_results) * top_pct))
    n = min(n, len(ranked_results))
    return ranked_results[:n]


def compute_factor_scores(conn, universe=None):
    """Recalcula los 3 factores + composite para TODO el universo (sin
    cache, ver ensure_factor_scores para la version con frescura semanal) --
    devuelve una lista de dicts ordenada de mejor a peor composite_score,
    con 'rank' ya asignado (1 = mejor). Tickers sin historia suficiente
    (MIN_HISTORY_ROWS) o sin NINGUN factor calculable quedan afuera -- nunca
    aparecen con un composite inventado."""
    universe = universe if universe is not None else CEDEAR_UNIVERSE
    momentum_raw, low_vol_raw, value_raw = {}, {}, {}
    for symbol, asset_type in universe:
        raw_df = backtest.fetch_ticker_history(symbol, asset_type, conn=conn)
        if len(raw_df) < MIN_HISTORY_ROWS:
            continue
        raw_df = backtest._fix_price_discontinuities(raw_df)
        model_df, _ccl, _currency = build_model_input(conn, raw_df, asset_type)
        close = model_df["close"].reset_index(drop=True)

        momentum_raw[symbol] = compute_momentum_factor(close)
        low_vol_raw[symbol] = compute_low_vol_factor(model_df)

        fundamentals = fetch_fundamentals(symbol, asset_type)
        pe = fundamentals.get("pe_ratio") if fundamentals.get("available") else None
        value_raw[symbol] = compute_value_factor(pe)

    momentum_z = _cross_sectional_zscore(momentum_raw)
    low_vol_z = _cross_sectional_zscore(low_vol_raw)
    value_z = _cross_sectional_zscore(value_raw)

    results = []
    for symbol in momentum_raw:
        composite = compute_composite_score(momentum_z.get(symbol), low_vol_z.get(symbol), value_z.get(symbol))
        if composite is None:
            continue
        results.append({
            "symbol": symbol, "momentum_raw": momentum_raw.get(symbol),
            "low_vol_raw": low_vol_raw.get(symbol), "value_raw": value_raw.get(symbol),
            "composite_score": composite,
        })
    results.sort(key=lambda r: r["composite_score"], reverse=True)
    for i, r in enumerate(results, start=1):
        r["rank"] = i
    return results


def ensure_factor_scores(conn, universe=None):
    """Recalcula SOLO si el ultimo computo tiene mas de FACTOR_STALENESS_DAYS
    -- mismo patron de frescura que portfolio/fetchers.py::ensure_benchmark_history,
    "calcula semanalmente" pedido explicitamente."""
    latest = db.get_factor_scores_latest_computed_at(conn)
    if latest:
        try:
            latest_dt = datetime.fromisoformat(latest)
            if datetime.now() - latest_dt < timedelta(days=FACTOR_STALENESS_DAYS):
                return
        except ValueError:
            pass  # timestamp corrupto/con otro formato -- se recalcula en vez de confiar en un dato invalido

    results = compute_factor_scores(conn, universe=universe)
    now_iso = datetime.now().isoformat()
    for r in results:
        db.upsert_factor_score(
            conn, r["symbol"], r["momentum_raw"], r["low_vol_raw"], r["value_raw"],
            r["composite_score"], r["rank"], computed_at=now_iso,
        )


def get_rotation_signals(conn, universe=None):
    """Punto de entrada para el dispatcher (ver portfolio/live_signals.py) --
    asegura el cache semanal y devuelve el Top N (ver top_rotation_symbols)
    ya ordenado, listo para mostrar como señal de rotacion."""
    ensure_factor_scores(conn, universe=universe)
    scores = db.list_factor_scores(conn)
    return top_rotation_symbols(scores)


# --- Backtest historico (ver conversacion, "simulacion historica multimotor") -
#
# Mismo shape de retorno que portfolio/backtest.py::simulate_satellite_engine
# ({"engine", "daily_returns_pct", "idle_weight", "trades"}).
#
# Rebalanceo semanal (FACTOR_REBALANCE_SPACING_DAYS ruedas, misma cadencia
# que FACTOR_STALENESS_DAYS en vivo): Momentum y Low-Vol se recalculan de
# forma 100% CAUSAL en cada fecha de rebalanceo (la serie se corta hasta esa
# fecha inclusive, igual disciplina que el resto del proyecto).
#
# LIMITACION declarada (no escondida): Value (1/PE) NO tiene fuente historica
# disponible -- fetch_fundamentals (yfinance) solo devuelve el PE ACTUAL, no
# una serie historica. Este backtest usa el PE de HOY para TODOS los puntos
# de rebalanceo historicos -- es un look-ahead real y declarado, limitado a
# este UNICO factor (Momentum/Low-Vol siguen siendo 100% causales). Se eligio
# mantener el factor con este limite declarado en vez de descartarlo del
# backtest, ya que el PE de una empresa suele cambiar mucho mas lento que el
# precio -- de todos modos, el usuario debe leer este numero sabiendo que
# tiene este sesgo, nunca se oculta.
FACTOR_REBALANCE_SPACING_DAYS = FACTOR_STALENESS_DAYS  # "semanalmente", misma cadencia que en vivo


def simulate_factor_engine(conn, universe=None, lookback_days=backtest.CORE_BACKTEST_LOOKBACK_DAYS, commission_pct=0.0):
    """Simulador historico del Motor Factor Investing -- rebalanceo semanal,
    Top N (ver top_rotation_symbols) equal-weighted hasta el proximo
    rebalanceo. Ver LIMITACION declarada arriba sobre el factor Value.

    commission_pct (ver backtest.ROUND_TRIP_COMMISSION_PCT, Fase 4 del plan
    de unificacion): a diferencia de Core/Satelite/Pairs/Canje (1 posicion,
    1 costo de ida+vuelta), acá cada rebalanceo solo rota una FRACCION de la
    cartera (turnover = la mitad de la suma de |cambio de peso| por simbolo,
    ya que todo lo que se vende de un lado se compra del otro) -- el costo
    se escala por esa fraccion, no por el costo completo de un trade nuevo.
    Default 0.0 para no romper ningun llamador que todavia no lo pase
    explicito."""
    universe = universe if universe is not None else CEDEAR_UNIVERSE
    series = {}
    value_raw_today = {}
    min_rows = MIN_HISTORY_ROWS + lookback_days
    for symbol, asset_type in universe:
        df = backtest._prepare_aligned_series(symbol, asset_type, conn, min_rows)
        if df is None:
            continue
        series[symbol] = df
        fundamentals = fetch_fundamentals(symbol, asset_type)
        pe = fundamentals.get("pe_ratio") if fundamentals.get("available") else None
        value_raw_today[symbol] = compute_value_factor(pe)

    if not series:
        empty = pd.Series(dtype=float)
        return {"engine": "factor", "daily_returns_pct": empty, "idle_weight": empty, "trades": []}

    pos_by_date = {sym: {d: i for i, d in enumerate(df["date"])} for sym, df in series.items()}
    master_dates = sorted(set().union(*[set(df["date"]) for df in series.values()]))[-lookback_days:]

    hurdle_tna = float(db.get_signal_config(conn).get("caucion_reference_tna") or 0.0)
    daily_caucion_return_pct = yield_engine.daily_caucion_return_pct(hurdle_tna)

    contributions = {d: 0.0 for d in master_dates}
    committed_weight = {d: 0.0 for d in master_dates}
    trades = []
    current_holdings = {}  # symbol -> peso
    last_rebalance_day = -FACTOR_REBALANCE_SPACING_DAYS - 1

    for day_i, date in enumerate(master_dates):
        if day_i - last_rebalance_day >= FACTOR_REBALANCE_SPACING_DAYS:
            last_rebalance_day = day_i
            momentum_raw, low_vol_raw = {}, {}
            for symbol, df in series.items():
                idx = pos_by_date[symbol].get(date)
                if idx is None or idx < MIN_HISTORY_ROWS:
                    continue
                momentum_raw[symbol] = compute_momentum_factor(df["close"].iloc[: idx + 1])
                low_vol_raw[symbol] = compute_low_vol_factor(df.iloc[: idx + 1])

            new_holdings = {}
            if momentum_raw:
                momentum_z = _cross_sectional_zscore(momentum_raw)
                low_vol_z = _cross_sectional_zscore(low_vol_raw)
                value_z = _cross_sectional_zscore({s: value_raw_today.get(s) for s in momentum_raw})
                ranked = []
                for symbol in momentum_raw:
                    composite = compute_composite_score(momentum_z.get(symbol), low_vol_z.get(symbol), value_z.get(symbol))
                    if composite is None:
                        continue
                    ranked.append({"symbol": symbol, "composite_score": composite})
                ranked.sort(key=lambda r: r["composite_score"], reverse=True)
                top = top_rotation_symbols(ranked)
                if top:
                    new_holdings = {r["symbol"]: 1.0 / len(top) for r in top}

            if set(new_holdings) != set(current_holdings):
                # Turnover = fraccion de la cartera que efectivamente se
                # compra/vende este rebalanceo -- 0.5x la suma de |cambios de
                # peso| porque cada peso que se vende de un lado se compra
                # del otro (no se cuenta doble). commission_pct ya es un
                # costo de ida+vuelta completo, se escala por esa fraccion.
                turnover = 0.5 * sum(
                    abs(new_holdings.get(s, 0.0) - current_holdings.get(s, 0.0))
                    for s in set(new_holdings) | set(current_holdings)
                )
                contributions[date] -= turnover * commission_pct
                trades.append({
                    "engine": "factor", "date": date,
                    "holdings_before": list(current_holdings.keys()), "holdings_after": list(new_holdings.keys()),
                })
            current_holdings = new_holdings

        committed_today = 0.0
        for symbol, weight in current_holdings.items():
            idx = pos_by_date[symbol].get(date)
            if idx is None or idx == 0:
                continue
            close_t = float(series[symbol]["close"].iloc[idx])
            close_prev = float(series[symbol]["close"].iloc[idx - 1])
            if not close_prev:
                continue
            contributions[date] += weight * (close_t - close_prev) / close_prev * 100
            committed_today += weight
        committed_weight[date] = committed_today

    daily_df = pd.DataFrame(
        [(d, contributions[d] + max(0.0, 1.0 - committed_weight[d]) * daily_caucion_return_pct,
          max(0.0, 1.0 - committed_weight[d])) for d in master_dates],
        columns=["date", "return_pct", "idle_weight"],
    ).set_index("date")

    return {
        "engine": "factor", "daily_returns_pct": daily_df["return_pct"], "idle_weight": daily_df["idle_weight"],
        "trades": trades,
    }
