"""Configuración de gunicorn para producción (ver conversación: "servidor de
producción real" en vez del dev server de Flask -- antes kronos.service
corría `python webui/app.py` directo, que es el server de desarrollo de
Werkzeug, con su propia advertencia explícita de "no usar en producción").

workers=1 A PROPÓSITO, no un descuido: webui/app.py arranca QuoteWorker y el
bot de Telegram AL IMPORTAR el módulo (ver ese archivo) -- más de 1 worker de
gunicorn importaría el módulo 2+ veces, arrancando 2+ pollers de Telegram
contra el MISMO bot token, que Telegram rechaza (Conflict: terminated by
other getUpdates request). La concurrencia real para requests HTTP viene de
`threads` (gthread), no de más workers -- mismo modelo que ya usaba el dev
server (`threaded=True`).

Invocado desde kronos.service: `gunicorn -c deploy/gunicorn_config.py webui.app:app`
(working directory /opt/kronos/app, mismo que antes)."""
import os

# webui/app.py tiene "from nav import NAV_ITEMS" -- una importación BARE que
# asume que el directorio del script (webui/) está en sys.path[0]. Eso es
# cierto cuando se corre `python webui/app.py` directo (dev.sh local, y el
# kronos.service VIEJO) -- Python siempre agrega el directorio del script
# que se ejecuta a sys.path[0]. Pero gunicorn IMPORTA `webui.app` como
# MÓDULO (nunca lo corre como script), así que sys.path solo tiene el cwd
# (/opt/kronos/app) -- "import nav" a secas no resuelve ahí. `pythonpath`
# (setting propio de gunicorn, nunca un hack de sys.path a mano) agrega
# webui/ en vez de cambiar el import en app.py -- menor riesgo que tocar
# ese import, que dev.sh/webui/run.py SÍ dependen de su comportamiento actual.
pythonpath = os.path.join(os.getcwd(), "webui")

bind = f"{os.environ.get('KRONOS_HOST', '127.0.0.1')}:{os.environ.get('KRONOS_PORT', '8090')}"
workers = 1
worker_class = "gthread"
threads = int(os.environ.get('GUNICORN_THREADS', '8'))
# Chronos-Bolt/pronósticos pueden tardar mas que el default de gunicorn
# (30s) -- mismo criterio que el resto del proyecto, nunca cortar algo que
# todavía está calculando de verdad.
timeout = 120
graceful_timeout = 30
# Recicla el worker cada tanto (fuga de memoria lenta esperable en un
# proceso de vida larga con torch/pandas) -- jitter para no sincronizar el
# reciclado con el polling de Telegram/QuoteWorker.
max_requests = 1000
max_requests_jitter = 100
accesslog = "-"  # stdout -- mismo destino que ya captura journalctl
errorlog = "-"
loglevel = "info"
