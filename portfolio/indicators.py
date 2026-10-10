"""Indicadores tecnicos estandar sobre una serie de precios de cierre."""
import pandas as pd


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
