#!/usr/bin/env bash
# Deploy a producción con gate de tests + rollback automático (ver
# conversación: "script de deploy con gate de tests y rollback" -- antes
# esto era rsync+chown+restart a mano, sin red de seguridad si algo salía
# mal). Mismo criterio que ya usa el deploy de deglia-bi en este mismo VPS
# (ver conversación/sesión del 2026-10-21): nunca restart sin healthcheck,
# nunca un "DEPLOY OK" sobre código roto.
#
# Pasos, en orden -- cualquiera que falle aborta ANTES del siguiente:
#   1. pytest tests/ local (nunca se toca el VPS con tests rojos).
#   2. Snapshot de portfolio.db + del código actual del VPS (para rollback).
#   3. rsync portfolio/, webui/, deploy/, tests/ (nunca portfolio.db ni
#      archivos generados/subidos por el usuario).
#   4. py_compile de todo .py en el VPS (sintaxis valida ANTES de reiniciar).
#   5. Instala deploy/kronos.service si cambió, daemon-reload.
#   6. Restart + healthcheck real (HTTP 200/302, nunca solo "active").
#   7. Si el healthcheck falla: restaura el código Y el unit de systemd del
#      snapshot, reinicia, y sale con error -- nunca deja el VPS a medio
#      actualizar sin avisar.
#
# Uso: deploy/deploy.sh (desde la raíz del repo). Variables de entorno
# opcionales: KRONOS_VPS_HOST (default root@212.85.17.7),
# KRONOS_VPS_APP_DIR (default /opt/kronos/app).
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

VPS_HOST="${KRONOS_VPS_HOST:-root@212.85.17.7}"
APP_DIR="${KRONOS_VPS_APP_DIR:-/opt/kronos/app}"
HEALTH_URL="http://127.0.0.1:8090/"
TS="$(date +%Y%m%d_%H%M%S)"
BACKUP_DIR="/opt/kronos/backups/pre_deploy_${TS}"

echo "== 1/7 Tests locales =="
if ! .venv/bin/python -m pytest tests/ -q; then
    echo "ABORTADO: tests rojos -- nunca se toca el VPS con esto." >&2
    exit 1
fi

echo "== 2/7 Snapshot de respaldo en el VPS ($BACKUP_DIR) =="
ssh "$VPS_HOST" "set -e
    mkdir -p '$BACKUP_DIR'
    cp '$APP_DIR/portfolio/portfolio.db' '$BACKUP_DIR/portfolio.db'
    tar -C '$APP_DIR' -czf '$BACKUP_DIR/code_snapshot.tar.gz' portfolio webui deploy 2>/dev/null || true
    cp /etc/systemd/system/kronos.service '$BACKUP_DIR/kronos.service.bak' 2>/dev/null || true
"

echo "== 3/7 rsync del código =="
rsync -avz --no-times \
    --exclude 'portfolio.db*' --exclude '__pycache__/' --exclude '*.pyc' --exclude 'loan_documents/' \
    portfolio/ "$VPS_HOST:$APP_DIR/portfolio/"
rsync -avz --no-times \
    --exclude '__pycache__/' --exclude '*.pyc' --exclude 'prediction_results/' \
    webui/ "$VPS_HOST:$APP_DIR/webui/"
rsync -avz --no-times deploy/ "$VPS_HOST:$APP_DIR/deploy/"
rsync -avz --no-times --exclude '__pycache__/' --exclude '*.pyc' tests/ "$VPS_HOST:$APP_DIR/tests/"
ssh "$VPS_HOST" "chown -R kronos:kronos '$APP_DIR/portfolio' '$APP_DIR/webui' '$APP_DIR/deploy' '$APP_DIR/tests'"

_rollback() {
    echo "!! Restaurando snapshot previo ($BACKUP_DIR) y reiniciando..." >&2
    ssh "$VPS_HOST" "set -e
        tar -C '$APP_DIR' -xzf '$BACKUP_DIR/code_snapshot.tar.gz'
        chown -R kronos:kronos '$APP_DIR/portfolio' '$APP_DIR/webui' '$APP_DIR/deploy'
        if [ -f '$BACKUP_DIR/kronos.service.bak' ]; then
            cp '$BACKUP_DIR/kronos.service.bak' /etc/systemd/system/kronos.service
            systemctl daemon-reload
        fi
        systemctl restart kronos
    "
    echo "!! Rollback aplicado -- el VPS quedó en el código de ANTES de este deploy." >&2
    exit 1
}

echo "== 4/7 py_compile en el VPS =="
if ! ssh "$VPS_HOST" "sudo -u kronos '$APP_DIR/.venv/bin/python' -m py_compile '$APP_DIR'/portfolio/*.py '$APP_DIR'/webui/*.py '$APP_DIR'/deploy/*.py"; then
    echo "ABORTADO: no compila en el VPS." >&2
    _rollback
fi

echo "== 5/7 systemd unit =="
ssh "$VPS_HOST" "
    if ! diff -q '$APP_DIR/deploy/kronos.service' /etc/systemd/system/kronos.service >/dev/null 2>&1; then
        cp '$APP_DIR/deploy/kronos.service' /etc/systemd/system/kronos.service
        systemctl daemon-reload
        echo '  unit actualizado'
    else
        echo '  unit sin cambios'
    fi
"

echo "== 6/7 Restart =="
ssh "$VPS_HOST" "systemctl restart kronos"

echo "== 7/7 Healthcheck (hasta 40s) =="
OK=0
for i in $(seq 1 8); do
    sleep 5
    STATUS="$(ssh "$VPS_HOST" "curl -fsS -o /dev/null -w '%{http_code}' --max-time 5 '$HEALTH_URL' 2>/dev/null" || echo "000")"
    if [ "$STATUS" = "200" ] || [ "$STATUS" = "302" ]; then
        OK=1
        break
    fi
    echo "  intento $i: http_status=$STATUS, reintentando..."
done

if [ "$OK" != "1" ]; then
    echo "ABORTADO: healthcheck no pasó después de 40s." >&2
    _rollback
fi

ERRORS="$(ssh "$VPS_HOST" "journalctl -u kronos --since '45 seconds ago' --no-pager | grep -iE 'error|traceback|exception' || true")"
if [ -n "$ERRORS" ]; then
    echo "ABORTADO: errores en el log post-restart:" >&2
    echo "$ERRORS" >&2
    _rollback
fi

echo "DEPLOY OK -- healthcheck pasó, sin errores en el log. Backup previo en $BACKUP_DIR."
