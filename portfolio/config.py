"""Configuracion de asignacion de capital entre el Motor CORE (institucional,
preservacion/eficiencia, ver portfolio/signals.py) y el Motor SATELITE
(trading corto plazo, ver portfolio/short_term_engine.py).

Dinamico de verdad, no constantes de archivo: un modulo .py con dos
numeros fijos no se puede "leer y actualizar dinamicamente desde el
backend" como pide el enunciado -- un reinicio del proceso lo resetearia.
Vive en signal_config (misma tabla/patron que cualquier otro umbral de este
proyecto, ver portfolio/db.py), asi que PUT /api/portfolio/signal-config y
los comandos /capital, /set_core de Telegram leen y escriben el mismo
numero real, sin una copia aparte que se pueda desincronizar.

satellite_pct nunca se persiste por separado -- siempre se deriva de
100 - core_pct, para que las dos mitades no puedan sumar algo distinto de
100 por un bug de sincronizacion."""
from portfolio import db

# 20/80 (ver conversacion, antes 70/30): el backtest Out-Of-Sample mostro al
# Motor Satelite con Sharpe 1.77 OOS consistente, mientras el Core sigue sin
# edge positivo demostrado (ver portfolio/signals.py::CORE_MIN_EFFICIENCY_SCORE)
# -- el default se corrige para que el patrimonio capture el edge real en
# vez de concentrar la mayoria del capital en el motor mas debil. Sigue
# siendo 100% ajustable por el usuario (UI/Telegram/API), esto es solo el
# punto de partida para una instalacion nueva.
DEFAULT_CORE_ALLOCATION_PCT = 20.0
DEFAULT_SATELLITE_ALLOCATION_PCT = 100.0 - DEFAULT_CORE_ALLOCATION_PCT


def get_capital_allocation(conn):
    """{'core_pct': ..., 'satellite_pct': ...} -- siempre suman 100."""
    config = db.get_signal_config(conn)
    core_pct = config.get("core_allocation_pct")
    if core_pct is None:
        core_pct = DEFAULT_CORE_ALLOCATION_PCT
    core_pct = float(core_pct)
    return {"core_pct": core_pct, "satellite_pct": round(100.0 - core_pct, 4)}


def set_core_allocation_pct(conn, core_pct):
    """Valida 0-100 y persiste -- satellite_pct nunca se guarda, se deriva
    siempre de 100 - core_pct (ver get_capital_allocation). Lanza ValueError
    con un mensaje claro si el valor no es valido, para que tanto la API
    (PUT /api/portfolio/signal-config) como /set_core en Telegram puedan
    mostrar el motivo real en vez de un error generico."""
    try:
        core_pct = float(core_pct)
    except (TypeError, ValueError):
        raise ValueError(f"core_pct debe ser un numero, se recibio {core_pct!r}")
    if not (0.0 <= core_pct <= 100.0):
        raise ValueError(f"core_pct debe estar entre 0 y 100, se recibio {core_pct}")
    db.update_signal_config(conn, core_allocation_pct=core_pct)
    return get_capital_allocation(conn)


# --- Asignacion NOMINAL de Pairs Trading / Factor Investing (ver conversacion) -
#
# A diferencia de core_pct/satellite_pct de arriba, estos 2 motores nuevos
# NO tienen todavia un backtest propio validado (Sharpe/Max DD medidos,
# igual que Core/Satelite si lo tienen) -- mezclarlos en el mismo 100% de
# get_capital_allocation seria fingir una comparabilidad que no existe.
# Son porcentajes INFORMATIVOS/sugeridos, independientes entre si y del
# 100% Core+Satelite, solo para mostrar "cuanto pondrias si operaras esta
# señal" en las tarjetas de portfolio.html -- no participan del blend de
# portfolio/backtest.py::run_dual_engine_backtest.
DEFAULT_PAIRS_ALLOCATION_PCT = 5.0
DEFAULT_FACTOR_ALLOCATION_PCT = 10.0


def get_pairs_allocation_pct(conn):
    config = db.get_signal_config(conn)
    pct = config.get("pairs_allocation_pct")
    return float(pct) if pct is not None else DEFAULT_PAIRS_ALLOCATION_PCT


def set_pairs_allocation_pct(conn, pct):
    try:
        pct = float(pct)
    except (TypeError, ValueError):
        raise ValueError(f"pairs_allocation_pct debe ser un numero, se recibio {pct!r}")
    if not (0.0 <= pct <= 100.0):
        raise ValueError(f"pairs_allocation_pct debe estar entre 0 y 100, se recibio {pct}")
    db.update_signal_config(conn, pairs_allocation_pct=pct)
    return get_pairs_allocation_pct(conn)


