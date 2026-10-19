"""Microservicio FastAPI que sirve Chronos-Bolt para pronóstico de series de
tiempo -- reemplaza al Kronos autoregresivo (model/kronos.py) que antes
corría in-process dentro de Flask. Vive en un proceso aparte a propósito:
aísla el runtime de PyTorch/MPS de Flask (se puede reiniciar/escalar solo,
y un crash acá no tumba la webui), y es el único lugar de todo el repo que
importa torch/chronos.

Diferencias reales de contrato frente a Kronos, declaradas explícitas (nunca
simuladas como si el nuevo modelo hiciera lo mismo que el viejo):

1. Chronos-Bolt es un modelo de REGRESIÓN POR CUANTILES: una sola pasada
   hacia adelante devuelve, para cada paso futuro, un cuantil de la
   distribución condicional -- no es autoregresivo ni soporta muestreo con
   temperatura/top_k/top_p como Kronos (ver predict_quantiles). No hay
   "sample_count" real acá: lo que antes eran N muestreos independientes
   ahora son los N niveles de cuantil pedidos en una sola corrida (mucho más
   rápido, pero la semántica de "dispersión entre muestras" de Kronos pasa a
   ser "ancho de la banda de cuantiles" -- parecido en espíritu, no idéntico).
2. Chronos-Bolt es UNIVARIADO (una sola serie por pronóstico). A diferencia
   de Kronos (que modela open/high/low/close/volume/amount en conjunto),
   acá SOLO se pronostica 'close'. Reconstruir open/high/low para el candle
   predicho es responsabilidad de model/client.py (no de este servicio), y
   se hace explícito ahí que son sintéticos (open=high=low=close), nunca un
   pronóstico real de esas columnas.

Serialización de la GPU/MPS: un único asyncio.Lock protege la única
instancia de modelo cargada -- mismo criterio que ya usaba
portfolio/forecast_worker.py con su cola de un solo thread (un solo device,
correr 2 inferencias en paralelo no sería más rápido y podría pisarse).
"""
import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import Dict, List, Optional

import numpy as np
import torch
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger("predictor")
logging.basicConfig(level=logging.INFO)

MODEL_ID = os.environ.get("CHRONOS_MODEL_ID", "amazon/chronos-bolt-small")
# Horizonte máximo nativo de chronos-bolt-small -- no es un número arbitrario,
# es el prediction_length con el que se entrenó (pedir más degrada mucho la
# calidad; la propia librería lo valida si no se desactiva ese chequeo, acá
# se prefiere que el límite quede explícito en el contrato HTTP).
MAX_PREDICTION_LENGTH = 64

_state = {"pipeline": None, "device": None}

# Calibracion de ancho de banda: Chronos-Bolt sale de fabrica sobreconfiado
# para esta cartera -- un backtest real sobre GGAL (210 forecasts,
# model_key=chronos-bolt-small, ver portfolio/accuracy.py::compute_calibration)
# mostro que el intervalo p10-p90 (nominalmente un 80%) contenia el precio
# real solo ~45% de las veces. CALIBRATION_SCALE es el ensanche necesario
# para que, asumiendo errores aproximadamente normales, esa banda pase de
# cubrir 45% a cubrir el 80% nominal: z(0.90) / z_equivalente(0.45) =
# 1.2816 / 0.5978 ~= 2.144. Es una correccion GLOBAL derivada de datos reales
# (no un numero inventado), pero sigue siendo una aproximacion gaussiana de
# un solo backtest -- recalcular si cambia el modelo o se junta mas
# evidencia de calibracion (ver services/predictor/README.md).
CALIBRATION_SCALE = 2.144

# Encima de la correccion global, un escalado proporcional a cuanto mas
# volatil vino el activo en las ultimas velas respecto a su propia historia
# disponible -- un activo que viene moviendose mas de lo usual necesita una
# banda mas ancha que uno tranquilo, incluso con la misma correccion de base.
VOL_WINDOW_RECENT = 30
VOL_WINDOW_BASELINE = 60
VOL_RATIO_MIN = 0.5
VOL_RATIO_MAX = 2.5
BAND_SCALE_MIN = 1.0  # nunca angostar lo que el modelo ya devuelve
BAND_SCALE_MAX = 5.0  # techo para no disparar la banda en contextos degenerados


