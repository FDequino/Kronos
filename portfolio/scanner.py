"""Escáner de liquidez de BYMA / "aduana de liquidez" (ver conversación: "que
los parámetros del escáner... sean 100% configurables" -- después de explicar
que Core/Satélite/Pairs/Factor/etc. solo consideran un universo FIJO de
tickers hardcodeado en portfolio/backtest.py, nunca "lo que esté en tu
cartera o catálogo").

Este módulo decide QUÉ instrumentos puede llegar a evaluar el Motor Core/
Satélite en una rueda dada -- nunca decide señales, pesos ni ejecuta nada.

Dos modos (signal_config.scanner_mode):
  - 'curated' (default, CERO cambio de comportamiento para quien no tocó
    nada en Ajustes): el mismo portfolio/backtest.py::UNIVERSE de siempre,
    el único backtesteado y validado (ver "Rendimiento Histórico").
  - 'global_filtered': TODO el universo de acciones/CEDEARs/bonos de BYMA
    (vía data912, mismo origen que portfolio/byma_universe.py) que pase la
    aduana de liquidez: volumen diario mínimo en USD, spread bid/ask máximo,
    y días operados mínimos en el último mes. Riesgo real y EXPLÍCITO: un
    instrumento que pasa el filtro de liquidez nunca pasó por el backtest de
    Core/Satélite -- por eso este modo es opt-in, nunca el default (ver
    conversación previa: "una estrategia nunca probada sobre ese instrumento
    puntual").

Costo acotado en 2 pasos (ver conversación: evaluar TODO BYMA en cada rueda
sería carísimo si se hiciera ingenuo): primero el filtro barato (volumen+
spread, 3 requests bulk a data912 /live/*), recién DESPUÉS -- solo sobre los
que ya pasaron eso -- el filtro caro de días operados (1 request de histórico
POR símbolo sobreviviente, paralelizado). Resultado cacheado
_LIQUIDITY_CACHE_TTL_SECONDS, invalidado solo si cambian los parámetros."""
import concurrent.futures
import time
from datetime import timedelta

from portfolio import db, fetchers
from portfolio.backtest import UNIVERSE

_LIQUIDITY_CACHE_TTL_SECONDS = 1800  # 30 min -- ver docstring del modulo
_OPERATING_DAYS_LOOKBACK = timedelta(days=30)  # aproximacion declarada a "el ultimo mes" (ver conversacion)
_HISTORICAL_SCAN_WORKERS = 8  # mismo orden de magnitud que compute_today_signals/thread pools del resto del proyecto

_liquidity_cache = {"universe": None, "fetched_at": 0.0, "params": None}


def _scanner_params(config):
    return {
        "min_daily_volume_usd": config["min_daily_volume_usd"],
        "max_bid_ask_spread_pct": config["max_bid_ask_spread_pct"],
        "min_operating_days_month": config["min_operating_days_month"],
    }


def _passes_cheap_liquidity_gate(row, ccl_rate, params):
    """Volumen (convertido a USD via CCL) + spread bid/ask -- ambos salen del
    MISMO payload bulk de /live/<endpoint>, sin requests adicionales por
    símbolo. Cualquier dato faltante/inválido degrada a "no pasa", nunca se
    asume liquidez sin evidencia (mismo criterio que el resto del proyecto,
    ver portfolio/nlp_agent.py)."""
    price = row.get("c")
    volume = row.get("v")
    if not price or not volume or not ccl_rate:
        return False
    volume_usd = (volume * price) / ccl_rate
    if volume_usd < params["min_daily_volume_usd"]:
        return False
    bid = row.get("px_bid")
    ask = row.get("px_ask")
    if not bid or not ask or bid <= 0 or ask <= 0:
        return False  # sin punta de compra/venta real, no hay spread que medir -- no pasa
    spread_pct = (ask - bid) / ((ask + bid) / 2) * 100
    return spread_pct <= params["max_bid_ask_spread_pct"]


