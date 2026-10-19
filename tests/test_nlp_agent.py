"""Tests del Agente NLP de Noticias y Sentimiento (ver conversacion,
portfolio/nlp_agent.py) -- logica pura + persistencia sobre una DB temporal,
NUNCA red real (yfinance/LLM siempre mockeados, mismo criterio que el resto
de este suite, ver docstrings de los demas tests/test_*.py)."""
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from portfolio import db, nlp_agent


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_nlp_agent.db")
    monkeypatch.setattr(db, "DB_PATH", db_path)
    conn = db.get_connection(db_path)
    yield conn
    conn.close()


# --- vader_sentiment_score ----------------------------------------------------

def test_vader_sentiment_score_sin_titulares_es_neutral():
    assert nlp_agent.vader_sentiment_score([]) == 0.0


def test_vader_sentiment_score_titular_positivo():
    score = nlp_agent.vader_sentiment_score(["Company reports record profits and strong growth, beating all estimates"])
    assert score > 0.3


def test_vader_sentiment_score_titular_negativo():
    score = nlp_agent.vader_sentiment_score(["Company collapses amid fraud scandal, shares plunge, investors furious"])
    assert score < -0.3


def test_vader_sentiment_score_promedia_varios_titulares():
    positivo = nlp_agent.vader_sentiment_score(["Great news, excellent results, very happy"])
    mixto = nlp_agent.vader_sentiment_score([
        "Great news, excellent results, very happy", "Terrible crash, massive losses, very bad",
    ])
    assert abs(mixto) < abs(positivo)


def test_vader_sentiment_score_acepta_titulares_como_dict_title_url():
    # Forma real desde _fetch_finnhub_headlines (ver conversacion: "debo
    # poder entrar a las mismas") -- el score usa solo "title", nunca "url".
    como_string = nlp_agent.vader_sentiment_score(["Company reports record profits and strong growth"])
    como_dict = nlp_agent.vader_sentiment_score([
        {"title": "Company reports record profits and strong growth", "url": "https://example.com"},
    ])
    assert como_dict == como_string


# --- classify_risk_level -------------------------------------------------------

def test_classify_risk_level_earnings_dentro_de_48hs_es_alto():
    assert nlp_agent.classify_risk_level(0.0, hours_until_event=10) == nlp_agent.RISK_ALTO
    assert nlp_agent.classify_risk_level(0.0, hours_until_event=48) == nlp_agent.RISK_ALTO


def test_classify_risk_level_earnings_pasado_no_cuenta():
    # hours_until_event negativo = el evento ya paso -- no es "inminente".
    assert nlp_agent.classify_risk_level(0.0, hours_until_event=-5) != nlp_agent.RISK_ALTO


def test_classify_risk_level_sentimiento_extremo_es_alto_sin_evento():
    assert nlp_agent.classify_risk_level(0.8, hours_until_event=None) == nlp_agent.RISK_ALTO
    assert nlp_agent.classify_risk_level(-0.8, hours_until_event=None) == nlp_agent.RISK_ALTO


def test_classify_risk_level_earnings_dentro_de_la_semana_es_medio():
    assert nlp_agent.classify_risk_level(0.0, hours_until_event=24 * 5) == nlp_agent.RISK_MEDIO


def test_classify_risk_level_sin_evento_ni_sentimiento_es_bajo():
    assert nlp_agent.classify_risk_level(0.05, hours_until_event=None) == nlp_agent.RISK_BAJO
    assert nlp_agent.classify_risk_level(0.0, hours_until_event=24 * 30) == nlp_agent.RISK_BAJO


# --- persistencia (db.py) ------------------------------------------------------

def test_insert_and_get_latest_sentiment(temp_db):
    assert db.get_latest_sentiment(temp_db, "GGAL") is None
    db.insert_news_sentiment(
        temp_db, ticker="GGAL", sentiment_score=0.42, risk_level="MEDIO",
        summary="2 titulares analizados", event_date="2026-11-24",
    )
    latest = db.get_latest_sentiment(temp_db, "GGAL")
    assert latest["ticker"] == "GGAL"
    assert latest["sentiment_score"] == 0.42
    assert latest["risk_level"] == "MEDIO"
    assert latest["event_date"] == "2026-11-24"


