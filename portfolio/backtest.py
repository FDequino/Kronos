"""Arnes de Walk-Forward Cross-Validation para el ensemble Chronos-Bolt +
meta-modelo (ver portfolio/features.py y services/predictor/meta_learner.py).

Historia (ver conversacion): Fase 1 entrenaba un RandomForest POR TICKER con
16 features -- overfitting confirmado (53.2% Chronos solo vs 47.5% ensemble).
Fase 2 pooleo los 12 activos en un UNICO dataset (Cross-Sectional Pooling,
ver meta_learner.pool_anchor_frames) con solo 4 features -- el ensemble dejo
de empeorar (50.1% -> 50.6%, marginal). Fase 3 sumo microestructura
adicional + 3 canales macro exogenos (ver portfolio/features.py) y entreno
DOS modelos globales por fold sobre el MISMO split -- V2 (sin macro) y V3
(con macro) -- para medir el aporte marginal real del contexto macro, no
solo reemplazarlo a ciegas. Fase 4 (este archivo, actual -- "evolucion
integral del motor cuantitativo", ver conversacion) suma RSI/MACD/ATR%% +
FFD (Diferenciacion Fraccionaria con Ventana Fija, preserva memoria
historica manteniendo estacionariedad, ver portfolio/features.py::fracdiff_ffd)
como V4 = V3 + tecnicos, y entrena TRES modelos globales por fold sobre el
MISMO split -- V2/V3/V4 -- mismo criterio de comparacion limpia que ya
existia entre V2 y V3. Fase 5 (este archivo, actual -- misma "evolucion
integral") reemplaza el label de horizonte fijo (y = 1 si el retorno a
HORIZON_DAYS supera el hurdle) por Etiquetado de Triple Barrera +
Meta-Labeling (Lopez de Prado, ver _triple_barrier_label/summarize): y* = 1
solo si, operando en la direccion de chronos_pred (el modelo PRIMARIO), el
precio toca la Barrera Superior (pt*sigma_t, sigma_t = vol EWMA 20d) antes
que la Inferior o la Vertical (expiracion a HORIZON_DAYS) -- el meta-modelo
ya no predice una direccion propia, CONFIRMA o RECHAZA la de Chronos, con un
solo piso de confianza (META_LABEL_CONFIDENCE_THRESHOLD=0.50) en vez de la
banda simetrica [EDGE_LOWER, EDGE_UPPER] que tenia sentido con el label
anterior. Fase 6 (este archivo, actual -- misma "evolucion integral", item 5)
suma CPCV (Validacion Cruzada Purgada y Combinatoria, ver
cpcv_evaluate_pooled/_purge_and_embargo_train_indices) como validacion
ANTI-OVERFITTING adicional al walk-forward expansivo de arriba (que sigue
existiendo, sin cambios, para la tabla comparativa principal): evalua TODAS
las combinaciones C(N_CPCV_GROUPS, N_CPCV_TEST_GROUPS) de bloques de test,
purgando/embargando filas de train que podrian filtrar informacion del
periodo de test, y reporta el Deflated Sharpe Ratio (Bailey & Lopez de
Prado, ver deflated_sharpe_ratio) sobre esa distribucion de Sharpe -- la
probabilidad de que el Sharpe final sea real y no un artefacto de haber
mirado muchas combinaciones antes de reportarlo.

Garantia de cero look-ahead (Data Leakage), la parte mas importante de este
archivo:
  1. Las features (portfolio/features.py, incluidas las macro) son 100%
     rolling hacia atras -- se calculan UNA vez sobre la serie completa por
     eficiencia, pero cada fila i solo depende de datos con indice <= i,
     asi que leer la fila de un anchor nunca expone nada posterior a el.
  2. Chronos-Bolt corre en CADA anchor con una ventana de lookback que
     termina justo en ese anchor (nunca ve mas alla).
  3. Los meta-modelos GLOBALES (V2, V3 y V4) se re-entrenan en cada fold SOLO
     con anchors -- de CUALQUIER ticker del pool -- cuya fecha es
     estrictamente anterior al bloque de test de ese fold (walk-forward
     expansivo sobre el pool ordenado por fecha, nunca por ticker). Nunca se
     evalua sobre anchors usados para entrenar ni se entrena con datos del
     futuro del fold que se esta evaluando, de NINGUN ticker.
  4. El Triple Barrier camina HACIA ADELANTE desde el anchor (anchor+1 ..
     anchor+HORIZON_DAYS) para determinar el label -- esto es leer un
     DESENLACE que ya ocurrio (dato historico real), no look-ahead: look-ahead
     seria que el MODELO/meta-modelo viera ese desenlace como FEATURE de
     entrada, y no lo hace (ver build_anchor_dataset, el label se calcula
     aparte de feature_matrix).

Corre standalone: `python3 -m portfolio.backtest` desde la raiz del repo
(con el predictor de services/predictor/ levantado en :8001 o
PREDICTOR_SERVICE_URL apuntando donde corresponda).
"""
import itertools
import sys
import time
from datetime import datetime

import numpy as np
import pandas as pd
from scipy import stats as scipy_stats

from model.client import ChronosServiceClient
from portfolio import config as capital_config
from portfolio import db, features, fetchers, indicators, macro_data, regime, signals, short_term_engine, yield_engine
from portfolio.forecasting import USD_MODEL_ASSET_TYPES, build_model_input, _apply_continuity_correction
from services.predictor.meta_learner import MetaLearner, pool_anchor_frames

# Universo pedido: BYMA acciones, CEDEARs/globales, renta fija (AL30D, bono
# ley NY liquidacion USD -- se trata como 'bono' igual que el resto de renta
# fija de este proyecto, ver fetchers.DATA912_HIST_ENDPOINT_BY_TYPE).
# Refactorizacion financiera en USD CCL (ver conversacion): CEDEARs de alta
# liquidez priorizados (SPY/QQQ/NVDA/TSLA/AAPL/MSFT, agregado aca) + acciones
# locales (ya ajustadas a USD CCL en todo el pipeline, ver
# portfolio/features.py::build_full_feature_matrix y
# backtest.py::_prepare_aligned_series) + AL30D (renta fija, YA nativo en
# USD, no necesita conversion).
UNIVERSE = [
    ("GGAL", "accion"), ("YPFD", "accion"), ("PAMP", "accion"),
    ("BMA", "accion"), ("ALUA", "accion"), ("CRES", "accion"),
    ("SPY", "cedear"), ("QQQ", "cedear"), ("AAPL", "cedear"),
    ("NVDA", "cedear"), ("TSLA", "cedear"), ("MSFT", "cedear"),
    # Ampliacion del universo (ver conversacion) -- confirmado disponible en
    # data912 (HTTP 200 en /historical/cedears/<symbol>) antes de sumarlos.
    ("AMZN", "cedear"), ("GOOGL", "cedear"), ("META", "cedear"), ("MELI", "cedear"),
    ("AL30D", "bono"),
]

HORIZON_DAYS = 15  # "N pasos" / "15 ruedas" pedido -- ahora doble uso: Barrera Vertical del Triple Barrier (ver mas abajo)
LOOKBACK = 100  # contexto que ve Chronos-Bolt en cada anchor
ANCHOR_SPACING_DAYS = 10  # cada cuantas ruedas se arma un anchor nuevo -- acota el costo total (1 corrida de Chronos por anchor)
WARMUP_ROWS = 60  # minimo de historia antes del primer anchor (para que EMA50 ya tenga señal real, no transitoria)
N_FOLDS = 5
MIN_TRAIN_ANCHORS = 30  # minimo POR TICKER para que build_anchor_dataset no descarte el ticker entero
MIN_TRAIN_ANCHORS_POOLED = 500  # minimo del POOL global antes de evaluar el primer fold (ver walk_forward_evaluate_pooled)

# Triple Barrier + Meta-Labeling (Lopez de Prado, ver conversacion,
# "evolucion integral" pedida -- reemplaza el label de horizonte fijo que
# usaba este archivo hasta Fase 4): y* = 1 UNICAMENTE si, operando en la
# DIRECCION de la señal del modelo PRIMARIO (Chronos, chronos_pred), el
# precio toca la Barrera Superior (pt*sigma_t) ANTES que la Inferior
# (sl*sigma_t) y ANTES de la Barrera Vertical (HORIZON_DAYS ruedas sin tocar
# ninguna) -- pedido explicito: "1 si alcanza la barrera superior, 0 en caso
# contrario" (barrera inferior y vertical sin tocar son AMBAS 0, no un
# esquema de 3 clases). sigma_t: volatilidad diaria EWMA(20d) del retorno
# log, resuelta EN el anchor (nunca con datos futuros).
EWMA_VOL_WINDOW = 20  # "volatilidad diaria (EWMA 20d)" pedido explicitamente
# pt/sl: la FORMULA (pt*sigma_t, sl*sigma_t) fue pedida explicitamente, el
# VALOR del multiplicador no -- 2.0 simetrico (equivalente a un TP/SL a ~2
# desvios diarios de distancia) declarado como default razonable, mismo
# criterio de "umbral default de referencia, ajustable" que portfolio/
# regime.py::VIX_HIGH_THRESHOLD. Verificado empiricamente (ver conversacion)
# que con este valor la mayoria de los anchors SI logran tocar alguna
# barrera antes de la vertical (no degenera en "todo vertical").
TRIPLE_BARRIER_PT_MULT = 2.0
TRIPLE_BARRIER_SL_MULT = 2.0
# Filtro de confianza de Meta-Labeling (pedido explicito: "si p < 0.50,
# degrada la señal a 'mantener'") -- UN solo umbral, no la banda simetrica
# [EDGE_LOWER, EDGE_UPPER] que usaba el label anterior: con Meta-Labeling el
# modelo secundario ya NO elige una direccion propia (eso lo decide Chronos,
# el primario) -- solo confirma o rechaza la direccion que Chronos ya
# propuso, asi que no hace falta una zona muerta simetrica alrededor de 0.5
# para "evitar apostar a ciegas en cualquier direccion": alcanza con un
# piso unico de confianza para confirmar esa UNA direccion propuesta.
META_LABEL_CONFIDENCE_THRESHOLD = 0.50


def fetch_ticker_history(symbol, asset_type, conn=None):
    """Bug real descubierto en la refactorizacion financiera a USD CCL (ver
    conversacion): varias acciones locales (GGAL, YPFD, BMA, CRES...) tienen
    historial de precio desde 2001, pero el CCL (fx_rate_history) recien
    arranca en 2013 -- build_model_input es todo-o-nada (ver
    forecasting.py::_ccl_rate_for_dates), asi que con la serie completa
    degradaba SILENCIOSAMENTE a ARS para el ticker ENTERO por faltarle CCL a
    un puñado de filas viejas (confirmado: GGAL/YPFD/PAMP/BMA/ALUA/CRES/AAPL
    nunca se convertian a USD pese a asset_type='accion'/'cedear').

    Con conn (asi lo llaman backtest.py y live_signals.py, siempre con conn
    a mano), accion/cedear se recortan a partir de la primera fecha con
    cobertura CCL real -- nunca se inventa un CCL anterior a 2013, se
    descarta la historia que no se puede convertir de forma honesta. bono/on
    y moneda/fondo no se tocan (no pasan por CCL, ver USD_MODEL_ASSET_TYPES)."""
    df = fetchers.fetch_data912_historical(symbol, asset_type)
    df = df.sort_values("timestamps").reset_index(drop=True)
    if conn is not None and asset_type in USD_MODEL_ASSET_TYPES:
        fetchers.ensure_ccl_history(conn)  # asegura que la tabla este poblada antes de recortar por ella
        min_ccl_date = db.get_fx_rate_earliest_fetched_date(conn, fetchers.CCL_CASA)
        if min_ccl_date:
            df = df[df["timestamps"].dt.strftime("%Y-%m-%d") >= min_ccl_date].reset_index(drop=True)
    return df


def compute_hurdle_pct(conn, horizon_days):
    """Tasa libre de riesgo / nivel actual (ver portfolio/signal_config).
    signal_config.caucion_reference_tna es la misma TNA manual que ya usa
    patrimonio.js para comparar la cartera contra cauciones -- no existe una
    fuente publica gratuita de esta tasa (ver glosario de la app), asi que si
    nunca se configuro el hurdle es simplemente "superar el precio actual"
    (0%), NUNCA un numero inventado."""
    config = db.get_signal_config(conn)
    tna = config.get("caucion_reference_tna")
    if not tna:
        return 0.0
    return float(tna) / 100 * (horizon_days / 365) * 100


