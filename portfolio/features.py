"""Feature engineering tabular para el meta-modelo de ensemble (ver
services/predictor/meta_learner.py y portfolio/backtest.py).

Historia de las fases (ver conversacion):
  Fase 2: 16 features, un RandomForest POR TICKER -- overfitting confirmado
    (ensemble empeoraba 53.2% -> 47.5%). Se podo a 4 features (chronos x2 +
    dist_ema50/volume_ratio) + Cross-Sectional Pooling (un modelo GLOBAL) ->
    el ensemble dejo de empeorar (50.1% -> 50.6%, marginal pero ya no daño).
  Fase 3 (multivariada, este modulo): se agregan microestructura adicional
    (log_return, volatility_10d, ya estacionarias por construccion) y 3
    canales macro/exogenos (SPY, CCL, brecha cambiaria, ver
    portfolio/macro_data.py) normalizados con z-score ROLLING. Quedan dos
    sets de columnas lado a lado para poder medir el aporte marginal del
    macro en el backtest (ver V2_FEATURE_COLUMNS/V3_FEATURE_COLUMNS mas
    abajo), no un reemplazo ciego de lo anterior.

Garantia de cero look-ahead: toda funcion de este archivo es estrictamente
causal -- cada fila i solo usa valores con indice <= i (rolling/ewm hacia
atras, z-score con ventana movil, nunca estadisticos de la serie completa).
Se calcula UNA sola vez sobre la serie completa (barato) y el walk-forward
de backtest.py simplemente para de leerla en cada corte temporal --
recalcular sobre un sufijo mas corto de la misma serie daria exactamente
los mismos valores en cada fila ya calculada, porque ninguna celda depende
de filas futuras a la propia.
"""
import numpy as np
import pandas as pd
from statsmodels.tsa.stattools import adfuller

from portfolio import fetchers, indicators, macro_data
from portfolio.forecasting import build_model_input

EMA_TREND_WINDOW = 50
VOLUME_SHORT_WINDOW = 5
VOLUME_LONG_WINDOW = 20
REALIZED_VOL_WINDOW = 10
RSI_WINDOW = 14
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9
ATR_WINDOW = 14

ROLLING_NORM_WINDOW = 60  # ventana del z-score movil de los canales macro
ROLLING_NORM_MIN_PERIODS = 20

# FFD (Fractional Differentiation con Ventana Fija, Lopez de Prado -- ver
# conversacion, "evolucion integral" pedida): d ~ 0.35 fue el valor PEDIDO
# explicitamente, pero verificado EMPIRICAMENTE con ADF real (ver
# conversacion) que NO alcanza -- sobre GGAL/AAPL/AL30D el p-value a d=0.35
# dio 0.25/0.63/0.03, ninguno < 0.01 (GGAL recien pasa en d=0.65). Mantener
# un d fijo global hubiera violado el objetivo explicito del propio pedido
# ("asegurando la estacionariedad requerida p < 0.01") en la mayoria de los
# tickers reales -- en vez de fabricar un resultado o mantener un d que no
# cumple, se implementa el metodo CANONICO completo: find_min_ffd_order()
# busca, POR TICKER, el d minimo que de verdad logra p < 0.01 (preserva la
# maxima memoria posible mientras sigue garantizando estacionariedad, que es
# el objetivo real declarado). FFD_ORDER queda como el d de ARRANQUE/referencia
# del barrido (no el que se usa finalmente).
FFD_ORDER = 0.35
FFD_ORDER_GRID = tuple(round(d, 2) for d in np.arange(0.05, 1.01, 0.05))
FFD_SIGNIFICANCE = 0.01
FFD_TAU = 1e-4

