"""Ejecución REAL de UNA pata contra Binance (ver portfolio/order_execution.py,
el mismo patrón pero para BYMA/instruments) -- place_real_binance_order es el
ÚNICO punto de entrada permitido para que cualquier código de este proyecto
mande una orden real a Binance.

BUG REAL encontrado y corregido (ver conversación, auditoría del camino de
ejecución 2026-11-10, antes de conectar la primera cuenta real): esta
función persistía SIEMPRE `cantidad`/`precio`/`monto_usd` tal como los pasó
el llamador (la estimación PRE-trade, usada para el chequeo de tope) -- para
una orden MARKET (el default, `operation_type="PRECIO-DE-MERCADO"`), el
precio real de ejecución lo decide el mercado en ese instante, nunca el
estimado. Eso significa que `sum_confirmed_binance_exposure_usd` (el tope de
exposición real) compondría sobre números cada vez más desactualizados, no
sobre lo que de verdad se ejecutó. Corregido extrayendo el fill REAL (ver
_extract_real_fill): para spot viene en la misma respuesta de la orden
(`fills`/`executedQty`/`cummulativeQuoteQty`, confirmado contra la doc
oficial); para futuros la respuesta de POST /fapi/v1/order NO trae
`avgPrice` (solo aparece en GET /fapi/v1/order, confirmado contra la doc
oficial) -- hace falta una consulta extra (`BinanceAdapter.query_order`)
para saber el precio real ejecutado. Si la orden todavía no tiene fill real
(ej. una LIMIT que queda 'NEW'), se conserva el estimado -- nunca se
inventa un fill que no pasó.

Nunca se llama a BinanceAdapter.get_order_budget/confirm_order directo desde otro lugar.

Sin `instruments` de por medio a propósito (ver schema.sql::binance_orders):
Carry BTC/ETH son 2 patas (spot + futuro perpetuo) del MISMO symbol
(BTCUSDT/ETHUSDT), y el modelo de `instruments` (1 fila por symbol+market,
pensado para una tenencia de cartera) no encaja con "2 mercados del mismo
symbol, ninguno es una tenencia que se muestra en el Resumen" -- forzarlo ahí
sería más confuso que una ruta paralela explícita.

Orden de los chequeos, igual criterio que order_execution.py -- cada uno
aborta ANTES de tocar la red, sin persistir nada:
1. ensure_auto_trading_enabled(conn) -- mismo interruptor maestro que el
   resto de la ejecución real (broker_client.py), un solo lugar lo prende/apaga.
2. compute_auto_trading_limit_usd(conn, patrimonio_usd) contra la exposición
   YA confirmada (sum_confirmed_binance_exposure_usd) MÁS el monto de ESTA
   orden -- si se pasaría del tope, se aborta antes de pedirle presupuesto a Binance.

Recién ahí se llama get_order_budget + confirm_order de BinanceAdapter --
SIEMPRE se persiste un registro en binance_orders después de ese punto
(confirmada o error), nunca se descarta un intento real sin dejar rastro.

Ejecuta UNA pata por llamada (igual que BinanceAdapter.confirm_order) --
armar las 2 patas de un carry trade como una sola posición hedgeada es
responsabilidad de portfolio/multi_leg_execution.py, no de acá."""
import time

from portfolio import broker_client, db


class BinanceExecutionError(Exception):
    """Cualquier motivo por el que place_real_binance_order abortó ANTES de
    tocar la red -- distinto de una falla DEL lado de Binance ya en vuelo
    (esa persiste un registro 'error' y re-lanza la excepción original)."""


def _extract_real_fill(adapter, result, *, market, symbol, asset_type, fallback_cantidad, fallback_precio):
    """Devuelve (cantidad, precio) REALES de la orden recien ejecutada, o el
    estimado pre-trade si todavia no hay fill real (ver docstring del
    modulo). spot: viene en la misma respuesta (fills/executedQty/
    cummulativeQuoteQty). futuros: la respuesta de POST no trae avgPrice --
    hace falta un query_order extra para conseguirlo."""
    if not isinstance(result, dict):
        return fallback_cantidad, fallback_precio

    if market == "spot":
        executed_qty = float(result.get("executedQty") or 0)
        cumulative_quote = float(result.get("cummulativeQuoteQty") or 0)
        if executed_qty > 0 and cumulative_quote > 0:
            return executed_qty, cumulative_quote / executed_qty
        return fallback_cantidad, fallback_precio

    # futuros -- consultar la orden para conseguir avgPrice real (no viene en el POST)
    order_id = result.get("orderId")
    if order_id is None:
        return fallback_cantidad, fallback_precio
    try:
        queried = adapter.query_order(symbol=symbol, order_id=order_id, asset_type=asset_type)
    except Exception:
        return fallback_cantidad, fallback_precio  # nunca rompe la orden ya confirmada por no poder consultarla despues
    executed_qty = float(queried.get("executedQty") or 0)
    avg_price = float(queried.get("avgPrice") or 0)
    if executed_qty > 0 and avg_price > 0:
        return executed_qty, avg_price
    return fallback_cantidad, fallback_precio


