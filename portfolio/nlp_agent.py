"""Agente NLP de Noticias y Sentimiento de Eventos (ver conversacion) --
consulta titulares recientes y la proxima fecha de resultados (earnings) de
un ticker via yfinance (gratis, ya es dependencia del proyecto, sin API key
nueva obligatoria), les asigna un sentiment_score (-1.0 a +1.0) y un
risk_level (BAJO/MEDIO/ALTO), y expone un filtro de riesgo preventivo que el
Motor Satelite puede consultar antes de abrir una posicion nueva.

Dos motores de sentimiento, nunca un hard-dependency del segundo:
  - VADER (vaderSentiment, local, determinista, sin red mas alla de
    yfinance) -- SIEMPRE disponible, es el fallback.
  - LLM (OpenAI o Anthropic) -- SOLO si signal_config.openai_api_key /
    anthropic_api_key esta configurada (ver schema.sql) Y la llamada
    efectivamente funciona; cualquier falla (sin key, red caida, respuesta
    rara) degrada a VADER en silencio, nunca rompe el pipeline por depender
    de una API paga.

Hallazgo real (ver conversacion, declarado explicitamente): en esta cuenta/
version de yfinance (1.7.0), Ticker(symbol).news devuelve SIEMPRE una lista
vacia para los tickers probados (AAPL/MSFT/TSLA/GGAL) -- la fuente de
titulares no es confiable hoy. Ticker(symbol).calendar SI funciona de forma
consistente (incluida la fecha de 'Earnings Date') y degrada limpio a {} para
bonos/cripto (sin excepcion, probado con AL30D/BTC-USD). Por eso el filtro de
riesgo preventivo NUNCA depende solo de noticias: si no hay titulares, el
sentiment_score cae a 0.0 (neutral/sin datos, nunca inventado) pero el
risk_level igual puede ser ALTO si hay un earnings dentro de las proximas 48hs
-- esa señal es la mas confiable que se pudo verificar con datos reales.

Cero look-ahead: assess_event_risk siempre mira "HOY hacia adelante" (la
proxima fecha de earnings, titulares YA publicados) -- nunca usa un desenlace
que todavia no paso, consistente con el resto del proyecto.

Finnhub (ver conversacion, "busca fuentes de noticias gratuitas"): reemplaza
a yfinance como fuente de titulares CUANDO signal_config.finnhub_api_key
esta configurada -- plan gratuito confirmado (sin tarjeta, 60 llamadas/min),
endpoint GET /api/v1/company-news?symbol=...&from=...&to=...&token=...
confirmado contra la API real esta sesion (una request sin token valido
responde {"error": "Invalid API key."}, confirmando la forma del endpoint).
Sin la key configurada, sigue intentando yfinance primero (comportamiento
de siempre, nunca una regresion)."""
from datetime import datetime, timedelta, timezone

import requests

from portfolio import db

FINNHUB_BASE = "https://finnhub.io/api/v1"

EARNINGS_WARNING_HOURS = 48  # "dentro de las proximas 48hs" pedido explicitamente
EARNINGS_WATCH_HOURS = 24 * 7  # mas alla de 48hs pero dentro de la semana -> MEDIO, no BAJO (hay un evento acercandose)

RISK_BAJO = "BAJO"
RISK_MEDIO = "MEDIO"
RISK_ALTO = "ALTO"

# Umbral de sentimiento "extremo" (ver conversacion: "noticia de alto impacto")
# -- declarado, no pedido con un numero explicito. VADER compound en [-1, 1],
# 0.6 es el piso que su propia documentacion sugiere para "fuertemente
# positivo/negativo".
SENTIMENT_EXTREME_THRESHOLD = 0.6
SENTIMENT_NOTABLE_THRESHOLD = 0.3

EVENT_RISK_NOTE_EARNINGS = "⚠️ Riesgo Evento: Balance inminente"
EVENT_RISK_NOTE_NEWS = "⚠️ Riesgo Evento: noticia de alto impacto"

