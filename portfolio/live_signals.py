"""Señales EN VIVO (de HOY) de ambos motores -- a diferencia de
portfolio/backtest.py (que simula la historia completa con foresight sobre
el precio futuro ya conocido), este modulo corre UNA vez sobre el ultimo
dato disponible de cada ticker del universo, sin simular la salida del
trade. Fuente unica para GET /api/signals (ver webui/app.py) y el comando
/signals de Telegram (ver portfolio/telegram_bot.py) -- asi los dos nunca
pueden mostrar numeros distintos para la misma señal del dia.

Reusa las mismas constantes y la misma logica de ranking/sizing que
portfolio/backtest.py (importadas de ahi, nunca reescritas con otro numero)
para que "lo que se simulo" y "lo que se opera en vivo" sean exactamente la
misma regla.

Refactorizacion financiera en USD CCL (ver conversacion): TODO el analisis
(deteccion de tacticas del Satelite, ATR, efficiency_score del Core) corre
sobre la serie YA CONVERTIDA a USD CCL (via
portfolio/forecasting.py::build_model_input, la MISMA conversion que ve
Chronos-Bolt y portfolio/features.py) -- nunca sobre la serie ARS cruda.
Cada señal se devuelve con AMBAS monedas (sufijo _usd/_ars) para que la UI y
Telegram muestren la equivalencia real al precio que se opera en el broker
(ARS en BYMA para accion/cedear), sin que la moneda de ANALISIS deje de ser
USD. ccl_rate expone el tipo de cambio usado para esa conversion puntual,
nunca oculto."""
import numpy as np

from portfolio import config as capital_config
from portfolio import (
    arbitrage, cash, db, factor_investing, features, fetchers, fixed_income, indicators, macro_data, nlp_agent,
    pairs_trading, regime, short_term_engine, signals, synthetic_dollar,
)
from portfolio.backtest import (
    CORE_ATR_WINDOW, CORE_MAX_HOLD_DAYS, CORE_REGIME_EMA_WINDOW, CORE_STOP_ATR_MULT, CORE_STOP_FLAT_PCT,
    CORE_TP_ATR_MULT, CORE_TP_FLAT_PCT, HORIZON_DAYS, LOOKBACK, SATELLITE_MAX_POSITIONS, SATELLITE_MIN_WARMUP_ROWS,
    UNIVERSE, _chronos_pct_changes, fetch_ticker_history,
)
from portfolio.forecasting import build_model_input


def total_portfolio_value_ars(conn):
    """Patrimonio total en ARS (holdings + cauciones vigentes + caja) -- mismo
    criterio que portfolio/routes.py::_compute_ganancia y
    webui/static/js/portfolio/patrimonio.js::headerHtml ("la caja es tan tuya
    como un holding", Fase 7). Antes esta funcion dejaba la caja afuera y el
    Patrimonio total del PDF (ver pdf_report.py) no coincidia con el
    Patrimonio neto del dashboard -- usa portfolio/cash.py directo (la misma
    fuente que ya alimenta GET /api/portfolio/cash-summary) en vez de
    re-derivar el saldo por su cuenta. Tambien es la base del sizing de
    Core/Satelite/Pairs/Factor (ver compute_today_signals abajo): segun
    portfolio/config.py, esos porcentajes reparten el 100% del patrimonio, no
    solo lo ya invertido, asi que la caja ociosa debe contar aca tambien."""
    holdings = db.get_holdings_with_quotes(conn)
    settings = db.get_app_settings(conn)
    try:
        rate = fetchers.resolve_usd_rate(settings)
    except Exception:
        rate = None  # la fuente de cotizacion elegida no respondio -- se suma sin convertir USD->ARS

    total = 0.0
    for h in holdings:
        valor = h.get("valor_actual")
        if valor is None:
            continue
        if h["instrument"]["currency"] == "USD" and rate:
            valor *= rate
        total += valor

    cauciones = [c for c in db.get_cauciones_resumen(conn) if not c["vencido"]]
    total += sum(c["valor_actual"] for c in cauciones)

    saldo_caja = cash.get_cash_balance(conn)
    total += saldo_caja["ARS"]
    if rate:
        total += saldo_caja["USD"] * rate  # sin cotizacion, la caja en USD no se suma (nunca se inventa una tasa)
    return total


