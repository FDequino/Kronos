"""Motor de Riesgo global (ver OBJETIVO_MARCO.md, plan "grado-fondo" del
2026-10-20) -- la UNICA puerta por la que pasa cualquier señal antes de
convertirse en una propuesta accionable (ver portfolio/proposals.py). Valida,
en este orden:

1. La estrategia que origina la señal esta 'validated'/'live' en el Registro
   de Estrategias (portfolio/strategy_registry.py) Y su automation_mode no es
   'off' -- nunca se propone nada de una estrategia que todavia esta en
   'research' (no paso el gate CPCV+DSR>=0.95 de OBJETIVO_MARCO.md), sin
   importar que tan convincente parezca un check mas liviano (ej. el edge
   tecnico por-instrumento que ya usa proposals.py).
2. El circuit breaker de cartera: si el drawdown REAL actual de la cartera
   (ver compute_portfolio_drawdown_pct) supera el limite del perfil de riesgo
   activo (ver portfolio/config.py::RISK_PROFILES), se bloquean TODAS las
   señales nuevas y se pasan todas las estrategias en full_auto a off (el
   "freno de mano" -- ver strategy_registry.trip_circuit_breaker). Posiciones
   YA abiertas (stop-loss/take-profit, vencimientos) NUNCA se bloquean aca --
   frenar la gestion de riesgo de lo que ya esta abierto durante una caida
   seria exactamente lo contrario de lo que un circuit breaker deberia hacer.

Nunca bloquea por falta de evidencia (sin historial suficiente para medir
drawdown, por ejemplo) -- la ausencia de dato no se trata como "todo bien" ni
como "todo mal", se declara explicita."""
import numpy as np
import pandas as pd

from portfolio import config as capital_config
from portfolio import db, history, macro_data, strategy_registry

# origen de proposals.py::_validated_signal_candidates -> nombre de estrategia
# en el Registro -- 'senal_kronos' es el Motor Core (forecast de Chronos-Bolt
# validado por accuracy.compute_signal_validation), 'senal_tecnica' es el
# equivalente a Satelite (RSI con edge medido). Unico lugar donde se mapea
# esto -- si se agrega un origen nuevo a proposals.py, hay que sumarlo aca.
SIGNAL_ORIGIN_TO_STRATEGY = {
    "senal_kronos": "core",
    "senal_tecnica": "satellite",
}


def compute_portfolio_drawdown_pct(conn):
    """Drawdown REAL actual (no el maximo historico) de la cartera completa
    en USD, sobre el historial reconstruido desde la primera transaccion
    (ver portfolio/history.py::reconstruct_portfolio_value, ya existente).
    Convierte ARS->USD con el CCL historico real (misma fuente que el resto
    del proyecto, portfolio/macro_data.py::get_ccl_series). None si no hay
    al menos 2 puntos de historial utilizables -- nunca se inventa un 0%."""
    reconstruction = history.reconstruct_portfolio_value(conn)
    series = reconstruction["series"]
    if len(series) < 2:
        return None

    ccl = macro_data.get_ccl_series(conn)
    if ccl is None or not len(ccl):
        return None

    values_usd = []
    for point in series:
        if point["valor_real"] is None:
            continue
        date = pd.Timestamp(point["date"])
        # Ultimo valor de CCL YA CONOCIDO a esa fecha (nunca interpolado hacia
        # el futuro) -- mismo criterio causal que el resto del proyecto.
        matched = ccl[ccl.index <= date]
        rate = float(matched.iloc[-1]) if len(matched) else None
        if rate and rate > 0:
            values_usd.append(point["valor_real"] / rate)

    if len(values_usd) < 2:
        return None

    values = np.array(values_usd, dtype=float)
    running_max = np.maximum.accumulate(values)
    drawdown_pct = (values[-1] - running_max[-1]) / running_max[-1] * 100
    return float(drawdown_pct)


def get_active_risk_limits(conn):
    """Limites del perfil de riesgo ACTIVO (ver portfolio/config.py::
    get_active_risk_profile/RISK_PROFILES) -- nunca un default hardcodeado
    aparte, siempre deriva del mismo perfil que ya gobierna Core/Satelite."""
    profile_id = capital_config.get_active_risk_profile(conn)
    profile = capital_config.RISK_PROFILES.get(profile_id)
    if profile is None:  # "personalizado" -- el usuario toco algo a mano, usar el default
        profile = capital_config.RISK_PROFILES[capital_config.DEFAULT_RISK_PROFILE]
    return profile


def check_circuit_breaker(conn):
    """Chequea el drawdown real actual contra el limite del perfil activo.
    Si lo supera, dispara strategy_registry.trip_circuit_breaker (pasa todas
    las estrategias full_auto a off) -- idempotente, se puede llamar en cada
    ciclo sin efecto adicional si ya estaba disparado. Devuelve el detalle
    siempre, tripped=False si no hay evidencia suficiente (nunca bloquea por
    falta de dato)."""
    limits = get_active_risk_limits(conn)
    drawdown_pct = compute_portfolio_drawdown_pct(conn)
    if drawdown_pct is None:
        return {"tripped": False, "drawdown_pct": None, "limit_pct": -limits["max_drawdown_pct"], "reason": "historial insuficiente para medir drawdown"}

    limit = -limits["max_drawdown_pct"]
    tripped = drawdown_pct <= limit
    if tripped:
        strategy_registry.trip_circuit_breaker(conn)
    return {"tripped": tripped, "drawdown_pct": round(drawdown_pct, 2), "limit_pct": limit}


def is_strategy_approved(conn, name):
    """True solo si la estrategia esta 'validated'/'live' en el Registro Y su
    automation_mode no es 'off'. Una estrategia desconocida (nunca paso por
    el registro) NUNCA se aprueba por default -- ausencia de registro no es
    lo mismo que aprobacion."""
    strategy = strategy_registry.get_strategy(conn, name)
    if strategy is None:
        return False
    return strategy["status"] in ("validated", "live") and strategy["automation_mode"] != "off"


def filter_signal_candidates(conn, candidates):
    """Puerta real para portfolio/proposals.py::_validated_signal_candidates
    -- corre el circuit breaker (que si dispara, ya puso todo en 'off') y
    descarta cualquier candidato cuyo origen mapee a una estrategia NO
    aprobada (ver SIGNAL_ORIGIN_TO_STRATEGY/is_strategy_approved). Candidatos
    con un origen no mapeado (ej. futuros origenes de research que todavia no
    generan señales en vivo) pasan sin filtrar -- este motor solo bloquea lo
    que sabe clasificar, nunca descarta a ciegas."""
    check_circuit_breaker(conn)  # efecto secundario intencional: si dispara, deja todo en 'off' antes del filtro de abajo

    approved_cache = {}
    kept = []
    for candidate in candidates:
        strategy_name = SIGNAL_ORIGIN_TO_STRATEGY.get(candidate.get("origen"))
        if strategy_name is None:
            kept.append(candidate)
            continue
        if strategy_name not in approved_cache:
            approved_cache[strategy_name] = is_strategy_approved(conn, strategy_name)
        if approved_cache[strategy_name]:
            kept.append(candidate)
    return kept
