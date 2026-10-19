"""Cliente LOCAL (in-process, sin HTTP) hacia Chronos-Bolt -- MISMO contrato
que model/client.py::ChronosServiceClient (predict_samples(df, x_timestamp,
y_timestamp, pred_len) -> (mean_df, [sample_df, ...])) para poder usarse como
reemplazo directo en portfolio/backtest.py::simulate_core_engine sin tocar
esa funcion.

Para que: la validacion historica comparativa "Chronos Base vs Chronos LoRA +
HMM" (ver conversacion) necesita correr DOS pipelines Chronos-Bolt distintas
(la base y la que tiene el adaptador LoRA cargado) en la MISMA corrida, sin
levantar un segundo microservicio HTTP en otro puerto (mas simple, mas
seguro: nunca toca ni compite con el servicio real de :8001 que ya usan
Core/Satelite en produccion, ver services/predictor/main.py).

Post-procesamiento DUPLICADO a proposito de services/predictor/main.py
(_volatility_scale/_apply_band_scale/_dispersion_ratio) -- no se importa
porque ese archivo requiere fastapi, que vive solo en el venv separado de
services/predictor/ (ver meta_learner.py para la misma razon de separacion
de venvs). Declarado: si esa logica cambia en main.py, hay que replicar el
cambio aca para que la comparacion siga siendo justa (misma post-proceso en
ambos lados)."""
import numpy as np
import torch

from model.client import DEFAULT_QUANTILE_LEVELS, _build_ohlc_frame

MODEL_ID = "amazon/chronos-bolt-small"

# --- Duplicado de services/predictor/main.py (ver docstring de arriba) -----
CALIBRATION_SCALE = 2.144
VOL_WINDOW_RECENT = 30
VOL_WINDOW_BASELINE = 60
VOL_RATIO_MIN = 0.5
VOL_RATIO_MAX = 2.5
BAND_SCALE_MIN = 1.0
BAND_SCALE_MAX = 5.0


def _volatility_scale(context):
    closes = np.asarray(context, dtype=float)
    if len(closes) < 3 or np.any(closes <= 0):
        return CALIBRATION_SCALE
    returns = np.diff(closes) / closes[:-1]
    baseline_returns = returns[-min(len(returns), VOL_WINDOW_BASELINE):]
    recent_returns = returns[-min(len(returns), VOL_WINDOW_RECENT):]
    baseline_vol = float(np.std(baseline_returns))
    recent_vol = float(np.std(recent_returns))
    vol_ratio = 1.0 if baseline_vol <= 0 else recent_vol / baseline_vol
    vol_ratio = min(max(vol_ratio, VOL_RATIO_MIN), VOL_RATIO_MAX)
    return min(max(CALIBRATION_SCALE * vol_ratio, BAND_SCALE_MIN), BAND_SCALE_MAX)


def _apply_band_scale(quantiles_np, mean_np, scale):
    return mean_np[:, None] + (quantiles_np - mean_np[:, None]) * scale


def pick_device():
    if torch.cuda.is_available():
        return "cuda"
    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def _patch_chronos_embedding_methods(model_class):
    """Mismo parche que finetune/finetune_chronos.py::_patch_chronos_embedding_methods
    (ver ese docstring para el detalle) -- necesario aca tambien porque
    PeftModel.from_pretrained() dispara el mismo chequeo de pesos "tied" que
    get_peft_model()."""
    if not hasattr(model_class, "_kronos_embedding_patch_applied"):
        model_class.get_input_embeddings = lambda self: self.shared
        model_class.set_input_embeddings = lambda self, value: setattr(self, "shared", value)
        model_class._kronos_embedding_patch_applied = True


class LocalChronosClient:
    """Pipeline Chronos-Bolt cargada EN PROCESO (sin HTTP) -- opcionalmente
    con un adaptador LoRA (ver finetune/finetune_chronos.py) aplicado encima
    del modelo base. health()/predict_samples() mismo contrato que
    ChronosServiceClient, para ser un reemplazo directo del parametro
    `client` en portfolio/backtest.py::simulate_core_engine."""

    def __init__(self, lora_adapter_path=None, model_id=MODEL_ID, device=None):
        from chronos import BaseChronosPipeline

        self.device = device or pick_device()
        self.pipeline = BaseChronosPipeline.from_pretrained(
            model_id, device_map=self.device, torch_dtype=torch.float32,
        )
        self.lora_adapter_path = lora_adapter_path
        if lora_adapter_path:
            from peft import PeftModel
            _patch_chronos_embedding_methods(type(self.pipeline.model))
            self.pipeline.model = PeftModel.from_pretrained(self.pipeline.model, lora_adapter_path)
            self.pipeline.model.eval()

    def health(self):
        return {"available": True, "device": self.device, "model_id": MODEL_ID, "lora_adapter": self.lora_adapter_path}

    def predict_samples(self, df, x_timestamp, y_timestamp, pred_len, **_kronos_only_kwargs):
        """MISMA logica que services/predictor/main.py::predict (ver
        docstring del modulo: post-procesamiento duplicado a proposito)."""
        context = df["close"].to_numpy(dtype=float)
        context_tensor = torch.tensor(context, dtype=torch.float32)

        quantiles, mean = self.pipeline.predict_quantiles(
            inputs=context_tensor, prediction_length=pred_len, quantile_levels=DEFAULT_QUANTILE_LEVELS,
        )
        quantiles_np = quantiles[0].numpy()
        mean_np = mean[0].numpy()

        scale = _volatility_scale(context)
        quantiles_np = _apply_band_scale(quantiles_np, mean_np, scale)

        mean_df = _build_ohlc_frame(mean_np, y_timestamp)
        sample_dfs = [_build_ohlc_frame(quantiles_np[:, i], y_timestamp) for i in range(quantiles_np.shape[1])]
        return mean_df, sample_dfs

    def predict(self, df, x_timestamp, y_timestamp, pred_len, **kwargs):
        mean_df, _ = self.predict_samples(df, x_timestamp, y_timestamp, pred_len, **kwargs)
        return mean_df
