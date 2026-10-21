"""Registro de Estrategias (ver OBJETIVO_MARCO.md, plan "grado-fondo" del
2026-10-20) -- fuente de verdad unica de que estrategia puede tocar plata
real hoy, y con que modo de automatizacion. Ninguna otra pieza del proyecto
decide esto por su cuenta: portfolio/risk_engine.py consulta este registro
antes de dejar pasar cualquier señal a una propuesta accionable.

'status' solo lo mueve record_validation_result (automatico, nunca a mano
desde la UI): 'research' hasta pasar DSR>=0.95 en el gate CPCV de
OBJETIVO_MARCO.md, 'validated' una vez que lo pasa. 'live' (plata real) es
una decision EXPLICITA del usuario via set_automation_mode/promote_to_live,
nunca automatica -- pasar el DSR es necesario pero no suficiente (falta
paper trading + drawdown real + batir benchmark, ver OBJETIVO_MARCO.md)."""
from datetime import datetime

from portfolio import db

DSR_GATE_THRESHOLD = 0.95


def record_validation_result(conn, name, dsr, annualized_return_pct=None, sharpe_ratio=None):
    """Persiste el resultado de una corrida real de run_strategy_cpcv_validation
    (ver portfolio/backtest.py) -- 'validated' si dsr >= DSR_GATE_THRESHOLD,
    'research' si no. NUNCA mueve una estrategia a 'live' ni cambia
    automation_mode aca -- eso requiere una decision explicita aparte (ver
    promote_to_live/set_automation_mode), pasar el DSR es solo el primer
    paso del gate completo de OBJETIVO_MARCO.md.

    baseline_sharpe/baseline_annualized_return_pct (si se pasan) quedan como
    la linea de base que portfolio/strategy_health.py (Fase 4) compara contra
    el rendimiento RECIENTE real una vez 'live' -- NUNCA se pisan en corridas
    posteriores una vez que la estrategia ya es 'live' (si se recalculara la
    base con cada corrida, un desvio real quedaria invisible: la linea de
    base se iria corriendo junto con el deterioro)."""
    existing = get_strategy(conn, name)
    # Una estrategia ya 'live' no se degrada sola a 'research' si una corrida
    # puntual da un DSR mas bajo (podria ser ruido de una ventana distinta) --
    # eso es trabajo del Monitor de Salud de Estrategia (Fase 4), no de este
    # registro de validacion inicial. Si todavia no es 'live', sigue el
    # estado derivado directo del DSR de esta corrida.
    already_live = existing and existing["status"] == "live"
    status = "live" if already_live else ("validated" if dsr is not None and dsr >= DSR_GATE_THRESHOLD else "research")
    baseline_sharpe = existing["baseline_sharpe"] if already_live else sharpe_ratio
    baseline_return = existing["baseline_annualized_return_pct"] if already_live else annualized_return_pct

    conn.execute(
        """INSERT INTO strategies (name, status, last_dsr, last_validated_at, baseline_sharpe, baseline_annualized_return_pct, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
           ON CONFLICT(name) DO UPDATE SET
               status = excluded.status, last_dsr = excluded.last_dsr,
               last_validated_at = excluded.last_validated_at,
               baseline_sharpe = excluded.baseline_sharpe,
               baseline_annualized_return_pct = excluded.baseline_annualized_return_pct,
               updated_at = excluded.updated_at""",
        (name, status, dsr, datetime.now().isoformat(), baseline_sharpe, baseline_return),
    )
    conn.commit()
    return get_strategy(conn, name)


def start_paper_trading(conn, name):
    """Decision EXPLICITA del usuario de arrancar a correr una estrategia
    'validated' sobre equity VIRTUAL (ver portfolio/paper_trading.py) --
    nunca arriesga plata real, es el paso obligatorio para juntar el resto
    del gate (periodo sin salvada manual + drawdown real + batir benchmark,
    ver OBJETIVO_MARCO.md) antes de 'live'."""
    strategy = get_strategy(conn, name)
    if strategy is None:
        raise ValueError(f"estrategia desconocida: {name!r}")
    if strategy["status"] != "validated":
        raise ValueError(f"{name!r} esta en estado {strategy['status']!r} -- solo arranca paper trading desde 'validated'")
    conn.execute(
        "UPDATE strategies SET status = 'paper_trading', paper_trading_paused = 0, updated_at = datetime('now') WHERE name = ?",
        (name,),
    )
    conn.commit()
    return get_strategy(conn, name)


def pause_paper_trading(conn, name):
    """Pausar NO es una regresion del gate -- la estrategia sigue en
    'paper_trading', solo deja de abrir posiciones virtuales nuevas (ver
    portfolio/monitor.py, que debe chequear este flag antes de escanear)."""
    return _set_paper_trading_paused(conn, name, True)


