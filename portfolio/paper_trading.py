"""Paper Trading / dinero ficticio (ver conversacion): simula la ejecucion
de las señales de ambos motores (portfolio/live_signals.py) sobre un
patrimonio VIRTUAL propio -- nunca toca holdings/transactions reales. Permite
validar el edge de Core/Satelite con el tiempo sin arriesgar plata de
verdad. Misma logica de dimensionamiento (position_weight, asignacion
core/satelite de portfolio/config.py) que el motor real, aplicada sobre el
equity VIRTUAL en vez del patrimonio real de la cuenta.

Este modulo es logica pura (sin red, sin Telegram, sin Chronos-Bolt) --
quien detecta oportunidades y trae precios de mercado es
portfolio/monitor.py, que llama a las funciones de aca con los datos ya
resueltos. Asi se puede testear la simulacion de compras/salidas/reporte sin
depender de servicios externos (ver tests/test_paper_trading.py).

Dos tablas (ver portfolio/schema.sql):
- paper_positions: posiciones virtuales UNICAMENTE abiertas (se borra la
  fila al cerrarse, nunca queda un status "closed" dando vueltas).
- paper_trades: historial INMUTABLE de cada operacion ya cerrada (entrada +
  salida + pnl), fuente de verdad para el reporte de rendimiento.
- paper_trading_snapshots: equity de cierre de rueda, un valor por dia --
  unica forma honesta de calcular "resultado del dia" sin inventar un
  baseline (ver compute_daily_report)."""
from portfolio import config as capital_config
from portfolio import db
from portfolio.tz import today_ar

# Capital virtual de arranque (USD) -- arbitrario y declarado como tal (no
# hay "capital real" al que anclarlo, es dinero ficticio). Ajustable via
# PUT /api/portfolio/paper-trading/settings o set_initial_capital_usd.
DEFAULT_INITIAL_CAPITAL_USD = 10000.0


# --- Flag de habilitacion y capital inicial (app_settings) ------------------

def is_enabled(conn):
    settings = db.get_app_settings(conn)
    return bool(settings.get("paper_trading_enabled", 1))


def set_enabled(conn, enabled):
    db.update_app_settings(conn, paper_trading_enabled=1 if enabled else 0)
    return is_enabled(conn)


def get_initial_capital_usd(conn):
    settings = db.get_app_settings(conn)
    value = settings.get("paper_trading_initial_capital_usd")
    return float(value) if value is not None else DEFAULT_INITIAL_CAPITAL_USD


def set_initial_capital_usd(conn, value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"initial_capital_usd debe ser un numero, se recibio {value!r}")
    if value <= 0:
        raise ValueError(f"initial_capital_usd debe ser > 0, se recibio {value}")
    db.update_app_settings(conn, paper_trading_initial_capital_usd=value)
    return get_initial_capital_usd(conn)


# --- Posiciones abiertas / historial de trades ------------------------------

def list_open_positions(conn):
    return db.list_paper_positions(conn)


def list_closed_trades(conn, limit=None):
    return db.list_paper_trades(conn, limit=limit)


def has_open_position(conn, symbol, engine):
    return db.get_open_paper_position(conn, symbol, engine) is not None


# --- Equity virtual (cash + valor de mercado de lo abierto) -----------------

def get_cash_usd(conn):
    """Capital inicial + ganancia/perdida YA realizada (trades cerrados) -
    costo de lo que sigue abierto (a precio de ENTRADA, no de mercado -- ese
    costo "sale" de la caja apenas se abre la posicion)."""
    return (
        get_initial_capital_usd(conn)
        + db.sum_paper_trades_pnl_usd(conn)
        - db.sum_open_paper_positions_cost_usd(conn)
    )


def get_equity_usd(conn, current_prices=None):
    """Patrimonio VIRTUAL total = cash + valor de mercado de lo abierto. Si
    no hay precio actual para algun simbolo abierto (current_prices no lo
    trae, ej. el ticker no respondio data912 en este ciclo), se usa el
    precio de ENTRADA de esa posicion como aproximacion declarada -- mejor
    que excluirla del total, nunca se inventa un precio distinto al ultimo
    conocido."""
    current_prices = current_prices or {}
    market_value = 0.0
    for pos in list_open_positions(conn):
        price = current_prices.get(pos["symbol"])
        price_usd = price[0] if price is not None else pos["entry_price_usd"]
        market_value += price_usd * pos["quantity"]
    return get_cash_usd(conn) + market_value


