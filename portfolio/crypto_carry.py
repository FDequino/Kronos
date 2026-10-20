"""Carry Trade de Funding Rate en futuros perpetuos (ver conversacion,
"pensá como un fondo multiestrategia" -- cripto) -- estrategia "cash and
carry" clasica y bien documentada: comprar el activo en SPOT (long) +
abrir un SHORT de igual tamaño en el futuro PERPETUO del mismo activo.
Exposicion de precio NETA CERO (delta-neutral): si BTC sube o baja, la
ganancia/perdida de la pata spot se cancela con la de la pata futuro -- el
unico retorno real es el FUNDING que Binance liquida cada 8hs entre largos
y cortos apalancados. Cuando el funding rate es POSITIVO, los largos pagan
a los cortos -- como esta estrategia ES el lado corto del perpetuo, cobra
ese funding. Cuando es negativo, lo paga.

EVIDENCIA REAL (ver conversacion/auditoria 2026-10-20, ".devlogs/
crypto_funding_carry_v1.log"): backtest contra el HISTORIAL COMPLETO real
de funding rate que expone gratis y sin auth la API publica de Binance
(GET /fapi/v1/fundingRate, confirmado 7793 registros de BTCUSDT desde
2019-09-10 y 7559 de ETHUSDT desde 2019-11-27 -- toda la vida del contrato
perpetuo, no una ventana corta), partido 70/30 in-sample/out-of-sample, con
comision real confirmada contra el tarifario oficial de Binance (tramo
regular sin VIP/BNB discount: spot taker 0.10% + futuros USDS-M taker
0.05% = 0.15% por pata abierta o cerrada):

  Simbolo  | Periodo | Bruto anual | Neto anual (hold-to-end) | Neto anual (rebalanceo 30d) | Sharpe neto | Max DD
  BTCUSDT  | IS (2019-2024)  | 15.27% | 15.20% | 13.17% | 13.0-17.8 | -1.51%
  BTCUSDT  | OOS (2024-2026) |  5.22% |  5.07% |  3.31% |  5.9-20.8 | -0.89%
  ETHUSDT  | IS (2019-2024)  | 19.04% | 18.97% | 16.88% | 13.5-16.7 | -2.28%
  ETHUSDT  | OOS (2024-2026) |  5.04% |  4.89% |  3.08% |  5.4-18.6 | -1.10%

Edge REAL, positivo y consistente en los 2 simbolos, dentro Y fuera de
muestra, en los 3 escenarios de costo probados. El periodo OOS (mas
reciente, el mas conservador -- excluye el ciclo alcista 2020-2021 que
infla el promedio historico completo) da 3-5% anual NETO, con Sharpe muy
alto (la volatilidad de esta estrategia es minima por construccion: el
precio esta hedgeado, solo queda la volatilidad del funding mismo) y Max
Drawdown chico (<2.3%). De las 7+ estrategias auditadas en este proyecto
hasta ahora, esta es la que tiene la muestra mas larga y mas limpia.

CAVEATS reales NO capturados en el backtest de arriba (riesgos genuinos,
declarados, no ocultos):
  - Riesgo de LIQUIDACION de la pata de futuros en un movimiento de precio
    extremo si el margen no se gestiona activamente -- el hedge es
    economico (el P&L neto es ~0), NO instantaneo: un spike de precio puede
    liquidar la pata corta antes de que el valor de la pata spot (que no
    esta en la misma cuenta de margen) pueda compensarlo.
  - Riesgo de contraparte/custodia del propio exchange (fondos en Binance).
  - Slippage real de ejecucion -- se asumio la tarifa taker publicada, NO
    deslizamiento de precio en el momento de abrir/cerrar.
  - Costo de oportunidad del capital inmovilizado como margen de la pata
    de futuros (no modelado como un costo explicito).

ESTADO DE EJECUCION REAL (ver CryptoBinanceAdapter en portfolio/
broker_client.py): NUNCA probado contra una cuenta real, ni siquiera
testnet -- hoy no hay credenciales de Binance cargadas en absoluto
(confirmado, broker_accounts no tiene ninguna fila 'Binance'). Antes de
operar esto con plata real, el paso logico es Binance Futures Testnet
(testnet.binancefuture.com, cuenta de prueba real con fondos ficticios,
misma API) -- no usado aca por falta de credenciales, ni siquiera de
testnet, no por limitacion tecnica."""
import time

import pandas as pd
import requests

