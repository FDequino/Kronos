"""Cliente HTTP hacia el microservicio de inferencia (services/predictor) --
reemplaza a model/runtime.py + KronosPredictor (model/kronos.py) como la
forma en que portfolio/forecasting.py obtiene un pronóstico. Vive en model/
(no en portfolio/) para mantener la misma frontera que ya existía: "model"
es la capa que sabe de ML, "portfolio" es la que sabe de la cartera -- acá
adentro ya no hay ni una línea de torch/chronos, solo HTTP + pandas.

Contrato de salida: predict_samples() devuelve (mean_df, [sample_df, ...]),
MISMA forma que ya devolvía KronosPredictor.predict_samples() -- ni
portfolio/signals.py ni portfolio/forecasting.py se enteran del cambio de
modelo.

Dos diferencias reales frente a Kronos, declaradas (no escondidas, ver
también services/predictor/main.py):
- Chronos-Bolt es univariado (solo pronostica 'close'). open/high/low del
  candle predicho se completan = close (una vela plana), nunca un rango que
  el modelo no predijo -- ver _build_ohlc_frame.
- Los "sample_dfs" ya no son trayectorias de un muestreo autoregresivo
  independiente (Chronos-Bolt no muestrea, es regresión por cuantiles en una
  sola pasada) -- son, por cada nivel de cuantil pedido, el camino temporal
  completo de ESE cuantil. Aguas abajo (portfolio/signals.py::compute_signal)
  se interpreta igual que antes (dispersión entre "muestras" = incertidumbre),
  pero la fuente estadística es distinta: cuantiles de una distribución
  condicional, no corridas independientes del modelo.
"""
import os

import numpy as np
import pandas as pd
import requests

DEFAULT_SERVICE_URL = "http://127.0.0.1:8000"

# Reemplazan al "sample_count" de Kronos -- con Chronos-Bolt pedir más
# cuantiles no cuesta una corrida extra del modelo (es una sola pasada), así
# que no hace falta que el llamador elija cuántos: siempre se piden estos 9.
DEFAULT_QUANTILE_LEVELS = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]


class ChronosServiceError(RuntimeError):
    """El microservicio de predicción no respondió o devolvió un error."""


def _build_ohlc_frame(close_values, y_timestamp):
    """Arma un DataFrame open/high/low/close/volume/amount a partir de una
    serie de 'close' pronosticada. Chronos-Bolt es univariado -- no pronostica
    open/high/low por separado, así que se completan con el mismo valor de
    close (vela plana) en vez de inventar un rango que el modelo nunca
    predijo. volume/amount quedan en 0 por el mismo motivo (no pronosticados)."""
    close_values = np.asarray(close_values, dtype=float)
    return pd.DataFrame(
        {
            "open": close_values,
            "high": close_values,
            "low": close_values,
            "close": close_values,
            "volume": 0.0,
            "amount": 0.0,
        },
        index=y_timestamp,
    )


class ChronosServiceClient:
    def __init__(self, base_url=None, timeout=120):
        self.base_url = (base_url or os.environ.get("PREDICTOR_SERVICE_URL") or DEFAULT_SERVICE_URL).rstrip("/")
        self.timeout = timeout

    def health(self):
        try:
            resp = requests.get(f"{self.base_url}/health", timeout=10)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            raise ChronosServiceError(f"No se pudo contactar al servicio de predicción en {self.base_url}: {e}") from e

    def _request_predict(self, context_values, prediction_length, quantile_levels):
        payload = {
            "context": [float(v) for v in context_values],
            "prediction_length": int(prediction_length),
            "quantile_levels": quantile_levels,
        }
        try:
            resp = requests.post(f"{self.base_url}/predict", json=payload, timeout=self.timeout)
        except requests.RequestException as e:
            raise ChronosServiceError(f"No se pudo contactar al servicio de predicción en {self.base_url}: {e}") from e
        if resp.status_code != 200:
            raise ChronosServiceError(f"El servicio de predicción devolvió {resp.status_code}: {resp.text}")
        return resp.json()

    def predict_samples(self, df, x_timestamp, y_timestamp, pred_len, **_kronos_only_kwargs):
        """Mismo shape de retorno que KronosPredictor.predict_samples():
        (mean_df, [sample_df, ...]), ambos indexados por y_timestamp, con
        columnas open/high/low/close/volume/amount.

        _kronos_only_kwargs absorbe T/top_p/top_k/sample_count/verbose --
        parámetros de muestreo de Kronos sin equivalente en una regresión por
        cuantiles. Se ignoran a propósito (nunca se simula un muestreo que no
        existe); quedan en la firma solo para no romper call-sites viejos."""
        context_values = df["close"].to_numpy()
        data = self._request_predict(context_values, pred_len, DEFAULT_QUANTILE_LEVELS)

        mean_df = _build_ohlc_frame(data["mean"], y_timestamp)
        sample_dfs = [_build_ohlc_frame(quantile_path, y_timestamp) for quantile_path in data["quantiles"]]
        return mean_df, sample_dfs

    def predict(self, df, x_timestamp, y_timestamp, pred_len, **kwargs):
        """Variante sin distribución -- solo el pronóstico puntual (mean)."""
        mean_df, _ = self.predict_samples(df, x_timestamp, y_timestamp, pred_len, **kwargs)
        return mean_df
