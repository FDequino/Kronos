"""Renta fija: TIR, duration y calendario de vencimientos/cupones para bono/on.

Modelo bullet simple (un solo pago de capital al vencimiento + cupones
periodicos, valor nominal 1 por unidad -- ver fetchers.py::_bono_on_divisor,
que ya normaliza el precio de data912 a esa convencion). Simplificacion
declarada: no contempla amortizaciones parciales escalonadas.

Bonos ajustables por CER (rate_type='cer') NO se calculan localmente -- el
valor nominal futuro depende de un indice de inflacion que no se puede
proyectar sin inventar un numero. En cambio se usa un precio+TIR ya calculado
por un tercero: api.argentinadatos.com/v1/finanzas/bonos-cer (fuente: Docta
Capital), gratis y sin key, confirmado con curl real que cubre TZXY7/TZX27.

Complementario (no bloqueante): fetch_cer_futures() consulta el feed publico
de A3 Mercados (ex Matba Rofex) por WebSocket para ver si hay precio de
mercado de futuros de CER -- confirmado que el feed funciona sin login, pero
hoy (2026-10) los 2 contratos vigentes no tienen ninguna operacion. Devuelve
{} sin romper nada cuando no hay precio, no es una fuente de TIR en si misma.

Carry Trade LECAPs vs CER (ver conversacion, al final del archivo):
fetch_letras_market_data() usa la MISMA fuente que bonos-cer
(api.argentinadatos.com/v1/finanzas/letras, confirmado real, devuelve
TNA/TEA/TEM ya calculadas) y compute_carry_trade_rotation_signals() compara
esa TEM nominal contra la TIR real de los bonos CER (convertida a nominal via
Fisher, usando la ultima inflacion mensual REALIZADA como proxy declarado de
la esperada) para sugerir hacia que curva rotar.
"""
import asyncio
import calendar
import json
import re
import time
from datetime import date, datetime, timedelta

import requests

from portfolio.fetchers import fetch_inflation_history
from portfolio.formatting import fmt_es
from portfolio.tz import today_ar

CER_BONDS_URL = "https://api.argentinadatos.com/v1/finanzas/bonos-cer"
_CER_BONDS_CACHE_TTL = 3600
_cer_bonds_cache = {"data": None, "fetched_at": 0.0}

A3_WS_URL = "wss://visordeprecios.a3mercados.com.ar/ws?session_id=&conn_id="
A3_REFDATA_URL = "https://visordeprecios.a3mercados.com.ar/api/v2/ref-data"


# --- Bonos CER (fuente externa, TIR ya calculada) ------------------------------

def fetch_cer_bonds_market_data():
    """{ticker: {precio_ars, tir_pct, fecha_vencimiento, volumen}} -- cacheado
    en memoria (mismo criterio que fetchers.py::_bna_cache) para no pegarle a
    la fuente en cada request."""
    if _cer_bonds_cache["data"] is not None and time.time() - _cer_bonds_cache["fetched_at"] < _CER_BONDS_CACHE_TTL:
        return _cer_bonds_cache["data"]
    resp = requests.get(CER_BONDS_URL, timeout=20)
    resp.raise_for_status()
    raw = resp.json()
    data = {
        b["ticker"]: {
            "precio_ars": b["precioArs"], "tir_pct": b["tirPorcentaje"],
            "fecha_vencimiento": b["fechaVencimiento"], "volumen": b.get("volumen"),
        }
        for b in raw.get("bonos", [])
    }
    _cer_bonds_cache["data"] = data
    _cer_bonds_cache["fetched_at"] = time.time()
    return data


# --- Bono bullet simple (tasa fija / cupon cero) -------------------------------

