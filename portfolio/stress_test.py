"""Modulo de Pruebas de Estres Sinteticas (ver conversacion) -- aplica
perturbaciones ESTOCASTICAS (Monte Carlo, no un unico replay deterministico)
sobre series de precios REALES para medir la resiliencia del patrimonio en
escenarios que puede que nunca hayan ocurrido historicamente tal cual, pero
son plausibles: salto de brecha CCL, volatilidad extrema (VIX > 35) y caida
de mercado con continuidad.

Simplificacion declarada (alcance, no un detalle menor): esto NO vuelve a
correr los motores Core/Satelite/Pairs/Factor bajo estres -- eso exigiria
reconstruir toda la logica de señales de cada motor sobre una serie
contrafactica, que es un proyecto aparte. En cambio, se mide la resiliencia
de un PORTFOLIO SINTETICO representativo: equal-weight sobre
portfolio/backtest.py::UNIVERSE completo (misma serie USD CCL que ya usa
todo el proyecto, via fetch_ticker_history + build_model_input), igual
criterio de "serie de referencia" que usa portfolio/factor_investing.py y
portfolio/pairs_trading.py para sus propios calculos. Esto mide la
resiliencia del UNIVERSO de activos que Kronos sigue, no de una cartera real
con pesos/holdings especificos de un usuario -- declarado para no fingir
mas precision de la que esto realmente tiene.

Calibracion EMPIRICA, no numeros inventados: donde es razonable, los
parametros de cada shock (magnitud del salto de CCL, multiplicador de
volatilidad VIX>35, caida inicial de un crash) se calibran contra la
distribucion REAL historica de esas series (ver _calibrate_ccl_shock_pct,
_calibrate_vix_shock, _calibrate_crash_shock) en vez de adivinarse a mano.
Donde no hay forma razonable de calibrar algo contra datos reales (p.ej. la
probabilidad dia a dia de que un crash continue al dia siguiente), se usa
una constante declarada como juicio explicito, nunca disfrazada de medida.

Cero look-ahead NO aplica de la misma forma que en el resto del proyecto
(esto es un generador de escenarios SINTETICOS a futuro de una ventana ya
conocida, no un backtest que podria filtrar informacion futura a una
decision pasada) -- lo que si se mantiene es la reproducibilidad: toda
aleatoriedad pasa por numpy.random.default_rng(seed) explicito, nunca
np.random global sin semilla (para que un trial sea reproducible en un
test)."""
import numpy as np
import pandas as pd

from portfolio import backtest, macro_data
from portfolio.forecasting import build_model_input

MIN_HISTORY_ROWS = 300  # por debajo de esto un ticker no aporta señal confiable a la serie combinada
DEFAULT_LOOKBACK_DAYS = 750  # ~3 años de ruedas -- ventana base sobre la que se corre cada trial
DEFAULT_N_TRIALS = 200
PERIODS_PER_YEAR = 252
RECOVERY_WINDOW_DAYS = 120  # ruedas maximas para considerar "recuperado" -- mas alla de esto, se reporta None (no inventa una recuperacion)

# --- Metricas de resiliencia -- formulas MIRROR de portfolio/risk.py,
# NO importadas: risk.py opera sobre listas planas de Python (por diseño,
# ver su propio docstring), este modulo necesita vectores numpy/pandas para
# el Monte Carlo -- acoplar los dos modulos por una formula de 3 lineas no
# vale la duplicacion de dependencias que generaria. Formula identica,
# verificada contra risk.py._max_drawdown_pct/_annualized_vol_pct.


def _max_drawdown_pct(cum_values):
    cum_values = np.asarray(cum_values, dtype=float)
    running_peak = np.maximum.accumulate(cum_values)
    drawdown = (running_peak - cum_values) / running_peak
    return float(np.max(drawdown) * 100)


def _annualized_vol_pct(returns, periods_per_year=PERIODS_PER_YEAR):
    returns = np.asarray(returns, dtype=float)
    if len(returns) < 2:
        return 0.0
    return float(np.std(returns, ddof=1) * np.sqrt(periods_per_year) * 100)


