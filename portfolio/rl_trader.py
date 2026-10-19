"""Entrenamiento PPO (Proximal Policy Optimization, stable-baselines3) sobre
RLCapitalAllocationEnv (ver portfolio/rl_env.py) -- el agente aprende una
politica de asignacion Core/Satelite/Caucion DINAMICA, entrenada IN-SAMPLE
(200r, ver portfolio/backtest.py::IN_SAMPLE_DAYS) y evaluada OUT-OF-SAMPLE
(100r, ver OUT_OF_SAMPLE_DAYS) contra la regla ESTATICA vigente (ver
portfolio/config.py::get_capital_allocation).

Alcance DECLARADO ("liviano"/exploratorio, mismo criterio que
finetune/finetune_chronos.py en esta misma sesion): TOTAL_TIMESTEPS modesto,
SIN busqueda de hiperparametros -- esto demuestra el pipeline funcionando de
punta a punta con una politica real entrenada, no una politica llevada a
convergencia ni comparada contra una referencia ya optimizada.

Corre standalone: `python -m portfolio.rl_trader` desde la raiz del repo
(con el predictor de services/predictor/ levantado en :8001 o
PREDICTOR_SERVICE_URL apuntando donde corresponda -- necesita Chronos-Bolt
real para Core y para las features de mercado del estado)."""
import sys
import time

import numpy as np
import pandas as pd
from stable_baselines3 import PPO
from stable_baselines3.common.vec_env import DummyVecEnv

from model.client import ChronosServiceClient
from portfolio import backtest, db, features, indicators, macro_data, yield_engine
from portfolio import config as capital_config
from portfolio.forecasting import build_model_input
from portfolio.rl_env import OBS_MARKET_COLUMNS, RLCapitalAllocationEnv

# Ticker representativo para las features de "mercado" del estado (ver
# portfolio/rl_env.py docstring, Espacio de Observacion) -- SPY: liquido,
# historial largo, ya usado como proxy de tendencia global en el resto del
# proyecto (portfolio/regime.py, portfolio/macro_data.py::get_global_trend_series).
# Declarado: NO es "todo el universo Core" (eso seria mucho mas caro de
# precomputar), es un proxy de contexto de mercado para el agente.
MARKET_PROXY_SYMBOL = "SPY"
MARKET_PROXY_ASSET_TYPE = "cedear"

TOTAL_TIMESTEPS = 5000  # declarado -- liviano/exploratorio, NO convergencia ni busqueda de hiperparametros


def build_market_observation_table(conn, client, dates):
    """DataFrame indexado 0..n-1 (MISMO orden que `dates`) con las 5 columnas
    de mercado de OBS_MARKET_COLUMNS -- chronos_p50/dispersion via anchors
    PRECOMPUTADOS sobre MARKET_PROXY_SYMBOL (mismo patron de anchors que
    portfolio/backtest.py::build_anchor_dataset: LOOKBACK/HORIZON_DAYS/
    ANCHOR_SPACING_DAYS reusados tal cual, NUNCA redefinidos con otro valor),
    forward-filled entre anchors (Chronos no corre todos los dias) -- y
    vix/brecha/atr diarios reales, alineados con portfolio/features.py::
    _align_ffill (mismo criterio causal del resto del proyecto: ultimo valor
    YA conocido a esa fecha, nunca interpolado hacia el futuro).

    dates: DatetimeIndex -- las fechas EXACTAS de core_result/satellite_result
    (ver run_rl_oos_validation), para que el estado del agente y los retornos
    de los sleeves esten perfectamente alineados dia a dia."""
    raw_df = backtest.fetch_ticker_history(MARKET_PROXY_SYMBOL, MARKET_PROXY_ASSET_TYPE, conn=conn)
    raw_df = raw_df.reset_index(drop=True)
    model_df, _ccl, _currency = build_model_input(conn, raw_df, MARKET_PROXY_ASSET_TYPE)
    close = model_df["close"].reset_index(drop=True)
    raw_dates = pd.DatetimeIndex(raw_df["timestamps"]).normalize()
    atr_series = indicators.atr_pct(model_df, backtest.CORE_ATR_WINDOW).reset_index(drop=True)

    full_index = pd.DatetimeIndex(dates).normalize()

    # Anchors de Chronos sobre el proxy -- UNA corrida por anchor (mismo
    # costo/criterio que Core en el resto del proyecto), nunca por dia.
    first_anchor = max(backtest.LOOKBACK - 1, backtest.WARMUP_ROWS)
    last_anchor = len(close) - 1
    chronos_p50 = {}
    chronos_dispersion = {}
    for anchor in range(first_anchor, last_anchor, backtest.ANCHOR_SPACING_DAYS):
        window = close.iloc[anchor - backtest.LOOKBACK + 1: anchor + 1]
        try:
            p10_pct, p50_pct, p90_pct = backtest._chronos_pct_changes(client, window, backtest.HORIZON_DAYS)
        except Exception:
            continue  # anchor saltado -- no rompe el resto, mismo criterio que build_anchor_dataset
        band_width = p90_pct - p10_pct
        dispersion_ratio = abs(p50_pct) / band_width if band_width > 0 else 0.0
        anchor_date = raw_dates[anchor]
        chronos_p50[anchor_date] = p50_pct
        chronos_dispersion[anchor_date] = dispersion_ratio

    p50_series = pd.Series(chronos_p50, dtype=float).sort_index()
    dispersion_series = pd.Series(chronos_dispersion, dtype=float).sort_index()
    atr_by_date = pd.Series(atr_series.to_numpy(), index=raw_dates)

    vix_series = macro_data.get_vix_series(conn)
    brecha_series = macro_data.get_brecha_cambiaria_pct_series(conn)

    out = pd.DataFrame(index=full_index)
    out["chronos_p50_return_pct"] = features._align_ffill(p50_series, full_index).to_numpy()
    out["chronos_dispersion_ratio"] = features._align_ffill(dispersion_series, full_index).to_numpy()
    out["vix_level"] = features._align_ffill(vix_series, full_index).to_numpy()
    out["brecha_cambiaria_pct"] = features._align_ffill(brecha_series, full_index).to_numpy()
    out["atr_pct"] = features._align_ffill(atr_by_date, full_index).to_numpy()

    # Primeras ruedas sin anchor/serie exogena resuelta todavia (antes del
    # primer anchor, o antes de que exista cobertura de VIX/brecha para esa
    # fecha) -- se completan con 0.0 (neutro: "sin señal todavia"), nunca se
    # inventa un valor de mercado. Declarado, no oculto.
    return out.fillna(0.0).reset_index(drop=True)


