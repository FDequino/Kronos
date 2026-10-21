"""Historial real de valor de cartera, reconstruido hacia atras a partir de lo
que ya tenemos (transactions + price_history), nunca inventado. Tambien arma
una serie teorica: el mismo calculo pero usando lo que Kronos predijo en su
momento (forecasts.result_json.predicted) donde haya cobertura real -- en el
resto de las fechas, teorico == real (no hay nada que inventar ahi).

price_history y los predicted de forecasts ya vienen normalizados "por 1 unidad"
en el borde con data912 (ver fetchers.py:_bono_on_divisor) -- ninguna cuenta
acá vuelve a dividir por 100, hacerlo again duplicaria el bug que ya se arreglo
una vez.

Los instrumentos en USD se convierten a ARS con el CCL HISTORICO de cada dia
(fx_rate_history, ver _load_ccl_history mas abajo), no con la cotizacion
actual aplicada a toda la serie -- antes si se hacia asi (simplificacion que
distorsionaba cualquier periodo con devaluacion de por medio) y quedo
corregido reusando la misma maquinaria que ya alimentaba a compute_twr(
currency="usd") para risk.py. Instrumentos sin historico disponible (ON sin
endpoint, fondos de precio manual) o sin cobertura de CCL para esa fecha (CCL
arranca ~2013, ver fetchers.ensure_ccl_history) se omiten de la reconstruccion
en vez de inventarles un precio o un tipo de cambio.
"""
import bisect
import json
import time
from collections import defaultdict
from datetime import datetime, timedelta

import pandas as pd

from portfolio import db, fetchers
from portfolio.backtest import _fix_price_discontinuities
from portfolio.tz import today_ar

# reconstruct_portfolio_value/compute_twr reconstruyen la serie dia-por-dia
# desde el historial completo de precios/transacciones -- caro (medido en la
# VPS con datos reales: ~16s, el cuello de botella real de dashboard-bundle/
# Resumen, un loop puro Python de O(dias x instrumentos tenidos) sin
# vectorizar), y dashboard-bundle (Fase 7+) termina llamando variantes de
# esto 3 veces en una sola request (benchmarks con currency="native", risk.py
# con currency="usd", y el historial crudo).
#
# TTL de 30 minutos (antes 60s -- demasiado corto para un costo de ~16s: con
# varias cargas de Resumen por sesion, casi SIEMPRE caia en frio) + invalida-
# cion explicita en cuanto se crea/edita/borra una transaccion (ver
# invalidate_reconstruction_cache, llamado desde portfolio/routes.py) -- asi
# un cambio real se refleja al toque en vez de esperar el TTL completo, pero
# una carga de pagina de rutina (nada cambio) no vuelve a pagar los 16s.
_TWR_CACHE_TTL = 1800
_twr_cache = {}
_reconstruct_cache = {"data": None, "fetched_at": 0.0}


def invalidate_reconstruction_cache():
    """Llamar despues de cualquier alta/edicion/baja de transaccion (ver
    portfolio/routes.py) -- esos son los unicos datos que reconstruct_portfolio_value/
    compute_twr reconstruyen ademas de price_history (que solo cambia por job
    de backfill, no por accion del usuario en esta sesion)."""
    _reconstruct_cache["data"] = None
    _reconstruct_cache["fetched_at"] = 0.0
    _twr_cache.clear()

HISTORICAL_TYPES = ("accion", "cedear", "bono")

# Ver conversacion -- bug real encontrado perfilando el dashboard en la VPS:
# instrumentos sin historico en data912 (LETRAS/bonos de muy corto plazo,
# ej. S16O6/TZX27/TZXY7 -- confirmado, data912 nunca los cubrio) reintentaban
# el fetch de red (fetchers.py, timeout=20s) en CADA carga de pagina, porque
# get_price_history seguia vacio despues de cada intento fallido y no habia
# donde recordar "esto ya se probo y no existe". Con 3-5 instrumentos asi,
# eso solo ya explicaba +60-100s por carga. 7 dias: ni "nunca mas" (data912
# podria sumar cobertura mas adelante) ni un reintento tan seguido como para
# seguir pagando el mismo costo de red a cada rato.
HISTORY_BACKFILL_RETRY_DAYS = 7


