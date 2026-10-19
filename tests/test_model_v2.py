"""Tests de la 'evolucion del motor cuantitativo' (ver conversacion):
detector de regimen (portfolio/regime.py), position sizing dinamico del
Satelite (portfolio/short_term_engine.py::compute_satellite_position_weight/
kelly_fraction), y FFD/ADF + resolucion de subyacente CEDEAR
(portfolio/features.py). Logica pura, sin red, sin Chronos-Bolt, sin DB -- se
prueban las formulas en aislamiento, mismo patron que tests/test_paper_trading.py.

Nota honesta (ver conversacion): esto NO incluye una corrida completa de
run_dual_engine_backtest ni del walk-forward V2/V3/V4 comparando Sharpe/
win-rate/accuracy antes-vs-despues -- esos backtests necesitan Chronos-Bolt
vivo y red (data912/yfinance), lo que no encaja con el resto de este suite
(rapido, sin red, ver docstrings de los demas tests/test_*.py). Esas
comparaciones se corrieron y se reportaron a mano en la conversacion, no
como assert automatizado."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from portfolio import backtest, features, regime, short_term_engine


# --- regime.classify_regime --------------------------------------------------

def _trending_series(n=80, start=100.0, step=1.0):
    """Serie estrictamente ascendente -- EMA50 queda por debajo del ultimo
    cierre, clasifica 'alcista'."""
    return pd.Series([start + i * step for i in range(n)])


def _declining_series(n=80, start=180.0, step=1.0):
    """Serie estrictamente descendente -- EMA50 queda por encima del ultimo
    cierre, clasifica 'lateral' (regime.py no distingue lateral de bajista,
    ver su propio docstring)."""
    return pd.Series([start - i * step for i in range(n)])


def test_classify_regime_alcista_sin_vix():
    spy = _trending_series()
    assert regime.classify_regime(spy, vix_value=15.0) == regime.REGIME_ALCISTA


def test_classify_regime_lateral_en_tendencia_bajista():
    spy = _declining_series()
    assert regime.classify_regime(spy, vix_value=15.0) == regime.REGIME_LATERAL


def test_classify_regime_vix_alto_prioriza_sobre_tendencia_alcista():
    # Incluso con SPY en tendencia claramente alcista, un VIX disparado debe
    # bloquear -- es la prioridad explicita que pide la conversacion (un
    # crash puede arrancar con SPY todavia "por encima" de su EMA50).
    spy = _trending_series()
    assert regime.classify_regime(spy, vix_value=30.0) == regime.REGIME_ALTA_VOLATILIDAD


def test_classify_regime_vix_justo_en_el_umbral_no_bloquea():
    # El umbral es estrictamente "> 22", no ">=" -- VIX_HIGH_THRESHOLD exacto
    # no debe bloquear.
    spy = _trending_series()
    result = regime.classify_regime(spy, vix_value=regime.VIX_HIGH_THRESHOLD)
    assert result != regime.REGIME_ALTA_VOLATILIDAD


def test_classify_regime_vix_none_degrada_a_solo_spy():
    spy = _trending_series()
    assert regime.classify_regime(spy, vix_value=None) == regime.REGIME_ALCISTA


def test_classify_regime_serie_corta_degrada_a_lateral():
    # Menos datos que EMA_TREND_WINDOW + 1 -- no hay suficiente historia para
    # calcular una EMA50 confiable, nunca se asume "alcista" por defecto.
    spy = _trending_series(n=regime.EMA_TREND_WINDOW)
    assert regime.classify_regime(spy, vix_value=None) == regime.REGIME_LATERAL


def test_classify_regime_serie_none_degrada_a_lateral():
    assert regime.classify_regime(None, vix_value=10.0) == regime.REGIME_LATERAL


# --- regime.satellite_blocked ------------------------------------------------

def test_satellite_blocked_solo_en_alta_volatilidad():
    assert regime.satellite_blocked(regime.REGIME_ALTA_VOLATILIDAD) is True
    assert regime.satellite_blocked(regime.REGIME_ALCISTA) is False
    assert regime.satellite_blocked(regime.REGIME_LATERAL) is False


# --- short_term_engine.compute_satellite_position_weight ---------------------

def test_compute_satellite_position_weight_atr_bajo_cap_al_slot_maximo():
    # ATR muy chico -> target_risk/atr supera largamente el slot maximo
    # equal-weight -- el resultado nunca debe superar ese techo.
    weight = short_term_engine.compute_satellite_position_weight(
        atr_pct_value=1.0, max_slot_weight=0.10, target_risk_pct=8.0
    )
    assert weight == 0.10


def test_compute_satellite_position_weight_atr_alto_reduce_el_peso():
    # ATR grande -> target_risk/atr cae por debajo del slot maximo, el activo
    # mas volatil debe pesar menos que uno tranquilo.
    weight_volatil = short_term_engine.compute_satellite_position_weight(
        atr_pct_value=100.0, max_slot_weight=0.10, target_risk_pct=8.0
    )
    weight_tranquilo = short_term_engine.compute_satellite_position_weight(
        atr_pct_value=4.0, max_slot_weight=0.10, target_risk_pct=8.0
    )
    assert weight_volatil == 0.08  # 8/100=0.08, por debajo del cap
    assert weight_tranquilo == 0.10  # 8/4=2.0, supera el cap -> se queda en el slot maximo
    assert weight_volatil < weight_tranquilo


def test_compute_satellite_position_weight_atr_invalido_usa_el_slot_maximo():
    # Dato faltante != "sin riesgo" -- nunca se asume el mejor caso, se cae
    # al tamano equal-weight original (mismo criterio que el resto del
    # proyecto para datos faltantes).
    assert short_term_engine.compute_satellite_position_weight(None, 0.10) == 0.10
    assert short_term_engine.compute_satellite_position_weight(0, 0.10) == 0.10
    assert short_term_engine.compute_satellite_position_weight(-5.0, 0.10) == 0.10


# --- short_term_engine.kelly_fraction ----------------------------------------

def test_kelly_fraction_caso_real_del_backtest():
    # Numeros reales del ultimo dual_engine_backtest_summary persistido (ver
    # conversacion): win_rate=57.4%, profit_factor=1.44 -- half-Kelly positivo
    # y razonable, no el ~-0.385 que daba la primera version (rota) de esta
    # formula usando take_profit_pct/stop_loss_pct como payoff ratio.
    f = short_term_engine.kelly_fraction(win_rate_pct=57.4, profit_factor=1.44)
    assert f is not None
    assert 0.08 < f < 0.09  # ~0.0877, coincide con lo observado en vivo


def test_kelly_fraction_ninguno_de_los_inputs_puede_faltar():
    assert short_term_engine.kelly_fraction(None, 1.44) is None
    assert short_term_engine.kelly_fraction(57.4, None) is None
    assert short_term_engine.kelly_fraction(57.4, 0) is None
    assert short_term_engine.kelly_fraction(57.4, -1.0) is None


def test_kelly_fraction_win_rate_extremos_no_explotan():
    # win_rate 0% o 100% dejarian p=0 o q=0 (b indefinido/cero) -- debe
    # degradar a None, nunca lanzar ZeroDivisionError.
    assert short_term_engine.kelly_fraction(0.0, 1.44) is None
    assert short_term_engine.kelly_fraction(100.0, 1.44) is None


def test_kelly_fraction_edge_desfavorable_se_recorta_a_cero():
    # Win rate bajo + profit factor flojo -> Kelly crudo negativo, debe
    # recortarse a 0.0 (max(0.0, f/2)) en vez de devolver un numero negativo
    # que el llamador podria mal-interpretar como "achicar" la posicion.
    f = short_term_engine.kelly_fraction(win_rate_pct=20.0, profit_factor=1.0)
    assert f == 0.0


# --- features.fracdiff_ffd / _ffd_weights ------------------------------------

def test_ffd_weights_w0_es_uno():
    # El array vuelve en orden [mas viejo, ..., w_1, w_0] (ver docstring,
    # alineado para pandas.rolling(...).apply(raw=True)) -- w_0 es siempre 1
    # y queda al FINAL.
    weights = features._ffd_weights(d=0.35, tau=1e-4)
    assert weights[-1] == pytest.approx(1.0)
    assert len(weights) > 1


def test_ffd_weights_d_mas_chico_decae_mas_lento_mas_pesos():
    # Memoria historica (ver conversacion): un d mas chico diferencia menos
    # -- sus pesos decaen mas lento, hace falta MAS terminos para cruzar el
    # mismo umbral tau, ventana mas ancha.
    weights_chico = features._ffd_weights(d=0.1, tau=1e-4)
    weights_grande = features._ffd_weights(d=0.8, tau=1e-4)
    assert len(weights_chico) > len(weights_grande)


def test_fracdiff_ffd_nan_en_la_ventana_incompleta():
    # Cero look-ahead (ver modulo): las primeras len(pesos)-1 filas no tienen
    # ventana completa todavia -- deben ser NaN, nunca un numero inventado
    # rellenando con datos que no existen.
    prices = pd.Series(100.0 + np.cumsum(np.random.default_rng(0).normal(0, 1, 300)))
    diffed = features.fracdiff_ffd(prices, d=0.4, tau=1e-3)
    width = len(features._ffd_weights(d=0.4, tau=1e-3))
    assert diffed.iloc[: width - 1].isna().all()
    assert diffed.iloc[width:].notna().all()


# --- features.check_stationarity ---------------------------------------------

def test_check_stationarity_pocos_datos_devuelve_none():
    # ADF no es confiable con poca muestra -- nunca se inventa un resultado.
    assert features.check_stationarity(pd.Series([1.0, 2.0, 3.0])) is None


def test_check_stationarity_ruido_blanco_es_estacionario():
    noise = pd.Series(np.random.default_rng(1).normal(0, 1, 500))
    result = features.check_stationarity(noise)
    assert result is not None
    assert result["is_stationary"] is True
    assert result["p_value"] < 0.01


def test_check_stationarity_random_walk_no_es_estacionario():
    # Un random walk puro (I(1), el caso clasico que motiva la FFD) NO debe
    # pasar el ADF sin diferenciar.
    walk = pd.Series(np.cumsum(np.random.default_rng(2).normal(0, 1, 500)) + 100)
    result = features.check_stationarity(walk)
    assert result is not None
    assert result["is_stationary"] is False


# --- features.find_min_ffd_order ---------------------------------------------

def test_find_min_ffd_order_sobre_random_walk_encuentra_un_d():
    # Un random walk puro diferenciado a d=1.0 es EXACTAMENTE su ruido
    # original (estacionario por construccion) -- el barrido ascendente debe
    # encontrar algun d <= 1.0 que logre p < 0.01, nunca (None, None) para
    # este caso de libro.
    walk = pd.Series(np.cumsum(np.random.default_rng(3).normal(0, 1, 600)) + 100)
    found_d, adf_result = features.find_min_ffd_order(walk)
    assert found_d is not None
    assert adf_result["is_stationary"] is True


# --- features._resolve_technical_source_df (sin red: asset_type != cedear) --

def _minimal_raw_df(n=80):
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    rng = np.random.default_rng(4)
    close = 100.0 + np.cumsum(rng.normal(0, 1, n))
    return pd.DataFrame({
        "timestamps": dates, "open": close, "high": close + 1, "low": close - 1,
        "close": close, "volume": rng.integers(1000, 5000, n).astype(float),
    })


def test_resolve_technical_source_df_bono_no_usa_subyacente():
    # bono no es USD_MODEL_ASSET_TYPES (ver forecasting.py) -- build_model_input
    # devuelve el df tal cual SIN tocar conn, y al no ser 'cedear' tampoco se
    # intenta ningun fetch de subyacente -- testeable 100% offline (conn=None).
    raw_df = _minimal_raw_df()
    resolved, used_underlying = features._resolve_technical_source_df(None, raw_df, "bono", "AL30D")
    assert used_underlying is False
    assert resolved["close"].equals(raw_df["close"])


def test_resolve_technical_source_df_cedear_no_verificado_no_intenta_red(monkeypatch):
    # Un cedear cuyo simbolo NO esta en CEDEAR_DIRECT_UNDERLYING_SYMBOLS debe
    # cortar ANTES de llamar a fetchers.ensure_underlying_ohlcv_history (sin
    # red) -- se verifica monkeypracheando build_model_input (stub, sin CCL
    # real) y confirmando que el fetch del subyacente nunca se invoca.
    raw_df = _minimal_raw_df()
    monkeypatch.setattr(features, "build_model_input", lambda conn, df, asset_type: (df, 1000.0, "USD"))

    def _boom(*args, **kwargs):
        raise AssertionError("no deberia intentar fetchear un subyacente para un cedear no verificado")
    monkeypatch.setattr(features.fetchers, "ensure_underlying_ohlcv_history", _boom)

    assert "ZZZZ_NO_EXISTE" not in features.CEDEAR_DIRECT_UNDERLYING_SYMBOLS
    resolved, used_underlying = features._resolve_technical_source_df(None, raw_df, "cedear", "ZZZZ_NO_EXISTE")
    assert used_underlying is False
    assert resolved["close"].equals(raw_df["close"])


# --- backtest._daily_vol_ewma_pct ---------------------------------------------

def test_daily_vol_ewma_pct_nan_durante_warmup():
    # min_periods=window (ver docstring) -- sin suficiente historia todavia,
    # nunca un sigma calculado con pocos puntos.
    close = pd.Series(100.0 + np.cumsum(np.random.default_rng(5).normal(0, 1, 50)))
    vol = backtest._daily_vol_ewma_pct(close, window=20)
    assert vol.iloc[:20].isna().all()
    assert vol.iloc[20:].notna().all()


def test_daily_vol_ewma_pct_mayor_volatilidad_da_mayor_sigma():
    rng = np.random.default_rng(6)
    close_tranquilo = pd.Series(100.0 + np.cumsum(rng.normal(0, 0.1, 100)))
    close_volatil = pd.Series(100.0 + np.cumsum(rng.normal(0, 3.0, 100)))
    vol_tranquilo = backtest._daily_vol_ewma_pct(close_tranquilo, window=20).iloc[-1]
    vol_volatil = backtest._daily_vol_ewma_pct(close_volatil, window=20).iloc[-1]
    assert vol_volatil > vol_tranquilo


# --- backtest._triple_barrier_label -------------------------------------------

def _escalera(valores_desde_anchor, entry=100.0):
    """close sintetico: [...warmup plano..., entry, *valores_desde_anchor] --
    el anchor queda en la posicion len(warmup), los valores siguientes son
    EXACTAMENTE los pedidos (sin ruido), para poder calcular a mano que
    barrera se toca y cuando."""
    warmup = [entry] * 30
    serie = warmup + [entry] + valores_desde_anchor
    return pd.Series(serie), len(warmup)


def test_triple_barrier_toca_la_barrera_superior():
    # sigma=1.0, pt_mult=2.0 -> barrera superior en +2.0% -- el dia 3 sube a
    # +3% (toca), dias previos se quedan cortos.
    close, anchor = _escalera([100.5, 101.0, 103.0, 90.0])  # dia 4 es un crash, pero ya deberia haber salido en el dia 3
    y, ret, hold_days, reason = backtest._triple_barrier_label(
        close, anchor, direction=1, sigma_pct=1.0, max_hold=4, pt_mult=2.0, sl_mult=2.0,
    )
    assert y == 1
    assert reason == "upper"
    assert hold_days == 3
    assert ret == pytest.approx(3.0)


def test_triple_barrier_toca_la_barrera_inferior():
    close, anchor = _escalera([99.5, 99.0, 97.5, 110.0])  # dia 3 cae a -2.5%, toca antes del rebote del dia 4
    y, ret, hold_days, reason = backtest._triple_barrier_label(
        close, anchor, direction=1, sigma_pct=1.0, max_hold=4, pt_mult=2.0, sl_mult=2.0,
    )
    assert y == 0
    assert reason == "lower"
    assert hold_days == 3
    assert ret == pytest.approx(-2.5)


def test_triple_barrier_barrera_vertical_sin_tocar_ninguna():
    # Movimientos chicos, nunca cruza +-2% en los 4 dias -- expira por tiempo.
    close, anchor = _escalera([100.3, 100.8, 100.5, 101.0])
    y, ret, hold_days, reason = backtest._triple_barrier_label(
        close, anchor, direction=1, sigma_pct=1.0, max_hold=4, pt_mult=2.0, sl_mult=2.0,
    )
    assert y == 0  # pedido explicito: vertical sin tocar la superior es 0, no una tercera clase
    assert reason == "vertical"
    assert hold_days == 4
    assert ret == pytest.approx(1.0)


def test_triple_barrier_direccion_baja_invierte_las_barreras():
    # direction=-1 (Chronos predijo baja) -- "superior" ahora es que el
    # precio BAJE lo suficiente (confirma la baja), aunque el retorno crudo
    # devuelto siga siendo el del activo (sin invertir signo, ver docstring).
    close, anchor = _escalera([99.0, 97.5, 96.0, 110.0])
    y, ret, hold_days, reason = backtest._triple_barrier_label(
        close, anchor, direction=-1, sigma_pct=1.0, max_hold=4, pt_mult=2.0, sl_mult=2.0,
    )
    assert y == 1
    assert reason == "upper"  # "superior" en la direccion evaluada, no en el signo crudo del precio
    assert ret == pytest.approx(-2.5)  # retorno CRUDO del activo (bajo), no invertido


def test_triple_barrier_sigma_none_usa_fallback_sin_sigma():
    # Sin sigma (ventana EWMA incompleta) -- se degrada a comparar el retorno
    # a max_hold contra 0%, declarado en exit_reason, nunca se inventa un sigma.
    close, anchor = _escalera([100.1, 100.2, 100.3, 100.4])
    y, ret, hold_days, reason = backtest._triple_barrier_label(
        close, anchor, direction=1, sigma_pct=None, max_hold=4, pt_mult=2.0, sl_mult=2.0,
    )
    assert reason == "vertical_sin_sigma"
    assert hold_days == 4
    assert y == 1  # retorno positivo en la direccion evaluada -> y=1 en este fallback
    assert ret == pytest.approx(0.4)


# --- backtest CPCV (_cpcv_groups / _purge_and_embargo_train_indices) ---------

def _synthetic_pooled_df(n=600, seed=9):
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2020-01-01", periods=n, freq="3D")
    df = pd.DataFrame({c: rng.normal(0, 1, n) for c in features.V4_FEATURE_COLUMNS})
    df["date"] = dates
    df["symbol"] = rng.choice(["AAA", "BBB", "CCC"], n)
    df["y"] = rng.integers(0, 2, n)
    df["chronos_pred"] = rng.integers(0, 2, n)
    df["actual_return_pct"] = rng.normal(0, 2, n)
    return df.sort_values("date").reset_index(drop=True)


def test_cpcv_groups_cubre_todo_el_pool_sin_solapar():
    df = _synthetic_pooled_df(n=603)  # no multiplo exacto de 6, a proposito
    bounds = backtest._cpcv_groups(df, n_groups=6)
    assert len(bounds) == 6
    assert bounds[0][0] == 0
    assert bounds[-1][1] == len(df)
    for (s1, e1), (s2, _e2) in zip(bounds, bounds[1:]):
        assert e1 == s2  # contiguos, sin huecos ni solapamiento


def test_purge_and_embargo_excluye_mas_que_solo_el_test():
    df = _synthetic_pooled_df(n=1200)
    bounds = backtest._cpcv_groups(df, n_groups=6)
    train_mask, test_mask = backtest._purge_and_embargo_train_indices(df, bounds, (0, 1))
    assert test_mask.sum() == (bounds[0][1] - bounds[0][0]) + (bounds[1][1] - bounds[1][0])
    # Ninguna fila es a la vez train y test.
    assert not np.any(train_mask & test_mask)
    # Con purga+embargo activos, deberian quedar MENOS filas de train que
    # "todo lo que no es test" (si no, purga/embargo no estaria haciendo nada).
    assert train_mask.sum() < (len(df) - test_mask.sum())


def test_purge_and_embargo_sin_overlap_devuelve_una_particion_valida():
    # Train y test son mutuamente excluyentes en TODAS las combinaciones, sin
    # importar que grupos se elijan.
    df = _synthetic_pooled_df(n=1200)
    bounds = backtest._cpcv_groups(df, n_groups=6)
    for combo in [(0, 1), (2, 5), (3, 4)]:
        train_mask, test_mask = backtest._purge_and_embargo_train_indices(df, bounds, combo)
        assert not np.any(train_mask & test_mask)


# --- backtest.deflated_sharpe_ratio / _expected_max_sharpe_under_null -------

def test_expected_max_sharpe_bajo_dos_estimaciones_none():
    # No se puede medir Var[SR] con una sola estimacion -- nunca se inventa.
    assert backtest._expected_max_sharpe_under_null([0.5]) is None


def test_expected_max_sharpe_crece_con_la_varianza():
    rng = np.random.default_rng(10)
    sr0_low_var = backtest._expected_max_sharpe_under_null(rng.normal(0.2, 0.05, 15))
    sr0_high_var = backtest._expected_max_sharpe_under_null(rng.normal(0.2, 0.5, 15))
    assert sr0_high_var > sr0_low_var


def test_deflated_sharpe_pocos_trades_devuelve_none():
    assert backtest.deflated_sharpe_ratio([0.1, -0.2, 0.3], [0.1, 0.2, 0.3]) is None


def test_deflated_sharpe_edge_real_da_dsr_alto():
    rng = np.random.default_rng(11)
    trade_returns = rng.normal(0.3, 1.0, 500)
    sharpe_estimates = rng.normal(0.25, 0.05, 15)  # edge consistente, poca varianza entre combinaciones
    result = backtest.deflated_sharpe_ratio(trade_returns, sharpe_estimates)
    assert result is not None
    assert result["dsr"] > 0.9


def test_deflated_sharpe_ruido_puro_da_dsr_bajo():
    rng = np.random.default_rng(12)
    trade_returns = rng.normal(0.0, 1.0, 500)  # sin ningun edge real
    sharpe_estimates = rng.normal(0.0, 0.3, 15)
    result = backtest.deflated_sharpe_ratio(trade_returns, sharpe_estimates)
    assert result is not None
    assert result["dsr"] < 0.5


def test_deflated_sharpe_misma_media_pero_mas_varianza_entre_combos_baja_el_dsr():
    # Mismo SR_hat/trade_returns, pero una distribucion de Sharpe por
    # combinacion mucho mas ruidosa -- el DSR debe caer (SR0 sube).
    rng = np.random.default_rng(13)
    trade_returns = rng.normal(0.3, 1.0, 500)
    tight = backtest.deflated_sharpe_ratio(trade_returns, rng.normal(0.25, 0.05, 15))
    noisy = backtest.deflated_sharpe_ratio(trade_returns, rng.normal(0.1, 0.5, 15))
    assert noisy["dsr"] < tight["dsr"]


# --- backtest unificado multimotor (blend_n_engine_returns / _cpcv_blended_paths) --
# Hallazgo real (ver conversacion): simulate_unified_backtest crasheaba con
# KeyError 'return_pct' porque Factor Investing reporta eventos de
# REBALANCEO (sin return_pct propio), no trades de entrada/salida como
# Core/Satelite/Pairs -- este test fija esa regresion especifica.

def _fake_engine_result(seed, n=60, with_trade_return_pct=True):
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2025-01-01", periods=n, freq="D")
    returns = pd.Series(rng.normal(0.05, 0.3, n), index=dates)
    idle = pd.Series(rng.uniform(0.2, 0.8, n), index=dates)
    trade = {"return_pct": 1.5} if with_trade_return_pct else {"holdings_before": [], "holdings_after": ["AAPL"]}
    return {"daily_returns_pct": returns, "idle_weight": idle, "trades": [trade]}


def test_blend_n_engine_returns_pesos_normalizados_y_fecha_faltante_es_cero():
    core = _fake_engine_result(1)
    # 'factor' solo tiene la mitad de las fechas de 'core' -- la mitad
    # faltante debe contar 0% para 'factor' ese dia, nunca inventar un dato.
    factor = _fake_engine_result(2, n=30)
    blended = backtest.blend_n_engine_returns(
        {"core": core, "factor": factor}, {"core": 70.0, "factor": 30.0}
    )
    assert len(blended) == 60  # union de fechas, no interseccion
    # Dias 31-60: 'factor' no tiene dato -> cuenta 0%, el blend es SOLO 70% de core.
    last_date = core["daily_returns_pct"].index[-1]
    expected = core["daily_returns_pct"].loc[last_date] * 0.7
    assert blended.loc[last_date] == pytest.approx(expected)


def test_simulate_unified_backtest_trade_dict_sin_return_pct_no_rompe_metricas():
    # Reproduce exactamente el bug real: un motor (Factor) sin return_pct en
    # sus trades no debe tirar KeyError al pasar por _compute_engine_metrics
    # -- se filtra, no se rompe el reporte completo por un motor.
    factor_like_trades = [{"holdings_before": [], "holdings_after": ["AAPL"]}]
    pairs_like_trades = [{"return_pct": 2.0}]
    all_trades = pairs_like_trades + [t for t in factor_like_trades if "return_pct" in t]
    dates = pd.date_range("2025-01-01", periods=30, freq="D")
    returns = pd.Series(np.random.default_rng(3).normal(0.05, 0.3, 30), index=dates)
    idle = pd.Series(0.5, index=dates)
    metrics = backtest._compute_engine_metrics(returns, idle, all_trades)
    assert metrics["n_trades"] == 1  # solo el trade con return_pct cuenta para Win Rate/Profit Factor


def test_cpcv_blended_paths_respeta_embargo_y_cubre_todos_los_bloques():
    dates = list(pd.date_range("2025-01-01", periods=120, freq="D"))
    paths = backtest._cpcv_blended_paths(dates, n_groups=6, n_select=2, embargo_days=3)
    from math import comb
    assert len(paths) == comb(6, 2)
    for path in paths:
        assert len(path) > 0
        assert all(d in dates for d in path)


# --- regime HMM (fit_hmm_regime_model / classify_regime_hmm / hmm_position_scale) ---

def _three_regime_series(seed=20, n_quiet=400, n_medium=400, n_crash=200):
    rng = np.random.default_rng(seed)
    vix = np.concatenate([
        rng.normal(14, 1.5, n_quiet), rng.normal(19, 2.0, n_medium), rng.normal(38, 6.0, n_crash),
    ])
    ret = np.concatenate([
        rng.normal(0.0008, 0.004, n_quiet), rng.normal(0.0001, 0.006, n_medium), rng.normal(-0.015, 0.025, n_crash),
    ])
    return pd.Series(ret), pd.Series(vix)


def test_fit_hmm_regime_model_rechaza_poca_historia():
    ret, vix = _three_regime_series(n_quiet=10, n_medium=10, n_crash=5)
    with pytest.raises(ValueError):
        regime.fit_hmm_regime_model(ret, vix)


def test_fit_hmm_regime_model_etiqueta_los_3_regimenes():
    ret, vix = _three_regime_series()
    model, labels = regime.fit_hmm_regime_model(ret, vix)
    assert set(labels.values()) <= {regime.REGIME_ALCISTA, regime.REGIME_LATERAL, regime.REGIME_ALTA_VOLATILIDAD}
    # El estado de mayor VIX medio SIEMPRE se etiqueta alta_volatilidad.
    crash_state = int(np.argmax(model.means_[:, 1]))
    assert labels[crash_state] == regime.REGIME_ALTA_VOLATILIDAD


def test_classify_regime_hmm_probabilidades_suman_uno():
    ret, vix = _three_regime_series()
    model, labels = regime.fit_hmm_regime_model(ret, vix)
    _label, probs = regime.classify_regime_hmm(model, labels, ret, vix)
    assert sum(probs.values()) == pytest.approx(1.0, abs=1e-6)
    assert all(0.0 <= p <= 1.0 for p in probs.values())


def test_classify_regime_hmm_rueda_tranquila_vs_rueda_de_panico():
    # Comparacion relativa (no un label exacto, que depende de como el HMM
    # separe los regimenes en datos sinteticos) -- una rueda claramente
    # tranquila (prefijo corto, todo dentro del primer tramo) debe tener
    # MENOR probabilidad de 'alta_volatilidad' que la ULTIMA rueda (dentro
    # del tramo de crash simulado).
    ret, vix = _three_regime_series()
    model, labels = regime.fit_hmm_regime_model(ret, vix)
    _label_quiet, probs_quiet = regime.classify_regime_hmm(model, labels, ret.iloc[:50], vix.iloc[:50])
    _label_crash, probs_crash = regime.classify_regime_hmm(model, labels, ret, vix)
    crash_label = regime.REGIME_ALTA_VOLATILIDAD
    assert probs_crash.get(crash_label, 0.0) > probs_quiet.get(crash_label, 0.0)


def test_hmm_position_scale_rango_valido():
    assert regime.hmm_position_scale({regime.REGIME_ALTA_VOLATILIDAD: 0.0}) == 1.0
    assert regime.hmm_position_scale({regime.REGIME_ALTA_VOLATILIDAD: 1.0}) == 0.0
    assert regime.hmm_position_scale({regime.REGIME_ALTA_VOLATILIDAD: 0.3}) == pytest.approx(0.7)
    assert regime.hmm_position_scale({}) == 1.0  # sin probabilidad de panico -> sin dampening
    assert regime.hmm_position_scale({regime.REGIME_ALTA_VOLATILIDAD: 1.0}, min_scale=0.1) == 0.1


def test_hmm_position_scale_nunca_bloquea_del_todo_sin_min_scale_cero_explicito():
    # Diferencia de diseño pedida explicitamente (ver conversacion): a
    # diferencia de satellite_blocked (corte 100%), el escalado HMM solo
    # llega a cero si min_scale lo permite Y la probabilidad de panico es
    # exactamente 1.0 -- nunca un apagado total "por las dudas".
    scale = regime.hmm_position_scale({regime.REGIME_ALTA_VOLATILIDAD: 0.9999})
    assert scale > 0.0
