"""
Fetchers de precios para los instrumentos cargados en el portfolio.

Cada instrumento sabe de donde traer su precio via price_source +
price_source_params (ver schema.sql). Nada de tickers ni endpoints
hardcodeados en codigo: todo sale de esos campos.

Fuentes:
  - data912       -> acciones/CEDEARs/bonos/ON de BYMA (gratis, sin auth)
  - dolarapi      -> cotizaciones de USD en Argentina (gratis, sin auth)
  - binance       -> precio spot de criptomonedas (publico, sin auth)
  - argentinadatos -> serie historica diaria de dolar (CCL/MEP/etc, gratis, sin auth)
"""
import json
import time
from datetime import datetime, timezone

import pandas as pd
import requests
import yfinance as yf
from bs4 import BeautifulSoup

from portfolio import broker_client, db

DATA912_BASE = "https://data912.com"
DOLARAPI_BASE = "https://dolarapi.com"
BINANCE_BASE = "https://api.binance.com"
BNA_URL = "https://www.bna.com.ar/Personas"
ARGENTINADATOS_BASE = "https://api.argentinadatos.com"

_BNA_CACHE_TTL = 300
_bna_cache = {"rates": None, "fetched_at": 0.0}

# "casa" de CCL en argentinadatos, usada para convertir series ARS a USD antes
# de alimentarlas a Kronos (ver portfolio/forecasting.py).
CCL_CASA = "contadoconliqui"
CCL_FRESHNESS_HOURS = 12

# tipo de instrumento -> endpoint live de data912
DATA912_LIVE_ENDPOINT_BY_TYPE = {
    "accion": "arg_stocks",
    "cedear": "arg_cedears",
    "bono": "arg_bonds",
    "on": "arg_corp",
}

# tipo de instrumento -> endpoint historico de data912 (ON no tiene historico)
DATA912_HIST_ENDPOINT_BY_TYPE = {
    "accion": "stocks",
    "cedear": "cedears",
    "bono": "bonds",
}


def _params(instrument):
    raw = instrument.get("price_source_params")
    return json.loads(raw) if raw else {}


# --- data912 -----------------------------------------------------------------

def fetch_data912_live(endpoint):
    resp = requests.get(f"{DATA912_BASE}/live/{endpoint}", timeout=15)
    resp.raise_for_status()
    return resp.json()


def _bono_on_divisor(instrument):
    """Bonos/ON cotizan en el mercado 'por cada 100 nominales'; en todo el resto
    del sistema 'precio' significa 'por 1 unidad de cantidad' (como una accion).
    Normalizamos aca, en el borde con data912, para que nadie mas tenga que
    acordarse de este detalle (si se olvida una sola vez, el valor de la
    tenencia sale 100x mas grande de lo real)."""
    return 100 if instrument["type"] in ("bono", "on") else 1


def fetch_data912_quote(instrument):
    endpoint = _params(instrument).get("endpoint") or DATA912_LIVE_ENDPOINT_BY_TYPE.get(instrument["type"])
    if not endpoint:
        raise ValueError(f"No se pudo determinar el endpoint data912 para {instrument['symbol']}")

    rows = fetch_data912_live(endpoint)
    match = next((r for r in rows if r["symbol"] == instrument["symbol"]), None)
    if not match:
        raise ValueError(f"{instrument['symbol']} no encontrado en data912 /live/{endpoint}")

    divisor = _bono_on_divisor(instrument)
    bid = match.get("px_bid")
    ask = match.get("px_ask")
    return {
        "price": match["c"] / divisor,
        "bid": bid / divisor if bid is not None else None,
        "ask": ask / divisor if ask is not None else None,
        # % variación real del día (contra el cierre de AYER, calculado por
        # data912) -- NUNCA se divide por `divisor` (es un %, no un precio
        # "por 100 nominales"). Ver conversación/auditoría: esto reemplaza al
        # cálculo basado en price_history, que puede quedar días atrás.
        "pct_change": match.get("pct_change"),
    }


