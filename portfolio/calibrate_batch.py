"""Batch SECUENCIAL de calibración/historial de pronósticos de Kronos (ver
conversación) -- dos alcances:

--scope catalogo (default): solo los instrumentos ya agregados a la cartera.
--scope byma: TODO el universo buscable de BYMA (acciones + CEDEARs, ver
    portfolio/byma_universe.py) que todavía no esté en el catálogo -- orden
    de magnitud mucho más grande (~1300 símbolos vs. ~25), y cada símbolo
    nuevo pega al menos una vez contra la API pública de data912 para traer
    su historial.

Corre forecasting.run_forecast_job DIRECTO, nunca en paralelo y sin pasar
por la cola en memoria de portfolio/forecast_worker.py -- ese módulo arranca
su propio hilo de worker + recupera jobs huérfanos apenas se IMPORTA (pensado
para vivir adentro del proceso de Flask, siempre corriendo); importarlo acá
solo para reusar su lógica de anchors dejaría un hilo fantasma esperando en
una cola que este script de una sola pasada nunca usa. Por eso
`_backtest_anchors` de abajo es una copia deliberada de
forecast_worker._backtest_anchors (mismo razonamiento, documentado ahí) --
no un import.

Para cada símbolo (instrumento de catálogo, o símbolo BYMA suelto):
1. Calcula qué fechas-ancla de backtest todavía faltan correr -- para
   --scope byma, esto exige que el backtest sea honesto también SIN
   instrument_id (ver forecasting.build_lookback_df, corregido para esto:
   antes esa rama ignoraba as_of_date por completo).
2. Corre cada ancla, una por una, con logging de progreso real (nunca
   silencioso: un batch de horas sin señales de vida es indistinguible de
   uno colgado) -- con --scope byma el volumen de anclas es demasiado alto
   para loguear cada una, así que se loguean ~10 checkpoints por símbolo en
   vez de cada ancla individual.
3. Al terminar las anclas de un símbolo (incluso si no había ninguna nueva),
   recalcula MAPE/acierto direccional/calibración p10-p90 desde accuracy.py
   y los persiste en la tabla `calibration_cache` -- ver schema.sql para la
   aclaración de que esto es un snapshot, no la fuente de verdad que sigue
   usando la UI en vivo.

bono/on se excluyen de --scope byma (no `on` no existe en el universo
buscable; `bono` sí, pero sin alta en catálogo no hay maturity_date -- sin
eso no se puede acotar el horizonte al vencimiento real, ver
_cap_pred_len_to_maturity, y predecir más allá del vencimiento no tiene
sentido). moneda/fondo/cripto se excluyen siempre, mismo motivo que
forecast_worker.enqueue_all_holdings.

Requiere el microservicio Chronos-Bolt levantado (services/predictor/, ver
model/client.py) -- si no responde, cada ancla individual falla, queda
registrada como error en `forecasts` y el batch sigue con la siguiente (de
ese símbolo y de los demás), nunca corta toda la corrida por un fallo
puntual.

Uso:
    python3 -m portfolio.calibrate_batch
    python3 -m portfolio.calibrate_batch --symbols GGAL,YPFD
    python3 -m portfolio.calibrate_batch --start-date 2024-01-01
    python3 -m portfolio.calibrate_batch --scope byma
    python3 -m portfolio.calibrate_batch --scope byma --symbols BBVA,TXAR
"""
import argparse
import logging
import sys
import time
from datetime import datetime, timedelta

from portfolio import accuracy, byma_universe, db, fetchers, forecasting
from portfolio.tz import today_ar

logger = logging.getLogger(__name__)

# Mismo criterio que forecast_worker.enqueue_all_holdings -- moneda/fondo/cripto
# no tienen un backtest de Kronos con sentido (moneda no es un precio de
# mercado propio; fondo/cripto sin histórico diario confiable para esto, ver
# conversación). Solo aplica a --scope catalogo -- --scope byma ya parte de
# accion/cedear únicamente (ver BYMA_SCOPE_TYPES).
EXCLUDED_TYPES = ("moneda", "fondo", "cripto")

