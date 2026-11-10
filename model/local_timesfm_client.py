"""Cliente LOCAL (in-process, sin HTTP) hacia TimesFM-2.5 -- MISMO contrato
que model/client.py::ChronosServiceClient / model/local_client.py::LocalChronosClient
(predict_samples(df, x_timestamp, y_timestamp, pred_len) -> (mean_df,
[sample_df, ...])) para poder usarse como reemplazo directo del parametro
`client` en portfolio/backtest.py::simulate_core_engine, sin tocar esa
funcion -- mismo patron que LocalChronosClient uso para comparar Chronos
Base vs LoRA.

Para que (ver OBJETIVO_MARCO.md, "Como hacer que el resto funcione" /
Investigacion 2026-11-10): examples/research_timesfm_vs_chronos.py ya
comparo el error de punto de TimesFM-2.5 contra Chronos-Bolt en 4 tickers
sueltos (sin ventaja clara, ver ese docstring) pero NO corrio TimesFM dentro
del motor Core completo (Triple Barrier + Meta-Labeling + CPCV/DSR) -- esto
cierra esa brecha: reemplaza el forecaster, deja intacta toda la logica de
features/labeling/validacion de signals.py/backtest.py.

Post-procesamiento DUPLICADO A PROPOSITO de model/local_client.py
(_volatility_scale/_apply_band_scale, reusadas tal cual importandolas de ahi
-- nunca reimplementadas) para que la UNICA diferencia real entre esta
corrida y la de Chronos sea el modelo de forecasting, no el calibrado de
bandas."""
import numpy as np

from model.client import DEFAULT_QUANTILE_LEVELS, _build_ohlc_frame
from model.local_client import _apply_band_scale, _volatility_scale

MODEL_ID = "google/timesfm-2.5-200m-pytorch"

# TimesFM-2.5 devuelve 10 columnas de cuantil -- la col. 0 no es parte de
# list[0.1..0.9] de su propia config (quantiles = [0.1,...,0.9], 9 niveles,
# decode_index=5), confirmado empiricamente (ver conversacion/auditoria
# 2026-11-10): quant[..., 5] == point_forecast exactamente (decode_index=5 ==
# la mediana/0.5). Las columnas 1..9 SI son los 9 niveles de
# DEFAULT_QUANTILE_LEVELS en orden creciente -- se descarta la columna 0
# (no corresponde a ningun nivel de DEFAULT_QUANTILE_LEVELS).
_QUANTILE_COLUMNS = slice(1, 10)


class LocalTimesFMClient:
    """health()/predict_samples() mismo contrato que ChronosServiceClient --
    reemplazo directo del parametro `client` en simulate_core_engine."""

    def __init__(self, model_id=MODEL_ID, max_context=1024, max_horizon=256):
        import timesfm

        self.model_id = model_id
        self.model = timesfm.TimesFM_2p5_200M_torch.from_pretrained(model_id)
        self.model.compile(timesfm.ForecastConfig(
            max_context=max_context, max_horizon=max_horizon,
            normalize_inputs=True, use_continuous_quantile_head=True,
        ))

    def health(self):
        return {"available": True, "model_id": self.model_id}

    def predict_samples(self, df, x_timestamp, y_timestamp, pred_len, **_chronos_only_kwargs):
        context = df["close"].to_numpy(dtype=float)
        point, quant = self.model.forecast(horizon=pred_len, inputs=[context])
        mean_np = point[0]
        quantiles_np = quant[0][:, _QUANTILE_COLUMNS]
        assert quantiles_np.shape[1] == len(DEFAULT_QUANTILE_LEVELS), (
            f"se esperaban {len(DEFAULT_QUANTILE_LEVELS)} cuantiles, TimesFM devolvio {quantiles_np.shape[1]}"
        )

        scale = _volatility_scale(context)
        quantiles_np = _apply_band_scale(quantiles_np, mean_np, scale)

        mean_df = _build_ohlc_frame(mean_np, y_timestamp)
        sample_dfs = [_build_ohlc_frame(quantiles_np[:, i], y_timestamp) for i in range(quantiles_np.shape[1])]
        return mean_df, sample_dfs

    def predict(self, df, x_timestamp, y_timestamp, pred_len, **kwargs):
        mean_df, _ = self.predict_samples(df, x_timestamp, y_timestamp, pred_len, **kwargs)
        return mean_df
