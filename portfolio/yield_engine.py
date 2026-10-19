"""Motor de Cash Management & Yield (ver conversacion): centraliza como se
calcula el rendimiento del capital OCIOSO de ambos motores (Core y Satelite,
ver portfolio/signals.py y portfolio/short_term_engine.py) cuando queda en
Caucion/Money Market en vez de invertido, y como se combinan las dos curvas
de retorno diario en una sola curva de PATRIMONIO GLOBAL ponderada por la
asignacion de capital vigente (portfolio/config.py, dinamica -- nunca una
constante fija).

Antes este calculo vivia duplicado e inline dentro de
portfolio/backtest.py::simulate_core_engine/simulate_satellite_engine --
ahora los dos llaman a daily_caucion_return_pct() de aca, asi nunca pueden
divergir en el criterio de prorateo si se ajusta a futuro."""
import pandas as pd


def daily_caucion_return_pct(tna_pct):
    """Retorno diario SIMPLE prorateado de una TNA -- mismo criterio que
    portfolio/backtest.py::compute_hurdle_pct (TNA/100 * dias/365). Nunca se
    inventa una tasa de interes compuesto diario que ninguna caucion real
    publica -- la composicion de varios dias se obtiene ENCADENANDO este
    retorno diario en una curva de equity (ver compound_idle_capital_curve),
    no inflando la tasa diaria misma."""
    return float(tna_pct) / 100.0 / 365.0


def compound_idle_capital_curve(idle_weight_series, tna_pct):
    """Curva de equity (base 1.0) de SOLO la porcion de capital ociosa de un
    motor -- cada rueda que una fraccion idle_weight no esta invertida, esa
    fraccion compone al retorno diario de daily_caucion_return_pct(tna_pct).
    Sirve para reportar por separado cuanto aporto la Caucion sola, sin
    mezclarla con el resultado de los trades (que ya viene incluido en
    daily_returns_pct de cada motor, ver simulate_core_engine /
    simulate_satellite_engine en portfolio/backtest.py)."""
    daily_rate_pct = daily_caucion_return_pct(tna_pct)
    daily_contribution = idle_weight_series.astype(float) * (daily_rate_pct / 100.0)
    return (1 + daily_contribution).cumprod()


def blend_engine_returns(core_daily_return_pct, satellite_daily_return_pct, core_capital_pct, satellite_capital_pct):
    """Retorno %/dia del PATRIMONIO GLOBAL: combina Core y Satelite (cada
    serie YA incluye su propia pata de Caucion ociosa) ponderados por la
    asignacion de capital vigente (ver portfolio/config.py,
    get_capital_allocation) -- una fecha que solo tiene datos de uno de los
    2 motores (historial distinto por ticker/skip) se trata como 0% en el
    que falta, nunca se extrapola un retorno inventado."""
    combined = pd.concat(
        [core_daily_return_pct.rename("core"), satellite_daily_return_pct.rename("satellite")], axis=1
    ).fillna(0.0)
    return combined["core"] * (core_capital_pct / 100.0) + combined["satellite"] * (satellite_capital_pct / 100.0)