def _ensure_history(conn, instrument):
    if instrument["price_source"] != "data912" or instrument["type"] not in HISTORICAL_TYPES:
        return
    if db.get_price_history(conn, instrument["id"], limit=1):
        return
    failed_at = instrument.get("history_backfill_failed_at")
    if failed_at:
        age_days = (datetime.utcnow() - datetime.fromisoformat(failed_at)).total_seconds() / 86400
        if age_days < HISTORY_BACKFILL_RETRY_DAYS:
            return
    try:
        fetchers.backfill_history(conn, instrument)
        if db.get_price_history(conn, instrument["id"], limit=1):
            db.update_instrument(conn, instrument["id"], history_backfill_failed_at=None)
        else:
            db.update_instrument(conn, instrument["id"], history_backfill_failed_at=datetime.utcnow().isoformat())
    except Exception:
        # sin historico disponible para este instrumento -- se omite, nunca
        # se inventa, pero SI se recuerda el intento (ver comentario arriba).
        db.update_instrument(conn, instrument["id"], history_backfill_failed_at=datetime.utcnow().isoformat())


def _date_range(start, end):
    days = (end - start).days
    return [start + timedelta(days=i) for i in range(days + 1)]


def _price_series(price_history_rows):
    """Bug real encontrado en produccion (auditoria 2026-10-20, ver
    conversacion "circuit breaker"): el historial de precios GUARDADO tiene
    saltos falsos de un dia para el otro (cambios de ratio de CEDEAR nunca
    ajustados hacia atras por data912 -- confirmado real: SPY el 2026-05-29,
    ~3x) que antes solo se corregian en los backtests de investigacion
    (portfolio/backtest.py::_fix_price_discontinuities) -- este reconstructor
    de valor de cartera REAL (el que alimenta Resumen y el circuit breaker de
    portfolio/risk_engine.py) leia el precio crudo sin corregir, mostrando una
    caida de ~27% de un dia para el otro que nunca paso. Se aplica el MISMO
    fix aca, sobre las filas ya guardadas (no se re-fetchea nada nuevo)."""
    if not price_history_rows:
        return {}
    df = pd.DataFrame(price_history_rows).sort_values("timestamp").reset_index(drop=True)
    df = _fix_price_discontinuities(df, price_cols=("close",))
    return {row["timestamp"].split("T")[0]: row["close"] for row in df.to_dict("records")}


def _predicted_series(forecasts):
    series = {}
    for f in forecasts:
        result = json.loads(f["result_json"])
        for point in result.get("predicted") or []:
            date_key = datetime.utcfromtimestamp(point["time"]).strftime("%Y-%m-%d")
            series[date_key] = point["close"]
    return series


def _load_ccl_history(conn):
    """Historico diario de CCL (fecha -> ARS por USD), listo para bisect --
    usado para convertir instrumentos USD con el tipo de cambio REAL de cada
    dia en vez de la cotizacion actual (ver docstring del modulo), tanto en
    reconstruct_portfolio_value como en compute_twr."""
    fetchers.ensure_ccl_history(conn)
    ccl_rate_map = db.get_fx_rate_history(conn, fetchers.CCL_CASA)
    return ccl_rate_map, sorted(ccl_rate_map)


def _ccl_rate_at(ccl_rate_map, ccl_known_dates, date_str):
    """Tasa CCL vigente en date_str: la ultima fecha conocida <= date_str
    (bisect). None si no hay cobertura todavia para esa fecha (CCL arranca
    ~2013, ver fetchers.ensure_ccl_history)."""
    i = bisect.bisect_right(ccl_known_dates, date_str) - 1
    return ccl_rate_map[ccl_known_dates[i]] if i >= 0 else None


def _any_usd_held(instruments, held_instrument_ids):
    return any(
        instruments[iid]["currency"] == "USD"
        for iid in held_instrument_ids if iid in instruments
    )


