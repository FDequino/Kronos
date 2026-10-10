"""Orquesta un forecast de punta a punta: datos -> modelo -> indicadores -> senial.

Corre dentro del worker en background (ver forecast_worker.py), nunca en el
hilo que atiende el request HTTP que lo dispara.
"""
import json

import pandas as pd

from model import runtime as model_runtime
from portfolio import db, fetchers, indicators, signals

OHLCV_COLS = ["open", "high", "low", "close", "volume", "amount"]


def build_lookback_df(conn, *, instrument_id, symbol, market, asset_type, lookback, as_of_date=None):
    """Holdings reusan price_history (completando con backfill si falta);
    el Explorador pide historico fresco directo, sin necesitar alta en instruments.

    as_of_date acota el lookback a velas <= esa fecha -- es lo que hace que un
    backtest sea honesto (el modelo nunca ve datos que todavia no existian en
    ese momento del pasado)."""
    if instrument_id is not None:
        if as_of_date:
            rows = db.get_price_history_as_of(conn, instrument_id, as_of_date, limit=lookback)
        else:
            rows = db.get_price_history(conn, instrument_id, limit=lookback)
            if len(rows) < lookback:
                instrument = db.get_instrument(conn, instrument_id)
                fetchers.backfill_history(conn, instrument)
                rows = db.get_price_history(conn, instrument_id, limit=lookback)
        df = pd.DataFrame(rows)
        df["timestamps"] = pd.to_datetime(df["timestamp"])
        df["amount"] = 0.0
    else:
        df = fetchers.fetch_data912_historical(symbol, asset_type)

    df = df.sort_values("timestamps").tail(lookback).reset_index(drop=True)
    return df[["timestamps"] + OHLCV_COLS]


def _future_timestamps(timestamps, pred_len):
    """Extiende la frecuencia detectada en el historico hacia adelante.
    Simplificacion aceptada: no excluye fines de semana/feriados en datos diarios."""
    deltas = timestamps.diff().dropna()
    step = deltas.mode().iloc[0] if not deltas.empty else pd.Timedelta(days=1)
    last = timestamps.iloc[-1]
    return pd.Series([last + step * (i + 1) for i in range(pred_len)])


def _candles_json(df, timestamps):
    return [
        {
            "time": int(pd.Timestamp(ts).timestamp()),
            "open": float(row.open),
            "high": float(row.high),
            "low": float(row.low),
            "close": float(row.close),
        }
        for ts, row in zip(timestamps, df.itertuples(index=False))
    ]


def run_forecast_job(conn, forecast_id):
    row = db.get_forecast(conn, forecast_id)
    config = db.get_signal_config(conn)

    lookback_df = build_lookback_df(
        conn, instrument_id=row["instrument_id"], symbol=row["symbol"],
        market=row["market"], asset_type=row["asset_type"], lookback=row["lookback"],
        as_of_date=row["as_of_date"],
    )
    x_df = lookback_df[OHLCV_COLS]
    x_timestamp = lookback_df["timestamps"]
    y_timestamp = _future_timestamps(x_timestamp, row["pred_len"])

    model_runtime.ensure_loaded(row["model_key"])
    predictor = model_runtime.get_predictor()

    if row["sample_count"] > 1:
        pred_mean, sample_dfs = predictor.predict_samples(
            df=x_df, x_timestamp=x_timestamp, y_timestamp=y_timestamp,
            pred_len=row["pred_len"], T=row["temperature"], top_p=row["top_p"],
            sample_count=row["sample_count"], verbose=False,
        )
    else:
        pred_mean = predictor.predict(
            df=x_df, x_timestamp=x_timestamp, y_timestamp=y_timestamp,
            pred_len=row["pred_len"], T=row["temperature"], top_p=row["top_p"],
            sample_count=1, verbose=False,
        )
        sample_dfs = None

    last_close = float(x_df["close"].iloc[-1])
    signal, pred_change_pct, uncertainty_pct = signals.compute_signal(
        last_close, pred_mean, sample_dfs, config
    )
    explanation = signals.explain(
        row["symbol"], pred_change_pct, uncertainty_pct, signal, row["pred_len"], config
    )

    result = {
        "symbol": row["symbol"],
        "historical": _candles_json(lookback_df, lookback_df["timestamps"]),
        "predicted": _candles_json(pred_mean, pred_mean.index),
        "indicators": indicators.compute_all(lookback_df),
        "signal": signal,
        "pred_change_pct": pred_change_pct,
        "uncertainty_pct": uncertainty_pct,
        "explanation": explanation,
    }

    db.mark_forecast_done(
        conn, forecast_id,
        result_json=json.dumps(result),
        signal=signal, pred_change_pct=pred_change_pct, uncertainty_pct=uncertainty_pct,
    )