def _cvar_pct(returns, alpha=0.05):
    """CVaR (Expected Shortfall) al nivel alpha -- promedio de los peores
    alpha% retornos diarios, en %. Mas informativo que un VaR puntual (que
    solo marca el umbral, no cuanto se pierde EN PROMEDIO una vez cruzado)."""
    returns = np.asarray(returns, dtype=float)
    if len(returns) < 20:
        return None  # muestra insuficiente para un percentil de cola confiable, nunca se inventa
    threshold = np.quantile(returns, alpha)
    tail = returns[returns <= threshold]
    if len(tail) == 0:
        return float(threshold * 100)
    return float(tail.mean() * 100)


def _time_to_recovery(cum_values, shock_idx, window=RECOVERY_WINDOW_DAYS):
    """Ruedas desde shock_idx hasta que cum_values vuelve al nivel JUSTO
    ANTES del shock (cum_values[shock_idx - 1], no cum_values[shock_idx] --
    ese ya esta post-shock, usarlo como referencia haria que "recuperarse"
    fuera casi instantaneo por definicion, bug real encontrado via test
    directo antes de confiar en esta funcion). None si no se recupera
    dentro de `window` ruedas -- nunca se extrapola mas alla de los datos
    simulados."""
    cum_values = np.asarray(cum_values, dtype=float)
    if shock_idx >= len(cum_values):
        return None
    pre_shock_level = cum_values[shock_idx - 1] if shock_idx > 0 else cum_values[0]
    end = min(len(cum_values), shock_idx + 1 + window)
    for i in range(shock_idx + 1, end):
        if cum_values[i] >= pre_shock_level:
            return i - shock_idx
    return None


# --- Portfolio sintetico representativo -------------------------------------

def _asset_returns_matrix(conn, universe=None, lookback_days=DEFAULT_LOOKBACK_DAYS):
    """DataFrame de retornos diarios (%, no log) por simbolo, en USD CCL
    (misma conversion que el resto del proyecto, build_model_input) --
    alineado por fecha (outer join + ffill, mismo criterio causal de
    alineacion que portfolio/features.py::_align_ffill, aunque aca no hay
    look-ahead que cuidar ya que es post-hoc sobre historia ya cerrada).
    Devuelve (returns_df, asset_type_by_symbol) -- el segundo hace falta
    para el shock de brecha CCL, que solo afecta a 'accion' (ver
    apply_ccl_gap_shock)."""
    universe = universe if universe is not None else backtest.UNIVERSE
    closes = {}
    asset_type_by_symbol = {}
    for symbol, asset_type in universe:
        try:
            raw_df = backtest.fetch_ticker_history(symbol, asset_type, conn=conn)
        except Exception:
            continue
        if len(raw_df) < MIN_HISTORY_ROWS:
            continue
        model_df, _ccl, _currency = build_model_input(conn, raw_df, asset_type)
        series = pd.Series(model_df["close"].to_numpy(), index=pd.DatetimeIndex(raw_df["timestamps"]))
        closes[symbol] = series.tail(lookback_days + 1)  # +1: el primer pct_change() sale NaN
        asset_type_by_symbol[symbol] = asset_type

    if not closes:
        return pd.DataFrame(), {}

    price_df = pd.DataFrame(closes).sort_index().ffill()
    returns_df = price_df.pct_change().dropna(how="all").tail(lookback_days)
    return returns_df, asset_type_by_symbol


def _portfolio_index_from_returns(returns_df, weights=None):
    """Serie de retorno diario del portfolio sintetico equal-weight (o
    `weights`: dict symbol->peso) -- promedio ponderado de los simbolos CON
    dato ese dia (un NaN puntual de un simbolo no tira todo el dia a NaN,
    se promedia sobre los disponibles, mismo criterio de "dato faltante no
    es cero" del resto del proyecto)."""
    if returns_df.empty:
        return pd.Series(dtype=float)
    if weights is None:
        return returns_df.mean(axis=1, skipna=True)
    w = pd.Series(weights).reindex(returns_df.columns).fillna(0.0)
    weighted = returns_df.mul(w, axis=1)
    valid_weight = (~returns_df.isna()).mul(w, axis=1).sum(axis=1)
    return weighted.sum(axis=1, skipna=True) / valid_weight.replace(0, np.nan)