def reconstruct_portfolio_value(conn):
    if _reconstruct_cache["data"] is not None and time.time() - _reconstruct_cache["fetched_at"] < _TWR_CACHE_TTL:
        return _reconstruct_cache["data"]
    result = _reconstruct_portfolio_value_impl(conn)
    _reconstruct_cache["data"] = result
    _reconstruct_cache["fetched_at"] = time.time()
    return result


def _reconstruct_portfolio_value_impl(conn):
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

    settings = db.get_app_settings(conn)
    ccl_rate_map, ccl_known_dates = (
        _load_ccl_history(conn) if _any_usd_held(instruments, held_instrument_ids) else ({}, [])
    )

    price_by_instrument = {}
    predicted_by_instrument = {}
    for instrument_id in held_instrument_ids:
        price_by_instrument[instrument_id] = _price_series(db.get_price_history(conn, instrument_id))
        predicted_by_instrument[instrument_id] = _predicted_series(db.list_forecasts_for_instrument(conn, instrument_id))

    start = datetime.fromisoformat(transactions[0]["fecha"]).date()
    end = today_ar()

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

            if instrument["currency"] == "USD":
                factor = _ccl_rate_at(ccl_rate_map, ccl_known_dates, date_str)
                if factor is None:
                    continue  # sin cobertura CCL para esta fecha -- se omite, no se inventa un tipo de cambio
            else:
                factor = 1.0

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

    # Ajuste por inflacion: valor_real queda siempre en pesos nominales (nunca
    # se le cambia el significado); se agrega valor_real_ajustado aparte,
    # re-expresando cada punto pasado en "pesos de hoy", solo si el usuario lo
    # activo en Preferencias y esta mirando la cartera en ARS (ajustar un valor
    # en USD por inflacion ARS no tiene sentido).
    if settings["display_currency"] == "ARS" and settings.get("ars_inflation_adjusted"):
        inflation_index = inflation_index_series(conn)
        if inflation_index:
            for point in series:
                point["valor_real_ajustado"] = round(
                    adjust_for_inflation(point["valor_real"], point["date"], conn, index=inflation_index), 2
                )

    return {"series": series, "missing_symbols": missing_symbols}


# --- TWR (rendimiento ponderado por tiempo) ------------------------------------

def _period_start_dates(end_date):
    """Fecha de inicio de cada periodo estandar (relativas a la ultima fecha
    de la serie, no necesariamente "hoy" si no hubo refresh reciente)."""
    return {
        "1m": end_date - timedelta(days=30),
        "3m": end_date - timedelta(days=90),
        "ytd": end_date.replace(month=1, day=1),
        "1a": end_date - timedelta(days=365),
        "inicio": None,  # toda la serie
    }


def compute_twr(conn, currency="native"):
    cached = _twr_cache.get(currency)
    if cached and time.time() - cached["fetched_at"] < _TWR_CACHE_TTL:
        return cached["data"]
    result = _compute_twr_impl(conn, currency)
    _twr_cache[currency] = {"data": result, "fetched_at": time.time()}
    return result


