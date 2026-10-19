"""Precision historica de Kronos: compara lo que predijo en el pasado contra lo que
realmente paso despues, usando datos que ya existen (forecasts.result_json + price_history).

Metodologia explicita, nunca una caja negra: por cada forecast pasado ya resuelto, cruza
su tramo predicho contra el precio real que ya tenemos para esas mismas fechas. Si todavia
no paso tiempo suficiente desde el primer forecast, no hay nada que cruzar y se devuelve
None -- nunca un numero inventado.
"""
import json
from datetime import datetime, timezone

from portfolio.formatting import fmt_es


def _date_key(iso_timestamp):
    return iso_timestamp.split("T")[0]


def _date_key_from_unix(unix_seconds):
    return datetime.fromtimestamp(unix_seconds, tz=timezone.utc).strftime("%Y-%m-%d")


def _to_unix(iso_timestamp):
    return int(datetime.fromisoformat(iso_timestamp).replace(tzinfo=timezone.utc).timestamp())


def _predicted_range(result):
    """(primer, ultimo) timestamp unix del tramo predicho, o None si no tiene."""
    predicted = result.get("predicted") or []
    return (predicted[0]["time"], predicted[-1]["time"]) if predicted else None


def _select_non_overlapping_forecasts(forecasts, model_key=None):
    """Maximiza la cantidad de forecasts cuyo tramo predicho no se superpone con
    el de otro ya elegido (interval scheduling clasico: ordenar por fecha de fin,
    tomar greedily el siguiente que empiece despues del ultimo elegido).

    Por que hace falta: los forecasts se generan periodicamente y sus horizontes
    predichos se pisan entre si (dos corridas con una semana de diferencia
    predicen casi los mismos dias) -- sin esto, MAPE y acierto direccional
    cuentan el mismo dia real varias veces, inflando la cantidad aparente de
    evidencia sin agregar informacion nueva.

    model_key: si se pasa, descarta antes de seleccionar cualquier forecast de
    otro motor (forecasts.model_key) -- sin esto la seleccion greedy puede
    elegir, para un anchor que se solapa en el tiempo, la corrida de un motor
    distinto al que se esta midiendo. Hallazgo real: forecasts historicos
    etiquetados 'kronos-base' resultaron ser una mezcla de corridas de Kronos
    autoregresivo real y de Chronos-Bolt (la migracion nunca actualizo la
    etiqueta), indistinguibles por fecha -- sin este filtro, compute_accuracy
    las mezclaba sin darse cuenta."""
    if model_key is not None:
        forecasts = [f for f in forecasts if f["model_key"] == model_key]
    with_range = []
    for f in forecasts:
        result = json.loads(f["result_json"])
        rng = _predicted_range(result)
        if rng:
            with_range.append((rng[0], rng[1], f, result))
    with_range.sort(key=lambda t: t[1])  # por fecha de fin

    selected = []
    last_end = None
    for start, end, f, result in with_range:
        if last_end is None or start > last_end:
            selected.append((f, result))
            last_end = end
    return selected


def _resolve_model_key(model_key, conn):
    """model_key explicito gana siempre; si no se pasa, se resuelve al
    default_model_key vigente en signal_config (requiere conn) -- es el motor
    que forecast_worker.py usa HOY para encolar corridas nuevas (ver
    portfolio/forecast_worker.py::submit_forecast), asi que es el candidato
    correcto para "el motor activo" cuando nadie pide uno en particular."""
    if model_key is not None:
        return model_key
    if conn is None:
        raise ValueError("se necesita 'model_key' o 'conn' para resolver el motor activo")
    from portfolio import db
    return db.get_signal_config(conn)["default_model_key"]


def compute_accuracy(forecasts, price_history, model_key=None, conn=None):
    """forecasts: filas de `forecasts` con status='done' (cualquier orden).
    price_history: filas reales de precio del mismo instrumento.

    model_key: filtra las metricas al motor que genero cada forecast -- sin
    esto, dos motores distintos que coexisten en la tabla (o que alguna vez
    compartieron la misma etiqueta por un bug de migracion, ver
    _select_non_overlapping_forecasts) se mezclan en el mismo MAPE/acierto
    direccional sin que nada lo avise. Si no se pasa explicito, se resuelve el
    default_model_key vigente en signal_config (requiere conn)."""
    model_key = _resolve_model_key(model_key, conn)
    forecasts = [f for f in forecasts if f["model_key"] == model_key]
    actual_by_date = {_date_key(row["timestamp"]): row["close"] for row in price_history}

    matched_points = []
    directional_hits = 0
    directional_evaluated = 0

    for forecast, result in _select_non_overlapping_forecasts(forecasts):
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


def price_history_candles(price_history):
    """Precio real completo como {time (unix), close} -- para graficar el
    abanico de pronósticos pasados contra TODO el histórico disponible (a
    diferencia de compute_accuracy, que solo expone el recorte acotado a los
    pronósticos ya emparejados)."""
    return [{"time": _to_unix(row["timestamp"]), "close": row["close"]} for row in price_history]


