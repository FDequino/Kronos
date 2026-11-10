"""Prima de Volatilidad en Opciones Cripto (ver conversación, "avanzá con el
paper trading de opciones en Deribit") -- vende un straddle ATM mensual
sobre BTC con cobertura delta vía futuro perpetuo, cosechando la prima de
volatilidad (IV implícita de Deribit sistemáticamente por encima de la
volatilidad realizada, ver investigación 2026-11-10 en OBJETIVO_MARCO.md).

ADVERTENCIA DECLARADA (ver OBJETIVO_MARCO.md, no omitir nunca en ningún lado
que use este módulo): el backtest con cobertura delta da DSR≈0.9999
(100% de aciertos en 97 períodos, 2021-2026 incluido el colapso de FTX) --
un número que NO se puede confiar de la misma forma que Carry cripto. La
simulación (Black-Scholes + rehedge "perfecto") estructuralmente NO puede
ver dos riesgos reales que son justo los que revientan posiciones cortas de
volatilidad: (1) riesgo de margen/liquidación forzada en un movimiento
violento antes de poder rehedgear, (2) riesgo de liquidez de la opción
misma (spread bid-ask disparado en una crisis real). Por eso esta estrategia
entra a Paper Trading pese a un DSR que técnicamente cruza el umbral --
mismo caso declarado que portfolio/arbitrage.py (Canje): el número del gate
no es suficiente acá, hace falta exponer el mecanismo a fricción real antes
de confiar en él. NUNCA debe promoverse a 'live' solo por el DSR -- requiere
além evidencia real de paper trading contra Deribit (test.deribit.com,
cuenta de prueba) viendo pasar al menos un evento de estrés real.

Fuentes REALES verificadas (sin auth, confirmado 2026-11-10):
  - DVOL (implied vol 30d de Deribit): GET /api/v2/public/
    get_volatility_index_data -- paginado con end_timestamp acotado (pedir
    un rango amplio con end_timestamp=ahora hace que la API devuelva solo
    los ultimos ~1000 registros, IGNORANDO start_timestamp -- hallazgo real,
    hay que paginar con end_timestamp acotado por chunk, nunca confiar en
    que start_timestamp solo alcance).
  - Precio spot/futuro de BTC: Binance Futures publico (fapi.binance.com,
    ya usado en portfolio/crypto_carry.py).

Granularidad HORARIA (no diaria) -- ver auditoria 2026-11-10: con cierres
diarios el backtest no veia el riesgo intradiario real de un vendedor de
opciones; con datos horarios el rehedge es mas realista (aunque sigue sin
poder ver saltos DENTRO de una hora, limitacion igual declarada)."""
import time

import numpy as np
import pandas as pd
import requests
from scipy.stats import norm

DVOL_URL = "https://www.deribit.com/api/v2/public/get_volatility_index_data"
FAPI_BASE = "https://fapi.binance.com"

ENTRY_FEE_PCT_OF_UNDERLYING = 0.03    # Deribit: 0.03% del subyacente por pata, tope 12.5% de la prima
ENTRY_FEE_CAP_PCT_OF_PREMIUM = 12.5
DELIVERY_FEE_PCT_OF_UNDERLYING = 0.015  # Deribit: 0.015% del subyacente al vencimiento, tope 12.5% del valor
DELIVERY_FEE_CAP_PCT_OF_VALUE = 12.5
FUTURES_HEDGE_FEE_PCT = 0.05  # taker futuros Binance, ya confirmado real en crypto_carry.py -- hedge via futuro perpetuo

HOURS_PER_PERIOD = 21 * 24  # ~21 ruedas habiles (~1 mes), mismo tenor que DVOL (30d)
DVOL_CHUNK_HOURS = 950      # bajo el tope de ~1000 filas/respuesta del endpoint


