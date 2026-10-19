"""Motor de Pairs Trading / Arbitraje Estadistico (ver conversacion) --
detecta pares de activos cointegrados (misma exposicion economica de fondo,
ej. dos bancos argentinos) y opera la REVERSION A LA MEDIA del spread entre
ambos cuando se aleja de forma inusual -- a diferencia de Core/Satelite
(direccionales, apuestan a que un activo sube o baja), este motor es
NEUTRAL AL MERCADO en teoria: no le importa si el PAR en conjunto sube o
baja, solo que la relacion RELATIVA entre los dos vuelva a su nivel tipico.

Series en USD CCL: MISMA conversion que el resto del proyecto
(portfolio.backtest.fetch_ticker_history + portfolio.forecasting.build_model_input),
nunca sobre la serie ARS cruda (arrastraria la tendencia estructural de
inflacion/devaluacion, que no tiene nada que ver con la relacion relativa
entre los dos activos).

LIMITACION REAL DE ESTA APP, declarada explicitamente (no escondida) -- ver
portfolio/backtest.py::summarize para el mismo limite ya declarado ahi: esta
app NO soporta venta en corto real, comprar/vender opera sobre TENENCIAS
EXISTENTES. El pairs trading clasico es neutral al mercado porque esta
LARGO una pata y CORTO la otra al mismo tiempo -- sin soporte de corto real,
la mitad de cada señal (la pata "cara", para vender) solo es una operacion
real si la cartera YA tiene esa tenencia (ahi es una ROTACION real: vender
lo que esta caro, comprar lo que esta barato). Si no se tiene esa tenencia,
esa pata queda marcada 'actionable': False -- es una señal de CALIDAD
(research), no una propuesta de venta en corto que esta app no puede
ejecutar. La pata barata (comprar) SIEMPRE es 'actionable': True -- comprar
algo que no se tiene es una operacion normal, sin necesidad de corto.

Cero look-ahead: test_cointegration/compute_hedge_ratio se calculan sobre
la ventana YA CORTADA que pasa el caller (misma disciplina del resto del
proyecto); spread_zscore es un z-score ROLLING (portfolio.features._rolling_zscore,
reusado tal cual) -- cada fila solo usa datos con fecha <= esa fila."""
import itertools

import pandas as pd
import statsmodels.api as sm
from statsmodels.tsa.stattools import coint

from portfolio import db, features, yield_engine
from portfolio.backtest import CORE_BACKTEST_LOOKBACK_DAYS, UNIVERSE, fetch_ticker_history
from portfolio.forecasting import build_model_input
from portfolio.signals import CORE_SECTOR_GROUPS

# Umbral de significancia ESTANDAR para el test de Engle-Granger (p < 0.05
# rechaza la hipotesis nula de "no cointegrado") -- no calibrado contra un
# backtest especifico, es el umbral convencional de la literatura.
COINTEGRATION_PVALUE_THRESHOLD = 0.05
ZSCORE_ENTRY_THRESHOLD = 2.0  # pedido explicito
# Misma ventana de z-score rolling que ya usa el resto del proyecto para
# señales macro (portfolio/features.py::ROLLING_NORM_WINDOW/_MIN_PERIODS) --
# reusada tal cual, no un numero nuevo elegido sin motivo.
ZSCORE_WINDOW = features.ROLLING_NORM_WINDOW
ZSCORE_MIN_PERIODS = features.ROLLING_NORM_MIN_PERIODS
# Igual que CORE_BACKTEST_LOOKBACK_DAYS en portfolio/backtest.py -- historia
# minima declarada para que el test de cointegracion tenga sentido (con muy
# pocos puntos, cualquier par "parece" cointegrado por azar).
MIN_HISTORY_ROWS = 300

