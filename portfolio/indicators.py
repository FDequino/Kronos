"""Indicadores técnicos estándar sobre una serie de precios de cierre."""
import pandas as pd

from portfolio.formatting import fmt_es

BACKTEST_HORIZON_DAYS = 14
BACKTEST_MIN_ROWS = BACKTEST_HORIZON_DAYS + 20


def sma(close, window=20):
    return close.rolling(window=window, min_periods=1).mean()


def ema(close, window=20):
    return close.ewm(span=window, adjust=False).mean()


def rsi(close, window=14):
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(window=window, min_periods=1).mean()
    loss = (-delta.clip(upper=0)).rolling(window=window, min_periods=1).mean()
    rs = gain / loss.replace(0, float("nan"))
    return (100 - 100 / (1 + rs)).fillna(50)


def macd(close, fast=12, slow=26, signal=9):
    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return pd.DataFrame({"macd": macd_line, "signal": signal_line, "hist": macd_line - signal_line})


def bollinger_bands(close, window=20, num_std=2):
    mid = sma(close, window)
    std = close.rolling(window=window, min_periods=1).std().fillna(0)
    return pd.DataFrame({"mid": mid, "upper": mid + num_std * std, "lower": mid - num_std * std})


def true_range(df):
    """A diferencia del resto de este archivo (solo 'close'), ATR necesita
    high/low tambien -- df: columnas open/high/low/close."""
    prev_close = df["close"].shift(1)
    ranges = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()],
        axis=1,
    )
    return ranges.max(axis=1)


def atr(df, window=14):
    """Average True Range -- usado por portfolio/signals.py (dimensionamiento
    Core) y portfolio/backtest.py (Take Profit/Trailing Stop dinamicos)."""
    return true_range(df).rolling(window=window, min_periods=1).mean()


def atr_pct(df, window=14):
    """ATR normalizado por precio (%) -- comparable entre activos de
    distinta escala de precio, a diferencia del ATR en valor absoluto."""
    close = df["close"]
    return atr(df, window) / close.replace(0, float("nan")) * 100


def compute_all(df, close_col="close"):
    """df con columna de cierre -> dict serializable con cada serie alineada al index."""
    close = df[close_col]
    macd_df = macd(close)
    bb_df = bollinger_bands(close)
    return {
        "sma_20": sma(close, 20).tolist(),
        "ema_20": ema(close, 20).tolist(),
        "rsi_14": rsi(close, 14).tolist(),
        "macd": macd_df["macd"].tolist(),
        "macd_signal": macd_df["signal"].tolist(),
        "macd_hist": macd_df["hist"].tolist(),
        "bb_upper": bb_df["upper"].tolist(),
        "bb_lower": bb_df["lower"].tolist(),
    }


def latest_technical_signal(indicators_dict, config, min_points=20):
    """Señal técnica "segunda opinión", independiente de Kronos: compara el
    último RSI(14) contra los umbrales configurables ta_rsi_oversold/overbought.
    Nunca inventa una señal con muy poco histórico -- devuelve (None, motivo)."""
    rsi_series = indicators_dict.get("rsi_14") or []
    if len(rsi_series) < min_points:
        return None, "Historial insuficiente para una señal técnica confiable."

    rsi_val = rsi_series[-1]
    oversold = config["ta_rsi_oversold"]
    overbought = config["ta_rsi_overbought"]
    if rsi_val <= oversold:
        signal = "comprar"
    elif rsi_val >= overbought:
        signal = "vender"
    else:
        signal = "mantener"

    explanation = (
        f"RSI(14) = {fmt_es(rsi_val)}. Umbral de sobreventa {fmt_es(oversold, decimals=0)}, "
        f"sobrecompra {fmt_es(overbought, decimals=0)} -> señal técnica: {signal}. "
        f"Heurística clásica sobre precio (no usa Kronos)."
    )
    return signal, explanation


def backtest_rsi_signal(price_rows, config):
    """Recorre TODO el historico ya guardado: en cada dia calcula que señal daba
    el RSI de ese momento (rolling, nunca mira para adelante) y la compara contra
    el retorno real BACKTEST_HORIZON_DAYS despues -- ya paso, no hace falta
    ningun modelo ni cola de trabajo para esto, es instantaneo."""
    if len(price_rows) < BACKTEST_MIN_ROWS:
        return {"evaluated": 0, "pending": True}

    df = pd.DataFrame(price_rows).sort_values("timestamp").reset_index(drop=True)
    close = df["close"]
    rsi_series = rsi(close, 14)
    oversold = config["ta_rsi_oversold"]
    overbought = config["ta_rsi_overbought"]

    buckets = {"comprar": [], "mantener": [], "vender": []}
    all_returns = []
    last_evaluable = len(df) - BACKTEST_HORIZON_DAYS
    for i in range(last_evaluable):
        r = rsi_series.iloc[i]
        if r <= oversold:
            signal = "comprar"
        elif r >= overbought:
            signal = "vender"
        else:
            signal = "mantener"
        c0 = close.iloc[i]
        c1 = close.iloc[i + BACKTEST_HORIZON_DAYS]
        if c0:
            ret = (c1 - c0) / c0 * 100
            buckets[signal].append(ret)
            all_returns.append(ret)

    # Tasa base: que tan seguido sube el activo en una ventana de
    # BACKTEST_HORIZON_DAYS sin importar que señal daba el RSI ese día -- el
    # acierto de una señal solo dice algo si supera esto (ver edge_vs_base_rate_pct).
    base_rate_up_pct = (
        sum(1 for x in all_returns if x > 0) / len(all_returns) * 100 if all_returns else None
    )

    summary = {}
    for signal, returns in buckets.items():
        if not returns:
            summary[signal] = None
            continue
        avg_return = sum(returns) / len(returns)
        edge_pct = None
        if signal == "comprar":
            hit_rate = sum(1 for x in returns if x > 0) / len(returns) * 100
            if base_rate_up_pct is not None:
                edge_pct = hit_rate - base_rate_up_pct
        elif signal == "vender":
            hit_rate = sum(1 for x in returns if x < 0) / len(returns) * 100
            if base_rate_up_pct is not None:
                edge_pct = hit_rate - (100 - base_rate_up_pct)
        else:
            hit_rate = None
        summary[signal] = {
            "count": len(returns),
            "avg_forward_return_pct": round(avg_return, 2),
            "hit_rate_pct": round(hit_rate, 1) if hit_rate is not None else None,
            "edge_vs_base_rate_pct": round(edge_pct, 1) if edge_pct is not None else None,
        }

    return {
        "evaluated": sum(len(v) for v in buckets.values()),
        "pending": False,
        "horizon_days": BACKTEST_HORIZON_DAYS,
        "base_rate_up_pct": round(base_rate_up_pct, 1) if base_rate_up_pct is not None else None,
        "by_signal": summary,
    }