def fetch_dvol_history(currency="BTC", start=None):
    """Historial horario COMPLETO de DVOL (ver HONESTIDAD DECLARADA arriba
    sobre la paginacion) desde `start` (datetime) hasta ahora. start=None
    usa el lanzamiento real de DVOL (2021-03-24)."""
    import datetime as dt

    start = start or dt.datetime(2021, 3, 24, tzinfo=dt.timezone.utc)
    cursor = int(start.timestamp() * 1000)
    end_overall = int(time.time() * 1000)
    chunk_ms = DVOL_CHUNK_HOURS * 3600 * 1000
    all_rows = []
    while cursor < end_overall:
        chunk_end = min(cursor + chunk_ms, end_overall)
        resp = requests.get(DVOL_URL, params={
            "currency": currency, "start_timestamp": cursor, "end_timestamp": chunk_end, "resolution": "3600",
        }, timeout=20)
        resp.raise_for_status()
        rows = resp.json()["result"]["data"]
        if rows:
            all_rows.extend(rows)
        cursor = chunk_end + 1
        time.sleep(0.05)
    if not all_rows:
        return pd.Series(dtype=float)
    df = pd.DataFrame(all_rows, columns=["ts", "open", "high", "low", "close"])
    df["dt"] = pd.to_datetime(df["ts"], unit="ms")
    return df.groupby("dt")["close"].last().rename("dvol")


def fetch_btc_hourly_closes(start=None):
    import datetime as dt

    start = start or dt.datetime(2021, 3, 24, tzinfo=dt.timezone.utc)
    cursor = int(start.timestamp() * 1000)
    end_overall = int(time.time() * 1000)
    all_rows = []
    while cursor < end_overall:
        resp = requests.get(f"{FAPI_BASE}/fapi/v1/klines",
                             params={"symbol": "BTCUSDT", "interval": "1h", "startTime": cursor, "limit": 1000},
                             timeout=20)
        resp.raise_for_status()
        rows = resp.json()
        if not rows:
            break
        all_rows.extend(rows)
        cursor = rows[-1][0] + 1
        if len(rows) < 1000:
            break
        time.sleep(0.05)
    if not all_rows:
        return pd.Series(dtype=float)
    df = pd.DataFrame(all_rows, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "qv", "n", "tbv", "tqv", "ignore",
    ])
    df["dt"] = pd.to_datetime(df["open_time"], unit="ms")
    df["close"] = df["close"].astype(float)
    return df.groupby("dt")["close"].last().rename("spot")