# Los 3 pares pedidos explicitamente -- los 3 ya caen en el mismo grupo
# sectorial declarado en portfolio/signals.py::CORE_SECTOR_GROUPS (bancos_arg,
# indices_us, big_tech_us respectivamente), confirmando que esa clasificacion
# ya sirve como fuente de candidatos razonable (ver candidate_pairs_from_sectors).
DEFAULT_PAIRS = [("GGAL", "BMA"), ("SPY", "QQQ"), ("AAPL", "MSFT")]

_SYMBOL_ASSET_TYPE = dict(UNIVERSE)

REGIME_SPREAD_WIDE_POSITIVE = "spread_ancho_positivo"  # leg A cara respecto de B
REGIME_SPREAD_WIDE_NEGATIVE = "spread_ancho_negativo"  # leg A barata respecto de B


def candidate_pairs_from_sectors(sector_groups=None):
    """Genera pares candidatos dentro del MISMO grupo sectorial (ver
    portfolio/signals.py::CORE_SECTOR_GROUPS, clasificacion ya declarada a
    mano para el filtro de concentracion de Core/Satelite, reusada tal cual
    aca) -- dos activos del mismo sector comparten exposicion economica de
    fondo, candidatos razonables a estar cointegrados; nunca se cruzan
    sectores distintos (ahi la relacion relativa no tendria ningun ancla
    economica). Incluye combinatoriamente TODOS los pares de cada grupo con
    2+ miembros (los 3 pedidos explicitamente ya caen aca)."""
    sector_groups = sector_groups if sector_groups is not None else CORE_SECTOR_GROUPS
    by_sector = {}
    for symbol, sector in sector_groups.items():
        by_sector.setdefault(sector, []).append(symbol)
    pairs = []
    for symbols in by_sector.values():
        if len(symbols) < 2:
            continue
        pairs.extend(itertools.combinations(sorted(symbols), 2))
    return pairs


def _usd_close_series(symbol, conn):
    """Serie de cierre en USD CCL de `symbol`, indexada por fecha -- MISMA
    conversion que el resto del proyecto (ver docstring del modulo)."""
    asset_type = _SYMBOL_ASSET_TYPE.get(symbol)
    if asset_type is None:
        raise ValueError(f"'{symbol}' no esta en portfolio.backtest.UNIVERSE -- no se conoce su asset_type")
    raw_df = fetch_ticker_history(symbol, asset_type, conn=conn)
    model_df, _ccl, _currency = build_model_input(conn, raw_df, asset_type)
    series = pd.Series(model_df["close"].to_numpy(), index=pd.DatetimeIndex(raw_df["timestamps"]))
    return series[~series.index.duplicated(keep="last")].sort_index()


def _align_pair(series_a, series_b):
    """Interseccion de fechas -- ambas patas deben tener dato ESE dia (nunca
    forward-fill entre pares: completar un hueco inventaria un precio que
    no existio para ese activo)."""
    idx = series_a.index.intersection(series_b.index)
    return series_a.loc[idx], series_b.loc[idx]


def test_cointegration(series_a, series_b, pvalue_threshold=COINTEGRATION_PVALUE_THRESHOLD):
    """Test de Engle-Granger (statsmodels.tsa.stattools.coint -- la
    implementacion estandar y ya probada de la libreria, no se reimplementa
    el procedimiento de 2 pasos a mano). Devuelve dict {pvalue,
    test_statistic, is_cointegrated, n_obs} -- None si hay menos de
    MIN_HISTORY_ROWS puntos en comun (no se inventa un resultado con poca
    evidencia, mismo criterio que portfolio/features.py::check_stationarity)."""
    series_a, series_b = _align_pair(series_a, series_b)
    if len(series_a) < MIN_HISTORY_ROWS:
        return None
    test_statistic, pvalue, _crit_values = coint(series_a.to_numpy(), series_b.to_numpy())
    return {
        "pvalue": float(pvalue), "test_statistic": float(test_statistic),
        "is_cointegrated": bool(pvalue < pvalue_threshold), "n_obs": int(len(series_a)),
    }