# Cache de 1 corrida por dia por ticker (ver _is_fresh_enough) -- evita pegarle
# a yfinance en cada request de /signals o cada anchor del backtest; mismo
# criterio de frescura diaria que portfolio/fetchers.py::ensure_benchmark_history.
_CACHE_FRESHNESS_HOURS = 6


def _fetch_finnhub_headlines(symbol, api_key):
    """Titulares via Finnhub /company-news -- SIEMPRE envuelto en
    try/except, [] en cualquier falla (red caida, simbolo no cubierto,
    respuesta inesperada). Ultimos 7 dias, mismo criterio de "noticias
    recientes" que ya tenia el intento de yfinance. "headline" es el campo
    de titulo en el esquema publico de Finnhub -- nunca se asume otro
    nombre de campo sin chequear."""
    today = datetime.now(timezone.utc).date()
    week_ago = today - timedelta(days=7)
    try:
        resp = requests.get(
            f"{FINNHUB_BASE}/company-news",
            params={"symbol": symbol, "from": week_ago.isoformat(), "to": today.isoformat(), "token": api_key},
            timeout=15,
        )
        resp.raise_for_status()
        news = resp.json()
    except Exception:
        return []
    if not isinstance(news, list):
        return []  # Finnhub devuelve {"error": "..."} (no una lista) si la key es invalida -- nunca se inventa un titular de ahi
    return [str(item["headline"]) for item in news if isinstance(item, dict) and item.get("headline")]


def _fetch_yfinance_headlines(symbol):
    """Titulares recientes via yfinance -- SIEMPRE envuelto en try/except,
    [] en cualquier falla (incluida la ya verificada: .news vacio en esta
    version/cuenta, ver docstring del modulo). Nunca se inventa un titular."""
    try:
        import yfinance as yf
        news = yf.Ticker(symbol).news or []
    except Exception:
        return []
    headlines = []
    for item in news:
        # Dos formas vistas en distintas versiones de yfinance: plano
        # {"title": ...} o anidado {"content": {"title": ...}} -- se prueban
        # ambas, nunca se asume una sola forma sin chequear.
        content = item.get("content", item) if isinstance(item, dict) else {}
        title = content.get("title") or item.get("title") if isinstance(item, dict) else None
        if title:
            headlines.append(str(title))
    return headlines


def _fetch_news_headlines(conn, symbol):
    """Finnhub primero SI hay finnhub_api_key configurada (ver
    signal_config) -- yfinance.Ticker().news devuelve siempre [] en esta
    cuenta/version (ver docstring del modulo), asi que sin la key esto
    sigue cayendo en yfinance exactamente como antes (nunca una regresion
    para quien no cargue una key)."""
    api_key = db.get_signal_config(conn).get("finnhub_api_key")
    if api_key:
        return _fetch_finnhub_headlines(symbol, api_key)
    return _fetch_yfinance_headlines(symbol)


def _fetch_next_earnings_date(symbol):
    """Proxima fecha de resultados (date, no datetime -- yfinance la expone
    sin hora) via Ticker.calendar['Earnings Date'][0] -- None si no hay dato
    (bono/cripto/FX, o la fuente no la tiene hoy) o si la llamada falla.
    Verificado (ver conversacion): calendar degrada a {} limpio para
    activos sin fundamentals, nunca lanza para esos casos, pero se envuelve
    en try/except igual por los que SI pueden fallar por red."""
    try:
        import yfinance as yf
        calendar = yf.Ticker(symbol).calendar or {}
    except Exception:
        return None
    dates = calendar.get("Earnings Date")
    if not dates:
        return None
    return min(dates)  # la MAS PROXIMA, no la primera de la lista (yfinance no garantiza orden)