def _shift_months_back(d, months):
    month_index = d.month - 1 - int(round(months))
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _cash_flow_schedule(maturity, coupon_rate, coupon_frequency, as_of):
    """[(fecha, monto_por_unidad)] desde as_of (exclusive) hasta el vencimiento.
    Cupon cero (coupon_rate/coupon_frequency faltante o 0): un unico flujo al
    vencimiento = 1 (capital). Con cupon: pagos periodicos de
    coupon_rate/100/frequency, el ultimo flujo suma ademas el capital."""
    if not coupon_rate or not coupon_frequency:
        return [(maturity, 1.0)]

    months_step = 12 / coupon_frequency
    coupon_amount = (coupon_rate / 100) / coupon_frequency

    dates = []
    d = maturity
    while d > as_of:
        dates.append(d)
        d = _shift_months_back(d, months_step)
    dates.reverse()
    if not dates:
        return [(maturity, 1.0)]

    schedule = [(d, coupon_amount) for d in dates]
    last_date, last_amount = schedule[-1]
    schedule[-1] = (last_date, last_amount + 1.0)
    return schedule


def _npv(rate, schedule, as_of, frequency):
    # Convencion estandar de mercado: tasa nominal anual (TNA) compuesta
    # `frequency` veces por año (igual convencion que ya usa el resto del
    # proyecto para TNA -- caucion_reference_tna, plazos_fijos.tna), no tasa
    # efectiva anual. Con esto un bono a la par da TIR == cupon exactamente.
    return sum(
        amount / (1 + rate / frequency) ** (((d - as_of).days / 365) * frequency)
        for d, amount in schedule
    )


def _solve_tir(schedule, price, as_of, frequency, low=-0.9, high=10.0, iterations=100, tol=1e-6):
    """Biseccion sobre NPV(rate) - price = 0 -- NPV es monotona decreciente en
    rate para flujos positivos, la raiz es unica en el rango."""
    f_low = _npv(low, schedule, as_of, frequency) - price
    f_high = _npv(high, schedule, as_of, frequency) - price
    if f_low * f_high > 0:
        return None
    for _ in range(iterations):
        mid = (low + high) / 2
        f_mid = _npv(mid, schedule, as_of, frequency) - price
        if abs(f_mid) < tol:
            return mid
        if f_low * f_mid < 0:
            high, f_high = mid, f_mid
        else:
            low, f_low = mid, f_mid
    return (low + high) / 2


def _duration(schedule, rate, price, as_of, frequency):
    weighted_sum = sum(
        ((d - as_of).days / 365) * (amount / (1 + rate / frequency) ** (((d - as_of).days / 365) * frequency))
        for d, amount in schedule
    )
    macaulay = weighted_sum / price
    modified = macaulay / (1 + rate / frequency)
    return macaulay, modified


def compute_bullet_bond_metrics(price, maturity_date, coupon_rate, coupon_frequency, as_of=None):
    as_of = as_of or today_ar()
    if not maturity_date or not price:
        return None
    maturity = datetime.fromisoformat(maturity_date).date()
    if maturity <= as_of:
        return None

    frequency = coupon_frequency or 1
    schedule = _cash_flow_schedule(maturity, coupon_rate, coupon_frequency, as_of)
    rate = _solve_tir(schedule, price, as_of, frequency)
    if rate is None:
        return None
    macaulay, modified = _duration(schedule, rate, price, as_of, frequency)
    return {
        "tir_pct": round(rate * 100, 2),
        "duration_years": round(macaulay, 2),
        "modified_duration": round(modified, 2),
        "fuente": "calculado, modelo bono bullet simple",
    }


# --- Resumen por instrumento ----------------------------------------------------

def compute_fixed_income_summary(instrument, price):
    if instrument["type"] not in ("bono", "on"):
        return {"available": False, "reason": "No aplica para este tipo de instrumento."}

    if instrument.get("rate_type") == "cer":
        try:
            market_data = fetch_cer_bonds_market_data()
        except Exception as e:
            return {"available": False, "reason": f"No se pudo consultar la fuente de bonos CER: {e}"}
        data = market_data.get(instrument["symbol"])
        if not data:
            return {
                "available": False,
                "reason": f"{instrument['symbol']} no está listado en la fuente de bonos CER (argentinadatos).",
            }
        return {
            "available": True,
            "tir_pct": data["tir_pct"],
            "precio_mercado": data["precio_ars"],
            "fuente": "argentinadatos / Docta Capital",
            "duration_years": None,
            "duration_reason": "No calculada para bonos CER: requeriría proyectar el índice CER futuro.",
        }

    if not instrument.get("maturity_date"):
        return {"available": False, "reason": "Cargá la fecha de vencimiento y el cupón del instrumento."}
    if not price:
        return {"available": False, "reason": "Sin precio de mercado disponible."}

    metrics = compute_bullet_bond_metrics(
        price, instrument["maturity_date"], instrument.get("coupon_rate"), instrument.get("coupon_frequency"),
    )
    if metrics is None:
        return {"available": False, "reason": "No se pudo calcular la TIR con los datos cargados."}
    return {"available": True, **metrics}


