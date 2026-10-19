"""Riesgo real por instrumento y de cartera -- volatilidad anualizada y caida
maxima desde un pico por instrumento; a nivel cartera se suma CVaR 95% mensual
(en USD) y concentracion de las 5 posiciones mas grandes.

Reemplaza el score 0-100 anterior (volatilidad*peso + riesgo_base_del_tipo),
que mezclaba una metrica real con un numero arbitrario por tipo de
instrumento sin decir nada accionable. Metodologia explicita y ajustable (ver
signal_config), nunca una caja negra: cuando no hay suficiente historico para
medir algo, se devuelve None/"insuficiente" con el motivo, nunca un numero de
relleno.
"""
import math

import numpy as np
from scipy.optimize import minimize

from portfolio import db, fetchers, history, loans as loans_module
from portfolio.formatting import fmt_es

MIN_PRICE_POINTS = 10
ANNUALIZATION_FACTOR = math.sqrt(252)


def _label(annualized_vol_pct, config):
    if annualized_vol_pct < config["risk_vol_low_pct"]:
        return "bajo"
    if annualized_vol_pct < config["risk_vol_high_pct"]:
        return "medio"
    return "alto"


def _log_returns(values):
    return [math.log(values[i] / values[i - 1]) for i in range(1, len(values)) if values[i - 1] > 0]


def _annualized_vol_pct(returns):
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / len(returns)
    return math.sqrt(variance) * ANNUALIZATION_FACTOR * 100


def _max_drawdown_pct(values):
    peak = values[0]
    max_dd = 0.0
    for v in values:
        peak = max(peak, v)
        if peak:
            max_dd = max(max_dd, (peak - v) / peak)
    return max_dd * 100


def _max_drawdown_detail(values):
    """Igual que _max_drawdown_pct pero ademas devuelve en que indices (pico,
    valle) ocurrio la caida mas grande -- el tooltip de riesgo de cartera
    quiere mostrar "cayo X% entre el DD/MM y el DD/MM", no solo el numero
    pelado."""
    peak = values[0]
    peak_idx = 0
    max_dd = 0.0
    max_dd_peak_idx = 0
    max_dd_trough_idx = 0
    for i, v in enumerate(values):
        if v > peak:
            peak = v
            peak_idx = i
        if peak:
            dd = (peak - v) / peak
            if dd > max_dd:
                max_dd = dd
                max_dd_peak_idx = peak_idx
                max_dd_trough_idx = i
    return max_dd * 100, max_dd_peak_idx, max_dd_trough_idx


def compute_instrument_metrics(closes):
    """Volatilidad anualizada + caida maxima directo de la serie de cierres.
    None si no hay suficiente historico -- nunca cae a un valor por defecto."""
    if len(closes) < MIN_PRICE_POINTS:
        return None
    returns = _log_returns(closes)
    if len(returns) < MIN_PRICE_POINTS - 1:
        return None
    return {
        "annualized_volatility_pct": round(_annualized_vol_pct(returns), 1),
        "max_drawdown_pct": round(_max_drawdown_pct(closes), 1),
    }


def compute_instrument_risk(closes, config):
    metrics = compute_instrument_metrics(closes)
    if metrics is None:
        return None
    return {**metrics, "label": _label(metrics["annualized_volatility_pct"], config)}


def explain_instrument_risk(symbol, risk):
    return (
        f"{symbol}: volatilidad anualizada {fmt_es(risk['annualized_volatility_pct'])}%, "
        f"caída máxima desde un pico {fmt_es(risk['max_drawdown_pct'])}% -> riesgo {risk['label']}."
    )


def instrument_risk_formula_lines(risk, config):
    return [
        f"riesgo = bajo si vol < {fmt_es(config['risk_vol_low_pct'], decimals=0)}%, "
        f"alto si vol >= {fmt_es(config['risk_vol_high_pct'], decimals=0)}%",
        f"volatilidad anualizada = {fmt_es(risk['annualized_volatility_pct'])}% -> {risk['label']}",
        f"caída máxima desde un pico = {fmt_es(risk['max_drawdown_pct'])}%",
    ]


def _monthly_returns(daily_returns_by_date):
    """Compone los retornos diarios dentro de cada mes calendario en un unico
    retorno mensual -- CVaR95 necesita observaciones independientes, no dias
    sueltos que ya estan correlacionados dentro del mismo mes."""
    by_month = {}
    for date_str, r in daily_returns_by_date:
        month_key = date_str[:7]
        by_month.setdefault(month_key, []).append(r)
    monthly = []
    for month_key in sorted(by_month):
        compounded = 1.0
        for r in by_month[month_key]:
            compounded *= (1 + r)
        monthly.append(compounded - 1)
    return monthly


