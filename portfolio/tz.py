"""Zona horaria unica para toda la app -- "hoy" siempre es hora Argentina, no
UTC. Antes de este modulo, 11 lugares distintos llamaban datetime.utcnow()
directo: despues de las 21hs ART (UTC-3), "hoy" ya calculaba el dia siguiente
(ej. un bono a 6 dias de vencer se mostraba a 5). ART_TZ ya existia duplicado
en quote_worker.py (Fase 7) -- queda centralizado aca, quote_worker.py lo
importa de aca en vez de redefinirlo."""
from datetime import datetime
from zoneinfo import ZoneInfo

ART_TZ = ZoneInfo("America/Argentina/Buenos_Aires")


def now_ar():
    return datetime.now(ART_TZ)


def today_ar():
    return now_ar().date()