def _compute_twr_impl(conn, currency="native"):
    """Rendimiento ponderado por tiempo: separa el efecto de aportes/retiros
    del rendimiento real, encadenando retornos diarios donde cada aporte
    (compra) o retiro (venta) se resta/suma como flujo externo antes de medir
    el retorno de ese dia -- asi un deposito no aparenta ser una ganancia (a
    diferencia de reconstruct_portfolio_value, pensada para otra cosa: mostrar
    el valor nominal real vs. el teorico de Kronos, no para medir rendimiento).

    No hay cuenta de efectivo modelada en este sistema (ver docstring del
    modulo): toda compra es un aporte externo, toda venta es un retiro externo
    -- es el criterio estandar de TWR cuando no se trackea cash por separado.

    currency="native" (default, usado por Fase 3a/benchmarks): ARS se queda en
    ARS, USD se expresa en ARS al CCL HISTORICO de cada dia (mismo
    fx_rate_history de abajo) -- antes se aplicaba la cotizacion ACTUAL
    (resolve_usd_rate) como factor CONSTANTE a toda la serie, lo que inflaba o
    desinflaba artificialmente cualquier retorno diario que mezclara ARS y USD
    en un periodo con devaluacion de por medio (a diferencia del reescalado
    puro de abajo, aca SI cambia el retorno porcentual real). Bug reportado:
    el TWR de "Patrimonio" no coincidia con la variacion real de la cartera en
    periodos largos.
    currency="usd" (usado por el motor de riesgo, ver risk.py): TODO se
    expresa en USD usando el CCL HISTORICO de cada dia (fx_rate_history, la
    misma tabla que ya alimenta a Kronos en forecasting.py), no la cotizacion
    actual -- dividir toda la serie por un numero CONSTANTE no cambia ningun
    retorno porcentual (es solo un reescalado), asi que no serviria para sacar
    la tendencia de devaluacion de la volatilidad/drawdown. Con el CCL de cada
    dia si se saca esa tendencia, igual que forecasting.py lo hace para
    Kronos."""
    instruments = {i["id"]: i for i in db.list_instruments(conn)}
    transactions = sorted(db.list_transactions(conn), key=lambda t: t["fecha"])
    if not transactions:
        return {"periods": {}, "index_series": [], "available": True}

    held_instrument_ids = {t["instrument_id"] for t in transactions}
    for instrument_id in held_instrument_ids:
        instrument = instruments.get(instrument_id)
        if instrument:
            _ensure_history(conn, instrument)

    ccl_known_dates = []
    ccl_rate_map = {}
    if currency == "usd" or _any_usd_held(instruments, held_instrument_ids):
        ccl_rate_map, ccl_known_dates = _load_ccl_history(conn)
        if currency == "usd" and not ccl_known_dates:
            return {
                "periods": {}, "index_series": [], "available": False,
                "reason": "No se pudo obtener el histórico de CCL para expresar el riesgo en dólares.",
            }

    def _factor(instrument_currency, date_str):
        if currency == "usd":
            if instrument_currency != "ARS":
                return 1.0
            rate = _ccl_rate_at(ccl_rate_map, ccl_known_dates, date_str)
            return (1 / rate) if rate else None
        if instrument_currency != "USD":
            return 1.0
        return _ccl_rate_at(ccl_rate_map, ccl_known_dates, date_str)

    price_by_instrument = {
        iid: _price_series(db.get_price_history(conn, iid)) for iid in held_instrument_ids
    }

    start = datetime.fromisoformat(transactions[0]["fecha"]).date()
    end = today_ar()

    cantidad_by_instrument = defaultdict(float)
    tx_index = 0
    last_known_price = {}
    daily_returns = []
    prev_value = None
    anchor_date = None  # fecha real del "dia 0" (indice 100), ver mas abajo

    for day in _date_range(start, end):
        date_str = day.isoformat()

        todays_transactions = []
        while tx_index < len(transactions) and transactions[tx_index]["fecha"] <= date_str:
            todays_transactions.append(transactions[tx_index])
            tx_index += 1

        # Actualiza last_known_price para TODO instrumento relevante hoy (los
        # que ya se tenian + los recien operados) antes de reconocer flujo de
        # caja -- si una posicion recien comprada todavia no tiene precio
        # conocido (fin de semana antes de la primera rueda, o backfill que
        # todavia no llego), su compra no se resta de golpe sin contrapartida
        # en valor: eso generaba retornos diarios absurdos (ej. -293% en un
        # dia). Se reconoce el flujo recien cuando haya precio con que valuarla.
        relevant_ids = set(cantidad_by_instrument.keys()) | {t["instrument_id"] for t in todays_transactions}
        for instrument_id in relevant_ids:
            price_series = price_by_instrument.get(instrument_id, {})
            if date_str in price_series:
                last_known_price[instrument_id] = price_series[date_str]

        cf = 0.0
        for t in todays_transactions:
            instrument = instruments.get(t["instrument_id"])
            signed_cantidad = t["cantidad"] if t["tipo"] == "compra" else -t["cantidad"]
            cantidad_by_instrument[t["instrument_id"]] += signed_cantidad
            if not instrument or last_known_price.get(t["instrument_id"]) is None:
                continue
            factor = _factor(instrument["currency"], date_str)
            if factor is None:
                continue
            cf += signed_cantidad * t["precio"] * factor

        valor = 0.0
        for instrument_id, cantidad in cantidad_by_instrument.items():
            if cantidad <= 0:
                continue
            instrument = instruments.get(instrument_id)
            if not instrument:
                continue
            price = last_known_price.get(instrument_id)
            if price is None:
                continue
            factor = _factor(instrument["currency"], date_str)
            if factor is None:
                continue
            valor += cantidad * price * factor

        if prev_value is not None and prev_value > 0:
            r_t = (valor - cf - prev_value) / prev_value
            daily_returns.append((date_str, r_t))
        else:
            # Se pisa cada dia hasta el ultimo antes del primer retorno real
            # -- queda con la fecha exacta del "dia 0" (valor recien
            # reconocido, todavia sin ningun retorno compuesto encima).
            anchor_date = date_str
        prev_value = valor

    periods = {}
    for key, period_start in _period_start_dates(end).items():
        relevant = daily_returns if period_start is None else [
            (d, r) for d, r in daily_returns if datetime.fromisoformat(d).date() >= period_start
        ]
        if not relevant:
            periods[key] = None
            continue
        compounded = 1.0
        for _, r in relevant:
            compounded *= (1 + r)
        periods[key] = {
            "twr_pct": round((compounded - 1) * 100, 2),
            "from_date": relevant[0][0],
            "to_date": relevant[-1][0],
        }

    # Ancla el "dia 0" en el indice en vez de arrancar ya compuesto por el
    # primer retorno -- sin esto, index_series[0] representaba el valor
    # DESPUES de aplicar daily_returns[0], nunca el punto de partida real
    # (100). El frontend rebasa el grafico dividiendo por index_series[0]
    # (ver benchmark-comparison-chart.js::_sliceAndRebase) -- sin este ancla,
    # eso hacia que el periodo "Desde inicio" se viera ~2pp mas bajo en el
    # grafico que el "Rendimiento TWR del período" del encabezado (mismo
    # numero, calculado distinto -- bug real, confirmado comparando
    # index_series[-1]/index_series[0] contra periods.inicio.twr_pct).
    index_series = []
    if anchor_date is not None:
        index_series.append({"date": anchor_date, "index": 100.0})
    index_value = 100.0
    for date_str, r in daily_returns:
        index_value *= (1 + r)
        index_series.append({"date": date_str, "index": round(index_value, 4)})

    return {"periods": periods, "index_series": index_series, "available": True}