def _cumulative_index(daily_returns_pct, base=1.0):
    return base * (1.0 + daily_returns_pct.to_numpy()).cumprod()


# --- Shocks (deterministicos dados sus parametros -- la aleatoriedad vive
# en run_monte_carlo, que elige shock_idx/magnitud por trial via rng) -------

def apply_ccl_gap_shock(returns_df, asset_type_by_symbol, shock_idx, magnitude_pct):
    """Salto de brecha CCL (devaluacion acelerandose): aplica UNA caida
    puntual de magnitude_pct en shock_idx SOLO a los simbolos 'accion'
    (precio nominal en ARS) -- representa el fenomeno real y documentado de
    que el precio LOCAL de una accion no reacciona instantaneamente a un
    salto del CCL implicito, generando una perdida temporaria en USD hasta
    que el precio nominal "alcanza" al nuevo tipo de cambio. CEDEAR (arbitra
    contra su subyacente real de Wall Street, ver portfolio/features.py) y
    bono (ya nativo en USD) NO se shockean por este escenario -- declarado,
    no un olvido."""
    shocked = returns_df.copy()
    accion_cols = [s for s, t in asset_type_by_symbol.items() if t == "accion" and s in shocked.columns]
    if 0 <= shock_idx < len(shocked):
        shocked.iloc[shock_idx, shocked.columns.get_indexer(accion_cols)] += -abs(magnitude_pct) / 100.0
    return shocked


def apply_vix_regime_shock(returns_df, shock_idx, duration_days, vol_multiplier):
    """Regimen de volatilidad extrema (VIX > 35): durante
    [shock_idx, shock_idx+duration_days) infla la DESVIACION de cada retorno
    respecto a la media HISTORICA de esa misma columna por vol_multiplier
    (shocked = mean + (original - mean) * multiplier) -- preserva el drift
    promedio de cada activo, solo amplifica que tan lejos de su propio
    comportamiento tipico se mueve cada dia. Afecta TODOS los simbolos por
    igual (un regimen de panico es global, no de un solo activo)."""
    shocked = returns_df.copy()
    end = min(len(shocked), shock_idx + duration_days)
    if shock_idx >= end:
        return shocked
    means = shocked.mean(axis=0, skipna=True)
    window = shocked.iloc[shock_idx:end]
    shocked.iloc[shock_idx:end] = means + (window - means) * vol_multiplier
    return shocked


def apply_crash_shock(returns_df, shock_idx, initial_drop_pct, decay_days, decay_prob, decay_magnitude_pct, rng):
    """Caida de mercado con continuidad (no un unico outlier aislado): dia
    shock_idx recibe initial_drop_pct (negativo) en TODOS los simbolos
    (correlacion de panico, un crash real no es idiosincratico de un solo
    activo) -- cada uno de los decay_days siguientes tiene probabilidad
    decay_prob (sorteada con rng, UNA tirada por dia, igual para todos los
    simbolos ese dia -- la continuacion de un crash tampoco es
    idiosincratica) de sumar otra caida de decay_magnitude_pct. rng:
    numpy.random.Generator ya creado por el caller (nunca np.random global),
    para que un trial sea reproducible dada la misma semilla."""
    shocked = returns_df.copy()
    if 0 <= shock_idx < len(shocked):
        shocked.iloc[shock_idx, :] += -abs(initial_drop_pct) / 100.0
    end = min(len(shocked), shock_idx + 1 + decay_days)
    for i in range(shock_idx + 1, end):
        if rng.random() < decay_prob:
            shocked.iloc[i, :] += -abs(decay_magnitude_pct) / 100.0
    return shocked