def fetch_data912_historical(symbol, asset_type):
    endpoint = DATA912_HIST_ENDPOINT_BY_TYPE.get(asset_type)
    if not endpoint:
        raise ValueError(f"data912 no tiene historico para el tipo '{asset_type}'")

    resp = requests.get(f"{DATA912_BASE}/historical/{endpoint}/{symbol}", timeout=20)
    resp.raise_for_status()
    raw = resp.json()
    if not raw or isinstance(raw, dict):
        raise ValueError(
            f"No hay historial de precios para {symbol} en data912 — "
            "esta fuente no cubre este instrumento en particular."
        )

    df = pd.DataFrame(raw)
    df["timestamps"] = pd.to_datetime(df["date"])
    df = df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
    if asset_type in ("bono", "on"):
        df[["open", "high", "low", "close"]] /= 100
    # Monto operado real (no un cero fantasma): aproximado como volumen * precio
    # promedio del dia -- mismo fallback que usa KronosPredictor._prepare_inputs
    # cuando no recibe esta columna, calculado aca (con el precio ya normalizado
    # "por 1 unidad") para que quede disponible tambien para indicadores/chart.
    df["amount"] = df["volume"] * df[["open", "high", "low", "close"]].mean(axis=1)
    return df[["timestamps", "open", "high", "low", "close", "volume", "amount"]]


# --- Banco Nacion (BNA) ------------------------------------------------------------

def _bna_usd_row(soup, tab_id):
    tab = soup.find(id=tab_id)
    if not tab:
        raise ValueError(f"No se encontro el bloque '{tab_id}' en la pagina de BNA")
    for row in tab.find_all("tr"):
        cells = [c.get_text(strip=True) for c in row.find_all("td")]
        if cells and cells[0].startswith("Dolar"):
            return float(cells[1].replace(",", ".")), float(cells[2].replace(",", "."))
    raise ValueError(f"No se encontro la fila de Dolar U.S.A. en '{tab_id}'")


