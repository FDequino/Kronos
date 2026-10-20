"""Flask-independent runtime state for the loaded Kronos model.

Lets portfolio/forecast_worker.py reuse whatever model is loaded without
importing anything from webui/, and without reloading weights per forecast.

NOTA (migración a Chronos-Bolt): el motor predictivo de la cartera real
(portfolio/forecasting.py) ya NO usa este módulo -- corre en un microservicio
aparte (services/predictor/) consumido vía model/client.py::ChronosServiceClient.
Este archivo sigue vivo solo para el demo standalone de Kronos (rutas
/api/predict, /api/load-model, /api/model-status, /api/available-models en
webui/app.py), que todavía carga Kronos in-process a propósito -- no se tocó
porque no era parte de lo pedido en la migración y seguía funcionando.
Nunca carga pesos al importar este módulo ni al arrancar Flask: load_model()/
ensure_loaded() son perezosos, solo se disparan si alguien pide explícitamente
el demo viejo.
"""
import torch

try:
    from model import Kronos, KronosTokenizer, KronosPredictor
    MODEL_AVAILABLE = True
except ImportError:
    MODEL_AVAILABLE = False

AVAILABLE_MODELS = {
    'kronos-mini': {
        'name': 'Kronos-mini',
        'model_id': 'NeoQuasar/Kronos-mini',
        'tokenizer_id': 'NeoQuasar/Kronos-Tokenizer-2k',
        'context_length': 2048,
        'params': '4.1M',
        'description': 'Modelo liviano, para predicciones rápidas',
    },
    'kronos-small': {
        'name': 'Kronos-small',
        'model_id': 'NeoQuasar/Kronos-small',
        'tokenizer_id': 'NeoQuasar/Kronos-Tokenizer-base',
        'context_length': 512,
        'params': '24.7M',
        'description': 'Modelo chico, equilibrio entre precisión y velocidad',
    },
    'kronos-base': {
        'name': 'Kronos-base',
        'model_id': 'NeoQuasar/Kronos-base',
        'tokenizer_id': 'NeoQuasar/Kronos-Tokenizer-base',
        'context_length': 512,
        'params': '102.3M',
        'description': 'Modelo base, mejor calidad de predicción',
    },
}

_predictor = None
_current_model_key = None


def pick_device():
    if torch.cuda.is_available():
        return "cuda:0"
    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def load_model(model_key, device=None):
    """Loads (or reloads) the given model and makes it the active one."""
    global _predictor, _current_model_key

    if not MODEL_AVAILABLE:
        raise RuntimeError("La librería del modelo Kronos no está disponible")
    if model_key not in AVAILABLE_MODELS:
        raise ValueError(f"Modelo no soportado: {model_key}")

    config = AVAILABLE_MODELS[model_key]
    device = device or pick_device()

    tokenizer = KronosTokenizer.from_pretrained(config["tokenizer_id"])
    model = Kronos.from_pretrained(config["model_id"])
    _predictor = KronosPredictor(model, tokenizer, device=device, max_context=config["context_length"])
    _current_model_key = model_key
    return config


def ensure_loaded(model_key="kronos-base", device=None):
    """Lazily loads model_key if nothing (or a different model) is loaded yet."""
    if _predictor is None or _current_model_key != model_key:
        load_model(model_key, device)
    return _predictor


def get_predictor():
    return _predictor


def status():
    if not MODEL_AVAILABLE:
        return {
            "available": False,
            "loaded": False,
            "message": "La librería del modelo Kronos no está disponible, instalá las dependencias",
        }
    if _predictor is None:
        return {"available": True, "loaded": False, "message": "Modelo Kronos disponible pero no cargado"}
    return {
        "available": True,
        "loaded": True,
        "message": "Modelo Kronos cargado y disponible",
        "current_model": {
            "name": _predictor.model.__class__.__name__,
            "device": str(next(_predictor.model.parameters()).device),
        },
    }
