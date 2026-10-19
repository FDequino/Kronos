"""Tests del escaner de liquidez / "aduana de liquidez" (ver conversacion,
portfolio/scanner.py) -- DB temporal propia (mismo patron que el resto del
suite), NUNCA red real (data912/dolarapi siempre mockeados)."""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from portfolio import db, scanner
from portfolio.backtest import UNIVERSE


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_scanner.db")
    monkeypatch.setattr(db, "DB_PATH", db_path)
    conn = db.get_connection(db_path)
    yield conn
    conn.close()


@pytest.fixture(autouse=True)
def reset_liquidity_cache():
    # El cache de scan_global_filtered es un dict a nivel modulo -- sin
    # resetearlo, un test que corre antes contamina el resultado del
    # siguiente (ver conversacion: mismo riesgo que cualquier cache global).
    scanner._liquidity_cache.update(universe=None, fetched_at=0.0, params=None)
    yield
    scanner._liquidity_cache.update(universe=None, fetched_at=0.0, params=None)


# --- actualizacion de parametros via API (PUT /api/portfolio/signal-config, ---
# ver portfolio/routes.py::signal_config -- pasa **request.get_json() directo
# a db.update_signal_config, asi que probar esta funcion ES probar la API) --

def test_signal_config_tiene_los_4_parametros_del_escaner_por_default(temp_db):
    config = db.get_signal_config(temp_db)
    assert config["scanner_mode"] == "curated"
    assert config["min_daily_volume_usd"] == 20000.0
    assert config["max_bid_ask_spread_pct"] == 1.5
    assert config["min_operating_days_month"] == 15


def test_update_signal_config_actualiza_los_4_parametros_del_escaner(temp_db):
    updated = db.update_signal_config(
        temp_db, scanner_mode="global_filtered", min_daily_volume_usd=5000.0,
        max_bid_ask_spread_pct=3.0, min_operating_days_month=10,
    )
    assert updated["scanner_mode"] == "global_filtered"
    assert updated["min_daily_volume_usd"] == 5000.0
    assert updated["max_bid_ask_spread_pct"] == 3.0
    assert updated["min_operating_days_month"] == 10

    # Persistido de verdad (no solo el dict devuelto en memoria).
    reloaded = db.get_signal_config(temp_db)
    assert reloaded["scanner_mode"] == "global_filtered"
    assert reloaded["min_daily_volume_usd"] == 5000.0


def test_update_signal_config_actualiza_un_solo_parametro_del_escaner_sin_tocar_los_demas(temp_db):
    db.update_signal_config(temp_db, min_daily_volume_usd=50000.0)
    config = db.get_signal_config(temp_db)
    assert config["min_daily_volume_usd"] == 50000.0
    assert config["scanner_mode"] == "curated"  # sin tocar
    assert config["max_bid_ask_spread_pct"] == 1.5  # sin tocar


# --- get_scanner_universe: 'curated' es CERO cambio de comportamiento -------

def test_get_scanner_universe_modo_curated_es_el_universo_fijo_de_siempre(temp_db):
    assert scanner.get_scanner_universe(temp_db) == UNIVERSE


def test_get_scanner_universe_default_sin_configurar_nada_es_curated(temp_db):
    # Una instalacion nueva (nadie toco Ajustes todavia) tiene que comportarse
    # exactamente igual que antes de que existiera este modulo.
    config = db.get_signal_config(temp_db)
    assert config["scanner_mode"] == "curated"
    assert scanner.get_scanner_universe(temp_db) == UNIVERSE


def test_get_scanner_universe_global_filtered_llama_al_escaneo_real(temp_db, monkeypatch):
    db.update_signal_config(temp_db, scanner_mode="global_filtered")
    monkeypatch.setattr(scanner, "scan_global_filtered", lambda params: [("FAKE", "accion")])
    assert scanner.get_scanner_universe(temp_db) == [("FAKE", "accion")]