def compute_portfolio_risk_metrics(conn, holdings, cauciones, config):
    """holdings: lista de {valor_actual, instrument} -- SIEMPRE en moneda
    NATIVA del instrumento (h['instrument']['currency']), nunca ya convertida
    a la moneda de exhibicion elegida en Preferencias -- top5/concentracion
    necesita normalizar todo a una sola unidad por su cuenta (ver mas abajo),
    y si el caller ya muto valor_actual a la moneda de exhibicion, ese dato
    nativo se pierde y el calculo queda invisiblemente mal (bug real
    encontrado y corregido en Fase 10: /portfolio-risk y /dashboard-bundle
    daban top5_concentration_pct DISTINTO para la misma cartera, porque uno
    pasaba holdings nativos y el otro ya convertidos).

    cauciones: salida de db.get_cauciones_resumen. Vol/drawdown/CVaR se
    calculan sobre el TWR en USD (ver history.compute_twr), que excluye
    aportes/retiros Y la devaluacion del peso -- sin esto ultimo, cualquier
    cartera mayormente en ARS mediria "riesgo" que en realidad es solo
    inflacion (mismo problema que ya se detecto en el sesgo de Kronos). Las
    cauciones NO entran a esta serie (no tienen vinculo con transactions/
    price_history para reconstruir un retorno diario), se aclara en la propia
    respuesta.

    Prestamos (Fase 10): entran a top5/concentracion igual que cauciones
    (mismo motivo: sin serie diaria, no entran a vol/drawdown/CVaR) + nueva
    metrica "monto en mora" (capital pendiente de prestamos vigentes
    atrasados), en ARS y USD por separado -- nunca se suman entre si."""
    twr_usd = history.compute_twr(conn, currency="usd")
    if not twr_usd.get("available"):
        return {
            "available": False,
            "reason": twr_usd.get("reason") or "No se pudo calcular el riesgo de cartera.",
        }

    index_series = twr_usd["index_series"]
    if len(index_series) < MIN_PRICE_POINTS:
        return {
            "available": False,
            "reason": "Historial insuficiente todavía para medir riesgo de cartera de forma confiable.",
        }

    values = [p["index"] for p in index_series]
    daily_returns = [(index_series[i]["date"], values[i] / values[i - 1] - 1) for i in range(1, len(values))]

    annualized_vol_pct = round(_annualized_vol_pct([r for _, r in daily_returns]), 1)
    max_dd_pct, max_dd_peak_idx, max_dd_trough_idx = _max_drawdown_detail(values)
    max_drawdown_pct = round(max_dd_pct, 1)
    # index_series[0] no tiene retorno diario propio (es la base) -- los
    # indices de _max_drawdown_detail corren sobre `values` (mismo largo que
    # index_series), no sobre `daily_returns` (uno menos), ver mas abajo.
    max_dd_peak_date = index_series[max_dd_peak_idx]["date"]
    max_dd_trough_date = index_series[max_dd_trough_idx]["date"]

    monthly = _monthly_returns(daily_returns)
    min_months = config["risk_cvar_min_months"]
    n_months = len(monthly)
    cvar_tail_n = None
    if n_months < min_months:
        cvar95_monthly_pct = None
        cvar_reason = f"Se necesitan al menos {min_months} meses de historial ({n_months} disponibles)."
    else:
        cvar_tail_n = max(1, round(n_months * 0.05))
        worst = sorted(monthly)[:cvar_tail_n]
        cvar95_monthly_pct = round(sum(worst) / len(worst) * 100, 1)
        cvar_reason = None

    try:
        usd_rate = fetchers.fetch_dolarapi_casa('bolsa')['venta']
    except Exception:
        usd_rate = None

    def to_usd(valor_nativo, moneda):
        if moneda == "USD":
            return valor_nativo
        return valor_nativo / usd_rate if usd_rate else None

    raw_positions = [
        (h["instrument"]["symbol"], h["valor_actual"] or 0, h["instrument"]["currency"])
        for h in holdings if h.get("valor_actual")
    ] + [
        (
            f"Caución {c['caucion']['numero_boleto']}" if c["caucion"]["numero_boleto"] else f"Caución {c['caucion']['id']}",
            c["valor_actual"], c["caucion"]["moneda"],
        )
        for c in cauciones
    ]

    # Solo 'prestado' (un activo): 'recibido' es una deuda propia, no una
    # posicion de riesgo de cartera -- resta del patrimonio (ver
    # _compute_currency_exposure) pero no entra a concentracion ni a "mora"
    # (que acá mide "me deben y no cobré", no "debo y no pagué").
    vigentes = [loan for loan in db.list_loans(conn) if loan["estado"] == "vigente" and loan["direccion"] == "prestado"]
    loan_statuses = {loan["id"]: loans_module.compute_loan_status(conn, loan) for loan in vigentes}
    raw_positions += [
        (f"Préstamo a {loan['deudor']}", loan_statuses[loan["id"]]["capital_pendiente"], loan["moneda"])
        for loan in vigentes
    ]

    positions = []
    omitted_no_rate = False
    for symbol, valor_nativo, moneda in raw_positions:
        valor_usd = to_usd(valor_nativo, moneda)
        if valor_usd is None:
            omitted_no_rate = True
            continue
        positions.append({"symbol": symbol, "valor_actual_usd": valor_usd})

    total_value = sum(p["valor_actual_usd"] for p in positions)
    top5 = sorted(positions, key=lambda p: p["valor_actual_usd"], reverse=True)[:5]
    top5_concentration_pct = round(sum(p["valor_actual_usd"] for p in top5) / total_value * 100, 1) if total_value else None
    top5_reason = None
    if top5_concentration_pct is None:
        top5_reason = "No se pudo obtener la cotización MEP para normalizar posiciones en varias monedas." if omitted_no_rate else None

    mora_ars = round(sum(
        loan_statuses[loan["id"]]["capital_pendiente"] for loan in vigentes
        if loan["moneda"] == "ARS" and loan_statuses[loan["id"]]["vencido_hace_dias"] is not None
    ), 2)
    mora_usd = round(sum(
        loan_statuses[loan["id"]]["capital_pendiente"] for loan in vigentes
        if loan["moneda"] == "USD" and loan_statuses[loan["id"]]["vencido_hace_dias"] is not None
    ), 2)

    return {
        "available": True,
        "annualized_volatility_pct": annualized_vol_pct,
        "n_days": len(daily_returns),
        "max_drawdown_pct": max_drawdown_pct,
        "max_dd_peak_date": max_dd_peak_date,
        "max_dd_trough_date": max_dd_trough_date,
        "cvar95_monthly_pct": cvar95_monthly_pct,
        "cvar95_reason": cvar_reason,
        "n_months": n_months,
        "cvar_tail_n": cvar_tail_n,
        "top5_concentration_pct": top5_concentration_pct,
        "top5_reason": top5_reason,
        "top5_positions": [{"symbol": p["symbol"], "pct": round(p["valor_actual_usd"] / total_value * 100, 1)} for p in top5] if total_value else [],
        "mora_ars": mora_ars,
        "mora_usd": mora_usd,
        "scope_note": (
            "Volatilidad, caída máxima y CVaR95 se calculan sobre la porción de la cartera que cotiza en "
            "mercado (acciones, CEDEARs, bonos), en USD -- las cauciones y préstamos no tienen historial diario "
            "con el que medir estas 3 métricas. La concentración top-5 (normalizada a USD con la cotización MEP) "
            "sí incluye cauciones y préstamos vigentes."
        ),
    }


