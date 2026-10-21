"""Ejecución REAL de UNA pata contra Binance (ver portfolio/order_execution.py,
el mismo patrón pero para BYMA/instruments) -- place_real_binance_order es el
ÚNICO punto de entrada permitido para que cualquier código de este proyecto
mande una orden real a Binance. Nunca se llama a BinanceAdapter.get_order_budget/
confirm_order directo desde otro lugar.

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
    external_id = f"kronos-binance-{symbol}-{market}-{int(time.time() * 1000)}"

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
    return db.add_binance_order(
        conn, broker_account_id=account["id"], market=market, symbol=symbol, side=side,
        cantidad=cantidad, precio=precio, monto_usd=monto_usd,
        estado="confirmada", external_id=external_id, binance_order_id=binance_order_id,
    )
