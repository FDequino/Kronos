"""Traduce un forecast de Kronos en una señal simple + su explicación.

Es una heurística de umbral sobre el % que pronostica el modelo.
"""
import numpy as np

from portfolio.formatting import fmt_es


def compute_signal(last_close, pred_mean_df, sample_dfs, config):
    pred_close = pred_mean_df["close"].iloc[-1]
    pred_change_pct = (pred_close - last_close) / last_close * 100

    uncertainty_pct = None
    distribution = None
    if sample_dfs and len(sample_dfs) > 1:
        sample_changes = np.array([
            (s["close"].iloc[-1] - last_close) / last_close * 100 for s in sample_dfs
        ])
        uncertainty_pct = float(np.std(sample_changes))
        median_pct = float(np.median(sample_changes))
        p10_pct = float(np.percentile(sample_changes, 10))
        p90_pct = float(np.percentile(sample_changes, 90))
        band_width = p90_pct - p10_pct
        # |mediana| / ancho de banda p10-p90: que tan grande es el movimiento
        # pronosticado frente a lo incierto que es el propio pronostico (mismo
        # concepto que services/predictor/main.py::_dispersion_ratio, pero
        # calculado aca con la distribucion ya resampleada que usa esta
        # funcion -- no se reusa el campo que devuelve el microservicio a
        # proposito, para no atar la decision de señal a un detalle de
        # implementacion de un motor de prediccion en particular).
        edge_ratio = abs(median_pct) / band_width if band_width > 0 else None
        distribution = {
            "median_pct": median_pct,
            "p10_pct": p10_pct,
            "p90_pct": p90_pct,
            "prob_up_pct": float((sample_changes > 0).mean() * 100),
            "sample_count": int(len(sample_changes)),
            "edge_ratio": edge_ratio,
        }
        # La mediana de las muestras es mas robusta a samples extremos de la
        # cola que el promedio puntual que ya trae pred_mean_df -- la señal
        # se decide con la distribucion completa, no con un unico punto.
        pred_change_pct = median_pct

    if pred_change_pct >= config["buy_threshold_pct"]:
        signal = "comprar"
    elif pred_change_pct <= config["sell_threshold_pct"]:
        signal = "vender"
    else:
        signal = "mantener"

    # Degradar a "mantener" si el movimiento proyectado es chico frente a la
    # incertidumbre del propio modelo, aunque ya haya cruzado el umbral de %
    # -- un +6% con una banda p10-p90 de 60 puntos no es la misma señal que
    # un +6% con una banda de 8 puntos, aunque los dos crucen buy_threshold_pct
    # igual. Nunca se oculta el porque (ver explain()).
    degraded_low_edge = False
    if signal != "mantener" and distribution and distribution["edge_ratio"] is not None:
        if distribution["edge_ratio"] < config["min_signal_edge_ratio"]:
            signal = "mantener"
            degraded_low_edge = True
    if distribution is not None:
        distribution["degraded_low_edge"] = degraded_low_edge

    return signal, float(pred_change_pct), uncertainty_pct, distribution


def explain(symbol, pred_change_pct, uncertainty_pct, signal, pred_len, config,
            distribution=None, model_currency=None):
    direccion = "suba" if pred_change_pct >= 0 else "baja"
    base = (
        f"El modelo pronostica una {direccion} del {fmt_es(abs(pred_change_pct))}% en {symbol} "
        f"a {pred_len} pasos. Umbral configurado: compra desde {fmt_es(config['buy_threshold_pct'], signed=True)}%, "
        f"venta desde {fmt_es(config['sell_threshold_pct'])}% -> señal: {signal}."
    )
    if distribution:
        base += (
            f" Sobre {distribution['sample_count']} muestras del modelo: mediana "
            f"{fmt_es(distribution['median_pct'], signed=True)}%, rango p10-p90 de "
            f"{fmt_es(distribution['p10_pct'], signed=True)}% a {fmt_es(distribution['p90_pct'], signed=True)}%, "
            f"P(suba) {fmt_es(distribution['prob_up_pct'], decimals=0)}%."
        )
        if distribution.get("degraded_low_edge"):
            base += (
                f" Señal degradada a mantener: el movimiento proyectado es chico frente a la "
                f"incertidumbre del propio pronóstico (edge ratio "
                f"{fmt_es(distribution['edge_ratio'], decimals=2)}, "
                f"umbral {fmt_es(config['min_signal_edge_ratio'], decimals=2)})."
            )
    elif uncertainty_pct is not None:
        base += (
            f" Las muestras del modelo difieren entre sí en torno a {fmt_es(uncertainty_pct)} puntos "
            f"porcentuales -- cuanto más alto, menos de fiar es el número puntual."
        )
    if model_currency == "USD":
        base += (
            " El modelo corrió sobre la serie convertida a USD (CCL); el gráfico se muestra "
            "reconvertido a ARS con el último CCL conocido."
        )
    elif model_currency == "USD_REAL_UNDERLYING":
        base += (
            " El modelo corrió sobre el precio real del activo subyacente en EEUU (en vez de la "
            "serie sintética CEDEAR/CCL), más preciso para este símbolo; el gráfico se muestra "
            "reconvertido a ARS preservando la forma de lo que predijo el modelo."
        )
    base += (
        " El primer paso de cada muestra se ancló al último cierre real conocido (el modelo no "
        "garantiza esa continuidad por sí solo) -- se corrige el nivel, nunca la forma de lo "
        "que predijo el modelo."
    )
    return base