def _static_baseline_metrics(core_aligned, satellite_aligned, allocation, out_idx):
    """Regla ESTATICA vigente (ver portfolio/config.py::get_capital_allocation)
    sobre el MISMO bloque OOS -- yield_engine.blend_engine_returns reusado
    TAL CUAL (nunca reimplementado con otro criterio), mismo que usa
    portfolio/backtest.py::run_dual_engine_backtest para su propia fila GLOBAL."""
    static_daily = yield_engine.blend_engine_returns(
        core_aligned.iloc[out_idx], satellite_aligned.iloc[out_idx],
        allocation["core_pct"], allocation["satellite_pct"],
    )
    idle_weight = pd.Series(0.0, index=static_daily.index)  # el blend ya incluye la parte ociosa de cada sleeve
    return backtest._compute_engine_metrics(static_daily, idle_weight, trades=[])


def print_rl_comparison_report(rl_metrics, static_metrics, allocation, avg_weights,
                                in_sample_days, out_of_sample_days):
    avg_core_w, avg_satellite_w, avg_caucion_w = avg_weights
    cols = [("Politica", 26), ("Retorno Anual USD %", 20), ("Sharpe USD", 11), ("Max DD USD", 11), ("Dias", 8)]
    header = " | ".join(name.ljust(w) for name, w in cols)
    print("\n" + "=" * len(header))
    print(f"CURVA DE PATRIMONIO APRENDIDA (PPO) vs. REGLA ESTATICA {allocation['core_pct']:.0f}/{allocation['satellite_pct']:.0f} "
          f"-- OUT-OF-SAMPLE ({out_of_sample_days}r, politica entrenada IN-SAMPLE {in_sample_days}r, nunca vista durante el entrenamiento)")
    print("=" * len(header))
    print(header)
    print("-" * len(header))
    rows = [
        ("RL (PPO aprendido)", rl_metrics),
        (f"ESTATICO {allocation['core_pct']:.0f}/{allocation['satellite_pct']:.0f}", static_metrics),
    ]
    for label, m in rows:
        row = [
            label.ljust(cols[0][1]),
            backtest._fmt_pct(m["annualized_return_pct"]).ljust(cols[1][1]),
            backtest._fmt_sharpe(m["sharpe_ratio"]).ljust(cols[2][1]),
            backtest._fmt_pct(m["max_drawdown_pct"]).ljust(cols[3][1]),
            str(m["n_days"]).ljust(cols[4][1]),
        ]
        print(" | ".join(row))
    print("=" * len(header))
    print(f"Pesos promedio aprendidos por el agente en el OOS: "
          f"Core {avg_core_w * 100:.1f}% / Satelite {avg_satellite_w * 100:.1f}% / Caucion {avg_caucion_w * 100:.1f}% "
          f"(la regla estatica usa {allocation['core_pct']:.0f}%/{allocation['satellite_pct']:.0f}%/0% fijo, sin Caucion dedicada)")