# CEDEARs cuyo simbolo coincide 1:1 con su ticker real de Wall Street --
# VERIFICADO contra yfinance (ver conversacion, no asumido a ciegas): un
# CEDEAR en general puede listarse bajo un simbolo distinto a su subyacente,
# pero para TODO el universo cedear de portfolio/backtest.py::UNIVERSE hoy,
# data912 usa el mismo ticker que yfinance/NYSE/NASDAQ. Si se suma a futuro
# un cedear cuyo simbolo NO sea su subyacente real, agregar el mapeo aca
# (symbol_local -> symbol_subyacente) en vez de asumir que coinciden -- por
# ahora, mapa identidad explicito, nunca una conversion automatica sin
# verificar.
CEDEAR_DIRECT_UNDERLYING_SYMBOLS = {
    "SPY", "QQQ", "AAPL", "NVDA", "TSLA", "MSFT", "AMZN", "GOOGL", "META", "MELI",
}
WARMUP_ROWS_FOR_UNDERLYING = 60  # minimo de historia del subyacente antes de confiar en el indicador tecnico


def _ffd_weights(d=FFD_ORDER, tau=FFD_TAU):
    """Pesos de la FFD (Lopez de Prado, 'Advances in Financial Machine
    Learning' cap. 5): w_0=1, w_k = -w_{k-1} * (d-k+1)/k -- se trunca la
    serie de pesos apenas |w_k| < tau (a diferencia de la diferenciacion
    fraccionaria 'expansiva' clasica, el ancho de ventana queda FIJO en
    len(pesos) desde aca, nunca crece con la longitud de la serie). Orden
    devuelto: [w_mas_viejo, ..., w_1, w_0] para alinear con una ventana de
    pandas.rolling(...).apply(raw=True) (que entrega el array en orden
    cronologico, mas viejo primero)."""
    weights = [1.0]
    k = 1
    while True:
        w_k = -weights[-1] * (d - k + 1) / k
        if abs(w_k) < tau:
            break
        weights.append(w_k)
        k += 1
    return np.array(weights[::-1])


def fracdiff_ffd(price_series, d=FFD_ORDER, tau=FFD_TAU):
    """Serie fraccionalmente diferenciada (ventana fija) del LOG precio --
    se diferencia el log, no el nivel crudo (practica estandar: el log
    precio es la version aditiva del proceso multiplicativo, diferenciar el
    nivel no tiene la misma lectura de 'retorno fraccional'). NaN en las
    primeras len(pesos)-1 filas (ventana todavia incompleta) -- cero
    look-ahead: la fila i solo usa log-precios con indice <= i, mismo
    criterio del resto de este archivo."""
    weights = _ffd_weights(d, tau)
    width = len(weights)
    log_price = np.log(price_series.replace(0, np.nan).astype(float))
    return log_price.rolling(width).apply(lambda w: float(np.dot(w, weights)), raw=True)


def check_stationarity(series, significance=0.01):
    """Test de Dickey-Fuller Aumentado (ADF, statsmodels) sobre una serie YA
    diferenciada -- confirma (o no) que fracdiff_ffd logro estacionariedad
    antes de alimentar la feature al meta-modelo. H0 del ADF = la serie
    TIENE raiz unitaria (no estacionaria); p < significance rechaza H0.
    Devuelve None si hay muy pocos puntos validos (ADF no es confiable con
    poca muestra) -- nunca se inventa un resultado."""
    clean = series.dropna()
    if len(clean) < 20:
        return None
    adf_stat, p_value, *_ = adfuller(clean, autolag="AIC", result_object=False)
    return {
        "adf_stat": float(adf_stat), "p_value": float(p_value),
        "is_stationary": bool(p_value < significance), "n_obs": len(clean),
    }


def find_min_ffd_order(price_series, d_grid=FFD_ORDER_GRID, tau=FFD_TAU, significance=FFD_SIGNIFICANCE):
    """Metodo CANONICO de Lopez de Prado (ver conversacion): barrido
    ASCENDENTE de d buscando el PRIMERO (el minimo) que logra ADF
    p < significance sobre fracdiff_ffd(d) -- preserva la maxima memoria
    historica posible (d mas chico = menos diferenciado, mas memoria) que
    siga garantizando la estacionariedad real que exige el ADF. Se prefiere
    esto a un d fijo global porque, verificado empiricamente (ver
    conversacion), un d fijo tipico (~0.35) NO alcanza para varios tickers
    reales de este proyecto.

    Devuelve (d_encontrado, resultado_adf) -- o (None, None) si ni el d mas
    alto del grid logra estacionariedad (no deberia pasar en la practica, un
    precio financiero es I(1) como mucho, y d=1.0 es diferenciacion entera
    completa), nunca se inventa un d que no se probo de verdad."""
    for d in d_grid:
        diffed = fracdiff_ffd(price_series, d=d, tau=tau)
        result = check_stationarity(diffed, significance=significance)
        if result and result["is_stationary"]:
            return float(d), result
    return None, None


