"""Nivel de riesgo por instrumento y de cartera.

Metodologia explicita y ajustable (ver signal_config), nunca una caja negra:
riesgo = volatilidad_historica * peso + riesgo_base_del_tipo * (1 - peso).
"""
import math

RISK_BASELINE_FIELD = {
    "moneda": "risk_baseline_moneda",
    "bono": "risk_baseline_bono",
    "on": "risk_baseline_on",
    "fondo": "risk_baseline_fondo",
    "cedear": "risk_baseline_cedear",
    "accion": "risk_baseline_accion",
}

MIN_PRICE_POINTS = 10
ANNUALIZATION_FACTOR = math.sqrt(252)
VOLATILITY_SCALE_PCT = 80.0  # 80% de volatilidad anualizada -> score 100


def _label(score):
    if score < 34:
        return "bajo"
    if score < 67:
        return "medio"
    return "alto"


def compute_volatility_score(closes):
    if len(closes) < MIN_PRICE_POINTS:
        return None
    returns = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes)) if closes[i - 1] > 0]
    if len(returns) < MIN_PRICE_POINTS - 1:
        return None
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / len(returns)
    annualized_vol_pct = math.sqrt(variance) * ANNUALIZATION_FACTOR * 100
    return min(100.0, annualized_vol_pct / VOLATILITY_SCALE_PCT * 100), annualized_vol_pct


def compute_instrument_risk(closes, instrument_type, config):
    baseline = config[RISK_BASELINE_FIELD.get(instrument_type, "risk_baseline_fondo")]
    weight = config["risk_weight_volatility"]
    vol_result = compute_volatility_score(closes)

    if vol_result is None:
        score = baseline
        vol_score = None
        annualized_vol_pct = None
    else:
        vol_score, annualized_vol_pct = vol_result
        score = vol_score * weight + baseline * (1 - weight)

    return {
        "score": round(score, 1),
        "label": _label(score),
        "baseline": baseline,
        "weight": weight,
        "volatility_score": round(vol_score, 1) if vol_score is not None else None,
        "annualized_volatility_pct": round(annualized_vol_pct, 1) if annualized_vol_pct is not None else None,
    }


def explain_instrument_risk(symbol, risk):
    if risk["annualized_volatility_pct"] is None:
        return (
            f"{symbol}: riesgo {risk['label']} ({risk['score']}/100), basado solo en el tipo de "
            f"instrumento -- no hay suficiente historico de precios para medir su volatilidad real."
        )
    return (
        f"{symbol}: riesgo {risk['label']} ({risk['score']}/100). Volatilidad anualizada "
        f"{risk['annualized_volatility_pct']}% combinada con el riesgo base de su tipo "
        f"({risk['baseline']}/100)."
    )


def compute_portfolio_risk(holdings_with_risk):
    """holdings_with_risk: lista de {valor_actual, risk: {score}}. Penaliza concentracion
    (una sola tenencia con mas del 25% de la cartera) ademas del promedio ponderado."""
    total_value = sum(h["valor_actual"] or 0 for h in holdings_with_risk)
    if total_value <= 0:
        return None

    weighted_score = sum((h["valor_actual"] or 0) / total_value * h["risk"]["score"] for h in holdings_with_risk)
    max_weight = max((h["valor_actual"] or 0) / total_value for h in holdings_with_risk)
    concentration_penalty = max(0.0, (max_weight - 0.25) * 100)
    score = min(100.0, weighted_score + concentration_penalty)

    return {
        "score": round(score, 1),
        "label": _label(score),
        "weighted_score": round(weighted_score, 1),
        "concentration_penalty": round(concentration_penalty, 1),
        "max_weight_pct": round(max_weight * 100, 1),
    }


def explain_portfolio_risk(risk):
    base = f"Riesgo de cartera {risk['label']} ({risk['score']}/100): promedio ponderado por valor {risk['weighted_score']}/100"
    if risk["concentration_penalty"] > 0:
        base += f", + {risk['concentration_penalty']} por concentracion (tu mayor tenencia es el {risk['max_weight_pct']}% de la cartera)"
    return base + "."