def _volatility_scale(context):
    """Factor de ensanche de banda para esta corrida puntual: CALIBRATION_SCALE
    modulado por la volatilidad realizada reciente del activo (ultimas
    VOL_WINDOW_RECENT velas) contra su propia volatilidad de base (ultimas
    VOL_WINDOW_BASELINE). Solo usa 'close' -- el contrato de este endpoint no
    recibe high/low (ver PredictRequest), asi que no hay forma de calcular un
    ATR real; se declara la aproximacion en vez de simular un ATR que no se
    tiene."""
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
    """Ensancha cada cuantil `scale` veces mas lejos de la media, en la MISMA
    direccion en la que ya estaba -- preserva el orden entre cuantiles (nunca
    se cruzan) y deja sin mover cualquier cuantil que ya coincida con la
    media (ej. la mediana, si quantile_levels pide 0.5)."""
    return mean_np[:, None] + (quantiles_np - mean_np[:, None]) * scale


def _dispersion_ratio(context, mean_np, quantiles_np, quantile_levels):
    """Que tan grande es el movimiento proyectado (media, ultimo paso del
    horizonte) frente al ancho de banda YA ESCALADO del propio modelo en ese
    mismo paso -- un numero bajo dice "esta proyeccion es chica comparada con
    lo incierta que es". Deliberadamente generico (ninguna nocion de
    "comprar/vender/mantener" vive aca): ese umbral es de cartera, no del
    modelo -- ver portfolio/signals.py::compute_signal, que aplica su propio
    criterio sobre un ratio equivalente calculado con su propia config."""
    last_close = float(context[-1])
    if last_close == 0:
        return None
    projected_move_pct = abs(float(mean_np[-1]) - last_close) / last_close * 100

    levels = np.asarray(quantile_levels)
    low_idx = int(np.argmin(levels))
    high_idx = int(np.argmax(levels))
    band_width_pct = abs(quantiles_np[-1, high_idx] - quantiles_np[-1, low_idx]) / last_close * 100
    if band_width_pct <= 0:
        return None
    return round(projected_move_pct / band_width_pct, 4)


def pick_device():
    """Mismo orden de preferencia que model/runtime.py (ya retirado) --
    cuda > mps > cpu. En esta máquina (Apple Silicon) el camino real es mps."""
    if torch.cuda.is_available():
        return "cuda"
    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def _load_pipeline():
    from chronos import BaseChronosPipeline

    device = pick_device()
    logger.info("Cargando %s en device=%s", MODEL_ID, device)
    pipeline = BaseChronosPipeline.from_pretrained(
        MODEL_ID,
        device_map=device,
        torch_dtype=torch.float32,
    )
    logger.info("Modelo cargado (%s).", MODEL_ID)
    return pipeline, device


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Carga al arrancar (no perezosa): un health-check inmediatamente después
    # de levantar el proceso ya refleja el estado real, en vez de reportar
    # "disponible" antes de haber intentado cargar los pesos.
    try:
        pipeline, device = _load_pipeline()
        _state["pipeline"] = pipeline
        _state["device"] = device
    except Exception:
        logger.exception("No se pudo cargar %s -- /health reportará available=false.", MODEL_ID)
    yield
    _state["pipeline"] = None


app = FastAPI(title="Chronos-Bolt Predictor Service", lifespan=lifespan)

# Un único lock de proceso: todas las corridas de /predict pasan por acá antes
# de tocar el modelo. asyncio.Lock (no threading.Lock) porque el endpoint es
# async y la espera debe ceder el loop de eventos, no bloquearlo.
_inference_lock = asyncio.Lock()