def _implied_ccl_rate(raw_df, model_df):
    """CCL EFECTIVO de la ultima rueda -- se recupera dividiendo el cierre
    nativo por el cierre ya convertido a USD (build_model_input hizo
    exactamente esa division al reves), nunca se vuelve a pedir la tasa por
    separado. Para bono/on (ya nativos en USD, model_df ES raw_df) da 1.0 --
    correcto, ahi ARS/USD no tienen sentido como par de conversion."""
    close_usd = float(model_df["close"].iloc[-1])
    if not close_usd:
        return 1.0
    return float(raw_df["close"].iloc[-1]) / close_usd


def _dual_price(price_usd, ccl_rate):
    return round(price_usd, 2), round(price_usd * ccl_rate, 2)


def _spy_regime_filter(conn, universe):
    """SPY > su propia EMA50 (sobre la serie en USD CCL) -- si no hay
    suficiente historial de SPY en el universo, se trata como regimen
    desfavorable (nunca se asume el mejor caso con datos faltantes)."""
    spy_entry = next((u for u in universe if u[0] == "SPY"), None)
    if spy_entry is None:
        return False
    raw_df = fetch_ticker_history(*spy_entry, conn=conn)
    if len(raw_df) < CORE_REGIME_EMA_WINDOW + 5:
        return False
    model_df, _ccl, _currency = build_model_input(conn, raw_df, spy_entry[1])
    close_model = model_df["close"].reset_index(drop=True)
    ema50 = indicators.ema(close_model, CORE_REGIME_EMA_WINDOW)
    return bool(close_model.iloc[-1] > ema50.iloc[-1])


def _today_core_candidates(universe, client, conn):
    """Recorre el universo UNA vez con el dato mas reciente de cada ticker --
    mismo ranking que portfolio/backtest.py::simulate_core_engine (ATR y
    precio YA en USD CCL), pero sin foresight (no simula la salida, solo
    evalua la entrada de hoy)."""
    candidates = []
    prices_usd = {}
    ccl_rates = {}
    skipped = {}
    min_rows = LOOKBACK + CORE_ATR_WINDOW + 5
    for symbol, asset_type in universe:
        raw_df = fetch_ticker_history(symbol, asset_type, conn=conn)
        if len(raw_df) < min_rows:
            skipped[symbol] = f"historial insuficiente (necesita >= {min_rows} ruedas)"
            continue
        model_df, _ccl, _currency = build_model_input(conn, raw_df, asset_type)
        close_model = model_df["close"].reset_index(drop=True)
        prices_usd[symbol] = float(close_model.iloc[-1])
        ccl_rates[symbol] = _implied_ccl_rate(raw_df, model_df)
        atr_pct_usd = float(indicators.atr_pct(model_df, CORE_ATR_WINDOW).iloc[-1])

        window = close_model.iloc[-LOOKBACK:]
        try:
            p10_pct, p50_pct, p90_pct = _chronos_pct_changes(client, window, HORIZON_DAYS)
        except Exception:
            skipped[symbol] = "Chronos-Bolt no respondio"
            continue
        band_width = p90_pct - p10_pct
        if band_width <= 0:
            skipped[symbol] = "banda de prediccion degenerada"
            continue
        dispersion_ratio = abs(p50_pct) / band_width
        candidates.append({
            "symbol": symbol,
            "efficiency_score": signals.compute_efficiency_score(p50_pct, dispersion_ratio),
            "dispersion_ratio": dispersion_ratio, "atr_pct": atr_pct_usd,
            "p10_pct": p10_pct, "p50_pct": p50_pct, "p90_pct": p90_pct,
        })
    return candidates, prices_usd, ccl_rates, skipped


