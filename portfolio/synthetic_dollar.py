"""Dólar Sintético y Futuros ROFEX (ver conversación) -- compara la tasa
implícita anualizada de los contratos de Dólar Futuro (ROFEX, vía A3
Mercados) contra la TNA de Caución configurada a mano por el usuario, y
emite una señal de "Cobertura / Tasa Sintética" cuando conviene más una
posición sintética (pesos a Caución + cobertura comprando dólar futuro) que
otras alternativas en pesos.

FUENTES REALES VERIFICADAS (ver conversación, nunca fabricar un endpoint):
  - Futuros de dólar ROFEX: EL MISMO feed público, sin login, que
    portfolio/fixed_income.py ya usa para futuros de CER (A3 Mercados / ex
    Matba Rofex). Verificado con una consulta real (2026-10) a
    A3_REFDATA_URL: existen 42 contratos reales con
    underlying == "rx_DDF_BCRA_A3500" (el dólar futuro DLR liquida contra la
    Comunicación A3500 del BCRA, confirmado por la descripción real del
    propio feed: "Dólar USA A3500"). Se filtran los contratos "outright"
    simples (ids tipo rx_DDF_DLR_OCT26) descartando variantes "...M" (mini,
    menor tamaño de contrato pero mismo precio de referencia) y los spreads
    de calendario (ids con 2 meses, ej. rx_DDF_DLR_OCT26_NOV26) -- ver
    _OUTRIGHT_ID_RE.
  - Spot de referencia: dolarapi.com casa "mayorista" (verificado real con
    una consulta a https://dolarapi.com/v1/dolares, que SÍ expone esa casa
    -- distinta de "oficial", que en la práctica incluye efectos de
    impuestos minoristas). Aproximación DECLARADA al tipo de cambio
    mayorista/A3500 contra el que en teoría liquida el futuro DLR -- no es
    un calce exacto con la Comunicación A3500 publicada por el BCRA, pero es
    la fuente gratuita más cercana disponible sin necesitar acceso directo
    al BCRA.

Reutiliza (sin duplicar) la lógica de parseo de mensajes ya resuelta en
fixed_income.py (_parse_market_data_message, reverse-engineered contra
tráfico real) -- nunca se reescribe esa lógica fragil dos veces."""
import asyncio
import json
import re
from datetime import date

import requests

from portfolio import db
from portfolio.fetchers import fetch_dolarapi_casa
from portfolio.fixed_income import A3_REFDATA_URL, A3_WS_URL, _parse_market_data_message
from portfolio.tz import today_ar

DOLLAR_FUTURES_UNDERLYING = "rx_DDF_BCRA_A3500"
# ids reales confirmados tipo "rx_DDF_DLR_OCT26" -- descarta variantes "...M"
# (mini) y spreads de calendario de 2 patas (ej. "..._OCT26_NOV26[_M]").
_OUTRIGHT_ID_RE = re.compile(r"^rx_DDF_DLR_[A-Z]{3}\d{2}$")

# Pedido explícito del usuario: "+5% TNA neto de comisiones" -- colchón
# declarado para cubrir costos de brokerage/clearing reales de ROFEX, que
# varían por bróker y no se calculan acá uno por uno (ver docstring de
# compute_synthetic_dollar_signal). Configurable via el parametro
# margin_tna_pct de esa funcion -- no se agrego todavia un setting propio en
# signal_config para no pisar otras ediciones de portfolio/db.py en curso en
# paralelo (ver conversacion); candidato razonable para una futura columna
# "synthetic_dollar_margin_tna" si se quiere exponer en la UI.
DEFAULT_MARGIN_TNA_PCT = 5.0


def _iso_from_yyyymmdd(yyyymmdd):
    return f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:]}"


def _discover_dollar_futures_topics():
    resp = requests.get(A3_REFDATA_URL, timeout=20)
    resp.raise_for_status()
    securities = resp.json().get("securities", [])
    topics = [
        {"id": s["id"], "maturity_date": _iso_from_yyyymmdd(s["maturityDate"]), "description": s["description"]}
        for s in securities
        if s.get("underlying") == DOLLAR_FUTURES_UNDERLYING and s.get("maturityDate") and _OUTRIGHT_ID_RE.match(s["id"])
    ]
    topics.sort(key=lambda t: t["maturity_date"])
    return topics


async def _collect_futures_async(topic_ids, timeout):
    import websockets

    prices = {}
    async with websockets.connect(A3_WS_URL, open_timeout=10) as ws:
        await ws.send(json.dumps({
            "_req": "S", "topicType": "md", "topics": [f"md.{t}" for t in topic_ids], "replace": False,
        }))
        loop = asyncio.get_event_loop()
        end = loop.time() + timeout
        while loop.time() < end:
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=max(0.5, end - loop.time()))
            except asyncio.TimeoutError:
                break
            if not msg.startswith("["):
                continue
            try:
                items = json.loads(msg)
            except json.JSONDecodeError:
                continue
            for item in items:
                if isinstance(item, str) and item.startswith("M:"):
                    symbol, price = _parse_market_data_message(item)
                    if price is not None:
                        prices[symbol] = price
    return prices


