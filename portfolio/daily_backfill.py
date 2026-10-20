"""Backfill diario de price_history via cron -- reemplaza la dependencia de
que corra un pronostico (o que un instrumento recien se quede sin ninguna
fila) para que price_history se actualice.

Los 2 puntos de entrada que ya existian (portfolio/history.py::_ensure_history
y portfolio/forecasting.py::build_lookback_df) solo llaman a
fetchers.backfill_history cuando FALTA data -- cero filas, o menos filas que
el lookback que pide un pronostico puntual. Una vez que un instrumento ya
tiene suficiente historico para esas 2 condiciones, backfill_history nunca
se vuelve a llamar para el: price_history queda congelado en lo que sea que
tenia ese dia, sin sumar las ruedas nuevas que data912 va publicando dia a
dia (fetchers.backfill_history trae y pisa la serie COMPLETA cada vez que se
llama, asi que si se llamara de nuevo si traeria lo nuevo -- el problema es
que nada lo vuelve a llamar).

Este job corre backfill_history para TODOS los instrumentos de catalogo con
price_source=='data912' y type en history.HISTORICAL_TYPES, sin importar
cuanta historia tengan ya -- pensado para una corrida diaria por cron
(despues del cierre de rueda de BYMA), no para vivir en un thread del
proceso de Flask (a diferencia de quote_worker.py/forecast_worker.py, que
son pollers de baja latencia -- este es un job pesado de una sola pasada por
dia, mismo espiritu que portfolio/calibrate_batch.py).

Crontab sugerido (corre a las 19:00 ART, despues del cierre de BYMA a las
17:00 + margen para que data912 publique el cierre del dia):
    0 19 * * 1-5 cd /opt/kronos/app && .venv/bin/python3 -m portfolio.daily_backfill >> /var/log/kronos-daily-backfill.log 2>&1
"""
import logging
import sys
from datetime import datetime

from portfolio import db, fetchers, history

logger = logging.getLogger(__name__)


def run(conn=None):
    """Devuelve {"ok": [symbols], "failed": [symbols]} -- un instrumento sin
    cobertura en data912 (ej. recien agregado, o un bono que data912 nunca
    cubrio) cuenta como failed pero no corta el resto del batch."""
    owns_conn = conn is None
    conn = conn or db.get_connection()
    ok, failed = [], []
    try:
        instruments = [
            i for i in db.list_instruments(conn)
            if i["price_source"] == "data912" and i["type"] in history.HISTORICAL_TYPES
        ]
        logger.info("Backfill diario: %d instrumentos a actualizar.", len(instruments))
        for instrument in instruments:
            try:
                n = fetchers.backfill_history(conn, instrument)
                db.update_instrument(conn, instrument["id"], history_backfill_failed_at=None)
                ok.append(instrument["symbol"])
                logger.info("  %s: %d filas.", instrument["symbol"], n)
            except Exception:
                logger.exception("  %s: fallo.", instrument["symbol"])
                db.update_instrument(conn, instrument["id"], history_backfill_failed_at=datetime.utcnow().isoformat())
                failed.append(instrument["symbol"])
        logger.info("Backfill diario terminado: %d ok, %d fallidos.", len(ok), len(failed))
    finally:
        if owns_conn:
            conn.close()
    return {"ok": ok, "failed": failed}


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    run()


if __name__ == "__main__":
    main()