def _technical_indicator_matrix(df):
    """RSI/MACD/ATR normalizado (pedidos explicitamente, ver conversacion)
    -- RVOL ya existe como volume_ratio_5_20 en _ticker_microstructure_matrix,
    no se duplica aca. macd_hist_pct normaliza el histograma MACD (crudo,
    en unidades de precio) dividiendolo por el close -- sin esto, el MACD
    crudo NO es comparable entre tickers de distinta escala de precio
    (ej. META ~700 vs AAPL ~340), mismo criterio de '_pct' que ya usa el
    resto de este archivo (dist_ema50_pct, atr_pct). df: columnas
    open/high/low/close."""
    close = df["close"]
    rsi_14 = indicators.rsi(close, RSI_WINDOW)
    macd_df = indicators.macd(close, MACD_FAST, MACD_SLOW, MACD_SIGNAL)
    macd_hist_pct = macd_df["hist"] / close.replace(0, np.nan) * 100
    atr_pct_14 = indicators.atr_pct(df, ATR_WINDOW)
    # d adaptativo por ticker (ver find_min_ffd_order) -- NO el d fijo ~0.35
    # pedido originalmente, que verificado con ADF real no alcanza
    # estacionariedad en varios tickers (ver conversacion). Si ni el grid
    # completo logra p<0.01 (found_d is None), se usa FFD_ORDER como ultimo
    # recurso declarado (mejor que NaN la columna entera, la serie queda
    # igual de "lo mas estacionaria posible dentro de lo probado").
    found_d, _adf_result = find_min_ffd_order(close)
    ffd_log_price = fracdiff_ffd(close, d=found_d if found_d is not None else FFD_ORDER)
    return pd.DataFrame(
        {
            "rsi_14": rsi_14,
            "macd_hist_pct": macd_hist_pct,
            "atr_pct_14": atr_pct_14,
            "ffd_log_price": ffd_log_price,
        },
        index=df.index,
    )


def _rolling_zscore(series, window=ROLLING_NORM_WINDOW, min_periods=ROLLING_NORM_MIN_PERIODS):
    """Z-score con media/desvio MOVILES, nunca globales -- un z-score con
    estadisticos de toda la serie usaria informacion futura para normalizar
    las filas viejas (data leakage); con ventana movil, la fila i solo mira
    dias <= i."""
    mean = series.rolling(window, min_periods=min_periods).mean()
    std = series.rolling(window, min_periods=min_periods).std().replace(0, np.nan)
    return (series - mean) / std


def _align_ffill(exogenous_series, target_dates):
    """Alinea una serie exogena (indexada por fecha) contra las fechas del
    ticker objetivo, llevando hacia adelante el ultimo valor YA conocido a
    esa fecha (nunca interpola entre dos puntos ni mira para adelante) --
    mismo criterio que ya usa portfolio/forecasting.py::_ccl_rate_for_dates
    (reescrito vectorizado con pandas reindex+ffill en vez de bisect a
    mano)."""
    target_index = pd.DatetimeIndex(target_dates)
    if exogenous_series.empty:
        return pd.Series(index=target_index, dtype=float)
    combined_index = exogenous_series.index.union(target_index)
    return exogenous_series.reindex(combined_index).ffill().reindex(target_index)


