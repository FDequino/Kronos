"""Entorno Gymnasium para Asignacion Dinamica de Capital (Deep RL, ver
conversacion "Aprendizaje por Refuerzo") -- el agente aprende, dia a dia,
que fraccion del patrimonio poner en [Core %, Satelite %, Caucion %] en vez
de la regla ESTATICA fija (ver portfolio/config.py::DEFAULT_CORE_ALLOCATION_PCT,
20/80 default) que usa el sistema hoy.

Simplificacion DECLARADA de diseño, necesaria para que esto sea viable de
entrenar (ver conversacion): el entorno NO vuelve a simular Core/Satelite
dia a dia ni llama a Chronos-Bolt en cada step() -- eso ya lo hace
portfolio/backtest.py::simulate_core_engine/simulate_satellite_engine UNA
sola vez, igual que build_anchor_dataset corre Chronos una vez por anchor y
cachea el resultado (nunca por epoch de entrenamiento). Este modulo RECIBE
esas series de retorno diario YA SIMULADAS (ver __init__) y el agente solo
decide, dia a dia, CUANTO pesar cada sleeve -- no vuelve a decidir que
ticker comprar ni corre ningun modelo de nuevo. Sin esta simplificacion,
cada episodio de entrenamiento (PPO necesita miles de steps) dispararia
miles de llamadas a Chronos-Bolt, inviable en tiempo/costo (ver
portfolio/rl_trader.py, que precomputa todo esto UNA vez antes de entrenar).

Espacio de Observacion (pedido explicito, ver conversacion):
  [chronos_p50_return_pct, chronos_dispersion_ratio, vix_level,
   brecha_cambiaria_pct, atr_pct, current_position_pct]
Las primeras 5 son de MERCADO (ver portfolio/rl_trader.py::
build_market_observation_table, que las arma sobre un ticker representativo
del universo como proxy de "señal/volatilidad del dia" -- declarado como
simplificacion: el Core real mira TODO el universo cross-sectional, un
proxy de 1 ticker es mucho mas barato de precomputar y alcanza para darle
contexto al agente). current_position_pct es memoria PROPIA del agente (su
propio core_w del paso anterior), no una señal de mercado.

Espacio de Acciones: Box continuo de 3 valores -- [core_w, satellite_w,
caucion_w]. El pedido dejaba abierto "continuo/discreto": se elige
CONTINUO + normalizacion por softmax DENTRO de step() -- mas simple de
entrenar con la politica Gaussiana por defecto de PPO (stable-baselines3)
que un espacio discreto/categorico para una asignacion fraccionaria.
Declarado: no es la unica opcion valida, es la eleccion mas simple para
este alcance. El softmax garantiza que los 3 pesos SIEMPRE sumen 1 y sean
no-negativos, sin importar que vector crudo entregue la politica (incluso
fuera de rango durante la exploracion inicial de PPO).

Funcion de Recompensa (pedido explicito: "premiando Sharpe, penalizando Max
Drawdown"): reward_t = retorno_diario_pct_del_dia - DRAWDOWN_PENALTY_COEF *
max(0, drawdown_actual_pct), mas un bonus de Sharpe SOLO en el ultimo step
del episodio (ver step(), terminated=True) escalado por SHARPE_BONUS_SCALE.
Sharpe no es una cantidad "por dia" -- agregarlo solo al final evita premiar/
penalizar por un numero que todavia no tiene sentido estadistico a mitad de
camino. DRAWDOWN_PENALTY_COEF y SHARPE_BONUS_SCALE son constantes
DECLARADAS, NO optimizadas ni barridas -- el diseño de reward shaping para
RL de trading es una decision de modelado, no "la" formula correcta de
ningun paper (declarado explicitamente, mismo criterio de honestidad que el
resto de esta sesion: nunca se presenta un default sin pedido explicito
como si fuera una verdad objetiva)."""
import gymnasium as gym
import numpy as np
from gymnasium import spaces

DRAWDOWN_PENALTY_COEF = 2.0  # declarado, no barrido/optimizado
SHARPE_BONUS_SCALE = 1.0  # declarado, no barrido/optimizado
ANNUALIZATION_PERIODS = 252

OBS_MARKET_COLUMNS = [
    "chronos_p50_return_pct", "chronos_dispersion_ratio", "vix_level",
    "brecha_cambiaria_pct", "atr_pct",
]