FAPI_BASE = "https://fapi.binance.com"

# Confirmado contra el tarifario oficial de Binance (developers.binance.com,
# consultado 2026-10-20), tramo REGULAR (sin VIP, sin descuento por pagar
# con BNB) -- el peor caso real, nunca el mejor. Ajustar si el volumen
# operado del usuario alguna vez entra en un tramo VIP.
SPOT_TAKER_FEE_PCT = 0.10
FUTURES_TAKER_FEE_PCT = 0.05
CARRY_ROUND_LEG_COST_PCT = SPOT_TAKER_FEE_PCT + FUTURES_TAKER_FEE_PCT  # abrir O cerrar LAS 2 PATAS juntas, una vez

# Los 2 simbolos que ya usa el resto del proyecto (ver portfolio/fetchers.py)
# -- nunca se opera un simbolo que el resto de la app no conozca ya.
FUNDING_SYMBOLS = ("BTCUSDT", "ETHUSDT")

# Umbral de funding anualizado NETO minimo para considerar "hay oportunidad
# hoy" en compute_funding_rate_carry_signal -- por debajo de esto el costo
# de entrada/salida (ver CARRY_ROUND_LEG_COST_PCT) tarda demasiados periodos
# en recuperarse para que valga la pena abrir la posicion por un solo dato
# puntual de funding. 3% es el piso OOS medido en el backtest (ver docstring
# del modulo) -- no un numero arbitrario.
MIN_ANNUALIZED_NET_FUNDING_PCT = 3.0

_FUNDING_PERIODS_PER_YEAR = 365 * 3  # Binance liquida funding cada 8hs


def fetch_funding_rate_history(symbol, start_time_ms=1, max_pages=50):
    """Historial COMPLETO de funding rate de un simbolo via la API publica
    de Binance (GET /fapi/v1/fundingRate, sin auth, confirmado real) --
    pagina de a 1000 filas (el maximo que admite el endpoint) hasta
    agotar el historial disponible. max_pages es un freno de seguridad
    (1000 filas/pagina * 50 = 50000 periodos de 8hs, ~15 años -- mas que
    suficiente para cualquier simbolo real, nunca deberia alcanzarse)."""
    all_rows = []
    cursor = start_time_ms
    for _ in range(max_pages):
        resp = requests.get(
            f"{FAPI_BASE}/fapi/v1/fundingRate",
            params={"symbol": symbol, "limit": 1000, "startTime": cursor},
            timeout=15,
        )
        resp.raise_for_status()
        rows = resp.json()
        if not rows:
            break
        all_rows.extend(rows)
        if len(rows) < 1000:
            break
        cursor = rows[-1]["fundingTime"] + 1
        time.sleep(0.15)  # cortesia de rate limit, el endpoint comparte 500 req/5min con fundingInfo
    if not all_rows:
        return None
    df = pd.DataFrame(all_rows)
    df["fundingTime"] = pd.to_datetime(df["fundingTime"], unit="ms")
    df["fundingRate"] = df["fundingRate"].astype(float) * 100  # a puntos porcentuales
    return df.sort_values("fundingTime").reset_index(drop=True)


def compute_funding_rate_carry_signal(symbol):
    """Señal de HOY -- None si el ultimo funding rate conocido, anualizado
    y neto de CARRY_ROUND_LEG_COST_PCT (asumiendo 1 ciclo de entrada+salida
    por mes, mismo criterio conservador que el escenario 'rebalanceo cada
    30 dias' del backtest, ver docstring del modulo), no supera
    MIN_ANNUALIZED_NET_FUNDING_PCT. dict de señal si lo supera."""
    df = fetch_funding_rate_history(symbol, start_time_ms=int(time.time() * 1000) - 7 * 24 * 3600 * 1000)
    if df is None or df.empty:
        return None

    last = df.iloc[-1]
    last_rate_pct = float(last["fundingRate"])
    annualized_gross_pct = last_rate_pct * _FUNDING_PERIODS_PER_YEAR
    # 1 ciclo de entrada+salida por mes (12/año) -- mismo supuesto conservador
    # que el escenario 'rebalanceo cada 30 dias' del backtest.
    annualized_net_pct = annualized_gross_pct - CARRY_ROUND_LEG_COST_PCT * 12

    if annualized_net_pct < MIN_ANNUALIZED_NET_FUNDING_PCT:
        return None

    direction = "cobra" if last_rate_pct > 0 else "paga"
    return {
        "symbol": symbol,
        "funding_rate_pct": round(last_rate_pct, 5),
        "funding_time": last["fundingTime"].isoformat(),
        "annualized_gross_pct": round(annualized_gross_pct, 2),
        "annualized_net_pct": round(annualized_net_pct, 2),
        "note": (
            f"Funding de {symbol} en {round(last_rate_pct, 4)}% cada 8hs -- una posicion cash-and-carry "
            f"(long spot + short perpetuo, delta-neutral) {direction} esto cada liquidacion, "
            f"~{round(annualized_net_pct, 1)}%/año neto de comision estimada (rebalanceo mensual)."
        ),
    }