def build_forecast_history(forecasts, price_history, limit=None):
    """Una fila por cada forecast 'done' del instrumento, mas reciente primero --
    a diferencia de compute_accuracy (que selecciona solo no-superpuestos para
    no inflar el MAPE), aca se listan TODOS: la vista "Histórico de Kronos"
    quiere mostrar cada corrida tal cual paso, resuelta o pendiente, no una
    muestra filtrada."""
    actual_by_date = {_date_key(row["timestamp"]): row["close"] for row in price_history}
    ordered = sorted(forecasts, key=lambda f: f["created_at"], reverse=True)
    if limit:
        ordered = ordered[:limit]

    rows = []
    for f in ordered:
        result = json.loads(f["result_json"])
        predicted = result.get("predicted") or []
        historical = result.get("historical") or []
        if not predicted or not historical:
            continue

        last_close = historical[-1]["close"]
        final_point = predicted[-1]
        final_date = _date_key_from_unix(final_point["time"])
        actual_close = actual_by_date.get(final_date)
        resolved = actual_close is not None

        distribution = result.get("distribution")
        median_pct = distribution["median_pct"] if distribution else result.get("pred_change_pct")
        actual_change_pct = round((actual_close - last_close) / last_close * 100, 2) if resolved else None
        error_pct = round(abs(median_pct - actual_change_pct), 2) if (resolved and median_pct is not None) else None
        within_band = None
        if resolved and distribution:
            within_band = distribution["p10_pct"] <= actual_change_pct <= distribution["p90_pct"]

        rows.append({
            "id": f["id"],
            "created_at": f["created_at"],
            "pred_len": f["pred_len"],
            "last_close": last_close,
            "predicted": predicted,
            "distribution": distribution,
            "median_pct": round(median_pct, 2) if median_pct is not None else None,
            "resolved": resolved,
            "final_date": final_date,
            "actual_change_pct": actual_change_pct,
            "error_pct": error_pct,
            "within_band": within_band,
        })
    return rows


def compute_calibration(forecasts, price_history, model_key=None, conn=None):
    """De los pronosticos no superpuestos ya resueltos (misma seleccion que
    compute_accuracy, para no contar el mismo desenlace real varias veces),
    que % tuvo el precio real dentro del rango [p10, p90] que devolvio el
    modelo -- si el modelo esta bien calibrado deberia rondar 80%. None si
    todavia no hay ningun pronostico con distribucion ya resuelto.

    model_key: mismo filtro y misma resolucion de default que compute_accuracy
    -- comparten _select_non_overlapping_forecasts, asi que sin esto la
    calibracion tiene la misma contaminacion entre motores."""
    model_key = _resolve_model_key(model_key, conn)
    forecasts = [f for f in forecasts if f["model_key"] == model_key]
    actual_by_date = {_date_key(row["timestamp"]): row["close"] for row in price_history}
    total = 0
    within = 0
    for forecast, result in _select_non_overlapping_forecasts(forecasts):
        distribution = result.get("distribution")
        predicted = result.get("predicted") or []
        historical = result.get("historical") or []
        if not distribution or not predicted or not historical:
            continue
        last_close = historical[-1]["close"]
        final_date = _date_key_from_unix(predicted[-1]["time"])
        actual_close = actual_by_date.get(final_date)
        if actual_close is None:
            continue
        actual_change_pct = (actual_close - last_close) / last_close * 100
        total += 1
        if distribution["p10_pct"] <= actual_change_pct <= distribution["p90_pct"]:
            within += 1

    if total == 0:
        return None
    return {
        "coverage_pct": round(within / total * 100, 1),
        "n": total,
        "expected_pct": 80.0,
    }


def explain_accuracy(symbol, accuracy):
    base = (
        f"{symbol}: evaluado sobre {accuracy['forecasts_evaluated']} pronóstico(s) pasado(s) "
        f"y {accuracy['points_evaluated']} punto(s) ya con precio real conocido. "
        f"Error promedio (MAPE) {fmt_es(accuracy['mape'], decimals=2)}%."
    )
    if accuracy["directional_hit_pct"] is not None:
        base += f" Acertó la dirección (suba/baja) el {fmt_es(accuracy['directional_hit_pct'])}% de las veces."
    return base


def compute_signal_validation(accuracy, config):
    """Una señal de Kronos solo se pinta como confiable (verde/rojo) si el
    ticker tiene suficiente evidencia INDEPENDIENTE detras -- nunca con
    cualquier pronostico corrido, ni con pronosticos que se pisan entre si
    (ver _select_non_overlapping_forecasts). Si no alcanza, se devuelve
    "sin validar" con el motivo, nunca se oculta el porque."""
    if not accuracy or accuracy.get("pending"):
        return {
            "validated": False,
            "reason": "Sin suficiente historial de pronósticos resueltos todavía.",
        }

    n = accuracy["forecasts_evaluated"]
    hit = accuracy["directional_hit_pct"]
    min_n = config["min_non_overlapping_forecasts"]
    min_hit = config["min_directional_hit_pct"]

    if n < min_n:
        return {
            "validated": False,
            "reason": f"Solo {n} pronósticos no superpuestos evaluados (se necesitan {fmt_es(min_n, decimals=0)}).",
        }
    if hit is None or hit < min_hit:
        hit_txt = f"{fmt_es(hit)}%" if hit is not None else "sin dato"
        return {
            "validated": False,
            "reason": (
                f"Acierto direccional {hit_txt} sobre {n} pronósticos no superpuestos "
                f"(se necesita ≥{fmt_es(min_hit, decimals=0)}%)."
            ),
        }
    return {
        "validated": True,
        "reason": f"{fmt_es(hit)}% de acierto direccional sobre {n} pronósticos no superpuestos.",
    }