def test_get_scanner_universe_global_filtered_cachea_resultado(temp_db, monkeypatch):
    db.update_signal_config(temp_db, scanner_mode="global_filtered")
    calls = {"n": 0}

    def fake_scan(params):
        calls["n"] += 1
        return [("FAKE", "accion")]

    monkeypatch.setattr(scanner, "scan_global_filtered", fake_scan)
    scanner.get_scanner_universe(temp_db)
    scanner.get_scanner_universe(temp_db)
    assert calls["n"] == 1  # segunda llamada usa el cache, no reescanea


def test_get_scanner_universe_invalida_cache_si_cambian_los_parametros(temp_db, monkeypatch):
    db.update_signal_config(temp_db, scanner_mode="global_filtered", min_daily_volume_usd=20000.0)
    calls = {"n": 0}

    def fake_scan(params):
        calls["n"] += 1
        return [(str(params["min_daily_volume_usd"]), "accion")]

    monkeypatch.setattr(scanner, "scan_global_filtered", fake_scan)
    scanner.get_scanner_universe(temp_db)
    db.update_signal_config(temp_db, min_daily_volume_usd=999.0)
    scanner.get_scanner_universe(temp_db)
    assert calls["n"] == 2  # parametros distintos -- no puede servir el cache viejo


# --- _passes_cheap_liquidity_gate (volumen + spread, datos de /live/*) ------

def _row(c=1000.0, v=10000.0, px_bid=995.0, px_ask=1005.0):
    return {"c": c, "v": v, "px_bid": px_bid, "px_ask": px_ask}


def test_passes_cheap_liquidity_gate_ok_con_volumen_y_spread_dentro_del_piso():
    params = {"min_daily_volume_usd": 100.0, "max_bid_ask_spread_pct": 5.0}
    # notional = 1000*10000 = 10_000_000 ARS / ccl 1000 = 10_000 USD
    assert scanner._passes_cheap_liquidity_gate(_row(), ccl_rate=1000.0, params=params) is True


def test_passes_cheap_liquidity_gate_rechaza_volumen_insuficiente():
    params = {"min_daily_volume_usd": 1_000_000.0, "max_bid_ask_spread_pct": 5.0}
    assert scanner._passes_cheap_liquidity_gate(_row(), ccl_rate=1000.0, params=params) is False


def test_passes_cheap_liquidity_gate_rechaza_spread_ancho():
    params = {"min_daily_volume_usd": 100.0, "max_bid_ask_spread_pct": 0.1}
    # spread real: (1005-995)/1000*100 = 1.0% > 0.1% permitido
    assert scanner._passes_cheap_liquidity_gate(_row(), ccl_rate=1000.0, params=params) is False


def test_passes_cheap_liquidity_gate_sin_ccl_rate_nunca_pasa():
    params = {"min_daily_volume_usd": 1.0, "max_bid_ask_spread_pct": 100.0}
    assert scanner._passes_cheap_liquidity_gate(_row(), ccl_rate=None, params=params) is False


def test_passes_cheap_liquidity_gate_sin_puntas_de_bid_ask_no_pasa():
    params = {"min_daily_volume_usd": 1.0, "max_bid_ask_spread_pct": 100.0}
    row = _row(px_bid=None, px_ask=None)
    assert scanner._passes_cheap_liquidity_gate(row, ccl_rate=1000.0, params=params) is False


def test_passes_cheap_liquidity_gate_sin_volumen_o_precio_no_pasa():
    params = {"min_daily_volume_usd": 1.0, "max_bid_ask_spread_pct": 100.0}
    assert scanner._passes_cheap_liquidity_gate(_row(v=0), ccl_rate=1000.0, params=params) is False
    assert scanner._passes_cheap_liquidity_gate(_row(c=0), ccl_rate=1000.0, params=params) is False


# --- _operating_days_ok (dias operados del ultimo mes, data912 historico) ---

def _historical_df(volumes):
    return pd.DataFrame({
        "timestamps": pd.date_range(end=pd.Timestamp.now(), periods=len(volumes), freq="D"),
        "volume": volumes,
    })


def test_operating_days_ok_cuenta_solo_ruedas_con_volumen_positivo(monkeypatch):
    # 20 dias con volumen, 10 en cero -- pide minimo 15.
    df = _historical_df([0] * 10 + [100] * 20)
    monkeypatch.setattr(scanner.fetchers, "fetch_data912_historical", lambda symbol, asset_type: df)
    assert scanner._operating_days_ok("GGAL", "accion", {"min_operating_days_month": 15}) is True