def _ticker_microstructure_matrix(df):
    """b) Microestructura del ticker -- log_return/volatility_10d (ya
    estacionarias por construccion: retorno log y vol realizada, no
    niveles) + dist_ema50_pct (tendencia primaria) + volume_ratio_5_20
    (confirmacion de volumen). df: columnas open/high/low/close/volume,
    orden cronologico ascendente."""
    close = df["close"]
    log_return = np.log(close / close.shift(1))
    volatility_10d = log_return.rolling(REALIZED_VOL_WINDOW, min_periods=2).std() * np.sqrt(252) * 100

    ema50 = indicators.ema(close, EMA_TREND_WINDOW)
    dist_ema50_pct = (close - ema50) / ema50.replace(0, np.nan) * 100

    volume = df["volume"].astype(float)
    vol_short = volume.rolling(VOLUME_SHORT_WINDOW, min_periods=1).mean()
    vol_long = volume.rolling(VOLUME_LONG_WINDOW, min_periods=1).mean()
    volume_ratio_5_20 = vol_short / vol_long.replace(0, np.nan)

    return pd.DataFrame(
        {
            "log_return": log_return,
            "volatility_10d": volatility_10d,
            "dist_ema50_pct": dist_ema50_pct,
            "volume_ratio_5_20": volume_ratio_5_20,
        },
        index=df.index,
    )


def _macro_exogenous_matrix(conn, dates):
    """c) Canales macro exogenos -- spy_zscore/ccl_zscore (z-score ROLLING
    del RETORNO log, no del nivel: un precio/tipo de cambio es una serie con
    tendencia, z-scorear el NIVEL no la hace estacionaria) y brecha_zscore
    (z-score rolling del NIVEL de la brecha -- un ratio/gap % ya acotado, a
    diferencia de un precio, asi que no hace falta diferenciarlo primero).
    dates: fechas del ticker objetivo (DatetimeIndex), mismo orden que el
    df que se le va a pegar esto al lado."""
    spy_aligned = _align_ffill(macro_data.get_global_trend_series(conn, "SPY"), dates)
    ccl_aligned = _align_ffill(macro_data.get_ccl_series(conn), dates)
    brecha_aligned = _align_ffill(macro_data.get_brecha_cambiaria_pct_series(conn), dates)

    spy_zscore = _rolling_zscore(np.log(spy_aligned / spy_aligned.shift(1)))
    ccl_zscore = _rolling_zscore(np.log(ccl_aligned / ccl_aligned.shift(1)))
    brecha_zscore = _rolling_zscore(brecha_aligned)

    return pd.DataFrame(
        {
            "spy_zscore": spy_zscore.to_numpy(),
            "ccl_zscore": ccl_zscore.to_numpy(),
            "brecha_zscore": brecha_zscore.to_numpy(),
        },
        index=dates,
    )


def _resolve_technical_source_df(conn, raw_df, asset_type, symbol):
    """Serie sobre la que se calculan microestructura + tecnicos (RSI/MACD/
    ATR/FFD, ver conversacion): para un CEDEAR con subyacente verificado
    (CEDEAR_DIRECT_UNDERLYING_SYMBOLS), la serie REAL de Wall Street (ver
    portfolio/fetchers.py::get_underlying_ohlcv_df) -- evita que ruido
    propio de BYMA (spread, liquidez, timing del CCL) contamine indicadores
    tecnicos que deberian reflejar la accion de precio REAL del activo. Para
    todo lo demas (accion/bono/on, o un cedear sin subyacente verificado
    todavia) se mantiene el criterio ya existente: la serie convertida a USD
    CCL (build_model_input) -- fallback silencioso a esto tambien si el
    fetch del subyacente fallara (yfinance caido, poca historia), NUNCA se
    rompe el pipeline por esto, declarado explicitamente en vez de ocultarlo
    en un try/except mudo."""
    usd_df, ccl_rate, currency = build_model_input(conn, raw_df, asset_type)
    if asset_type != "cedear" or symbol not in CEDEAR_DIRECT_UNDERLYING_SYMBOLS:
        return usd_df, False
    fetchers.ensure_underlying_ohlcv_history(conn, symbol)
    underlying_df = fetchers.get_underlying_ohlcv_df(conn, symbol)
    if len(underlying_df) < WARMUP_ROWS_FOR_UNDERLYING:
        return usd_df, False
    # Alineado por FECHA (no por posicion: el subyacente cotiza en dias/feriados
    # distintos a BYMA) contra las fechas de raw_df, forward-fill causal (mismo
    # criterio que _align_ffill) -- nunca interpola hacia el futuro.
    aligned = underlying_df.set_index("timestamps").reindex(
        pd.DatetimeIndex(raw_df["timestamps"]), method="ffill"
    )
    aligned.index = raw_df.index
    return aligned, True


