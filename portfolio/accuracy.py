"""Precision historica de Kronos: compara lo que predijo en el pasado contra lo que
realmente paso despues, usando datos que ya existen (forecasts.result_json + price_history).

Metodologia explicita, nunca una caja negra: por cada forecast pasado ya resuelto, cruza
su tramo predicho contra el precio real que ya tenemos para esas mismas fechas. Si todavia
no paso tiempo suficiente desde el primer forecast, no hay nada que cruzar y se devuelve
None -- nunca un numero inventado.
"""
import json
from datetime import datetime, timezone


def _date_key(iso_timestamp):
    return iso_timestamp.split("T")[0]


def _date_key_from_unix(unix_seconds):
    return datetime.fromtimestamp(unix_seconds, tz=timezone.utc).strftime("%Y-%m-%d")


def _to_unix(iso_timestamp):
    return int(datetime.fromisoformat(iso_timestamp).replace(tzinfo=timezone.utc).timestamp())


def compute_accuracy(forecasts, price_history):
    """forecasts: filas de `forecasts` con status='done' (cualquier orden).
    price_history: filas reales de precio del mismo instrumento."""
    actual_by_date = {_date_key(row["timestamp"]): row["close"] for row in price_history}

    matched_points = []
    directional_hits = 0
    directional_evaluated = 0

    for forecast in forecasts:
        result = json.loads(forecast["result_json"])
        predicted = result.get("predicted") or []
        historical = result.get("historical") or []
        if not predicted or not historical:
            continue

        last_historical_close = historical[-1]["close"]

        for i, point in enumerate(predicted):
            date_key = _date_key_from_unix(point["time"])
            actual_close = actual_by_date.get(date_key)
            if actual_close is None:
                continue
            matched_points.append({
                "time": point["time"],
                "predicted": point["close"],
                "actual": actual_close,
                "forecast_id": forecast["id"],
            })
            if i == 0:
                predicted_up = point["close"] > last_historical_close
                actual_up = actual_close > last_historical_close
                directional_evaluated += 1
                if predicted_up == actual_up:
                    directional_hits += 1

    if not matched_points:
        earliest_predicted_date = None
        for forecast in forecasts:
            result = json.loads(forecast["result_json"])
            predicted = result.get("predicted") or []
            if not predicted:
                continue
            date_key = _date_key_from_unix(predicted[0]["time"])
            if earliest_predicted_date is None or date_key < earliest_predicted_date:
                earliest_predicted_date = date_key
        if earliest_predicted_date is None:
            return None
        return {"pending": True, "earliest_evaluable_date": earliest_predicted_date}

    matched_points.sort(key=lambda p: p["time"])
    mape = sum(
        abs(p["predicted"] - p["actual"]) / p["actual"] * 100
        for p in matched_points if p["actual"]
    ) / len(matched_points)

    from_date = _date_key_from_unix(matched_points[0]["time"])
    to_date = _date_key_from_unix(matched_points[-1]["time"])
    historical_candles = [
        {
            "time": _to_unix(row["timestamp"]),
            "open": row["open"], "high": row["high"], "low": row["low"], "close": row["close"],
        }
        for row in price_history
        if from_date <= _date_key(row["timestamp"]) <= to_date
    ]

    return {
        "historical": historical_candles,
        "predicted_points": matched_points,
        "mape": round(mape, 2),
        "directional_hit_pct": round(directional_hits / directional_evaluated * 100, 1) if directional_evaluated else None,
        "forecasts_evaluated": len({p["forecast_id"] for p in matched_points}),
        "points_evaluated": len(matched_points),
    }


def explain_accuracy(symbol, accuracy):
    base = (
        f"{symbol}: evaluado sobre {accuracy['forecasts_evaluated']} pronostico(s) pasado(s) "
        f"y {accuracy['points_evaluated']} punto(s) ya con precio real conocido. "
        f"Error promedio (MAPE) {accuracy['mape']}%."
    )
    if accuracy["directional_hit_pct"] is not None:
        base += f" Acerto la direccion (suba/baja) el {accuracy['directional_hit_pct']}% de las veces."
    return base