class PredictRequest(BaseModel):
    context: List[float] = Field(..., min_length=8, description="Serie histórica (closes), orden cronológico, sin huecos.")
    prediction_length: int = Field(..., gt=0, le=MAX_PREDICTION_LENGTH)
    quantile_levels: List[float] = Field(
        default=[0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9],
        description="Niveles de cuantil a devolver -- reemplazan al 'sample_count' de Kronos.",
    )
    # Aceptado por compatibilidad de contrato hacia adelante, TODAVIA NO
    # CONSUMIDO -- declarado explicito en vez de fingir soporte. Verificado
    # contra la libreria real instalada: chronos.BaseChronosPipeline.
    # predict_quantiles(inputs, prediction_length, quantile_levels) solo
    # acepta una serie 1D o un batch de series 1D INDEPENDIENTES (el
    # "batch_size" de la firma es "N series sin relacion entre si", nunca "N
    # canales/covariables de la MISMA serie") -- Chronos-Bolt es un
    # transformer univariado de fabrica, no tiene forma arquitectonica de
    # consumir covariables dinamicas, a diferencia de modelos tipo DeepAR/TFT
    # que si reciben un past_feat_dynamic_real. Si se manda este campo, la
    # respuesta lo refleja en covariates_ignored (ver PredictResponse) en vez
    # de descartarlo en silencio. La fusion multivariada real para este
    # pipeline pasa por el meta-modelo tabular (services/predictor/
    # meta_learner.py + portfolio/features.py::build_multivariate_dataframe),
    # que si puede sumar estas columnas como features de la segunda capa.
    past_feat_dynamic_real: Optional[Dict[str, List[float]]] = Field(
        default=None,
        description=(
            "Covariables dinamicas pasadas (ej. {'spy_close': [...], 'ccl_usd': [...]}), "
            "misma longitud que context. NO se usan todavia para la inferencia -- ver nota "
            "arriba y covariates_ignored en la respuesta."
        ),
    )


class PredictResponse(BaseModel):
    mean: List[float]
    quantile_levels: List[float]
    quantiles: List[List[float]]  # shape (num_quantiles, prediction_length), uno por quantile_level
    device: str
    model_id: str
    # Ya incluye la calibracion de banda (ver CALIBRATION_SCALE/_volatility_scale) --
    # None si last_close es 0 o el ancho de banda escalado da 0 (degenerado).
    dispersion_ratio: Optional[float] = None
    # Nombres de las claves de past_feat_dynamic_real que se recibieron pero
    # NO se usaron para esta inferencia (ver nota en PredictRequest) -- lista
    # vacia si no se mando el campo. Señal honesta en vez de silencio: un
    # caller que manda covariables esperando que importen se entera en la
    # misma respuesta de que no importaron.
    covariates_ignored: List[str] = Field(default_factory=list)


@app.get("/health")
def health():
    pipeline = _state["pipeline"]
    return {
        "available": pipeline is not None,
        "device": _state["device"],
        "model_id": MODEL_ID,
        "message": "Modelo cargado y disponible." if pipeline is not None else "Modelo no disponible (falló la carga al arrancar).",
    }


@app.post("/predict", response_model=PredictResponse)
async def predict(req: PredictRequest):
    pipeline = _state["pipeline"]
    if pipeline is None:
        raise HTTPException(503, "El modelo todavía no está cargado (ver /health).")

    context_tensor = torch.tensor(req.context, dtype=torch.float32)

    async with _inference_lock:
        # predict_quantiles es síncrono/bloqueante (CPU o MPS) -- a un hilo
        # aparte para no congelar el event loop mientras se sostiene el lock
        # (otros requests, p.ej. /health, siguen respondiendo mientras tanto).
        quantiles, mean = await asyncio.to_thread(
            pipeline.predict_quantiles,
            inputs=context_tensor,
            prediction_length=req.prediction_length,
            quantile_levels=req.quantile_levels,
        )

    # quantiles: (1, prediction_length, num_quantiles) ; mean: (1, prediction_length)
    quantiles_np = quantiles[0].numpy()
    mean_np = mean[0].numpy()

    scale = _volatility_scale(req.context)
    quantiles_np = _apply_band_scale(quantiles_np, mean_np, scale)
    dispersion_ratio = _dispersion_ratio(req.context, mean_np, quantiles_np, req.quantile_levels)

    covariates_ignored = sorted(req.past_feat_dynamic_real) if req.past_feat_dynamic_real else []
    if covariates_ignored:
        logger.warning(
            "past_feat_dynamic_real recibido (%s) pero Chronos-Bolt es univariado -- "
            "se ignora para esta inferencia (ver nota en PredictRequest).",
            ", ".join(covariates_ignored),
        )

    return PredictResponse(
        mean=mean_np.tolist(),
        quantile_levels=req.quantile_levels,
        quantiles=quantiles_np.T.tolist(),
        device=_state["device"],
        model_id=MODEL_ID,
        dispersion_ratio=dispersion_ratio,
        covariates_ignored=covariates_ignored,
    )