def compute_core_signals(conn, client, universe=UNIVERSE, account_value=None):
    """Señales Core de HOY, analizadas en USD CCL. Si SPY no esta por encima
    de su EMA50, ni siquiera se consulta a Chronos-Bolt (mismo criterio que
    simulate_core_engine) -- el capital Core queda 100% en Caucion."""
    allocation = capital_config.get_capital_allocation(conn)
    core_capital = (account_value or 0) * allocation["core_pct"] / 100.0
    spy_above_ema50 = _spy_regime_filter(conn, universe)

    if not spy_above_ema50:
        return {
            "signals": [], "spy_above_ema50": spy_above_ema50, "skipped": {},
            "core_capital_ars": round(core_capital, 2) if account_value else None,
        }

    candidates, prices_usd, ccl_rates, skipped = _today_core_candidates(universe, client, conn)
    core_min_score = capital_config.get_core_params(conn)["min_efficiency_score"]
    ranked = signals.rank_core_candidates(
        candidates, spy_above_ema50, top_n=signals.CORE_TOP_N, min_score=core_min_score
    )

    out = []
    for cand in ranked:
        raw_weight = signals.compute_core_position_weight(cand["atr_pct"], cand["dispersion_ratio"])
        effective_weight = raw_weight / signals.CORE_TOP_N
        symbol = cand["symbol"]
        entry_usd = prices_usd[symbol]
        ccl_rate = ccl_rates[symbol]
        tp_pct = max(CORE_TP_FLAT_PCT, CORE_TP_ATR_MULT * cand["atr_pct"])
        # CORE_STOP_FLAT_PCT es un TECHO, no un piso (ver backtest.py,
        # mismo criterio que simulate_core_engine(..., stop_is_ceiling=True)
        # -- diagnostico de la conversacion: el piso dejaba stops de hasta
        # -12% en nombres volatiles).
        stop_pct = min(CORE_STOP_FLAT_PCT, CORE_STOP_ATR_MULT * cand["atr_pct"])

        entry_price_usd, entry_price_ars = _dual_price(entry_usd, ccl_rate)
        take_profit_price_usd, take_profit_price_ars = _dual_price(entry_usd * (1 + tp_pct / 100), ccl_rate)
        initial_stop_price_usd, initial_stop_price_ars = _dual_price(entry_usd * (1 - stop_pct / 100), ccl_rate)
        out.append({
            "symbol": symbol,
            "efficiency_score": round(cand["efficiency_score"], 1),
            "chronos_p50_pct": round(cand["p50_pct"], 2),
            "dispersion_ratio": round(cand["dispersion_ratio"], 4),
            "ccl_rate": round(ccl_rate, 2),
            "entry_price_usd": entry_price_usd, "entry_price_ars": entry_price_ars,
            "take_profit_price_usd": take_profit_price_usd, "take_profit_price_ars": take_profit_price_ars,
            "take_profit_pct": round(tp_pct, 2),
            "initial_stop_price_usd": initial_stop_price_usd, "initial_stop_price_ars": initial_stop_price_ars,
            "initial_stop_pct": round(stop_pct, 2),
            "position_weight": round(effective_weight, 4),
            "suggested_amount_ars": (
                round(capital_config.suggested_amount(account_value, allocation["core_pct"], effective_weight), 2)
                if account_value else None
            ),
            "max_hold_days": CORE_MAX_HOLD_DAYS,
        })
    return {
        "signals": out, "spy_above_ema50": spy_above_ema50, "skipped": skipped,
        "core_capital_ars": round(core_capital, 2) if account_value else None,
    }


def _today_market_regime(conn):
    """Régimen de HOY (ver portfolio/regime.py) -- VIX más reciente conocido
    + SPY vs su propia EMA50, misma fuente (portfolio/macro_data.py) que usa
    el backtest para que "lo que se simuló" y "lo que se opera en vivo" sean
    la misma regla. Degrada a 'lateral' si falta algún dato (nunca asume el
    mejor caso con datos faltantes, mismo criterio que _spy_regime_filter)."""
    try:
        spy_series = macro_data.get_global_trend_series(conn, "SPY")
        vix_series = macro_data.get_vix_series(conn)
    except Exception:
        return regime.REGIME_LATERAL
    vix_value = float(vix_series.iloc[-1]) if len(vix_series) else None
    return regime.classify_regime(spy_series, vix_value)


def _today_ccl_scale(conn):
    """Escala por Z-Score de CCL de HOY (ver portfolio/regime.py::ccl_exposure_scale
    y portfolio/backtest.py::_ccl_scale_by_date -- misma formula/fuente,
    'lo que se simuló' y 'lo que se opera en vivo' son la misma regla). 1.0
    (sin modular) si no se puede calcular -- nunca se asume un salto que no
    se pudo medir."""
    try:
        ccl_series = macro_data.get_ccl_series(conn)
        ccl_log_return = np.log(ccl_series / ccl_series.shift(1))
        ccl_zscore_series = features._rolling_zscore(ccl_log_return).dropna()
        if ccl_zscore_series.empty:
            return 1.0
        return regime.ccl_exposure_scale(float(ccl_zscore_series.iloc[-1]))
    except Exception:
        return 1.0