def get_factor_allocation_pct(conn):
    config = db.get_signal_config(conn)
    pct = config.get("factor_allocation_pct")
    return float(pct) if pct is not None else DEFAULT_FACTOR_ALLOCATION_PCT


def set_factor_allocation_pct(conn, pct):
    try:
        pct = float(pct)
    except (TypeError, ValueError):
        raise ValueError(f"factor_allocation_pct debe ser un numero, se recibio {pct!r}")
    if not (0.0 <= pct <= 100.0):
        raise ValueError(f"factor_allocation_pct debe estar entre 0 y 100, se recibio {pct}")
    db.update_signal_config(conn, factor_allocation_pct=pct)
    return get_factor_allocation_pct(conn)


def suggested_amount(total_value, pct, position_weight=1.0):
    """Monto sugerido a operar: total_value * (pct/100) * position_weight --
    position_weight (0-1) es el dimensionamiento dinamico de cada señal (ver
    portfolio/signals.py::compute_core_position_weight), nunca el 100% de la
    porcion asignada a menos que la señal misma pida full size."""
    return total_value * (pct / 100.0) * position_weight


# --- Salida configurable del Motor Satelite (ver conversacion) -------------
#
# Igual criterio que get/set_core_allocation_pct de arriba: vive en
# signal_config (satellite_take_profit_pct/satellite_stop_loss_pct, ver
# portfolio/db.py), nunca una constante de archivo -- portfolio/
# short_term_engine.py sigue declarando SATELLITE_TAKE_PROFIT_PCT/
# SATELLITE_STOP_LOSS_PCT como default de referencia (para quien importe ese
# modulo sin pasar por aca), pero el valor REAL que opera el backtest/señales
# en vivo/Telegram siempre pasa por estas dos funciones.

DEFAULT_SATELLITE_TAKE_PROFIT_PCT = 4.0
DEFAULT_SATELLITE_STOP_LOSS_PCT = 9.0  # TECHO, no piso -- ver nota en schema/db.py


def get_satellite_exit_params(conn):
    """{'take_profit_pct': ..., 'stop_loss_pct': ...} -- defaults si por
    algun motivo la columna todavia no existe en una DB muy vieja (no
    deberia pasar, el ALTER TABLE con DEFAULT ya lo cubre, pero nunca se
    asume que una columna este poblada sin chequear)."""
    config = db.get_signal_config(conn)
    tp = config.get("satellite_take_profit_pct")
    sl = config.get("satellite_stop_loss_pct")
    return {
        "take_profit_pct": float(tp) if tp is not None else DEFAULT_SATELLITE_TAKE_PROFIT_PCT,
        "stop_loss_pct": float(sl) if sl is not None else DEFAULT_SATELLITE_STOP_LOSS_PCT,
    }


def _validate_exit_pct(value, field_name):
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field_name} debe ser un numero, se recibio {value!r}")
    if not (0.0 < value <= 100.0):
        raise ValueError(f"{field_name} debe estar entre 0 (exclusivo) y 100, se recibio {value}")
    return value


def set_satellite_exit_params(conn, take_profit_pct=None, stop_loss_pct=None):
    """Actualiza uno o ambos -- nunca pisa el que no se paso con None (ver
    PUT /api/portfolio/satellite-exit-params, que puede mandar solo uno).
    Lanza ValueError con mensaje claro si algun valor no es valido."""
    updates = {}
    if take_profit_pct is not None:
        updates["satellite_take_profit_pct"] = _validate_exit_pct(take_profit_pct, "take_profit_pct")
    if stop_loss_pct is not None:
        updates["satellite_stop_loss_pct"] = _validate_exit_pct(stop_loss_pct, "stop_loss_pct")
    if not updates:
        raise ValueError("Pasa al menos take_profit_pct o stop_loss_pct")
    db.update_signal_config(conn, **updates)
    return get_satellite_exit_params(conn)


# --- Umbral de seleccion del Motor Core (ver conversacion) ------------------
#
# Mismo criterio que get/set_satellite_exit_params de arriba: vive en
# signal_config (core_min_efficiency_score, ver portfolio/db.py), nunca una
# constante de archivo. portfolio/signals.py sigue declarando
# CORE_MIN_EFFICIENCY_SCORE=80.0 como default de referencia.