class RLCapitalAllocationEnv(gym.Env):
    """Un episodio = recorrer, dia a dia, la ventana completa de
    market_df/retornos pasada a __init__ (tipicamente el bloque IN-SAMPLE o
    OUT-OF-SAMPLE de portfolio/backtest.py::IN_SAMPLE_DAYS/OUT_OF_SAMPLE_DAYS,
    ver portfolio/rl_trader.py). terminated=True al llegar al final de esa
    ventana (episodio de horizonte FIJO, no hay condicion de "quiebra"
    anticipada -- declarado, un stop-loss de cartera que termina el episodio
    antes no fue pedido)."""

    metadata = {"render_modes": []}

    def __init__(self, market_df, core_daily_return_pct, satellite_daily_return_pct, caucion_daily_return_pct):
        """market_df: DataFrame con columnas OBS_MARKET_COLUMNS, SIN NaN (ya
        resuelto por el caller -- este entorno no decide como rellenar datos
        faltantes, ver build_market_observation_table). Las 3 series de
        retorno: %/dia de cada sleeve, YA simuladas, MISMA longitud/orden
        que market_df (alineacion por POSICION, no por fecha -- el caller
        garantiza que ya vienen alineadas)."""
        super().__init__()
        n = len(market_df)
        if not (n == len(core_daily_return_pct) == len(satellite_daily_return_pct) == len(caucion_daily_return_pct)):
            raise ValueError(
                "market_df y las 3 series de retorno deben tener la misma longitud (mismas fechas, mismo orden)"
            )
        if n < 2:
            raise ValueError(f"el entorno necesita >= 2 ruedas para tener algun step, vinieron {n}")

        self.market_values = market_df[OBS_MARKET_COLUMNS].to_numpy(dtype=np.float32)
        self.core_return = np.asarray(core_daily_return_pct, dtype=np.float32)
        self.satellite_return = np.asarray(satellite_daily_return_pct, dtype=np.float32)
        self.caucion_return = np.asarray(caucion_daily_return_pct, dtype=np.float32)
        self.n_steps = n

        obs_dim = len(OBS_MARKET_COLUMNS) + 1  # +1 por current_position_pct (memoria del agente)
        self.observation_space = spaces.Box(low=-np.inf, high=np.inf, shape=(obs_dim,), dtype=np.float32)
        self.action_space = spaces.Box(low=0.0, high=1.0, shape=(3,), dtype=np.float32)

        self._idx = 0
        self._core_w = 0.0
        self._equity = 1.0
        self._peak_equity = 1.0
        self._episode_returns_pct = []

    def _observation(self):
        row = self.market_values[self._idx]
        return np.concatenate([row, np.array([self._core_w], dtype=np.float32)])

    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed)
        self._idx = 0
        self._core_w = 0.0
        self._equity = 1.0
        self._peak_equity = 1.0
        self._episode_returns_pct = []
        return self._observation(), {}

    @staticmethod
    def normalize_action(action):
        """Softmax -- ver docstring del modulo (Espacio de Acciones).
        Metodo estatico (no depende de self) para que tests/test_rl_env.py
        pueda probarlo en aislamiento sin instanciar un entorno completo."""
        action = np.asarray(action, dtype=np.float64)
        shifted = action - np.max(action)
        exp = np.exp(shifted)
        return exp / exp.sum()

    def step(self, action):
        if self._idx >= self.n_steps:
            raise RuntimeError("step() llamado despues de terminated=True -- hace falta reset()")

        weights = self.normalize_action(action)
        core_w, satellite_w, caucion_w = weights

        daily_return_pct = float(
            core_w * self.core_return[self._idx]
            + satellite_w * self.satellite_return[self._idx]
            + caucion_w * self.caucion_return[self._idx]
        )
        self._episode_returns_pct.append(daily_return_pct)

        self._equity *= (1.0 + daily_return_pct / 100.0)
        self._peak_equity = max(self._peak_equity, self._equity)
        drawdown_pct = (
            (self._peak_equity - self._equity) / self._peak_equity * 100.0 if self._peak_equity > 0 else 0.0
        )

        reward = daily_return_pct - DRAWDOWN_PENALTY_COEF * max(0.0, drawdown_pct)

        self._core_w = float(core_w)
        self._idx += 1
        terminated = self._idx >= self.n_steps
        truncated = False

        if terminated:
            returns = np.array(self._episode_returns_pct, dtype=np.float64) / 100.0
            sharpe = 0.0
            if len(returns) > 1 and returns.std(ddof=1) > 1e-9:
                sharpe = float(returns.mean() / returns.std(ddof=1) * np.sqrt(ANNUALIZATION_PERIODS))
            reward += SHARPE_BONUS_SCALE * sharpe
            # Observacion terminal: no hay un dia "siguiente" que observar --
            # se devuelve el vector en ceros (convencion estandar Gymnasium,
            # el algoritmo de RL nunca actua sobre esta observacion).
            obs = np.zeros(self.observation_space.shape, dtype=np.float32)
        else:
            obs = self._observation()

        info = {
            "weights": weights.tolist(), "daily_return_pct": daily_return_pct,
            "equity": self._equity, "drawdown_pct": drawdown_pct,
        }
        return obs, float(reward), bool(terminated), bool(truncated), info