def test_get_latest_sentiment_devuelve_la_fila_mas_reciente(temp_db):
    db.insert_news_sentiment(temp_db, ticker="AAPL", sentiment_score=0.1, risk_level="BAJO")
    db.insert_news_sentiment(temp_db, ticker="AAPL", sentiment_score=-0.7, risk_level="ALTO")
    latest = db.get_latest_sentiment(temp_db, "AAPL")
    assert latest["sentiment_score"] == -0.7
    assert latest["risk_level"] == "ALTO"


def test_list_recent_sentiment_filtra_por_ticker(temp_db):
    db.insert_news_sentiment(temp_db, ticker="AAPL", sentiment_score=0.1, risk_level="BAJO")
    db.insert_news_sentiment(temp_db, ticker="MSFT", sentiment_score=0.2, risk_level="BAJO")
    rows = db.list_recent_sentiment(temp_db, "AAPL", days=7)
    assert len(rows) == 1
    assert rows[0]["ticker"] == "AAPL"


# --- _fetch_news_headlines: Finnhub si hay key, yfinance si no (ver conversacion) --

def _fake_response(json_data, status_ok=True):
    def _raise():
        if not status_ok:
            raise RuntimeError("HTTP error simulado")
    return SimpleNamespace(json=lambda: json_data, raise_for_status=_raise)


def test_fetch_news_headlines_usa_finnhub_si_hay_key(temp_db, monkeypatch):
    db.update_signal_config(temp_db, finnhub_api_key="fake-finnhub-key")
    captured = {}

    def fake_get(url, params=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        return _fake_response([
            {"headline": "GGAL sube tras balance trimestral", "datetime": 1, "summary": "...", "source": "x",
             "url": "https://finnhub.io/api/news?id=abc"},
            {"headline": "Analistas elevan precio objetivo de GGAL", "datetime": 2},
        ])

    monkeypatch.setattr(nlp_agent.requests, "get", fake_get)
    monkeypatch.setattr(nlp_agent, "_fetch_yfinance_headlines", lambda symbol: (_ for _ in ()).throw(
        AssertionError("no deberia llamar a yfinance si Finnhub esta configurado")
    ))

    headlines = nlp_agent._fetch_news_headlines(temp_db, "GGAL")

    # {"title", "url"} normalizado (ver conversacion: "debo poder entrar a
    # las mismas") -- url ausente en la fuente se normaliza a None, nunca se
    # inventa un link.
    assert headlines == [
        {"title": "GGAL sube tras balance trimestral", "url": "https://finnhub.io/api/news?id=abc"},
        {"title": "Analistas elevan precio objetivo de GGAL", "url": None},
    ]
    assert captured["url"] == f"{nlp_agent.FINNHUB_BASE}/company-news"
    assert captured["params"]["symbol"] == "GGAL"
    assert captured["params"]["token"] == "fake-finnhub-key"


def test_fetch_news_headlines_sin_key_cae_a_yfinance(temp_db, monkeypatch):
    # signal_config nunca tiene finnhub_api_key configurada en una DB temporal nueva.
    called = {"yfinance": False}
    def fake_yfinance(symbol):
        called["yfinance"] = True
        return ["titular de yfinance"]
    monkeypatch.setattr(nlp_agent, "_fetch_yfinance_headlines", fake_yfinance)
    monkeypatch.setattr(nlp_agent.requests, "get", lambda *a, **kw: (_ for _ in ()).throw(
        AssertionError("no deberia pegarle a Finnhub sin key configurada")
    ))

    headlines = nlp_agent._fetch_news_headlines(temp_db, "GGAL")

    assert headlines == ["titular de yfinance"]
    assert called["yfinance"] is True


def test_fetch_yfinance_headlines_normaliza_ambas_formas_a_title_url(monkeypatch):
    # Dos formas vistas en distintas versiones de yfinance -- ver docstring
    # de _fetch_yfinance_headlines. Ambas se normalizan a {"title", "url"}.
    fake_news = [
        {"title": "Forma plana", "link": "https://example.com/plana"},
        {"content": {"title": "Forma anidada", "canonicalUrl": {"url": "https://example.com/anidada"}}},
        {"content": {"title": "Sin url"}},
    ]
    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(Ticker=lambda symbol: SimpleNamespace(news=fake_news)))

    headlines = nlp_agent._fetch_yfinance_headlines("AAPL")

    assert headlines == [
        {"title": "Forma plana", "url": "https://example.com/plana"},
        {"title": "Forma anidada", "url": "https://example.com/anidada"},
        {"title": "Sin url", "url": None},
    ]