def test_operating_days_ok_rechaza_si_no_llega_al_piso(monkeypatch):
    df = _historical_df([0] * 20 + [100] * 10)
    monkeypatch.setattr(scanner.fetchers, "fetch_data912_historical", lambda symbol, asset_type: df)
    assert scanner._operating_days_ok("GGAL", "accion", {"min_operating_days_month": 15}) is False


def test_operating_days_ok_falla_de_red_degrada_a_false(monkeypatch):
    def fake_fetch(symbol, asset_type):
        raise ConnectionError("simulado")
    monkeypatch.setattr(scanner.fetchers, "fetch_data912_historical", fake_fetch)
    assert scanner._operating_days_ok("GGAL", "accion", {"min_operating_days_month": 1}) is False


def test_operating_days_ok_historico_vacio_degrada_a_false(monkeypatch):
    monkeypatch.setattr(scanner.fetchers, "fetch_data912_historical", lambda symbol, asset_type: pd.DataFrame({"timestamps": [], "volume": []}))
    assert scanner._operating_days_ok("GGAL", "accion", {"min_operating_days_month": 1}) is False


# --- scan_global_filtered (orquestacion: live barato primero, historico caro ---
# solo sobre los sobrevivientes) ---------------------------------------------

def test_scan_global_filtered_sin_ccl_devuelve_lista_vacia(monkeypatch):
    monkeypatch.setattr(scanner, "_current_ccl_rate", lambda: None)
    assert scanner.scan_global_filtered({"min_daily_volume_usd": 1, "max_bid_ask_spread_pct": 100, "min_operating_days_month": 0}) == []


def test_scan_global_filtered_filtra_por_liquidez_y_dias_operados(monkeypatch):
    live_rows_by_endpoint = {
        "arg_stocks": [
            {"symbol": "LIQUIDO", **_row()},
            {"symbol": "ILIQUIDO", **_row(v=1)},  # no pasa el filtro barato de volumen
        ],
        "arg_cedears": [],
        "arg_bonds": [],
    }
    monkeypatch.setattr(scanner.fetchers, "fetch_data912_live", lambda endpoint: live_rows_by_endpoint[endpoint])
    monkeypatch.setattr(scanner.fetchers, "fetch_data912_historical", lambda symbol, asset_type: _historical_df([100] * 20))

    params = {"min_daily_volume_usd": 100.0, "max_bid_ask_spread_pct": 5.0, "min_operating_days_month": 15}
    result = scanner.scan_global_filtered(params, ccl_rate=1000.0)
    assert result == [("LIQUIDO", "accion")]


def test_scan_global_filtered_descarta_si_no_pasa_dias_operados(monkeypatch):
    monkeypatch.setattr(
        scanner.fetchers, "fetch_data912_live",
        lambda endpoint: [{"symbol": "X", **_row()}] if endpoint == "arg_stocks" else [],
    )
    monkeypatch.setattr(scanner.fetchers, "fetch_data912_historical", lambda symbol, asset_type: _historical_df([0] * 30))

    params = {"min_daily_volume_usd": 100.0, "max_bid_ask_spread_pct": 5.0, "min_operating_days_month": 15}
    assert scanner.scan_global_filtered(params, ccl_rate=1000.0) == []


def test_scan_global_filtered_una_fuente_caida_no_rompe_el_escaneo_completo(monkeypatch):
    def fake_live(endpoint):
        if endpoint == "arg_stocks":
            raise ConnectionError("simulado")
        return [{"symbol": "CEDEAR_OK", **_row()}] if endpoint == "arg_cedears" else []

    monkeypatch.setattr(scanner.fetchers, "fetch_data912_live", fake_live)
    monkeypatch.setattr(scanner.fetchers, "fetch_data912_historical", lambda symbol, asset_type: _historical_df([100] * 20))

    params = {"min_daily_volume_usd": 100.0, "max_bid_ask_spread_pct": 5.0, "min_operating_days_month": 15}
    assert scanner.scan_global_filtered(params, ccl_rate=1000.0) == [("CEDEAR_OK", "cedear")]