def fetch_dollar_futures(timeout=8):
    """Precio de referencia mas reciente de los futuros de dolar ROFEX
    vigentes. Mismo criterio de honestidad que
    fixed_income.py::fetch_cer_futures: conexion bajo demanda, nunca rompe
    por falta de datos/red, devuelve {} ante cualquier falla -- un contrato
    sin operaciones recientes es la realidad del mercado, no un error de la
    integracion."""
    try:
        topics_info = _discover_dollar_futures_topics()
    except Exception:
        return {}
    if not topics_info:
        return {}
    try:
        prices = asyncio.run(_collect_futures_async([t["id"] for t in topics_info], timeout))
    except Exception:
        return {}
    return {
        t["id"]: {"maturity_date": t["maturity_date"], "description": t["description"], "price": prices.get(t["id"])}
        for t in topics_info
    }


def get_dollar_spot_rate():
    """Proxy de spot mayorista/A3500 -- ver HONESTIDAD DECLARADA arriba del
    modulo. Se usa "venta" (no "compra"): es el lado relevante para armar la
    cobertura real (cubrirse de una suba de dolar implica poder comprarlo,
    no venderlo, al precio spot)."""
    return fetch_dolarapi_casa("mayorista")["venta"]


def _implied_annual_rate_pct(futuro_price, spot_price, days_to_maturity):
    """Tasa implicita TNA SIMPLE (no capitalizada) del futuro de dolar frente
    al spot -- formula estandar de forwards FX: (F/S - 1) * (365/d) * 100.
    Simplificacion declarada: tasa simple (no TNA compuesta/efectiva), y
    365 dias corridos en vez de una convencion de dias habiles exacta de
    ROFEX."""
    if not days_to_maturity or days_to_maturity <= 0 or not spot_price or spot_price <= 0:
        return None
    return (futuro_price / spot_price - 1) * (365.0 / days_to_maturity) * 100.0


def compute_synthetic_dollar_signal(conn=None, margin_tna_pct=DEFAULT_MARGIN_TNA_PCT, as_of=None):
    """Punto de entrada unico (ver conversacion, sera importado por
    portfolio/live_signals.py). Compara la tasa implicita del contrato de
    dolar futuro ROFEX de vencimiento MAS CERCANO contra la TNA de Caucion
    configurada (signal_config.caucion_reference_tna, cargada a mano por el
    usuario -- mismo criterio ya establecido en
    portfolio/pairs_trading.py/portfolio/factor_investing.py) mas un margen
    configurable (default +5 puntos de TNA, pedido explicito del usuario
    como aproximacion neta de comisiones -- las comisiones reales de ROFEX
    varian por broker/clearing, este margen es un colchon declarado, no un
    calculo de comision real).

    Devuelve un dict de señal cuando la tasa implicita supera a
    Caucion + margen, o None si: no hay Caucion configurada (no hay contra
    que comparar, nunca se inventa un default), no hay datos de futuros
    disponibles, o el spread no supera el margen. Nunca rompe el
    dispatcher unificado de live_signals.py por una falla de red."""
    own_conn = False
    if conn is None:
        conn = db.get_connection()
        own_conn = True
    try:
        caucion_tna = float(db.get_signal_config(conn).get("caucion_reference_tna") or 0.0)
    finally:
        if own_conn:
            conn.close()
    if caucion_tna <= 0:
        return None

    try:
        spot = get_dollar_spot_rate()
    except Exception:
        return None
    if not spot or spot <= 0:
        return None

    futures = fetch_dollar_futures()
    today = as_of or today_ar()
    best = None
    for contract_id, info in futures.items():
        if info.get("price") is None:
            continue
        try:
            maturity = date.fromisoformat(info["maturity_date"])
        except (TypeError, ValueError):
            continue
        days = (maturity - today).days
        implied_tna = _implied_annual_rate_pct(info["price"], spot, days)
        if implied_tna is None:
            continue
        if best is None or maturity < date.fromisoformat(best["maturity_date"]):
            best = {
                "contract": contract_id, "maturity_date": info["maturity_date"],
                "futuro_price": info["price"], "implied_tna_pct": implied_tna,
            }
    if best is None:
        return None

    hurdle_tna = caucion_tna + margin_tna_pct
    spread_pct = best["implied_tna_pct"] - hurdle_tna
    if spread_pct <= 0:
        return None

    return {
        "contract": best["contract"],
        "maturity_date": best["maturity_date"],
        "implied_tna_pct": round(best["implied_tna_pct"], 2),
        "caucion_tna_pct": round(caucion_tna, 2),
        "margin_pct": round(margin_tna_pct, 2),
        "spread_pct": round(spread_pct, 2),
        "spot_price": spot,
        "futuro_price": best["futuro_price"],
        "note": (
            f"Dólar futuro {best['contract']} (vto. {best['maturity_date']}) implica una tasa anualizada de "
            f"{round(best['implied_tna_pct'], 1)}% TNA, {round(spread_pct, 1)} puntos por encima de Caución "
            f"({round(caucion_tna, 1)}% TNA) + margen ({margin_tna_pct}% TNA). Cobertura/Tasa Sintética: pesos a "
            "Caución + compra de este futuro de dólar puede rendir más que otras alternativas en pesos, cubriendo "
            "además el riesgo de tipo de cambio."
        ),
    }