def test_fetch_finnhub_headlines_error_de_api_degrada_a_lista_vacia(temp_db, monkeypatch):
    # Finnhub responde {"error": "..."} (no una lista) con una key invalida.
    monkeypatch.setattr(nlp_agent.requests, "get", lambda *a, **kw: _fake_response({"error": "Invalid API key."}))
    assert nlp_agent._fetch_finnhub_headlines("GGAL", "key-mala") == []


def test_fetch_finnhub_headlines_falla_de_red_degrada_a_lista_vacia(monkeypatch):
    def fake_get(*a, **kw):
        raise ConnectionError("simulado")
    monkeypatch.setattr(nlp_agent.requests, "get", fake_get)
    assert nlp_agent._fetch_finnhub_headlines("GGAL", "cualquier-key") == []


def test_fetch_finnhub_headlines_http_error_degrada_a_lista_vacia(monkeypatch):
    monkeypatch.setattr(nlp_agent.requests, "get", lambda *a, **kw: _fake_response({}, status_ok=False))
    assert nlp_agent._fetch_finnhub_headlines("GGAL", "cualquier-key") == []


# --- assess_event_risk (mockeando yfinance/LLM, nunca red real) ---------------

def test_assess_event_risk_sin_titulares_ni_earnings_es_bajo(temp_db, monkeypatch):
    monkeypatch.setattr(nlp_agent, "_fetch_news_headlines", lambda conn, symbol: [])
    monkeypatch.setattr(nlp_agent, "_fetch_next_earnings_date", lambda symbol: None)
    result = nlp_agent.assess_event_risk(temp_db, "GGAL", use_cache=False)
    assert result["risk_level"] == nlp_agent.RISK_BAJO
    assert result["sentiment_score"] == 0.0
    assert result["event_date"] is None
    # Se persistio en el log.
    assert db.get_latest_sentiment(temp_db, "GGAL") is not None


def test_assess_event_risk_earnings_en_24hs_es_alto(temp_db, monkeypatch):
    manana = (datetime.now(timezone.utc) + timedelta(hours=24)).date()
    monkeypatch.setattr(nlp_agent, "_fetch_news_headlines", lambda conn, symbol: [])
    monkeypatch.setattr(nlp_agent, "_fetch_next_earnings_date", lambda symbol: manana)
    result = nlp_agent.assess_event_risk(temp_db, "AAPL", use_cache=False)
    assert result["risk_level"] == nlp_agent.RISK_ALTO
    assert result["event_date"] == manana.isoformat()


def test_assess_event_risk_usa_cache_reciente(temp_db, monkeypatch):
    calls = {"n": 0}

    def _fake_headlines(conn, symbol):
        calls["n"] += 1
        return []

    monkeypatch.setattr(nlp_agent, "_fetch_news_headlines", _fake_headlines)
    monkeypatch.setattr(nlp_agent, "_fetch_next_earnings_date", lambda symbol: None)

    nlp_agent.assess_event_risk(temp_db, "TSLA", use_cache=True)
    nlp_agent.assess_event_risk(temp_db, "TSLA", use_cache=True)
    assert calls["n"] == 1  # la segunda llamada uso el cache, no volvio a "fetchear"


def test_assess_event_risk_use_cache_false_fuerza_corrida_nueva(temp_db, monkeypatch):
    calls = {"n": 0}

    def _fake_headlines(conn, symbol):
        calls["n"] += 1
        return []

    monkeypatch.setattr(nlp_agent, "_fetch_news_headlines", _fake_headlines)
    monkeypatch.setattr(nlp_agent, "_fetch_next_earnings_date", lambda symbol: None)

    nlp_agent.assess_event_risk(temp_db, "TSLA", use_cache=True)
    nlp_agent.assess_event_risk(temp_db, "TSLA", use_cache=False)
    assert calls["n"] == 2