def _active_binance_account(conn):
    """Única cuenta Binance activa -- ValueError explícito si no hay ninguna
    o si hay más de una (ambigüedad real: con cuál operar), nunca adivina."""
    accounts = [a for a in db.list_broker_accounts(conn) if a["broker_name"] == "Binance" and a["is_active"]]
    if not accounts:
        raise BinanceExecutionError("No hay ninguna cuenta Binance activa cargada en Ajustes > Integraciones.")
    if len(accounts) > 1:
        raise BinanceExecutionError(
            f"Hay {len(accounts)} cuentas Binance activas -- ambiguo con cuál operar, desactivá todas menos una."
        )
    return accounts[0]


def place_real_binance_order(conn, *, market, symbol, side, cantidad, precio, patrimonio_usd,
                              operation_type="PRECIO-DE-MERCADO"):
    """Punto único de entrada -- ver docstring del módulo. market: 'spot'|'futuros'
    (se traduce al asset_type que ya entiende BinanceAdapter: 'cripto'|'futuro_perpetuo').
    side: 'buy'|'sell' (se traduce a 'Compra'/'Venta', vocabulario genérico
    del proyecto). Devuelve el registro de binance_orders recién insertado."""
    broker_client.ensure_auto_trading_enabled(conn)

    if market not in ("spot", "futuros"):
        raise BinanceExecutionError(f"market debe ser 'spot' o 'futuros', recibido {market!r}")
    if side not in ("buy", "sell"):
        raise BinanceExecutionError(f"side debe ser 'buy' o 'sell', recibido {side!r}")

    account = _active_binance_account(conn)

    monto_usd = cantidad * precio
    limite = broker_client.compute_auto_trading_limit_usd(conn, patrimonio_usd)
    exposicion_actual = db.sum_confirmed_binance_exposure_usd(conn)
    if exposicion_actual + monto_usd > limite:
        raise BinanceExecutionError(
            f"Orden de {monto_usd:.2f} USD superaría el tope de exposición del bot en Binance "
            f"({exposicion_actual:.2f} ya confirmado + esta orden > {limite:.2f} de tope configurado)."
        )

    adapter = broker_client._adapter_for(account)
    asset_type = "cripto" if market == "spot" else "futuro_perpetuo"
    binance_operation = "Compra" if side == "buy" else "Venta"
    # BUG REAL encontrado y corregido (ver conversación, primera orden de
    # prueba real en Demo Trading tras el fix de LOT_SIZE, 2026-11-11):
    # Binance rechaza `newClientOrderId` con -1100 "Illegal characters" si
    # supera 36 caracteres (regex real: ^[a-zA-Z0-9-_]{1,36}$) -- el formato
    # anterior ("kronos-binance-{symbol}-{market}-{timestamp}") da 41
    # caracteres para BTCUSDT, ya pasa el límite con el symbol más corto que
    # existe. Acotado: "kr-" + symbol (máx. 12, confirmado real contra el
    # symbol más largo de FUNDING_SYMBOLS, "1000satsusdt") + mercado
    # abreviado + timestamp de 13 dígitos = máx. 34 caracteres.
    market_abbr = "spot" if market == "spot" else "fut"
    external_id = f"kr-{symbol}-{market_abbr}-{int(time.time() * 1000)}"

    order_kwargs = dict(
        symbol=symbol, asset_type=asset_type, operation=binance_operation,
        quantity=cantidad, price=precio, operation_type=operation_type,
    )
    try:
        budget = adapter.get_order_budget(**order_kwargs)
        result = adapter.confirm_order(**order_kwargs, external_id=external_id, budget=budget)
    except Exception as e:
        db.add_binance_order(
            conn, broker_account_id=account["id"], market=market, symbol=symbol, side=side,
            cantidad=cantidad, precio=precio, monto_usd=monto_usd,
            estado="error", external_id=external_id, error_detalle=str(e),
        )
        raise

    binance_order_id = str(result.get("orderId") or "") if isinstance(result, dict) else ""
    cantidad_real, precio_real = _extract_real_fill(
        adapter, result, market=market, symbol=symbol, asset_type=asset_type,
        fallback_cantidad=cantidad, fallback_precio=precio,
    )
    return db.add_binance_order(
        conn, broker_account_id=account["id"], market=market, symbol=symbol, side=side,
        cantidad=cantidad_real, precio=precio_real, monto_usd=cantidad_real * precio_real,
        estado="confirmada", external_id=external_id, binance_order_id=binance_order_id,
    )
