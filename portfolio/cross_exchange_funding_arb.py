"""Arbitraje de Funding Rate CROSS-EXCHANGE (ver conversacion, "que otras
estrategias podemos usar ademas de las que tenemos" -- candidata nueva real,
no una variante de ajuste de las existentes): en vez de comprar SPOT + vender
FUTURO del MISMO exchange (ver portfolio/crypto_carry.py), esto abre DOS
patas de futuro perpetuo en DOS EXCHANGES DISTINTOS para el mismo simbolo --
CORTO en el exchange con funding mas alto (cobra ese funding) + LARGO en el
exchange con funding mas bajo (paga menos, o cobra si es negativo). Exposicion
de precio NETA CERO igual que crypto_carry.py (ambas patas son el mismo
activo, misma direccion de hedge), pero sin necesitar la pata SPOT -- dos
futuros alcanzan. El retorno es la DIFERENCIA de funding entre exchanges, no
el funding absoluto de uno solo.

FUENTES REALES VERIFICADAS (ver conversacion, nunca fabricar un endpoint):
  - Binance: reusa crypto_carry.py::fetch_funding_rate_history tal cual (ya
    confirmado, GET /fapi/v1/fundingRate, sin auth).
  - Bybit: GET https://api.bybit.com/v5/market/funding/history (sin auth,
    confirmado real 2026-11-10, category=linear) -- max 200 filas/pagina,
    paginado hacia atras con el parametro `endTime` (confirmado empiricamente:
    pasar endTime=<timestamp mas viejo visto> devuelve la pagina anterior).
  - OKX: GET https://www.okx.com/api/v5/public/funding-rate-history (sin
    auth, confirmado real 2026-11-10, instId=BTC-USDT-SWAP/ETH-USDT-SWAP) --
    max 100 filas/pagina, paginado hacia atras con el parametro `after`
    (CONTRAINTUITIVO: no es "before", es "after=<fundingTime mas viejo visto>"
    el que devuelve la pagina mas vieja -- verificado con una llamada real,
    `before` no avanza el cursor como se esperaria).

Comisiones REALES (consultadas 2026-11-10, tramo regular sin VIP/descuento,
mismo criterio "peor caso" que crypto_carry.py):
  Binance futuros USDS-M taker: 0.05%
  Bybit  futuros lineales taker: 0.055%
  OKX    futuros perpetuos taker: 0.05%
Costo de abrir O cerrar LAS 2 PATAS juntas (una vez, mismo criterio que
crypto_carry.py::CARRY_ROUND_LEG_COST_PCT) = suma de los 2 takers del par de
exchanges elegido -- varia segun el par (Binance+Bybit != Binance+OKX)."""
import time

import pandas as pd
import requests

from portfolio.crypto_carry import _aggregate_8h_to_daily
from portfolio.crypto_carry import fetch_funding_rate_history as fetch_binance_funding_history

BYBIT_BASE = "https://api.bybit.com"
OKX_BASE = "https://www.okx.com"

# Taker fee por exchange (ver docstring del modulo) -- futuros perpetuos,
# tramo regular.
TAKER_FEE_PCT = {"binance": 0.05, "bybit": 0.055, "okx": 0.05}

# Simbolo generico -> instId/symbol especifico de cada exchange (BTCUSDT es
# el mismo en Binance/Bybit, OKX usa su propio formato con guiones).
_OKX_INST_ID = {"BTCUSDT": "BTC-USDT-SWAP", "ETHUSDT": "ETH-USDT-SWAP"}

_FUNDING_PERIODS_PER_YEAR = 365 * 3  # 3 liquidaciones de funding cada 8hs, mismo criterio que crypto_carry.py


