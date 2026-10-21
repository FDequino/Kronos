"""Actualiza cotizaciones en background mientras el mercado esta abierto, y
publica cada actualizacion a los clientes conectados por SSE (/api/portfolio/
stream) para que el precio en pantalla cambie sin que el usuario recargue.

Mismo espiritu que forecast_worker.py (un solo thread en el proceso de Flask,
nada de Celery/Redis -- esto es una app local de un solo usuario), pero sin
cola: ac'a no hay "trabajos" que encolar, es un polling periodico simple.
"""
import logging
import queue
import threading
from datetime import datetime

from portfolio import db, fetchers
from portfolio.tz import ART_TZ

logger = logging.getLogger(__name__)

MARKET_OPEN_HOUR = 11
MARKET_CLOSE_HOUR = 17

# Intervalo en horario de mercado -- 60s de arranque, deliberadamente
# conservador: el rate-limit real de data912 NUNCA se verifico a esta
# frecuencia (hasta ahora se la llamaba solo bajo demanda, nunca en loop).
# Si en la practica empieza a devolver errores seguidos, hay que subir este
# numero -- no bajarlo a ciegas ni ignorar los errores en silencio (ver
# _tick, que loggea cada fallo por instrumento sin tirar abajo el resto).
MARKET_HOURS_INTERVAL_SECONDS = 60
OFF_HOURS_INTERVAL_SECONDS = 3600  # 1 vez por hora (FX/cierre), fuera de mercado

# Confirmado en produccion (ver _tick): instrumentos que data912 ya no cubre
# (ej. LETRAS vencidas -- S16O6/S30S6/TO26 no aparecen en NINGUN endpoint
# live de data912 al 2026-10-20) reintentaban cada MARKET_HOURS_INTERVAL_SECONDS
# para siempre. Mismo criterio que HISTORY_BACKFILL_RETRY_DAYS en history.py:
# no se deja de intentar nunca (la fuente podria volver a cubrirlo), pero se
# espacian los reintentos y se loggea un solo warning corto en vez de un
# traceback completo en cada ciclo.
QUOTE_RETRY_COOLDOWN_SECONDS = 1800

_subscribers = []
_subscribers_lock = threading.Lock()


def is_market_open(now=None):
    """11:00-17:00 ART, lunes a viernes -- mismo horario de rueda de BYMA que
    ya asume el resto del proyecto (ver fetchers.py)."""
    now = now or datetime.now(ART_TZ)
    return now.weekday() < 5 and MARKET_OPEN_HOUR <= now.hour < MARKET_CLOSE_HOUR


def subscribe():
    q = queue.Queue()
    with _subscribers_lock:
        _subscribers.append(q)
    return q


def unsubscribe(q):
    with _subscribers_lock:
        if q in _subscribers:
            _subscribers.remove(q)


def _publish(event):
    with _subscribers_lock:
        subs = list(_subscribers)
    for q in subs:
        q.put(event)


def _tick():
    conn = db.get_connection()
    try:
        for instrument in db.list_instruments(conn):
            failed_at = instrument.get("quote_failed_at")
            if failed_at:
                age_seconds = (datetime.utcnow() - datetime.fromisoformat(failed_at)).total_seconds()
                if age_seconds < QUOTE_RETRY_COOLDOWN_SECONDS:
                    continue
            try:
                before = db.get_quote(conn, instrument["id"])
                quote = fetchers.update_quote(conn, instrument)
                if quote is None:
                    continue  # price_source='manual' -- nada que publicar
                if failed_at:
                    db.update_instrument(conn, instrument["id"], quote_failed_at=None)
                var_pct = None
                if before and before.get("price"):
                    var_pct = round((quote["price"] - before["price"]) / before["price"] * 100, 2)
                _publish({"instrument_id": instrument["id"], "precio": quote["price"], "var_pct": var_pct})
            except Exception as exc:
                db.update_instrument(conn, instrument["id"], quote_failed_at=datetime.utcnow().isoformat())
                logger.warning("QuoteWorker: fallo actualizando %s (%s) -- reintenta en %ds",
                               instrument["symbol"], exc, QUOTE_RETRY_COOLDOWN_SECONDS)
    finally:
        conn.close()


def _loop():
    while True:
        _tick()
        interval = MARKET_HOURS_INTERVAL_SECONDS if is_market_open() else OFF_HOURS_INTERVAL_SECONDS
        threading.Event().wait(interval)


_thread = None


def start():
    """Llamado explicitamente desde webui/app.py (no al importar este modulo,
    a diferencia de forecast_worker.py) porque solo app.py sabe si el
    reloader de Werkzeug (debug=True) esta en juego para este proceso.

    El reloader relanza el script completo como un subproceso hijo
    (WERKZEUG_RUN_MAIN=true) y deja un proceso "watcher" corriendo el mismo
    modulo en paralelo (ese padre SI ejecuta todo el import-time de
    app.py/routes.py, incluida la cadena de imports hasta este archivo --
    arrancar el thread aca con un auto-start a nivel de modulo, como hace
    forecast_worker.py, arrancaria 2 pollers pegandole a data912 el doble de
    seguido). forecast_worker.py no necesita este cuidado porque una cola
    vacia con 2 consumidores no cuesta nada; un poller de red si. Por eso
    app.py llama a start() solo dentro de `if __name__ == '__main__'` y solo
    cuando WERKZEUG_RUN_MAIN ya vale "true" (o directamente no hay reloader)."""
    global _thread
    if _thread is not None:
        return
    _thread = threading.Thread(target=_loop, daemon=True, name="kronos-quote-worker")
    _thread.start()
