"""Detector de régimen de mercado (ver conversación, evolución del motor
cuantitativo) -- clasifica cada rueda en 'alcista' / 'lateral' /
'alta_volatilidad', independiente del propio filtro de régimen del Core
(SPY > su EMA50, ver portfolio/signals.py::_spy_regime_filter equivalente en
live_signals.py) -- este módulo es el que usa el Motor SATÉLITE, que hasta
ahora operaba sin ningún filtro de régimen.

Señales usadas:
  - VIX (CBOE Volatility Index, ver portfolio/macro_data.py::get_vix_series)
    -- proxy de miedo/volatilidad implícita GLOBAL. No existe un índice de
    volatilidad argentino público y gratuito, mismo límite ya declarado para
    la tasa de caución en otras partes del proyecto.
  - SPY vs su propia EMA50 -- tendencia primaria, mismo criterio que ya usa
    Core.

'alta_volatilidad' (VIX > VIX_HIGH_THRESHOLD) tiene PRIORIDAD sobre la
tendencia: un VIX disparado bloquea al Satélite sin importar qué diga SPY
(un crash puede arrancar con SPY todavía técnicamente "por encima" de su
EMA50 -- el VIX reacciona más rápido que un promedio móvil de 50 ruedas).

Cero look-ahead: classify_regime recibe la serie de SPY YA CORTADA hasta la
fecha a evaluar y el VIX YA RESUELTO a esa fecha (nunca mira para adelante),
mismo criterio que portfolio/short_term_engine.py y portfolio/features.py."""
from portfolio import indicators

# Recalibrado empiricamente (ver conversacion) barriendo VIX_HIGH_THRESHOLD
# en {22, 25, 26, 28, sin filtro} contra portfolio/backtest.py::simulate_satellite_engine
# (ultimas 300 ruedas, universo completo): el efecto del umbral resulto
# MARGINAL y NO monotono (26/28 midieron peor que 22 Y que 25 en win rate/
# profit factor/Sharpe -- "mas permisivo" no es automaticamente "mejor" en
# esta ventana):
#   VIX>22: 163 trades, +7.4% USD, win 58.3%, PF 1.45, Sharpe 1.48
#   VIX>25: 173 trades, +7.6% USD, win 57.8%, PF 1.43, Sharpe 1.48  <- elegido
#   VIX>26: 176 trades, +6.9% USD, win 56.8%, PF 1.38, Sharpe 1.34
#   VIX>28: 180 trades, +7.2% USD, win 56.7%, PF 1.39, Sharpe 1.35
#   sin filtro: 183 trades, +8.1% USD, win 57.4%, PF 1.44, Sharpe 1.49
# 25.0 es el piso del rango pedido (ver conversacion) y el que mejor sostiene
# el Sharpe dentro de ese rango -- la caida grande de retorno observada en el
# backtest (30.6% -> ~8% USD) NO es por este umbral (persiste incluso con el
# filtro prácticamente desactivado): es el nuevo position sizing por ATR/Kelly
# (ver portfolio/short_term_engine.py), que achica las apuestas ~4x contra el
# equal-weight fijo anterior -- declarado aca para no repetir el error de
# atribuirselo a este umbral otra vez.
VIX_HIGH_THRESHOLD = 25.0
EMA_TREND_WINDOW = 50

REGIME_ALCISTA = "alcista"
REGIME_LATERAL = "lateral"
REGIME_ALTA_VOLATILIDAD = "alta_volatilidad"


def classify_regime(spy_close_series, vix_value):
    """spy_close_series: pd.Series de cierres de SPY (USD), cronológica, YA
    cortada hasta la rueda a evaluar (inclusive). vix_value: último VIX
    conocido a esa fecha, o None si no hay dato -- sin VIX se degrada a
    clasificar solo por SPY (nunca se asume "tranquilo" por defecto, mismo
    principio de "dato faltante != mejor caso" ya aplicado en
    live_signals.py::_spy_regime_filter)."""
    if vix_value is not None and vix_value > VIX_HIGH_THRESHOLD:
        return REGIME_ALTA_VOLATILIDAD

    if spy_close_series is None or len(spy_close_series) < EMA_TREND_WINDOW + 1:
        return REGIME_LATERAL

    ema50 = indicators.ema(spy_close_series, EMA_TREND_WINDOW)
    above = float(spy_close_series.iloc[-1]) > float(ema50.iloc[-1])
    return REGIME_ALCISTA if above else REGIME_LATERAL


def satellite_blocked(regime):
    """Único régimen que bloquea COMPRAS NUEVAS del Satélite -- posiciones ya
    abiertas siguen su salida normal (TP/SL/expiración), esto solo afecta
    si se abren posiciones nuevas. 'lateral' y 'alcista' lo dejan operar
    igual: el Satélite es táctico/corto plazo y sus 3 tácticas ya exigen
    condiciones técnicas propias (sobreventa, breakout de volumen/ATR) --
    no hace falta exigirle además una tendencia alcista de fondo, solo
    blindarlo del escenario de crash/pánico que SI invalida cualquier regla
    técnica de corto plazo."""
    return regime == REGIME_ALTA_VOLATILIDAD