def explain_portfolio_risk(risk):
    if not risk.get("available"):
        return risk.get("reason") or "Riesgo de cartera no disponible."
    base = (
        f"Volatilidad anualizada {fmt_es(risk['annualized_volatility_pct'])}%, caída máxima desde un pico "
        f"{fmt_es(risk['max_drawdown_pct'])}% (en USD, excluyendo aportes/retiros). "
    )
    if risk["cvar95_monthly_pct"] is not None:
        base += f"CVaR 95% mensual: en el 5% peor de los meses, pérdida promedio de {fmt_es(abs(risk['cvar95_monthly_pct']))}%. "
    else:
        base += f"CVaR 95% mensual: {risk['cvar95_reason']} "
    if risk["top5_concentration_pct"] is not None:
        base += f"Las 5 posiciones más grandes (incluye cauciones y préstamos) son el {fmt_es(risk['top5_concentration_pct'])}% del total. "
    if risk.get("mora_ars") or risk.get("mora_usd"):
        partes = []
        if risk["mora_ars"]:
            partes.append(f"ARS {fmt_es(risk['mora_ars'])}")
        if risk["mora_usd"]:
            partes.append(f"USD {fmt_es(risk['mora_usd'])}")
        base += f"Préstamos en mora: {' + '.join(partes)}."
    return base