# --- Ajuste por inflacion -------------------------------------------------------

def inflation_index_series(conn):
    """Indice acumulado de inflacion (IPC mensual), rebasado a 100 en la fecha
    mas vieja disponible -- cada punto dice cuanto valen $100 de esa fecha base
    expresados en pesos de ese mes."""
    fetchers.ensure_inflation_history(conn)
    rows = db.get_inflation_history(conn)
    if not rows:
        return {}
    index = {}
    value = 100.0
    for fecha, valor_mensual_pct in rows:
        index[fecha] = value
        value *= (1 + valor_mensual_pct / 100)
    return index


def adjust_for_inflation(valor, fecha, conn, index=None):
    """Convierte un monto ARS nominal de `fecha` a "pesos de hoy" usando el
    indice acumulado de inflacion (IPC es mensual, se usa el ultimo punto
    conocido del mes correspondiente). Degrada sin romper: si no hay cobertura
    del indice para esa fecha, devuelve el valor sin ajustar -- nunca se
    inventa un ajuste. `index` es opcional para no recalcularlo en cada
    llamada cuando se ajustan muchos puntos de una misma serie."""
    if index is None:
        index = inflation_index_series(conn)
    if not index:
        return valor
    known_dates = sorted(index)
    month_key = fecha[:7]
    candidates = [d for d in known_dates if d[:7] <= month_key]
    if not candidates:
        return valor
    base_date = candidates[-1]
    today_date = known_dates[-1]
    return valor * index[today_date] / index[base_date]