def fetch_bna_rates():
    """Cotizacion real de BNA, scrapeada de su propia web (no publican API):
    billete (efectivo) y divisa (transferencia) tienen tasas distintas."""
    if _bna_cache["rates"] is not None and time.time() - _bna_cache["fetched_at"] < _BNA_CACHE_TTL:
        return _bna_cache["rates"]

    resp = requests.get(BNA_URL, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    billete_compra, billete_venta = _bna_usd_row(soup, "billetes")
    divisa_compra, divisa_venta = _bna_usd_row(soup, "divisas")

    rates = {
        "billete_compra": billete_compra, "billete_venta": billete_venta,
        "divisa_compra": divisa_compra, "divisa_venta": divisa_venta,
    }
    _bna_cache["rates"] = rates
    _bna_cache["fetched_at"] = time.time()
    return rates


# --- argentinadatos (historico de dolar) ---------------------------------------

def fetch_fx_historical(casa=CCL_CASA):
    """Serie diaria completa (no admite rango de fechas, la API devuelve todo
    el historico junto -- ~5000 puntos desde 2013, una sola request)."""
    resp = requests.get(f"{ARGENTINADATOS_BASE}/v1/cotizaciones/dolares/{casa}", timeout=30)
    resp.raise_for_status()
    raw = resp.json()
    return [{"fecha": r["fecha"], "compra": r.get("compra"), "venta": r.get("venta")} for r in raw]


def ensure_ccl_history(conn, casa=CCL_CASA):
    """Refresca la serie historica de CCL si la ultima fecha guardada no es de
    hoy (mismo criterio de frescura que quotes_latest/fundamentals_latest: no
    pegarle a la fuente externa en cada carga)."""
    latest = db.get_fx_rate_latest_fetched_date(conn, casa)
    today = datetime.now(timezone.utc).date().isoformat()
    if latest == today:
        return
    try:
        rows = fetch_fx_historical(casa)
    except Exception:
        return  # sin conexion o la fuente esta caida -- se usa lo que ya hay guardado
    if rows:
        db.upsert_fx_rates(conn, casa, rows)


INFLATION_URL = f"{ARGENTINADATOS_BASE}/v1/finanzas/indices/inflacion"


def fetch_inflation_history():
    """IPC mensual (% variacion mes a mes), serie completa en una sola request."""
    resp = requests.get(INFLATION_URL, timeout=30)
    resp.raise_for_status()
    raw = resp.json()
    return [{"fecha": r["fecha"], "valor_mensual_pct": r["valor"]} for r in raw]


def ensure_inflation_history(conn):
    latest = db.get_inflation_latest_fetched_date(conn)
    today = datetime.now(timezone.utc).date().isoformat()
    if latest == today:
        return
    try:
        rows = fetch_inflation_history()
    except Exception:
        return
    if rows:
        db.upsert_inflation_rates(conn, rows)


# --- yfinance (benchmarks, ej. SPY) ---------------------------------------------

def fetch_benchmark_history(symbol):
    """Historico diario de cierre de un simbolo via yfinance -- usado para
    comparar la cartera contra un indice en USD (SPY)."""
    hist = yf.Ticker(symbol).history(period="max")
    if hist.empty:
        raise ValueError(f"yfinance no devolvio historico para {symbol}")
    hist = hist.reset_index()
    return [
        {"fecha": pd.Timestamp(row.Date).strftime("%Y-%m-%d"), "close": float(row.Close)}
        for row in hist.itertuples()
        if pd.notna(row.Close)  # yfinance a veces devuelve el dia en curso sin cierre todavia (NaN) -- se saltea, nunca se inserta un cierre inventado
    ]


def ensure_benchmark_history(conn, symbol="SPY"):
    latest = db.get_benchmark_latest_fetched_date(conn, symbol)
    today = datetime.now(timezone.utc).date().isoformat()
    if latest == today:
        return
    try:
        rows = fetch_benchmark_history(symbol)
    except Exception:
        return
    if rows:
        db.upsert_benchmark_prices(conn, symbol, rows)


def fetch_underlying_ohlcv_history(symbol):
    """OHLCV completo (no solo cierre) de un ticker de Wall Street via
    yfinance -- ver schema.sql::underlying_price_history. Usado por
    portfolio/features.py para calcular indicadores tecnicos de un CEDEAR
    sobre su activo subyacente REAL, no sobre la serie sintetica ARS/CCL."""
    hist = yf.Ticker(symbol).history(period="max")
    if hist.empty:
        raise ValueError(f"yfinance no devolvio historico para {symbol}")
    hist = hist.reset_index()
    return [
        {
            "fecha": pd.Timestamp(row.Date).strftime("%Y-%m-%d"),
            "open": float(row.Open), "high": float(row.High), "low": float(row.Low),
            "close": float(row.Close), "volume": float(row.Volume) if pd.notna(row.Volume) else None,
        }
        for row in hist.itertuples()
        if pd.notna(row.Close)  # mismo criterio que fetch_benchmark_history: nunca un cierre inventado
    ]


def ensure_underlying_ohlcv_history(conn, symbol):
    """Mismo patron de frescura diaria que ensure_benchmark_history --
    no pega a yfinance mas de una vez por dia por simbolo."""
    latest = db.get_underlying_latest_fetched_date(conn, symbol)
    today = datetime.now(timezone.utc).date().isoformat()
    if latest == today:
        return
    try:
        rows = fetch_underlying_ohlcv_history(symbol)
    except Exception:
        return
    if rows:
        db.upsert_underlying_ohlcv(conn, symbol, rows)


def get_underlying_ohlcv_df(conn, symbol):
    """DataFrame timestamps/open/high/low/close/volume ordenado
    cronologicamente, mismas columnas que fetch_data912_historical, para
    poder reusar portfolio/indicators.py sin distinguir el origen del dato.
    No fetchea -- el caller debe llamar ensure_underlying_ohlcv_history
    antes (mismo criterio que el resto de este modulo)."""
    rows = db.get_underlying_ohlcv_history(conn, symbol)
    if not rows:
        return pd.DataFrame(columns=["timestamps", "open", "high", "low", "close", "volume"])
    df = pd.DataFrame(rows)
    df["timestamps"] = pd.to_datetime(df["fecha"])
    return df[["timestamps", "open", "high", "low", "close", "volume"]].sort_values("timestamps").reset_index(drop=True)


# --- dolarapi ------------------------------------------------------------------

_DOLARAPI_CACHE_TTL = 60
_dolarapi_cache = {}


def fetch_dolarapi_casa(casa):
    """Cacheado (Fase 10): dashboard-bundle llama esto hasta 4 veces en una
    sola request (currency-exposure, risk, ganancia, usd_mep_rate) -- sin
    cache, cada llamada es un HTTP real (~5s con esta API en particular),
    volviendo el bundle completo lentísimo (~20s, reportado por el usuario)."""
    cached = _dolarapi_cache.get(casa)
    if cached and time.time() - cached["fetched_at"] < _DOLARAPI_CACHE_TTL:
        return cached["data"]
    resp = requests.get(f"{DOLARAPI_BASE}/v1/dolares/{casa}", timeout=15)
    resp.raise_for_status()
    data = resp.json()
    _dolarapi_cache[casa] = {"data": data, "fetched_at": time.time()}
    return data


def fetch_dolarapi_quote(instrument):
    casa = _params(instrument).get("casa", "oficial")
    data = fetch_dolarapi_casa(casa)
    return {"price": data["venta"], "bid": data["compra"], "ask": data["venta"]}


# "casa" de dolarapi por fuente de USD elegida en Preferencias (ver app_settings.usd_rate_source).
_DOLARAPI_CASA_BY_SOURCE = {"mep": "bolsa", "ccl": "contadoconliqui"}


def resolve_usd_rate(settings):
    """Centraliza de donde sale "la" cotizacion USD/ARS usada para convertir
    la cartera -- antes estaba hardcodeada a BNA por separado en routes.py e
    history.py; ahora ambos la piden aca segun app_settings.usd_rate_source."""
    source = settings.get("usd_rate_source") or "bna"
    if source in _DOLARAPI_CASA_BY_SOURCE:
        return fetch_dolarapi_casa(_DOLARAPI_CASA_BY_SOURCE[source])["venta"]
    return fetch_bna_rates()[settings["bna_rate_variant"]]


# --- binance ---------------------------------------------------------------------

def fetch_binance_quote(instrument):
    pair = _params(instrument).get("pair", instrument["symbol"])
    resp = requests.get(f"{BINANCE_BASE}/api/v3/ticker/price", params={"symbol": pair}, timeout=15)
    resp.raise_for_status()
    price = float(resp.json()["price"])
    return {"price": price, "bid": None, "ask": None}


# --- orquestacion ------------------------------------------------------------------

_QUOTE_FETCHERS = {
    "data912": fetch_data912_quote,
    "dolarapi": fetch_dolarapi_quote,
    "binance": fetch_binance_quote,
}


def fetch_broker_account_quote(account, instrument):
    """Precio de EJECUCIÓN (ver conversación: "que tome los precios de la
    plataforma que usa" -- si opera en PPI, el de PPI; si en IOL, el de
    IOL). Solo se llama cuando instrument['broker_account_id'] está seteado
    -- ver update_quote. Nunca "PPI" en abstracto: una cuenta PUNTUAL (si
    hay 2 cuentas del mismo bróker, esto saca la ambigüedad de cuál usar)."""
    adapter = broker_client._adapter_for(account)
    return adapter.get_current_price(instrument["symbol"], instrument["type"])


def update_quote(conn, instrument):
    """Trae y guarda la cotizacion actual de un instrumento. No hace nada
    para price_source='manual'. broker_account_id (si está seteado) manda
    por encima de price_source -- ver fetch_broker_account_quote."""
    if instrument.get("broker_account_id"):
        account = db.get_broker_account(conn, instrument["broker_account_id"])
        if account is None:
            raise ValueError(f"broker_account_id={instrument['broker_account_id']} no existe (¿se borró la cuenta?)")
        quote = fetch_broker_account_quote(account, instrument)
        source = account["broker_name"].lower()
    else:
        fetcher = _QUOTE_FETCHERS.get(instrument["price_source"])
        if fetcher is None:
            return None
        quote = fetcher(instrument)
        source = instrument["price_source"]

    db.upsert_quote(
        conn, instrument["id"], quote["price"],
        source=source, bid=quote.get("bid"), ask=quote.get("ask"), pct_change=quote.get("pct_change"),
    )
    return quote


def update_all_quotes(conn):
    """Actualiza la cotizacion de todos los instrumentos. Devuelve {symbol: error} de los que fallaron."""
    errors = {}
    for instrument in db.list_instruments(conn):
        try:
            update_quote(conn, instrument)
        except Exception as e:
            errors[instrument["symbol"]] = str(e)
    return errors


def backfill_history(conn, instrument):
    """Descarga historico OHLC de data912 y lo guarda en price_history. Solo accion/cedear/bono."""
    if instrument["price_source"] != "data912":
        raise ValueError("El historico automatico solo esta soportado para instrumentos data912")

    df = fetch_data912_historical(instrument["symbol"], instrument["type"])
    rows = [
        (instrument["id"], row.timestamps.isoformat(), row.open, row.high, row.low,
         row.close, row.volume, "data912")
        for row in df.itertuples()
    ]
    conn.executemany(
        """INSERT INTO price_history (instrument_id, timestamp, open, high, low, close, volume, source)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(instrument_id, timestamp) DO UPDATE SET
               open=excluded.open, high=excluded.high, low=excluded.low,
               close=excluded.close, volume=excluded.volume, source=excluded.source""",
        rows,
    )
    conn.commit()
    return len(rows)
