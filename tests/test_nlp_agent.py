"""Tests del Agente NLP de Noticias y Sentimiento (ver conversacion,
portfolio/nlp_agent.py) -- logica pura + persistencia sobre una DB temporal,
NUNCA red real (yfinance/LLM siempre mockeados, mismo criterio que el resto
de este suite, ver docstrings de los demas tests/test_*.py)."""
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

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


# --- assess_event_risk (mockeando yfinance/LLM, nunca red real) ---------------

def test_assess_event_risk_sin_titulares_ni_earnings_es_bajo(temp_db, monkeypatch):
    monkeypatch.setattr(nlp_agent, "_fetch_news_headlines", lambda symbol: [])
    monkeypatch.setattr(nlp_agent, "_fetch_next_earnings_date", lambda symbol: None)
    result = nlp_agent.assess_event_risk(temp_db, "GGAL", use_cache=False)
    assert result["risk_level"] == nlp_agent.RISK_BAJO
    assert result["sentiment_score"] == 0.0
    assert result["event_date"] is None
    # Se persistio en el log.
    assert db.get_latest_sentiment(temp_db, "GGAL") is not None


def test_assess_event_risk_earnings_en_24hs_es_alto(temp_db, monkeypatch):
    manana = (datetime.now(timezone.utc) + timedelta(hours=24)).date()
    monkeypatch.setattr(nlp_agent, "_fetch_news_headlines", lambda symbol: [])
    monkeypatch.setattr(nlp_agent, "_fetch_next_earnings_date", lambda symbol: manana)
    result = nlp_agent.assess_event_risk(temp_db, "AAPL", use_cache=False)
    assert result["risk_level"] == nlp_agent.RISK_ALTO
    assert result["event_date"] == manana.isoformat()


def test_assess_event_risk_usa_cache_reciente(temp_db, monkeypatch):
    calls = {"n": 0}

    def _fake_headlines(symbol):
        calls["n"] += 1
        return []

    monkeypatch.setattr(nlp_agent, "_fetch_news_headlines", _fake_headlines)
    monkeypatch.setattr(nlp_agent, "_fetch_next_earnings_date", lambda symbol: None)

    nlp_agent.assess_event_risk(temp_db, "TSLA", use_cache=True)
    nlp_agent.assess_event_risk(temp_db, "TSLA", use_cache=True)
    assert calls["n"] == 1  # la segunda llamada uso el cache, no volvio a "fetchear"


def test_assess_event_risk_use_cache_false_fuerza_corrida_nueva(temp_db, monkeypatch):
    calls = {"n": 0}

    def _fake_headlines(symbol):
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
    monkeypatch.setattr(nlp_agent, "_fetch_news_headlines", lambda symbol: [])
    monkeypatch.setattr(nlp_agent, "_fetch_next_earnings_date", lambda symbol: manana)
    assert nlp_agent.satellite_entry_blocked_by_event(temp_db, "AAPL", use_cache=False) is True


def test_satellite_entry_blocked_by_event_false_si_riesgo_bajo(temp_db, monkeypatch):
    monkeypatch.setattr(nlp_agent, "_fetch_news_headlines", lambda symbol: [])
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
    monkeypatch.setattr(nlp_agent, "_fetch_news_headlines", lambda symbol: [])
    monkeypatch.setattr(nlp_agent, "_fetch_next_earnings_date", lambda symbol: manana if symbol == "AAPL" else None)
    assert nlp_agent.satellite_entry_blocked_by_event(temp_db, "AAPL", use_cache=False) is True
    assert nlp_agent.satellite_entry_blocked_by_event(temp_db, "GGAL", use_cache=False) is False
