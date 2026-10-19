"""Orquesta un forecast de punta a punta: datos -> modelo -> indicadores -> senial.

Corre dentro del worker en background (ver forecast_worker.py), nunca en el
hilo que atiende el request HTTP que lo dispara.

El modelo (antes Kronos in-process, ver model/kronos.py + model/runtime.py)
ahora corre en el microservicio FastAPI de services/predictor/, consumido acá
vía model/client.py::ChronosServiceClient -- este archivo no sabe (ni le
importa) que el motor es Chronos-Bolt y no Kronos; solo le pide
predict_samples() con un DataFrame OHLCV y recibe de vuelta el mismo shape
de siempre (mean_df, sample_dfs).
"""
import bisect
import json

import pandas as pd

from model.client import ChronosServiceClient
from portfolio import db, fetchers, indicators, signals

OHLCV_COLS = ["open", "high", "low", "close", "volume", "amount"]

# Un solo cliente reusado entre corridas (igual que antes se reusaba la
# instancia de modelo ya cargada vía model_runtime.get_predictor()) -- no
# abre conexión hasta el primer request, así que importar este módulo nunca
# falla aunque el microservicio todavía no esté arriba.
_predictor_client = ChronosServiceClient()

# accion/cedear cotizan en ARS en data912. Convertir a USD via CCL historico
# no elimina por si solo el sesgo de normalizacion por ventana de Kronos (se
# probo empiricamente contra el precio USD real de 8 instrumentos: el gap es
# practicamente identico en ambas monedas -- es una propiedad de la
# normalizacion frente a CUALQUIER serie con tendencia, no un artefacto de
# moneda), pero aisla el componente de tendencia que es 100% estructural y
# garantizado en ARS (inflacion/devaluacion) del que es propio del activo, asi
# que se mantiene junto con la correccion de continuidad (_apply_continuity_
# correction), que es la que realmente ataca el salto del primer paso. bono/on
# no tienen contrapartida USD clara via CCL (son renta fija local), quedan ARS.
USD_MODEL_ASSET_TYPES = ("accion", "cedear")
PRICE_COLS = ("open", "high", "low", "close")


def build_lookback_df(conn, *, instrument_id, symbol, market, asset_type, lookback, as_of_date=None):
    """Holdings reusan price_history (completando con backfill si falta);
    el Explorador pide historico fresco directo, sin necesitar alta en instruments.

    as_of_date acota el lookback a velas <= esa fecha -- es lo que hace que un
    backtest sea honesto (el modelo nunca ve datos que todavia no existian en
    ese momento del pasado). Hasta la corrida que agrego portfolio/
    calibrate_batch.py con --scope byma (ver conversacion), la rama SIN
    instrument_id (Explorador) ignoraba as_of_date por completo -- nunca
    importaba porque antes solo se usaba para el pronostico de HOY (siempre
    as_of_date=None); un backtest retroactivo sobre un simbolo fuera de
    catalogo con esa rama le mostraba al modelo datos del futuro sin que
    nada lo avisara. Ahora filtra exactamente igual que el camino de
    catalogo."""
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
        # Monto operado real (no un cero fantasma), misma aproximacion que
        # fetchers.fetch_data912_historical para cuando esta columna se arma
        # directo del Explorador.
        df["amount"] = df["volume"].fillna(0) * df[["open", "high", "low", "close"]].mean(axis=1)
    else:
        df = fetchers.fetch_data912_historical(symbol, asset_type)
        if as_of_date:
            df = df[df["timestamps"] <= pd.Timestamp(as_of_date)]

    df = df.sort_values("timestamps").tail(lookback).reset_index(drop=True)
    return df[["timestamps"] + OHLCV_COLS]


def build_chart_payload(conn, *, instrument_id, symbol, market, asset_type, lookback):
    """La mitad "rapida" de run_forecast_job: historico + indicadores, sin tocar
    el modelo. Esto es lo que deja ver el grafico y las medias/RSI/MACD al
    instante, sin esperar una corrida de Kronos (que es lo lento)."""
    lookback_df = build_lookback_df(
        conn, instrument_id=instrument_id, symbol=symbol, market=market,
        asset_type=asset_type, lookback=lookback, as_of_date=None,
    )
    return {
        "symbol": symbol,
        "historical": _candles_json(lookback_df, lookback_df["timestamps"]),
        "indicators": indicators.compute_all(lookback_df),
    }


def _ccl_rate_for_dates(conn, date_strs):
    """Serie de cotizacion CCL-venta alineada 1 a 1 con date_strs (forward-fill
    desde la ultima fecha conocida <= cada fecha -- mismo criterio ya usado en
    portfolio/history.py para precios). Devuelve None si falta cobertura para
    alguna fecha de la ventana (no se arriesga a mezclar ARS/USD en la misma
    serie que ve el modelo)."""
    fetchers.ensure_ccl_history(conn)
    rate_map = db.get_fx_rate_history(conn, fetchers.CCL_CASA)
    if not rate_map:
        return None

    known_dates = sorted(rate_map)
    rates = []
    for date_str in date_strs:
        i = bisect.bisect_right(known_dates, date_str) - 1
        if i < 0:
            return None
        rate = rate_map[known_dates[i]]
        if rate is None:
            return None
        rates.append(rate)
    return pd.Series(rates)


