"""Scanner intradia + orquestacion de Paper Trading (ver conversacion) --
logica SIN dependencia de Telegram (reusable desde la API web o un cron
aparte), misma separacion que portfolio/live_signals.py (logica pura) vs
portfolio/telegram_bot.py (que solo formatea texto y manda mensajes). Corre
sobre el MISMO universo/parametros que portfolio/live_signals.py -- una
oportunidad detectada aca es exactamente la misma que mostraria GET
/api/signals o el boton "📊 Oportunidades del día" de Telegram.

Horario de rueda declarado por el usuario: 11:00 a 17:00 ART (ver
is_market_hours). Tambien se excluye fin de semana -- chequeo defensivo no
pedido explicitamente pero necesario para no evaluar señales un dia sin
mercado (Argentina no opera sabado/domingo)."""
from portfolio import live_signals, paper_trading
from portfolio.backtest import UNIVERSE, fetch_ticker_history
from portfolio.forecasting import build_model_input
from portfolio.live_signals import _implied_ccl_rate
from portfolio.tz import now_ar

MARKET_OPEN_HOUR = 11
MARKET_CLOSE_HOUR = 17


def is_trading_day(moment=None):
    moment = moment or now_ar()
    return moment.weekday() < 5


def is_market_hours(moment=None):
    moment = moment or now_ar()
    if not is_trading_day(moment):
        return False
    return MARKET_OPEN_HOUR <= moment.hour < MARKET_CLOSE_HOUR


def current_price(conn, symbol, asset_type):
    """Precio actual (USD CCL, ARS) de un ticker del universo -- ultima fila
    de fetch_ticker_history (data912, se actualiza intradia), la MISMA
    fuente que usa live_signals para evaluar señales de HOY. None si no hay
    historial suficiente para convertir a USD."""
    raw_df = fetch_ticker_history(symbol, asset_type, conn=conn)
    if raw_df.empty:
        return None
    model_df, _ccl, _currency = build_model_input(conn, raw_df, asset_type)
    if model_df.empty:
        return None
    price_usd = float(model_df["close"].iloc[-1])
    ccl_rate = _implied_ccl_rate(raw_df, model_df)
    return round(price_usd, 2), round(price_usd * ccl_rate, 2)


def current_prices_for_symbols(conn, symbol_asset_pairs):
    """{'symbol': (price_usd, price_ars)} -- un simbolo se omite (no una
    excepcion) si fetch_ticker_history o la conversion fallan, para que un
    ticker puntual caido no tire abajo todo el ciclo de scan."""
    prices = {}
    for symbol, asset_type in symbol_asset_pairs:
        try:
            price = current_price(conn, symbol, asset_type)
        except Exception:
            price = None
        if price is not None:
            prices[symbol] = price
    return prices


def scan_once(conn, client, universe=UNIVERSE):
    """Un ciclo del scanner intradia:
    1. Chequea salidas (TP/SL/max_hold) de las posiciones virtuales abiertas
       contra el precio actual de cada una.
    2. Si paper_trading_enabled, evalua las señales de HOY de ambos motores
       (Chronos-Bolt solo corre para Core si el filtro de regimen pasa,
       igual que /signals) y abre una posicion virtual nueva por cada señal
       que todavia no tenga una posicion abierta en el mismo symbol+engine.
    Devuelve {'closed_trades': [...], 'new_entries': [...]} -- este modulo
    nunca manda nada por Telegram, eso lo decide el llamador."""
    asset_type_by_symbol = {symbol: asset_type for symbol, asset_type in universe}

    open_positions = paper_trading.list_open_positions(conn)
    open_pairs = {(p["symbol"], p["asset_type"]) for p in open_positions}
    prices = current_prices_for_symbols(conn, open_pairs)
    closed_trades = paper_trading.check_exits(conn, prices)

    new_entries = []
    if paper_trading.is_enabled(conn):
        result = live_signals.compute_today_signals(conn, client, universe=universe, account_value=None)
        for engine, signals in (("core", result["core_signals"]), ("satellite", result["satellite_signals"])):
            for signal in signals:
                asset_type = asset_type_by_symbol.get(signal["symbol"])
                if asset_type is None:
                    continue
                opened = paper_trading.open_from_signal(conn, signal, engine, asset_type)
                if opened is not None:
                    new_entries.append(opened)
    return {"closed_trades": closed_trades, "new_entries": new_entries}


def build_closing_report(conn):
    """Reporte de Cierre de Rueda (ver conversacion) -- trae precios
    actuales solo de lo que sigue abierto (no re-escanea todo el universo),
    calcula el reporte y GUARDA el snapshot de equity de hoy (para que el
    reporte de mañana tenga contra que comparar)."""
    open_positions = paper_trading.list_open_positions(conn)
    pairs = {(p["symbol"], p["asset_type"]) for p in open_positions}
    prices = current_prices_for_symbols(conn, pairs)
    report = paper_trading.compute_daily_report(conn, prices)
    paper_trading.save_daily_snapshot(conn, report["equity_usd"], date=report["date"])
    return report
