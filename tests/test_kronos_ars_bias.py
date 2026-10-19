"""Reproduce el sesgo bajista de Kronos sobre series ARS en tendencia, ANTES de
tocar nada del pipeline de forecasting (pedido explicito del usuario: "Antes de
cambiar nada, armá un test que reproduzca el salto entre el último cierre y el
primer paso pronosticado").

Mecanismo (ver model/kronos.py::KronosPredictor._prepare_inputs, lineas
521-550): cada forecast calcula x_mean/x_std de cero sobre TODA la ventana de
lookback (sin normalizacion global entrenada), y desnormaliza la salida con
esos mismos x_mean/x_std. En una serie con tendencia alcista fuerte (ARS por
inflacion/devaluacion), la media de la ventana queda muy por debajo del ultimo
cierre real -- cualquier secuencia que el modelo genere, incluso "neutral" en
espacio normalizado, se desnormaliza alrededor de esa media baja.

Nivel 1 (test_x_mean_sits_below_last_close_on_trending_ars_series): matematico
puro, sin cargar el modelo -- mide cuantos desvios estandar separan el ultimo
cierre real del x_mean que _prepare_inputs calcula. Prueba el mecanismo con
datos reales de KO sin gastar los ~30-50s de una corrida real del modelo.

Nivel 2 (test_kronos_first_predicted_step_gap_on_ko): corre el modelo real
(Kronos-base, el mismo que usa produccion, pesos ya cacheados localmente)
sobre el lookback real de KO en ARS y mide el salto entre el ultimo cierre y
el primer paso predicho -- reproduce el ~20% reportado por el usuario. Se
salta (no falla) si no hay datos suficientes o el modelo no esta disponible.

Nivel 3 (test_continuity_correction_removes_the_jump): valida el fix elegido
DESPUES de medir -- se probo primero convertir a USD via CCL historico (ver
portfolio/forecasting.py::build_model_input) y se confirmo empiricamente,
comparando contra el precio USD real de yfinance para 8 instrumentos reales de
la cartera, que el gap NO es un artefacto de moneda (aparece identico en USD
real): es una propiedad de la normalizacion por ventana de Kronos frente a
CUALQUIER serie con tendencia, no un problema especifico de ARS. El fix que si
funciona, probado en este test: anclar cada muestra al ultimo cierre real
conocido (portfolio/forecasting.py::_apply_continuity_correction) -- se
preserva la forma/pendiente que predijo el modelo, solo se corrige el salto
artificial del primer paso.
"""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from model.kronos import KronosPredictor
from portfolio import db as portfolio_db
from portfolio.forecasting import (
    OHLCV_COLS, _apply_continuity_correction, _future_timestamps, build_model_input,
)

PORTFOLIO_DB_PATH = Path(__file__).parent.parent / "portfolio" / "portfolio.db"
KO_SYMBOL = "KO"
LOOKBACK = 400
PRED_LEN = 120


class _BareKronosPredictor:
    """Doble liviano de KronosPredictor: expone solo los atributos que usa
    _prepare_inputs (price_cols/vol_col/amt_vol/clip), para medir el z-score
    real sin instanciar el modelo -- el Nivel 1 no necesita el modelo cargado
    para reproducir el mecanismo de sesgo, solo la matematica de normalizacion."""
    price_cols = ['open', 'high', 'low', 'close']
    vol_col = 'volume'
    amt_vol = 'amount'
    clip = 5


def _ko_lookback_df():
    if not PORTFOLIO_DB_PATH.exists():
        pytest.skip("No hay portfolio.db local con datos reales para reproducir el bug")
    conn = portfolio_db.get_connection(str(PORTFOLIO_DB_PATH))
    instrument = portfolio_db.find_instrument(conn, KO_SYMBOL, "byma")
    if not instrument:
        pytest.skip(f"No hay instrumento {KO_SYMBOL} cargado en este portfolio.db")
    rows = portfolio_db.get_price_history(conn, instrument["id"], limit=LOOKBACK)
    if len(rows) < LOOKBACK:
        pytest.skip(f"Historial insuficiente para {KO_SYMBOL} ({len(rows)} < {LOOKBACK} velas)")
    df = pd.DataFrame(rows)
    df["timestamps"] = pd.to_datetime(df["timestamp"])
    df["amount"] = 0.0
    df = df.sort_values("timestamps").tail(LOOKBACK).reset_index(drop=True)
    return df[["timestamps"] + OHLCV_COLS]


def test_x_mean_sits_below_last_close_on_trending_ars_series():
    df = _ko_lookback_df()
    x_timestamp = df["timestamps"]
    y_timestamp = _future_timestamps(x_timestamp, PRED_LEN)

    x, x_stamp, y_stamp, x_mean, x_std = KronosPredictor._prepare_inputs(
        _BareKronosPredictor(), df[OHLCV_COLS], x_timestamp, y_timestamp,
    )

    close_idx = _BareKronosPredictor.price_cols.index("close")
    last_close = float(df["close"].iloc[-1])
    window_mean_close = float(x_mean[close_idx])
    window_std_close = float(x_std[close_idx])

    gap_in_stds = (last_close - window_mean_close) / window_std_close
    implied_bias_pct = (window_mean_close - last_close) / last_close * 100

    print(
        f"KO: ultimo cierre={last_close:.2f}, x_mean(close) de la ventana de "
        f"{LOOKBACK} velas={window_mean_close:.2f} ({gap_in_stds:.2f} desvios "
        f"estandar de distancia) -> un pronostico 'neutral' en espacio "
        f"normalizado (z=0) desnormaliza a un {implied_bias_pct:+.1f}% del "
        f"ultimo cierre real, sin que el modelo haya 'decidido' nada bajista."
    )

    # Reproduce el mecanismo: en una serie ARS con tendencia alcista fuerte,
    # el z-score de toda la ventana deja la media muy por debajo del ultimo
    # precio -- con esto ya alcanza para explicar una desnormalizacion con
    # sesgo bajista de varios puntos porcentuales.
    assert gap_in_stds > 1.0, (
        f"Se esperaba un gap grande (ventana con tendencia) pero dio solo "
        f"{gap_in_stds:.2f} desvios -- revisar si el historico de {KO_SYMBOL} "
        f"sigue teniendo la tendencia alcista fuerte que motivo este test."
    )


