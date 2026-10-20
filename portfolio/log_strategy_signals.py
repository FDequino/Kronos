"""Entry point diario: `python -m portfolio.log_strategy_signals`.

Corre y persiste el snapshot de HOY de Carry Trade LECAP/CER y Dólar
Sintético (ver portfolio/fixed_income.py::log_carry_trade_snapshot,
portfolio/synthetic_dollar.py::log_synthetic_dollar_snapshot) en
strategy_signal_log (ver schema.sql). Ninguna de las 2 fuentes externas
expone historial -- esta es la única forma honesta de juntar evidencia hacia
adelante para poder, en unas semanas/meses, construir un backtest real de
estas 2 estrategias en vez de confiar en la señal de un solo día.

Pensado para un cron/LaunchAgent diario (NO se instala acá -- queda para
decidir junto con el usuario, igual que el de portfolio/daily_backfill.py).
Cada estrategia corre aislada: si una falla (fuente externa caída), la otra
igual se loguea -- nunca se pierde un día entero por una sola falla."""
import sys

from portfolio import db, fixed_income, synthetic_dollar


def main():
    conn = db.get_connection()
    try:
        try:
            row_id = fixed_income.log_carry_trade_snapshot(conn)
            print(f"carry_trade: logueado (id={row_id})")
        except Exception as e:
            print(f"carry_trade: FALLO -- {e}", file=sys.stderr)

        try:
            row_id = synthetic_dollar.log_synthetic_dollar_snapshot(conn)
            print(f"synthetic_dollar: logueado (id={row_id})")
        except Exception as e:
            print(f"synthetic_dollar: FALLO -- {e}", file=sys.stderr)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