def run_rl_oos_validation(universe=backtest.UNIVERSE, lookback_days=backtest.CORE_BACKTEST_LOOKBACK_DAYS,
                           total_timesteps=TOTAL_TIMESTEPS):
    client = ChronosServiceClient()
    try:
        client.health()
    except Exception as exc:
        print(f"ERROR: no se pudo contactar al microservicio de prediccion ({exc}). "
              f"Levantalo primero (ver ./dev.sh o services/predictor/README.md).", file=sys.stderr)
        sys.exit(1)

    conn = db.get_connection()
    try:
        print(f"Simulando Motor CORE sobre {len(universe)} activos ({lookback_days} ruedas) -- insumo del entorno RL...")
        t0 = time.time()
        core_result = backtest.simulate_core_engine(universe, client, conn, lookback_days)
        print(f"-- Core: {len(core_result['trades'])} trades, {core_result['chronos_calls']} llamadas ({time.time() - t0:.1f}s)")

        print("Simulando Motor SATELITE (sin Chronos-Bolt, mas barato)...")
        t0 = time.time()
        satellite_result = backtest.simulate_satellite_engine(universe, conn, lookback_days)
        print(f"-- Satelite: {len(satellite_result['trades'])} trades ({time.time() - t0:.1f}s)")

        dates = core_result["daily_returns_pct"].index
        core_aligned = core_result["daily_returns_pct"].reindex(dates).fillna(0.0)
        satellite_aligned = satellite_result["daily_returns_pct"].reindex(dates).fillna(0.0)

        hurdle_tna = float(db.get_signal_config(conn).get("caucion_reference_tna") or 0.0)
        caucion_daily = pd.Series(yield_engine.daily_caucion_return_pct(hurdle_tna), index=dates)

        print(f"Armando observaciones de mercado ({MARKET_PROXY_SYMBOL}, anchors Chronos cada "
              f"{backtest.ANCHOR_SPACING_DAYS}r, VIX/brecha/ATR diarios)...")
        t0 = time.time()
        market_df = build_market_observation_table(conn, client, dates)
        print(f"-- listo ({time.time() - t0:.1f}s)")

        allocation = capital_config.get_capital_allocation(conn)
    finally:
        conn.close()

    in_sample_days = backtest.IN_SAMPLE_DAYS
    out_of_sample_days = backtest.OUT_OF_SAMPLE_DAYS
    if len(dates) < in_sample_days + out_of_sample_days:
        raise ValueError(
            f"ventana de {len(dates)} ruedas no alcanza para separar {in_sample_days}+{out_of_sample_days}"
        )

    in_idx = slice(0, in_sample_days)
    out_idx = slice(in_sample_days, in_sample_days + out_of_sample_days)

    def _make_train_env():
        return RLCapitalAllocationEnv(
            market_df.iloc[in_idx].reset_index(drop=True),
            core_aligned.iloc[in_idx].reset_index(drop=True),
            satellite_aligned.iloc[in_idx].reset_index(drop=True),
            caucion_daily.iloc[in_idx].reset_index(drop=True),
        )

    train_env = DummyVecEnv([_make_train_env])

    print(f"Entrenando PPO ({total_timesteps} timesteps, IN-SAMPLE {in_sample_days}r -- "
          f"liviano/exploratorio, SIN busqueda de hiperparametros)...")
    t0 = time.time()
    model = PPO("MlpPolicy", train_env, verbose=0)
    model.learn(total_timesteps=total_timesteps)
    print(f"-- entrenamiento listo ({time.time() - t0:.1f}s)")

    # Evaluacion OUT-OF-SAMPLE: la politica aprendida (deterministic=True,
    # SIN ruido de exploracion) corre sobre el bloque nunca visto durante el
    # entrenamiento -- la MISMA separacion in/out-of-sample que ya usa
    # portfolio/backtest.py::print_in_out_sample_report para el resto del
    # sistema (200r/100r), reusada tal cual, no un corte distinto inventado
    # para este modulo.
    eval_env = RLCapitalAllocationEnv(
        market_df.iloc[out_idx].reset_index(drop=True),
        core_aligned.iloc[out_idx].reset_index(drop=True),
        satellite_aligned.iloc[out_idx].reset_index(drop=True),
        caucion_daily.iloc[out_idx].reset_index(drop=True),
    )
    obs, _info = eval_env.reset()
    rl_daily_returns = []
    rl_weights_log = []
    done = False
    while not done:
        action, _state = model.predict(obs, deterministic=True)
        obs, _reward, terminated, truncated, info = eval_env.step(action)
        rl_daily_returns.append(info["daily_return_pct"])
        rl_weights_log.append(info["weights"])
        done = terminated or truncated

    out_dates = dates[out_idx]
    rl_daily_series = pd.Series(rl_daily_returns, index=out_dates)
    rl_idle_weight = pd.Series([w[2] for w in rl_weights_log], index=out_dates)  # caucion_w como "ocioso" del agente
    rl_metrics = backtest._compute_engine_metrics(rl_daily_series, rl_idle_weight, trades=[])

    static_metrics = _static_baseline_metrics(core_aligned, satellite_aligned, allocation, out_idx)

    avg_weights = tuple(float(np.mean([w[i] for w in rl_weights_log])) for i in range(3))
    print_rl_comparison_report(rl_metrics, static_metrics, allocation, avg_weights, in_sample_days, out_of_sample_days)
    return rl_metrics, static_metrics


if __name__ == "__main__":
    run_rl_oos_validation()