def fetch_bybit_funding_history(symbol, max_pages=50):
    """Historial COMPLETO de funding rate de un simbolo en Bybit (linear
    perpetual), paginado hacia atras con `endTime` (ver docstring del
    modulo). max_pages*200 filas = freno de seguridad, igual criterio que
    crypto_carry.py."""
    all_rows = []
    cursor = None
    for _ in range(max_pages):
        params = {"category": "linear", "symbol": symbol, "limit": 200}
        if cursor is not None:
            params["endTime"] = cursor
        resp = requests.get(f"{BYBIT_BASE}/v5/market/funding/history", params=params, timeout=15)
        resp.raise_for_status()
        rows = resp.json()["result"]["list"]
        if not rows:
            break
        all_rows.extend(rows)
        oldest_ts = int(rows[-1]["fundingRateTimestamp"])
        if cursor is not None and oldest_ts >= cursor:
            break  # no avanzo mas (fin real del historial) -- corta en vez de loopear infinito
        cursor = oldest_ts
        if len(rows) < 200:
            break
        time.sleep(0.15)
    if not all_rows:
        return None
    df = pd.DataFrame(all_rows)
    df["fundingTime"] = pd.to_datetime(df["fundingRateTimestamp"].astype(int), unit="ms")
    df["fundingRate"] = df["fundingRate"].astype(float) * 100
    df = df[["fundingTime", "fundingRate"]].drop_duplicates(subset="fundingTime")
    return df.sort_values("fundingTime").reset_index(drop=True)


def fetch_okx_funding_history(symbol, max_pages=80):
    """Historial COMPLETO de funding rate de un simbolo en OKX (perpetual
    swap), paginado hacia atras con `after` (ver docstring del modulo --
    contraintuitivo, verificado real). max_pages*100 filas = freno de
    seguridad."""
    inst_id = _OKX_INST_ID.get(symbol)
    if inst_id is None:
        return None
    all_rows = []
    cursor = None
    for _ in range(max_pages):
        params = {"instId": inst_id, "limit": 100}
        if cursor is not None:
            params["after"] = cursor
        resp = requests.get(f"{OKX_BASE}/api/v5/public/funding-rate-history", params=params, timeout=15)
        resp.raise_for_status()
        rows = resp.json()["data"]
        if not rows:
            break
        all_rows.extend(rows)
        oldest_ts = int(rows[-1]["fundingTime"])
        if cursor is not None and oldest_ts >= cursor:
            break
        cursor = oldest_ts
        if len(rows) < 100:
            break
        time.sleep(0.15)
    if not all_rows:
        return None
    df = pd.DataFrame(all_rows)
    df["fundingTime"] = pd.to_datetime(df["fundingTime"].astype(int), unit="ms")
    df["fundingRate"] = df["fundingRate"].astype(float) * 100
    df = df[["fundingTime", "fundingRate"]].drop_duplicates(subset="fundingTime")
    return df.sort_values("fundingTime").reset_index(drop=True)


_FETCHERS = {"binance": fetch_binance_funding_history, "bybit": fetch_bybit_funding_history, "okx": fetch_okx_funding_history}


