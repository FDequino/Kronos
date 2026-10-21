"""Monitor de Salud de Estrategia (Fase 4, ver OBJETIVO_MARCO.md, plan
"grado-fondo" del 2026-10-20) -- compara el rendimiento RECIENTE real de cada
estrategia 'live' contra la linea de base (baseline_sharpe) que la paso el
gate CPCV+DSR (ver portfolio/strategy_registry.py). Si se desvia de forma
sostenida, la baja a 'semi_auto' (NUNCA la apaga del todo sola -- un corte
total es decision humana, ver 'off' en strategy_registry.set_automation_mode)
y lo notifica por Telegram.

Esto es exactamente lo que hubiera detectado solo el problema real del
Satelite (Kelly con tasa de acierto vieja, ver auditoria 2026-10-20) en vez
de encontrarse a mano -- la motivacion directa de esta pieza.

Pensado para correr diariamente via cron (mismo patron que
portfolio/log_strategy_signals.py)."""
import sys

from portfolio import backtest, capital_allocator, db, strategy_registry, telegram_bot

# Umbral de degradacion: el Sharpe RECIENTE (ultimos RECENT_WINDOW_DAYS) cae
# por debajo de esta fraccion del Sharpe de la linea de base -- declarado,
# no un numero magico de ningun paper puntual. 0.0 (umbral mas laxo posible,
# "todavia positivo") seria demasiado tolerante: una estrategia puede seguir
# en positivo y haber perdido la mayor parte de su edge real. 0.3 exige que
# siga mostrando al menos un tercio del Sharpe que la valido, suficiente
# margen para no reaccionar a ruido de una ventana corta.
DEGRADATION_THRESHOLD_FRACTION = 0.3
RECENT_WINDOW_DAYS = 90
MIN_RECENT_OBSERVATIONS = 30  # no se juzga con menos de un mes de datos recientes


def _recent_sharpe(result, window_days=RECENT_WINDOW_DAYS):
    """Sharpe (anualizado) de los ultimos window_days de daily_returns_pct --
    None si no hay suficiente historial reciente (nunca se inventa un
    veredicto con poca evidencia)."""
    returns = result["daily_returns_pct"]
    if len(returns) < MIN_RECENT_OBSERVATIONS:
        return None
    recent_returns = returns.tail(window_days)
    if len(recent_returns) < MIN_RECENT_OBSERVATIONS:
        return None
    recent_idle = result["idle_weight"].reindex(recent_returns.index).fillna(0.0)
    metrics = backtest._compute_engine_metrics(recent_returns, recent_idle, [])
    return metrics["sharpe_ratio"]


def check_strategy_health(conn, name):
    """Chequea UNA estrategia 'live'. Devuelve dict con el veredicto; no hace
    nada (ni degrada ni notifica) si la estrategia no esta 'live', si no hay
    baseline_sharpe registrado, o si no hay suficiente historial reciente --
    ausencia de evidencia nunca se trata como degradacion."""
    strategy = strategy_registry.get_strategy(conn, name)
    if strategy is None or strategy["status"] != "live":
        return {"name": name, "checked": False, "reason": "no esta 'live'"}
    if strategy["baseline_sharpe"] is None:
        return {"name": name, "checked": False, "reason": "sin baseline_sharpe registrado"}

    simulator = capital_allocator._STRATEGY_SIMULATORS.get(name)
    if simulator is None:
        return {"name": name, "checked": False, "reason": "sin simulador conocido"}

    result = simulator(conn)
    recent_sharpe = _recent_sharpe(result)
    if recent_sharpe is None:
        return {"name": name, "checked": False, "reason": "historial reciente insuficiente"}

    baseline = strategy["baseline_sharpe"]
    # Si el baseline fue negativo o cero (no deberia pasar para algo 'live',
    # pero declarado): cualquier baseline <= 0 se trata como "sin referencia
    # util para fraccion", solo se chequea que lo reciente tampoco sea peor.
    threshold = baseline * DEGRADATION_THRESHOLD_FRACTION if baseline > 0 else 0.0
    degraded = bool(recent_sharpe < threshold)  # nunca un np.bool_ -- rompe comparaciones "is True/False" en callers

    degraded_from_mode = None
    if degraded and strategy["automation_mode"] != "off":
        degraded_from_mode = strategy["automation_mode"]
        strategy_registry.set_automation_mode(conn, name, "semi_auto")
        _notify_degradation(conn, name, baseline, recent_sharpe, degraded_from_mode)

    return {
        "name": name, "checked": True, "degraded": degraded,
        "baseline_sharpe": baseline, "recent_sharpe": recent_sharpe,
        "threshold": threshold, "degraded_from_mode": degraded_from_mode,
    }


def _notify_degradation(conn, name, baseline, recent, previous_mode):
    text = (
        f"⚠️ *{name}* bajó a *semi-auto* automáticamente.\n"
        f"Sharpe de referencia (el que la validó): {baseline:.2f}\n"
        f"Sharpe real de los últimos {RECENT_WINDOW_DAYS} días: {recent:.2f}\n"
        f"Modo anterior: {previous_mode} — ahora requiere tu aprobación por operación hasta que se revise."
    )
    for user in db.list_telegram_allowed_users(conn):
        ok, reason = telegram_bot.send_test_message(user["chat_id"], text=text)
        if not ok:
            print(f"strategy_health: no se pudo notificar a {user['chat_id']}: {reason}", file=sys.stderr)


def run_health_check(conn):
    live_strategies = [s for s in strategy_registry.list_strategies(conn) if s["status"] == "live"]
    return [check_strategy_health(conn, s["name"]) for s in live_strategies]


def main():
    conn = db.get_connection()
    try:
        results = run_health_check(conn)
        if not results:
            print("strategy_health: ninguna estrategia 'live' todavia -- nada que chequear")
            return
        for r in results:
            if not r["checked"]:
                print(f"{r['name']:22s} -- sin chequear ({r['reason']})")
                continue
            estado = "DEGRADADA -> semi_auto" if r["degraded"] else "OK"
            print(f"{r['name']:22s} -- baseline {r['baseline_sharpe']:.2f} vs. reciente {r['recent_sharpe']:.2f} -- {estado}")
    except Exception as e:
        print(f"strategy_health: FALLO -- {e}", file=sys.stderr)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