def compute_satellite_signals(conn, universe=UNIVERSE, account_value=None):
    """Señales Satelite de HOY, analizadas en USD CCL (mismo criterio que
    simulate_satellite_engine), sin foresight. No llama a Chronos-Bolt.
    Take Profit/Stop Loss salen de signal_config (ver
    portfolio/config.py::get_satellite_exit_params), nunca de una constante
    fija -- stop_loss_pct es un TECHO (ver conversacion): el stop real es
    min(stop_loss_pct, SATELLITE_TRAILING_STOP_ATR_MULT * ATR%).

    Régimen de mercado + Volatility Targeting + Kelly (ver conversación,
    evolución del motor cuantitativo): en régimen de alta volatilidad
    (VIX > regime.VIX_HIGH_THRESHOLD) no se abren posiciones nuevas -- mismo
    criterio que simulate_satellite_engine. El tamaño de cada señal ya no es
    equal-weight fijo (1/SATELLITE_MAX_POSITIONS): es el MENOR entre el tope
    de volatilidad (ATR de cada activo) y el tope de Kelly (medido sobre el
    último resumen de backtest persistido)."""
    allocation = capital_config.get_capital_allocation(conn)
    exit_params = capital_config.get_satellite_exit_params(conn)
    satellite_capital = (account_value or 0) * allocation["satellite_pct"] / 100.0
    max_slot_weight = 1.0 / SATELLITE_MAX_POSITIONS

    current_regime = _today_market_regime(conn)
    if regime.satellite_blocked(current_regime):
        return {
            "signals": [], "skipped": {}, "regime": current_regime,
            "satellite_capital_ars": round(satellite_capital, 2) if account_value else None,
        }

    prior_summary = db.get_dual_engine_backtest_summary(conn)
    prior_satellite_metrics = prior_summary["satellite_metrics"] if prior_summary and prior_summary.get("satellite_metrics") else {}
    kelly_cap = short_term_engine.kelly_fraction(
        prior_satellite_metrics.get("win_rate_pct"), prior_satellite_metrics.get("profit_factor"),
    )
    ccl_scale_today = _today_ccl_scale(conn)

    out = []
    skipped = {}
    for symbol, asset_type in universe:
        raw_df = fetch_ticker_history(symbol, asset_type, conn=conn)
        if len(raw_df) < SATELLITE_MIN_WARMUP_ROWS:
            skipped[symbol] = f"historial insuficiente (necesita >= {SATELLITE_MIN_WARMUP_ROWS} ruedas)"
            continue
        model_df, _ccl, _currency = build_model_input(conn, raw_df, asset_type)
        triggered = short_term_engine.evaluate_short_term_signals(model_df)
        if not triggered:
            continue

        # Filtro de riesgo preventivo del Agente NLP (ver conversacion,
        # portfolio/nlp_agent.py): earnings dentro de 48hs o sentimiento
        # extremo -- no se abre una posicion NUEVA en ESE ticker puntual hoy
        # (posiciones ya abiertas no se tocan, mismo criterio que
        # regime.satellite_blocked). Nunca bloquea el Satelite ENTERO, solo
        # el ticker con el evento detectado.
        if nlp_agent.satellite_entry_blocked_by_event(conn, symbol):
            skipped[symbol] = "evento de riesgo inminente (Agente NLP -- ver news_sentiment_log)"
            continue

        entry_usd = float(model_df["close"].iloc[-1])
        ccl_rate = _implied_ccl_rate(raw_df, model_df)
        atr_pct_usd = float(indicators.atr_pct(model_df, CORE_ATR_WINDOW).iloc[-1])
        stop_pct = min(exit_params["stop_loss_pct"], short_term_engine.SATELLITE_TRAILING_STOP_ATR_MULT * atr_pct_usd)
        weight = short_term_engine.compute_satellite_position_weight(atr_pct_usd, max_slot_weight)
        if kelly_cap is not None:
            weight = min(weight, kelly_cap)
        weight *= ccl_scale_today

        entry_price_usd, entry_price_ars = _dual_price(entry_usd, ccl_rate)
        take_profit_price_usd, take_profit_price_ars = _dual_price(
            entry_usd * (1 + exit_params["take_profit_pct"] / 100), ccl_rate
        )
        initial_stop_price_usd, initial_stop_price_ars = _dual_price(entry_usd * (1 - stop_pct / 100), ccl_rate)
        out.append({
            "symbol": symbol,
            "tactics": [t["tactic"] for t in triggered],
            "ccl_rate": round(ccl_rate, 2),
            "entry_price_usd": entry_price_usd, "entry_price_ars": entry_price_ars,
            "take_profit_price_usd": take_profit_price_usd, "take_profit_price_ars": take_profit_price_ars,
            "take_profit_pct": round(exit_params["take_profit_pct"], 2),
            "initial_stop_price_usd": initial_stop_price_usd, "initial_stop_price_ars": initial_stop_price_ars,
            "initial_stop_pct": round(stop_pct, 2),
            "max_hold_days": short_term_engine.SATELLITE_MAX_HOLD_DAYS,
            "position_weight": round(weight, 4),
            "atr_pct": round(atr_pct_usd, 2),
            "suggested_amount_ars": (
                round(capital_config.suggested_amount(account_value, allocation["satellite_pct"], weight), 2)
                if account_value else None
            ),
        })

    # Filtro de concentracion sectorial (ver conversacion, mismo criterio que
    # Core y que simulate_satellite_engine): entre las señales de HOY, si 2+
    # pertenecen al mismo grupo sectorial (portfolio/signals.py::CORE_SECTOR_GROUPS,
    # reusado tal cual -- la clasificacion es del universo, no de un motor en
    # particular) se descartan todas salvo la de mayor position_weight.
    out.sort(key=lambda s: s["position_weight"], reverse=True)
    seen_groups = set()
    deduped_out = []
    for s in out:
        group = signals.CORE_SECTOR_GROUPS.get(s["symbol"])
        if group is not None:
            if group in seen_groups:
                continue
            seen_groups.add(group)
        deduped_out.append(s)

    return {
        "signals": deduped_out, "skipped": skipped, "regime": current_regime,
        "satellite_capital_ars": round(satellite_capital, 2) if account_value else None,
    }