# --- Clasificador de regímenes por HMM (ver conversación, "Fine-Tuning LoRA +
# HMM") -----------------------------------------------------------------
#
# Alternativa AL clasificador de reglas de arriba (VIX>umbral + SPY vs EMA50)
# -- un Hidden Markov Model Gaussiano aprende 3 estados estocásticos sobre
# las MISMAS 2 señales (retorno log diario de SPY, nivel de VIX) en vez de
# un umbral fijo, y expone una PROBABILIDAD por estado (no un corte binario)
# para escalar el Position Sizing del Satélite de forma continua -- pedido
# explícito: "en lugar de aplicar cortes rígidos de VIX".
#
# "GaussianHMM de hmmlearn o scikit-learn" (pedido) -- aclarado: scikit-learn
# NO tiene HMM (se removió hace años a su propio paquete dedicado,
# hmmlearn), así que hmmlearn es la única opción real, no una elección
# arbitraria entre dos.
#
# Este clasificador es ADICIONAL, no un reemplazo silencioso del de reglas:
# el Satélite EN VIVO (portfolio/live_signals.py) y el backtest ya shippeado
# esta sesión (portfolio/backtest.py::simulate_satellite_engine) siguen
# usando classify_regime/satellite_blocked (ya validado y en producción) --
# este HMM lo usa la NUEVA validación histórica comparativa (ver
# portfolio/backtest.py::simulate_satellite_engine_hmm) para medir si el
# escalado suave mejora algo ANTES de reemplazar lo que ya funciona.
from hmmlearn.hmm import GaussianHMM
import numpy as np

HMM_N_STATES = 3
HMM_RANDOM_STATE = 42
HMM_N_ITER = 200
HMM_MIN_TRAIN_ROWS = 250  # ~1 año de ruedas -- piso declarado para que el HMM tenga señal real, no una estimación con 4 datos


def fit_hmm_regime_model(spy_log_returns, vix_series, n_states=HMM_N_STATES, random_state=HMM_RANDOM_STATE):
    """Entrena el HMM sobre 2 features por rueda: retorno log diario de SPY
    y nivel de VIX (pedido explícito: "retornos y volatilidad VIX/ATR" --
    se usa VIX, no ATR, para quedar alineado con las MISMAS 2 señales que ya
    usa classify_regime y no introducir una tercera fuente de volatilidad
    sin pedido explícito de cuál activo). spy_log_returns/vix_series: ya
    alineadas por fecha y SIN NaN (dropna antes de llamar -- hmmlearn no
    tolera NaN).

    Devuelve (modelo_entrenado, {state_id: label}) -- hmmlearn numera
    estados 0..n-1 sin ningún orden/significado inherente; el mapeo a
    'alcista'/'lateral'/'alta_volatilidad' se resuelve DESPUES de entrenar,
    mirando las medias ya ajustadas de cada estado (ver _label_hmm_states),
    para que el estado 0 no signifique algo distinto cada vez que se
    reentrena (p.ej. tras agregar más historia)."""
    if len(spy_log_returns) < HMM_MIN_TRAIN_ROWS:
        raise ValueError(
            f"HMM necesita >= {HMM_MIN_TRAIN_ROWS} ruedas de entrenamiento, vinieron {len(spy_log_returns)}"
        )
    X = np.column_stack([np.asarray(spy_log_returns, dtype=float), np.asarray(vix_series, dtype=float)])
    model = GaussianHMM(n_components=n_states, covariance_type="diag", n_iter=HMM_N_ITER, random_state=random_state)
    model.fit(X)
    state_labels = _label_hmm_states(model)
    return model, state_labels


def _label_hmm_states(model):
    """Etiqueta cada estado por sus propias medias ya ajustadas (model.means_,
    columnas [retorno_log, VIX]): el estado de mayor VIX medio es SIEMPRE
    'alta_volatilidad' (misma prioridad que classify_regime -- el pánico
    manda sobre la tendencia); de los 2 restantes, el de mayor retorno medio
    es 'alcista', el otro 'lateral' (consolidación)."""
    means = model.means_  # shape (n_states, 2) -> [:, 0]=retorno log medio, [:, 1]=VIX medio
    n_states = means.shape[0]
    order_by_vix_desc = np.argsort(-means[:, 1])
    crash_state = int(order_by_vix_desc[0])
    remaining = sorted((s for s in range(n_states) if s != crash_state), key=lambda s: -means[s, 0])

    labels = {crash_state: REGIME_ALTA_VOLATILIDAD}
    if remaining:
        labels[remaining[0]] = REGIME_ALCISTA
    for s in remaining[1:]:
        labels[s] = REGIME_LATERAL
    return labels