def bs_price_delta(S, K, T, sigma, option_type):
    if T <= 1e-9 or sigma <= 0:
        intrinsic = max(0.0, S - K) if option_type == "call" else max(0.0, K - S)
        delta = (1.0 if S > K else 0.0) if option_type == "call" else (-1.0 if S < K else 0.0)
        return intrinsic, delta
    d1 = (np.log(S / K) + 0.5 * sigma ** 2 * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    if option_type == "call":
        return S * norm.cdf(d1) - K * norm.cdf(d2), norm.cdf(d1)
    return K * norm.cdf(-d2) - S * norm.cdf(-d1), norm.cdf(d1) - 1.0


def _entry_fee(S, premium):
    return min(ENTRY_FEE_PCT_OF_UNDERLYING / 100 * S, ENTRY_FEE_CAP_PCT_OF_PREMIUM / 100 * premium)


def _delivery_fee(S, payout):
    if payout <= 0:
        return 0.0
    return min(DELIVERY_FEE_PCT_OF_UNDERLYING / 100 * S, DELIVERY_FEE_CAP_PCT_OF_VALUE / 100 * payout)


def simulate_crypto_options_vrp_engine(conn=None, hours_per_period=HOURS_PER_PERIOD):
    """Backtest/paper-trading del straddle ATM delta-hedgeado -- MISMO shape
    de salida que el resto de los motores del proyecto (ver
    crypto_carry.py::simulate_funding_rate_carry_engine). `conn` no se usa
    (sin estado persistido, ver docstring del modulo: recompute completo
    cada corrida, mismo patron que crypto_carry.py con el historial de
    funding). Re-corrido cada dia via portfolio/paper_ledger.py, la serie
    se extiende sola con los dias/horas nuevas reales."""
    dvol = fetch_dvol_history()
    spot = fetch_btc_hourly_closes()
    if dvol.empty or spot.empty:
        empty = pd.Series(dtype=float)
        return {"engine": "crypto_options_vrp", "daily_returns_pct": empty, "idle_weight": empty, "trades": []}

    df = pd.DataFrame({"dvol": dvol, "spot": spot}).dropna()
    idx = df.index
    if len(idx) < hours_per_period * 2:
        empty = pd.Series(dtype=float)
        return {"engine": "crypto_options_vrp", "daily_returns_pct": empty, "idle_weight": empty, "trades": []}

    trades = []
    hourly_pnl_pct = pd.Series(0.0, index=idx)

    for i in range(0, len(idx) - hours_per_period, hours_per_period):
        entry_i = i
        exit_i = i + hours_per_period
        period_idx = idx[entry_i:exit_i + 1]
        S0 = float(df["spot"].iloc[entry_i])
        K = S0
        T_total_years = (period_idx[-1] - period_idx[0]).total_seconds() / (365 * 24 * 3600)

        iv0 = float(df["dvol"].iloc[entry_i]) / 100.0
        call0, _ = bs_price_delta(S0, K, T_total_years, iv0, "call")
        put0, _ = bs_price_delta(S0, K, T_total_years, iv0, "put")
        premium_received = call0 + put0
        fees_total = _entry_fee(S0, call0) + _entry_fee(S0, put0)

        option_value_prev = premium_received
        hedge_shares_prev = 0.0
        period_pnl_usd = premium_received - fees_total

        for j in range(1, len(period_idx)):
            t = period_idx[j]
            S_t = float(df["spot"].loc[t])
            S_prev = float(df["spot"].loc[period_idx[j - 1]])
            elapsed_years = (t - period_idx[0]).total_seconds() / (365 * 24 * 3600)
            T_remaining = max(0.0, T_total_years - elapsed_years)
            iv_t = float(df["dvol"].loc[t]) / 100.0 if T_remaining > 0 else iv0

            call_t, delta_call_t = bs_price_delta(S_t, K, T_remaining, iv_t, "call")
            put_t, delta_put_t = bs_price_delta(S_t, K, T_remaining, iv_t, "put")
            option_value_t = call_t + put_t
            combined_delta = delta_call_t + delta_put_t

            hedge_pnl = hedge_shares_prev * (S_t - S_prev)
            option_pnl = option_value_prev - option_value_t
            hedge_cost = abs(combined_delta - hedge_shares_prev) * S_t * FUTURES_HEDGE_FEE_PCT / 100

            hour_pnl_usd = hedge_pnl + option_pnl - hedge_cost
            hourly_pnl_pct.loc[t] += hour_pnl_usd / S0 * 100
            period_pnl_usd += hour_pnl_usd

            hedge_shares_prev = combined_delta
            option_value_prev = option_value_t

        final_S = float(df["spot"].loc[period_idx[-1]])
        call_T, _ = bs_price_delta(final_S, K, 0.0, iv0, "call")
        put_T, _ = bs_price_delta(final_S, K, 0.0, iv0, "put")
        delivery_cost = _delivery_fee(S0, call_T) + _delivery_fee(S0, put_T)
        hourly_pnl_pct.loc[period_idx[-1]] -= delivery_cost / S0 * 100
        period_pnl_usd -= delivery_cost

        trades.append({
            "engine": "crypto_options_vrp",
            "entry_date": str(period_idx[0].date()), "exit_date": str(period_idx[-1].date()),
            "S0": round(S0, 1), "ST": round(final_S, 1), "iv_entry_pct": round(iv0 * 100, 1),
            "return_pct": round(period_pnl_usd / S0 * 100, 3),
        })

    daily_pnl_pct = hourly_pnl_pct.groupby(hourly_pnl_pct.index.normalize()).sum()
    idle_weight = pd.Series(0.0, index=daily_pnl_pct.index)
    return {"engine": "crypto_options_vrp", "daily_returns_pct": daily_pnl_pct, "idle_weight": idle_weight, "trades": trades}