# --- Motor CORE institucional (ver conversacion) ---------------------------
#
# Seleccion CROSS-SECTIONAL sobre el universo completo, no un umbral fijo
# por instrumento como compute_signal() de arriba (que sigue intacto -- lo
# siguen usando Señales/forecast-card/instrument-detail/etc, nada de esto lo
# reemplaza). portfolio/backtest.py orquesta estas funciones dia a dia sobre
# todo el universo para el Motor Core; portfolio/short_term_engine.py es el
# Motor Satelite, independiente.

# Refactorizacion financiera (ver conversacion): estas funciones son puras
# (reciben numeros ya calculados, nunca precios ni DataFrames) -- la
# conversion a USD CCL vive en el CALLER (portfolio/backtest.py,
# portfolio/live_signals.py, via forecasting.build_model_input), nunca aca.
# Por contrato, quien las llama debe pasar atr_pct_value/p50_pct/dispersion
# ya calculados sobre la serie en USD, nunca ARS cruda.
CORE_MIN_EFFICIENCY_SCORE = 80.0  # REVERTIDO a 80.0 (ver conversacion): se probo 68.0 para aumentar
                                   # frecuencia -- subio de 14 a 23 trades, pero la esperanza matematica de
                                   # los ~10 trades nuevos (score 68-80) resulto NEGATIVA (7 perdedores, 3
                                   # ganadores chicos), empeorando USD anualizado de -3.1% a -14.7% y Max DD
                                   # de -21.2% a -26.3%. Confirmado por separado que la falta de trades
                                   # Out-Of-Sample NO era por este umbral: el filtro de regimen (SPY>EMA50)
                                   # estuvo abierto solo 4 de los ultimos 100 dias -- un hecho estructural
                                   # del mercado en ese tramo, independiente de este numero. 80.0 queda como
                                   # el valor con mejor evidencia empirica hasta ahora (no el mas alto
                                   # probado -- 130.0 fue aun mas selectivo -- pero el 68.0 probo que bajar
                                   # de 80 destruye valor, no lo crea).
CORE_PERCENTILE_THRESHOLD = 75  # percentil dinamico por rueda (ver rank_core_candidates), ademas del piso fijo de arriba
CORE_MIN_CANDIDATES_FOR_PERCENTILE = 4  # con menos candidatos ese dia, un percentil 75 no es representativo -- se usa solo el piso fijo
CORE_TOP_N = 2
CORE_TARGET_RISK_PCT = 15.0  # "Target_Risk_15pct" del pedido

# Filtro de correlacion/concentracion (ver conversacion): hallazgo real del
# backtest -- GGAL y BMA (los 2 bancos argentinos mas grandes del universo,
# ver portfolio/backtest.py::UNIVERSE) calificaron el MISMO dia, el Core
# abrio las 2 posiciones a la vez, y ambas tocaron stop casi juntas --
# duplico el golpe al drawdown de cartera en lugar de diversificarlo (dos
# apuestas al mismo sector no son dos apuestas independientes).
#
# Clasificacion sectorial DECLARADA a mano (no una matriz de correlacion
# rolling calculada -- seria mas preciso pero bastante mas trabajo para lo
# que pide esto; se documenta la simplificacion en vez de ocultarla), cubre
# TODO el universo de backtest.py para que ningun ticker nuevo quede sin
# clasificar en silencio. Activos sin entrada aca (no deberia pasar) nunca
# se consideran correlacionados entre si.
CORE_SECTOR_GROUPS = {
    "GGAL": "bancos_arg", "BMA": "bancos_arg",
    "YPFD": "energia_arg", "PAMP": "energia_arg",
    "ALUA": "industrial_arg",
    "CRES": "real_estate_arg",
    "SPY": "indices_us", "QQQ": "indices_us",
    "AAPL": "big_tech_us", "MSFT": "big_tech_us", "GOOGL": "big_tech_us",
    "META": "big_tech_us", "AMZN": "big_tech_us", "NVDA": "big_tech_us",
    "TSLA": "auto_ev_us",
    "MELI": "ecommerce_latam",
    "AL30D": "renta_fija",
}