# --scope byma: bono excluido a propósito -- ver docstring del módulo
# (sin maturity_date fuera de catálogo no se puede acotar el horizonte).
BYMA_SCOPE_TYPES = ("accion", "cedear")

# Mismas constantes que forecast_worker.py -- ver ese módulo para el
# razonamiento (30 pronósticos no superpuestos para "validado", ventana de
# 1500 días para poder llegar a esa cantidad con la cadencia semanal).
BACKTEST_ANCHOR_SPACING_DAYS = 7
BACKTEST_PRED_LEN = 35
BACKTEST_DEFAULT_WINDOW_DAYS = 1500

# Cachea el historial COMPLETO de cada símbolo por la vida de ESTE proceso
# (ver conversación) -- sin esto, --scope byma llamaría a
# fetch_data912_historical UNA VEZ POR ANCLA (hasta ~200 por símbolo) en vez
# de una sola vez por símbolo, multiplicando por ~200 el tráfico contra la
# API pública de data912 para nada (el historial de un símbolo no cambia
# mientras dura esta corrida). El parche vive SOLO en este proceso de batch
# -- nunca se toca el módulo compartido para el proceso de Flask.
_history_cache = {}
_original_fetch_data912_historical = fetchers.fetch_data912_historical


def _cached_fetch_data912_historical(symbol, asset_type):
    key = (symbol, asset_type)
    if key not in _history_cache:
        _history_cache[key] = _original_fetch_data912_historical(symbol, asset_type)
    return _history_cache[key].copy()


fetchers.fetch_data912_historical = _cached_fetch_data912_historical


def _make_target(*, instrument_id, symbol, market, asset_type, maturity_date=None):
    return {
        "instrument_id": instrument_id, "symbol": symbol, "market": market,
        "type": asset_type, "maturity_date": maturity_date,
    }


def _backtest_anchors(conn, target, config, start_date=None):
    """Copia deliberada de forecast_worker._backtest_anchors, extendida para
    símbolos SIN instrument_id (ver docstring del módulo) -- la fuente de
    price_rows/anclas-ya-cubiertas cambia según el caso, el resto de la
    lógica (ventanas, espaciado) es idéntica."""
    if target["instrument_id"] is not None:
        price_rows = db.get_price_history(conn, target["instrument_id"])
        already = db.list_backtest_anchors(conn, target["instrument_id"], model_key=config["default_model_key"])
    else:
        try:
            history_df = fetchers.fetch_data912_historical(target["symbol"], target["type"])
        except Exception as exc:
            logger.warning("  %s -- sin histórico en data912 (%s), se omite.", target["symbol"], exc)
            return []
        price_rows = [{"timestamp": ts.isoformat()} for ts in sorted(history_df["timestamps"])]
        already = db.list_backtest_anchors_by_symbol(
            conn, target["symbol"], target["market"], model_key=config["default_model_key"]
        )

    if len(price_rows) < config["default_lookback"]:
        return []

    earliest_with_lookback = (
        datetime.fromisoformat(price_rows[config["default_lookback"] - 1]["timestamp"].split("T")[0]).date()
    )
    today = today_ar()
    window_start = today - timedelta(days=BACKTEST_DEFAULT_WINDOW_DAYS)
    start = max(earliest_with_lookback, window_start)
    if start_date:
        start = max(start, datetime.fromisoformat(start_date).date())
        start = max(start, earliest_with_lookback)
    end = today - timedelta(days=BACKTEST_PRED_LEN)

    anchors = []
    cursor = start
    while cursor <= end:
        iso = cursor.isoformat()
        if iso not in already:
            anchors.append(iso)
        cursor += timedelta(days=BACKTEST_ANCHOR_SPACING_DAYS)
    return anchors


