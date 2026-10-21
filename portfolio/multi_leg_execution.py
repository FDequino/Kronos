"""Orquestacion de ordenes de 2+ patas (ver portfolio/order_execution.py,
portfolio/binance_execution.py: cada uno ejecuta UNA pata por llamada, nunca
una posicion compuesta completa) -- usado por portfolio/execution_router.py
para ejecutar de verdad un execution_decision (abrir/cerrar Carry BTC/ETH:
spot + futuro perpetuo).

Ejecuta las patas EN ORDEN, SECUENCIAL -- se detiene en la primera falla. Si
una pata YA se ejecuto y la siguiente falla, la posicion queda hedgeada a
medias (ej. compraste spot pero no pudiste vender el futuro) -- esto NUNCA
se intenta "deshacer" solo (un rollback automatico de una orden de mercado
ya ejecutada es, en si mismo, una tercera orden con su propio riesgo/costo,
no algo seguro de inventar) -- se marca 'partially_filled' y se avisa fuerte
por Telegram para que el usuario decida a mano, mismo principio que
order_execution.py (nunca se descarta un intento real sin dejar rastro)."""
from portfolio import binance_execution, db


def execute_legs(conn, *, legs, patrimonio_usd):
    """Ejecuta cada pata de `legs` (shape: [{"broker", "market", "symbol",
    "side", "quantity", "price_usd"}, ...]) EN ORDEN contra Binance (unico
    broker con patas multiples hoy, ver docstring del modulo). Devuelve
    {"status": "executed"|"partially_filled", "filled_legs": [...],
    "error": str|None}."""
    filled = []
    for leg in legs:
        if leg["broker"] != "Binance":
            # Nunca inventado -- hoy solo Carry BTC/ETH usa este orquestador,
            # y sus 2 patas son siempre Binance. Un broker no soportado aca
            # es un bug de quien arma el intent, no algo que este modulo
            # deba adivinar como resolver.
            return {
                "status": "partially_filled" if filled else "failed", "filled_legs": filled,
                "error": f"Broker {leg['broker']!r} no soportado por multi_leg_execution (solo Binance hoy).",
            }
        try:
            order = binance_execution.place_real_binance_order(
                conn, market=leg["market"], symbol=leg["symbol"], side=leg["side"],
                cantidad=leg["quantity"], precio=leg["price_usd"], patrimonio_usd=patrimonio_usd,
            )
        except Exception as e:
            return {
                "status": "partially_filled" if filled else "failed", "filled_legs": filled,
                "error": f"Pata {leg['symbol']} ({leg['market']}, {leg['side']}) falló DESPUÉS de "
                         f"{len(filled)} pata(s) ya ejecutada(s): {e}" if filled else str(e),
            }
        filled.append(order)

    return {"status": "executed", "filled_legs": filled, "error": None}