def explain_fixed_income(symbol, summary):
    if not summary.get("available"):
        return summary.get("reason") or "Sin datos de renta fija disponibles."
    base = f"{symbol}: TIR {fmt_es(summary['tir_pct'], decimals=2)}% (fuente: {summary['fuente']})."
    if summary.get("duration_years") is not None:
        base += (
            f" Duration {fmt_es(summary['duration_years'], decimals=2)} años "
            f"(modificada {fmt_es(summary['modified_duration'], decimals=2)})."
        )
    elif summary.get("duration_reason"):
        base += f" Duration: {summary['duration_reason']}"
    return base


# --- Calendario de vencimientos/cupones ----------------------------------------

def upcoming_fixed_income_events(instruments, as_of=None, horizon_days=365):
    as_of = as_of or today_ar()
    horizon = as_of + timedelta(days=horizon_days)
    events = []
    for inst in instruments:
        if inst["type"] not in ("bono", "on") or not inst.get("maturity_date"):
            continue
        maturity = datetime.fromisoformat(inst["maturity_date"]).date()
        if maturity < as_of:
            continue
        schedule = _cash_flow_schedule(maturity, inst.get("coupon_rate"), inst.get("coupon_frequency"), as_of)
        for d, _amount in schedule:
            if as_of <= d <= horizon:
                events.append({
                    "symbol": inst["symbol"],
                    "date": d.isoformat(),
                    "kind": "vencimiento" if d == maturity else "cupon",
                    "dias": (d - as_of).days,
                })
    events.sort(key=lambda e: e["date"])
    return events


# --- Futuros de CER (A3 Mercados, complementario) -------------------------------

def _iso_from_yyyymmdd(yyyymmdd):
    return f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:]}"


def _discover_cer_futures_topics():
    resp = requests.get(A3_REFDATA_URL, timeout=20)
    resp.raise_for_status()
    securities = resp.json().get("securities", [])
    return [
        {"id": s["id"], "maturity_date": _iso_from_yyyymmdd(s["maturityDate"]), "description": s["description"]}
        for s in securities
        if s.get("underlying") == "rx_DUAL_CER" and s.get("maturityDate")
    ]


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _parse_market_data_message(raw):
    """Formato pipe-delimitado reverse-engineered de trafico real (sin
    documentacion publica): un precio real siempre viene seguido por su fecha
    (ej. "...|1514.2|2026-10-09|..."). Otros campos numericos (volumen, size,
    contadores en 0) NO tienen una fecha atras y se descartan -- sin este
    chequeo, un contrato sin operaciones parseaba un "0" suelto como si fuera
    precio."""
    parts = raw.split("|")
    symbol = parts[0].split(":", 1)[1] if ":" in parts[0] else parts[0]
    price = None
    for i in range(len(parts) - 2, 0, -1):
        if not _DATE_RE.match(parts[i + 1]):
            continue
        try:
            price = float(parts[i])
            break
        except ValueError:
            continue
    return symbol, price


