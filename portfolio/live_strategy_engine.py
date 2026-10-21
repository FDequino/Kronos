"""Motor de decision diaria para ejecucion REAL (ver conversacion, "quiero
una plataforma funcional... full-auto o semi-auto") -- compara la señal de
HOY de cada estrategia con plata real contra la posicion REAL actualmente
abierta (portfolio/db.py::strategy_positions) y devuelve un intent
(abrir/cerrar) o None (sin cambios, el caso mas comun: ya se esta bien
posicionado).

Solo cubre lo que de verdad es ejecutable 1:1 contra lo que paso su propio
gate DSR>=0.95 (ver OBJETIVO_MARCO.md):

- Carry BTC/ETH (crypto_carry_btcusdt/crypto_carry_ethusdt): cash-and-carry
  real -- comprar spot + vender futuro perpetuo, delta-neutral, exactamente
  lo que portfolio/crypto_carry.py::simulate_funding_rate_carry_engine
  valida. Registrado abajo.

- Canje AL29/GD29 (canje_al29_gd29): DELIBERADAMENTE SIN intent builder real
  (ver conversacion 2026-10-21, "hagamos el backtest primero"). El backtest
  que paso el gate DSR mide una convergencia algebraica de brecha_pct que
  asume poder estar largo una pata y corto la otra -- irreal para un usuario
  retail que no puede shortear bonos soberanos en IOL/PPI (ver docstring de
  simulate_canje_arbitrage_engine, "LIMITACION declarada"). Se corrio el
  UNICO modelo real ejecutable (comprar la pata barata y rotar, ver
  arbitrage.py::simulate_canje_real_position_engine) contra el mismo gate
  CPCV+DSR: -20.8% anual, Sharpe -0.4, Max DD -68.8%, DSR~0 -- PIERDE PLATA.
  Por eso Canje sigue SOLO en paper trading (portfolio/paper_ledger.py, que
  reusa el modelo teorico para seguir juntando evidencia) -- nunca se
  registra un intent builder real para esta estrategia. Si un dia aparece
  una forma real de shortear/hedgear la pata cara (o cambia la evidencia),
  se vuelve a evaluar -- hasta entonces, run_daily_execution_cycle debe
  loguear explicito "sin ejecucion real implementada" si alguien la
  promueve a 'live', nunca fallar en silencio."""
from portfolio import crypto_carry, db

# Notional 1:1 (sin apalancamiento): se compra spot y se vende EXACTAMENTE
# el mismo monto nocional en el futuro perpetuo -- delta-neutral conservador,
# mismo criterio que crypto_carry.py (sin modelar margen/apalancamiento).


def capital_usd_for_strategy(conn, strategy_name):
    """Capital REAL (USD) asignado a esta estrategia puntual --
    auto_trading_capital_usd (Ajustes, nunca asumido) * capital_pct (ya
    calculado por capital_allocator.py, risk parity entre validated/live).
    None si auto_trading_capital_usd no esta configurado o la estrategia no
    tiene capital_pct > 0 -- nunca se inventa un monto para operar real."""
    from portfolio import strategy_registry

    settings = db.get_app_settings(conn)
    total = settings.get("auto_trading_capital_usd")
    if not total:
        return None
    strategy = strategy_registry.get_strategy(conn, strategy_name)
    if strategy is None or not strategy["capital_pct"]:
        return None
    return total * strategy["capital_pct"] / 100.0


def _crypto_carry_intent(conn, strategy_name, symbol):
    """symbol: 'BTCUSDT'/'ETHUSDT'. Devuelve un intent dict ({"action",
    "legs", "reason"}) o None (sin cambios)."""
    position = db.get_open_strategy_position(conn, strategy_name, "real")
    signal = crypto_carry.compute_funding_rate_carry_signal(symbol)

    if position is None:
        if signal is None:
            return None  # sin edge, sin posicion -- nada que hacer
        capital_usd = capital_usd_for_strategy(conn, strategy_name)
        if not capital_usd or capital_usd <= 0:
            return None  # ver docstring de capital_usd_for_strategy -- nunca inventa un monto
        price = _spot_price_usd(symbol)
        if not price:
            return None
        quantity = round(capital_usd / price, 6)
        if quantity <= 0:
            return None
        return {
            "action": "open",
            "capital_usd": capital_usd,
            "legs": [
                {"broker": "Binance", "market": "spot", "symbol": symbol, "side": "buy",
                 "quantity": quantity, "price_usd": price},
                {"broker": "Binance", "market": "futuros", "symbol": symbol, "side": "sell",
                 "quantity": quantity, "price_usd": price},
            ],
            "reason": signal["note"],
        }

    if signal is not None:
        return None  # ya posicionado y el funding sigue rindiendo -- mantener

    # El funding dejo de rendir lo suficiente (ver MIN_ANNUALIZED_NET_FUNDING_PCT
    # en crypto_carry.py) -- cerrar las 2 patas (reversa de como se abrieron).
    legs = position["legs"]
    close_legs = [
        {"broker": leg["broker"], "market": leg["market"], "symbol": leg["symbol"],
         "side": "sell" if leg["side"] == "buy" else "buy", "quantity": leg["quantity"],
         "price_usd": leg["price_usd"]}
        for leg in legs
    ]
    return {
        "action": "close", "capital_usd": position["capital_usd"], "legs": close_legs,
        "reason": f"Funding de {symbol} ya no cubre el costo neto minimo -- se cierra la posicion.",
        "position_id": position["id"],
    }


def _spot_price_usd(symbol):
    """Precio spot actual -- reusa el mismo fetch que crypto_carry.py para no
    duplicar la fuente (Binance publico, sin credenciales)."""
    import requests

    try:
        resp = requests.get(
            "https://api.binance.com/api/v3/ticker/price", params={"symbol": symbol}, timeout=10,
        )
        resp.raise_for_status()
        return float(resp.json()["price"])
    except Exception:
        return None


# Registro estrategia -> builder de intent REAL (ver docstring del modulo:
# Canje NO esta acá a proposito).
_REAL_INTENT_BUILDERS = {
    "crypto_carry_btcusdt": lambda conn: _crypto_carry_intent(conn, "crypto_carry_btcusdt", "BTCUSDT"),
    "crypto_carry_ethusdt": lambda conn: _crypto_carry_intent(conn, "crypto_carry_ethusdt", "ETHUSDT"),
}


def build_real_intent(conn, strategy_name):
    """None si la estrategia no tiene builder real registrado (ver
    docstring del modulo -- Canje) o si el builder decide que no hay cambios
    que hacer hoy. dict de intent si hay una accion real para tomar."""
    builder = _REAL_INTENT_BUILDERS.get(strategy_name)
    if builder is None:
        return None
    return builder(conn)


def has_real_intent_builder(strategy_name):
    return strategy_name in _REAL_INTENT_BUILDERS