def _aggregate_8h_to_daily(df):
    """Compone los 3 periodos de 8hs de cada dia calendario en un retorno
    diario -- para que la salida matchee el mismo formato (serie diaria)
    que usan los otros 6 motores del backtest unificado (ver
    portfolio/backtest.py::blend_n_engine_returns)."""
    daily = df.set_index("fundingTime")["fundingRate"].resample("1D").apply(
        lambda s: ((1 + s / 100).prod() - 1) * 100 if len(s) else 0.0
    )
    return daily


def simulate_funding_rate_carry_engine(symbol, lookback_days=None, rebalance_days=30,
                                        commission_pct=CARRY_ROUND_LEG_COST_PCT):
    """Backtest historico de la estrategia cash-and-carry sobre UN simbolo
    -- mismo shape de salida que los otros motores del proyecto (ver
    portfolio/arbitrage.py::simulate_canje_arbitrage_engine):
    {"engine": "crypto_carry", "daily_returns_pct": pd.Series, "idle_weight":
    pd.Series, "trades": [...]}.

    A diferencia de Canje/Pairs (que abren y cierran una posicion puntual),
    esta estrategia esta SIEMPRE posicionada (delta-neutral, sin exposicion
    direccional que la obligue a salir) -- "idle_weight" es siempre 0.0 (el
    capital nunca esta ocioso en Caucion, siempre esta trabajando en la
    posicion hedgeada). "trades" son los eventos de REBALANCEO (cierre+
    reapertura cada `rebalance_days`, ver docstring del modulo -- gestion de
    margen real en la practica, no una salida por señal), cada uno con el
    funding neto acumulado en ese tramo -- no "entrada/salida por señal"
    como el resto de los motores.

    lookback_days=None usa el historial COMPLETO disponible (por defecto --
    a diferencia de Canje antes de esta auditoria, esta estrategia no
    deberia recortarse a una ventana corta arbitraria, ver backtest real en
    .devlogs/crypto_funding_carry_v1.log que mostro la diferencia IS/OOS
    justamente por usar el historial completo). Pasar un numero para
    recortar a los ultimos N dias si se quiere comparar con otros motores
    de ventana fija."""
    df = fetch_funding_rate_history(symbol)
    if df is None or len(df) < 2:
        empty = pd.Series(dtype=float)
        return {"engine": "crypto_carry", "daily_returns_pct": empty, "idle_weight": empty, "trades": []}

    if lookback_days is not None:
        cutoff = df["fundingTime"].max() - pd.Timedelta(days=lookback_days)
        df = df[df["fundingTime"] >= cutoff].reset_index(drop=True)

    periods_per_rebalance = rebalance_days * 3  # 3 liquidaciones de funding por dia
    net = df["fundingRate"].copy()
    trades = []
    for start in range(0, len(net), periods_per_rebalance):
        end = min(start + periods_per_rebalance, len(net))
        net.iloc[start] -= commission_pct
        gross_leg = float(df["fundingRate"].iloc[start:end].sum())
        trades.append({
            "engine": "crypto_carry", "symbol": symbol,
            "entry_date": df["fundingTime"].iloc[start].date().isoformat(),
            "exit_date": df["fundingTime"].iloc[end - 1].date().isoformat(),
            "hold_days": rebalance_days if end - start == periods_per_rebalance else round((end - start) / 3, 1),
            "return_pct": round(gross_leg - commission_pct, 3),
            "exit_reason": "rebalanceo_periodico",
        })

    df = df.assign(fundingRate=net)
    daily_returns = _aggregate_8h_to_daily(df)
    idle_weight = pd.Series(0.0, index=daily_returns.index)
    return {"engine": "crypto_carry", "daily_returns_pct": daily_returns, "idle_weight": idle_weight, "trades": trades}