def compute_hedge_ratio(series_a, series_b):
    """Hedge Ratio via OLS: series_a = alpha + beta*series_b + residual --
    beta es el hedge ratio (Engle-Granger paso 1, statsmodels.OLS estandar).

    Declarado: se estima sobre TODA la ventana que pasa el caller, sin
    re-ajuste rolling automatico aca adentro -- la relacion real puede
    derivar en el tiempo (ej. un split, un cambio de negocio), asi que en
    produccion conviene re-estimar periodicamente (ej. semanal, igual
    cadencia que el motor de Factor Investing) en vez de tratar este numero
    como eterno; esta funcion solo calcula el hedge ratio de LA ventana
    dada, la cadencia de re-estimado es decision del caller, no un ajuste
    optimizado/tuneado aca."""
    series_a, series_b = _align_pair(series_a, series_b)
    X = sm.add_constant(series_b.to_numpy())
    model = sm.OLS(series_a.to_numpy(), X).fit()
    alpha, beta = float(model.params[0]), float(model.params[1])
    return {"hedge_ratio": beta, "alpha": alpha, "r_squared": float(model.rsquared)}


def compute_spread(series_a, series_b, hedge_ratio):
    series_a, series_b = _align_pair(series_a, series_b)
    return series_a - hedge_ratio * series_b


def spread_zscore(spread, window=ZSCORE_WINDOW, min_periods=ZSCORE_MIN_PERIODS):
    """Z-score ROLLING del spread -- MISMA formula/causalidad que
    portfolio/features.py::_rolling_zscore (reusada tal cual, nunca
    duplicada con otro criterio): cada fila solo usa dias <= esa fila,
    nunca estadisticos de la serie completa."""
    return features._rolling_zscore(spread, window=window, min_periods=min_periods)


def generate_pair_signal(symbol_a, symbol_b, conn, entry_threshold=ZSCORE_ENTRY_THRESHOLD,
                          pvalue_threshold=COINTEGRATION_PVALUE_THRESHOLD, held_symbols=None):
    """Señal de Pairs Trading para (symbol_a, symbol_b) de HOY. None si: el
    par no esta cointegrado (p >= pvalue_threshold), no hay suficiente
    historia comun, o |z-score| <= entry_threshold (spread dentro de lo
    normal, sin oportunidad).

    Devuelve SIEMPRE las 2 patas (cheap_leg/expensive_leg) marcadas segun lo
    que esta app puede ejecutar DE VERDAD -- ver LIMITACION REAL en el
    docstring del modulo: la pata cara solo es 'actionable' si
    held_symbols (tenencias actuales, las pasa el caller) la incluye."""
    held_symbols = held_symbols or set()
    series_a = _usd_close_series(symbol_a, conn)
    series_b = _usd_close_series(symbol_b, conn)
    coint_result = test_cointegration(series_a, series_b, pvalue_threshold)
    if coint_result is None or not coint_result["is_cointegrated"]:
        return None

    hedge = compute_hedge_ratio(series_a, series_b)
    spread = compute_spread(series_a, series_b, hedge["hedge_ratio"])
    zscore_series = spread_zscore(spread).dropna()
    if zscore_series.empty:
        return None
    z = float(zscore_series.iloc[-1])
    if abs(z) <= entry_threshold:
        return None

    if z > 0:
        expensive, cheap, regime_label = symbol_a, symbol_b, REGIME_SPREAD_WIDE_POSITIVE
    else:
        expensive, cheap, regime_label = symbol_b, symbol_a, REGIME_SPREAD_WIDE_NEGATIVE

    cheap_leg = {
        "symbol": cheap, "action": "comprar", "actionable": True,
        "note": "pata barata del par -- compra directa, no requiere tenencia previa",
    }
    expensive_actionable = expensive in held_symbols
    expensive_leg = {
        "symbol": expensive,
        "action": "vender" if expensive_actionable else "sin_accion_real_disponible",
        "actionable": expensive_actionable,
        "note": (
            "pata cara del par -- se tiene la tenencia, rotar vendiendola es una operacion real"
            if expensive_actionable else
            "pata cara del par -- esta app no soporta venta en corto real y no hay tenencia "
            "existente para rotar; señal informativa (calidad del par), no una propuesta de operatoria"
        ),
    }

    return {
        "pair": (symbol_a, symbol_b), "zscore": round(z, 3), "regime": regime_label,
        "hedge_ratio": round(hedge["hedge_ratio"], 4), "cointegration_pvalue": round(coint_result["pvalue"], 4),
        "n_obs": coint_result["n_obs"], "cheap_leg": cheap_leg, "expensive_leg": expensive_leg,
    }