def _held_symbols(conn):
    """Simbolos con tenencia actual (cantidad > 0 implicito en get_holdings,
    que ya filtra posiciones cerradas) -- ver portfolio/pairs_trading.py,
    necesario para saber si la pata 'cara' de un par es una rotacion real
    (se tiene para vender) o solo informativa (no se tiene, no hay venta en
    corto real en esta app)."""
    try:
        return {h["instrument"]["symbol"] for h in db.get_holdings_with_quotes(conn)}
    except Exception:
        return set()


def compute_pairs_signals(conn, account_value=None):
    """Señales de Pairs Trading de HOY (ver portfolio/pairs_trading.py) --
    asignacion NOMINAL/sugerida (ver portfolio/config.py::get_pairs_allocation_pct),
    independiente del 100% Core+Satelite (este motor no tiene todavia un
    backtest propio validado, ver conversacion)."""
    allocation_pct = capital_config.get_pairs_allocation_pct(conn)
    capital = (account_value or 0) * allocation_pct / 100.0
    held = _held_symbols(conn)
    try:
        pair_signals = pairs_trading.scan_pairs(conn, held_symbols=held)
    except Exception:
        pair_signals = []  # sin red/datos -- nunca rompe el dispatcher unificado por este motor
    for s in pair_signals:
        s["suggested_amount_ars"] = (
            round(capital_config.suggested_amount(account_value, allocation_pct, 1.0), 2) if account_value else None
        )
    return {"signals": pair_signals, "capital_ars": round(capital, 2) if account_value else None}


def compute_factor_signals(conn, account_value=None):
    """Señales de rotacion de Factor Investing de HOY (ver
    portfolio/factor_investing.py) -- asignacion NOMINAL/sugerida (ver
    portfolio/config.py::get_factor_allocation_pct), dividida en partes
    iguales entre los tickers del Top N (sin un position_weight propio como
    Core/Satelite, ya que esto es una rotacion de cartera completa, no un
    trade individual)."""
    allocation_pct = capital_config.get_factor_allocation_pct(conn)
    capital = (account_value or 0) * allocation_pct / 100.0
    try:
        rotation = factor_investing.get_rotation_signals(conn)
    except Exception:
        rotation = []
    per_symbol_pct = allocation_pct / len(rotation) if rotation else 0.0
    for r in rotation:
        r["suggested_amount_ars"] = (
            round(capital_config.suggested_amount(account_value, per_symbol_pct, 1.0), 2) if account_value else None
        )
    return {"signals": rotation, "capital_ars": round(capital, 2) if account_value else None}