def build_full_feature_matrix(conn, raw_df, asset_type, symbol=None):
    """Microestructura del ticker (4 cols) + tecnicos RSI/MACD/ATR/FFD (4
    cols) + macro exogeno (3 cols), en un solo DataFrame alineado al index
    POSICIONAL de raw_df (0..n-1, el mismo que usa portfolio/backtest.py
    para indexar anchors) -- las 2 columnas que faltan para completar
    FEATURE_COLUMNS (chronos_p50_return_pct/chronos_dispersion_ratio) vienen
    de chronos_feature_row(), no de aca, porque dependen de correr
    Chronos-Bolt por anchor, no de la serie historica sola.

    Refactorizacion financiera (ver conversacion): la microestructura
    (log_return/volatility_10d/dist_ema50_pct/volume_ratio_5_20) se calcula
    sobre la serie YA CONVERTIDA a USD CCL para accion/cedear (via
    portfolio/forecasting.py::build_model_input, la MISMA conversion que ya
    ve Chronos-Bolt) -- antes esta funcion usaba raw_df tal cual (ARS), lo
    que desalineaba las features tecnicas (en ARS, con la tendencia
    estructural de inflacion/devaluacion) de lo que el modelo realmente
    pronostica (USD). bono/on no se tocan (build_model_input los deja en su
    moneda nativa, ya USD). volume NO se convierte (no es un precio).

    'symbol': si se pasa y es un CEDEAR con subyacente verificado (ver
    _resolve_technical_source_df/CEDEAR_DIRECT_UNDERLYING_SYMBOLS), los
    indicadores TECNICOS (microestructura + RSI/MACD/ATR/FFD) se calculan
    sobre la serie REAL de Wall Street en vez de la sintetica ARS/CCL -- el
    ratio CEDEAR/subyacente NO se aplica porque es matematicamente
    irrelevante para estos indicadores (todos son invariantes a un factor de
    escala constante: retorno log, %, RSI, ATR%% -- ver conversacion), la
    ejecucion/precio de entrada real sigue viniendo de data912 sin cambios,
    esto es solo para la FEATURE del meta-modelo offline. Sin symbol (o sin
    subyacente verificado), se mantiene el criterio anterior (serie CCL).

    raw_df: el MISMO DataFrame que ya fetchea el caller (nunca se vuelve a
    pedir la serie al ticker -- evita una llamada HTTP duplicada por
    ticker)."""
    technical_source_df, used_underlying = _resolve_technical_source_df(conn, raw_df, asset_type, symbol)
    micro = _ticker_microstructure_matrix(technical_source_df)
    technical = _technical_indicator_matrix(technical_source_df)
    dates = pd.DatetimeIndex(raw_df["timestamps"])
    macro = _macro_exogenous_matrix(conn, dates)
    macro.index = raw_df.index  # mismo orden que dates (ver _align_ffill), solo cambia la etiqueta del index
    return pd.concat([micro, technical, macro], axis=1)


def chronos_feature_row(p10_pct, p50_pct, p90_pct, dispersion_ratio=None):
    """a) chronos_p50_return_pct: retorno proyectado (mediana) a N pasos.
    chronos_dispersion_ratio: |p50| / (p90-p10) -- que tan grande es el
    movimiento proyectado frente a lo incierto que es el propio pronostico.
    dispersion_ratio: si no viene del microservicio (ver
    services/predictor/main.py::_dispersion_ratio), se recalcula igual aca
    con la misma formula, para que la feature nunca falte."""
    band_width = p90_pct - p10_pct
    if dispersion_ratio is None:
        dispersion_ratio = abs(p50_pct) / band_width if band_width > 0 else 0.0
    return {"chronos_p50_return_pct": p50_pct, "chronos_dispersion_ratio": dispersion_ratio}