# --- Apertura de posiciones --------------------------------------------------

def size_position(conn, engine, position_weight, entry_price_usd, equity_usd=None):
    """Cantidad de titulos (fraccionaria, es dinero ficticio) para una señal
    nueva: equity_virtual * %asignado al motor (Core/Satelite, ver
    portfolio/config.py) * position_weight de la señal / precio de entrada
    -- MISMO criterio de dimensionamiento que el motor real
    (portfolio/live_signals.py), aplicado sobre el equity VIRTUAL."""
    if equity_usd is None:
        equity_usd = get_equity_usd(conn)
    allocation = capital_config.get_capital_allocation(conn)
    engine_pct = allocation["core_pct"] if engine == "core" else allocation["satellite_pct"]
    amount_usd = equity_usd * (engine_pct / 100.0) * position_weight
    if amount_usd <= 0 or entry_price_usd <= 0:
        return 0.0
    return amount_usd / entry_price_usd


# Tipos que en BYMA se compran en unidades enteras, nunca fraccionadas (ver
# auditoria 19/10/2026, hallazgo 2.4: "simula CEDEARs fraccionados, ej. 20,32
# QQQ" -- el bróker real jamás ejecutaría esa cantidad, así que el resultado
# simulado no era representativo de lo que pasaría con plata real). Bonos/ON
# quedan fuera a propósito: se operan por nominales y SI admiten fracciones
# en la práctica, nada en la auditoria señala un problema ahi.
WHOLE_UNIT_ASSET_TYPES = ("accion", "cedear")


def open_from_signal(conn, signal, engine, asset_type):
    """Abre una posicion virtual a partir de una señal de
    portfolio/live_signals.py (compute_core_signals/compute_satellite_signals,
    llamadas SIN account_value -- el tamaño se calcula aca sobre el equity
    VIRTUAL, nunca sobre el patrimonio real). No abre una segunda posicion
    si ya hay una abierta en el mismo symbol+engine (ver has_open_position).
    Devuelve None si no se abrio nada (duplicado o tamaño 0 -- incluido el
    caso de accion/cedear donde el monto asignado no alcanza ni para 1
    unidad entera), o la fila nueva de paper_positions."""
    symbol = signal["symbol"]
    if has_open_position(conn, symbol, engine):
        return None
    entry_price_usd = signal["entry_price_usd"]
    quantity = size_position(conn, engine, signal["position_weight"], entry_price_usd)
    if asset_type in WHOLE_UNIT_ASSET_TYPES:
        quantity = float(int(quantity))
    if quantity <= 0:
        return None
    tactic = ",".join(signal["tactics"]) if signal.get("tactics") else None
    return db.insert_paper_position(
        conn,
        symbol=symbol, asset_type=asset_type, engine=engine, tactic=tactic, quantity=quantity,
        entry_price_usd=entry_price_usd, entry_price_ars=signal["entry_price_ars"],
        entry_date=today_ar().isoformat(),
        take_profit_price_usd=signal["take_profit_price_usd"], take_profit_price_ars=signal["take_profit_price_ars"],
        stop_loss_price_usd=signal["initial_stop_price_usd"], stop_loss_price_ars=signal["initial_stop_price_ars"],
        max_hold_days=signal["max_hold_days"],
    )


# --- Cierre de posiciones -----------------------------------------------------

