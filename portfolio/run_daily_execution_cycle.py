"""Entry point diario UNICO para el ciclo de vida de estrategias: `python -m
portfolio.run_daily_execution_cycle` (ver conversacion, "plataforma
funcional, paper y real, configurable"). Corre, en este orden:

1. portfolio/paper_ledger.py::run_all -- pone al dia el ledger de Paper
   Trading de toda estrategia en 'paper_trading'/'live' (cero riesgo real,
   nunca pide confirmacion).
2. portfolio/execution_router.py::run_daily_execution_cycle -- para toda
   estrategia 'live', ejecuta directo (full_auto) o deja pendiente de
   confirmacion (semi_auto) segun automation_mode (ver
   portfolio/strategy_registry.py).

Cada paso aislado en su propio try/except (mismo patron que
log_strategy_signals.py/daily_backfill.py) -- una falla en el paso 2 nunca
pierde el trabajo ya hecho en el paso 1.

NOTA: si el bot de Telegram esta corriendo (portfolio/telegram_bot.py), este
ciclo YA se ejecuta solo todos los dias (ver _paper_ledger_job a las 20:00 ART
+ _execution_router_job a las 20:05 ART, job_queue de la Application) -- este
script es el mismo ciclo a mano, pensado para cuando el bot esta apagado o
para correrlo manualmente mientras probás algo. Si igual queres un cron
aparte en el VPS (ej. redundancia si el bot se cae), es cosa tuya agregarlo,
no se instala acá:

    0 18 * * 1-5 cd /opt/kronos/app && .venv/bin/python -m portfolio.run_daily_execution_cycle >> .devlogs/daily_execution_cycle.log 2>&1
"""
import sys

from portfolio import db, execution_router, paper_ledger


def main():
    conn = db.get_connection()
    try:
        try:
            paper_results = paper_ledger.run_all(conn)
            if not paper_results:
                print("paper_ledger: ninguna estrategia en paper_trading/live (sin pausar) todavia")
            for name, inserted in paper_results.items():
                if inserted:
                    last = inserted[-1]
                    print(f"paper_ledger: {name:22s} -- {len(inserted)} dia(s) nuevo(s), equity {last['equity_usd']:.2f} USD")
                else:
                    print(f"paper_ledger: {name:22s} -- ya estaba al dia")
        except Exception as e:
            print(f"paper_ledger: FALLO -- {e}", file=sys.stderr)

        try:
            exec_summary = execution_router.run_daily_execution_cycle(conn)
            if not exec_summary:
                print("execution_router: ninguna estrategia 'live' todavia")
            for name, info in exec_summary.items():
                if info["action"] is None:
                    print(f"execution_router: {name:22s} -- sin acción ({info['reason']})")
                else:
                    print(f"execution_router: {name:22s} -- {info['action']} -> {info['status']} (decision_id={info.get('decision_id')})")
        except Exception as e:
            print(f"execution_router: FALLO -- {e}", file=sys.stderr)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
