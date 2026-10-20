"""Entry point diario: `python -m portfolio.log_strategy_signals`.

Corre y persiste el snapshot de HOY de Carry Trade LECAP/CER, Dólar
Sintético y Canje AL29/GD29 (ver portfolio/fixed_income.py::
log_carry_trade_snapshot, portfolio/synthetic_dollar.py::
log_synthetic_dollar_snapshot, portfolio/arbitrage.py::log_canje_snapshot)
en strategy_signal_log (ver schema.sql). Carry Trade y Dólar Sintético no
tienen historial descargable de ninguna fuente externa -- esta es la única
forma honesta de juntar evidencia hacia adelante para poder, en unas
semanas/meses, construir un backtest real de esas 2 estrategias en vez de
confiar en la señal de un solo día. Canje sí tiene historial completo (ver
arbitrage.py), pero se loguea igual para confirmar que el edge medido
retrospectivamente (AL29/GD29) sigue vivo hacia adelante.

Pensado para un cron/LaunchAgent diario (NO se instala acá -- queda para
decidir junto con el usuario, igual que el de portfolio/daily_backfill.py).
Cada estrategia corre aislada: si una falla (fuente externa caída), las
otras igual se loguean -- nunca se pierde un día entero por una sola falla."""
import sys

from portfolio import arbitrage, db, fixed_income, synthetic_dollar


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

        try:
            row_id = arbitrage.log_canje_snapshot(conn)
            print(f"canje: logueado (id={row_id})")
        except Exception as e:
            print(f"canje: FALLO -- {e}", file=sys.stderr)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