def _hours_until(event_date):
    """event_date: date (sin hora, ver _fetch_next_earnings_date) -- se
    asume el FIN del dia (23:59:59 UTC), no la medianoche de arranque
    (aproximacion declarada: no se conoce la hora exacta del reporte, solo
    el dia). Bug real encontrado y corregido (ver conversacion, tests): usar
    medianoche de ARRANQUE hacia un evento de HOY mismo daba un resultado
    NEGATIVO (ya "paso la medianoche de hoy") aunque el evento todavia no
    ocurrio -- fin de dia es la aproximacion conservadora correcta: nunca
    subestima cuanto falta para un evento de hoy o de una fecha futura."""
    if event_date is None:
        return None
    event_dt = datetime(event_date.year, event_date.month, event_date.day, 23, 59, 59, tzinfo=timezone.utc)
    delta = event_dt - datetime.now(timezone.utc)
    return delta.total_seconds() / 3600.0


def vader_sentiment_score(headlines):
    """Promedio del compound score de VADER sobre los titulares -- 0.0
    (neutral) si no hay titulares, NUNCA un valor inventado sin evidencia.
    Import tardio (mismo criterio que el resto del proyecto para
    dependencias pesadas/opcionales -- vaderSentiment es liviana pero el
    import tardio evita pagarlo si este modulo nunca se usa)."""
    if not headlines:
        return 0.0
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
    analyzer = SentimentIntensityAnalyzer()
    scores = [analyzer.polarity_scores(h)["compound"] for h in headlines]
    return sum(scores) / len(scores)


def _llm_sentiment_score(headlines, conn):
    """Upgrade OPCIONAL via LLM (OpenAI o Anthropic, ver signal_config) --
    devuelve None si no hay key configurada, si ninguna libreria esta
    instalada, o si la llamada falla por cualquier motivo (nunca rompe el
    pipeline por depender de una API paga, ver docstring del modulo). Nunca
    se llama si headlines esta vacio (nada que analizar)."""
    if not headlines:
        return None
    config = db.get_signal_config(conn)
    openai_key = config.get("openai_api_key")
    anthropic_key = config.get("anthropic_api_key")
    if not openai_key and not anthropic_key:
        return None

    prompt = (
        "Analiza el sentimiento de mercado de estos titulares financieros. "
        "Responde UNICAMENTE con un numero decimal entre -1.0 (muy negativo) "
        "y 1.0 (muy positivo), sin texto adicional.\n\n"
        + "\n".join(f"- {h}" for h in headlines[:10])
    )
    try:
        if anthropic_key:
            import anthropic
            client = anthropic.Anthropic(api_key=anthropic_key)
            resp = client.messages.create(
                model="claude-haiku-4-5-20251001", max_tokens=16,
                messages=[{"role": "user", "content": prompt}],
            )
            text = resp.content[0].text.strip()
        else:
            import openai
            client = openai.OpenAI(api_key=openai_key)
            resp = client.chat.completions.create(
                model="gpt-4o-mini", max_tokens=16,
                messages=[{"role": "user", "content": prompt}],
            )
            text = resp.choices[0].message.content.strip()
        score = float(text)
        return max(-1.0, min(1.0, score))
    except Exception:
        return None  # red caida, libreria no instalada, respuesta no parseable, etc. -- degrada a VADER


def classify_risk_level(sentiment_score, hours_until_event):
    """Combina magnitud del sentimiento con proximidad del evento (pedido
    explicito: risk_level NO es solo sentimiento) --
    ALTO: earnings dentro de EARNINGS_WARNING_HOURS (48hs) O sentimiento extremo.
    MEDIO: earnings dentro de la semana O sentimiento notable pero no extremo.
    BAJO: nada de lo anterior."""
    abs_score = abs(sentiment_score)
    if (hours_until_event is not None and 0 <= hours_until_event <= EARNINGS_WARNING_HOURS) or abs_score >= SENTIMENT_EXTREME_THRESHOLD:
        return RISK_ALTO
    if (hours_until_event is not None and 0 <= hours_until_event <= EARNINGS_WATCH_HOURS) or abs_score >= SENTIMENT_NOTABLE_THRESHOLD:
        return RISK_MEDIO
    return RISK_BAJO