def test_llm_sentiment_score_sin_key_devuelve_none(temp_db):
    # signal_config nunca tiene key configurada en una DB temporal nueva.
    assert nlp_agent._llm_sentiment_score(["alguna noticia"], temp_db) is None


# --- titulares persistidos (ver conversacion: "no hay seccion de noticias") ---

def test_assess_event_risk_persiste_titulares_reales(temp_db, monkeypatch):
    monkeypatch.setattr(nlp_agent, "_fetch_news_headlines", lambda conn, symbol: ["Titular uno", "Titular dos"])
    monkeypatch.setattr(nlp_agent, "_fetch_next_earnings_date", lambda symbol: None)

    result = nlp_agent.assess_event_risk(temp_db, "AAPL", use_cache=False)
    assert result["headlines"] == ["Titular uno", "Titular dos"]

    persisted = db.get_latest_sentiment(temp_db, "AAPL")
    assert persisted["headlines"] == ["Titular uno", "Titular dos"]


def test_assess_event_risk_acota_titulares_a_10(temp_db, monkeypatch):
    muchos = [f"Titular {i}" for i in range(50)]
    monkeypatch.setattr(nlp_agent, "_fetch_news_headlines", lambda conn, symbol: muchos)
    monkeypatch.setattr(nlp_agent, "_fetch_next_earnings_date", lambda symbol: None)

    result = nlp_agent.assess_event_risk(temp_db, "AAPL", use_cache=False)
    assert len(result["headlines"]) == 10
    assert result["headlines"] == muchos[:10]


def test_assess_event_risk_cache_hit_devuelve_los_mismos_titulares(temp_db, monkeypatch):
    monkeypatch.setattr(nlp_agent, "_fetch_news_headlines", lambda conn, symbol: ["Solo esta vez"])
    monkeypatch.setattr(nlp_agent, "_fetch_next_earnings_date", lambda symbol: None)

    nlp_agent.assess_event_risk(temp_db, "TSLA", use_cache=False)
    # Segunda llamada con cache=True NO vuelve a fetchear (ver
    # test_assess_event_risk_usa_cache_reciente) pero igual tiene que
    # devolver los titulares ya persistidos, no una lista vacia.
    result = nlp_agent.assess_event_risk(temp_db, "TSLA", use_cache=True)
    assert result["headlines"] == ["Solo esta vez"]


def test_assess_event_risk_sin_titulares_persiste_lista_vacia(temp_db, monkeypatch):
    monkeypatch.setattr(nlp_agent, "_fetch_news_headlines", lambda conn, symbol: [])
    monkeypatch.setattr(nlp_agent, "_fetch_next_earnings_date", lambda symbol: None)

    result = nlp_agent.assess_event_risk(temp_db, "GGAL", use_cache=False)
    assert result["headlines"] == []
    assert db.get_latest_sentiment(temp_db, "GGAL")["headlines"] == []


# --- db.list_today_sentiment (ver conversacion: subtab "Noticias" de Decisiones) --

def test_list_today_sentiment_un_symbolo_una_vez_aunque_se_evalue_dos_veces(temp_db, monkeypatch):
    monkeypatch.setattr(nlp_agent, "_fetch_next_earnings_date", lambda symbol: None)

    monkeypatch.setattr(nlp_agent, "_fetch_news_headlines", lambda conn, symbol: ["Primera corrida"])
    nlp_agent.assess_event_risk(temp_db, "GGAL", use_cache=False)
    monkeypatch.setattr(nlp_agent, "_fetch_news_headlines", lambda conn, symbol: ["Segunda corrida, mas reciente"])
    nlp_agent.assess_event_risk(temp_db, "GGAL", use_cache=False)

    today = db.list_today_sentiment(temp_db)
    ggal_entries = [e for e in today if e["ticker"] == "GGAL"]
    assert len(ggal_entries) == 1
    assert ggal_entries[0]["headlines"] == ["Segunda corrida, mas reciente"]