# --- Calibracion empirica de parametros de shock ----------------------------

def _calibrate_ccl_shock_pct(conn, percentile=99):
    """Magnitud de referencia del shock de CCL: percentil `percentile` de
    los saltos diarios POSITIVOS reales del CCL historico (ver
    macro_data.get_ccl_series) -- un salto "del tamaño de los peores saltos
    reales ya vistos", no un numero inventado. None si no hay suficiente
    historia (nunca se inventa un default sin decirlo)."""
    ccl = macro_data.get_ccl_series(conn)
    if len(ccl) < 60:
        return None
    pct_changes = ccl.pct_change().dropna() * 100
    jumps = pct_changes[pct_changes > 0]
    if jumps.empty:
        return None
    return float(np.percentile(jumps, percentile))


def _calibrate_vix_shock(conn, vix_threshold=35.0):
    """Multiplicador de volatilidad de referencia: compara la volatilidad
    REALIZADA de SPY en ruedas reales donde VIX > vix_threshold contra la
    volatilidad de base de TODA la historia disponible -- ese ratio es el
    vol_multiplier (nunca < 1.0: si los dias de VIX alto historicamente no
    fueron mas volatiles que el promedio, no tiene sentido amplificar).
    duration_days: duracion PROMEDIO real de episodios contiguos de
    VIX > vix_threshold en la historia (minimo 1). Devuelve (None, None) si
    no hay suficiente historia de VIX o nunca se supero el umbral."""
    vix = macro_data.get_vix_series(conn)
    spy = macro_data.get_global_trend_series(conn, "SPY")
    if len(vix) < 60 or len(spy) < 60:
        return None, None
    spy_returns = spy.pct_change().dropna()
    idx = vix.index.intersection(spy_returns.index)
    if len(idx) < 60:
        return None, None
    vix_aligned = vix.reindex(idx)
    returns_aligned = spy_returns.reindex(idx)

    baseline_vol = returns_aligned.std()
    high_vix_mask = vix_aligned > vix_threshold
    if high_vix_mask.sum() < 5 or baseline_vol == 0:
        return None, None
    high_vol = returns_aligned[high_vix_mask].std()
    multiplier = max(1.0, float(high_vol / baseline_vol))

    # Duracion promedio de rachas contiguas de VIX > umbral.
    runs = []
    current = 0
    for flag in high_vix_mask.to_numpy():
        if flag:
            current += 1
        elif current > 0:
            runs.append(current)
            current = 0
    if current > 0:
        runs.append(current)
    duration_days = max(1, int(round(np.mean(runs)))) if runs else 5
    return multiplier, duration_days


def _calibrate_crash_shock(conn, percentile=1):
    """Caida inicial de referencia: percentil `percentile` (cola izquierda)
    de los retornos diarios reales de SPY -- "una caida del tamaño de los
    peores dias ya vistos". decay_days/decay_prob/decay_magnitude_pct NO se
    calibran (no hay una forma razonable de medir "probabilidad de
    continuacion" contra una sola serie historica sin asumir un modelo de
    por si) -- quedan como constantes declaradas (CRASH_DECAY_DAYS_DEFAULT,
    etc.), juicio explicito, no un numero disfrazado de medicion."""
    spy = macro_data.get_global_trend_series(conn, "SPY")
    if len(spy) < 60:
        return None
    returns = spy.pct_change().dropna() * 100
    return float(np.percentile(returns, percentile))


CRASH_DECAY_DAYS_DEFAULT = 3
CRASH_DECAY_PROB_DEFAULT = 0.4
CRASH_DECAY_MAGNITUDE_FRACTION = 0.3  # cada dia de continuacion pierde esta fraccion de la caida inicial, no la misma magnitud completa


# --- Monte Carlo -------------------------------------------------------------

SCENARIO_CCL_GAP = "ccl_gap"
SCENARIO_VIX_REGIME = "vix_regime"
SCENARIO_CRASH = "crash"