DEFAULT_CORE_MIN_EFFICIENCY_SCORE = 80.0


def get_core_params(conn):
    """{'min_efficiency_score': ...}"""
    config = db.get_signal_config(conn)
    score = config.get("core_min_efficiency_score")
    return {
        "min_efficiency_score": float(score) if score is not None else DEFAULT_CORE_MIN_EFFICIENCY_SCORE,
    }


# --- Perfiles de Inversion (ver conversacion) --------------------------------
#
# Un "perfil" es un PRESET de 3 parametros que YA EXISTEN arriba
# (core_allocation_pct via set_core_allocation_pct, satellite_stop_loss_pct/
# satellite_take_profit_pct via set_satellite_exit_params) -- activar un
# perfil llama a esas 2 funciones con los numeros del preset, NUNCA una tabla
# ni columna paralela. Esto es deliberado (mismo criterio que todo este
# archivo, ver docstring del modulo): si "perfil activo" se guardara aparte,
# cambiar manualmente el slider de Core/Satelite o el SL/TP en Ajustes
# dejaria ese flag mintiendo (mostraria "Moderado" con numeros que ya no son
# los de Moderado). En cambio, get_active_risk_profile() DERIVA el perfil
# activo comparando los 3 parametros reales contra los 3 presets en cada
# consulta -- si no matchea ninguno (el usuario toco algo a mano despues de
# activar un perfil), devuelve "personalizado" en vez de mentir.
#
# FIX (ver auditoria 19/10/2026, hallazgo 1.4 "los perfiles prometen 4-5x lo
# que da el backtest"): este dict ANTES traia ademas expected_return_usd_low/
# high_pct, sharpe_low/high y max_drawdown_pct hardcodeados -- "valores de
# referencia tal como los dio el enunciado de este feature", nunca atados al
# backtest real (textual, ver commit e72a4b4). Resultado medido: Moderado
# prometia +23,6% a +26,7% / Sharpe 1,34-1,77 cuando el backtest real a esa
# misma asignacion (20/80) da +5,3% / Sharpe 0,89 -- una promesa de
# rendimiento 4-5x mayor a lo que la propia plataforma mide. Se saca esa
# fantasia de aca: RISK_PROFILES ahora SOLO define los 3 parametros que
# describen al perfil (asignacion + salidas), nunca metricas de resultado.
# Las metricas de resultado las resuelve list_risk_profiles(conn) en vivo
# contra el backtest real guardado (ver _backtest_metrics_for_core_pct) --
# si no hay un backtest corrido exactamente a esa asignacion, el perfil
# queda sin metricas en vez de inventar un numero.
RISK_PROFILES = {
    "conservador": {"label": "Conservador", "core_pct": 70.0, "stop_loss_pct": 4.0, "take_profit_pct": 3.5},
    "moderado": {"label": "Moderado", "core_pct": 20.0, "stop_loss_pct": 9.0, "take_profit_pct": 4.0},
    "agresivo": {"label": "Agresivo", "core_pct": 0.0, "stop_loss_pct": 12.0, "take_profit_pct": 6.0},
}
# Orden de presentacion (Ajustes/Telegram) -- del mas defensivo al mas
# agresivo, nunca el orden de insercion incidental de un dict.
RISK_PROFILE_ORDER = ("conservador", "moderado", "agresivo")
DEFAULT_RISK_PROFILE = "moderado"  # coincide con DEFAULT_CORE_ALLOCATION_PCT=20.0 de arriba
_PROFILE_MATCH_TOLERANCE = 1e-6


def _backtest_metrics_for_core_pct(summary, core_pct):
    """Metricas REALES del backtest dual (ver portfolio/backtest.py,
    db.get_dual_engine_backtest_summary) que corresponden EXACTO a una
    asignacion core_pct dada -- None si nunca corrio un backtest a esa
    mezcla puntual (nunca se interpola/inventa un numero intermedio sin
    evidencia). satellite_metrics = la corrida a 0% Core (100% Satelite,
    perfil "agresivo"); core_metrics = la corrida a 100% Core (ningun
    perfil usa esto hoy); global_metrics = la corrida blendeada a la
    asignacion vigente en capital_allocation (hoy 20/80, matchea
    "moderado" -- si esa asignacion default cambia alguna vez, el perfil
    que deja de tener backtest exacto simplemente pasa a mostrarse sin
    metricas, nunca con una vieja desactualizada)."""
    if summary is None:
        return None
    if abs(core_pct - 0.0) < _PROFILE_MATCH_TOLERANCE:
        return summary.get("satellite_metrics")
    if abs(core_pct - 100.0) < _PROFILE_MATCH_TOLERANCE:
        return summary.get("core_metrics")
    allocation = summary.get("capital_allocation")
    if allocation and abs(core_pct - allocation.get("core_pct", -1)) < _PROFILE_MATCH_TOLERANCE:
        return summary.get("global_metrics")
    return None