CHRONOS_FEATURE_COLUMNS = ["chronos_p50_return_pct", "chronos_dispersion_ratio"]
MICROSTRUCTURE_FEATURE_COLUMNS = ["log_return", "volatility_10d", "volume_ratio_5_20", "dist_ema50_pct"]
MACRO_FEATURE_COLUMNS = ["spy_zscore", "ccl_zscore", "brecha_zscore"]
# RSI/MACD/ATR/FFD (pedidos explicitamente, ver conversacion) -- RVOL no se
# duplica aca, ya esta en MICROSTRUCTURE_FEATURE_COLUMNS (volume_ratio_5_20).
TECHNICAL_FEATURE_COLUMNS = ["rsi_14", "macd_hist_pct", "atr_pct_14", "ffd_log_price"]

# V2: meta-modelo unificado SIN macro (Chronos + microestructura del propio
# ticker) -- el control del experimento. V3: V2 + los 3 canales macro -- lo
# que esa fase queria medir si aportaba algo por encima de V2. V4: V3 + los
# 4 tecnicos nuevos (RSI/MACD/ATR%/FFD, ver conversacion, "evolucion
# integral") -- misma logica de comparacion limpia (portfolio/backtest.py
# entrena los 3 sobre el MISMO split de cada fold).
V2_FEATURE_COLUMNS = CHRONOS_FEATURE_COLUMNS + MICROSTRUCTURE_FEATURE_COLUMNS
V3_FEATURE_COLUMNS = V2_FEATURE_COLUMNS + MACRO_FEATURE_COLUMNS
V4_FEATURE_COLUMNS = V3_FEATURE_COLUMNS + TECHNICAL_FEATURE_COLUMNS

# Set "default" para quien instancie MetaLearner() sin elegir explicito --
# el mas completo (V4).
FEATURE_COLUMNS = V4_FEATURE_COLUMNS


def build_multivariate_dataframe(conn, symbol, asset_type):
    """Variante standalone de inspeccion/debug -- mismas columnas que
    build_full_feature_matrix() (microestructura + macro) MAS los niveles
    crudos (close/high/low/volume/spy_close/ccl_usd/brecha_cambiaria_pct/
    caucion_tna_pct) para poder mirar los numeros reales, no solo las
    features ya transformadas. Fetchea la serie ella misma (a diferencia de
    build_full_feature_matrix, pensada para cuando el caller YA tiene el
    df) -- para uso exploratorio, no la usa el backtest."""
    raw_df = fetchers.fetch_data912_historical(symbol, asset_type).sort_values("timestamps").reset_index(drop=True)
    dates = pd.DatetimeIndex(raw_df["timestamps"])

    full = build_full_feature_matrix(conn, raw_df, asset_type, symbol=symbol)
    df = pd.DataFrame({
        "date": dates,
        "close": raw_df["close"].to_numpy(),
        "high": raw_df["high"].to_numpy(),
        "low": raw_df["low"].to_numpy(),
        "volume": raw_df["volume"].to_numpy(),
    })
    df = pd.concat([df, full.reset_index(drop=True)], axis=1)

    spy_aligned = _align_ffill(macro_data.get_global_trend_series(conn, "SPY"), dates)
    ccl_aligned = _align_ffill(macro_data.get_ccl_series(conn), dates)
    brecha_aligned = _align_ffill(macro_data.get_brecha_cambiaria_pct_series(conn), dates)
    df["spy_close"] = spy_aligned.to_numpy()
    df["ccl_usd"] = ccl_aligned.to_numpy()
    df["brecha_cambiaria_pct"] = brecha_aligned.to_numpy()
    df["caucion_tna_pct"] = macro_data.get_caucion_tna_pct(conn)

    return df


TICKER_SERIES_COLUMNS = ["close", "high", "low", "volume", "log_return", "volatility_10d"]
EXOGENOUS_SERIES_COLUMNS = [
    "spy_close", "spy_zscore", "ccl_usd", "ccl_zscore",
    "brecha_cambiaria_pct", "brecha_zscore", "caucion_tna_pct",
]
MULTIVARIATE_COLUMNS = TICKER_SERIES_COLUMNS + EXOGENOUS_SERIES_COLUMNS