def _trial_metrics(returns_series, shock_idx):
    cum = _cumulative_index(returns_series)
    return {
        "max_drawdown_pct": _max_drawdown_pct(cum),
        "annualized_vol_pct": _annualized_vol_pct(returns_series.to_numpy()),
        "cvar_5pct": _cvar_pct(returns_series.to_numpy()),
        "recovery_days": _time_to_recovery(cum, shock_idx),
        "final_return_pct": float((cum[-1] / cum[0] - 1) * 100),
    }


def run_monte_carlo(conn, scenario, n_trials=DEFAULT_N_TRIALS, random_state=0,
                     lookback_days=DEFAULT_LOOKBACK_DAYS, universe=None):
    """Corre n_trials simulaciones de `scenario` sobre el portfolio sintetico
    equal-weight (ver _asset_returns_matrix) y devuelve
    {"baseline": {...}, "trials": [{...} por trial], "params": {...}} --
    params documenta que parametros se usaron (calibrados o default) para
    que el reporte final sea trazable, nunca una caja negra."""
    returns_df, asset_type_by_symbol = _asset_returns_matrix(conn, universe=universe, lookback_days=lookback_days)
    if returns_df.empty or len(returns_df) < MIN_HISTORY_ROWS:
        return None

    baseline_returns = _portfolio_index_from_returns(returns_df)
    baseline_metrics = _trial_metrics(baseline_returns, shock_idx=0)

    n = len(returns_df)
    # Rango valido de shock_idx: deja margen al principio (para que haya
    # "antes" del shock) y al final (para que haya ventana de recuperacion
    # que medir) -- sin esto, un shock en el ultimo dia nunca podria
    # "recuperarse" dentro de la ventana simulada por pura falta de dias.
    margin = min(RECOVERY_WINDOW_DAYS, n // 4)
    low, high = margin, max(margin + 1, n - margin)

    params = {}
    trials = []
    for trial_i in range(n_trials):
        rng = np.random.default_rng((random_state, trial_i))
        shock_idx = int(rng.integers(low, high)) if high > low else low

        if scenario == SCENARIO_CCL_GAP:
            magnitude = _calibrate_ccl_shock_pct(conn) or 8.0
            params.setdefault("magnitude_pct_p99", magnitude)
            shocked_df = apply_ccl_gap_shock(returns_df, asset_type_by_symbol, shock_idx, magnitude)
        elif scenario == SCENARIO_VIX_REGIME:
            multiplier, duration = _calibrate_vix_shock(conn)
            multiplier = multiplier or 2.5
            duration = duration or 10
            params.setdefault("vol_multiplier", multiplier)
            params.setdefault("duration_days", duration)
            shocked_df = apply_vix_regime_shock(returns_df, shock_idx, duration, multiplier)
        elif scenario == SCENARIO_CRASH:
            initial_drop = _calibrate_crash_shock(conn) or -7.0
            params.setdefault("initial_drop_pct", initial_drop)
            params.setdefault("decay_days", CRASH_DECAY_DAYS_DEFAULT)
            params.setdefault("decay_prob", CRASH_DECAY_PROB_DEFAULT)
            decay_magnitude = abs(initial_drop) * CRASH_DECAY_MAGNITUDE_FRACTION
            params.setdefault("decay_magnitude_pct", decay_magnitude)
            shocked_df = apply_crash_shock(
                returns_df, shock_idx, initial_drop, CRASH_DECAY_DAYS_DEFAULT,
                CRASH_DECAY_PROB_DEFAULT, decay_magnitude, rng,
            )
        else:
            raise ValueError(f"escenario desconocido: {scenario!r}")

        shocked_returns = _portfolio_index_from_returns(shocked_df)
        trials.append(_trial_metrics(shocked_returns, shock_idx))

    return {"baseline": baseline_metrics, "trials": trials, "params": params, "n_assets": returns_df.shape[1]}


def summarize_trials(trials):
    """mean/worst Max DD, mean/worst CVaR, recovery stats (cuantos trials no
    se recuperaron dentro de la ventana) -- resumen de la distribucion, no
    un unico numero que esconda la dispersion entre trials."""
    if not trials:
        return None
    max_dds = [t["max_drawdown_pct"] for t in trials]
    cvars = [t["cvar_5pct"] for t in trials if t["cvar_5pct"] is not None]
    recoveries = [t["recovery_days"] for t in trials if t["recovery_days"] is not None]
    n_no_recovery = sum(1 for t in trials if t["recovery_days"] is None)
    return {
        "max_drawdown_mean_pct": float(np.mean(max_dds)),
        "max_drawdown_worst_pct": float(np.max(max_dds)),
        "cvar_5pct_mean": float(np.mean(cvars)) if cvars else None,
        "cvar_5pct_worst": float(np.min(cvars)) if cvars else None,
        "recovery_days_mean": float(np.mean(recoveries)) if recoveries else None,
        "n_no_recovery": n_no_recovery,
        "n_trials": len(trials),
    }


def _fmt(v, decimals=2, suffix=""):
    return f"{v:.{decimals}f}{suffix}" if v is not None else "—"


def print_stress_test_report(conn, n_trials=DEFAULT_N_TRIALS, random_state=0,
                              lookback_days=DEFAULT_LOOKBACK_DAYS, universe=None):
    """Reporte en consola: baseline (sin shock) vs. cada escenario (N
    trials Monte Carlo) -- numeros reales de una corrida real, nunca
    ilustrativos."""
    scenarios = [
        (SCENARIO_CCL_GAP, "Salto de Brecha CCL"),
        (SCENARIO_VIX_REGIME, "Volatilidad Extrema (VIX > 35)"),
        (SCENARIO_CRASH, "Caida de Mercado"),
    ]
    header = "=" * 100
    print(header)
    print(f"PRUEBA DE ESTRES SINTETICA -- portfolio equal-weight sobre {len(universe or backtest.UNIVERSE)} "
          f"activos de referencia, {n_trials} trials Monte Carlo por escenario, ventana {lookback_days} ruedas")
    print(header)

    baseline_printed = False
    for scenario_key, label in scenarios:
        result = run_monte_carlo(
            conn, scenario_key, n_trials=n_trials, random_state=random_state,
            lookback_days=lookback_days, universe=universe,
        )
        if result is None:
            print(f"\n{label}: sin suficiente historia para simular (universo/cobertura CCL insuficiente)")
            continue
        if not baseline_printed:
            b = result["baseline"]
            print(f"\nBASELINE (sin shock, {result['n_assets']} activos con historia suficiente):")
            print(f"  Max Drawdown {_fmt(b['max_drawdown_pct'], suffix='%')} · "
                  f"Vol anualizada {_fmt(b['annualized_vol_pct'], suffix='%')} · "
                  f"CVaR 5% diario {_fmt(b['cvar_5pct'], suffix='%')} · "
                  f"Retorno del periodo {_fmt(b['final_return_pct'], suffix='%')}")
            baseline_printed = True

        summary = summarize_trials(result["trials"])
        print(f"\n{label} -- parametros: {result['params']}")
        print(f"  Max Drawdown -- media {_fmt(summary['max_drawdown_mean_pct'], suffix='%')} · "
              f"peor caso {_fmt(summary['max_drawdown_worst_pct'], suffix='%')}")
        print(f"  CVaR 5% diario -- media {_fmt(summary['cvar_5pct_mean'], suffix='%')} · "
              f"peor caso {_fmt(summary['cvar_5pct_worst'], suffix='%')}")
        print(f"  Recuperacion -- {_fmt(summary['recovery_days_mean'], decimals=1)} ruedas promedio · "
              f"{summary['n_no_recovery']}/{summary['n_trials']} trials NO se recuperaron en "
              f"{RECOVERY_WINDOW_DAYS} ruedas")
    print(header)


if __name__ == "__main__":
    from portfolio import db
    _conn = db.get_connection()
    try:
        print_stress_test_report(_conn)
    finally:
        _conn.close()