def build_model_input(conn, lookback_df, asset_type):
    """Convierte el lookback a USD (via CCL historico) antes de alimentarlo a
    Kronos, para accion/cedear -- la serie en ARS cruda arrastra la tendencia
    estructural que sesga la normalizacion por ventana del modelo. El grafico y
    los indicadores siguen mostrandose en ARS (lookback_df no se toca); esta
    conversion es solo para lo que *ve* el modelo.

    Devuelve (df_para_el_modelo, ultimo_ccl_conocido_o_None, "USD"|"ARS"). Si
    falta cobertura de CCL para alguna fecha de la ventana, degrada a ARS sin
    romper (igual que cualquier otra fuente de datos faltante en este proyecto)."""
    if asset_type not in USD_MODEL_ASSET_TYPES:
        return lookback_df, None, "ARS"

    date_strs = lookback_df["timestamps"].dt.strftime("%Y-%m-%d")
    rates = _ccl_rate_for_dates(conn, date_strs)
    if rates is None:
        return lookback_df, None, "ARS"

    df_usd = lookback_df.copy()
    for col in PRICE_COLS:
        df_usd[col] = df_usd[col] / rates.values
    return df_usd, float(rates.iloc[-1]), "USD"


def _apply_continuity_correction(pred_mean, sample_dfs, last_close):
    """Ancla cada trayectoria predicha (la media y cada muestra individual) para
    que su primer paso coincida exactamente con el ultimo cierre real conocido.

    Por que hace falta: el autoregresivo de Kronos no tiene ninguna restriccion
    que ate el primer paso generado al contexto real -- se desnormaliza con el
    x_mean/x_std de toda la ventana (ver model/kronos.py::_prepare_inputs), asi
    que en una ventana con tendencia el primer paso ya arranca lejos del ultimo
    cierre (confirmado con datos reales en tests/test_kronos_ars_bias.py, y
    reproducido en USD tambien -- no es un artefacto de moneda, es una
    propiedad de la normalizacion por ventana frente a CUALQUIER serie con
    tendencia). Sin esta correccion, la mayor parte de la "señal" termina
    siendo ese salto artificial del primer paso, no la forma que el modelo
    realmente predice a partir de ahi.

    Se corrige con un offset CONSTANTE por trayectoria (se preserva la
    forma/pendiente predicha, solo se desplaza el nivel), aplicado por igual a
    las 4 columnas de precio."""
    def _shift(df):
        offset = last_close - df["close"].iloc[0]
        df = df.copy()
        for col in PRICE_COLS:
            df[col] = df[col] + offset
        return df

    pred_mean = _shift(pred_mean)
    sample_dfs = [_shift(s) for s in sample_dfs] if sample_dfs else sample_dfs
    return pred_mean, sample_dfs


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
    model_input_df, last_ccl, model_currency = build_model_input(conn, lookback_df, row["asset_type"])
    x_df = model_input_df[OHLCV_COLS]
    x_timestamp = model_input_df["timestamps"]
    y_timestamp = _future_timestamps(x_timestamp, row["pred_len"])

    # Chronos-Bolt siempre devuelve la distribución completa por cuantiles en
    # una sola pasada (no hay un camino "más barato" sin muestras como con
    # Kronos, donde sample_count>1 implicaba correr el modelo N veces) -- ya
    # no hace falta la rama if/else que elegía entre predict()/predict_samples()
    # según row["sample_count"]. model_key/temperature/top_p/sample_count
    # quedan grabados en la fila de forecasts (compatibilidad de schema) pero
    # el cliente los ignora -- no tienen equivalente en una regresión por
    # cuantiles (ver model/client.py).
    pred_mean, sample_dfs = _predictor_client.predict_samples(
        df=x_df, x_timestamp=x_timestamp, y_timestamp=y_timestamp, pred_len=row["pred_len"],
    )

    last_close = float(x_df["close"].iloc[-1])  # en USD si model_currency == "USD"
    pred_mean, sample_dfs = _apply_continuity_correction(pred_mean, sample_dfs, last_close)
    signal, pred_change_pct, uncertainty_pct, distribution = signals.compute_signal(
        last_close, pred_mean, sample_dfs, config
    )
    explanation = signals.explain(
        row["symbol"], pred_change_pct, uncertainty_pct, signal, row["pred_len"], config,
        distribution=distribution, model_currency=model_currency,
    )

    # El grafico siempre se muestra en ARS (lo que el usuario conoce) -- si el
    # modelo corrio en USD, reconvertimos la prediccion con el ultimo CCL
    # conocido (simplificacion declarada: se mantiene fijo durante todo el
    # horizonte predicho, igual que ya acepta portfolio/history.py para el
    # historico reconstruido).
    pred_display = pred_mean.copy()
    if model_currency == "USD" and last_ccl:
        for col in PRICE_COLS:
            pred_display[col] = pred_display[col] * last_ccl

    result = {
        "symbol": row["symbol"],
        "historical": _candles_json(lookback_df, lookback_df["timestamps"]),
        "predicted": _candles_json(pred_display, pred_display.index),
        "indicators": indicators.compute_all(lookback_df),
        "signal": signal,
        "pred_change_pct": pred_change_pct,
        "uncertainty_pct": uncertainty_pct,
        "distribution": distribution,
        "model_currency": model_currency,
        "explanation": explanation,
    }

    db.mark_forecast_done(
        conn, forecast_id,
        result_json=json.dumps(result),
        signal=signal, pred_change_pct=pred_change_pct, uncertainty_pct=uncertainty_pct,
    )
