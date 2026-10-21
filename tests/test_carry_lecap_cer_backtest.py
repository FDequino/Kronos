"""Tests de portfolio/carry_lecap_cer_backtest.py -- backtest historico
APROXIMADO del Carry LECAP/CER (ver docstring del modulo: proxea LECAP con
BADLAR real del BCRA + precio real de un bono CER de data912, rota por
retorno realizado). Nunca pega a red real -- fetch_data912_historical,
requests.get (BADLAR) y get_ccl_series siempre monkeypatcheados."""
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from portfolio import carry_lecap_cer_backtest as clc
from portfolio import db, fetchers, macro_data


def _cer_df(closes, start="2024-01-01"):
    dates = pd.date_range(start, periods=len(closes), freq="D")
    return pd.DataFrame({"timestamps": dates, "close": closes})


def _badlar_response(rows):
    resp = MagicMock()
    resp.raise_for_status = lambda: None
    resp.json.return_value = {
        "metadata": {"resultset": {"count": len(rows), "offset": 0, "limit": 1000}},
        "results": [{"detalle": rows}],
    }
    return resp


def test_badlar_daily_return_scale():
    """TNA 36.5% -> 0.1 (percent) por dia -- NUNCA /100/365 (esa escala,
    usada hoy en yield_engine.daily_caucion_return_pct, da un retorno
    compuesto ~100x mas chico que el real al tratarlo como daily_returns_pct,
    ver nota del modulo)."""
    s = pd.Series([36.5])
    out = clc._badlar_daily_return_pct(s)
    assert out.iloc[0] == pytest.approx(0.1)


def test_fetch_badlar_history_pagina_y_combina(monkeypatch):
    page1 = [{"fecha": "2024-01-02", "valor": 100.0}]
    page2 = [{"fecha": "2024-01-01", "valor": 99.0}]
    calls = []

    def fake_get(url, params=None, timeout=None, verify=None):
        calls.append(params["offset"])
        if params["offset"] == 0:
            resp = MagicMock()
            resp.raise_for_status = lambda: None
            resp.json.return_value = {
                "metadata": {"resultset": {"count": 2, "offset": 0, "limit": 1}},
                "results": [{"detalle": page1}],
            }
            return resp
        resp = MagicMock()
        resp.raise_for_status = lambda: None
        resp.json.return_value = {
            "metadata": {"resultset": {"count": 2, "offset": 1, "limit": 1}},
            "results": [{"detalle": page2}],
        }
        return resp

    monkeypatch.setattr(clc.requests, "get", fake_get)

    df = clc.fetch_badlar_history("2024-01-01", "2024-01-02")

    assert len(calls) == 2
    assert list(df["date"].dt.strftime("%Y-%m-%d")) == ["2024-01-01", "2024-01-02"]
    assert list(df["tna_pct"]) == [99.0, 100.0]


def test_fetch_badlar_history_fuente_caida_devuelve_vacio(monkeypatch):
    def fake_get(*a, **k):
        raise ConnectionError("red caida")
    monkeypatch.setattr(clc.requests, "get", fake_get)

    df = clc.fetch_badlar_history("2024-01-01", "2024-01-02")

    assert df.empty


def test_simulate_sin_historial_cer_devuelve_vacio(monkeypatch):
    monkeypatch.setattr(fetchers, "fetch_data912_historical", lambda symbol, asset_type: pd.DataFrame())

    result = clc.simulate_carry_lecap_cer_engine(None)

    assert len(result["daily_returns_pct"]) == 0
    assert result["engine"] == "carry_lecap_cer"


def test_simulate_rota_hacia_la_pata_que_mejor_rindio(monkeypatch):
    # CER sube fuerte todo el tramo -> despues del primer rebalanceo (21
    # ruedas) deberia rotar de BADLAR a CER.
    n = 40
    cer_closes = [100.0 * (1.01 ** i) for i in range(n)]  # +1%/dia compuesto
    monkeypatch.setattr(fetchers, "fetch_data912_historical", lambda symbol, asset_type: _cer_df(cer_closes))

    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    badlar_rows = [{"fecha": d.strftime("%Y-%m-%d"), "valor": 30.0} for d in dates]  # TNA constante, bajo
    monkeypatch.setattr(clc, "fetch_badlar_history", lambda desde, hasta: pd.DataFrame({
        "date": dates, "tna_pct": [30.0] * n,
    }))

    result = clc.simulate_carry_lecap_cer_engine(None, rebalance_days=21)

    assert len(result["daily_returns_pct"]) == n
    assert len(result["trades"]) >= 1
    assert result["trades"][0]["to"] == "CER"
    # Despues de rotar a CER, el retorno diario debe acercarse al +1% de CER,
    # no al de BADLAR (30/365 ~= 0.082%).
    last_returns = result["daily_returns_pct"].iloc[-5:]
    assert (last_returns > 0.5).all()


def test_simulate_usd_convierte_con_ccl_real(monkeypatch):
    n = 30
    cer_closes = [100.0] * n  # CER plano en ARS
    monkeypatch.setattr(fetchers, "fetch_data912_historical", lambda symbol, asset_type: _cer_df(cer_closes))
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    monkeypatch.setattr(clc, "fetch_badlar_history", lambda desde, hasta: pd.DataFrame({
        "date": dates, "tna_pct": [30.0] * n,
    }))
    # CCL subiendo (devaluacion real) -- con CER/BADLAR planos en ARS, el
    # resultado en USD debe ser NEGATIVO (perdida real por devaluacion).
    ccl = pd.Series([1000.0 * (1.01 ** i) for i in range(n)], index=dates)
    monkeypatch.setattr(macro_data, "get_ccl_series", lambda conn: ccl)

    result = clc.simulate_carry_lecap_cer_engine_usd(None)

    assert len(result["daily_returns_pct"]) > 0
    assert result["daily_returns_pct"].mean() < 0