def close_position(conn, position, *, exit_price_usd, exit_price_ars, exit_reason, exit_date=None):
    """Cierra UNA posicion: inserta el trade cerrado (historial inmutable) y
    borra la fila de paper_positions. quantity es la misma cantidad de
    titulos en ambas monedas (no de pesos), por eso pnl_ars se calcula igual
    que pnl_usd pero con los precios ARS -- consistente con como BYMA
    liquida de verdad (mismo numero de titulos, precio en pesos)."""
    exit_date = exit_date or today_ar().isoformat()
    quantity = position["quantity"]
    pnl_usd = (exit_price_usd - position["entry_price_usd"]) * quantity
    pnl_pct = (exit_price_usd / position["entry_price_usd"] - 1) * 100
    pnl_ars = (exit_price_ars - position["entry_price_ars"]) * quantity
    trade = db.insert_paper_trade(
        conn,
        position_id=position["id"], symbol=position["symbol"], asset_type=position["asset_type"],
        engine=position["engine"], tactic=position["tactic"], quantity=quantity,
        entry_price_usd=position["entry_price_usd"], entry_price_ars=position["entry_price_ars"],
        entry_date=position["entry_date"],
        exit_price_usd=exit_price_usd, exit_price_ars=exit_price_ars, exit_date=exit_date,
        exit_reason=exit_reason, pnl_usd=pnl_usd, pnl_pct=pnl_pct, pnl_ars=pnl_ars,
    )
    db.delete_paper_position(conn, position["id"])
    return trade


def _days_held(entry_date, exit_date):
    from datetime import date as _date
    return (_date.fromisoformat(exit_date) - _date.fromisoformat(entry_date)).days


def check_exits(conn, current_prices):
    """Revisa TODAS las posiciones abiertas contra el precio actual (dict
    symbol -> (price_usd, price_ars), ver portfolio/monitor.py) y cierra las
    que tocaron Take Profit, Stop Loss, o superaron max_hold_days ruedas
    abiertas -- en ese orden de prioridad (si una vela gappea y toca los dos
    niveles a la vez, se prioriza el Take Profit, igual criterio optimista
    que portfolio/backtest.py::simulate_position_exit salvo que aca no hay
    ambiguedad intradia real, es un precio puntual por ciclo de scan).
    Devuelve la lista de trades recien cerrados (para que el llamador
    decida como notificar)."""
    closed = []
    for position in list_open_positions(conn):
        price = current_prices.get(position["symbol"])
        if price is None:
            continue
        price_usd, price_ars = price
        if price_usd >= position["take_profit_price_usd"]:
            reason = "take_profit"
        elif price_usd <= position["stop_loss_price_usd"]:
            reason = "stop_loss"
        elif _days_held(position["entry_date"], today_ar().isoformat()) >= position["max_hold_days"]:
            reason = "max_hold"
        else:
            continue
        closed.append(close_position(
            conn, position, exit_price_usd=price_usd, exit_price_ars=price_ars, exit_reason=reason,
        ))
    return closed


# --- Reporte de rendimiento / snapshot diario -------------------------------

def save_daily_snapshot(conn, equity_usd, date=None):
    db.upsert_paper_snapshot(conn, date=date or today_ar().isoformat(), equity_usd=equity_usd)


def compute_daily_report(conn, current_prices=None):
    """Reporte de Cierre de Rueda (ver conversacion): patrimonio virtual
    total, resultado del dia (vs. el snapshot de la rueda anterior, ver
    save_daily_snapshot -- None si todavia no hay snapshot previo, nunca se
    inventa un 0% de referencia) y resumen de trades cerrados/abiertos."""
    today = today_ar().isoformat()
    trades_today = [t for t in list_closed_trades(conn) if t["exit_date"] == today]
    open_positions = list_open_positions(conn)
    equity_usd = get_equity_usd(conn, current_prices)

    prev_snapshot = db.get_latest_paper_snapshot_before(conn, today)
    if prev_snapshot and prev_snapshot["equity_usd"]:
        resultado_dia_usd = equity_usd - prev_snapshot["equity_usd"]
        resultado_dia_pct = resultado_dia_usd / prev_snapshot["equity_usd"] * 100
    else:
        resultado_dia_usd = None
        resultado_dia_pct = None

    return {
        "date": today,
        "equity_usd": round(equity_usd, 2),
        "cash_usd": round(get_cash_usd(conn), 2),
        "resultado_dia_usd": round(resultado_dia_usd, 2) if resultado_dia_usd is not None else None,
        "resultado_dia_pct": round(resultado_dia_pct, 2) if resultado_dia_pct is not None else None,
        "trades_closed_today": trades_today,
        "trades_closed_today_count": len(trades_today),
        "open_positions": open_positions,
        "open_positions_count": len(open_positions),
    }