def resume_paper_trading(conn, name):
    return _set_paper_trading_paused(conn, name, False)


def _set_paper_trading_paused(conn, name, paused):
    strategy = get_strategy(conn, name)
    if strategy is None:
        raise ValueError(f"estrategia desconocida: {name!r}")
    if strategy["status"] != "paper_trading":
        raise ValueError(f"{name!r} esta en estado {strategy['status']!r} -- pausar/continuar solo aplica en 'paper_trading'")
    conn.execute(
        "UPDATE strategies SET paper_trading_paused = ?, updated_at = datetime('now') WHERE name = ?",
        (1 if paused else 0, name),
    )
    conn.commit()
    return get_strategy(conn, name)


def promote_to_live(conn, name):
    """Decision EXPLICITA del usuario (nunca automatica) de que una
    estrategia ya corrio el tiempo necesario en 'paper_trading' y puede tocar
    plata real -- requiere HABER PASADO por paper trading, nunca se salta
    ese paso desde 'validated' directo (ver OBJETIVO_MARCO.md, correccion del
    2026-10-21: Validada -> Paper Trading -> Live, no Validada -> Live).
    Requiere ademas que tenga baseline_sharpe (si record_validation_result
    nunca recibio sharpe_ratio, no hay con que comparar despues en el Monitor
    de Salud -- se rechaza en vez de promover a ciegas)."""
    strategy = get_strategy(conn, name)
    if strategy is None:
        raise ValueError(f"estrategia desconocida: {name!r}")
    if strategy["status"] != "paper_trading":
        raise ValueError(f"{name!r} esta en estado {strategy['status']!r} -- solo se promueve desde 'paper_trading'")
    if strategy["baseline_sharpe"] is None:
        raise ValueError(f"{name!r} no tiene baseline_sharpe registrado -- re-validar pasando sharpe_ratio antes de promover")
    conn.execute("UPDATE strategies SET status = 'live', updated_at = datetime('now') WHERE name = ?", (name,))
    conn.commit()
    return get_strategy(conn, name)


def get_strategy(conn, name):
    row = conn.execute("SELECT * FROM strategies WHERE name = ?", (name,)).fetchone()
    return dict(row) if row else None


def list_strategies(conn):
    rows = conn.execute("SELECT * FROM strategies ORDER BY name").fetchall()
    return [dict(r) for r in rows]


def set_automation_mode(conn, name, mode):
    """mode: 'full_auto' | 'semi_auto' | 'off'. Solo tiene sentido activar
    full_auto/semi_auto sobre una estrategia 'validated' o 'live' -- lanza
    ValueError si se intenta sobre una en 'research' (nunca se automatiza
    algo que todavia no paso el gate estadistico, ver OBJETIVO_MARCO.md)."""
    if mode not in ("full_auto", "semi_auto", "off"):
        raise ValueError(f"mode debe ser full_auto/semi_auto/off, se recibio {mode!r}")
    strategy = get_strategy(conn, name)
    if strategy is None:
        raise ValueError(f"estrategia desconocida: {name!r}")
    if mode != "off" and strategy["status"] == "research":
        raise ValueError(
            f"{name!r} todavia esta en 'research' (no paso DSR>=0.95) -- no se puede "
            f"activar {mode!r} hasta que pase el gate CPCV de OBJETIVO_MARCO.md"
        )
    conn.execute(
        "UPDATE strategies SET automation_mode = ?, updated_at = datetime('now') WHERE name = ?",
        (mode, name),
    )
    conn.commit()
    return get_strategy(conn, name)


def set_capital_pct(conn, name, pct):
    """Usado por el Asignador de Capital Dinamico (Fase 3, ver OBJETIVO_MARCO.md)
    -- redistribuye capital entre estrategias YA validated/live, nunca activa
    una nueva por su cuenta."""
    if pct < 0:
        raise ValueError("capital_pct no puede ser negativo")
    conn.execute(
        "UPDATE strategies SET capital_pct = ?, updated_at = datetime('now') WHERE name = ?",
        (pct, name),
    )
    conn.commit()


def trip_circuit_breaker(conn):
    """El 'freno de mano' de portfolio/risk_engine.py: pasa TODAS las
    estrategias en full_auto a off (nunca a semi_auto -- un circuit breaker
    de cartera significa 'nada se dispara solo hasta revision manual', no
    'pedí aprobacion por operacion'). Idempotente: no rompe nada si ya
    estaban todas en off."""
    conn.execute(
        "UPDATE strategies SET automation_mode = 'off', updated_at = datetime('now') WHERE automation_mode = 'full_auto'"
    )
    conn.commit()