def _cap_pred_len_to_maturity(target, pred_len, as_of_date):
    """Mismo recorte que forecast_worker._cap_pred_len_to_maturity -- un
    bono/ON no se pronostica más allá de su propio vencimiento. Nunca se
    activa para --scope byma (bono excluido, maturity_date siempre None)."""
    if target["type"] not in ("bono", "on") or not target.get("maturity_date"):
        return pred_len
    reference = datetime.fromisoformat(as_of_date).date()
    maturity = datetime.fromisoformat(target["maturity_date"]).date()
    dias_restantes = (maturity - reference).days
    return min(pred_len, dias_restantes) if dias_restantes > 0 else pred_len


def _run_one_anchor(conn, target, config, anchor):
    """Corre UN backtest puntual de forma SÍNCRONA (nunca encolado) --
    devuelve True/False, nunca lanza: un ancla fallida (ej. Chronos-Bolt
    caído a mitad de la corrida) queda registrada en `forecasts` con su
    error real y el batch sigue con la siguiente."""
    pred_len = _cap_pred_len_to_maturity(target, BACKTEST_PRED_LEN, anchor)
    expected = config["benchmark_seconds_per_step"] * pred_len * 1
    forecast_id = db.insert_forecast_job(
        conn, instrument_id=target["instrument_id"], symbol=target["symbol"], market=target["market"],
        asset_type=target["type"], model_key=config["default_model_key"], lookback=config["default_lookback"],
        pred_len=pred_len, temperature=config["default_temperature"], top_p=config["default_top_p"],
        sample_count=1, expected_duration_seconds=expected, as_of_date=anchor,
    )
    db.mark_forecast_running(conn, forecast_id)
    try:
        forecasting.run_forecast_job(conn, forecast_id)
        return True
    except Exception as exc:
        logger.warning("    %s %s -- ERROR: %s", target["symbol"], anchor, exc)
        db.mark_forecast_error(conn, forecast_id, str(exc))
        return False


def _update_calibration_cache(conn, target):
    """Recalcula MAPE/acierto direccional/calibración desde accuracy.py
    (misma fuente que usa la UI en vivo) -- ver schema.sql, el resultado es
    un snapshot, no reemplaza ese cálculo en vivo."""
    if target["instrument_id"] is not None:
        forecasts = db.list_forecasts_for_instrument(conn, target["instrument_id"])
        price_history = db.get_price_history(conn, target["instrument_id"])
    else:
        forecasts = db.list_forecasts_by_symbol(conn, target["symbol"], target["market"])
        try:
            history_df = fetchers.fetch_data912_historical(target["symbol"], target["type"])
            # Mismas columnas que una fila real de price_history (ver
            # schema.sql) -- accuracy.compute_accuracy arma velas completas
            # (open/high/low/close), no solo el cierre.
            price_history = [
                {
                    "timestamp": row.timestamps.isoformat(), "open": row.open, "high": row.high,
                    "low": row.low, "close": row.close, "volume": row.volume,
                }
                for row in history_df.itertuples()
            ]
        except Exception:
            price_history = []
    acc = accuracy.compute_accuracy(forecasts, price_history, conn=conn)
    acc = acc if (acc and not acc.get("pending")) else None
    calibration = accuracy.compute_calibration(forecasts, price_history, conn=conn)
    return acc, calibration


def _build_targets(conn, scope, symbols):
    if scope == "byma":
        catalog_symbols = {i["symbol"].upper() for i in db.list_instruments(conn)}
        targets = [
            _make_target(instrument_id=None, symbol=row["symbol"], market=row["market"], asset_type=row["type"])
            for row in byma_universe._load()
            if row["type"] in BYMA_SCOPE_TYPES and row["symbol"].upper() not in catalog_symbols
        ]
    else:
        instruments = db.list_instruments(conn)
        if not symbols:
            instruments = [i for i in instruments if i["type"] not in EXCLUDED_TYPES]
        targets = [
            _make_target(
                instrument_id=i["id"], symbol=i["symbol"], market=i["market"],
                asset_type=i["type"], maturity_date=i.get("maturity_date"),
            )
            for i in instruments
        ]
    if symbols:
        wanted = {s.strip().upper() for s in symbols}
        targets = [t for t in targets if t["symbol"].upper() in wanted]
    return targets