def _operating_days_ok(symbol, asset_type, params):
    """Cuenta ruedas con volumen > 0 en los últimos _OPERATING_DAYS_LOOKBACK
    días corridos (aproximación declarada a "días operados del mes" -- un
    mes calendario real tiene ~21 ruedas hábiles, 30 días corridos cubre eso
    con margen sin complicarse con el calendario de feriados de BYMA).
    Cualquier falla de datos (símbolo sin histórico en data912, red caída)
    degrada a "no pasa" -- nunca se asume liquidez sin evidencia."""
    try:
        df = fetchers.fetch_data912_historical(symbol, asset_type)
    except Exception:
        return False
    if df.empty:
        return False
    cutoff = df["timestamps"].max() - _OPERATING_DAYS_LOOKBACK
    recent = df[df["timestamps"] >= cutoff]
    operating_days = int((recent["volume"] > 0).sum())
    return operating_days >= params["min_operating_days_month"]


def _current_ccl_rate():
    """CCL "venta" de dolarapi (mismo criterio que
    _DOLARAPI_CASA_BY_SOURCE["ccl"] en fetchers.py) -- independiente de
    app_settings.usd_rate_source (que puede ser BNA, no representativo del
    tipo de cambio real usado para convertir volumen operado en BYMA).
    None si la fuente falla (nunca se inventa una tasa)."""
    try:
        return fetchers.fetch_dolarapi_casa("contadoconliqui")["venta"]
    except Exception:
        return None


def scan_global_filtered(params, ccl_rate=None):
    """Corre el escaneo completo de BYMA contra `params` -- función pura
    (no toca el cache ni SQLite), separada de get_scanner_universe para que
    los tests puedan ejercitarla sin pasar por signal_config."""
    ccl_rate = ccl_rate if ccl_rate is not None else _current_ccl_rate()
    if ccl_rate is None:
        return []  # sin tasa real, no hay forma honesta de calcular volumen en USD

    cheap_survivors = []
    for asset_type, hist_endpoint in fetchers.DATA912_HIST_ENDPOINT_BY_TYPE.items():
        live_endpoint = fetchers.DATA912_LIVE_ENDPOINT_BY_TYPE[asset_type]
        try:
            rows = fetchers.fetch_data912_live(live_endpoint)
        except Exception:
            continue  # esta fuente puntual esta caida -- no rompe el resto del escaneo
        for row in rows:
            if _passes_cheap_liquidity_gate(row, ccl_rate, params):
                cheap_survivors.append((row["symbol"], asset_type))

    if not cheap_survivors:
        return []

    survivors = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=_HISTORICAL_SCAN_WORKERS) as pool:
        futures = {
            pool.submit(_operating_days_ok, symbol, asset_type, params): (symbol, asset_type)
            for symbol, asset_type in cheap_survivors
        }
        for future, (symbol, asset_type) in futures.items():
            if future.result():
                survivors.append((symbol, asset_type))
    return survivors


def get_scanner_universe(conn):
    """Punto de entrada único -- reemplaza al universo fijo que hoy pasan
    webui/app.py, telegram_bot.py, pdf_report.py y monitor.py a
    live_signals.compute_today_signals/monitor.scan_once. En modo 'curated'
    (default) devuelve EXACTAMENTE backtest.UNIVERSE, cero cambio de
    comportamiento para quien no tocó nada en Ajustes."""
    config = db.get_signal_config(conn)
    if (config.get("scanner_mode") or "curated") != "global_filtered":
        return UNIVERSE

    params = _scanner_params(config)
    cached = _liquidity_cache
    if (cached["universe"] is not None
            and cached["params"] == params
            and time.time() - cached["fetched_at"] < _LIQUIDITY_CACHE_TTL_SECONDS):
        return cached["universe"]

    universe = scan_global_filtered(params)
    _liquidity_cache["universe"] = universe
    _liquidity_cache["params"] = params
    _liquidity_cache["fetched_at"] = time.time()
    return universe