def simulate_cross_exchange_funding_arb_engine(symbol, exchange_a, exchange_b, lookback_days=None, rebalance_days=30):
    """Backtest historico del par (exchange_a, exchange_b) para `symbol` --
    mismo shape de salida que el resto de los motores del proyecto (ver
    crypto_carry.py::simulate_funding_rate_carry_engine), mismo criterio de
    rebalanceo periodico (gestion de margen real, no salida por señal):
    en cada `rebalance_days`, se elige CORTO el exchange con funding MAS ALTO
    ese dia y LARGO el de funding MAS BAJO (fijo durante todo el tramo, sin
    re-evaluar dia a dia -- mismo supuesto conservador que crypto_carry.py,
    nunca asume que el bot reacciona intra-tramo). Retorno de cada tramo =
    SUMA de (funding_exchange_corto - funding_exchange_largo) en cada
    liquidacion de ESE tramo, menos el costo de abrir/cerrar las 2 patas.

    Alineacion de fechas: INNER JOIN por fundingTime exacto (ambos exchanges
    liquidan funding en la misma grilla UTC 00/08/16hs, confirmado real) --
    una liquidacion sin match en el otro exchange se descarta entera (nunca
    se inventa un funding faltante)."""
    df_a = _FETCHERS[exchange_a](symbol)
    df_b = _FETCHERS[exchange_b](symbol)
    if df_a is None or df_b is None or df_a.empty or df_b.empty:
        empty = pd.Series(dtype=float)
        return {"engine": "cross_exchange_funding_arb", "daily_returns_pct": empty, "idle_weight": empty, "trades": []}

    # Redondeo a minuto ANTES de mergear (hallazgo real, verificado 2026-11-10):
    # Binance reporta ~43% de sus fundingTime con 1-2ms de jitter sobre la
    # hora exacta (ej. 16:00:00.002 en vez de 16:00:00) -- un merge exacto sin
    # esto pierde silenciosamente la mitad del historial superpuesto (3934 de
    # ~7340 filas esperadas en el par Binance/Bybit, verificado). Bybit/OKX no
    # tienen este jitter, pero redondearlos tambien es inofensivo (ya caen
    # justo en la hora).
    df_a = df_a.assign(fundingTime=df_a["fundingTime"].dt.round("1min"))
    df_b = df_b.assign(fundingTime=df_b["fundingTime"].dt.round("1min"))
    merged = pd.merge(df_a, df_b, on="fundingTime", suffixes=(f"_{exchange_a}", f"_{exchange_b}")).sort_values("fundingTime").reset_index(drop=True)
    if lookback_days is not None and not merged.empty:
        cutoff = merged["fundingTime"].max() - pd.Timedelta(days=lookback_days)
        merged = merged[merged["fundingTime"] >= cutoff].reset_index(drop=True)
    if merged.empty:
        empty = pd.Series(dtype=float)
        return {"engine": "cross_exchange_funding_arb", "daily_returns_pct": empty, "idle_weight": empty, "trades": []}

    rate_a = merged[f"fundingRate_{exchange_a}"]
    rate_b = merged[f"fundingRate_{exchange_b}"]
    round_leg_cost_pct = TAKER_FEE_PCT[exchange_a] + TAKER_FEE_PCT[exchange_b]

    periods_per_rebalance = rebalance_days * 3
    net = pd.Series(0.0, index=merged.index)
    trades = []
    for start in range(0, len(merged), periods_per_rebalance):
        end = min(start + periods_per_rebalance, len(merged))
        # Direccion fija al INICIO del tramo: corto el exchange con funding
        # mas alto ESE dia (cobra mas / paga menos), largo el otro.
        short_is_a = float(rate_a.iloc[start]) >= float(rate_b.iloc[start])
        spread = (rate_a - rate_b) if short_is_a else (rate_b - rate_a)
        tramo = spread.iloc[start:end].copy()
        tramo.iloc[0] -= round_leg_cost_pct
        net.iloc[start:end] = tramo.to_numpy()
        trades.append({
            "engine": "cross_exchange_funding_arb", "symbol": symbol,
            "entry_date": merged["fundingTime"].iloc[start].date().isoformat(),
            "exit_date": merged["fundingTime"].iloc[end - 1].date().isoformat(),
            "hold_days": rebalance_days if end - start == periods_per_rebalance else round((end - start) / 3, 1),
            "short_exchange": exchange_a if short_is_a else exchange_b,
            "long_exchange": exchange_b if short_is_a else exchange_a,
            "return_pct": round(float(tramo.sum()), 4),
            "exit_reason": "rebalanceo_periodico",
        })

    # Reusa _aggregate_8h_to_daily tal cual (ver crypto_carry.py) -- misma
    # convencion de unidades (puntos porcentuales, compuesto correctamente
    # dentro de cada dia) que el resto de los motores, nunca reimplementada.
    net_df = pd.DataFrame({"fundingTime": merged["fundingTime"], "fundingRate": net})
    daily_returns = _aggregate_8h_to_daily(net_df)
    idle_weight = pd.Series(0.0, index=daily_returns.index)
    return {"engine": "cross_exchange_funding_arb", "daily_returns_pct": daily_returns, "idle_weight": idle_weight, "trades": trades}