def scan_pairs(conn, pairs=None, held_symbols=None):
    """Recorre `pairs` (default: DEFAULT_PAIRS) y devuelve la lista de
    señales ACTIVAS (no None) de generate_pair_signal -- un par sin datos
    suficientes, sin cointegracion, o sin red (fetch fallido) se OMITE, nunca
    rompe el escaneo completo por un solo par problematico."""
    pairs = pairs if pairs is not None else DEFAULT_PAIRS
    out = []
    for symbol_a, symbol_b in pairs:
        try:
            signal = generate_pair_signal(symbol_a, symbol_b, conn, held_symbols=held_symbols)
        except Exception:
            continue
        if signal is not None:
            out.append(signal)
    return out


# --- Backtest historico (ver conversacion, "simulacion historica multimotor") -
#
# Mismo shape de retorno que portfolio/backtest.py::simulate_satellite_engine
# ({"engine", "daily_returns_pct", "idle_weight", "trades"}), para que el
# backtest unificado de 5 motores pueda combinarlo sin adaptacion.
#
# Re-estimacion PERIODICA (no diaria) de cointegracion/hedge ratio: declarado
# -- re-ajustar el OLS/Engle-Granger todos los dias es (a) caro, (b) RUIDOSO
# con una ventana corta (el hedge ratio saltaria de un dia a otro sin que la
# relacion economica real haya cambiado). Se re-estima cada
# PAIRS_REESTIMATION_SPACING_DAYS ruedas sobre una ventana TRAILING de
# PAIRS_COINT_WINDOW_DAYS (= MIN_HISTORY_ROWS, mismo piso ya declarado
# arriba) terminada EN esa fecha -- misma cadencia conceptual que
# ANCHOR_SPACING_DAYS en portfolio/backtest.py, no un numero optimizado.
PAIRS_REESTIMATION_SPACING_DAYS = 10
PAIRS_COINT_WINDOW_DAYS = MIN_HISTORY_ROWS
# Salida: reversion a zscore dentro de PAIRS_EXIT_ZSCORE (spread ya volvio a
# un nivel normal) o expiracion a PAIRS_MAX_HOLD_DAYS ruedas (time-stop de
# seguridad si el spread nunca revierte) -- ambos declarados, no calibrados.
PAIRS_EXIT_ZSCORE = 0.5
PAIRS_MAX_HOLD_DAYS = 30
PAIRS_MAX_CONCURRENT = 3  # = len(DEFAULT_PAIRS), declarado -- nunca mas de 1 posicion por par a la vez


def _pair_price_frame(symbol_a, symbol_b, conn):
    """DataFrame date/close_a/close_b alineado por interseccion de fechas
    (ver _align_pair) -- base para recorrer el backtest dia a dia sin
    re-fetchear cada vez."""
    series_a = _usd_close_series(symbol_a, conn)
    series_b = _usd_close_series(symbol_b, conn)
    series_a, series_b = _align_pair(series_a, series_b)
    return pd.DataFrame({"date": series_a.index, "close_a": series_a.to_numpy(), "close_b": series_b.to_numpy()})