def _is_fresh_enough(latest_row):
    if latest_row is None:
        return False
    try:
        created_at = datetime.fromisoformat(latest_row["created_at"].replace(" ", "T")).replace(tzinfo=timezone.utc)
    except Exception:
        return False
    age_hours = (datetime.now(timezone.utc) - created_at).total_seconds() / 3600.0
    return age_hours < _CACHE_FRESHNESS_HOURS


def assess_event_risk(conn, symbol, use_cache=True):
    """Punto de entrada principal -- devuelve un dict {ticker, sentiment_score,
    risk_level, summary, event_date, hours_until_event} y lo persiste en
    news_sentiment_log (ver portfolio/db.py). Cacheado por
    _CACHE_FRESHNESS_HOURS (ver arriba) para no pegarle a yfinance/LLM en
    cada llamada -- use_cache=False fuerza una corrida nueva (tests,
    debugging)."""
    if use_cache:
        latest = db.get_latest_sentiment(conn, symbol)
        if _is_fresh_enough(latest):
            return {
                "ticker": symbol, "sentiment_score": latest["sentiment_score"],
                "risk_level": latest["risk_level"], "summary": latest["summary"],
                "event_date": latest["event_date"],
                "hours_until_event": _hours_until(
                    datetime.fromisoformat(latest["event_date"]).date() if latest["event_date"] else None
                ),
            }

    headlines = _fetch_news_headlines(conn, symbol)
    earnings_date = _fetch_next_earnings_date(symbol)
    hours_until_event = _hours_until(earnings_date)

    llm_score = _llm_sentiment_score(headlines, conn)
    sentiment_score = llm_score if llm_score is not None else vader_sentiment_score(headlines)
    risk_level = classify_risk_level(sentiment_score, hours_until_event)

    summary_parts = []
    if earnings_date is not None:
        summary_parts.append(f"earnings el {earnings_date.isoformat()} ({hours_until_event:.0f}hs)")
    if headlines:
        summary_parts.append(f"{len(headlines)} titular(es) analizados (score={sentiment_score:.2f})")
    else:
        summary_parts.append("sin titulares disponibles hoy")
    summary = " · ".join(summary_parts)

    db.insert_news_sentiment(
        conn, ticker=symbol, sentiment_score=round(sentiment_score, 4), risk_level=risk_level,
        summary=summary, event_date=earnings_date.isoformat() if earnings_date else None,
    )
    return {
        "ticker": symbol, "sentiment_score": round(sentiment_score, 4), "risk_level": risk_level,
        "summary": summary, "event_date": earnings_date.isoformat() if earnings_date else None,
        "hours_until_event": hours_until_event,
    }


def satellite_entry_blocked_by_event(conn, symbol, use_cache=True):
    """Filtro de riesgo preventivo (pedido explicito: "limita la entrada del
    Motor Satelite") -- True si el riesgo evaluado es ALTO. Posiciones YA
    abiertas no se tocan (mismo criterio que portfolio/regime.py::satellite_blocked,
    esto solo afecta si se abre una posicion NUEVA)."""
    assessment = assess_event_risk(conn, symbol, use_cache=use_cache)
    return assessment["risk_level"] == RISK_ALTO


def event_risk_note(assessment):
    """Texto para mostrar en Telegram/UI (pedido explicito: el string literal
    para el caso de earnings) -- None si el riesgo es BAJO (nada que avisar)."""
    if assessment is None or assessment["risk_level"] == RISK_BAJO:
        return None
    hours = assessment.get("hours_until_event")
    if assessment["event_date"] and hours is not None and 0 <= hours <= EARNINGS_WATCH_HOURS:
        return EVENT_RISK_NOTE_EARNINGS
    if abs(assessment["sentiment_score"]) >= SENTIMENT_NOTABLE_THRESHOLD:
        return EVENT_RISK_NOTE_NEWS
    return None
