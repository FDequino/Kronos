"""Enrutador de decisiones de ejecucion REAL (ver conversacion, "full-auto o
semi-auto... configurable desde Ajustes") -- un UNICO lugar que decide, para
cada estrategia 'live', si una accion propuesta por
portfolio/live_strategy_engine.py se ejecuta directo (full_auto), queda
esperando tu aprobacion por Telegram/Web (semi_auto), o ni se intenta
(off, o circuit breaker disparado). Paper trading NO pasa por acá -- sigue
100% en portfolio/paper_ledger.py (cero riesgo real, nunca pide confirmacion,
ver OBJETIVO_MARCO.md).

run_daily_execution_cycle(conn) es el entry point diario (ver
portfolio/run_daily_execution_cycle.py). resolve_decision(conn, id, ...) es
lo que llaman el boton de Telegram y el de la Web -- el que resuelva
PRIMERO gana, el otro canal se encuentra el status ya cambiado y no hace
nada (ver chequeo de status al principio)."""
from portfolio import db, live_strategy_engine, multi_leg_execution, risk_engine, strategy_registry
from portfolio.tz import now_ar


def _patrimonio_usd(conn):
    """Base para el tope de exposicion (ver
    broker_client.compute_auto_trading_limit_usd) -- el mismo pool dedicado
    de Ajustes (auto_trading_capital_usd), nunca la cartera real de 32
    instrumentos (en otro broker, nunca tocada, ver OBJETIVO_MARCO.md)."""
    return db.get_app_settings(conn).get("auto_trading_capital_usd") or 0.0


def _apply_decision(conn, decision, *, resolved_via):
    """Ejecuta de verdad las patas de `decision` y persiste el resultado --
    SIEMPRE deja la decision en un estado terminal (nunca la deja
    'pending_confirmation' después de llamar esto)."""
    patrimonio_usd = _patrimonio_usd(conn)
    result = multi_leg_execution.execute_legs(conn, legs=decision["legs"], patrimonio_usd=patrimonio_usd)
    resolved_at = now_ar().isoformat()

    if result["status"] != "executed":
        db.update_execution_decision(
            conn, decision["id"], status=result["status"], error_detalle=result["error"],
            resolved_via=resolved_via, resolved_at=resolved_at,
        )
        return result

    if decision["action"] == "open":
        position = db.insert_strategy_position(
            conn, strategy_name=decision["strategy_name"], mode="real",
            legs=decision["legs"], capital_usd=decision["legs"][0]["quantity"] * decision["legs"][0]["price_usd"],
        )
        status = "auto_executed" if resolved_via == "auto" else "executed"
        db.update_execution_decision(
            conn, decision["id"], status=status, resolved_via=resolved_via,
            resolved_at=resolved_at, position_id=position["id"],
        )
    elif decision["action"] == "close" and decision.get("position_id"):
        db.close_strategy_position(conn, decision["position_id"])
        status = "auto_executed" if resolved_via == "auto" else "executed"
        db.update_execution_decision(
            conn, decision["id"], status=status, resolved_via=resolved_via, resolved_at=resolved_at,
        )
    return result


def resolve_decision(conn, decision_id, *, approved, via):
    """via: 'telegram'|'web'. Devuelve {"already_resolved": bool,
    "decision": {...}, "execution_result": {...}|None} -- already_resolved
    True significa "el otro canal ya actuo, esto no hizo nada nuevo" (nunca
    se ejecuta 2 veces la misma decision)."""
    decision = db.get_execution_decision(conn, decision_id)
    if decision is None:
        raise ValueError(f"execution_decision {decision_id} no existe")
    if decision["status"] != "pending_confirmation":
        return {"already_resolved": True, "decision": decision, "execution_result": None}

    if not approved:
        db.update_execution_decision(
            conn, decision_id, status="rejected", resolved_via=via, resolved_at=now_ar().isoformat(),
        )
        return {"already_resolved": False, "decision": db.get_execution_decision(conn, decision_id), "execution_result": None}

    result = _apply_decision(conn, decision, resolved_via=via)
    return {"already_resolved": False, "decision": db.get_execution_decision(conn, decision_id), "execution_result": result}


def run_daily_execution_cycle(conn):
    """Un ciclo diario (ver portfolio/run_daily_execution_cycle.py) -- recorre
    toda estrategia 'live' (paper trading queda afuera, ver docstring del
    modulo) y decide que hacer. Devuelve {strategy_name: {"action": str|None,
    "reason": str, ...}} -- SIEMPRE una entrada por estrategia 'live', nunca
    se salta una en silencio."""
    summary = {}
    breaker = risk_engine.check_circuit_breaker(conn)

    for strategy in strategy_registry.list_strategies(conn):
        name = strategy["name"]
        if strategy["status"] != "live":
            continue

        if strategy["automation_mode"] == "off":
            summary[name] = {"action": None, "reason": "automation_mode='off' -- estrategia 'live' sin operar"}
            continue

        if not live_strategy_engine.has_real_intent_builder(name):
            summary[name] = {
                "action": None,
                "reason": "sin ejecucion real implementada para esta estrategia todavia (ver live_strategy_engine.py)",
            }
            continue

        if breaker["tripped"]:
            summary[name] = {
                "action": None,
                "reason": f"circuit breaker disparado (drawdown {breaker['drawdown_pct']}% <= límite {breaker['limit_pct']}%) -- ninguna ejecución real nueva hasta revisión manual",
            }
            continue

        intent = live_strategy_engine.build_real_intent(conn, name)
        if intent is None:
            summary[name] = {"action": None, "reason": "ya bien posicionado / sin oportunidad hoy"}
            continue

        decision = db.insert_execution_decision(
            conn, strategy_name=name, mode="real", action=intent["action"],
            legs=intent["legs"], reason=intent["reason"], status="pending_confirmation",
        )
        if intent.get("position_id"):
            decision = db.update_execution_decision(conn, decision["id"], position_id=intent["position_id"])

        if strategy["automation_mode"] == "full_auto":
            _apply_decision(conn, decision, resolved_via="auto")
            final_decision = db.get_execution_decision(conn, decision["id"])
            summary[name] = {
                "action": intent["action"], "decision_id": decision["id"],
                "status": final_decision["status"], "reason": intent["reason"],
            }
        else:  # semi_auto
            summary[name] = {
                "action": intent["action"], "decision_id": decision["id"],
                "status": "pending_confirmation", "reason": intent["reason"],
            }

    return summary