# --- Optimizacion dinamica de asignacion entre ESTRATEGIAS (ver conversacion,
# "Black-Litterman / Risk Parity") -------------------------------------------
#
# ACLARACION HONESTA (no ocultar el recorte de alcance): esto NO es
# Black-Litterman completo. Black-Litterman de verdad combina (a) un prior de
# retornos de EQUILIBRIO DE MERCADO (reverse-optimizado desde pesos de
# capitalizacion de mercado) con (b) "vistas" subjetivas del inversor
# (retornos esperados propios, con su propia confianza), vía actualizacion
# Bayesiana -- ninguna de las dos cosas tiene un analogo real y no inventado
# aca: no existe una "capitalizacion de mercado" de Core/Satelite/Pairs/
# Factor/Caucion (son motores internos, no activos cotizantes), y no se
# proveyeron "vistas" explicitas del usuario para blendear. Lo que SI se
# implementa, y es la parte bien definida y verificable de lo pedido
# ("resuelva la matriz de varianza-covarianza... minimice la varianza global
# y maximice el Sharpe"): Risk Parity (paridad de riesgo, cada estrategia
# contribuye lo mismo al riesgo total), Minima Varianza, y Maximo Sharpe
# (portfolio tangente) -- las 3 vía optimizacion de Markowitz clasica sobre
# la matriz de covarianza REAL medida entre los retornos diarios de cada
# motor. Restriccion w>=0 en las 3 (sin short) -- consistente con que esta
# app no soporta posiciones en corto reales (ver portfolio/backtest.py::summarize).
#
# Esto es un COMPUTO/REPORTE, no un reemplazo automatico y silencioso de
# portfolio/config.py::get_capital_allocation -- cambiar la asignacion REAL de
# capital sin que el usuario lo vea/apruebe seria una accion de alto impacto
# tomada sin confirmacion (ver conversacion, criterio general del proyecto).
# El caller (p.ej. una ruta nueva de la UI, o portfolio/backtest.py al armar
# el reporte unificado) decide si mostrar esto como sugerencia o aplicarlo.

MIN_STRATEGY_OVERLAP_DAYS = 30  # piso declarado -- una covarianza con menos observaciones comunes no es confiable


def estimate_strategy_covariance(returns_by_engine):
    """returns_by_engine: {nombre_motor: pd.Series de retorno diario %,
    indexada por fecha} -- ALINEA por INNER JOIN de fechas (solo dias donde
    TODOS los motores tienen dato, nunca se rellena con 0 un dia sin datos
    de un motor, eso inventaria una correlacion que no se midio). Devuelve
    (nombres_ordenados, matriz_covarianza np.ndarray, n_obs) -- o
    (None, None, 0) si hay menos de MIN_STRATEGY_OVERLAP_DAYS fechas en
    comun o menos de 2 motores con serie."""
    series = {name: s for name, s in returns_by_engine.items() if s is not None and len(s) > 0}
    if len(series) < 2:
        return None, None, 0
    import pandas as pd
    aligned = pd.concat(series, axis=1, join="inner")
    aligned = aligned.dropna()
    if len(aligned) < MIN_STRATEGY_OVERLAP_DAYS:
        return None, None, len(aligned)
    names = list(aligned.columns)
    cov = aligned.cov().to_numpy()
    return names, cov, len(aligned)


def _risk_contributions(weights, cov):
    portfolio_var = float(weights @ cov @ weights)
    if portfolio_var <= 0:
        return np.zeros_like(weights)
    marginal = cov @ weights
    return weights * marginal / math.sqrt(portfolio_var)


def risk_parity_weights(names, cov, max_iter=500):
    """Pesos tales que cada estrategia aporta la MISMA fraccion del riesgo
    total (no el mismo % de capital) -- minimiza la dispersion entre
    contribuciones de riesgo via optimizacion (SLSQP), partiendo de pesos
    iguales. w>=0, suma 1 (sin short, ver nota de arriba del modulo)."""
    n = len(names)
    x0 = np.full(n, 1.0 / n)

    def objective(w):
        rc = _risk_contributions(w, cov)
        target = rc.mean()
        return float(np.sum((rc - target) ** 2))

    result = minimize(
        objective, x0, method="SLSQP", bounds=[(0.0, 1.0)] * n,
        constraints=[{"type": "eq", "fun": lambda w: np.sum(w) - 1.0}],
        options={"maxiter": max_iter, "ftol": 1e-12},
    )
    weights = result.x if result.success else x0
    weights = np.clip(weights, 0.0, None)
    weights = weights / weights.sum()
    return dict(zip(names, weights.tolist()))