def classify_regime_hmm(model, state_labels, spy_log_returns_so_far, vix_series_so_far):
    """Clasifica la ÚLTIMA rueda de spy_log_returns_so_far/vix_series_so_far
    -- ambas deben venir YA CORTADAS hasta esa rueda (inclusive), la serie
    COMPLETA desde el inicio del entrenamiento, NUNCA una observación
    aislada: hmmlearn necesita la secuencia completa para que predict_proba
    aplique el algoritmo FORWARD (filtrado -- solo pasado+presente) en vez
    de forward-backward (que "suavizaría" usando datos posteriores DENTRO de
    la secuencia que se le pase, si se le pasara historia futura). Cero
    look-ahead idéntico al resto de este archivo (classify_regime recibe el
    mismo tipo de serie ya cortada).

    Devuelve (label, {label: probabilidad}) -- la probabilidad está
    agregada por LABEL (no por estado crudo), ya que _label_hmm_states
    puede mapear más de un estado al mismo label (p.ej. 2 estados
    'lateral' distintos)."""
    X = np.column_stack([
        np.asarray(spy_log_returns_so_far, dtype=float), np.asarray(vix_series_so_far, dtype=float),
    ])
    posteriors = model.predict_proba(X)[-1]
    label_probabilities = {}
    for state_id, proba in enumerate(posteriors):
        label = state_labels.get(state_id, REGIME_LATERAL)
        label_probabilities[label] = label_probabilities.get(label, 0.0) + float(proba)
    best_label = max(label_probabilities, key=label_probabilities.get)
    return best_label, label_probabilities


def hmm_position_scale(label_probabilities, min_scale=0.0):
    """Escala SUAVE de Position Sizing (pedido explícito: "en lugar de
    aplicar cortes rígidos de VIX", ver satellite_blocked arriba para el
    corte binario que esto reemplaza en la validación comparativa): el peso
    de la posición se multiplica por (1 - P(alta_volatilidad)) -- a mayor
    probabilidad de pánico, MENOR el tamaño de forma continua, nunca un
    apagado total salvo que esa probabilidad sea ~100%. min_scale: piso
    declarado (0.0 por defecto, se permite llegar a cero)."""
    crash_proba = label_probabilities.get(REGIME_ALTA_VOLATILIDAD, 0.0)
    return max(min_scale, 1.0 - crash_proba)


# --- Z-Score de CCL para modular exposición (ver conversación) -------------
#
# Señal MACRO adicional a VIX/HMM: un salto INUSUAL del dólar CCL (devaluación
# acelerándose, medida como z-score del retorno log diario -- MISMA formula
# que portfolio/features.py::_macro_exogenous_matrix usa para el meta-modelo,
# ver features._rolling_zscore, reusada por el caller en vez de duplicarse
# aca) es una señal de estrés especifica del mercado ARGENTINO que ni VIX
# (global) ni SPY>EMA50 (global) capturan. A diferencia de hmm_position_scale,
# esta es una señal SECUNDARIA -- nunca reduce la exposición a cero por si
# sola (min_scale por defecto 0.5), solo la atenua cuando el salto es
# genuinamente inusual (> CCL_ZSCORE_HIGH_THRESHOLD desvios de su propia
# volatilidad reciente, no cualquier variación diaria normal).
CCL_ZSCORE_HIGH_THRESHOLD = 2.0  # declarado -- +2 desvios de su propia vol reciente es un salto inusual, no una devaluacion gradual tipica
CCL_ZSCORE_MIN_SCALE = 0.5  # nunca baja de esto solo por el CCL -- señal secundaria, no reemplaza a VIX/HMM como gate principal


def ccl_exposure_scale(ccl_zscore, high_threshold=CCL_ZSCORE_HIGH_THRESHOLD, min_scale=CCL_ZSCORE_MIN_SCALE):
    """ccl_zscore: z-score YA RESUELTO del retorno log diario del CCL a la
    fecha a evaluar (ver portfolio/features.py::_rolling_zscore, calculado
    por el caller sobre la serie ya cortada -- cero look-ahead idéntico al
    resto de este módulo). None/NaN (sin cobertura de CCL para esa fecha) o
    <= high_threshold (salto normal, incluyendo cualquier z-score NEGATIVO --
    CCL estabilizándose o apreciándose) no modifican la exposición
    (scale=1.0). Por encima del umbral, escala LINEALMENTE desde 1.0 hasta
    min_scale, saturando en min_scale a partir de high_threshold + 2 desvios
    adicionales (declarado, no un ajuste fino calibrado)."""
    if ccl_zscore is None or not np.isfinite(ccl_zscore) or ccl_zscore <= high_threshold:
        return 1.0
    excess = ccl_zscore - high_threshold
    scale = 1.0 - min(1.0, excess / 2.0) * (1.0 - min_scale)
    return max(min_scale, scale)
