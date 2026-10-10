"""
Descarga datos historicos en el formato que espera Kronos (open, high, low, close,
volume, amount, timestamps) y los guarda en la carpeta data/ del proyecto.

Fuentes soportadas:
  - binance: velas de criptomonedas (API publica, sin API key)
  - yfinance: acciones, indices y CEDEARs argentinos (sufijo .BA en BYMA)

Ejemplos:
  python examples/fetch_data.py binance --symbol BTCUSDT --interval 1h --limit 1000
  python examples/fetch_data.py yfinance --ticker AAPL.BA --period 2y --interval 1d
"""
import argparse
import os

import pandas as pd
import requests

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


def fetch_binance(symbol: str, interval: str, limit: int) -> pd.DataFrame:
    url = "https://api.binance.com/api/v3/klines"
    params = {"symbol": symbol.upper(), "interval": interval, "limit": limit}
    resp = requests.get(url, params=params, timeout=15)
    resp.raise_for_status()
    raw = resp.json()
    df = pd.DataFrame(raw, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_asset_volume", "num_trades",
        "taker_buy_base", "taker_buy_quote", "ignore",
    ])
    df["timestamps"] = pd.to_datetime(df["open_time"], unit="ms")
    df["amount"] = df["quote_asset_volume"].astype(float)
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    return df[["timestamps", "open", "high", "low", "close", "volume", "amount"]]


def fetch_yfinance(ticker: str, period: str, interval: str) -> pd.DataFrame:
    import yfinance as yf

    df = yf.download(ticker, period=period, interval=interval, progress=False, auto_adjust=False)
    if df.empty:
        raise ValueError(f"yfinance no devolvio datos para {ticker}")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.reset_index()
    date_col = "Date" if "Date" in df.columns else "Datetime"
    df = df.rename(columns={
        date_col: "timestamps", "Open": "open", "High": "high",
        "Low": "low", "Close": "close", "Volume": "volume",
    })
    df["amount"] = 0
    return df[["timestamps", "open", "high", "low", "close", "volume", "amount"]]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="source", required=True)

    p_binance = sub.add_parser("binance", help="Criptomonedas via API publica de Binance")
    p_binance.add_argument("--symbol", required=True, help="Ej: BTCUSDT, ETHUSDT")
    p_binance.add_argument("--interval", default="1h", help="1m,5m,15m,1h,4h,1d (default: 1h)")
    p_binance.add_argument("--limit", type=int, default=1000, help="Cantidad de velas, max 1000 (default: 1000)")

    p_yf = sub.add_parser("yfinance", help="Acciones, indices y CEDEARs (Yahoo Finance)")
    p_yf.add_argument("--ticker", required=True, help="Ej: AAPL.BA (CEDEAR), GGAL.BA, BTC-USD")
    p_yf.add_argument("--period", default="2y", help="Ej: 1y,2y,5y,max (default: 2y)")
    p_yf.add_argument("--interval", default="1d", help="1d,1h,... (intradia limitado a ~60 dias por Yahoo)")

    args = parser.parse_args()
    os.makedirs(DATA_DIR, exist_ok=True)

    if args.source == "binance":
        df = fetch_binance(args.symbol, args.interval, args.limit)
        out_name = f"{args.symbol.upper()}_{args.interval}.csv"
    else:
        df = fetch_yfinance(args.ticker, args.period, args.interval)
        safe_ticker = args.ticker.replace(".", "_").replace("-", "_")
        out_name = f"{safe_ticker}_{args.interval}.csv"

    out_path = os.path.join(DATA_DIR, out_name)
    df.to_csv(out_path, index=False)
    print(f"Guardado {len(df)} filas en {out_path}")


if __name__ == "__main__":
    main()
