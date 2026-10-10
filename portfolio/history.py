"""Historial real de valor de cartera, reconstruido hacia atras a partir de lo
que ya tenemos (transactions + price_history), nunca inventado. Tambien arma
una serie teorica: el mismo calculo pero usando lo que Kronos predijo en su
momento (forecasts.result_json.predicted) donde haya cobertura real -- en el
resto de las fechas, teorico == real (no hay nada que inventar ahi).

price_history y los predicted de forecasts ya vienen normalizados "por 1 unidad"
en el borde con data912 (ver fetchers.py:_bono_on_divisor) -- ninguna cuenta
acá vuelve a dividir por 100, hacerlo again duplicaria el bug que ya se arreglo
una vez.

Simplificacion aceptada y declarada: no se trackea el tipo de cambio BNA
dia por dia hacia atras, asi que todo instrumento en USD se convierte con la
cotizacion ACTUAL en toda la serie, no con la de cada dia pasado. Instrumentos
sin historico disponible (ON sin endpoint, fondos de precio manual) se omiten
de la reconstruccion en vez de inventarles un precio.
"""
import json
from collections import defaultdict
from datetime import datetime, timedelta

from portfolio import db, fetchers

HISTORICAL_TYPES = ("accion", "cedear", "bono")


def _ensure_history(conn, instrument):
    if instrument["price_source"] != "data912" or instrument["type"] not in HISTORICAL_TYPES:
        return
    if db.get_price_history(conn, instrument["id"], limit=1):
        return
    try:
        fetchers.backfill_history(conn, instrument)
    except Exception:
        pass  # sin historico disponible para este instrumento -- se omite, nunca se inventa


def _date_range(start, end):
    days = (end - start).days
    return [start + timedelta(days=i) for i in range(days + 1)]


def _price_series(price_history_rows):
    return {row["timestamp"].split("T")[0]: row["close"] for row in price_history_rows}


def _predicted_series(forecasts):
    series = {}
    for f in forecasts:
        result = json.loads(f["result_json"])
        for point in result.get("predicted") or []:
            date_key = datetime.utcfromtimestamp(point["time"]).strftime("%Y-%m-%d")
            series[date_key] = point["close"]
    return series


def reconstruct_portfolio_value(conn):
    instruments = {i["id"]: i for i in db.list_instruments(conn)}
    transactions = sorted(db.list_transactions(conn), key=lambda t: t["fecha"])
    if not transactions:
        return {"series": [], "missing_symbols": []}

    held_instrument_ids = {t["instrument_id"] for t in transactions}
    for instrument_id in held_instrument_ids:
        instrument = instruments.get(instrument_id)
        if instrument:
            _ensure_history(conn, instrument)

    missing_symbols = sorted(
        instruments[iid]["symbol"] for iid in held_instrument_ids
        if iid in instruments and not db.get_price_history(conn, iid, limit=1)
    )

    try:
        settings = db.get_app_settings(conn)
        bna_rate = fetchers.fetch_bna_rates()[settings["bna_rate_variant"]]
    except Exception:
        bna_rate = None

    price_by_instrument = {}
    predicted_by_instrument = {}
    for instrument_id in held_instrument_ids:
        price_by_instrument[instrument_id] = _price_series(db.get_price_history(conn, instrument_id))
        predicted_by_instrument[instrument_id] = _predicted_series(db.list_forecasts_for_instrument(conn, instrument_id))

    start = datetime.fromisoformat(transactions[0]["fecha"]).date()
    end = datetime.utcnow().date()

    cantidad_by_instrument = defaultdict(float)
    tx_index = 0
    last_known_price = {}
    last_known_predicted = {}
    series = []

    for day in _date_range(start, end):
        date_str = day.isoformat()
        while tx_index < len(transactions) and transactions[tx_index]["fecha"] <= date_str:
            t = transactions[tx_index]
            delta = t["cantidad"] if t["tipo"] == "compra" else -t["cantidad"]
            cantidad_by_instrument[t["instrument_id"]] += delta
            tx_index += 1

        valor_real = 0.0
        valor_teorico = 0.0
        tiene_teorico = False

        for instrument_id, cantidad in cantidad_by_instrument.items():
            if cantidad <= 0:
                continue
            instrument = instruments.get(instrument_id)
            if not instrument:
                continue

            price_series = price_by_instrument.get(instrument_id, {})
            if date_str in price_series:
                last_known_price[instrument_id] = price_series[date_str]
            price = last_known_price.get(instrument_id)

            pred_series = predicted_by_instrument.get(instrument_id, {})
            if date_str in pred_series:
                last_known_predicted[instrument_id] = pred_series[date_str]
            predicted_price = last_known_predicted.get(instrument_id)

            factor = bna_rate if (instrument["currency"] == "USD" and bna_rate) else 1.0

            if price is not None:
                valor_real += cantidad * price * factor

            if predicted_price is not None:
                tiene_teorico = True
                valor_teorico += cantidad * predicted_price * factor
            elif price is not None:
                valor_teorico += cantidad * price * factor

        series.append({
            "date": date_str,
            "valor_real": round(valor_real, 2),
            "valor_teorico": round(valor_teorico, 2) if tiene_teorico else None,
        })

    return {"series": series, "missing_symbols": missing_symbols}
