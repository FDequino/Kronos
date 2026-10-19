"""Fundamentales de mercado via yfinance -- data912 (la fuente de precios de BYMA
que ya usamos) no expone nada de esto. Nunca inventa un numero: si yfinance no
tiene el campo o el simbolo no resuelve, queda marcado available=False con el
motivo, no se rellena con un valor falso."""
import yfinance as yf

from portfolio.formatting import fmt_es

NOT_APPLICABLE_TYPES = {"bono", "on", "moneda"}

FIELD_MAP = {
    "pe_ratio": "trailingPE",
    "forward_pe": "forwardPE",
    "profit_margin": "profitMargins",
    "debt_to_equity": "debtToEquity",
    "dividend_yield": "dividendYield",
    "market_cap": "marketCap",
    "target_mean_price": "targetMeanPrice",
}


def fetch_fundamentals(symbol, instrument_type):
    if instrument_type in NOT_APPLICABLE_TYPES:
        return {
            "available": False, "not_applicable": True,
            "reason": "No aplica para renta fija / moneda.",
        }

    try:
        info = yf.Ticker(symbol).info
    except Exception as e:
        return {"available": False, "not_applicable": False, "reason": f"yfinance no respondió: {e}"}

    if not info or (info.get("regularMarketPrice") is None and info.get("currentPrice") is None):
        return {
            "available": False, "not_applicable": False,
            "reason": f"Sin datos fundamentales para el símbolo {symbol} en yfinance.",
        }

    data = {key: info.get(src) for key, src in FIELD_MAP.items()}
    data["available"] = True
    data["not_applicable"] = False
    data["reason"] = None
    return data


def explain_fundamentals(symbol, data, config):
    if not data.get("available"):
        return data.get("reason") or "Sin datos fundamentales disponibles."

    pe = data.get("pe_ratio")
    margin = data.get("profit_margin")
    if pe is None or margin is None:
        return f"{symbol}: faltan P/E o margen neto para evaluar salud fundamental."

    healthy = pe < config["fa_max_pe"] and margin > config["fa_min_margin_pct"] / 100
    veredicto = "saludable según el filtro configurado" if healthy else "no cumple el filtro configurado"
    return (
        f"{symbol}: P/E {fmt_es(pe, decimals=2)} (umbral {fmt_es(config['fa_max_pe'], decimals=0)}), "
        f"margen neto {fmt_es(margin * 100)}% (umbral {fmt_es(config['fa_min_margin_pct'], decimals=0)}%) "
        f"-> {veredicto}."
    )
