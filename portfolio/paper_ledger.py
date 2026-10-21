"""Paper Trading diario para estrategias que NO encajan en el modelo de
posiciones de portfolio/paper_trading.py (1 symbol, Take Profit/Stop Loss,
ver paper_positions -- especifico de una apuesta direccional de un solo
activo, como operan Core/Satelite).

Canje (posicion de PAR de bonos, sale por reversion del z-score, no por un
precio) y Carry cripto (delta-neutral, sin entrada/salida discreta, devenga
funding todos los dias mientras esta posicionado) no tienen un "precio de
entrada/salida" que forzar en ese esquema sin fingir un mecanismo que no es
el real.

Insight (ver conversacion, 2026-10-21): portfolio/arbitrage.py::
simulate_canje_arbitrage_engine y portfolio/crypto_carry.py::
simulate_funding_rate_carry_engine YA devuelven una serie daily_returns_pct
real (mismo contrato que portfolio/capital_allocator.py::_STRATEGY_SIMULATORS,
el MISMO que ya paso el gate CPCV+DSR, ver portfolio/backtest.py::
run_strategy_cpcv_validation) -- Paper Trading para estas estrategias es
simplemente llevar un libro diario de ESE retorno ya validado, reusado tal
cual, sin inventar ningun modelo de posicion nuevo."""
import sys
from datetime import datetime

import pandas as pd

from portfolio import capital_allocator, db, strategy_registry

DEFAULT_INITIAL_CAPITAL_USD = 10000.0


def get_last_ledger_entry(conn, strategy_name):
    row = conn.execute(
        "SELECT * FROM paper_strategy_ledger WHERE strategy_name = ? ORDER BY date DESC LIMIT 1",
        (strategy_name,),
    ).fetchone()
    return dict(row) if row else None


def get_equity_curve(conn, strategy_name):
    rows = conn.execute(
        "SELECT * FROM paper_strategy_ledger WHERE strategy_name = ? ORDER BY date",
        (strategy_name,),
    ).fetchall()
    return [dict(r) for r in rows]


def record_daily_return(conn, strategy_name):
    """Corre el simulador REAL de la estrategia (el mismo que ya paso el
    gate CPCV+DSR) y agrega al ledger TODOS los dias nuevos desde la ultima
    fila ya registrada -- nunca solo "hoy": si el cron estuvo caido unos
    dias, se pone al dia solo (mismo criterio que portfolio/fetchers.py::
    ensure_ccl_history). Idempotente: correr esto 2 veces el mismo dia no
    duplica nada (UNIQUE(strategy_name, date) en el schema + chequeo de
    fecha aca). Devuelve la lista de filas nuevas insertadas (puede ser
    vacia si ya estaba al dia)."""
    simulator = capital_allocator._STRATEGY_SIMULATORS.get(strategy_name)
    if simulator is None:
        return []

    result = simulator(conn)
    returns = result["daily_returns_pct"]
    if not len(returns):
        return []

    strategy = strategy_registry.get_strategy(conn, strategy_name)
    started_at = pd.Timestamp(strategy["paper_trading_started_at"]) if strategy and strategy["paper_trading_started_at"] else None

    last_entry = get_last_ledger_entry(conn, strategy_name)
    equity = last_entry["equity_usd"] if last_entry else DEFAULT_INITIAL_CAPITAL_USD
    last_date = pd.Timestamp(last_entry["date"]) if last_entry else None

    # Nunca cuenta como "paper trading" un dia anterior a paper_trading_started_at
    # -- si no, la primera corrida "backfillea" anios de backtest ya conocido
    # como si fuera track record recien generado (ver conversacion 2026-10-21).
    candidates = [d for d in (last_date, started_at) if d is not None]
    floor_date = max(candidates) if candidates else None
    new_dates = returns.index if floor_date is None else returns.index[returns.index > floor_date]
    inserted = []
    for date in new_dates:
        daily_return_pct = float(returns.loc[date])
        equity = equity * (1 + daily_return_pct / 100)
        date_str = date.strftime("%Y-%m-%d")
        conn.execute(
            """INSERT INTO paper_strategy_ledger (strategy_name, date, daily_return_pct, equity_usd)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(strategy_name, date) DO NOTHING""",
            (strategy_name, date_str, daily_return_pct, equity),
        )
        inserted.append({"strategy_name": strategy_name, "date": date_str,
                          "daily_return_pct": daily_return_pct, "equity_usd": equity})
    conn.commit()
    return inserted


def run_all(conn):
    """Corre record_daily_return para toda estrategia en 'paper_trading'/
    'live' que no este pausada -- nunca toca una en 'research'/'validated'
    (todavia no arranco Paper Trading, ver strategy_registry.start_paper_trading)."""
    eligible = [
        s for s in strategy_registry.list_strategies(conn)
        if s["status"] in ("paper_trading", "live") and not s["paper_trading_paused"]
    ]
    results = {}
    for s in eligible:
        results[s["name"]] = record_daily_return(conn, s["name"])
    return results


def main():
    conn = db.get_connection()
    try:
        results = run_all(conn)
        if not results:
            print("paper_ledger: ninguna estrategia en paper_trading/live (sin pausar) todavia")
            return
        for name, inserted in results.items():
            if inserted:
                last = inserted[-1]
                print(f"{name:22s} -- {len(inserted)} dia(s) nuevo(s), equity {last['equity_usd']:.2f} USD (al {last['date']})")
            else:
                print(f"{name:22s} -- ya estaba al dia")
    except Exception as e:
        print(f"paper_ledger: FALLO -- {e}", file=sys.stderr)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