def compute_arbitrage_signals():
    """Señal de Arbitraje de Canje MEP/CCL de HOY (ver portfolio/arbitrage.py)
    -- a diferencia de Pairs/Factor, no tiene asignacion de capital propia
    (es una alerta informativa de un desvio puntual entre 2 pares de bonos,
    no un motor que gestiona una porcion de la cartera), por eso el dict
    devuelto no trae capital_ars/suggested_amount_ars."""
    try:
        signal = arbitrage.compute_canje_arbitrage_signal()
    except Exception:
        signal = None  # sin datos/red -- nunca rompe el dispatcher unificado por este modulo
    return {"signals": [signal] if signal else []}


def compute_synthetic_dollar_signals(conn):
    """Señal de Dólar Sintético/Futuros ROFEX de HOY (ver
    portfolio/synthetic_dollar.py) -- idem compute_arbitrage_signals, sin
    asignacion de capital propia (es una alerta de cobertura/tasa, no un
    motor de cartera)."""
    try:
        signal = synthetic_dollar.compute_synthetic_dollar_signal(conn)
    except Exception:
        signal = None
    return {"signals": [signal] if signal else []}


def compute_fixed_income_carry_signals(conn):
    """Rotacion LECAP/CER de HOY (ver
    portfolio/fixed_income.py::compute_carry_trade_rotation_signals) -- idem
    los 2 anteriores, sin asignacion de capital propia."""
    try:
        rotation = fixed_income.compute_carry_trade_rotation_signals(conn)
    except Exception:
        rotation = []
    return {"signals": rotation}


def compute_today_signals(conn, client, universe=UNIVERSE, account_value=None):
    """Punto de entrada unico para GET /api/signals y /signals de Telegram."""
    core = compute_core_signals(conn, client, universe=universe, account_value=account_value)
    satellite = compute_satellite_signals(conn, universe=universe, account_value=account_value)
    pairs = compute_pairs_signals(conn, account_value=account_value)
    factor = compute_factor_signals(conn, account_value=account_value)
    arbitrage_result = compute_arbitrage_signals()
    synthetic_dollar_result = compute_synthetic_dollar_signals(conn)
    fixed_income_result = compute_fixed_income_carry_signals(conn)

    # Notas del Agente NLP (ver conversacion, portfolio/nlp_agent.py) -- SOLO
    # para los tickers que de verdad aparecen en una señal de hoy (no todo el
    # universo: evita llamadas a yfinance/LLM para tickers sin señal, que ya
    # fue el Satelite el que los filtro si corresponde). Usa el cache diario
    # de assess_event_risk, asi que esto no pega a yfinance de nuevo si
    # compute_satellite_signals ya lo hizo mas arriba para ese ticker.
    signaled_symbols = {s["symbol"] for s in core["signals"]} | {s["symbol"] for s in satellite["signals"]}
    event_risk_notes = {}
    for symbol in signaled_symbols:
        assessment = nlp_agent.assess_event_risk(conn, symbol)
        note = nlp_agent.event_risk_note(assessment)
        if note:
            event_risk_notes[symbol] = note

    return {
        "core_signals": core["signals"],
        "satellite_signals": satellite["signals"],
        "pairs_signals": pairs["signals"],
        "factor_signals": factor["signals"],
        "arbitrage_signals": arbitrage_result["signals"],
        "synthetic_dollar_signals": synthetic_dollar_result["signals"],
        "fixed_income_signals": fixed_income_result["signals"],
        "spy_above_ema50": core["spy_above_ema50"],
        "market_regime": satellite["regime"],
        "core_capital_ars": core["core_capital_ars"],
        "satellite_capital_ars": satellite["satellite_capital_ars"],
        "pairs_capital_ars": pairs["capital_ars"],
        "factor_capital_ars": factor["capital_ars"],
        "skipped": {**core["skipped"], **satellite["skipped"]},
        "account_value_ars": account_value,
        "event_risk_notes": event_risk_notes,
    }