def list_risk_profiles(conn=None):
    """Lista ordenada de {"id": ..., **preset, **metricas_reales_o_None} --
    copia (nunca el dict interno de RISK_PROFILES, para que nadie mute el
    preset desde afuera). conn=None (compatibilidad hacia atras / tests que
    no necesitan metricas) deja annualized_return_pct/sharpe_ratio/
    max_drawdown_pct/backtest_validated en None -- nunca un valor fantasma."""
    summary = db.get_dual_engine_backtest_summary(conn) if conn is not None else None
    profiles = []
    for pid in RISK_PROFILE_ORDER:
        preset = dict(RISK_PROFILES[pid])
        metrics = _backtest_metrics_for_core_pct(summary, preset["core_pct"])
        preset["backtest_validated"] = metrics is not None
        preset["annualized_return_pct"] = metrics["annualized_return_pct"] if metrics else None
        preset["sharpe_ratio"] = metrics["sharpe_ratio"] if metrics else None
        preset["max_drawdown_pct"] = metrics["max_drawdown_pct"] if metrics else None
        profiles.append({"id": pid, **preset})
    return profiles


def get_active_risk_profile(conn):
    """ID del perfil cuyos 3 parametros reales (core_pct/stop_loss_pct/
    take_profit_pct) matchean EXACTO -- o "personalizado" si no matchea
    ninguno (ver docstring de la seccion: esto se deriva, nunca se guarda
    aparte)."""
    allocation = get_capital_allocation(conn)
    exit_params = get_satellite_exit_params(conn)
    for pid in RISK_PROFILE_ORDER:
        preset = RISK_PROFILES[pid]
        if (
            abs(allocation["core_pct"] - preset["core_pct"]) < _PROFILE_MATCH_TOLERANCE
            and abs(exit_params["stop_loss_pct"] - preset["stop_loss_pct"]) < _PROFILE_MATCH_TOLERANCE
            and abs(exit_params["take_profit_pct"] - preset["take_profit_pct"]) < _PROFILE_MATCH_TOLERANCE
        ):
            return pid
    return "personalizado"


def activate_risk_profile(conn, profile_id):
    """Aplica los 3 parametros del preset via las MISMAS funciones de
    escritura que ya usan la API/Telegram para cambiarlos a mano
    (set_core_allocation_pct/set_satellite_exit_params) -- activar un perfil
    es 100% equivalente a cargar esos 3 valores uno por uno. Lanza
    ValueError si profile_id no es uno de los 3 presets."""
    preset = RISK_PROFILES.get(profile_id)
    if preset is None:
        raise ValueError(f"profile_id debe ser uno de {list(RISK_PROFILE_ORDER)}, se recibio {profile_id!r}")
    set_core_allocation_pct(conn, preset["core_pct"])
    set_satellite_exit_params(conn, take_profit_pct=preset["take_profit_pct"], stop_loss_pct=preset["stop_loss_pct"])
    return {
        "active_profile": profile_id,
        **get_capital_allocation(conn),
        **get_satellite_exit_params(conn),
    }


def set_core_min_efficiency_score(conn, min_efficiency_score):
    """Sin techo artificial de 100 (a diferencia de los % de TP/SL): el
    efficiency_score NO es un porcentaje, es un ratio retorno/dispersion que
    en la practica rondo entre -40 y 400 en esta conversacion (ver
    diagnostico en portfolio/signals.py) -- un valor negativo no tendria
    sentido como piso de seleccion (dejaria pasar candidatos con retorno
    proyectado negativo), por eso el minimo exigido es 0."""
    try:
        min_efficiency_score = float(min_efficiency_score)
    except (TypeError, ValueError):
        raise ValueError(f"min_efficiency_score debe ser un numero, se recibio {min_efficiency_score!r}")
    if min_efficiency_score < 0:
        raise ValueError(f"min_efficiency_score debe ser >= 0, se recibio {min_efficiency_score}")
    db.update_signal_config(conn, core_min_efficiency_score=min_efficiency_score)
    return get_core_params(conn)