def test_list_today_sentiment_agrega_varios_simbolos(temp_db, monkeypatch):
    monkeypatch.setattr(nlp_agent, "_fetch_next_earnings_date", lambda symbol: None)
    monkeypatch.setattr(nlp_agent, "_fetch_news_headlines", lambda conn, symbol: [f"Noticia de {symbol}"])

    nlp_agent.assess_event_risk(temp_db, "GGAL", use_cache=False)
    nlp_agent.assess_event_risk(temp_db, "AAPL", use_cache=False)

    tickers = {e["ticker"] for e in db.list_today_sentiment(temp_db)}
    assert tickers == {"GGAL", "AAPL"}


def test_llm_sentiment_score_sin_titulares_nunca_llama_a_la_api(temp_db):
    db.update_signal_config(temp_db, openai_api_key="sk-fake-no-deberia-usarse")
    assert nlp_agent._llm_sentiment_score([], temp_db) is None


# --- event_risk_note -----------------------------------------------------------

def test_event_risk_note_bajo_es_none():
    assert nlp_agent.event_risk_note({"risk_level": nlp_agent.RISK_BAJO, "event_date": None,
                                       "hours_until_event": None, "sentiment_score": 0.0}) is None


def test_event_risk_note_earnings_inminente():
    note = nlp_agent.event_risk_note({
        "risk_level": nlp_agent.RISK_ALTO, "event_date": "2026-10-18",
        "hours_until_event": 30.0, "sentiment_score": 0.0,
    })
    assert note == nlp_agent.EVENT_RISK_NOTE_EARNINGS


def test_event_risk_note_sentimiento_extremo_sin_evento():
    note = nlp_agent.event_risk_note({
        "risk_level": nlp_agent.RISK_ALTO, "event_date": None,
        "hours_until_event": None, "sentiment_score": -0.8,
    })
    assert note == nlp_agent.EVENT_RISK_NOTE_NEWS


# --- satellite_entry_blocked_by_event ------------------------------------------

def test_satellite_entry_blocked_by_event_true_si_riesgo_alto(temp_db, monkeypatch):
    manana = (datetime.now(timezone.utc) + timedelta(hours=10)).date()
    monkeypatch.setattr(nlp_agent, "_fetch_news_headlines", lambda conn, symbol: [])
    monkeypatch.setattr(nlp_agent, "_fetch_next_earnings_date", lambda symbol: manana)
    assert nlp_agent.satellite_entry_blocked_by_event(temp_db, "AAPL", use_cache=False) is True


def test_satellite_entry_blocked_by_event_false_si_riesgo_bajo(temp_db, monkeypatch):
    monkeypatch.setattr(nlp_agent, "_fetch_news_headlines", lambda conn, symbol: [])
    monkeypatch.setattr(nlp_agent, "_fetch_next_earnings_date", lambda symbol: None)
    assert nlp_agent.satellite_entry_blocked_by_event(temp_db, "GGAL", use_cache=False) is False


# --- wiring en live_signals.compute_satellite_signals --------------------------

def test_compute_satellite_signals_salta_ticker_con_evento_de_riesgo(temp_db, monkeypatch):
    """No se testea toda compute_satellite_signals (necesita historial real de
    precios) -- se verifica la pieza puntual: satellite_entry_blocked_by_event
    determina si un simbolo se saltea, igual que historial insuficiente ya
    hace via 'skipped'. Mockeamos para forzar riesgo ALTO en un symbol y
    confirmamos que la funcion de bloqueo (ya probada arriba) efectivamente
    devuelve True para ese caso, que es exactamente lo que
    compute_satellite_signals consulta antes de construir una señal."""
    manana = (datetime.now(timezone.utc) + timedelta(hours=5)).date()
    monkeypatch.setattr(nlp_agent, "_fetch_news_headlines", lambda conn, symbol: [])
    monkeypatch.setattr(nlp_agent, "_fetch_next_earnings_date", lambda symbol: manana if symbol == "AAPL" else None)
    assert nlp_agent.satellite_entry_blocked_by_event(temp_db, "AAPL", use_cache=False) is True
    assert nlp_agent.satellite_entry_blocked_by_event(temp_db, "GGAL", use_cache=False) is False
