"""Ejecución REAL de órdenes (ver conversación: "empezá a desarrollar la
ejecución real") -- place_real_order es el ÚNICO punto de entrada permitido
para que cualquier código de este proyecto mande una orden real a un
bróker. Nunca se llama a adapter.get_order_budget/confirm_order directo
desde otro lugar (ruta HTTP, scanner, lo que sea) -- siempre a través de
acá, para que los 2 gates (interruptor maestro + tope de exposición) sean
imposibles de saltear por accidente.

Orden de los chequeos, en este orden exacto -- cada uno puede abortar todo
ANTES de tocar la red, sin persistir nada (no se intentó nada, no hay nada
que auditar):
1. ensure_auto_trading_enabled(conn) -- el interruptor maestro (ver
   broker_client.py), apagado por default.
2. El instrumento tiene que tener broker_account_id seteado -- sin eso no
   hay forma de saber CON QUÉ cuenta ejecutar (ver conversación, "cómo
   hacemos con múltiples brokers": nunca "PPI" en abstracto).
3. compute_auto_trading_limit_ars(conn, patrimonio_ars) contra la
   exposición YA confirmada (sum_confirmed_auto_trading_exposure_ars) MÁS
   el monto de ESTA orden -- si se pasaría del tope, se aborta antes de
   pedirle presupuesto al bróker.

Recién ahí se llama get_order_budget + confirm_order del adapter
correspondiente -- SIEMPRE se persiste un registro en auto_trading_orders
después de ese punto (confirmada o error), nunca se descarta un intento
real sin dejar rastro para auditar después.

PPI e IOL tienen ejecución real implementada (ver broker_client.py::
PPIAdapter/IOLAdapter, esta última agregada 2026-10-20) -- Balanz/Matrix
siguen dejando NotImplementedError explícito (ver BrokerAdapter base),
mismo criterio honesto que el resto del proyecto: nunca fabricar una
integración que no existe. Nota: IOL no tiene un endpoint de presupuesto
real (ver IOLAdapter.get_order_budget) -- su "presupuesto" es una
estimación local, nunca una cotización del bróker como sí lo es la de PPI;
quien llame a este módulo no debería tratar `budget` como equivalente
entre brokers.

Sin UI ni ruta HTTP todavía que llame esto -- "Aceptar" en una Propuesta
sigue siendo 100% manual (ver proposals.js::accept). Conectar esto a un
botón real es una decisión de UX aparte (confirmaciones, manejo de
errores en pantalla, etc.), deliberadamente fuera del alcance de esta
ronda."""
import time

from portfolio import broker_client, db


class OrderExecutionError(Exception):
    """Cualquier motivo por el que place_real_order abortó ANTES de tocar
    la red (interruptor apagado, instrumento sin cuenta de bróker, tope de
    exposición superado) -- distinto de una falla DEL lado del bróker ya
    en vuelo (esa persiste un registro 'error' y re-lanza la excepción
    original del adapter, no esta)."""


def place_real_order(conn, *, instrument_id, operacion, cantidad, precio, patrimonio_ars,
                      quantity_type="Papeles", operation_type="PRECIO-LIMITE",
                      operation_term="HASTA-SU-EJECUCIÓN", settlement="INMEDIATA"):
    """Punto único de entrada -- ver docstring del módulo. Devuelve el
    registro de auto_trading_orders recién insertado."""
    broker_client.ensure_auto_trading_enabled(conn)

    if operacion not in ("compra", "venta"):
        raise OrderExecutionError(f"operacion debe ser 'compra' o 'venta', recibido {operacion!r}")

    instrument = db.get_instrument(conn, instrument_id)
    if instrument is None:
        raise OrderExecutionError(f"Instrumento {instrument_id} no existe.")
    if not instrument.get("broker_account_id"):
        raise OrderExecutionError(
            f"{instrument['symbol']}: sin cuenta de bróker asignada (\"Cotiza según\" en el instrumento) -- "
            "no hay con qué ejecutar la orden."
        )

    account = db.get_broker_account(conn, instrument["broker_account_id"])
    if account is None or not account["is_active"]:
        raise OrderExecutionError(f"{instrument['symbol']}: la cuenta de bróker asignada no existe o está inactiva.")

    monto_ars = cantidad * precio
    limite = broker_client.compute_auto_trading_limit_ars(conn, patrimonio_ars)
    exposicion_actual = db.sum_confirmed_auto_trading_exposure_ars(conn)
    if exposicion_actual + monto_ars > limite:
        raise OrderExecutionError(
            f"Orden de {monto_ars:.2f} ARS superaría el tope de exposición del bot "
            f"({exposicion_actual:.2f} ya confirmado + esta orden > {limite:.2f} de tope configurado)."
        )

    adapter = broker_client._adapter_for(account)
    ppi_operation = "Compra" if operacion == "compra" else "Venta"
    external_id = f"kronos-{instrument_id}-{int(time.time() * 1000)}"

    order_kwargs = dict(
        symbol=instrument["symbol"], asset_type=instrument["type"], operation=ppi_operation,
        quantity=cantidad, price=precio, quantity_type=quantity_type, operation_type=operation_type,
        operation_term=operation_term, settlement=settlement,
    )
    try:
        budget = adapter.get_order_budget(**order_kwargs)
        result = adapter.confirm_order(**order_kwargs, external_id=external_id, budget=budget)
    except Exception as e:
        order_id = db.add_auto_trading_order(
            conn, instrument_id=instrument_id, broker_account_id=account["id"], symbol=instrument["symbol"],
            operacion=operacion, cantidad=cantidad, precio=precio, monto_ars=monto_ars,
            estado="error", external_id=external_id, error_detalle=str(e),
        )
        raise

    ppi_order_id = str(result.get("orderId") or result.get("id") or "") if isinstance(result, dict) else ""
    order_id = db.add_auto_trading_order(
        conn, instrument_id=instrument_id, broker_account_id=account["id"], symbol=instrument["symbol"],
        operacion=operacion, cantidad=cantidad, precio=precio, monto_ars=monto_ars,
        estado="confirmada", external_id=external_id, ppi_order_id=ppi_order_id,
    )
    return db.get_auto_trading_order(conn, order_id)
