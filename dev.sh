#!/usr/bin/env bash
# Levanta el stack de desarrollo completo: el microservicio predictor
# (Chronos-Bolt, FastAPI/Uvicorn) en :8001 y la app Flask (webui) en :7070,
# con PREDICTOR_SERVICE_URL ya apuntado del segundo al primero.
#
# Ctrl+C mata ambos procesos (y sus árboles de hijos, por el reloader de
# Flask debug=True que spawnea un subproceso propio).
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREDICTOR_DIR="$ROOT_DIR/services/predictor"
PREDICTOR_VENV="$PREDICTOR_DIR/.venv"
FLASK_VENV="$ROOT_DIR/.venv"
PREDICTOR_PORT="${PREDICTOR_PORT:-8001}"
# Fijo: webui/app.py hardcodea port=7070 en su propio app.run(), no lee env
# var -- esta constante es solo para los mensajes/health-check de este script.
FLASK_PORT=7070

LOG_DIR="$ROOT_DIR/.devlogs"
mkdir -p "$LOG_DIR"
PREDICTOR_LOG="$LOG_DIR/predictor.log"
FLASK_LOG="$LOG_DIR/webui.log"

if [ ! -x "$PREDICTOR_VENV/bin/uvicorn" ]; then
    echo "No encuentro uvicorn en $PREDICTOR_VENV/bin -- corré primero:" >&2
    echo "  cd services/predictor && python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt" >&2
    exit 1
fi

if [ ! -x "$FLASK_VENV/bin/python3" ]; then
    echo "No encuentro $FLASK_VENV/bin/python3 -- corré primero:" >&2
    echo "  python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt" >&2
    exit 1
fi

PIDS=()

cleanup() {
    echo ""
    echo "Cerrando servicios..."
    for pid in "${PIDS[@]:-}"; do
        if kill -0 "$pid" 2>/dev/null; then
            # Grupo de proceso completo (el reloader de Flask spawnea un hijo
            # con WERKZEUG_RUN_MAIN=true) -- matar solo el PID guardado deja
            # al hijo real sirviendo huérfano en el puerto.
            kill -TERM "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
        fi
    done
    wait 2>/dev/null || true
    echo "Listo."
}
trap cleanup INT TERM EXIT

echo "Iniciando predictor (Chronos-Bolt) en :$PREDICTOR_PORT -- log en $PREDICTOR_LOG"
(
    cd "$PREDICTOR_DIR"
    source "$PREDICTOR_VENV/bin/activate"
    exec uvicorn main:app --host 127.0.0.1 --port "$PREDICTOR_PORT"
) >"$PREDICTOR_LOG" 2>&1 &
PREDICTOR_PID=$!
PIDS+=("$PREDICTOR_PID")

echo "Esperando a que el predictor responda en /health..."
for _ in $(seq 1 60); do
    if curl -fsS "http://127.0.0.1:$PREDICTOR_PORT/health" >/dev/null 2>&1; then
        echo "Predictor arriba (pid $PREDICTOR_PID)."
        break
    fi
    sleep 1
done

echo "Iniciando Flask (webui) en :$FLASK_PORT -- log en $FLASK_LOG"
(
    cd "$ROOT_DIR"
    source "$FLASK_VENV/bin/activate"
    export PREDICTOR_SERVICE_URL="http://127.0.0.1:$PREDICTOR_PORT"
    exec python3 webui/app.py
) >"$FLASK_LOG" 2>&1 &
FLASK_PID=$!
PIDS+=("$FLASK_PID")

echo ""
echo "Servicios corriendo -- Ctrl+C para cortar todo."
echo "  Predictor: http://127.0.0.1:$PREDICTOR_PORT  (pid $PREDICTOR_PID, log: $PREDICTOR_LOG)"
echo "  Webui:     http://127.0.0.1:$FLASK_PORT       (pid $FLASK_PID, log: $FLASK_LOG)"

wait