def compute_efficiency_score(chronos_p50_return_pct, chronos_dispersion_ratio):
    """efficiency_score = retorno proyectado / dispersion -- cuanto mas
    retorno promete el modelo POR UNIDAD de su propia incertidumbre, mejor
    candidato cross-sectional. +1e-6 en el denominador solo para no dividir
    por cero con una banda perfectamente angosta (caso degenerado, no deberia
    pasar en la practica, pero nunca se deja romper el ranking por eso)."""
    return chronos_p50_return_pct / (chronos_dispersion_ratio + 1e-6)


def rank_core_candidates(candidates, spy_above_ema50, top_n=CORE_TOP_N, min_score=CORE_MIN_EFFICIENCY_SCORE):
    """candidates: lista de dicts con al menos 'symbol'/'efficiency_score'.
    spy_above_ema50: filtro macro de regimen -- si el mercado global no
    acompaña (SPY por debajo de su propia EMA50), NINGUN candidato califica
    ese dia, sin importar su score individual (el capital de Core queda en
    Caucion, ver portfolio/config.py).

    Umbral EFECTIVO = max(min_score, percentil 75 de los scores de ESTE dia)
    -- el piso fijo nunca se relaja, pero en una rueda donde el universo
    entero pronostica muy bien (todos los scores altos) el umbral sube para
    seguir exigiendo estar entre lo mejor del dia, no solo superar un numero
    fijo. Con pocos candidatos (< CORE_MIN_CANDIDATES_FOR_PERCENTILE) el
    percentil de una muestra tan chica no es representativo, se usa solo el
    piso fijo.

    Filtro de correlacion/concentracion (ver conversacion, CORE_SECTOR_GROUPS
    arriba): entre los que superan el umbral, si 2+ pertenecen al MISMO
    grupo sectorial se descartan todos salvo el de mayor efficiency_score de
    ese grupo -- nunca se abren 2 posiciones simultaneas en activos
    historicamente correlacionados (ej. GGAL+BMA), aunque ambos califiquen
    individualmente.

    Devuelve los top_n candidatos que superan el umbral efectivo Y pasan el
    filtro de concentracion, ordenados de mejor a peor -- puede ser una
    lista vacia, de 1, o de top_n elementos."""
    if not spy_above_ema50 or not candidates:
        return []
    effective_min_score = min_score
    if len(candidates) >= CORE_MIN_CANDIDATES_FOR_PERCENTILE:
        scores = [c["efficiency_score"] for c in candidates]
        dynamic_threshold = float(np.percentile(scores, CORE_PERCENTILE_THRESHOLD))
        effective_min_score = max(min_score, dynamic_threshold)
    qualifying = [c for c in candidates if c["efficiency_score"] > effective_min_score]
    qualifying.sort(key=lambda c: c["efficiency_score"], reverse=True)

    seen_groups = set()
    deduped = []
    for c in qualifying:
        group = CORE_SECTOR_GROUPS.get(c["symbol"])
        if group is not None:
            if group in seen_groups:
                continue
            seen_groups.add(group)
        deduped.append(c)

    return deduped[:top_n]


def compute_core_position_weight(atr_pct_value, chronos_dispersion_ratio, target_risk_pct=CORE_TARGET_RISK_PCT):
    """Volatility Targeting: mientras mas volatil (ATR alto) o mas incierto
    (dispersion alta) este el activo, MENOR la posicion dentro de la porcion
    de capital asignada a Core -- nunca se apalanca por encima de 1.0 (100%
    de esa porcion sobre una sola señal)."""
    denom = atr_pct_value * chronos_dispersion_ratio
    if not denom or denom <= 0:
        return 0.0
    return min(1.0, target_risk_pct / denom)
