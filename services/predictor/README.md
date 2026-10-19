# Predictor service (Chronos-Bolt)

Microservicio FastAPI que sirve `amazon/chronos-bolt-small` para pronóstico
de series de tiempo. Reemplaza a Kronos (antes corrido in-process dentro de
Flask, ver `model/kronos.py` + `model/runtime.py`) como motor predictivo de
`portfolio/forecasting.py`, consumido vía `model/client.py::ChronosServiceClient`.

## Instalación

Entorno virtual propio, separado del de la app Flask (este servicio importa
`torch`/`chronos-forecasting`, pesado, y no tiene por qué compartir proceso
ni dependencias con Flask):

```bash
cd services/predictor
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Correr

```bash
uvicorn main:app --host 127.0.0.1 --port 8000
```

**Puerto por defecto (8000)**: en esta máquina ya hay otro proceso
(Docker Desktop) escuchando en `127.0.0.1:8000` -- confirmado con
`lsof -nP -iTCP:8000 -sTCP:LISTEN` al implementar esto. Elegí otro puerto
libre (ej. `--port 8001`) o liberá el 8000, y actualizá `PREDICTOR_SERVICE_URL`
del lado de Flask para que coincida (ver abajo).

La primera carga descarga los pesos de `amazon/chronos-bolt-small` desde
Hugging Face Hub (requiere red la primera vez; quedan cacheados localmente
después). El dispositivo se elige automáticamente: `cuda` > `mps` > `cpu`
(`torch.backends.mps.is_available()` en Apple Silicon).

## Variables de entorno

- `CHRONOS_MODEL_ID` (default `amazon/chronos-bolt-small`) -- qué checkpoint cargar.
- Del lado de Flask: `PREDICTOR_SERVICE_URL` (default `http://127.0.0.1:8000`)
  le dice a `model/client.py::ChronosServiceClient` dónde está este servicio.

## Endpoints

- `GET /health` -- `{available, device, model_id, message}`.
- `POST /predict` -- body `{context: [float], prediction_length: int, quantile_levels?: [float]}`,
  devuelve `{mean, quantile_levels, quantiles, device, model_id}`.
