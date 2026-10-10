"""Un unico worker en background para correr forecasts de Kronos sin bloquear Flask.

Nada de Celery/Redis: el predictor es una sola instancia de modelo sobre un
device -- dos inferencias en paralelo no serian mas rapidas, y esto es una app
local de un solo usuario. Un thread + una cola alcanza.
"""
import logging
import queue
import threading
from datetime import datetime, timedelta

from portfolio import db, forecasting

logger = logging.getLogger(__name__)

_queue = queue.Queue()

BACKTEST_ANCHOR_SPACING_DAYS = 7
BACKTEST_PRED_LEN = 35
BACKTEST_DEFAULT_WINDOW_DAYS = 180


def submit_forecast(*, instrument_id=None, symbol, market, asset_type, sample_count=None,
                     pred_len=None, as_of_date=None):
    conn = db.get_connection()
    try:
        config = db.get_signal_config(conn)
        sample_count = sample_count or config["default_sample_count"]
        pred_len = pred_len or config["default_pred_len"]
        expected = config["benchmark_seconds_per_step"] * pred_len * sample_count
        forecast_id = db.insert_forecast_job(
            conn, instrument_id=instrument_id, symbol=symbol, market=market, asset_type=asset_type,
            model_key=config["default_model_key"], lookback=config["default_lookback"],
            pred_len=pred_len, temperature=config["default_temperature"], top_p=config["default_top_p"],
            sample_count=sample_count, expected_duration_seconds=expected, as_of_date=as_of_date,
        )
    finally:
        conn.close()
    _queue.put(forecast_id)
    return forecast_id


def _is_fresh(conn, instrument_id, freshness_hours):
    latest = db.get_latest_forecast(conn, instrument_id=instrument_id)
    if not latest or latest["status"] != "done" or not latest["finished_at"]:
        return False
    age_hours = (datetime.utcnow() - datetime.fromisoformat(latest["finished_at"])).total_seconds() / 3600
    return age_hours < freshness_hours


def enqueue_all_holdings(force=False):
    conn = db.get_connection()
    try:
        config = db.get_signal_config(conn)
        holdings = [h for h in db.get_holdings(conn) if h["instrument"]["type"] != "fondo"]
        targets = [
            h["instrument"] for h in holdings
            if force or not _is_fresh(conn, h["instrument"]["id"], config["freshness_hours"])
        ]
    finally:
        conn.close()

    return [
        submit_forecast(instrument_id=i["id"], symbol=i["symbol"], market=i["market"], asset_type=i["type"])
        for i in targets
    ]


def _backtest_anchors(conn, instrument, config, start_date=None):
    """Fechas pasadas donde todavia no corrimos un backtest para este instrumento,
    espaciadas BACKTEST_ANCHOR_SPACING_DAYS dias, acotadas a:
    - no antes de que exista suficiente historico previo (config['default_lookback'] dias antes)
    - no despues de hoy menos BACKTEST_PRED_LEN (para que el tramo predicho entero ya sea pasado)
    - start_date (si se pasa) nunca se ignora: es desde cuando importa la cartera."""
    price_rows = db.get_price_history(conn, instrument["id"])
    if len(price_rows) < config["default_lookback"]:
        return []

    earliest_with_lookback = (
        datetime.fromisoformat(price_rows[config["default_lookback"] - 1]["timestamp"].split("T")[0]).date()
    )
    today = datetime.utcnow().date()
    window_start = today - timedelta(days=BACKTEST_DEFAULT_WINDOW_DAYS)
    start = max(earliest_with_lookback, window_start)
    if start_date:
        start = max(start, datetime.fromisoformat(start_date).date())
        start = max(start, earliest_with_lookback)
    end = today - timedelta(days=BACKTEST_PRED_LEN)

    already = db.list_backtest_anchors(conn, instrument["id"])
    anchors = []
    cursor = start
    while cursor <= end:
        iso = cursor.isoformat()
        if iso not in already:
            anchors.append(iso)
        cursor += timedelta(days=BACKTEST_ANCHOR_SPACING_DAYS)
    return anchors


def enqueue_backtest(instrument_id, start_date=None):
    """Encola un backtest retroactivo para un instrumento: una corrida de Kronos por
    cada fecha ancla, usando solo datos disponibles hasta esa fecha. Devuelve la
    lista de forecast_ids encolados (puede ser vacia si ya estaban todos cubiertos)."""
    conn = db.get_connection()
    try:
        instrument = db.get_instrument(conn, instrument_id)
        if not instrument:
            return []
        config = db.get_signal_config(conn)
        anchors = _backtest_anchors(conn, instrument, config, start_date=start_date)
    finally:
        conn.close()

    return [
        submit_forecast(
            instrument_id=instrument["id"], symbol=instrument["symbol"], market=instrument["market"],
            asset_type=instrument["type"], sample_count=1, pred_len=BACKTEST_PRED_LEN, as_of_date=anchor,
        )
        for anchor in anchors
    ]


def enqueue_portfolio_backtest():
    """Backtest retroactivo de todas las tenencias actuales, cada una desde su
    primera transaccion -- no tiene sentido retroceder mas alla de cuando se
    empezo a tener la posicion, ya que la cartera reconstruida no la usa antes."""
    conn = db.get_connection()
    try:
        holdings = [h for h in db.get_holdings(conn) if h["instrument"]["type"] != "fondo"]
        starts = {}
        for h in holdings:
            txs = db.list_transactions(conn, h["instrument"]["id"])
            if txs:
                starts[h["instrument"]["id"]] = min(t["fecha"] for t in txs)
        instrument_ids = [h["instrument"]["id"] for h in holdings]
    finally:
        conn.close()

    forecast_ids = []
    for instrument_id in instrument_ids:
        forecast_ids.extend(enqueue_backtest(instrument_id, start_date=starts.get(instrument_id)))
    return forecast_ids


def _worker_loop():
    while True:
        forecast_id = _queue.get()
        conn = db.get_connection()
        try:
            db.mark_forecast_running(conn, forecast_id)
            forecasting.run_forecast_job(conn, forecast_id)
        except Exception as exc:
            logger.exception("Forecast %s fallo", forecast_id)
            db.mark_forecast_error(conn, forecast_id, str(exc))
        finally:
            conn.close()
            _queue.task_done()


def _recover_orphaned_jobs():
    """Jobs en queued/running al importar este modulo quedaron huerfanos de una
    corrida anterior (p.ej. el reload de debug=True mato al worker a mitad de camino)."""
    conn = db.get_connection()
    try:
        for job in db.list_pending_forecasts(conn):
            db.mark_forecast_error(conn, job["id"], "Interrumpido por reinicio del servidor")
    finally:
        conn.close()


_recover_orphaned_jobs()
threading.Thread(target=_worker_loop, daemon=True, name="kronos-forecast-worker").start()
