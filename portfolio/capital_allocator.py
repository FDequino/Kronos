"""Asignador de Capital Dinamico (Fase 3, ver OBJETIVO_MARCO.md, plan
"grado-fondo" del 2026-10-20) -- conecta portfolio/risk.py::
compute_dynamic_strategy_allocation (ya escrito, confirmado real en la
auditoria de hoy como "nunca conectado a nada") a las estrategias que HOY
estan 'validated'/'live' en el Registro de Estrategias
(portfolio/strategy_registry.py).

Nunca decide activar una estrategia nueva ni la saca de 'research' -- eso lo
decide exclusivamente el gate CPCV+DSR (portfolio/backtest.py::
run_strategy_validation_report). Esto SOLO redistribuye capital entre las
estrategias ya aprobadas, segun su Sharpe/correlacion reciente real (risk
parity por default: ninguna estrategia individual domina el riesgo total,
el criterio mas conservador de los 3 disponibles en risk.py).

Pensado para correr semanalmente via cron (mismo patron que
portfolio/log_strategy_signals.py), nunca sincronico en un request HTTP --
implica re-simular cada estrategia elegible, puede tardar minutos."""
import sys

from portfolio import arbitrage, backtest, crypto_carry, db, factor_investing, risk, strategy_registry

# Estrategia en el Registro -> como conseguir su serie real de retornos
# diarios -- MISMOS parametros que portfolio/backtest.py::
# run_strategy_validation_report (el gate que las puso en 'validated'), para
# no evaluar la asignacion con una ventana distinta a la que valido el DSR.
_STRATEGY_SIMULATORS = {
    "canje_al29_gd29": lambda conn: arbitrage.simulate_canje_arbitrage_engine(
        conn, lookback_days=2000, commission_pct=arbitrage.CANJE_ROUND_TRIP_COMMISSION_PCT),
    "crypto_carry_btcusdt": lambda conn: crypto_carry.simulate_funding_rate_carry_engine("BTCUSDT"),
    "crypto_carry_ethusdt": lambda conn: crypto_carry.simulate_funding_rate_carry_engine("ETHUSDT"),
    "factor_investing": lambda conn: factor_investing.simulate_factor_engine(
        conn, lookback_days=600, commission_pct=backtest.ROUND_TRIP_COMMISSION_PCT),
}


def run_dynamic_allocation(conn, method="risk_parity"):
    """Corre compute_dynamic_strategy_allocation sobre las estrategias HOY
    'validated'/'live' y persiste result['weights'] en strategy_registry
    (capital_pct, 0-100). Nunca toca una estrategia en 'research' -- ni para
    incluirla en el calculo ni para asignarle capital. {'available': False,
    'reason': ...} si no hay evidencia suficiente (nunca pesos inventados)."""
    eligible = [s for s in strategy_registry.list_strategies(conn) if s["status"] in ("validated", "live")]
    if len(eligible) < 2:
        return {"available": False, "reason": f"necesita >= 2 estrategias validated/live, hay {len(eligible)}"}

    returns_by_strategy = {}
    for s in eligible:
        simulator = _STRATEGY_SIMULATORS.get(s["name"])
        if simulator is None:
            continue  # estrategia validada pero sin simulador conocido aca -- se omite, nunca rompe el resto
        result = simulator(conn)
        if len(result["daily_returns_pct"]):
            returns_by_strategy[s["name"]] = result["daily_returns_pct"]

    if len(returns_by_strategy) < 2:
        return {"available": False, "reason": f"solo {len(returns_by_strategy)} estrategia(s) elegible(s) con datos simulables"}

    result = risk.compute_dynamic_strategy_allocation(returns_by_strategy, method=method)
    if result["available"]:
        for name, weight in result["weights"].items():
            strategy_registry.set_capital_pct(conn, name, round(weight * 100, 2))
    return result


def main():
    conn = db.get_connection()
    try:
        result = run_dynamic_allocation(conn)
        if not result["available"]:
            print(f"Asignacion dinamica: NO disponible -- {result['reason']}")
            return
        print(f"Asignacion dinamica ({result['method']}, {result['n_obs']} ruedas en comun):")
        for name, weight in sorted(result["weights"].items(), key=lambda kv: -kv[1]):
            print(f"  {name:22s} {weight * 100:5.1f}%")
        print(f"Sharpe anualizado esperado de esta mezcla: {result['expected_sharpe_annualized']}")
    except Exception as e:
        print(f"Asignacion dinamica: FALLO -- {e}", file=sys.stderr)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