def run_batch(symbols=None, start_date=None, scope="catalogo"):
    conn = db.get_connection()
    try:
        targets = _build_targets(conn, scope, symbols)
        config = db.get_signal_config(conn)
    finally:
        conn.close()

    total = len(targets)
    logger.info("Calibración batch (scope=%s): %d símbolo(s) a procesar (secuencial).", scope, total)
    results = []

    for idx, target in enumerate(targets, start=1):
        symbol = target["symbol"]
        start_ts = time.monotonic()
        conn = db.get_connection()
        try:
            anchors = _backtest_anchors(conn, target, config, start_date=start_date)
            ok = failed = 0
            if not anchors:
                logger.info("[%d/%d] %s -- sin anclas nuevas (o sin histórico suficiente).", idx, total, symbol)
            else:
                logger.info("[%d/%d] %s -- %d ancla(s) de backtest por correr.", idx, total, symbol, len(anchors))
                # Checkpoints espaciados (~10 por símbolo) en vez de loguear
                # cada ancla -- con --scope byma el volumen (hasta ~200
                # anclas x ~1300 símbolos) haría el log ilegible/gigante.
                checkpoint_every = max(1, len(anchors) // 10)
                for anchor_idx, anchor in enumerate(anchors, start=1):
                    if _run_one_anchor(conn, target, config, anchor):
                        ok += 1
                    else:
                        failed += 1
                    if anchor_idx % checkpoint_every == 0 or anchor_idx == len(anchors):
                        logger.info("    %s %s (%d/%d) ok=%d error=%d", symbol, anchor, anchor_idx, len(anchors), ok, failed)

            acc, calibration = _update_calibration_cache(conn, target)
            db.upsert_calibration_cache(
                conn, symbol=symbol, market=target["market"], instrument_id=target["instrument_id"],
                mape=acc["mape"] if acc else None,
                directional_hit_pct=acc["directional_hit_pct"] if acc else None,
                forecasts_evaluated=acc["forecasts_evaluated"] if acc else 0,
                calibration_pct=calibration["coverage_pct"] if calibration else None,
                calibration_n=calibration["n"] if calibration else None,
                anchors_run=ok, anchors_failed=failed,
            )
            elapsed = time.monotonic() - start_ts
            mape_txt = f"{acc['mape']:.2f}%" if acc else "sin pronósticos resueltos todavía"
            logger.info(
                "[%d/%d] %s completo en %.1fs -- %d ok, %d error(es). MAPE=%s",
                idx, total, symbol, elapsed, ok, failed, mape_txt,
            )
            results.append({"symbol": symbol, "accuracy": acc, "calibration": calibration, "anchors_run": ok, "anchors_failed": failed})
        except Exception:
            logger.exception("[%d/%d] %s -- fallo inesperado, se sigue con el resto.", idx, total, symbol)
        finally:
            conn.close()

    logger.info("Calibración batch (scope=%s) terminada: %d/%d procesados.", scope, len(results), total)
    return results


def main():
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout,
    )
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--symbols", help="Lista separada por comas (ej. GGAL,YPFD) -- default: todo el alcance elegido.")
    parser.add_argument("--start-date", help="No retroceder antes de esta fecha (YYYY-MM-DD).")
    parser.add_argument(
        "--scope", choices=["catalogo", "byma"], default="catalogo",
        help="'catalogo' (default): solo instrumentos ya agregados a la cartera. "
             "'byma': TODO el universo buscable de BYMA (acciones+CEDEARs) que no esté ya en el catálogo -- "
             "mucho más grande, pega contra la API pública de data912 una vez por símbolo nuevo.",
    )
    args = parser.parse_args()
    symbols = args.symbols.split(",") if args.symbols else None
    run_batch(symbols=symbols, start_date=args.start_date, scope=args.scope)


if __name__ == "__main__":
    main()
