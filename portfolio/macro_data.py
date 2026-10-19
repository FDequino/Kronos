"""Series exogenas/macro alineadas por fecha, para el meta-modelo de
ensemble (ver portfolio/features.py::build_multivariate_dataframe).

Importante: esto NO es para Chronos-Bolt. El motor base es univariado por
diseño -- verificado contra la libreria real instalada
(chronos.BaseChronosPipeline.predict_quantiles solo acepta una serie 1D o
una lista/batch de series 1D independientes, nunca canales/covariables de
la MISMA serie, ver services/predictor/main.py para la nota completa). Estas
series alimentan al META-MODELO (services/predictor/meta_learner.py), que
si es tabular y puede sumar cuantas columnas se le den.

Cache: reusa el cache SQLite que YA existe en este proyecto
(fx_rate_history/benchmark_price_history, ver portfolio/fetchers.py) en vez
de duplicar fetch+cache logic -- este modulo es una capa de ENSAMBLADO sobre
eso, con su propio control de frescura (ensure_*) ya resuelto ahi.
"""
import pandas as pd

from portfolio import db, fetchers

TREND_SYMBOLS = ("SPY", "QQQ")
OFICIAL_CASA = "oficial"  # casa de argentinadatos para el dolar oficial (ver fetchers.CCL_CASA para el CCL)


def _series_from_history(history_dict):
    """{fecha_str: valor} -> pd.Series indexada por fecha (datetime,
    ascendente) -- forma comun para alinear cualquiera de las fuentes de
    abajo contra la serie del ticker."""
    if not history_dict:
        return pd.Series(dtype=float)
    s = pd.Series(history_dict, dtype=float)
    s.index = pd.to_datetime(s.index)
    return s.sort_index()


def get_global_trend_series(conn, symbol="SPY"):
    """a) SPY/QQQ como proxy de tendencia/impulso global -- refresca el cache
    (ensure_benchmark_history ya trae su propio control de frescura diaria,
    no pega a yfinance en cada llamada) y devuelve los cierres diarios."""
    if symbol not in TREND_SYMBOLS:
        raise ValueError(f"symbol debe ser uno de {TREND_SYMBOLS}, se recibio '{symbol}'")
    fetchers.ensure_benchmark_history(conn, symbol)
    return _series_from_history(db.get_benchmark_history(conn, symbol))


def get_ccl_series(conn):
    """b.1) Dolar CCL -- MISMO cache que ya usa portfolio/forecasting.py para
    convertir accion/cedear a USD antes de Chronos (ensure_ccl_history/
    get_fx_rate_history, ver fetchers.CCL_CASA) -- nunca un segundo fetch
    con otro criterio de frescura para la misma serie."""
    fetchers.ensure_ccl_history(conn, fetchers.CCL_CASA)
    return _series_from_history(db.get_fx_rate_history(conn, fetchers.CCL_CASA))


def get_oficial_fx_series(conn):
    """b.2) Dolar oficial (BNA/mayorista, via argentinadatos casa='oficial')
    -- se guarda en la MISMA tabla fx_rate_history que el CCL, solo cambia
    el parametro 'casa' (ensure_ccl_history/fetch_fx_historical ya son
    genericos sobre la casa, no hace falta un fetcher nuevo)."""
    fetchers.ensure_ccl_history(conn, OFICIAL_CASA)
    return _series_from_history(db.get_fx_rate_history(conn, OFICIAL_CASA))


def get_brecha_cambiaria_pct_series(conn):
    """b) Brecha cambiaria real (CCL/oficial - 1, en %) -- para aislar
    devaluacion "pura" (el oficial) de la brecha especulativa (lo que suma
    el CCL por encima). Alineado por fecha via reindex+ffill (ultimo valor
    conocido de cada serie, nunca interpolado hacia adelante -- ver
    portfolio/features.py::_align_ffill para la misma logica reutilizada)."""
    ccl = get_ccl_series(conn)
    oficial = get_oficial_fx_series(conn)
    if ccl.empty or oficial.empty:
        return pd.Series(dtype=float)
    idx = ccl.index.union(oficial.index)
    ccl_aligned = ccl.reindex(idx).ffill()
    oficial_aligned = oficial.reindex(idx).ffill()
    brecha = (ccl_aligned / oficial_aligned.replace(0, float("nan")) - 1) * 100
    return brecha.dropna()


VIX_SYMBOL = "^VIX"


def get_vix_series(conn):
    """d) VIX (CBOE Volatility Index) -- proxy de miedo/volatilidad implicita
    GLOBAL (no existe un indice de volatilidad argentino publico y gratuito,
    mismo limite que get_caucion_tna_pct de abajo). Usado por
    portfolio/regime.py para bloquear al Motor Satelite en regimen de alta
    volatilidad (ver conversacion) -- mismo mecanismo de cache/frescura
    (ensure_benchmark_history/benchmark_price_history) que ya usa SPY/QQQ,
    simbolo "^VIX" en vez de un ticker de accion."""
    fetchers.ensure_benchmark_history(conn, VIX_SYMBOL)
    return _series_from_history(db.get_benchmark_history(conn, VIX_SYMBOL))


def get_caucion_tna_pct(conn):
    """c) Tasa de caución TNA / risk-free de referencia -- NO existe una
    fuente publica gratuita de esta tasa con serie historica diaria (ver
    glosario de la app y portfolio/backtest.py::compute_hurdle_pct, mismo
    limite ya declarado ahi); se usa el valor MANUAL configurado en
    signal_config.caucion_reference_tna como una constante (no una serie que
    varia por fecha) -- None si el usuario nunca lo configuro, nunca un
    numero inventado."""
    config = db.get_signal_config(conn)
    tna = config.get("caucion_reference_tna")
    return float(tna) if tna else None
