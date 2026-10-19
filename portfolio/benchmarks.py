"""Compara el rendimiento real de la cartera (TWR, ver history.py) contra 3
referencias: caución, inflación y SPY. Cada una expone sus propios períodos
({twr_pct, from_date, to_date}) + una serie de índice diario -- el frontend
rebasa cada serie a 100 en el inicio del período elegido para que las 4 líneas
sean comparables pese a estar en unidades nativas distintas (ARS, tasa fija,
% de inflación, USD).

Ninguna referencia se inventa si falta el dato: "available": False + el motivo
explícito, nunca un número de relleno.
"""
from datetime import datetime, timedelta

from portfolio import db, fetchers, history
from portfolio.tz import today_ar


def _period_windows(end_date, start_date):
    """Igual que history.py::_period_start_dates, pero clampeado al inicio
    real de la cartera (las referencias externas, a diferencia del TWR, tienen
    datos de sobra desde mucho antes -- sin este clamp "desde inicio" de
    inflación mostraría décadas de IPC ajenas al período invertido)."""
    return {
        "1m": max(end_date - timedelta(days=30), start_date),
        "3m": max(end_date - timedelta(days=90), start_date),
        "ytd": max(end_date.replace(month=1, day=1), start_date),
        "1a": max(end_date - timedelta(days=365), start_date),
        "inicio": start_date,
    }


def _value_at_or_before(series_by_date, known_dates, date_str, month_granularity=False):
    """Devuelve (valor, fecha_real_usada) -- la fecha real puede ser anterior a
    `date_str` (no hay dato futuro o todavia no publicado, ej. IPC del mes en
    curso), nunca se inventa un punto. None si no hay ningun dato <= date_str."""
    key = (lambda d: d[:7]) if month_granularity else (lambda d: d)
    target = key(date_str)
    candidates = [d for d in known_dates if key(d) <= target]
    if not candidates:
        return None
    matched = candidates[-1]
    return series_by_date[matched], matched


def _reindex_and_rebase(series_by_date, dates, month_granularity=False):
    """Reindexa una serie externa (con sus propias fechas) sobre las MISMAS
    fechas que la serie de la cartera (forward-fill, nunca inventa un punto
    futuro), y la rebasa a 100 en el primer punto compartido -- asi las 4
    lineas del grafico comparten eje X y arrancan todas en 100."""
    known_dates = sorted(series_by_date)
    if not known_dates:
        return []
    out = []
    base_value = None
    for date_str in dates:
        match = _value_at_or_before(series_by_date, known_dates, date_str, month_granularity=month_granularity)
        if match is None:
            continue
        value, _ = match
        if base_value is None:
            base_value = value
        out.append({"date": date_str, "index": round(value / base_value * 100, 4)})
    return out


def _caucion_index_series(tna, dates):
    """Sintetiza el indice de una caucion con esa TNA constante, interes
    simple compuesto dia a dia -- mismo criterio que ya usa db.py para el
    devengado de cauciones/plazos fijos."""
    series = []
    value = 100.0
    prev_date = None
    for date_str in dates:
        if prev_date is not None:
            dias = (datetime.fromisoformat(date_str).date() - datetime.fromisoformat(prev_date).date()).days
            # tna esta en PUNTOS PORCENTUALES (35.0 = 35%) -- hay que dividir
            # por 100 para usarlo como fraccion en el factor de crecimiento.
            value *= (1 + (tna / 100) / 365 * dias)
        series.append({"date": date_str, "index": round(value, 4)})
        prev_date = date_str
    return series


def _caucion_benchmark(conn, end_date, start_date, dates):
    config = db.get_signal_config(conn)
    tna = config.get("caucion_reference_tna")
    if tna is None:
        return {
            "available": False,
            "reason": "Tasa de referencia de caución no configurada todavía (cargala en Preferencias).",
            "periods": {},
        }

    periods = {}
    for key, period_start in _period_windows(end_date, start_date).items():
        dias = (end_date - period_start).days
        if dias <= 0:
            periods[key] = None
            continue
        periods[key] = {
            "twr_pct": round(tna / 365 * dias, 2),
            "from_date": period_start.isoformat(), "to_date": end_date.isoformat(),
        }
    return {
        "available": True, "periods": periods, "tna": tna,
        "index_series": _caucion_index_series(tna, dates),
    }


def _inflacion_benchmark(conn, end_date, start_date, dates):
    index = history.inflation_index_series(conn)
    if not index:
        return {"available": False, "reason": "Sin datos de inflación disponibles.", "periods": {}}

    known_dates = sorted(index)
    today_value = index[known_dates[-1]]
    periods = {}
    for key, period_start in _period_windows(end_date, start_date).items():
        match = _value_at_or_before(index, known_dates, period_start.isoformat(), month_granularity=True)
        if match is None:
            periods[key] = None
            continue
        start_value, matched_date = match
        periods[key] = {
            "twr_pct": round((today_value / start_value - 1) * 100, 2),
            "from_date": matched_date, "to_date": known_dates[-1],
        }
    return {
        "available": True, "periods": periods,
        "index_series": _reindex_and_rebase(index, dates, month_granularity=True),
    }


def _spy_benchmark(conn, end_date, start_date, dates):
    fetchers.ensure_benchmark_history(conn, "SPY")
    series = db.get_benchmark_history(conn, "SPY")
    if not series:
        return {"available": False, "reason": "Sin histórico de SPY disponible.", "periods": {}, "index_series": []}

    known_dates = sorted(series)
    today_value = series[known_dates[-1]]
    periods = {}
    for key, period_start in _period_windows(end_date, start_date).items():
        match = _value_at_or_before(series, known_dates, period_start.isoformat())
        if match is None:
            periods[key] = None
            continue
        start_value, matched_date = match
        periods[key] = {
            "twr_pct": round((today_value / start_value - 1) * 100, 2),
            "from_date": matched_date, "to_date": known_dates[-1],
        }
    return {
        "available": True, "periods": periods,
        "index_series": _reindex_and_rebase(series, dates),
    }


def compute_benchmarks(conn):
    twr = history.compute_twr(conn)
    transactions = db.list_transactions(conn)
    if not transactions:
        empty = {"available": False, "reason": "Sin transacciones todavía.", "periods": {}}
        return {"cartera": twr, "caucion": empty, "inflacion": empty, "spy": empty}

    start_date = datetime.fromisoformat(min(t["fecha"] for t in transactions)).date()
    end_date = (
        datetime.fromisoformat(twr["index_series"][-1]["date"]).date()
        if twr["index_series"] else today_ar()
    )
    dates = [p["date"] for p in twr["index_series"]]

    return {
        "cartera": twr,
        "caucion": _caucion_benchmark(conn, end_date, start_date, dates),
        "inflacion": _inflacion_benchmark(conn, end_date, start_date, dates),
        "spy": _spy_benchmark(conn, end_date, start_date, dates),
    }