def _chronos_pct_changes(client, close_window, horizon_days):
    """Corre Chronos-Bolt sobre una ventana de cierres que YA esta en la
    misma unidad/moneda que se va a usar para medir el desenlace real (ver
    build_anchor_dataset, que llama a build_model_input UNA vez por ticker
    antes de recorrer anchors) -- devuelve el % de cambio proyectado desde el
    ultimo cierre de la ventana hasta el paso final del horizonte, para
    p10/p50/p90. Reusa _apply_continuity_correction de portfolio/forecasting.py
    (no se reimplementa: es la misma correccion de nivel que ve el usuario
    en la app real, evaluar sin ella seria medir un modelo distinto al que
    corre en produccion)."""
    last_close = float(close_window.iloc[-1])
    x_df = pd.DataFrame({"close": close_window.to_numpy()})
    # x_timestamp no lo usa ChronosServiceClient.predict_samples (ver
    # model/client.py) -- y_timestamp solo necesita longitud/orden, no
    # fechas reales, para indexar el resultado.
    y_index = pd.RangeIndex(horizon_days)
    pred_mean, sample_dfs = client.predict_samples(
        df=x_df, x_timestamp=None, y_timestamp=y_index, pred_len=horizon_days,
    )
    _pred_mean, sample_dfs = _apply_continuity_correction(pred_mean, sample_dfs, last_close)

    n = len(sample_dfs)

    def pct(i):
        final_close = sample_dfs[i]["close"].iloc[-1]
        return (final_close - last_close) / last_close * 100 if last_close else 0.0

    return pct(0), pct(n // 2), pct(n - 1)


def _daily_vol_ewma_pct(close, window=EWMA_VOL_WINDOW):
    """Volatilidad diaria EWMA (ver conversacion, Triple Barrier) -- desvio
    estandar EXPONENCIALMENTE ponderado del retorno log diario, en %. Cero
    look-ahead: ewm() es intrinsecamente causal (cada fila solo pesa
    observaciones <= esa fila), mismo criterio del resto del proyecto.
    min_periods=window: NaN hasta tener la ventana completa, nunca un sigma
    calculado con pocos puntos (degradaria la barrera a un ancho poco
    confiable) -- ver _triple_barrier_label para el fallback cuando da NaN."""
    log_return = np.log(close / close.shift(1))
    return log_return.ewm(span=window, min_periods=window).std() * 100


def _triple_barrier_label(close, anchor, direction, sigma_pct, max_hold,
                           pt_mult=TRIPLE_BARRIER_PT_MULT, sl_mult=TRIPLE_BARRIER_SL_MULT):
    """Etiquetado de Triple Barrera (Lopez de Prado, ver conversacion) sobre
    la DIRECCION de la señal del modelo primario (direction=+1 si Chronos
    predice suba, -1 si predice baja -- ver build_anchor_dataset): camina
    dia a dia desde anchor+1 hasta anchor+max_hold evaluando el retorno
    acumulado EN esa direccion -- la primera barrera que se toca (Superior
    pt*sigma_t, Inferior sl*sigma_t, o Vertical = expiracion sin tocar
    ninguna) determina la salida.

    y*=1 UNICAMENTE si se toca la barrera SUPERIOR (la señal primaria "dio en
    el blanco") -- Inferior y Vertical son AMBAS y*=0 (pedido explicito: "1
    si alcanza la barrera superior, 0 en caso contrario", no un esquema de 3
    clases). Devuelve (y, actual_return_pct, hold_days, exit_reason) --
    actual_return_pct es el retorno CRUDO del activo (sin multiplicar por
    direction: asi summarize() sigue pudiendo aplicar su propio signo segun
    la direccion que de verdad se evalue, sin que este helper le imponga una).

    sigma_pct ya viene resuelto al valor conocido EN el anchor (nunca se
    recalcula con datos futuros, ver _daily_vol_ewma_pct). Si es NaN/<=0 (la
    ventana EWMA todavia no tiene suficiente historia) se degrada al
    horizonte fijo anterior (compara el retorno a max_hold ruedas contra 0%,
    declarado explicitamente en exit_reason, no se inventa un sigma)."""
    entry_close = float(close.iloc[anchor])

    def _raw_return_pct(idx):
        px = float(close.iloc[idx])
        return (px - entry_close) / entry_close * 100 if entry_close else 0.0

    if not entry_close or sigma_pct is None or not np.isfinite(sigma_pct) or sigma_pct <= 0:
        raw_return_pct = _raw_return_pct(anchor + max_hold)
        y = 1 if (direction * raw_return_pct) > 0 else 0
        return y, raw_return_pct, max_hold, "vertical_sin_sigma"

    upper_pct = pt_mult * sigma_pct
    lower_pct = -sl_mult * sigma_pct
    for h in range(1, max_hold + 1):
        raw_return_pct = _raw_return_pct(anchor + h)
        signed_return_pct = direction * raw_return_pct
        if signed_return_pct >= upper_pct:
            return 1, raw_return_pct, h, "upper"
        if signed_return_pct <= lower_pct:
            return 0, raw_return_pct, h, "lower"

    raw_return_pct = _raw_return_pct(anchor + max_hold)
    return 0, raw_return_pct, max_hold, "vertical"


def build_anchor_dataset(symbol, asset_type, client, conn):
    """Devuelve (DataFrame de anchors, None) o (None, motivo) si el ticker no
    tiene suficiente historial o muy pocos anchors resolvieron contra
    Chronos-Bolt. Cada fila: las features de portfolio/features.py (V4, el
    set completo -- V2/V3/V4 se eligen despues, al entrenar, ver
    meta_learner.MetaLearner(feature_columns=...)) + date
    (fecha real del anchor, clave para el pooling por orden temporal, ver
    meta_learner.pool_anchor_frames) + y (label de Meta-Labeling via Triple
    Barrier, ver _triple_barrier_label) + chronos_pred (señal cruda del p50
    solo, sin meta-modelo -- tambien define la DIRECCION que evalua el
    Triple Barrier) + actual_return_pct (retorno crudo del activo hasta la
    barrera que se toco, para profit factor/sharpe aguas abajo) +
    hold_days/exit_reason (diagnostico de que barrera se toco y cuando).
    'symbol' se agrega en build_pooled_dataset, no aca (esta funcion no sabe
    que se va a poolear con otros tickers)."""
    raw_df = fetch_ticker_history(symbol, asset_type, conn=conn)
    if len(raw_df) < WARMUP_ROWS + LOOKBACK + HORIZON_DAYS + ANCHOR_SPACING_DAYS * MIN_TRAIN_ANCHORS // 4:
        return None, f"historial insuficiente ({len(raw_df)} ruedas)"

    # USD-convertido para accion/cedear (mismo criterio que forecasting.py en
    # produccion) -- se llama UNA vez sobre la serie completa, no por anchor;
    # mirar el CCL de una fecha ya pasada para medir un desenlace que YA
    # ocurrio no es look-ahead (es leer un dato historico real), look-ahead
    # seria que el MODELO/meta-modelo lo usara como feature de entrada, y no
    # lo hace.
    raw_df = raw_df.reset_index(drop=True)
    model_df, _last_ccl, _currency = build_model_input(conn, raw_df, asset_type)
    close = model_df["close"].reset_index(drop=True)
    dates = raw_df["timestamps"].reset_index(drop=True)
    # microestructura (4) + tecnicos (4) + macro exogeno (3), ver
    # portfolio/features.py -- las 2 columnas de Chronos se agregan mas
    # abajo, por anchor.
    feature_matrix = features.build_full_feature_matrix(conn, raw_df, asset_type, symbol=symbol)
    hurdle_pct = compute_hurdle_pct(conn, HORIZON_DAYS)
    # sigma_t para el Triple Barrier (ver _triple_barrier_label) -- se
    # calcula UNA vez vectorizado sobre la serie completa (mismo patron del
    # resto del proyecto), cada anchor lee su propio valor YA resuelto.
    vol_series = _daily_vol_ewma_pct(close)

    first_anchor = max(WARMUP_ROWS, LOOKBACK - 1)
    last_anchor = len(close) - HORIZON_DAYS - 1
    anchors = list(range(first_anchor, last_anchor, ANCHOR_SPACING_DAYS))
    if len(anchors) < MIN_TRAIN_ANCHORS + N_FOLDS:
        return None, f"muy pocos anchors posibles ({len(anchors)}) para {N_FOLDS} folds"

    rows = []
    for anchor in anchors:
        window = close.iloc[anchor - LOOKBACK + 1: anchor + 1]
        try:
            p10_pct, p50_pct, p90_pct = _chronos_pct_changes(client, window, HORIZON_DAYS)
        except Exception:
            continue  # anchor saltado (microservicio caido, ventana degenerada, etc.) -- no rompe el resto
        last_close = float(close.iloc[anchor])
        if not last_close:
            continue
        # chronos_pred define la DIRECCION que el Triple Barrier evalua (la
        # señal del modelo PRIMARIO, ver conversacion Meta-Labeling) -- 1
        # (suba) o -1 (baja), nunca 0, para que _triple_barrier_label siempre
        # tenga una direccion concreta que confirmar o rechazar.
        chronos_pred = 1 if p50_pct > hurdle_pct else 0
        direction = 1 if chronos_pred == 1 else -1
        sigma_pct = float(vol_series.iloc[anchor]) if pd.notna(vol_series.iloc[anchor]) else None
        y, actual_return_pct, hold_days, exit_reason = _triple_barrier_label(
            close, anchor, direction, sigma_pct, HORIZON_DAYS,
        )

        row = {**feature_matrix.iloc[anchor].to_dict(), **features.chronos_feature_row(p10_pct, p50_pct, p90_pct)}
        row.update({
            "date": dates.iloc[anchor], "anchor": anchor, "y": y,
            "chronos_pred": chronos_pred, "actual_return_pct": actual_return_pct,
            "hold_days": hold_days, "exit_reason": exit_reason,
        })
        rows.append(row)

    if len(rows) < MIN_TRAIN_ANCHORS + N_FOLDS:
        return None, f"muy pocos anchors resueltos contra Chronos-Bolt ({len(rows)}/{len(anchors)})"

    return pd.DataFrame(rows).reset_index(drop=True), None


def build_pooled_dataset(universe, client, conn):
    """Arma el anchor dataset de cada ticker (build_anchor_dataset) y los
    poolea en uno solo ordenado por fecha (meta_learner.pool_anchor_frames)
    -- devuelve (pooled_df, {symbol: motivo de los que se saltearon})."""
    frames = []
    skipped = {}
    timings = {}
    for symbol, asset_type in universe:
        t0 = time.time()
        anchor_df, error = build_anchor_dataset(symbol, asset_type, client, conn)
        timings[symbol] = time.time() - t0
        if error:
            skipped[symbol] = error
            continue
        anchor_df = anchor_df.copy()
        anchor_df["symbol"] = symbol
        frames.append(anchor_df)
    pooled = pool_anchor_frames(frames) if frames else None
    return pooled, skipped, timings


def walk_forward_evaluate_pooled(pooled_df, n_folds=N_FOLDS, min_train=MIN_TRAIN_ANCHORS_POOLED):
    """Walk-forward EXPANSIVO sobre el pool GLOBAL (ordenado por fecha, ver
    build_pooled_dataset): el fold k entrena con pooled_df[:train_end] (de
    CUALQUIER ticker, mientras su fecha sea anterior al bloque de test) y
    evalua pooled_df[train_end:test_end] -- train_end siempre creciendo,
    nunca se entrena con una fecha posterior al bloque de test de ese fold,
    sin importar de que ticker sea.

    En CADA fold se entrenan TRES modelos sobre el MISMO split (comparacion
    limpia, ver portfolio/features.py): V2 (sin macro), V3 (con macro) y V4
    (V3 + tecnicos RSI/MACD/ATR%%/FFD) -- devuelve (resultados concatenados
    con columnas ensemble_proba_v2/v3/v4, lista de feature_importances() de
    cada fold V4 -- el modelo mas completo -- para promediar)."""
    n = len(pooled_df)
    if n <= min_train:
        return None, []
    remaining = n - min_train
    fold_size = max(1, remaining // n_folds)
    out = []
    v4_importances = []
    for k in range(n_folds):
        train_end = min_train + k * fold_size
        test_end = n if k == n_folds - 1 else min(n, min_train + (k + 1) * fold_size)
        if train_end >= test_end:
            continue
        train_df = pooled_df.iloc[:train_end]
        test_df = pooled_df.iloc[train_end:test_end]
        if train_df.empty or test_df.empty:
            continue

        model_v2 = MetaLearner(feature_columns=features.V2_FEATURE_COLUMNS)
        model_v2.fit(train_df, train_df["y"])
        proba_v2 = model_v2.predict_proba_up(test_df)

        model_v3 = MetaLearner(feature_columns=features.V3_FEATURE_COLUMNS)
        model_v3.fit(train_df, train_df["y"])
        proba_v3 = model_v3.predict_proba_up(test_df)

        model_v4 = MetaLearner(feature_columns=features.V4_FEATURE_COLUMNS)
        model_v4.fit(train_df, train_df["y"])
        proba_v4 = model_v4.predict_proba_up(test_df)
        importances = model_v4.feature_importances()
        if importances:
            v4_importances.append(importances)

        fold_result = test_df[["symbol", "y", "chronos_pred", "actual_return_pct"]].copy()
        fold_result["ensemble_proba_v2"] = proba_v2
        fold_result["ensemble_proba_v3"] = proba_v3
        fold_result["ensemble_proba_v4"] = proba_v4
        out.append(fold_result)
    if not out:
        return None, []
    return pd.concat(out, ignore_index=True), v4_importances


def average_feature_importances(importances_list):
    """Promedia feature_importances() de cada fold V4 -- mas robusto que
    mirar solo el ultimo fold (un RandomForest nuevo por fold, con su propia
    variabilidad). Devuelve lista de (feature, importancia_promedio) ya
    ordenada de mayor a menor."""
    if not importances_list:
        return []
    totals = {}
    for importances in importances_list:
        for feature, value in importances.items():
            totals.setdefault(feature, []).append(value)
    averaged = {feature: sum(values) / len(values) for feature, values in totals.items()}
    return sorted(averaged.items(), key=lambda kv: -kv[1])


def summarize(results_df, symbol, proba_col="ensemble_proba"):
    """Meta-Labeling (ver conversacion): el modelo secundario YA NO elige su
    propia direccion -- esa la propone Chronos (chronos_pred, el primario),
    el secundario solo CONFIRMA o RECHAZA esa propuesta. 'Operada' =
    proba_col >= META_LABEL_CONFIDENCE_THRESHOLD (un solo piso de confianza,
    pedido explicito: 'si p < 0.50, degrada a mantener' -- reemplaza la banda
    simetrica [EDGE_LOWER, EDGE_UPPER] que tenia sentido con el label de
    horizonte fijo anterior, donde CUALQUIER modelo podia proponer cualquier
    direccion). Confirmada, se opera en la direccion de chronos_pred (nunca
    la que proponga proba_col: el secundario no tiene direccion propia)."""
    n = len(results_df)
    chronos_acc = (results_df["chronos_pred"] == results_df["y"]).mean() * 100
    ensemble_pred = (results_df[proba_col] >= 0.5).astype(int)
    ensemble_acc = (ensemble_pred == results_df["y"]).mean() * 100

    operated = results_df[results_df[proba_col] >= META_LABEL_CONFIDENCE_THRESHOLD].copy()
    n_filtered = n - len(operated)

    win_rate = profit_factor = sharpe = None
    trade_return = np.array([])
    if len(operated):
        # Simplificacion declarada: se mide como si se pudiera tomar la
        # posicion corta simetrica cuando chronos_pred predice baja -- esta
        # app no soporta short real (ver portfolio/signals.py, comprar/vender
        # es sobre tenencias existentes), esto mide la CALIDAD de la señal,
        # no es una propuesta de operatoria.
        direction = np.where(operated["chronos_pred"].to_numpy() == 1, 1, -1)
        trade_return = direction * operated["actual_return_pct"].to_numpy()
        win_rate = float((trade_return > 0).mean() * 100)
        gains = trade_return[trade_return > 0].sum()
        losses = -trade_return[trade_return < 0].sum()
        if losses > 0:
            profit_factor = float(gains / losses)
        elif gains > 0:
            profit_factor = float("inf")
        periods_per_year = 252 / ANCHOR_SPACING_DAYS
        if len(trade_return) > 1 and trade_return.std(ddof=1) > 0:
            sharpe = float((trade_return.mean() / trade_return.std(ddof=1)) * np.sqrt(periods_per_year))

    return {
        "symbol": symbol,
        "n_test_anchors": n,
        "chronos_accuracy_pct": round(chronos_acc, 1),
        "ensemble_accuracy_pct": round(ensemble_acc, 1),
        "win_rate_pct": round(win_rate, 1) if win_rate is not None else None,
        "profit_factor": profit_factor,
        "sharpe_ratio": round(sharpe, 2) if sharpe is not None else None,
        "n_operated": len(operated),
        "n_filtered": n_filtered,
        # Retornos POR TRADE (no diarios, sin anualizar) del subconjunto
        # operado -- ver conversacion/deflated_sharpe_ratio: el DSR necesita
        # la serie cruda (asimetria/curtosis reales), no solo el Sharpe ya
        # resumido.
        "trade_returns": trade_return,
    }


# --- CPCV (Validacion Cruzada Purgada y Combinatoria) + Deflated Sharpe -----
#
# Lopez de Prado, ver conversacion ("evolucion integral", item 5): a
# diferencia de walk_forward_evaluate_pooled (UN solo camino expansivo por el
# pool), CPCV parte el pool en N_CPCV_GROUPS bloques y evalua TODAS las
# combinaciones de que bloques son test (C(N,k)) -- cada combinacion entrena
# con el resto del pool, purgado/embargado contra fuga de informacion, y
# evalua sobre los bloques de test elegidos. El resultado es una DISTRIBUCION
# de Sharpe (uno por combinacion), no un solo numero -- esa distribucion es
# la base del Deflated Sharpe Ratio (ver deflated_sharpe_ratio): corrige el
# Sharpe final por el sesgo de haber mirado muchas combinaciones/variantes
# antes de reportar la mejor.
#
# N_CPCV_GROUPS/N_CPCV_TEST_GROUPS NO fueron pedidos con un valor explicito
# (solo el metodo) -- 6 grupos eligiendo 2 de test (C(6,2)=15 combinaciones)
# declarado como default razonable: bastantes combinaciones para una
# distribucion util, sin que cada grupo quede con tan pocos anchors que el
# RandomForest no tenga con que entrenar/evaluar.
N_CPCV_GROUPS = 6
N_CPCV_TEST_GROUPS = 2
# Purga (ver _purge_and_embargo_train_indices): la ventana de resultado del
# Triple Barrier es HORIZON_DAYS RUEDAS, no dias calendario -- el pool
# mezcla tickers con calendarios de rueda propios (BYMA vs NYSE, feriados
# distintos), asi que se aproxima conservadoramente a HORIZON_DAYS*1.5 dias
# CALENDARIO (cubre fines de semana de sobra) en vez de intentar reconstruir
# el calendario de rueda exacto de cada ticker -- declarado, nunca se
# pretende que sea un conteo exacto de ruedas.
CPCV_PURGE_CALENDAR_DAYS = HORIZON_DAYS * 1.5
# Embargo: mismo horizonte que el propio Triple Barrier (ver conversacion) --
# margen extra, DESPUES de cada bloque de test, contra autocorrelacion serial
# que el purgado por ventana de resultado no cubre (Lopez de Prado recomienda
# un embargo proporcional al horizonte de las etiquetas, no un numero fijo
# arbitrario).
CPCV_EMBARGO_CALENDAR_DAYS = HORIZON_DAYS


def _cpcv_groups(pooled_df, n_groups=N_CPCV_GROUPS):
    """Parte el pool (YA ordenado por fecha, ver build_pooled_dataset) en
    n_groups bloques CONTIGUOS de tamaño similar -- devuelve una lista de
    (start_idx, end_idx) posicionales (end exclusivo, indexacion de
    pooled_df.iloc)."""
    n = len(pooled_df)
    edges = np.linspace(0, n, n_groups + 1).astype(int)
    return [(edges[i], edges[i + 1]) for i in range(n_groups)]


def _purge_and_embargo_train_indices(
    pooled_df, group_bounds, test_group_idxs,
    purge_days=CPCV_PURGE_CALENDAR_DAYS, embargo_days=CPCV_EMBARGO_CALENDAR_DAYS,
):
    """Devuelve (train_mask, test_mask) -- arrays booleanos (misma longitud
    que pooled_df) marcando que filas se usan para entrenar/evaluar en ESTA
    combinacion CPCV.

    test_mask: las filas de los grupos elegidos como test (test_group_idxs).

    train_mask excluye, ademas de el propio test: (a) PURGA -- cualquier fila
    de train cuya ventana de resultado [date, date+purge_days] se solapa con
    el rango de fechas de ALGUN grupo de test (evita que el label de
    entrenamiento dependa de informacion que ocurrio durante el periodo que
    se esta evaluando); (b) EMBARGO -- cualquier fila de train dentro de
    embargo_days INMEDIATAMENTE DESPUES del final de un bloque de test
    (margen extra contra autocorrelacion serial). Una fila purgada/embargada
    nunca se usa para entrenar, aunque tampoco sea parte del test de esta
    combinacion -- simplemente se descarta para esta combinacion puntual."""
    dates = pd.DatetimeIndex(pooled_df["date"])
    n = len(pooled_df)
    test_mask = np.zeros(n, dtype=bool)
    for g in test_group_idxs:
        start, end = group_bounds[g]
        test_mask[start:end] = True

    # pd.Timedelta(days=...) con un valor no-entero (ver CPCV_PURGE_CALENDAR_DAYS
    # = HORIZON_DAYS*1.5) dispara un DeprecationWarning de pandas/numpy sobre
    # "generic unit" -- pd.Timedelta(valor, unit="D") es la forma sin ese
    # warning, mismo resultado.
    purge_td = pd.Timedelta(purge_days, unit="D")
    embargo_td = pd.Timedelta(embargo_days, unit="D")
    excluded_from_train = test_mask.copy()
    outcome_end_dates = dates + purge_td
    for g in test_group_idxs:
        start, end = group_bounds[g]
        test_start, test_end = dates[start], dates[end - 1]
        # Purga: la ventana de resultado del train [date, date+purge_td] se
        # solapa con [test_start, test_end] del bloque de test.
        overlap = (outcome_end_dates >= test_start) & (dates <= test_end)
        excluded_from_train |= np.asarray(overlap)
        # Embargo: train dentro de embargo_td despues del fin del bloque de test.
        embargo_zone = (dates > test_end) & (dates <= test_end + embargo_td)
        excluded_from_train |= np.asarray(embargo_zone)

    train_mask = ~excluded_from_train
    return train_mask, test_mask


def cpcv_evaluate_pooled(pooled_df, n_groups=N_CPCV_GROUPS, n_test_groups=N_CPCV_TEST_GROUPS):
    """CPCV (ver seccion de arriba): entrena/evalua los 3 modelos (V2/V3/V4)
    sobre TODAS las combinaciones C(n_groups, n_test_groups) de bloques de
    test, con purga+embargo en cada una (ver _purge_and_embargo_train_indices).

    Devuelve (resultados concatenados de TODAS las combinaciones -- con
    columna 'cpcv_combo' identificando de cual vino cada fila, para nunca
    tratar filas de combinaciones distintas como si fueran un unico fold
    gigante --, lista de feature_importances() de V4 por combinacion, lista
    de Sharpe de V4 por combinacion -- esta ultima es la distribucion que usa
    deflated_sharpe_ratio)."""
    group_bounds = _cpcv_groups(pooled_df, n_groups)
    combos = list(itertools.combinations(range(n_groups), n_test_groups))

    out = []
    v4_importances = []
    v4_sharpes_by_combo = []
    for combo_id, test_groups in enumerate(combos):
        train_mask, test_mask = _purge_and_embargo_train_indices(pooled_df, group_bounds, test_groups)
        train_df = pooled_df[train_mask]
        test_df = pooled_df[test_mask]
        if train_df.empty or test_df.empty or train_df["y"].nunique() < 2:
            continue  # combinacion sin evidencia suficiente -- se saltea, no rompe el resto

        model_v2 = MetaLearner(feature_columns=features.V2_FEATURE_COLUMNS)
        model_v2.fit(train_df, train_df["y"])
        proba_v2 = model_v2.predict_proba_up(test_df)

        model_v3 = MetaLearner(feature_columns=features.V3_FEATURE_COLUMNS)
        model_v3.fit(train_df, train_df["y"])
        proba_v3 = model_v3.predict_proba_up(test_df)

        model_v4 = MetaLearner(feature_columns=features.V4_FEATURE_COLUMNS)
        model_v4.fit(train_df, train_df["y"])
        proba_v4 = model_v4.predict_proba_up(test_df)
        importances = model_v4.feature_importances()
        if importances:
            v4_importances.append(importances)

        fold_result = test_df[["symbol", "y", "chronos_pred", "actual_return_pct"]].copy()
        fold_result["ensemble_proba_v2"] = proba_v2
        fold_result["ensemble_proba_v3"] = proba_v3
        fold_result["ensemble_proba_v4"] = proba_v4
        fold_result["cpcv_combo"] = combo_id
        out.append(fold_result)

        combo_summary = summarize(fold_result, f"combo_{combo_id}", proba_col="ensemble_proba_v4")
        if combo_summary["sharpe_ratio"] is not None:
            v4_sharpes_by_combo.append(combo_summary["sharpe_ratio"])

    if not out:
        return None, [], []
    return pd.concat(out, ignore_index=True), v4_importances, v4_sharpes_by_combo


def _expected_max_sharpe_under_null(sharpe_estimates):
    """SR0 (Bailey & Lopez de Prado 2014, 'The Deflated Sharpe Ratio') -- el
    Sharpe MAXIMO esperado por puro azar al haber probado N configuraciones
    independientes (aca: N = combinaciones CPCV del modelo final, ver
    cpcv_evaluate_pooled) -- mas combinaciones probadas, mayor el Sharpe
    maximo que se espera ver aunque NINGUNA tenga edge real. Formula exacta
    del paper: SR0 = sqrt(Var[SR]) * ((1-gamma)*Phi^-1(1-1/N) + gamma*Phi^-1(1-1/(N*e))),
    gamma = constante de Euler-Mascheroni. None (no 0.0) si hay menos de 2
    estimaciones -- no se puede medir Var[SR] con una sola, nunca se inventa
    una varianza."""
    sharpe_estimates = np.asarray(sharpe_estimates, dtype=float)
    n_trials = len(sharpe_estimates)
    if n_trials < 2:
        return None
    var_sr = float(np.var(sharpe_estimates, ddof=1))
    if var_sr <= 0:
        return 0.0
    euler_mascheroni = 0.5772156649015329
    z1 = scipy_stats.norm.ppf(1 - 1.0 / n_trials)
    z2 = scipy_stats.norm.ppf(1 - 1.0 / (n_trials * np.e))
    sr0 = np.sqrt(var_sr) * ((1 - euler_mascheroni) * z1 + euler_mascheroni * z2)
    return float(sr0)


def deflated_sharpe_ratio(trade_returns, sharpe_estimates_for_selection):
    """DSR (Deflated Sharpe Ratio, Bailey & Lopez de Prado 2014, ver
    conversacion item 5) -- P(el Sharpe observado de la estrategia ELEGIDA es
    real, no producto de haber probado muchas combinaciones/configuraciones)
    YA corregido por (a) sesgo de seleccion multiple (ver
    _expected_max_sharpe_under_null, usa sharpe_estimates_for_selection: la
    distribucion de Sharpe por combinacion CPCV del modelo final) y (b)
    desvios de normalidad de los retornos reales (asimetria/curtosis -- un
    Sharpe clasico asume retornos normales, los financieros rara vez lo son).

    trade_returns: retornos POR TRADE (no diarios, sin anualizar) de la
    estrategia evaluada -- de aca salen SR_hat/asimetria/curtosis reales.
    sharpe_estimates_for_selection: los N Sharpe por combinacion CPCV
    (INCLUYE el de la combinacion/estrategia elegida) -- define SR0 y N.

    Devuelve un dict {dsr, sr_hat, sr0, skewness, kurtosis, n_trials,
    n_trade_returns} -- o None si no hay suficiente evidencia en cualquiera
    de los dos insumos (ni un trade_returns muy chico ni un SR0 indefinido
    se disfrazan de un numero, nunca se inventa un resultado)."""
    returns = np.asarray(trade_returns, dtype=float)
    returns = returns[~np.isnan(returns)]
    t_obs = len(returns)
    if t_obs < 10:
        return None
    std = returns.std(ddof=1)
    if std <= 0:
        return None
    sr_hat = float(returns.mean() / std)
    skewness = float(scipy_stats.skew(returns))
    # kurtosis "cruda" (no excedente, ver formula de Bailey/Lopez de Prado --
    # una normal da 3.0 aca, no 0.0 como el default de scipy con fisher=True).
    kurtosis = float(scipy_stats.kurtosis(returns, fisher=False))

    sr0 = _expected_max_sharpe_under_null(sharpe_estimates_for_selection)
    if sr0 is None:
        return None

    denom = np.sqrt(max(1e-12, 1 - skewness * sr_hat + (kurtosis - 1) / 4 * sr_hat ** 2))
    z = (sr_hat - sr0) * np.sqrt(t_obs - 1) / denom
    dsr = float(scipy_stats.norm.cdf(z))
    return {
        "dsr": dsr, "sr_hat": round(sr_hat, 4), "sr0": round(sr0, 4),
        "skewness": round(skewness, 3), "kurtosis": round(kurtosis, 3),
        "n_trials": len(np.asarray(sharpe_estimates_for_selection, dtype=float)), "n_trade_returns": t_obs,
    }


def _fmt_pct(v):
    return f"{v:.1f}%" if v is not None else "—"


def _fmt_pf(v):
    if v is None:
        return "—"
    return "inf" if v == float("inf") else f"{v:.2f}"


def _fmt_sharpe(v):
    return f"{v:.2f}" if v is not None else "—"


def _report_row(symbol, v2_summary, v3_summary, v4_summary):
    """Fusiona los summary V2 (sin macro), V3 (con macro) y V4 (+tecnicos)
    de un mismo grupo de anchors en una sola fila de reporte --
    chronos_accuracy_pct es identico en los 3 (chronos_pred no depende de la
    columna de proba del ensemble), Win Rate/Profit Factor/Sharpe/Operadas-
    Filtradas se toman de V4 (el modelo final y mas completo, el que se
    estaria evaluando para operar de verdad)."""
    return {
        "symbol": symbol,
        "chronos_accuracy_pct": v2_summary["chronos_accuracy_pct"],
        "v2_accuracy_pct": v2_summary["ensemble_accuracy_pct"],
        "v3_accuracy_pct": v3_summary["ensemble_accuracy_pct"],
        "v4_accuracy_pct": v4_summary["ensemble_accuracy_pct"],
        "win_rate_pct": v4_summary["win_rate_pct"],
        "profit_factor": v4_summary["profit_factor"],
        "sharpe_ratio": v4_summary["sharpe_ratio"],
        "n_operated": v4_summary["n_operated"],
        "n_filtered": v4_summary["n_filtered"],
    }


def print_report(results, skipped, pooled_row, hurdle_pct, avg_importances, n_importance_folds):
    cols = [
        ("Ticker", 8), ("Chronos solo", 13), ("Meta V2 (sin macro)", 21), ("Meta V3 (con macro)", 21),
        ("Meta V4 (+tecnicos)", 21), ("Win Rate", 9), ("Profit Factor", 14), ("Sharpe", 8),
        ("Operadas/Filtradas", 20),
    ]
    header = " | ".join(name.ljust(w) for name, w in cols)
    print("\n" + "=" * len(header))
    print("REPORTE COMPARATIVO -- Chronos-Bolt solo vs. Meta-Modelo Global V2 (sin macro) vs. "
          "V3 (multivariado, con macro) vs. V4 (+RSI/MACD/ATR%/FFD)")
    print(f"Horizonte (Barrera Vertical): {HORIZON_DAYS} ruedas · Triple Barrier pt/sl={TRIPLE_BARRIER_PT_MULT}/"
          f"{TRIPLE_BARRIER_SL_MULT}x sigma EWMA({EWMA_VOL_WINDOW}d) · Anchors cada {ANCHOR_SPACING_DAYS} ruedas · "
          f"{N_FOLDS} folds walk-forward expansivo sobre el pool · confianza Meta-Labeling >= {META_LABEL_CONFIDENCE_THRESHOLD} · "
          f"hurdle {hurdle_pct:.2f}% a {HORIZON_DAYS}d (caucion TNA, define chronos_pred). Win Rate/Profit Factor/Sharpe son de V4.")
    print("=" * len(header))
    print(header)
    print("-" * len(header))
    for r in results:
        row = [
            r["symbol"].ljust(cols[0][1]),
            _fmt_pct(r["chronos_accuracy_pct"]).ljust(cols[1][1]),
            _fmt_pct(r["v2_accuracy_pct"]).ljust(cols[2][1]),
            _fmt_pct(r["v3_accuracy_pct"]).ljust(cols[3][1]),
            _fmt_pct(r["v4_accuracy_pct"]).ljust(cols[4][1]),
            _fmt_pct(r["win_rate_pct"]).ljust(cols[5][1]),
            _fmt_pf(r["profit_factor"]).ljust(cols[6][1]),
            _fmt_sharpe(r["sharpe_ratio"]).ljust(cols[7][1]),
            f"{r['n_operated']}/{r['n_filtered']}".ljust(cols[8][1]),
        ]
        print(" | ".join(row))
    print("-" * len(header))
    if pooled_row:
        row = [
            "GLOBAL".ljust(cols[0][1]),
            _fmt_pct(pooled_row["chronos_accuracy_pct"]).ljust(cols[1][1]),
            _fmt_pct(pooled_row["v2_accuracy_pct"]).ljust(cols[2][1]),
            _fmt_pct(pooled_row["v3_accuracy_pct"]).ljust(cols[3][1]),
            _fmt_pct(pooled_row["v4_accuracy_pct"]).ljust(cols[4][1]),
            _fmt_pct(pooled_row["win_rate_pct"]).ljust(cols[5][1]),
            _fmt_pf(pooled_row["profit_factor"]).ljust(cols[6][1]),
            _fmt_sharpe(pooled_row["sharpe_ratio"]).ljust(cols[7][1]),
            f"{pooled_row['n_operated']}/{pooled_row['n_filtered']}".ljust(cols[8][1]),
        ]
        print(" | ".join(row) + "   <- todos los anchors del pool juntos, no un promedio de las filas de arriba")
    print("=" * len(header))

    if results:
        avg_chronos = sum(r["chronos_accuracy_pct"] for r in results) / len(results)
        avg_v2 = sum(r["v2_accuracy_pct"] for r in results) / len(results)
        avg_v3 = sum(r["v3_accuracy_pct"] for r in results) / len(results)
        avg_v4 = sum(r["v4_accuracy_pct"] for r in results) / len(results)
        print(f"Promedio simple por ticker ({len(results)}/{len(results) + len(skipped)} evaluados): "
              f"Chronos solo {avg_chronos:.1f}% · Meta V2 (sin macro) {avg_v2:.1f}% · "
              f"Meta V3 (con macro) {avg_v3:.1f}% · Meta V4 (+tecnicos) {avg_v4:.1f}%")
    if skipped:
        print("Sin resultado: " + ", ".join(f"{s} ({m})" for s, m in skipped.items()))

    print("\n" + "-" * 60)
    print(f"FEATURE IMPORTANCE -- Meta-Modelo V4, promedio sobre {n_importance_folds} "
          f"fold(s) walk-forward (RandomForest.feature_importances_)")
    print("-" * 60)
    if not avg_importances:
        print("(sin modelos V4 entrenados -- ningun fold tuvo suficiente evidencia)")
    else:
        for feature, value in avg_importances:
            group = (
                "Chronos" if feature in features.CHRONOS_FEATURE_COLUMNS
                else "Macro" if feature in features.MACRO_FEATURE_COLUMNS
                else "Tecnico" if feature in features.TECHNICAL_FEATURE_COLUMNS
                else "Ticker"
            )
            bar = "█" * max(1, round(value * 40))
            print(f"  {feature:<24} [{group:<7}] {value * 100:5.1f}%  {bar}")
        macro_total = sum(v for f, v in avg_importances if f in features.MACRO_FEATURE_COLUMNS)
        ticker_total = sum(v for f, v in avg_importances if f in features.MICROSTRUCTURE_FEATURE_COLUMNS)
        chronos_total = sum(v for f, v in avg_importances if f in features.CHRONOS_FEATURE_COLUMNS)
        technical_total = sum(v for f, v in avg_importances if f in features.TECHNICAL_FEATURE_COLUMNS)
        print(f"\n  Por grupo: Chronos {chronos_total * 100:.1f}% · Microestructura ticker {ticker_total * 100:.1f}% · "
              f"Macro exogeno {macro_total * 100:.1f}% · Tecnico (RSI/MACD/ATR%/FFD) {technical_total * 100:.1f}%")


def print_cpcv_report(cpcv_sharpes, cpcv_global_summary, dsr_result):
    """VALIDACION ANTI-OVERFITTING -- CPCV + Deflated Sharpe Ratio (ver
    conversacion item 5): a diferencia del walk-forward de arriba (un solo
    camino), esto muestra la DISTRIBUCION de Sharpe de V4 sobre
    N_CPCV_GROUPS-elige-N_CPCV_TEST_GROUPS combinaciones purgadas+embargadas,
    y el DSR -- la probabilidad de que el Sharpe reportado sea real y no un
    artefacto de haber mirado muchas combinaciones."""
    header = "=" * 100
    print("\n" + header)
    print(f"VALIDACION ANTI-OVERFITTING -- CPCV (Purgada y Combinatoria, {N_CPCV_GROUPS} grupos / "
          f"{N_CPCV_TEST_GROUPS} de test = {len(cpcv_sharpes)} combinaciones) + Deflated Sharpe Ratio -- Meta-Modelo V4")
    print(header)
    if not cpcv_sharpes:
        print("(sin combinaciones evaluables -- pool insuficiente para este N_CPCV_GROUPS)")
        print(header)
        return
    sharpes = np.asarray(cpcv_sharpes, dtype=float)
    print(f"Sharpe por combinacion -- media {sharpes.mean():.2f} · desvio {sharpes.std(ddof=1):.2f} · "
          f"min {sharpes.min():.2f} · max {sharpes.max():.2f} · {len(sharpes)} combinaciones")
    print(f"Sharpe global CPCV (todas las combinaciones pooleadas): "
          f"{_fmt_sharpe(cpcv_global_summary['sharpe_ratio'])} · "
          f"Win Rate {_fmt_pct(cpcv_global_summary['win_rate_pct'])} · "
          f"Profit Factor {_fmt_pf(cpcv_global_summary['profit_factor'])} · "
          f"{cpcv_global_summary['n_operated']} operadas / {cpcv_global_summary['n_filtered']} filtradas")
    if dsr_result is None:
        print("\nDeflated Sharpe Ratio: no calculable (pocas operaciones o varianza nula entre combinaciones -- "
              "nunca se inventa un resultado con poca evidencia)")
    else:
        veredicto = (
            "ESTADISTICAMENTE SIGNIFICATIVO" if dsr_result["dsr"] >= 0.95
            else "NO significativo (el Sharpe observado es consistente con azar/sobreajuste de seleccion)"
        )
        print(f"\nDeflated Sharpe Ratio (DSR): {dsr_result['dsr']:.4f} -- {veredicto}")
        print(f"  SR_hat (por trade, sin anualizar) {dsr_result['sr_hat']} · SR0 (maximo esperado por azar con "
              f"{dsr_result['n_trials']} combinaciones) {dsr_result['sr0']} · asimetria {dsr_result['skewness']} · "
              f"curtosis {dsr_result['kurtosis']} · n={dsr_result['n_trade_returns']} trades")
    print(header)


def run_full_report(universe=UNIVERSE):
    client = ChronosServiceClient()
    try:
        client.health()
    except Exception as exc:
        print(f"ERROR: no se pudo contactar al microservicio de prediccion ({exc}). "
              f"Levantalo primero (ver ./dev.sh o services/predictor/README.md).", file=sys.stderr)
        sys.exit(1)

    conn = db.get_connection()
    try:
        hurdle_pct = compute_hurdle_pct(conn, HORIZON_DAYS)
        print(f"Construyendo anchors por ticker (hurdle {hurdle_pct:.2f}% a {HORIZON_DAYS}d)...")
        pooled, skipped, timings = build_pooled_dataset(universe, client, conn)
        for symbol, _asset_type in universe:
            if symbol in timings:
                status = "ok" if symbol not in skipped else f"sin resultado ({skipped[symbol]})"
                print(f"-- {symbol}... {status} ({timings[symbol]:.1f}s)")

        if pooled is None:
            print("ERROR: ningun ticker del universo pudo construir anchors.", file=sys.stderr)
            sys.exit(1)

        print(f"\nPool global: {len(pooled)} anchors de {pooled['symbol'].nunique()} tickers "
              f"({pooled['date'].min()} -> {pooled['date'].max()}). Entrenando walk-forward ({N_FOLDS} folds, "
              f"V2 sin macro + V3 con macro + V4 +tecnicos por fold)...")
        pooled_results, v4_importances = walk_forward_evaluate_pooled(pooled)
        if pooled_results is None or pooled_results.empty:
            print("ERROR: el walk-forward pooled no produjo folds evaluables "
                  f"(¿MIN_TRAIN_ANCHORS_POOLED={MIN_TRAIN_ANCHORS_POOLED} > anchors disponibles?).", file=sys.stderr)
            sys.exit(1)

        per_ticker = []
        for symbol, group in pooled_results.groupby("symbol", sort=False):
            v2_summary = summarize(group, symbol, proba_col="ensemble_proba_v2")
            v3_summary = summarize(group, symbol, proba_col="ensemble_proba_v3")
            v4_summary = summarize(group, symbol, proba_col="ensemble_proba_v4")
            per_ticker.append(_report_row(symbol, v2_summary, v3_summary, v4_summary))
        # Orden original del universo (groupby reordena alfabetico/de aparicion)
        order = {s: i for i, (s, _) in enumerate(universe)}
        per_ticker.sort(key=lambda r: order.get(r["symbol"], 999))

        pooled_v2 = summarize(pooled_results, "GLOBAL", proba_col="ensemble_proba_v2")
        pooled_v3 = summarize(pooled_results, "GLOBAL", proba_col="ensemble_proba_v3")
        pooled_v4 = summarize(pooled_results, "GLOBAL", proba_col="ensemble_proba_v4")
        pooled_row = _report_row("GLOBAL", pooled_v2, pooled_v3, pooled_v4)

        avg_importances = average_feature_importances(v4_importances)

        # CPCV + DSR (ver conversacion item 5) -- corre sobre el MISMO
        # `pooled` ya construido arriba (cero llamadas nuevas a Chronos-Bolt,
        # solo re-entrena RandomForest por combinacion, barato).
        print(f"\nCorriendo CPCV ({N_CPCV_GROUPS} grupos, {N_CPCV_TEST_GROUPS} de test por combinacion)...")
        cpcv_results, _cpcv_importances, cpcv_sharpes = cpcv_evaluate_pooled(pooled)
        cpcv_global_summary = dsr_result = None
        if cpcv_results is not None and not cpcv_results.empty:
            cpcv_global_summary = summarize(cpcv_results, "CPCV_GLOBAL", proba_col="ensemble_proba_v4")
            dsr_result = deflated_sharpe_ratio(cpcv_global_summary["trade_returns"], cpcv_sharpes)
    finally:
        conn.close()

    print_report(per_ticker, skipped, pooled_row, hurdle_pct, avg_importances, len(v4_importances))
    if cpcv_global_summary is not None:
        print_cpcv_report(cpcv_sharpes, cpcv_global_summary, dsr_result)
    else:
        print("\n(CPCV no produjo combinaciones evaluables -- ver pool/N_CPCV_GROUPS)")
    return per_ticker, skipped, pooled_row, avg_importances


# --- Backtest comparativo Motor CORE vs Motor SATELITE (ver conversacion) --
#
# Seccion INDEPENDIENTE del walk-forward de arriba (que evalua la CALIDAD de
# la señal Chronos+meta-modelo, no una operatoria real con TP/stop/expiracion
# ni asignacion de capital) -- nada de lo de arriba se modifica ni se
# reusa salvo fetch_ticker_history/compute_hurdle_pct/_chronos_pct_changes,
# que son utilidades genericas, no especificas del meta-modelo.
#
# Motor CORE: portfolio/signals.py (ranking cross-sectional por
# efficiency_score + filtro de regimen SPY>EMA50 + Volatility Targeting).
# Motor SATELITE: portfolio/short_term_engine.py (2 tacticas tecnicas, sin
# Chronos). Ambos comparten el simulador de salida (simulate_position_exit)
# y el criterio de Cash Sweep a Caucion para el capital ocioso.

CORE_BACKTEST_LOOKBACK_DAYS = 300  # ventana reciente -- acota el costo total en llamadas a Chronos-Bolt

# Validacion Out-Of-Sample (ver conversacion): las reglas/parametros de
# ambos motores se vinieron ajustando mirando resultados de la ventana
# COMPLETA de 300 ruedas en iteraciones anteriores -- eso es tuning dentro
# de la muestra, no una validacion real. IN_SAMPLE_DAYS son las primeras 200
# ruedas de esa misma ventana (lo ya mirado), OUT_OF_SAMPLE_DAYS son las
# ultimas 100 (nunca miradas al elegir umbrales/parametros) -- deben sumar
# CORE_BACKTEST_LOOKBACK_DAYS.
IN_SAMPLE_DAYS = 200
OUT_OF_SAMPLE_DAYS = 100
assert IN_SAMPLE_DAYS + OUT_OF_SAMPLE_DAYS == CORE_BACKTEST_LOOKBACK_DAYS
CORE_REGIME_EMA_WINDOW = 50
CORE_ATR_WINDOW = 14
CORE_TP_FLAT_PCT = 6.0
CORE_TP_ATR_MULT = 2.0
# Diagnostico (ver conversacion): sobre la ventana de 300 ruedas, el Max DD
# de -17.4% con solo 11 trades NO viene del horizonte de 15 ruedas (ningun
# trade llego a time_exit -- todos resuelven antes por TP/stop) ni del
# filtro de regimen (SPY>EMA50 esta ABIERTO el 57% de los dias, sin rachas
# cerradas mas alla de la logica -- no es el cuello de botella de frecuencia:
# ESE es CORE_MIN_EFFICIENCY_SCORE=80.0 en signals.py, deliberadamente
# estricto, ver nota ahi). El verdadero driver del drawdown es el ANCHO del
# stop: con PISO (max(3.0, 1.5xATR)), en nombres volatiles como BMA/GGAL/ALUA
# el stop real llegaba a 6-12%, confirmado en las 2 peores operaciones
# (-10.4% y -12.4%). Mismo fix ya validado en el Motor Satelite (ver
# portfolio/config.py::get_satellite_exit_params): el stop pasa a ser un
# TECHO -- min(CORE_STOP_FLAT_PCT, CORE_STOP_ATR_MULT*ATR%), ver
# simulate_core_engine(..., stop_is_ceiling=True). CORE_STOP_FLAT_PCT subio
# de 3.0 a 10.0 porque como TECHO acota el peor caso observado (-12.4%) sin
# volverse una regla "siempre 3%" en nombres volatiles (el ATR real seguia
# dando 6-12%, un techo de 3.0 lo hubiera angostado SIEMPRE, no solo en la
# cola) -- Take Profit se deja intacto (piso/max, nunca conviene topear
# cuanto puede correr un ganador).
CORE_STOP_FLAT_PCT = 10.0
CORE_STOP_ATR_MULT = 1.5
CORE_MAX_HOLD_DAYS = 15

SATELLITE_MAX_POSITIONS = 3  # no pedido explicitamente -- declarado aca para poder simular multiples trades concurrentes sin apalancar
SATELLITE_MIN_WARMUP_ROWS = 40

# Costo de transaccion ida+vuelta (ver conversacion/plan de unificacion, Fase
# 4: "ningun backtest/paper trading resta comision... todo el edge medido es
# BRUTO, no neto" -- un edge que ya es ~0 bruto (ver DSR=0.0000 del motor
# base) puede ser negativo neto). No es un numero inventado: es el promedio
# real observado en portfolio/transactions con comision>0 (comision importada
# de resumenes de broker reales via import_broker_report.py, columna
# `comision` de schema.sql) -- ronda 1.25% por pata (rango real 0.07%-2.2%),
# asi que 2.5% ida+vuelta es razonable y conservador. Ajustable si cambia el
# broker/la comision real; declarado explicito aca para que cualquier
# Sharpe/DSR calculado de ahora en mas sea interpretable como NETO.
ROUND_TRIP_COMMISSION_PCT = 2.5


def _prepare_aligned_series(symbol, asset_type, conn, min_rows):
    """Fetchea + alinea a la MISMA moneda que ve Chronos en produccion
    (build_model_input convierte las 4 columnas de precio a USD CCL para
    accion/cedear, ver forecasting.py) -- devuelve un DataFrame
    date/open/high/low/close/volume o None si no alcanza min_rows o si el
    fetch/conversion falla (ticker nuevo en el universo sin cobertura en
    data912, fuente caida, etc. -- nunca tumba el resto del universo por
    uno solo)."""
    try:
        raw_df = fetch_ticker_history(symbol, asset_type, conn=conn)
        if len(raw_df) < min_rows:
            return None
        model_df, _ccl, _currency = build_model_input(conn, raw_df, asset_type)
    except Exception:
        return None
    return pd.DataFrame({
        "date": pd.to_datetime(raw_df["timestamps"]).dt.normalize().reset_index(drop=True),
        "open": model_df["open"].reset_index(drop=True).astype(float),
        "high": model_df["high"].reset_index(drop=True).astype(float),
        "low": model_df["low"].reset_index(drop=True).astype(float),
        "close": model_df["close"].reset_index(drop=True).astype(float),
        "volume": raw_df["volume"].reset_index(drop=True).astype(float),
    })


def simulate_position_exit(future_rows, entry_price, tp_pct, initial_stop_pct, max_hold_days,
                            trailing=False, atr_pct_value=None, tp_atr_mult=None, stop_atr_mult=None,
                            stop_is_ceiling=False, commission_pct=0.0):
    """Simulador de salida COMPARTIDO por Core y Satelite (misma logica para
    los dos, para que ninguno tenga una ventaja de simulacion que el otro no
    tenga). future_rows: filas high/low/close YA posteriores a la entrada
    (nunca incluye el dia de entrada), orden cronologico -- se recorta a
    max_hold_days.

    Si se pasa atr_pct_value, el TP efectivo es siempre el MAS ANCHO entre el
    porcentaje fijo y el multiplo de ATR (pedido explicito del Core: "+6.0%
    o +2.0xATR", nunca el mas angosto). El STOP depende de stop_is_ceiling:
    False (Core, default) = igual criterio que el TP, el MAS ANCHO de los
    dos (nunca un stop mas angosto que initial_stop_pct). True (Satelite,
    ver conversacion) = initial_stop_pct es un TECHO, el stop real es el MAS
    ANGOSTO entre el % configurado y el multiplo de ATR -- nunca se pierde
    mas que ese % en una sola operacion, aunque el activo sea muy volatil.

    Ambiguedad declarada: con velas diarias OHLC no hay forma de saber si el
    high o el low de una misma rueda se tocaron primero -- se asume que el
    STOP se toca primero (peor caso posible), nunca se infla el resultado
    asumiendo la salida mas favorable.

    commission_pct: costo de transaccion ida+vuelta ya restado del return_pct
    devuelto (ver ROUND_TRIP_COMMISSION_PCT) -- default 0.0 para no romper
    ningun llamador que todavia no lo pase explicito.

    Devuelve (hold_days, exit_price, return_pct, exit_reason) con
    exit_reason en {'take_profit','stop_loss','trailing_stop','time_exit','no_data'}.
    return_pct ya es NETO de comision si se paso commission_pct."""
    effective_tp_pct = tp_pct
    if tp_atr_mult is not None and atr_pct_value and not pd.isna(atr_pct_value):
        effective_tp_pct = max(tp_pct, tp_atr_mult * atr_pct_value)
    effective_stop_pct = initial_stop_pct
    if stop_atr_mult is not None and atr_pct_value and not pd.isna(atr_pct_value):
        atr_stop_pct = stop_atr_mult * atr_pct_value
        effective_stop_pct = min(initial_stop_pct, atr_stop_pct) if stop_is_ceiling else max(initial_stop_pct, atr_stop_pct)

    tp_price = entry_price * (1 + effective_tp_pct / 100)
    initial_stop_price = entry_price * (1 - effective_stop_pct / 100)
    stop_price = initial_stop_price
    highest_close = entry_price

    rows = future_rows.iloc[:max_hold_days]
    if rows.empty:
        return 0, entry_price, 0.0, "no_data"

    for i, row in enumerate(rows.itertuples(), start=1):
        high, low, close = float(row.high), float(row.low), float(row.close)

        if trailing and close > highest_close:
            highest_close = close
            trailed_stop = highest_close * (1 - effective_stop_pct / 100)
            if trailed_stop > stop_price:
                stop_price = trailed_stop

        if low <= stop_price:
            reason = "trailing_stop" if (trailing and stop_price > initial_stop_price) else "stop_loss"
            return i, stop_price, (stop_price - entry_price) / entry_price * 100 - commission_pct, reason
        if high >= tp_price:
            return i, tp_price, (tp_price - entry_price) / entry_price * 100 - commission_pct, "take_profit"

    last_close = float(rows["close"].iloc[-1])
    return len(rows), last_close, (last_close - entry_price) / entry_price * 100 - commission_pct, "time_exit"


def _trade_daily_returns(df, entry_pos, entry_price, hold_days, exit_price, commission_pct=0.0):
    """Retorno %/dia para marcar a mercado dia a dia una posicion ya resuelta
    por simulate_position_exit -- cada dia usa el cierre real de esa rueda,
    EXCEPTO el ultimo (donde se usa el precio de salida real: TP/stop/ultimo
    cierre, no el cierre de esa rueda, para que el equity curve refleje el
    motivo real de cierre). commission_pct (ver ROUND_TRIP_COMMISSION_PCT) se
    resta completo en el ultimo dia -- es cuando se "realiza" el costo
    ida+vuelta, mismo criterio contable que una liquidacion real. Devuelve
    lista de (fecha, retorno_pct), ya NETO de comision si se paso
    commission_pct."""
    out = []
    prev_price = entry_price
    rows = df.iloc[entry_pos + 1: entry_pos + 1 + hold_days]
    for i, row in enumerate(rows.itertuples(), start=1):
        price_today = float(exit_price) if i == hold_days else float(row.close)
        day_ret = (price_today - prev_price) / prev_price * 100 if prev_price else 0.0
        if i == hold_days:
            day_ret -= commission_pct
        out.append((row.date, day_ret))
        prev_price = price_today
    return out


def simulate_core_engine(universe, client, conn, lookback_days=CORE_BACKTEST_LOOKBACK_DAYS):
    """Simulador del Motor CORE sobre todo el universo: ranking cross-sectional
    por efficiency_score (portfolio/signals.py), filtro de regimen
    SPY>EMA50, dimensionamiento por Volatility Targeting, salida asimetrica
    (TP/trailing stop/expiracion a 15 ruedas) y Cash Sweep a Caucion cuando
    ningun candidato califica. Acotado a los ultimos lookback_days de cada
    ticker para que el costo en llamadas a Chronos-Bolt sea manejable.

    Reparto de peso DECLARADO: hasta CORE_TOP_N posiciones conviven a la vez,
    cada una dimensionada por compute_core_position_weight() (0-1) pero
    escalada ademas por 1/CORE_TOP_N -- asi la suma de las dos nunca puede
    superar el 100% de la porcion de capital asignada a Core (sin esto, dos
    señales con weight=1.0 cada una implicarian apalancamiento 2x, que el
    pedido no exigio explicitamente pero tampoco tiene sentido para un motor
    descripto como "de preservacion").

    Devuelve dict con 'daily_returns_pct' (pd.Series indexada por fecha, el
    retorno diario de TODA la porcion Core: trades + caucion ociosa),
    'idle_weight' (fraccion ociosa por dia), 'trades', 'skipped' y
    'chronos_calls' (para reportar el costo real de la corrida)."""
    core_min_score = capital_config.get_core_params(conn)["min_efficiency_score"]
    series = {}
    skipped = {}
    min_rows = LOOKBACK + CORE_REGIME_EMA_WINDOW + CORE_ATR_WINDOW + lookback_days
    for symbol, asset_type in universe:
        df = _prepare_aligned_series(symbol, asset_type, conn, min_rows)
        if df is None:
            skipped[symbol] = f"historial insuficiente (necesita >= {min_rows} ruedas)"
            continue
        df["atr_pct"] = indicators.atr_pct(df, CORE_ATR_WINDOW).to_numpy()
        series[symbol] = df

    if "SPY" not in series:
        raise RuntimeError(
            "SPY sin historial suficiente -- requerido para el filtro de regimen (SPY > su propia EMA50)"
        )

    spy_df = series["SPY"]
    spy_ema50 = indicators.ema(spy_df["close"], CORE_REGIME_EMA_WINDOW).to_numpy()
    spy_pos_by_date = {d: i for i, d in enumerate(spy_df["date"])}

    pos_by_date = {sym: {d: i for i, d in enumerate(df["date"])} for sym, df in series.items()}
    master_dates = sorted(set().union(*[set(df["date"]) for df in series.values()]))[-lookback_days:]

    hurdle_tna = float(db.get_signal_config(conn).get("caucion_reference_tna") or 0.0)
    daily_caucion_return_pct = yield_engine.daily_caucion_return_pct(hurdle_tna)  # ver portfolio/yield_engine.py

    contributions = {d: 0.0 for d in master_dates}
    committed_weight = {d: 0.0 for d in master_dates}
    open_slots = {}  # symbol -> exit_date
    trades = []
    chronos_calls = 0

    for date in master_dates:
        for symbol in [s for s, exit_date in open_slots.items() if exit_date <= date]:
            del open_slots[symbol]

        free_slots = signals.CORE_TOP_N - len(open_slots)
        if free_slots <= 0:
            continue

        spy_idx = spy_pos_by_date.get(date)
        if spy_idx is None:
            continue
        spy_above_ema50 = bool(spy_df["close"].iloc[spy_idx] > spy_ema50[spy_idx])
        if not spy_above_ema50:
            continue  # todo el capital libre de Core queda en Caucion hoy

        candidates = []
        for symbol, df in series.items():
            if symbol in open_slots:
                continue
            idx = pos_by_date[symbol].get(date)
            if idx is None or idx < LOOKBACK - 1 or idx + 1 >= len(df):
                continue  # sin fila hoy, sin contexto para Chronos, o sin ninguna rueda futura para simular la salida
            window = df["close"].iloc[idx - LOOKBACK + 1: idx + 1]
            try:
                p10_pct, p50_pct, p90_pct = _chronos_pct_changes(client, window, HORIZON_DAYS)
            except Exception:
                continue
            chronos_calls += 1
            band_width = p90_pct - p10_pct
            if band_width <= 0:
                continue
            dispersion_ratio = abs(p50_pct) / band_width
            candidates.append({
                "symbol": symbol, "idx": idx, "dispersion_ratio": dispersion_ratio,
                "atr_pct": float(df["atr_pct"].iloc[idx]),
                "efficiency_score": signals.compute_efficiency_score(p50_pct, dispersion_ratio),
            })

        ranked = signals.rank_core_candidates(candidates, spy_above_ema50, top_n=free_slots, min_score=core_min_score)
        for cand in ranked:
            symbol = cand["symbol"]
            df = series[symbol]
            idx = cand["idx"]
            entry_price = float(df["close"].iloc[idx])
            raw_weight = signals.compute_core_position_weight(cand["atr_pct"], cand["dispersion_ratio"])
            if raw_weight <= 0:
                continue
            effective_weight = raw_weight / signals.CORE_TOP_N

            future_rows = df.iloc[idx + 1: idx + 1 + CORE_MAX_HOLD_DAYS]
            hold_days, exit_price, return_pct, exit_reason = simulate_position_exit(
                future_rows, entry_price, CORE_TP_FLAT_PCT, CORE_STOP_FLAT_PCT, CORE_MAX_HOLD_DAYS,
                trailing=True, atr_pct_value=cand["atr_pct"],
                tp_atr_mult=CORE_TP_ATR_MULT, stop_atr_mult=CORE_STOP_ATR_MULT,
                stop_is_ceiling=True, commission_pct=ROUND_TRIP_COMMISSION_PCT,
            )
            if hold_days == 0:
                continue

            trade_days = _trade_daily_returns(df, idx, entry_price, hold_days, exit_price, commission_pct=ROUND_TRIP_COMMISSION_PCT)
            for day_date, day_ret in trade_days:
                if day_date in contributions:
                    contributions[day_date] += effective_weight * day_ret
                    committed_weight[day_date] += effective_weight
            open_slots[symbol] = trade_days[-1][0] if trade_days else date
            trades.append({
                "engine": "core", "symbol": symbol, "entry_date": date, "exit_date": open_slots[symbol],
                "hold_days": hold_days, "return_pct": return_pct, "weight": effective_weight,
                "exit_reason": exit_reason, "efficiency_score": cand["efficiency_score"],
            })
            free_slots -= 1

    daily_df = pd.DataFrame(
        [(d, contributions[d] + max(0.0, 1.0 - committed_weight[d]) * daily_caucion_return_pct,
          max(0.0, 1.0 - committed_weight[d])) for d in master_dates],
        columns=["date", "return_pct", "idle_weight"],
    ).set_index("date")

    return {
        "engine": "core", "daily_returns_pct": daily_df["return_pct"], "idle_weight": daily_df["idle_weight"],
        "trades": trades, "skipped": skipped, "chronos_calls": chronos_calls,
    }


def _fit_satellite_hmm(conn, master_dates):
    """Entrena el HMM de regimen (ver portfolio/regime.py::fit_hmm_regime_model)
    SOLO con historia ANTERIOR al inicio de la ventana de backtest
    (master_dates[0]) -- cero look-ahead: el modelo nunca ajusta sus
    parametros mirando datos de la propia ventana que se esta evaluando.
    Devuelve (modelo, state_labels, log_returns_completa, vix_completa) --
    las series COMPLETAS (pre-ventana + ventana) para que classify_regime_hmm
    pueda hacer el forward-filter causal dia a dia DENTRO de la ventana."""
    spy_series = macro_data.get_global_trend_series(conn, "SPY")
    vix_series = macro_data.get_vix_series(conn)
    combined_index = spy_series.index.union(vix_series.index)
    spy_full = spy_series.reindex(combined_index).ffill()
    vix_full = vix_series.reindex(combined_index).ffill()
    log_returns_full = np.log(spy_full / spy_full.shift(1))

    window_start = pd.Timestamp(master_dates[0])
    train_mask = combined_index < window_start
    train_returns = log_returns_full[train_mask].dropna()
    train_vix = vix_full[train_mask].reindex(train_returns.index)
    valid = train_vix.notna()
    train_returns, train_vix = train_returns[valid], train_vix[valid]

    model, state_labels = regime.fit_hmm_regime_model(train_returns, train_vix)
    return model, state_labels, log_returns_full, vix_full


def _hmm_scale_by_date(conn, master_dates):
    """Escala suave (ver portfolio/regime.py::hmm_position_scale) por cada
    fecha de master_dates -- forward-filter causal: cada fecha usa SOLO la
    historia con indice <= esa fecha (ver classify_regime_hmm)."""
    hmm_model, hmm_state_labels, log_returns_full, vix_full = _fit_satellite_hmm(conn, master_dates)
    scale_by_date = {}
    for d in master_dates:
        ts = pd.Timestamp(d)
        history_mask = log_returns_full.index <= ts
        returns_so_far = log_returns_full[history_mask].dropna()
        vix_so_far = vix_full.reindex(returns_so_far.index)
        valid = vix_so_far.notna()
        returns_so_far, vix_so_far = returns_so_far[valid], vix_so_far[valid]
        if len(returns_so_far) < 2:
            scale_by_date[d] = 1.0
            continue
        _label, probs = regime.classify_regime_hmm(hmm_model, hmm_state_labels, returns_so_far, vix_so_far)
        scale_by_date[d] = regime.hmm_position_scale(probs)
    return scale_by_date


def _ccl_scale_by_date(conn, master_dates):
    """Escala por Z-Score de CCL (ver portfolio/regime.py::ccl_exposure_scale,
    pedido explicito: "Z-Score de CCL para modular la exposición") por cada
    fecha de master_dates -- MISMA formula de z-score que el meta-modelo
    (portfolio/features.py::_rolling_zscore sobre el retorno log diario del
    CCL, reusada tal cual, nunca duplicada con otro criterio). Forward-fill +
    ventana rolling son intrinsecamente causales (cada fila solo pesa
    observaciones <= esa fila), mismo criterio de cero look-ahead del resto
    del proyecto. 1.0 (sin modular) para fechas sin suficiente cobertura de
    CCL -- dato faltante nunca se trata como "salto inusual"."""
    ccl_series = macro_data.get_ccl_series(conn)
    ccl_aligned = features._align_ffill(ccl_series, pd.DatetimeIndex(master_dates))
    ccl_log_return = np.log(ccl_aligned / ccl_aligned.shift(1))
    ccl_zscore_series = features._rolling_zscore(ccl_log_return)
    ccl_zscore_series.index = master_dates
    return {
        d: regime.ccl_exposure_scale(
            float(ccl_zscore_series.loc[d]) if pd.notna(ccl_zscore_series.loc[d]) else None
        )
        for d in master_dates
    }


def simulate_satellite_engine(universe, conn, lookback_days=CORE_BACKTEST_LOOKBACK_DAYS, use_hmm_regime=False):
    """Simulador del Motor SATELITE: escanea cada ticker de forma
    INDEPENDIENTE (sin ranking cross-sectional, sin Chronos-Bolt, ver
    portfolio/short_term_engine.py) y abre una posicion cuando cualquiera de
    las 3 tacticas dispara, hasta SATELLITE_MAX_POSITIONS simultaneas. Si
    varios tickers disparan el mismo dia: primero se aplica el filtro de
    concentracion sectorial (ver portfolio/signals.py::CORE_SECTOR_GROUPS,
    reusado tal cual de Core -- mismo riesgo real que motivo ese filtro ahi,
    GGAL+BMA abriendo juntos y tocando stop juntos) descartando todos salvo
    el de mayor peso (ATR/Kelly) por grupo sectorial; despues, si siguen
    sobrando mas candidatos que slots libres, se prioriza por ese mismo peso
    de mayor a menor. "Salida Rapida Acelerada": TP
    fijo (configurable, ver portfolio/config.py::get_satellite_exit_params),
    trailing stop a 1.8xATR con TECHO configurable (nunca pierde mas que
    stop_loss_pct en una operacion, ver simulate_position_exit con
    stop_is_ceiling=True), expiracion a 3 ruedas.

    No llama a Chronos-Bolt -- mucho mas barato que Core, corre sobre todo
    lookback_days sin necesidad de acotar llamadas.

    use_hmm_regime=False (default, SIN CAMBIOS de comportamiento respecto a
    antes de esta opcion) usa regime.satellite_blocked -- corte RIGIDO de
    VIX que bloquea el 100% de las compras nuevas en regimen de alta
    volatilidad. use_hmm_regime=True (ver conversacion, "Fine-Tuning LoRA +
    HMM", SOLO usado por la validacion historica comparativa, nunca por el
    motor en vivo) reemplaza ese corte por un escalado CONTINUO del tamaño
    de posicion segun la probabilidad de régimen de pánico del HMM (ver
    portfolio/regime.py::hmm_position_scale) -- nunca bloquea del todo, solo
    achica la apuesta proporcionalmente a esa probabilidad."""
    exit_params = capital_config.get_satellite_exit_params(conn)
    series = {}
    skipped = {}
    min_rows = SATELLITE_MIN_WARMUP_ROWS + lookback_days
    for symbol, asset_type in universe:
        df = _prepare_aligned_series(symbol, asset_type, conn, min_rows)
        if df is None:
            skipped[symbol] = f"historial insuficiente (necesita >= {min_rows} ruedas)"
            continue
        df["atr_pct"] = indicators.atr_pct(df, CORE_ATR_WINDOW).to_numpy()
        series[symbol] = df

    pos_by_date = {sym: {d: i for i, d in enumerate(df["date"])} for sym, df in series.items()}
    master_dates = sorted(set().union(*[set(df["date"]) for df in series.values()]))[-lookback_days:]

    hurdle_tna = float(db.get_signal_config(conn).get("caucion_reference_tna") or 0.0)
    daily_caucion_return_pct = yield_engine.daily_caucion_return_pct(hurdle_tna)
    max_slot_weight = 1.0 / SATELLITE_MAX_POSITIONS

    # Regimen de mercado (ver conversacion, portfolio/regime.py) -- VIX/SPY
    # alineados a master_dates con el MISMO forward-fill causal que ya usa
    # portfolio/features.py (ultimo valor YA conocido a esa fecha, nunca
    # interpolado hacia el futuro). Bloquea COMPRAS NUEVAS del Satelite en
    # regimen de alta volatilidad (VIX > regime.VIX_HIGH_THRESHOLD) -- las
    # posiciones ya abiertas siguen su salida normal, esto solo afecta si se
    # abren posiciones nuevas ese dia.
    master_dates_idx = pd.DatetimeIndex(master_dates)
    spy_aligned = features._align_ffill(macro_data.get_global_trend_series(conn, "SPY"), master_dates_idx)
    vix_aligned = features._align_ffill(macro_data.get_vix_series(conn), master_dates_idx)
    spy_aligned.index = master_dates
    vix_aligned.index = master_dates
    regime_by_date = {
        d: regime.classify_regime(spy_aligned.loc[:d], vix_aligned.loc[d] if pd.notna(vix_aligned.loc[d]) else None)
        for d in master_dates
    }
    # regime_by_date se calcula SIEMPRE (barato, y queda en el registro de
    # cada trade como diagnostico comun a ambas variantes) -- hmm_scale solo
    # se usa de verdad si use_hmm_regime=True.
    hmm_scale_by_date = _hmm_scale_by_date(conn, master_dates) if use_hmm_regime else {}
    # ccl_scale SIEMPRE activo (a diferencia del HMM, no es una variante de
    # validacion -- ver conversacion, "Z-Score de CCL para modular la
    # exposición" se pide como parte del clasificador de regimen en
    # produccion, no solo para la comparativa historica).
    ccl_scale_by_date = _ccl_scale_by_date(conn, master_dates)

    # Kelly Criterium (ver conversacion, short_term_engine.kelly_fraction) --
    # usa el win_rate de la ULTIMA corrida conocida del Satelite (ultimo
    # edge medido, nunca el de ESTA MISMA corrida -- seria circular). None en
    # la primera corrida de la vida del proyecto, antes de que exista algun
    # resumen persistido -- sin eso, Kelly no aplica ningun tope adicional,
    # el tamaño queda gobernado solo por el de volatilidad (ATR).
    prior_summary = db.get_dual_engine_backtest_summary(conn)
    prior_satellite_metrics = prior_summary["satellite_metrics"] if prior_summary and prior_summary.get("satellite_metrics") else {}
    kelly_cap = short_term_engine.kelly_fraction(
        prior_satellite_metrics.get("win_rate_pct"), prior_satellite_metrics.get("profit_factor"),
    )

    contributions = {d: 0.0 for d in master_dates}
    committed_weight = {d: 0.0 for d in master_dates}
    open_until = {}  # symbol -> exit_date
    # sector -> ultimo dia con contribucion de una posicion de ese sector
    # (ver mas abajo, seen_groups) -- separado de open_until porque este se
    # vacia del simbolo el mismo dia de su exit_date (para liberar el SLOT
    # ese dia), pero ese exit_date todavia tuvo contribucion de capital de
    # ese sector -- sin este dict por separado, un segundo activo del mismo
    # sector podia abrir el mismo dia que el primero cerraba (hallazgo real
    # verificado: GGAL cerrando y BMA abriendo el mismo dia, doble apuesta
    # al sector bancario ese dia puntual).
    sector_occupied_until = {}
    trades = []
    regime_blocked_entries = 0

    for date in master_dates:
        for symbol in [s for s, exit_date in open_until.items() if exit_date <= date]:
            del open_until[symbol]

        free_slots = SATELLITE_MAX_POSITIONS - len(open_until)
        if free_slots <= 0:
            continue

        # use_hmm_regime=True: nunca bloquea del todo (ver docstring) -- el
        # HMM dampenea el tamaño de posicion mas abajo, no la apertura.
        blocked_today = False if use_hmm_regime else regime.satellite_blocked(regime_by_date[date])
        hmm_scale_today = hmm_scale_by_date.get(date, 1.0) if use_hmm_regime else 1.0
        ccl_scale_today = ccl_scale_by_date.get(date, 1.0)

        # Se juntan TODOS los candidatos de HOY antes de abrir nada (en vez
        # de abrir apenas dispara cada ticker, orden de universe) para poder
        # aplicar el mismo filtro de concentracion sectorial que ya tiene
        # Core (ver portfolio/signals.py::CORE_SECTOR_GROUPS -- hallazgo real
        # ahi fue GGAL+BMA abriendo el mismo dia y tocando stop juntos,
        # mismo riesgo le aplica a Satelite, que hasta ahora no tenia ningun
        # filtro de este tipo). Empate se desempata por peso (ATR/Kelly) ya
        # que Satelite no tiene un efficiency_score como Core.
        candidates_today = []
        for symbol, _asset_type in universe:
            if symbol not in series or symbol in open_until:
                continue
            df = series[symbol]
            idx = pos_by_date[symbol].get(date)
            if idx is None or idx + 1 >= len(df):
                continue

            triggered = short_term_engine.evaluate_short_term_signals(df.iloc[:idx + 1])
            if not triggered:
                continue
            if blocked_today:
                regime_blocked_entries += 1
                continue

            entry_price = float(df["close"].iloc[idx])
            atr_pct_at_entry = float(df["atr_pct"].iloc[idx])
            # Volatility Targeting + Kelly (ver conversacion): el tamaño real
            # es el MENOR de los dos topes -- nunca mas grande que lo que
            # permite la volatilidad propia del activo NI que lo que permite
            # el edge historico medido del Satelite.
            weight = short_term_engine.compute_satellite_position_weight(atr_pct_at_entry, max_slot_weight)
            if kelly_cap is not None:
                weight = min(weight, kelly_cap)
            weight *= hmm_scale_today
            weight *= ccl_scale_today
            candidates_today.append({
                "symbol": symbol, "df": df, "idx": idx, "entry_price": entry_price,
                "atr_pct_at_entry": atr_pct_at_entry, "weight": weight, "tactic": triggered[0]["tactic"],
            })

        candidates_today.sort(key=lambda c: c["weight"], reverse=True)
        # seen_groups arranca con los sectores YA ocupados por posiciones
        # todavia vigentes HOY (sector_occupied_until >= date), no solo los
        # candidatos de hoy -- hallazgo real verificado (ver conversacion):
        # con el dedup limitado a "mismo dia" (el mismo criterio que usa
        # Core, ver portfolio/signals.py::rank_core_candidates) seguian
        # abriendose YPFD y PAMP, o GGAL y BMA, en dias DISTINTOS con
        # ventanas de holding superpuestas -- "maximo 1 activo por sector"
        # significa en todo momento, no solo en la misma rueda.
        seen_groups = {g for g, last_date in sector_occupied_until.items() if last_date >= date}
        deduped_today = []
        for c in candidates_today:
            group = signals.CORE_SECTOR_GROUPS.get(c["symbol"])
            if group is not None:
                if group in seen_groups:
                    continue
                seen_groups.add(group)
            deduped_today.append(c)

        for c in deduped_today:
            if free_slots <= 0:
                break
            symbol, df, idx = c["symbol"], c["df"], c["idx"]
            entry_price, atr_pct_at_entry, weight = c["entry_price"], c["atr_pct_at_entry"], c["weight"]
            future_rows = df.iloc[idx + 1: idx + 1 + short_term_engine.SATELLITE_MAX_HOLD_DAYS]
            hold_days, exit_price, return_pct, exit_reason = simulate_position_exit(
                future_rows, entry_price,
                exit_params["take_profit_pct"], exit_params["stop_loss_pct"],
                short_term_engine.SATELLITE_MAX_HOLD_DAYS, trailing=True,
                atr_pct_value=atr_pct_at_entry, stop_atr_mult=short_term_engine.SATELLITE_TRAILING_STOP_ATR_MULT,
                stop_is_ceiling=True, commission_pct=ROUND_TRIP_COMMISSION_PCT,
            )
            if hold_days == 0:
                continue

            trade_days = _trade_daily_returns(df, idx, entry_price, hold_days, exit_price, commission_pct=ROUND_TRIP_COMMISSION_PCT)
            for day_date, day_ret in trade_days:
                if day_date in contributions:
                    contributions[day_date] += weight * day_ret
                    committed_weight[day_date] += weight
            open_until[symbol] = trade_days[-1][0] if trade_days else date
            group = signals.CORE_SECTOR_GROUPS.get(symbol)
            if group is not None:
                sector_occupied_until[group] = open_until[symbol]
            trades.append({
                "engine": "satellite", "symbol": symbol, "entry_date": date, "exit_date": open_until[symbol],
                "hold_days": hold_days, "return_pct": return_pct, "weight": weight,
                "exit_reason": exit_reason, "tactic": c["tactic"],
                "regime": regime_by_date[date],
            })
            free_slots -= 1

    daily_df = pd.DataFrame(
        [(d, contributions[d] + max(0.0, 1.0 - committed_weight[d]) * daily_caucion_return_pct,
          max(0.0, 1.0 - committed_weight[d])) for d in master_dates],
        columns=["date", "return_pct", "idle_weight"],
    ).set_index("date")

    return {
        "engine": "satellite", "daily_returns_pct": daily_df["return_pct"], "idle_weight": daily_df["idle_weight"],
        "trades": trades, "skipped": skipped, "regime_blocked_entries": regime_blocked_entries,
    }


def _annualized_return_ars_pct(daily_returns_pct, ccl_series, periods_per_year=252):
    """Retorno anualizado en ARS -- DERIVADO del retorno en USD ya simulado
    (la UNICA moneda en la que se decide/mide la operatoria desde la
    refactorizacion financiera, ver conversacion), nunca re-simulado: se
    re-expresa el MISMO resultado en la otra moneda componiendo con la
    variacion de la cotizacion CCL en el mismo periodo --
    equity_ars(t) = equity_usd(t) * ccl(t)/ccl(t0) (ccl_series: serie real
    historica, ver portfolio/macro_data.py::get_ccl_series, no una implicita
    por ticker). None si no hay cobertura de CCL para alguna fecha del
    rango -- nunca se inventa un tipo de cambio faltante."""
    if ccl_series is None or ccl_series.empty or len(daily_returns_pct) == 0:
        return None
    dates = daily_returns_pct.index
    combined_index = ccl_series.index.union(dates)
    ccl_aligned = ccl_series.reindex(combined_index).ffill().reindex(dates)
    if ccl_aligned.isna().any():
        return None
    ccl_values = ccl_aligned.to_numpy()
    if ccl_values[0] <= 0:
        return None
    equity_usd = (1 + daily_returns_pct.to_numpy() / 100).cumprod()
    equity_ars = equity_usd * (ccl_values / ccl_values[0])
    n_days = len(equity_ars)
    if n_days == 0 or equity_ars[-1] <= 0:
        return None
    return float((equity_ars[-1] ** (periods_per_year / n_days) - 1) * 100)


def _compute_engine_metrics(daily_returns_pct, idle_weight, trades, ccl_series=None, periods_per_year=252):
    """Win Rate/Profit Factor sobre TRADES individuales; Sharpe/Max
    Drawdown/Retorno Anualizado (USD) sobre la curva de equity diaria
    COMPLETA de la porcion de capital del motor (trades + caucion ociosa) --
    es la metrica correcta para esto, no un promedio por-trade.

    Si se pasa ccl_series, agrega 'annualized_return_ars_pct' (ver
    _annualized_return_ars_pct) -- reporte dual USD/ARS pedido en la
    refactorizacion financiera. Sharpe/Max Drawdown se reportan SOLO en USD
    (la moneda en la que de verdad se decide/mide la operatoria) -- el
    retorno anualizado es la unica metrica que tiene sentido ver en ambas,
    ya que ARS/USD difieren estructuralmente por la devaluacion del periodo."""
    n_trades = len(trades)
    win_rate = profit_factor = None
    if n_trades:
        returns = np.array([t["return_pct"] for t in trades])
        win_rate = float((returns > 0).mean() * 100)
        gains = returns[returns > 0].sum()
        losses = -returns[returns < 0].sum()
        if losses > 0:
            profit_factor = float(gains / losses)
        elif gains > 0:
            profit_factor = float("inf")

    daily = daily_returns_pct.to_numpy() / 100
    sharpe = None
    # Bug real encontrado (ver conversacion): con una serie CONSTANTE (ej.
    # 0 trades, 100% del periodo en Caucion con la misma tasa diaria todos
    # los dias), numpy.std(ddof=1) no da exactamente 0.0 -- queda un residuo
    # de redondeo de punto flotante (~1e-21 observado), y dividir una media
    # minuscula por ESO explota a un Sharpe absurdo (~4.8e16). 1e-9 es un
    # piso muy por debajo de cualquier desvio diario real (~0.01% en
    # adelante) pero muy por encima del ruido de punto flotante.
    STD_EPSILON = 1e-9
    daily_std = daily.std(ddof=1)
    if len(daily) > 1 and daily_std > STD_EPSILON:
        sharpe = float((daily.mean() / daily_std) * np.sqrt(periods_per_year))

    equity = (1 + daily).cumprod()
    max_drawdown_pct = annualized_return_pct = None
    if len(equity):
        running_max = np.maximum.accumulate(equity)
        drawdown = (equity - running_max) / running_max
        max_drawdown_pct = float(drawdown.min() * 100)
        if equity[-1] > 0:
            annualized_return_pct = float((equity[-1] ** (periods_per_year / len(equity)) - 1) * 100)

    pct_time_caucion = float(idle_weight.mean() * 100) if len(idle_weight) else None
    annualized_return_ars_pct = _annualized_return_ars_pct(daily_returns_pct, ccl_series, periods_per_year)

    return {
        "n_trades": n_trades,
        "n_days": len(daily),
        "win_rate_pct": round(win_rate, 1) if win_rate is not None else None,
        "profit_factor": profit_factor,
        "sharpe_ratio": round(sharpe, 2) if sharpe is not None else None,
        "max_drawdown_pct": round(max_drawdown_pct, 1) if max_drawdown_pct is not None else None,
        # USD: la moneda real de la operatoria desde la refactorizacion
        # financiera (ver conversacion) -- el nombre de la clave se mantiene
        # por compatibilidad con lo ya persistido/consumido (API, UI).
        "annualized_return_pct": round(annualized_return_pct, 1) if annualized_return_pct is not None else None,
        "annualized_return_ars_pct": round(annualized_return_ars_pct, 1) if annualized_return_ars_pct is not None else None,
        "pct_time_caucion": round(pct_time_caucion, 1) if pct_time_caucion is not None else None,
    }


def print_dual_engine_report(core_result, satellite_result, capital_allocation=None, ccl_series=None):
    """capital_allocation: {'core_pct','satellite_pct'} vigente (ver
    portfolio/config.py) -- si se pasa, agrega una fila GLOBAL que blendea
    las 2 curvas diarias con ese split real (ver portfolio/yield_engine.py,
    item 3 del pedido: "registrar en la curva de patrimonio global el
    retorno... del capital ocioso de ambos motores"). Sin esto (None), la
    fila GLOBAL se omite -- nunca se inventa un split si no se pidio.

    ccl_series: serie historica real de CCL (ver
    portfolio/macro_data.py::get_ccl_series) -- habilita la columna "Retorno
    Anual ARS %" (refactorizacion financiera, ver conversacion). Sin esto,
    esa columna se imprime vacia."""
    core_metrics = _compute_engine_metrics(
        core_result["daily_returns_pct"], core_result["idle_weight"], core_result["trades"], ccl_series=ccl_series
    )
    satellite_metrics = _compute_engine_metrics(
        satellite_result["daily_returns_pct"], satellite_result["idle_weight"], satellite_result["trades"],
        ccl_series=ccl_series,
    )
    rows = [("CORE", core_metrics), ("SATELITE", satellite_metrics)]

    global_metrics = None
    if capital_allocation is not None:
        global_daily = yield_engine.blend_engine_returns(
            core_result["daily_returns_pct"], satellite_result["daily_returns_pct"],
            capital_allocation["core_pct"], capital_allocation["satellite_pct"],
        )
        # blend_engine_returns pondera por capital y suma -- el mismo calculo
        # sirve para promediar fracciones ociosas (idle_weight), no solo
        # retornos: se escala x100/÷100 para reusar la misma funcion sin
        # duplicar la logica de ponderacion en dos lugares.
        global_idle = yield_engine.blend_engine_returns(
            core_result["idle_weight"] * 100, satellite_result["idle_weight"] * 100,
            capital_allocation["core_pct"], capital_allocation["satellite_pct"],
        ) / 100
        global_trades = core_result["trades"] + satellite_result["trades"]
        global_metrics = _compute_engine_metrics(global_daily, global_idle, global_trades, ccl_series=ccl_series)
        rows.append((f"GLOBAL ({capital_allocation['core_pct']:.0f}/{capital_allocation['satellite_pct']:.0f})", global_metrics))

    cols = [
        ("Motor", 18), ("Retorno Anual USD %", 20), ("Retorno Anual ARS %", 20), ("Win Rate", 9),
        ("Profit Factor", 14), ("Sharpe USD", 11), ("Max DD USD", 11), ("% Tiempo Caucion", 17), ("Trades", 8),
    ]
    header = " | ".join(name.ljust(w) for name, w in cols)
    print("\n" + "=" * len(header))
    print("REPORTE COMPARATIVO -- Motor CORE (institucional, Chronos cross-sectional) vs. "
          "Motor SATELITE (corto plazo, reglas tecnicas) -- analisis y operatoria en USD CCL")
    print(f"Ventana: ultimas {CORE_BACKTEST_LOOKBACK_DAYS} ruedas de cada activo del universo. "
          "Retorno Anual ARS = el MISMO resultado en USD recompuesto con la variacion real del CCL en el "
          "periodo (nunca un trade re-simulado en ARS). % Tiempo Caucion = promedio ponderado por capital "
          "de la fraccion ociosa. Umbral Core: "
          f"efficiency_score > max({signals.CORE_MIN_EFFICIENCY_SCORE}, percentil {signals.CORE_PERCENTILE_THRESHOLD} del dia).")
    print("=" * len(header))
    print(header)
    print("-" * len(header))
    for label, m in rows:
        row = [
            label.ljust(cols[0][1]),
            _fmt_pct(m["annualized_return_pct"]).ljust(cols[1][1]),
            _fmt_pct(m["annualized_return_ars_pct"]).ljust(cols[2][1]),
            _fmt_pct(m["win_rate_pct"]).ljust(cols[3][1]),
            _fmt_pf(m["profit_factor"]).ljust(cols[4][1]),
            _fmt_sharpe(m["sharpe_ratio"]).ljust(cols[5][1]),
            _fmt_pct(m["max_drawdown_pct"]).ljust(cols[6][1]),
            _fmt_pct(m["pct_time_caucion"]).ljust(cols[7][1]),
            str(m["n_trades"]).ljust(cols[8][1]),
        ]
        print(" | ".join(row))
    print("=" * len(header))
    core_skip = ", ".join(f"{s} ({m})" for s, m in core_result["skipped"].items()) or "ninguno"
    sat_skip = ", ".join(f"{s} ({m})" for s, m in satellite_result["skipped"].items()) or "ninguno"
    print(f"CORE: {core_metrics['n_trades']} trades sobre {core_metrics['n_days']} ruedas, "
          f"{core_result['chronos_calls']} llamadas a Chronos-Bolt. Sin historial suficiente: {core_skip}.")
    print(f"SATELITE: {satellite_metrics['n_trades']} trades sobre {satellite_metrics['n_days']} ruedas. "
          f"Sin historial suficiente: {sat_skip}.")
    if global_metrics:
        print(f"GLOBAL: blend de Core+Satelite ponderado por la asignacion de capital vigente "
              f"({capital_allocation['core_pct']:.1f}% / {capital_allocation['satellite_pct']:.1f}%, "
              f"ver portfolio/config.py -- ajustable via PUT /api/portfolio/signal-config o /set_core).")
    return core_metrics, satellite_metrics, global_metrics


def _split_in_out_sample(result, in_sample_days=IN_SAMPLE_DAYS):
    """Separa un resultado de simulate_core_engine/simulate_satellite_engine
    en IN-SAMPLE (las primeras in_sample_days fechas de la ventana, ya
    miradas al calibrar umbrales en iteraciones anteriores) y OUT-OF-SAMPLE
    (el resto, nunca miradas antes de este reporte) -- ver conversacion.

    El retorno diario se corta por fecha CALENDARIO estricta (lo que
    efectivamente paso ese dia de capital, sin importar cuando abrio el
    trade que lo genero). Un trade se asigna al periodo de su fecha de
    ENTRADA -- si abrio en IN-SAMPLE y cerro ya entrado el OUT-OF-SAMPLE, el
    trade (Win Rate/Profit Factor) cuenta como IN-SAMPLE, pero el retorno
    diario que genero en ruedas OUT-OF-SAMPLE SI se cuenta ahi (es capital
    real movido en esos dias, aunque la decision de entrar fuera anterior)."""
    dates = result["daily_returns_pct"].index
    if len(dates) <= in_sample_days:
        raise ValueError(f"la ventana tiene {len(dates)} ruedas, no alcanza para separar {in_sample_days} in-sample")
    split_date = dates[in_sample_days - 1]
    in_sample_dates = dates[:in_sample_days]
    out_sample_dates = dates[in_sample_days:]

    def _slice(date_range):
        return {
            "daily_returns_pct": result["daily_returns_pct"].loc[date_range],
            "idle_weight": result["idle_weight"].loc[date_range],
        }

    in_sample = _slice(in_sample_dates)
    out_sample = _slice(out_sample_dates)
    in_sample["trades"] = [t for t in result["trades"] if t["entry_date"] <= split_date]
    out_sample["trades"] = [t for t in result["trades"] if t["entry_date"] > split_date]
    return in_sample, out_sample


def print_in_out_sample_report(core_result, satellite_result, capital_allocation=None,
                                in_sample_days=IN_SAMPLE_DAYS, out_of_sample_days=OUT_OF_SAMPLE_DAYS,
                                variant_label=None):
    """Reporte Out-Of-Sample (ver conversacion): separa la ventana de
    CORE_BACKTEST_LOOKBACK_DAYS en IN-SAMPLE (primeras in_sample_days, lo ya
    mirado al calibrar CORE_MIN_EFFICIENCY_SCORE/satellite_take_profit_pct/
    satellite_stop_loss_pct en iteraciones anteriores) y OUT-OF-SAMPLE
    (ultimas out_of_sample_days, nunca miradas antes) -- solo USD (el pedido
    es puntual sobre esto, no agrega la pata ARS del reporte principal)."""
    core_in, core_out = _split_in_out_sample(core_result, in_sample_days)
    satellite_in, satellite_out = _split_in_out_sample(satellite_result, in_sample_days)

    rows = []
    for label, in_r, out_r in (("CORE", core_in, core_out), ("SATELITE", satellite_in, satellite_out)):
        rows.append((label, f"IN-SAMPLE ({in_sample_days}r)", _compute_engine_metrics(in_r["daily_returns_pct"], in_r["idle_weight"], in_r["trades"])))
        rows.append((label, f"OUT-OF-SAMPLE ({out_of_sample_days}r)", _compute_engine_metrics(out_r["daily_returns_pct"], out_r["idle_weight"], out_r["trades"])))

    if capital_allocation is not None:
        global_in_daily = yield_engine.blend_engine_returns(
            core_in["daily_returns_pct"], satellite_in["daily_returns_pct"],
            capital_allocation["core_pct"], capital_allocation["satellite_pct"],
        )
        global_in_idle = yield_engine.blend_engine_returns(
            core_in["idle_weight"] * 100, satellite_in["idle_weight"] * 100,
            capital_allocation["core_pct"], capital_allocation["satellite_pct"],
        ) / 100
        global_out_daily = yield_engine.blend_engine_returns(
            core_out["daily_returns_pct"], satellite_out["daily_returns_pct"],
            capital_allocation["core_pct"], capital_allocation["satellite_pct"],
        )
        global_out_idle = yield_engine.blend_engine_returns(
            core_out["idle_weight"] * 100, satellite_out["idle_weight"] * 100,
            capital_allocation["core_pct"], capital_allocation["satellite_pct"],
        ) / 100
        global_label = f"GLOBAL ({capital_allocation['core_pct']:.0f}/{capital_allocation['satellite_pct']:.0f})"
        rows.append((global_label, f"IN-SAMPLE ({in_sample_days}r)", _compute_engine_metrics(
            global_in_daily, global_in_idle, core_in["trades"] + satellite_in["trades"]
        )))
        rows.append((global_label, f"OUT-OF-SAMPLE ({out_of_sample_days}r)", _compute_engine_metrics(
            global_out_daily, global_out_idle, core_out["trades"] + satellite_out["trades"]
        )))

    cols = [
        ("Motor", 18), ("Periodo", 24), ("Retorno Anual USD %", 20), ("Win Rate", 9),
        ("Profit Factor", 14), ("Sharpe USD", 11), ("Max DD USD", 11), ("Trades", 8),
    ]
    header = " | ".join(name.ljust(w) for name, w in cols)
    print("\n" + "=" * len(header))
    titulo = "VALIDACION OUT-OF-SAMPLE"
    if variant_label:
        titulo += f" -- {variant_label}"
    print(f"{titulo} -- las reglas/umbrales se calibraron mirando TODA la ventana en "
          "iteraciones anteriores (ver conversacion); este corte separa que tan bien sostiene el resultado "
          "en datos que nunca se miraron al elegir esos parametros.")
    print("=" * len(header))
    print(header)
    print("-" * len(header))
    last_motor = None
    for label, periodo, m in rows:
        if last_motor is not None and label != last_motor:
            print("-" * len(header))
        last_motor = label
        row = [
            label.ljust(cols[0][1]),
            periodo.ljust(cols[1][1]),
            _fmt_pct(m["annualized_return_pct"]).ljust(cols[2][1]),
            _fmt_pct(m["win_rate_pct"]).ljust(cols[3][1]),
            _fmt_pf(m["profit_factor"]).ljust(cols[4][1]),
            _fmt_sharpe(m["sharpe_ratio"]).ljust(cols[5][1]),
            _fmt_pct(m["max_drawdown_pct"]).ljust(cols[6][1]),
            str(m["n_trades"]).ljust(cols[7][1]),
        ]
        print(" | ".join(row))
    print("=" * len(header))


def run_dual_engine_backtest(universe=UNIVERSE, lookback_days=CORE_BACKTEST_LOOKBACK_DAYS):
    client = ChronosServiceClient()
    try:
        client.health()
    except Exception as exc:
        print(f"ERROR: no se pudo contactar al microservicio de prediccion ({exc}). "
              f"Levantalo primero (ver ./dev.sh o services/predictor/README.md).", file=sys.stderr)
        sys.exit(1)

    conn = db.get_connection()
    try:
        print(f"Simulando Motor CORE sobre {len(universe)} activos (ultimas {lookback_days} ruedas)...")
        t0 = time.time()
        core_result = simulate_core_engine(universe, client, conn, lookback_days)
        print(f"-- Core: {len(core_result['trades'])} trades, {core_result['chronos_calls']} llamadas "
              f"a Chronos-Bolt ({time.time() - t0:.1f}s)")

        print(f"Simulando Motor SATELITE sobre {len(universe)} activos (ultimas {lookback_days} ruedas)...")
        t0 = time.time()
        satellite_result = simulate_satellite_engine(universe, conn, lookback_days)
        print(f"-- Satelite: {len(satellite_result['trades'])} trades ({time.time() - t0:.1f}s)")

        allocation = capital_config.get_capital_allocation(conn)
        ccl_series = macro_data.get_ccl_series(conn)
        core_metrics, satellite_metrics, global_metrics = print_dual_engine_report(
            core_result, satellite_result, capital_allocation=allocation, ccl_series=ccl_series
        )
        if lookback_days == CORE_BACKTEST_LOOKBACK_DAYS:
            # El split IN/OUT-OF-SAMPLE asume la ventana estandar de 300
            # ruedas (200/100, ver IN_SAMPLE_DAYS/OUT_OF_SAMPLE_DAYS) -- si
            # alguien corre esto con otro lookback_days, se omite en vez de
            # tirar un ValueError por una ventana mas chica que 200.
            print_in_out_sample_report(core_result, satellite_result, capital_allocation=allocation)
        # Persistido para que GET /api/backtest/summary (ver webui/app.py) no
        # tenga que re-correr el backtest (minutos + llamadas a Chronos-Bolt)
        # en cada request HTTP -- sirve el ULTIMO resultado conocido.
        db.save_dual_engine_backtest_summary(
            conn, run_at=datetime.now().isoformat(), lookback_days=lookback_days,
            core_metrics=core_metrics, satellite_metrics=satellite_metrics,
            global_metrics=global_metrics, capital_allocation=allocation,
        )
    finally:
        conn.close()

    return core_metrics, satellite_metrics, global_metrics


# --- Validacion historica: Chronos Base vs Chronos LoRA + HMM --------------
#
# Ver conversacion ("Fine-Tuning LoRA + HMM"): compara el dual-engine COMPLETO
# (Core+Satelite, blend GLOBAL) usando dos variantes del Motor CORE/Satelite:
#   - CHRONOS BASE: Core con el modelo base (sin adaptador), Satelite con el
#     corte RIGIDO de VIX existente (regime.satellite_blocked) -- EXACTAMENTE
#     lo que ya corre run_dual_engine_backtest, sin ningun cambio.
#   - CHRONOS LORA + HMM: Core con el adaptador LoRA entrenado (ver
#     finetune/finetune_chronos.py), Satelite con el escalado SUAVE de
#     Position Sizing por HMM (ver portfolio/regime.py::hmm_position_scale)
#     en vez del corte rigido.
#
# Ambas variantes de Core usan model/local_client.py::LocalChronosClient (EN
# PROCESO, nunca el servicio HTTP real de :8001) para que la UNICA diferencia
# entre las dos corridas sea el adaptador LoRA -- nunca se toca ni compite
# con el servicio de produccion.
def run_lora_hmm_oos_validation(universe=UNIVERSE, lookback_days=CORE_BACKTEST_LOOKBACK_DAYS,
                                 lora_adapter_path="finetune/adapters/chronos-bolt-small-lora"):
    from model.local_client import LocalChronosClient

    conn = db.get_connection()
    try:
        allocation = capital_config.get_capital_allocation(conn)

        print(f"Cargando Chronos-Bolt BASE (sin adaptador, en proceso, device local)...")
        t0 = time.time()
        base_client = LocalChronosClient(lora_adapter_path=None)
        print(f"-- listo ({time.time() - t0:.1f}s)")

        print(f"Cargando Chronos-Bolt + LoRA ({lora_adapter_path})...")
        t0 = time.time()
        lora_client = LocalChronosClient(lora_adapter_path=lora_adapter_path)
        print(f"-- listo ({time.time() - t0:.1f}s)")

        print(f"\n[CHRONOS BASE] Simulando Motor CORE sobre {len(universe)} activos ({lookback_days} ruedas)...")
        t0 = time.time()
        core_base = simulate_core_engine(universe, base_client, conn, lookback_days)
        print(f"-- Core base: {len(core_base['trades'])} trades, {core_base['chronos_calls']} llamadas ({time.time() - t0:.1f}s)")
        print("[CHRONOS BASE] Simulando Motor SATELITE (corte rigido de VIX)...")
        t0 = time.time()
        satellite_base = simulate_satellite_engine(universe, conn, lookback_days, use_hmm_regime=False)
        print(f"-- Satelite base: {len(satellite_base['trades'])} trades ({time.time() - t0:.1f}s)")

        print(f"\n[CHRONOS LORA + HMM] Simulando Motor CORE sobre {len(universe)} activos ({lookback_days} ruedas)...")
        t0 = time.time()
        core_lora = simulate_core_engine(universe, lora_client, conn, lookback_days)
        print(f"-- Core LoRA: {len(core_lora['trades'])} trades, {core_lora['chronos_calls']} llamadas ({time.time() - t0:.1f}s)")
        print("[CHRONOS LORA + HMM] Simulando Motor SATELITE (escalado suave por HMM)...")
        t0 = time.time()
        satellite_hmm = simulate_satellite_engine(universe, conn, lookback_days, use_hmm_regime=True)
        print(f"-- Satelite HMM: {len(satellite_hmm['trades'])} trades ({time.time() - t0:.1f}s)")

        print_in_out_sample_report(
            core_base, satellite_base, capital_allocation=allocation,
            in_sample_days=IN_SAMPLE_DAYS, out_of_sample_days=OUT_OF_SAMPLE_DAYS, variant_label="CHRONOS BASE",
        )
        print_in_out_sample_report(
            core_lora, satellite_hmm, capital_allocation=allocation,
            in_sample_days=IN_SAMPLE_DAYS, out_of_sample_days=OUT_OF_SAMPLE_DAYS, variant_label="CHRONOS LORA + HMM",
        )
    finally:
        conn.close()


# --- Backtest unificado MULTIMOTOR (5 motores) + CPCV a nivel PORTFOLIO ----
#
# Ver conversacion ("simulacion historica multimotor completa") -- a
# diferencia de cpcv_evaluate_pooled (arriba, CPCV sobre el POOL de anchors
# del meta-modelo, re-entrenando un RandomForest por combinacion), esto NO
# entrena nada: Core/Satelite/Pairs/Factor son reglas YA FIJAS (ver sus
# propios simuladores), asi que "purgar" en el sentido de Lopez de Prado (una
# fila de entrenamiento cuyo LABEL depende de datos del bloque de test) no
# tiene un analogo literal aca -- no hay label, hay retorno diario YA
# REALIZADO. Lo que SI se preserva del espiritu de CPCV: en vez de reportar
# el Sharpe de UN solo camino historico (que puede ser una muestra con
# suerte/mala suerte), se generan MUCHOS caminos independientes combinando
# distintos subconjuntos de bloques temporales, con un EMBARGO en cada borde
# de bloque (declarado, ver UNIFIED_CPCV_EMBARGO_DAYS) para amortiguar el
# artefacto de empalmar periodos no contiguos cuando una posicion de
# varios dias (Core hasta 15 ruedas, Satelite hasta 3, Pairs hasta 30) cae
# justo en el borde -- esa DISTRIBUCION de Sharpe por camino es la que
# alimenta el Deflated Sharpe Ratio (deflated_sharpe_ratio, reusada tal cual,
# es generica: no le importa si "trade_returns" son retornos por trade o
# retornos diarios del portfolio combinado).
UNIFIED_YIELD_SLEEVE_PCT = 10.0  # declarado -- porcion de capital dedicada a Caucion PURA (0% exposicion de mercado) en este reporte, para poder medir su impacto por separado
# Declarado, mismo espiritu que UNIFIED_YIELD_SLEEVE_PCT -- el Motor de
# Arbitraje de Canje (ver portfolio/arbitrage.py) todavia no tiene una
# asignacion de capital propia en produccion (es una alerta tactica, no un
# motor que gestiona una porcion fija de la cartera en la Web UI), asi que
# este % es SOLO para poder medir su aporte en este reporte unificado.
UNIFIED_CANJE_SLEEVE_PCT = 5.0
UNIFIED_CPCV_GROUPS = 6
UNIFIED_CPCV_TEST_GROUPS = 2
UNIFIED_CPCV_EMBARGO_DAYS = 3  # declarado -- cubre el hold maximo del Satelite (3 ruedas), no los 15/30 de Core/Pairs (eso ensancharia demasiado el embargo para una ventana de 300 ruedas)


def _unified_weights(conn):
    """Pesos RENORMALIZADOS a 100% para este reporte -- core_pct/satellite_pct
    (ya suman 100, ver get_capital_allocation) + pairs_pct/factor_pct
    (nominales, ver conversacion: NO forman parte del mismo 100% en
    produccion) + UNIFIED_YIELD_SLEEVE_PCT (nuevo, solo para este reporte) se
    normalizan juntos -- declarado explicitamente que esto es un supuesto de
    reporte, no necesariamente la asignacion real configurada hoy (que no
    tiene todavia un quinto motor de Caucion pura separado)."""
    allocation = capital_config.get_capital_allocation(conn)
    raw = {
        "core": allocation["core_pct"], "satellite": allocation["satellite_pct"],
        "pairs": capital_config.get_pairs_allocation_pct(conn),
        "factor": capital_config.get_factor_allocation_pct(conn),
        "canje": UNIFIED_CANJE_SLEEVE_PCT,
        "yield": UNIFIED_YIELD_SLEEVE_PCT,
    }
    total = sum(raw.values())
    return {k: v / total * 100.0 for k, v in raw.items()}


def _yield_sleeve_series(conn, master_dates):
    """Serie del 5to motor (Caucion pura, 0% exposicion de mercado) -- mismo
    daily_caucion_return_pct que ya usan los otros 4 simuladores para SU
    capital ocioso, aca aplicado al 100% de la porcion de este sleeve, todos
    los dias (idle_weight=1.0 siempre, nunca invertido en nada mas)."""
    hurdle_tna = float(db.get_signal_config(conn).get("caucion_reference_tna") or 0.0)
    daily_pct = yield_engine.daily_caucion_return_pct(hurdle_tna)
    returns = pd.Series(daily_pct, index=master_dates, dtype=float)
    idle = pd.Series(1.0, index=master_dates, dtype=float)
    return {"engine": "yield", "daily_returns_pct": returns, "idle_weight": idle, "trades": []}


def blend_n_engine_returns(results_by_engine, weights_pct):
    """Generaliza portfolio/yield_engine.py::blend_engine_returns a N motores
    -- MISMO criterio (fecha sin dato de un motor = 0% ese dia para ESE
    motor, nunca un retorno inventado, ver docstring de la version de 2).
    results_by_engine: {nombre: resultado de simulate_*_engine}. weights_pct:
    {nombre: % 0-100}, se asume YA normalizado (ver _unified_weights)."""
    frames = {
        name: r["daily_returns_pct"].rename(name) for name, r in results_by_engine.items() if len(r["daily_returns_pct"])
    }
    if not frames:
        return pd.Series(dtype=float)
    combined = pd.concat(frames.values(), axis=1).fillna(0.0)
    blended = pd.Series(0.0, index=combined.index)
    for name in frames:
        blended += combined[name] * (weights_pct.get(name, 0.0) / 100.0)
    return blended


def simulate_unified_backtest(universe=UNIVERSE, conn=None, lookback_days=CORE_BACKTEST_LOOKBACK_DAYS):
    """Corre los 5 motores BACKTESTEABLES (Core/Satelite/Pairs/Factor/Canje) +
    el sleeve de Caucion pura sobre la MISMA ventana, y los combina con
    _unified_weights. Dolar Sintetico/Futuros ROFEX y Carry Trade LECAP/CER
    (ver conversacion) NO estan incluidos aca -- sus fuentes de datos (feed
    de A3 Mercados vía websocket en vivo, curva de LECAPs vigentes de
    argentinadatos.com) son solo del ESTADO ACTUAL, sin historial descargable,
    asi que no hay con que simular una serie de retornos diarios pasados sin
    inventar precios; run_unified_cpcv_backtest reporta su señal EN VIVO por
    separado en vez de fabricar un backtest. Devuelve dict con
    'daily_returns_pct'/'idle_weight' (promedio ponderado de ociosidad,
    informativo) ya BLENDEADOS, 'trades' (concatenado de los motores activos,
    mismo criterio que run_dual_engine_backtest para el GLOBAL Core+Satelite),
    'per_engine' (el resultado crudo de cada motor, para poder reportar el
    impacto individual de cada uno) y 'weights' (los pesos normalizados
    usados)."""
    from portfolio import arbitrage, factor_investing, pairs_trading  # import tardio -- evita el ciclo pairs_trading/factor_investing/arbitrage -> backtest -> pairs_trading/factor_investing/arbitrage

    own_conn = conn is None
    conn = conn or db.get_connection()
    try:
        client = ChronosServiceClient()
        core_result = simulate_core_engine(universe, client, conn, lookback_days)
        satellite_result = simulate_satellite_engine(universe, conn, lookback_days)
        pairs_result = pairs_trading.simulate_pairs_engine(conn, lookback_days=lookback_days)
        factor_result = factor_investing.simulate_factor_engine(conn, lookback_days=lookback_days)
        canje_result = arbitrage.simulate_canje_arbitrage_engine(conn, lookback_days=lookback_days)

        per_engine = {
            "core": core_result, "satellite": satellite_result,
            "pairs": pairs_result, "factor": factor_result, "canje": canje_result,
        }
        # master_dates del blend = union de fechas de los 4 (el sleeve de
        # Caucion se arma DESPUES, sobre esa misma union -- un motor sin dato
        # ese dia ya cuenta 0% via blend_n_engine_returns, nunca excluye el
        # dia entero).
        all_dates = sorted(set().union(*(set(r["daily_returns_pct"].index) for r in per_engine.values() if len(r["daily_returns_pct"]))))
        yield_result = _yield_sleeve_series(conn, all_dates)
        per_engine["yield"] = yield_result

        weights = _unified_weights(conn)
        blended_returns = blend_n_engine_returns(per_engine, weights)
        idle_frames = {
            name: r["idle_weight"].rename(name) for name, r in per_engine.items() if len(r["idle_weight"])
        }
        blended_idle = pd.concat(idle_frames.values(), axis=1).fillna(1.0) if idle_frames else pd.DataFrame()
        idle_weighted = pd.Series(0.0, index=blended_idle.index)
        for name in idle_frames:
            idle_weighted += blended_idle[name] * (weights.get(name, 0.0) / 100.0)

        # Factor Investing no tiene "trades" en el sentido de entrada/salida
        # con un return_pct propio -- son eventos de REBALANCEO (que
        # holdings entraron/salieron, ver factor_investing.py::simulate_factor_engine),
        # la rotacion continua no se presta a un P&L "por trade" discreto
        # como Core/Satelite/Pairs. _compute_engine_metrics (Win Rate/Profit
        # Factor) necesita return_pct -- se filtran los que no lo tienen en
        # vez de romper, su retorno YA esta capturado correctamente en
        # daily_returns_pct (lo que de verdad mueve Sharpe/Max DD/retorno
        # anualizado, los numeros centrales de este reporte).
        all_trades = (
            core_result["trades"] + satellite_result["trades"] + pairs_result["trades"] + canje_result["trades"]
            + [t for t in factor_result["trades"] if "return_pct" in t]
        )
        return {
            "daily_returns_pct": blended_returns, "idle_weight": idle_weighted,
            "trades": all_trades, "per_engine": per_engine, "weights": weights,
        }
    finally:
        if own_conn:
            conn.close()


def run_suggested_allocation_report(conn=None, method="max_sharpe"):
    """Fase 0 del plan de unificacion (ver conversacion: "la asignacion
    dinamica de capital por motor, ya construida en
    risk.py::compute_dynamic_strategy_allocation, nunca se conecto a nada").
    Corre los mismos 5 motores que simulate_unified_backtest, les pasa su
    daily_returns_pct real a compute_dynamic_strategy_allocation, y guarda el
    resultado junto con la asignacion ACTUALMENTE CONFIGURADA (_unified_weights,
    la misma base de comparacion que ya usa este reporte) para poder mostrar
    "sugerido vs. configurado" sin correr nada en el momento de la consulta.

    'yield' (el sleeve de Caucion pura, 0% exposicion de mercado) se excluye
    a proposito de los motores a optimizar -- no es una ESTRATEGIA con la que
    competir por capital, es el destino por defecto del capital ocioso de las
    demas; compute_dynamic_strategy_allocation(risk_parity/min_variance/
    max_sharpe) asume que todos los inputs son estrategias de riesgo
    comparable, incluirlo sesgaria la optimizacion hacia asignarle todo el
    peso (retorno positivo con volatilidad ~0).

    Pensado para correrse manualmente (python3 -m portfolio.backtest
    allocation), nunca sincronico en un request HTTP -- implica correr
    simulate_unified_backtest completo (5 motores, puede tardar varios
    minutos). Puramente informativo: NUNCA escribe core_allocation_pct/
    pairs_allocation_pct/factor_allocation_pct, eso lo sigue decidiendo el
    usuario a mano en Ajustes."""
    own_conn = conn is None
    conn = conn or db.get_connection()
    try:
        from portfolio import risk  # import tardio -- mismo criterio que arbitrage/factor_investing/pairs_trading mas arriba

        unified = simulate_unified_backtest(conn=conn)
        returns_by_engine = {
            name: r["daily_returns_pct"] for name, r in unified["per_engine"].items()
            if name != "yield" and len(r["daily_returns_pct"])
        }
        result = risk.compute_dynamic_strategy_allocation(returns_by_engine, method=method)
        configured_weights = {k: v for k, v in unified["weights"].items() if k != "yield"}
        db.save_suggested_allocation(
            conn, run_at=datetime.now().isoformat(), method=method,
            result=result, configured_weights=configured_weights,
        )
        return result
    finally:
        if own_conn:
            conn.close()


def _cpcv_blended_paths(dates, n_groups=UNIFIED_CPCV_GROUPS, n_select=UNIFIED_CPCV_TEST_GROUPS,
                         embargo_days=UNIFIED_CPCV_EMBARGO_DAYS):
    """Genera C(n_groups, n_select) 'caminos' -- cada uno, la concatenacion
    CRONOLOGICA de n_select bloques (de los n_groups bloques contiguos en
    que se parte `dates`), con los primeros embargo_days de CADA bloque
    salvo el primero de la ventana completa EXCLUIDOS del camino (buffer
    contra el artefacto de empalmar periodos no contiguos, ver docstring de
    la seccion). Devuelve lista de listas de fechas."""
    n = len(dates)
    edges = np.linspace(0, n, n_groups + 1).astype(int)
    blocks = [dates[edges[i]:edges[i + 1]] for i in range(n_groups)]
    paths = []
    for combo in itertools.combinations(range(n_groups), n_select):
        path_dates = []
        for block_id in sorted(combo):
            block = blocks[block_id]
            # El embargo solo se aplica si este bloque NO es el primero de
            # TODA la ventana (ese no tiene un "periodo previo" del que
            # protegerse) -- mismo criterio de borde que el resto del proyecto.
            start = embargo_days if block_id > 0 else 0
            path_dates.extend(block[start:])
        if path_dates:
            paths.append(path_dates)
    return paths


def _print_live_only_alert_status(conn):
    """Dólar Sintético/Futuros ROFEX y Carry Trade LECAP/CER (ver
    conversacion) no tienen historial descargable (feed de A3 Mercados es un
    snapshot en vivo por websocket, la curva de LECAPs vigentes de
    argentinadatos.com tampoco tiene serie historica) -- en vez de fabricar
    un backtest con datos que no existen, esta funcion consulta y muestra su
    señal EN VIVO de hoy, declarado explicitamente como tal."""
    from portfolio import fixed_income, synthetic_dollar

    print("\nDólar Sintético (ROFEX) y Renta Fija/Carry Trade -- SIN historial descargable (feed en vivo, no series "
          "historicas), se muestra su señal de HOY en vez de un backtest fabricado:")
    try:
        synth_signal = synthetic_dollar.compute_synthetic_dollar_signal(conn)
    except Exception as e:
        synth_signal = None
        print(f"  Dólar Sintético: error consultando el feed en vivo ({e})")
    else:
        if synth_signal:
            print(f"  Dólar Sintético: señal HOY en {synth_signal['contract']} -- tasa implícita "
                  f"{_fmt_pct(synth_signal['implied_tna_pct'])} TNA vs. Caución+margen "
                  f"{_fmt_pct(synth_signal['caucion_tna_pct'] + synth_signal['margin_pct'])} TNA "
                  f"(spread +{_fmt_pct(synth_signal['spread_pct'])}).")
        else:
            print("  Dólar Sintético: sin señal hoy (spread actual no supera Caución + margen, o sin datos de futuros disponibles).")

    try:
        carry_signals = fixed_income.compute_carry_trade_rotation_signals(conn)
    except Exception as e:
        carry_signals = []
        print(f"  Carry Trade LECAP/CER: error consultando la fuente en vivo ({e})")
    if carry_signals:
        top = carry_signals[0]
        print(f"  Carry Trade LECAP/CER: {len(carry_signals)} pares comparados hoy -- mejor spread en "
              f"{top['bucket_label']} (rota hacia {top['better']}, +{_fmt_pct(top['spread_pct'])}/mes).")
    else:
        print("  Carry Trade LECAP/CER: sin pares comparables hoy (sin datos de LECAPs/CER disponibles).")


def run_unified_cpcv_backtest(universe=UNIVERSE, lookback_days=CORE_BACKTEST_LOOKBACK_DAYS):
    """Punto de entrada: `python -m portfolio.backtest unified`. Simula los 6
    motores backtesteables (Core/Satelite/Pairs/Factor/Canje + sleeve de
    Caucion), blendea, corre CPCV a nivel de retorno diario del portfolio
    combinado, y reporta DSR + Max Drawdown + Retorno Anualizado (USD y ARS)
    + desglose por motor + impacto del barrido de Caucion + señal en vivo de
    Dólar Sintético/Carry Trade (ver conversacion, estos 2 ultimos no tienen
    historial descargable)."""
    conn = db.get_connection()
    try:
        print(f"Simulando backtest unificado (6 motores) sobre {len(universe)} activos ({lookback_days} ruedas)...")
        t0 = time.time()
        unified = simulate_unified_backtest(universe, conn, lookback_days)
        print(f"-- listo ({time.time() - t0:.1f}s) -- pesos: " +
              " / ".join(f"{k} {v:.1f}%" for k, v in unified["weights"].items()))

        ccl_series = macro_data.get_ccl_series(conn)

        dates = list(unified["daily_returns_pct"].index)
        paths = _cpcv_blended_paths(dates)
        path_metrics = []
        for path_dates in paths:
            path_returns = unified["daily_returns_pct"].loc[path_dates]
            path_idle = unified["idle_weight"].loc[path_dates]
            m = _compute_engine_metrics(path_returns, path_idle, [])  # trades=[] -- win rate/PF no aplican a un sub-camino combinado, solo Sharpe/MaxDD/retorno
            if m["sharpe_ratio"] is not None:
                path_metrics.append(m["sharpe_ratio"])

        full_metrics = _compute_engine_metrics(
            unified["daily_returns_pct"], unified["idle_weight"], unified["trades"], ccl_series=ccl_series,
        )
        dsr_result = deflated_sharpe_ratio(unified["daily_returns_pct"].dropna().to_numpy(), path_metrics) if path_metrics else None

        # Impacto del barrido de Caucion: mismo backtest pero con el sleeve
        # 'yield' puesto a 0% y renormalizado entre los demas -- la diferencia
        # de retorno/Sharpe es el impacto REAL medido, no una estimacion a mano.
        weights_no_yield = {k: v for k, v in unified["weights"].items() if k != "yield"}
        total_no_yield = sum(weights_no_yield.values())
        weights_no_yield = {k: v / total_no_yield * 100.0 for k, v in weights_no_yield.items()} if total_no_yield else {}
        returns_no_yield = blend_n_engine_returns(unified["per_engine"], weights_no_yield)
        idle_no_yield_frames = {
            n_: r["idle_weight"].rename(n_) for n_, r in unified["per_engine"].items() if n_ != "yield" and len(r["idle_weight"])
        }
        idle_no_yield_df = pd.concat(idle_no_yield_frames.values(), axis=1).fillna(1.0) if idle_no_yield_frames else pd.DataFrame()
        idle_no_yield = pd.Series(0.0, index=idle_no_yield_df.index)
        for n_ in idle_no_yield_frames:
            idle_no_yield += idle_no_yield_df[n_] * (weights_no_yield.get(n_, 0.0) / 100.0)
        metrics_no_yield = _compute_engine_metrics(returns_no_yield, idle_no_yield, [], ccl_series=ccl_series)

        caucion_tna = float(db.get_signal_config(conn).get("caucion_reference_tna") or 0.0)

        header = "=" * 100
        print("\n" + header)
        print("BACKTEST UNIFICADO MULTIMOTOR (Core + Satelite + Pairs + Factor + Canje + Caucion) -- CPCV + Deflated Sharpe Ratio")
        print(header)
        print(f"Retorno Anual USD: {_fmt_pct(full_metrics['annualized_return_pct'])} · "
              f"Retorno Anual ARS: {_fmt_pct(full_metrics['annualized_return_ars_pct'])} · "
              f"Sharpe: {_fmt_sharpe(full_metrics['sharpe_ratio'])} · "
              f"Max Drawdown: {_fmt_pct(full_metrics['max_drawdown_pct'])} · {full_metrics['n_trades']} trades combinados")
        print(f"CPCV: {len(path_metrics)}/{len(paths)} caminos evaluables ({UNIFIED_CPCV_GROUPS} grupos / {UNIFIED_CPCV_TEST_GROUPS} de test) -- "
              + (f"Sharpe por camino: media {np.mean(path_metrics):.2f} · desvio {np.std(path_metrics, ddof=1):.2f} · "
                 f"min {np.min(path_metrics):.2f} · max {np.max(path_metrics):.2f}" if path_metrics else "sin caminos evaluables"))
        if dsr_result is None:
            print("Deflated Sharpe Ratio: no calculable (evidencia insuficiente)")
        else:
            veredicto = "ESTADISTICAMENTE SIGNIFICATIVO" if dsr_result["dsr"] >= 0.95 else "NO significativo"
            print(f"Deflated Sharpe Ratio (DSR): {dsr_result['dsr']:.4f} -- {veredicto} "
                  f"(SR_hat={dsr_result['sr_hat']} · SR0={dsr_result['sr0']} · n={dsr_result['n_trade_returns']} dias)")

        print(f"\nDesglose por motor (peso en este reporte / retorno anual USD / Sharpe / Max DD / trades):")
        for name, result in unified["per_engine"].items():
            # Factor Investing reporta eventos de REBALANCEO sin 'return_pct'
            # propio (ver docstring de simulate_unified_backtest/all_trades
            # mas arriba) -- mismo filtro aca, nunca un KeyError por motor.
            engine_trades = [t for t in result["trades"] if "return_pct" in t]
            m = _compute_engine_metrics(result["daily_returns_pct"], result["idle_weight"], engine_trades, ccl_series=ccl_series)
            weight = unified["weights"].get(name, 0.0)
            print(f"  {name.upper():10s} {weight:5.1f}% -- Retorno Anual USD: {_fmt_pct(m['annualized_return_pct'])} · "
                  f"Sharpe: {_fmt_sharpe(m['sharpe_ratio'])} · Max DD: {_fmt_pct(m['max_drawdown_pct'])} · {m['n_trades']} trades")

        print(f"\nImpacto del barrido de Caucion (sleeve {unified['weights']['yield']:.1f}%, TNA de referencia {_fmt_pct(caucion_tna)}): "
              f"CON Caucion {_fmt_pct(full_metrics['annualized_return_pct'])}/{_fmt_sharpe(full_metrics['sharpe_ratio'])} Sharpe vs. "
              f"SIN Caucion {_fmt_pct(metrics_no_yield['annualized_return_pct'])}/{_fmt_sharpe(metrics_no_yield['sharpe_ratio'])} Sharpe -- "
              f"la liquidez ociosa (capital sin posicion abierta en ningun motor, {_fmt_pct(full_metrics['pct_time_caucion'])} del tiempo "
              f"ponderado) rinde esta tasa en vez de quedar parada sin generar nada.")

        _print_live_only_alert_status(conn)
        print(header)
        return {
            "full_metrics": full_metrics, "dsr": dsr_result, "weights": unified["weights"],
            "metrics_no_yield": metrics_no_yield,
        }
    finally:
        conn.close()


if __name__ == "__main__":
    if "dual" in sys.argv[1:]:
        run_dual_engine_backtest()
    elif "lora" in sys.argv[1:]:
        run_lora_hmm_oos_validation()
    elif "unified" in sys.argv[1:]:
        run_unified_cpcv_backtest()
    elif "allocation" in sys.argv[1:]:
        run_suggested_allocation_report()
    else:
        run_full_report()