async def _collect_cer_futures_async(topic_ids, timeout):
    import websockets

    prices = {}
    async with websockets.connect(A3_WS_URL, open_timeout=10) as ws:
        await ws.send(json.dumps({
            "_req": "S", "topicType": "md",
            "topics": [f"md.{t}" for t in topic_ids], "replace": False,
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


def fetch_cer_futures(timeout=8):
    """Precio de referencia mas reciente de los futuros de CER vigentes.
    Conexion bajo demanda (nada de procesos en segundo plano nuevos): se
    suscribe, espera `timeout` segundos y devuelve lo que haya. Confirmado
    real (2026-10): estos contratos no tienen ninguna operacion todavia, asi
    que esto puede devolver price=None para todos sin que sea un error --
    es la realidad del mercado, no una falla de la integracion."""
    try:
        topics_info = _discover_cer_futures_topics()
    except Exception:
        return {}
    if not topics_info:
        return {}
    try:
        prices = asyncio.run(_collect_cer_futures_async([t["id"] for t in topics_info], timeout))
    except Exception:
        return {}
    return {
        t["id"]: {
            "maturity_date": t["maturity_date"], "description": t["description"],
            "price": prices.get(t["id"]),
        }
        for t in topics_info
    }


# --- Carry Trade: LECAPs vs Bonos CER (ver conversacion) ------------------------
#
# Compara el rendimiento mensual esperado entre letras a tasa fija (LECAPs/
# BONCAP, ARS nominal) y bonos ajustados por CER (ARS real) de vencimiento
# similar, para sugerir hacia cual rotar.
#
# Simplificacion declarada (necesaria para no inventar una proyeccion de
# inflacion): se usa el ULTIMO dato de inflacion MENSUAL REALIZADA
# (fetchers.fetch_inflation_history(), serie publica de IPC) como proxy de la
# inflacion esperada de los proximos meses -- NO es un pronostico. Si la
# inflacion futura termina siendo mayor a la ultima realizada, el bono CER en
# la realidad rinde mas en terminos nominales de lo que esta funcion calcula
# (y viceversa si termina siendo menor).
#
# Emparejamiento de curva: cada bono CER se empareja con la LECAP de
# vencimiento MAS CERCANO en dias (no una grilla de buckets fijos de
# duracion), para comparar activos de plazo similar.

LETRAS_URL = "https://api.argentinadatos.com/v1/finanzas/letras"
_LETRAS_CACHE_TTL = 3600
_letras_cache = {"data": None, "fetched_at": 0.0}


def fetch_letras_market_data():
    """{ticker: {precio_ars, tna_pct, tea_pct, tem_pct, fecha_vencimiento,
    dias_al_vencimiento, volumen}} -- misma fuente (argentinadatos.com) y
    mismo criterio de cache que fetch_cer_bonds_market_data. Endpoint
    confirmado real con una consulta directa: /v1/finanzas/letras devuelve
    las LECAPs/BONCAP vigentes con TNA/TEA/TEM ya calculadas por la fuente
    (no hace falta derivarlas de precio+vencimiento como con un bono bullet
    comun, a diferencia de compute_bullet_bond_metrics de arriba)."""
    if _letras_cache["data"] is not None and time.time() - _letras_cache["fetched_at"] < _LETRAS_CACHE_TTL:
        return _letras_cache["data"]
    resp = requests.get(LETRAS_URL, timeout=20)
    resp.raise_for_status()
    raw = resp.json()
    data = {
        item["ticker"]: {
            "precio_ars": item["precioArs"], "tna_pct": item["tnaPorcentaje"], "tea_pct": item["teaPorcentaje"],
            "tem_pct": item["temPorcentaje"], "fecha_vencimiento": item["fechaVencimiento"],
            "dias_al_vencimiento": item.get("diasAlVencimiento"), "volumen": item.get("volumen"),
        }
        for item in raw.get("letras", [])
    }
    _letras_cache["data"] = data
    _letras_cache["fetched_at"] = time.time()
    return data


def _latest_monthly_inflation_pct():
    """Ultimo dato mensual REALIZADO de inflacion -- ver nota de modulo
    arriba, es un proxy declarado de la inflacion esperada, nunca un
    pronostico. None si la fuente no devolvio nada."""
    history = fetch_inflation_history()
    if not history:
        return None
    return history[-1]["valor_mensual_pct"]


def _cer_real_annual_to_nominal_monthly_pct(tir_real_anual_pct, inflacion_mensual_pct):
    """Convierte la TIR real anual de un bono CER a una tasa efectiva mensual
    NOMINAL comparable con la TEM de una LECAP (relacion de Fisher): primero
    la TIR real anual a mensual real (geometrica), despues se compone con la
    inflacion mensual proxy para obtener la tasa nominal mensual."""
    real_monthly = (1 + tir_real_anual_pct / 100) ** (1 / 12) - 1
    nominal_monthly = (1 + real_monthly) * (1 + inflacion_mensual_pct / 100) - 1
    return nominal_monthly * 100


def compute_carry_trade_rotation_signals(conn=None):
    """Punto de entrada unico de este sub-modulo. `conn` se acepta por
    consistencia de interfaz con los demas motores de
    portfolio/live_signals.py (compute_pairs_signals, compute_factor_signals,
    etc.) pero NO se usa: esto es pura comparacion de curvas de mercado, no
    depende de las tenencias del usuario. Devuelve una lista de señales de
    rotacion (una por bono CER emparejado con su LECAP de vencimiento mas
    cercano), ordenada por spread descendente -- lista vacia si alguna fuente
    no esta disponible (nunca rompe el dispatcher unificado)."""
    try:
        letras = fetch_letras_market_data()
    except Exception:
        return []
    if not letras:
        return []
    try:
        cer_bonds = fetch_cer_bonds_market_data()
    except Exception:
        return []
    if not cer_bonds:
        return []
    try:
        inflacion_mensual_pct = _latest_monthly_inflation_pct()
    except Exception:
        inflacion_mensual_pct = None
    if inflacion_mensual_pct is None:
        return []

    today = today_ar()
    letras_list = []
    for ticker, data in letras.items():
        dias = data.get("dias_al_vencimiento")
        if dias is None:
            try:
                dias = (datetime.fromisoformat(data["fecha_vencimiento"]).date() - today).days
            except (ValueError, TypeError):
                continue
        if dias and dias > 0 and data.get("tem_pct") is not None:
            letras_list.append({"ticker": ticker, "dias": dias, "tem_pct": data["tem_pct"]})
    if not letras_list:
        return []

    signals = []
    for ticker, cer in cer_bonds.items():
        if not cer.get("fecha_vencimiento") or cer.get("tir_pct") is None:
            continue
        try:
            cer_dias = (datetime.fromisoformat(cer["fecha_vencimiento"]).date() - today).days
        except ValueError:
            continue
        if cer_dias <= 0:
            continue

        nearest = min(letras_list, key=lambda l: abs(l["dias"] - cer_dias))
        cer_tem_nominal_pct = _cer_real_annual_to_nominal_monthly_pct(cer["tir_pct"], inflacion_mensual_pct)
        lecap_tem_pct = nearest["tem_pct"]
        better = "LECAP" if lecap_tem_pct > cer_tem_nominal_pct else "CER"
        dias_diff = abs(nearest["dias"] - cer_dias)
        note = (
            f"{better} rinde más este mes a vencimiento similar (TEM {fmt_es(lecap_tem_pct, decimals=2)}% "
            f"en {nearest['ticker']} vs {fmt_es(cer_tem_nominal_pct, decimals=2)}% nominal equivalente en {ticker}, "
            f"CER a TIR real {fmt_es(cer['tir_pct'], decimals=2)}%) -- diferencia de {dias_diff} días entre vencimientos."
        )
        signals.append({
            "bucket_label": f"~{min(nearest['dias'], cer_dias)}-{max(nearest['dias'], cer_dias)} días",
            "lecap_symbol": nearest["ticker"],
            "lecap_tem_pct": round(lecap_tem_pct, 3),
            "cer_symbol": ticker,
            "cer_tem_nominal_pct": round(cer_tem_nominal_pct, 3),
            "better": better,
            "spread_pct": round(abs(lecap_tem_pct - cer_tem_nominal_pct), 3),
            "dias_diff": dias_diff,
            "note": note,
        })

    signals.sort(key=lambda s: -s["spread_pct"])
    return signals