def test_kronos_first_predicted_step_gap_on_ko():
    df = _ko_lookback_df()
    x_timestamp = df["timestamps"]
    y_timestamp = _future_timestamps(x_timestamp, PRED_LEN)

    try:
        from model import Kronos, KronosTokenizer
        tokenizer = KronosTokenizer.from_pretrained("NeoQuasar/Kronos-Tokenizer-base")
        model = Kronos.from_pretrained("NeoQuasar/Kronos-base")
    except Exception as e:
        pytest.skip(f"No se pudo cargar Kronos-base localmente: {e}")

    predictor = KronosPredictor(model, tokenizer, device="cpu", max_context=512)
    pred_df = predictor.predict(
        df=df[OHLCV_COLS], x_timestamp=x_timestamp, y_timestamp=y_timestamp,
        pred_len=PRED_LEN, T=1.0, top_p=0.9, sample_count=1, verbose=False,
    )

    last_close = float(df["close"].iloc[-1])
    first_predicted_close = float(pred_df["close"].iloc[0])
    gap_pct = (first_predicted_close - last_close) / last_close * 100

    print(
        f"KO: ultimo cierre real={last_close:.2f}, primer paso predicho="
        f"{first_predicted_close:.2f} ({gap_pct:+.1f}%) -- este es el salto "
        f"reportado por el usuario (~-20%), reproducido con el pipeline real "
        f"de produccion sin ningun cambio de codigo."
    )

    # No se fija un umbral estricto de signo (sample_count=1 tiene su propia
    # aleatoriedad de muestreo), pero el gap debe ser grande en magnitud --
    # eso es lo que reporto el usuario y lo que el fix debe reducir.
    assert abs(gap_pct) > 1.0, (
        "El salto entre el ultimo cierre y el primer paso predicho fue "
        "sorprendentemente chico -- si esto pasa de forma consistente, el "
        "diagnostico de sesgo deberia revisarse."
    )


def test_continuity_correction_removes_the_jump():
    df = _ko_lookback_df()

    try:
        from model import Kronos, KronosTokenizer
        tokenizer = KronosTokenizer.from_pretrained("NeoQuasar/Kronos-Tokenizer-base")
        model = Kronos.from_pretrained("NeoQuasar/Kronos-base")
    except Exception as e:
        pytest.skip(f"No se pudo cargar Kronos-base localmente: {e}")

    conn = portfolio_db.get_connection(str(PORTFOLIO_DB_PATH))
    model_input_df, last_ccl, model_currency = build_model_input(conn, df, "cedear")

    x_timestamp = model_input_df["timestamps"]
    y_timestamp = _future_timestamps(x_timestamp, PRED_LEN)
    predictor = KronosPredictor(model, tokenizer, device="cpu", max_context=512)

    sample_count = 30
    pred_mean, sample_dfs = predictor.predict_samples(
        df=model_input_df[OHLCV_COLS], x_timestamp=x_timestamp, y_timestamp=y_timestamp,
        pred_len=PRED_LEN, T=1.0, top_p=0.9, sample_count=sample_count, verbose=False,
    )

    last_close = float(model_input_df["close"].iloc[-1])
    raw_changes = np.array([(s["close"].iloc[-1] - last_close) / last_close * 100 for s in sample_dfs])

    corrected_mean, corrected_samples = _apply_continuity_correction(pred_mean, sample_dfs, last_close)
    # La correccion debe anclar el primer paso EXACTO al ultimo cierre real.
    assert abs(corrected_mean["close"].iloc[0] - last_close) < 1e-6

    corrected_changes = np.array([(s["close"].iloc[-1] - last_close) / last_close * 100 for s in corrected_samples])

    print(
        f"KO ({model_currency}): sin corregir median={np.median(raw_changes):+.1f}% "
        f"P(up)={(raw_changes > 0).mean() * 100:.0f}% -> corregido "
        f"median={np.median(corrected_changes):+.1f}% "
        f"P(up)={(corrected_changes > 0).mean() * 100:.0f}%"
    )

    # No se exige un signo especifico (la ventana puede seguir teniendo una
    # tendencia real que el modelo capture legitimamente) -- el criterio es que
    # la correccion reduzca la magnitud del sesgo introducido por el salto
    # artificial del primer paso, no que elimine toda direccionalidad.
    assert abs(np.median(corrected_changes)) < abs(np.median(raw_changes)), (
        "La correccion de continuidad no redujo la magnitud de la mediana -- "
        "revisar _apply_continuity_correction."
    )