def simulate_pairs_engine(conn, pairs=None, lookback_days=CORE_BACKTEST_LOOKBACK_DAYS):
    """Simulador historico del Motor Pairs Trading. Cero look-ahead: en la
    fecha de re-estimacion t, cointegracion/hedge ratio se calculan SOLO con
    la ventana [t - PAIRS_COINT_WINDOW_DAYS, t] (nunca datos posteriores a
    t); el z-score de salida dia a dia usa el hedge ratio YA FIJADO al abrir
    la posicion, sobre el spread hasta ese dia inclusive.

    LIMITACION REAL declarada (ver docstring del modulo): sin soporte de
    venta en corto real, el retorno simulado de la pata "cara" es TEORICO
    (como si se pudiera vender en corto) -- esto mide la CALIDAD del par
    (si la reversion a la media realmente ocurre y es rentable en conjunto),
    no una propuesta de operatoria ejecutable 1:1 (igual criterio ya usado en
    portfolio/backtest.py::summarize para el meta-modelo). El retorno diario
    del spread se combina como weight*(retorno_diario_barata -
    hedge_ratio*retorno_diario_cara) -- una aproximacion beta-ponderada
    estandar, declarada como tal, no una medicion dollar-neutral exacta.

    Devuelve {"engine": "pairs", "daily_returns_pct": pd.Series, "idle_weight":
    pd.Series, "trades": [...]}, mismo shape que simulate_satellite_engine."""
    pairs = pairs if pairs is not None else DEFAULT_PAIRS
    frames = {}
    for symbol_a, symbol_b in pairs:
        try:
            frame = _pair_price_frame(symbol_a, symbol_b, conn)
        except Exception:
            continue
        if len(frame) < PAIRS_COINT_WINDOW_DAYS + lookback_days:
            continue
        frames[(symbol_a, symbol_b)] = frame

    if not frames:
        empty = pd.Series(dtype=float)
        return {"engine": "pairs", "daily_returns_pct": empty, "idle_weight": empty, "trades": []}

    master_dates = sorted(set.intersection(*(set(f["date"]) for f in frames.values())))[-lookback_days:]
    pos_by_date = {pair: {d: i for i, d in enumerate(f["date"])} for pair, f in frames.items()}

    hurdle_tna = float(db.get_signal_config(conn).get("caucion_reference_tna") or 0.0)
    daily_caucion_return_pct = yield_engine.daily_caucion_return_pct(hurdle_tna)
    weight = 1.0 / PAIRS_MAX_CONCURRENT

    contributions = {d: 0.0 for d in master_dates}
    committed_weight = {d: 0.0 for d in master_dates}
    open_positions = {}  # pair -> {entry_idx, hedge_ratio, direction, hold_days}
    last_reestimate = {}  # pair -> ultimo indice en que se re-estimo cointegracion (para espaciar PAIRS_REESTIMATION_SPACING_DAYS)
    trades = []

    for date in master_dates:
        # 1) Actualiza posiciones abiertas: retorno diario + chequeo de salida.
        for pair in list(open_positions.keys()):
            frame = frames[pair]
            idx = pos_by_date[pair].get(date)
            pos = open_positions[pair]
            if idx is None or idx == 0:
                continue
            close_a_t, close_a_prev = frame["close_a"].iloc[idx], frame["close_a"].iloc[idx - 1]
            close_b_t, close_b_prev = frame["close_b"].iloc[idx], frame["close_b"].iloc[idx - 1]
            ret_a = (close_a_t - close_a_prev) / close_a_prev if close_a_prev else 0.0
            ret_b = (close_b_t - close_b_prev) / close_b_prev if close_b_prev else 0.0
            # direction=+1: largo A/corto B (spread A-hedge*B se achica desde arriba);
            # direction=-1: largo B/corto A.
            spread_ret = ret_a - pos["hedge_ratio"] * ret_b if pos["direction"] == 1 else ret_b - pos["hedge_ratio"] * ret_a
            contributions[date] += weight * spread_ret * 100
            committed_weight[date] += weight
            pos["hold_days"] += 1
            pos["cum_return_pct"] = (1 + pos.get("cum_return_pct", 0.0) / 100) * (1 + spread_ret) * 100 - 100

            spread_now = frame["close_a"].iloc[idx] - pos["hedge_ratio"] * frame["close_b"].iloc[idx]
            z_now = pos["zscore_path"].get(idx)
            should_exit = pos["hold_days"] >= PAIRS_MAX_HOLD_DAYS or (z_now is not None and abs(z_now) <= PAIRS_EXIT_ZSCORE)
            if should_exit:
                trades.append({
                    "engine": "pairs", "pair": pair, "direction": pos["direction"],
                    "entry_date": pos["entry_date"], "exit_date": date, "hold_days": pos["hold_days"],
                    "hedge_ratio": round(pos["hedge_ratio"], 4), "return_pct": round(pos["cum_return_pct"], 3),
                    "exit_reason": "time_stop" if pos["hold_days"] >= PAIRS_MAX_HOLD_DAYS else "reversion",
                })
                del open_positions[pair]

        # 2) Busca nuevas entradas si hay slots libres.
        free_slots = PAIRS_MAX_CONCURRENT - len(open_positions)
        if free_slots <= 0:
            continue
        for pair, frame in frames.items():
            if free_slots <= 0:
                break
            if pair in open_positions:
                continue
            idx = pos_by_date[pair].get(date)
            if idx is None or idx < PAIRS_COINT_WINDOW_DAYS:
                continue
            last_idx = last_reestimate.get(pair, -PAIRS_REESTIMATION_SPACING_DAYS - 1)
            if idx - last_idx < PAIRS_REESTIMATION_SPACING_DAYS:
                continue
            last_reestimate[pair] = idx

            window = frame.iloc[idx - PAIRS_COINT_WINDOW_DAYS + 1: idx + 1]
            series_a = pd.Series(window["close_a"].to_numpy(), index=window["date"])
            series_b = pd.Series(window["close_b"].to_numpy(), index=window["date"])
            coint_result = test_cointegration(series_a, series_b)
            if coint_result is None or not coint_result["is_cointegrated"]:
                continue
            hedge = compute_hedge_ratio(series_a, series_b)
            spread_window = compute_spread(series_a, series_b, hedge["hedge_ratio"])
            z_series = spread_zscore(spread_window).dropna()
            if z_series.empty or abs(float(z_series.iloc[-1])) <= ZSCORE_ENTRY_THRESHOLD:
                continue

            z_entry = float(z_series.iloc[-1])
            direction = -1 if z_entry > 0 else 1  # z>0: A cara/B barata -> largo B/corto A (direction=-1, ver arriba)
            # Pre-calcula el z-score FUTURO con el hedge ratio YA FIJADO (sobre
            # la serie completa del par, pero solo se LEE hacia adelante desde
            # `idx` dia a dia -- no es look-ahead: es la misma formula rolling
            # causal aplicada una sola vez por eficiencia, igual criterio que
            # el resto del proyecto declara en portfolio/features.py).
            full_series_a = pd.Series(frame["close_a"].to_numpy(), index=frame["date"])
            full_series_b = pd.Series(frame["close_b"].to_numpy(), index=frame["date"])
            full_spread = compute_spread(full_series_a, full_series_b, hedge["hedge_ratio"])
            full_zscore = spread_zscore(full_spread)
            open_positions[pair] = {
                "entry_date": date, "hedge_ratio": hedge["hedge_ratio"], "direction": direction,
                "hold_days": 0, "cum_return_pct": 0.0,
                "zscore_path": {i: float(v) if pd.notna(v) else None for i, v in enumerate(full_zscore.to_numpy())},
            }
            free_slots -= 1

    daily_df = pd.DataFrame(
        [(d, contributions[d] + max(0.0, 1.0 - committed_weight[d]) * daily_caucion_return_pct,
          max(0.0, 1.0 - committed_weight[d])) for d in master_dates],
        columns=["date", "return_pct", "idle_weight"],
    ).set_index("date")

    return {
        "engine": "pairs", "daily_returns_pct": daily_df["return_pct"], "idle_weight": daily_df["idle_weight"],
        "trades": trades,
    }