def min_variance_weights(names, cov):
    """Portfolio de MINIMA VARIANZA GLOBAL (Markowitz) -- w>=0, suma 1."""
    n = len(names)
    x0 = np.full(n, 1.0 / n)

    def objective(w):
        return float(w @ cov @ w)

    result = minimize(
        objective, x0, method="SLSQP", bounds=[(0.0, 1.0)] * n,
        constraints=[{"type": "eq", "fun": lambda w: np.sum(w) - 1.0}],
        options={"maxiter": 500, "ftol": 1e-14},
    )
    weights = result.x if result.success else x0
    weights = np.clip(weights, 0.0, None)
    weights = weights / weights.sum()
    return dict(zip(names, weights.tolist()))


def max_sharpe_weights(names, mean_returns, cov, risk_free_daily_pct=0.0):
    """Portfolio TANGENTE (maximo Sharpe) -- maximiza (w'mu - rf)/sqrt(w'Σw),
    w>=0, suma 1. mean_returns: np.ndarray de retorno diario % promedio por
    estrategia, MISMO orden que `names` (ver estimate_strategy_covariance)."""
    n = len(names)
    x0 = np.full(n, 1.0 / n)
    mean_returns = np.asarray(mean_returns, dtype=float)

    def neg_sharpe(w):
        port_return = float(w @ mean_returns) - risk_free_daily_pct
        port_vol = math.sqrt(float(w @ cov @ w))
        if port_vol <= 0:
            return 0.0
        return -port_return / port_vol

    result = minimize(
        neg_sharpe, x0, method="SLSQP", bounds=[(0.0, 1.0)] * n,
        constraints=[{"type": "eq", "fun": lambda w: np.sum(w) - 1.0}],
        options={"maxiter": 500, "ftol": 1e-12},
    )
    weights = result.x if result.success else x0
    weights = np.clip(weights, 0.0, None)
    total = weights.sum()
    weights = weights / total if total > 0 else x0
    return dict(zip(names, weights.tolist()))


def compute_dynamic_strategy_allocation(returns_by_engine, method="max_sharpe", risk_free_daily_pct=0.0):
    """Punto de entrada unico -- method en {'risk_parity', 'min_variance',
    'max_sharpe'}. Devuelve {'available': False, 'reason': ...} si no hay
    suficiente historia en comun entre motores (NUNCA pesos inventados con
    poca evidencia), o {'available': True, 'method', 'weights' (dict motor->
    peso 0-1), 'n_obs', 'expected_daily_return_pct', 'expected_daily_vol_pct',
    'expected_sharpe_annualized'} con el Sharpe RESULTANTE de esos pesos
    sobre la MISMA muestra (anualizado *sqrt(252), igual criterio que
    portfolio/backtest.py::_compute_engine_metrics) -- para poder comparar
    de un vistazo contra el Sharpe de la asignacion fija vigente."""
    names, cov, n_obs = estimate_strategy_covariance(returns_by_engine)
    if names is None:
        return {
            "available": False,
            "reason": f"necesita >= {MIN_STRATEGY_OVERLAP_DAYS} ruedas en comun entre >= 2 motores, hay {n_obs}",
        }

    import pandas as pd
    series = {name: s for name, s in returns_by_engine.items() if name in names}
    aligned = pd.concat(series, axis=1, join="inner").dropna()
    mean_returns = aligned.mean().to_numpy()

    if method == "risk_parity":
        weights = risk_parity_weights(names, cov)
    elif method == "min_variance":
        weights = min_variance_weights(names, cov)
    elif method == "max_sharpe":
        weights = max_sharpe_weights(names, mean_returns, cov, risk_free_daily_pct)
    else:
        raise ValueError(f"method debe ser 'risk_parity'/'min_variance'/'max_sharpe', se recibio {method!r}")

    w = np.array([weights[n] for n in names])
    expected_daily_return = float(w @ mean_returns)
    expected_daily_vol = math.sqrt(float(w @ cov @ w))
    expected_sharpe_annualized = (
        (expected_daily_return - risk_free_daily_pct) / expected_daily_vol * ANNUALIZATION_FACTOR
        if expected_daily_vol > 0 else None
    )
    return {
        "available": True, "method": method, "weights": {k: round(v, 4) for k, v in weights.items()},
        "n_obs": n_obs, "expected_daily_return_pct": round(expected_daily_return, 4),
        "expected_daily_vol_pct": round(expected_daily_vol, 4),
        "expected_sharpe_annualized": round(expected_sharpe_annualized, 2) if expected_sharpe_annualized is not None else None,
    }
