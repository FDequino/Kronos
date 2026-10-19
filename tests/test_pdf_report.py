"""Tests del Reporte PDF configurable (ver conversacion, portfolio/pdf_report.py
-- rediseño ejecutivo completo). Corre contra una DB temporal propia (mismo
patron que tests/test_paper_trading.py), nunca toca portfolio/portfolio.db
real ni pega a red real: fetchers.resolve_usd_rate y
live_signals.compute_today_signals se monkeypatchean para TODOS los tests de
este archivo (el segundo evalúa contra data912/Chronos-Bolt reales si no se
mockea -- ver _opportunities_section, gateado por pdf_include_opportunities
justamente por ese costo de red)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from portfolio import db, fetchers, live_signals, pdf_report

ALL_FLAGS_OFF = {
    "pdf_currency": "DUAL", "pdf_include_paper_trading": 0,
    "pdf_include_risk_metrics": 0, "pdf_include_yield_caucion": 0, "pdf_include_opportunities": 0,
}

_EMPTY_SIGNALS_RESULT = {
    "core_signals": [], "satellite_signals": [], "spy_above_ema50": True, "market_regime": "normal",
}


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_pdf_report.db")
    monkeypatch.setattr(db, "DB_PATH", db_path)
    conn = db.get_connection(db_path)
    # Nunca pegar a dolarapi/BNA reales en un test -- cotizacion fija conocida.
    monkeypatch.setattr(fetchers, "resolve_usd_rate", lambda settings: 1000.0)
    # Nunca pegar a data912/Chronos-Bolt reales -- por defecto sin señales,
    # los tests que SI quieren probar _opportunities_section pisan esto de nuevo.
    monkeypatch.setattr(live_signals, "compute_today_signals", lambda *a, **k: dict(_EMPTY_SIGNALS_RESULT))
    yield conn
    conn.close()


def _seed_holding(conn, symbol="GGAL", currency="ARS", cantidad=10.0, precio=100.0, quote_price=120.0):
    inst_id = db.add_instrument(conn, symbol, "accion", "byma", "data912", currency=currency)
    db.add_transaction(conn, inst_id, "2025-01-01", "compra", cantidad, precio)
    db.upsert_quote(conn, inst_id, quote_price, "data912")
    return inst_id


def _find(sections, kind):
    return next((s for s in sections if s["kind"] == kind), None)


def _holdings_section(sections):
    return _find(sections, "holdings_table")


# --- build_report_sections: inclusion/exclusion por flag --------------------

def test_resumen_y_tenencias_siempre_presentes_incluso_sin_otros_flags(temp_db):
    sections = pdf_report.build_report_sections(temp_db, config=ALL_FLAGS_OFF)
    kinds = [s["kind"] for s in sections]
    assert kinds == ["resumen_ejecutivo", "holdings_table"]


@pytest.mark.parametrize("flag,kind", [
    ("pdf_include_risk_metrics", "risk_metrics"),
    ("pdf_include_paper_trading", "paper_trading_summary"),
    ("pdf_include_yield_caucion", "yield_caucion"),
    ("pdf_include_opportunities", "oportunidades_dia"),
])
def test_flag_activo_agrega_su_seccion(temp_db, flag, kind):
    config = dict(ALL_FLAGS_OFF)
    config[flag] = 1
    sections = pdf_report.build_report_sections(temp_db, config=config)
    kinds = [s["kind"] for s in sections]
    assert kind in kinds


def test_todos_los_flags_activos_incluye_las_6_secciones(temp_db):
    config = {
        "pdf_currency": "DUAL", "pdf_include_paper_trading": 1,
        "pdf_include_risk_metrics": 1, "pdf_include_yield_caucion": 1, "pdf_include_opportunities": 1,
    }
    sections = pdf_report.build_report_sections(temp_db, config=config)
    kinds = {s["kind"] for s in sections}
    assert kinds == {
        "resumen_ejecutivo", "holdings_table", "risk_metrics", "paper_trading_summary",
        "yield_caucion", "oportunidades_dia",
    }


def test_moneda_invalida_degrada_a_dual_sin_romper(temp_db):
    config = dict(ALL_FLAGS_OFF)
    config["pdf_currency"] = "ALGO_INVALIDO"
    sections = pdf_report.build_report_sections(temp_db, config=config)
    assert _holdings_section(sections)["data"]["currency"] == "DUAL"


# --- pdf_currency afecta los datos de la seccion de tenencias ---------------

def test_pdf_currency_usd_solo_trae_valor_usd(temp_db):
    _seed_holding(temp_db, currency="ARS")
    config = dict(ALL_FLAGS_OFF, pdf_currency="USD")
    sections = pdf_report.build_report_sections(temp_db, config=config)
    row = _holdings_section(sections)["data"]["rows"][0]
    assert "valor_usd" in row
    assert "valor_ars" not in row


def test_pdf_currency_ars_solo_trae_valor_ars(temp_db):
    _seed_holding(temp_db, currency="ARS")
    config = dict(ALL_FLAGS_OFF, pdf_currency="ARS")
    sections = pdf_report.build_report_sections(temp_db, config=config)
    row = _holdings_section(sections)["data"]["rows"][0]
    assert "valor_ars" in row
    assert "valor_usd" not in row


def test_pdf_currency_dual_trae_ambas_y_convierte_con_el_rate(temp_db):
    _seed_holding(temp_db, currency="ARS", cantidad=10.0, quote_price=120.0)  # valor_ars = 1200
    config = dict(ALL_FLAGS_OFF, pdf_currency="DUAL")
    sections = pdf_report.build_report_sections(temp_db, config=config)
    row = _holdings_section(sections)["data"]["rows"][0]
    assert row["valor_ars"] == pytest.approx(1200.0)
    assert row["valor_usd"] == pytest.approx(1200.0 / 1000.0)  # rate mockeado = 1000


def test_dual_value_sin_rate_devuelve_none_en_el_lado_no_convertible():
    valor_ars, valor_usd = pdf_report._dual_value(100.0, "ARS", rate=None)
    assert valor_ars == 100.0
    assert valor_usd is None


# --- Resumen Ejecutivo (Block 1) --------------------------------------------

def test_resumen_ejecutivo_sin_snapshot_previo_resultado_dia_es_none(temp_db):
    _seed_holding(temp_db, currency="ARS", cantidad=10.0, quote_price=120.0)
    sections = pdf_report.build_report_sections(temp_db, config=ALL_FLAGS_OFF)
    data = _find(sections, "resumen_ejecutivo")["data"]
    assert data["patrimonio_total_ars"] == pytest.approx(1200.0)
    assert data["patrimonio_total_usd"] == pytest.approx(1.2)
    assert data["resultado_dia_ars"] is None
    assert data["resultado_dia_pct"] is None
    assert data["capital_caucion_ars"] == 0.0


def test_resumen_ejecutivo_con_snapshot_previo_calcula_resultado_del_dia(temp_db):
    _seed_holding(temp_db, currency="ARS", cantidad=10.0, quote_price=120.0)  # patrimonio hoy = 1200
    db.upsert_portfolio_snapshot(temp_db, date="2025-01-01", valor_total=1000.0, pnl_total=0.0, risk_score=None)
    sections = pdf_report.build_report_sections(temp_db, config=ALL_FLAGS_OFF)
    data = _find(sections, "resumen_ejecutivo")["data"]
    assert data["resultado_dia_ars"] == pytest.approx(200.0)
    assert data["resultado_dia_pct"] == pytest.approx(20.0)


def test_resumen_ejecutivo_ignora_snapshot_de_hoy_mismo(temp_db, monkeypatch):
    from portfolio.tz import today_ar
    _seed_holding(temp_db, currency="ARS", cantidad=10.0, quote_price=120.0)
    db.upsert_portfolio_snapshot(temp_db, date="2025-01-01", valor_total=1000.0, pnl_total=0.0, risk_score=None)
    db.upsert_portfolio_snapshot(temp_db, date=today_ar().isoformat(), valor_total=1150.0, pnl_total=0.0, risk_score=None)
    sections = pdf_report.build_report_sections(temp_db, config=ALL_FLAGS_OFF)
    data = _find(sections, "resumen_ejecutivo")["data"]
    # debe comparar contra el snapshot de 2025-01-01 (1000), NUNCA contra el
    # de hoy (1150) -- "ayer" es el ultimo snapshot que NO sea de hoy.
    assert data["resultado_dia_ars"] == pytest.approx(200.0)


# --- Paper Trading: posiciones con precio en vivo (Block 2) -----------------

def _seed_paper_position(conn, symbol="AAPL", engine="satellite", tactic="mean_reversion"):
    db.insert_paper_position(
        conn, symbol=symbol, asset_type="cedear", engine=engine, tactic=tactic, quantity=2.0,
        entry_price_usd=100.0, entry_price_ars=100000.0, entry_date="2025-01-01",
        take_profit_price_usd=110.0, take_profit_price_ars=110000.0,
        stop_loss_price_usd=90.0, stop_loss_price_ars=90000.0, max_hold_days=3,
    )


def test_paper_trading_section_con_precio_en_vivo_calcula_valor_y_pnl(temp_db, monkeypatch):
    _seed_paper_position(temp_db)
    monkeypatch.setattr(pdf_report.monitor, "current_prices_for_symbols", lambda conn, pairs: {"AAPL": (120.0, 120000.0)})
    config = dict(ALL_FLAGS_OFF, pdf_include_paper_trading=1)
    sections = pdf_report.build_report_sections(temp_db, config=config)
    positions = _find(sections, "paper_trading_summary")["data"]["positions_detail"]
    assert len(positions) == 1
    p = positions[0]
    assert p["symbol"] == "AAPL"
    assert p["current_price_usd"] == 120.0
    assert p["valor_actual_usd"] == pytest.approx(240.0)  # 2.0 * 120
    assert p["pnl_pct"] == pytest.approx(20.0)  # 120/100 - 1


def test_paper_trading_section_sin_precio_disponible_degrada_a_none(temp_db, monkeypatch):
    _seed_paper_position(temp_db)
    monkeypatch.setattr(pdf_report.monitor, "current_prices_for_symbols", lambda conn, pairs: {})
    config = dict(ALL_FLAGS_OFF, pdf_include_paper_trading=1)
    sections = pdf_report.build_report_sections(temp_db, config=config)
    p = _find(sections, "paper_trading_summary")["data"]["positions_detail"][0]
    assert p["current_price_usd"] is None
    assert p["valor_actual_usd"] is None
    assert p["pnl_pct"] is None


def test_paper_trading_section_fallo_de_red_degrada_sin_romper(temp_db, monkeypatch):
    _seed_paper_position(temp_db)

    def _boom(conn, pairs):
        raise ConnectionError("data912 no respondió")
    monkeypatch.setattr(pdf_report.monitor, "current_prices_for_symbols", _boom)
    config = dict(ALL_FLAGS_OFF, pdf_include_paper_trading=1)
    sections = pdf_report.build_report_sections(temp_db, config=config)
    p = _find(sections, "paper_trading_summary")["data"]["positions_detail"][0]
    assert p["current_price_usd"] is None


def test_paper_trading_section_sin_posiciones_abiertas(temp_db):
    config = dict(ALL_FLAGS_OFF, pdf_include_paper_trading=1)
    sections = pdf_report.build_report_sections(temp_db, config=config)
    assert _find(sections, "paper_trading_summary")["data"]["positions_detail"] == []


# --- Oportunidades del Día (Block 3) -----------------------------------------

def test_opportunities_section_sin_senales(temp_db):
    config = dict(ALL_FLAGS_OFF, pdf_include_opportunities=1)
    sections = pdf_report.build_report_sections(temp_db, config=config)
    data = _find(sections, "oportunidades_dia")["data"]
    assert data["available"] is True
    assert data["core_signals"] == []
    assert data["satellite_signals"] == []


def test_opportunities_section_con_senales(temp_db, monkeypatch):
    fake_signal = {
        "symbol": "GGAL", "entry_price_usd": 10.0, "entry_price_ars": 10000.0,
        "take_profit_price_usd": 11.0, "take_profit_price_ars": 11000.0, "take_profit_pct": 10.0,
        "initial_stop_price_usd": 9.0, "initial_stop_price_ars": 9000.0, "initial_stop_pct": 10.0,
        "max_hold_days": 3, "tactics": ["mean_reversion"],
    }
    monkeypatch.setattr(live_signals, "compute_today_signals", lambda *a, **k: {
        "core_signals": [], "satellite_signals": [fake_signal], "spy_above_ema50": True, "market_regime": "normal",
    })
    config = dict(ALL_FLAGS_OFF, pdf_include_opportunities=1)
    sections = pdf_report.build_report_sections(temp_db, config=config)
    data = _find(sections, "oportunidades_dia")["data"]
    assert data["satellite_signals"] == [fake_signal]


def test_opportunities_section_fallo_del_servicio_degrada_a_no_disponible(temp_db, monkeypatch):
    def _boom(*a, **k):
        raise Exception("Chronos-Bolt no respondió a tiempo")
    monkeypatch.setattr(live_signals, "compute_today_signals", _boom)
    config = dict(ALL_FLAGS_OFF, pdf_include_opportunities=1)
    sections = pdf_report.build_report_sections(temp_db, config=config)
    data = _find(sections, "oportunidades_dia")["data"]
    assert data["available"] is False
    assert "Chronos-Bolt" in data["error"]


# --- Formato de numeros es-AR (miles ".", decimales ",") --------------------

def test_fmt_es_number_separa_miles_y_decimales_estilo_argentino():
    assert pdf_report._fmt_es_number(1250.0, 2) == "1.250,00"
    assert pdf_report._fmt_es_number(1850000.0, 0) == "1.850.000"
    assert pdf_report._fmt_es_number(None) is None


def test_fmt_usd_y_fmt_ars_con_prefijo_y_sufijo():
    assert pdf_report._fmt_usd(1250.0) == "USD 1.250,00"
    assert pdf_report._fmt_ars(1850000.0) == "$ 1.850.000 ARS"
    assert pdf_report._fmt_usd(None) == "—"
    assert pdf_report._fmt_ars(None) == "—"


def test_fmt_pct_con_signo():
    assert pdf_report._fmt_pct(12.3456) == "+12,35%"
    assert pdf_report._fmt_pct(-5.0) == "-5,00%"
    assert pdf_report._fmt_pct(None) == "—"


# --- Traduccion de jerga tecnica a castellano --------------------------------

def test_traduccion_de_motores_y_tacticas():
    assert pdf_report._engine_label("core") == "Fondo de Mediano Plazo"
    assert pdf_report._engine_label("satellite") == "Estrategia Corto Plazo"
    assert pdf_report._tactic_label("mean_reversion") == "Rebote en Soporte"
    assert pdf_report._tactic_label("yield_sweep") == "Tasa Pasiva Caución"
    assert pdf_report._tactic_label(None) == "—"
    assert pdf_report._instrument_type_label("cedear") == "CEDEAR"


# --- render_pdf / generate_portfolio_pdf: bytes validos, nunca crashea -----

def test_render_pdf_cartera_vacia_no_crashea(temp_db):
    # Instalacion fresca, sin ninguna tenencia cargada -- caso real, debe
    # degradar con gracia, nunca romper.
    sections = pdf_report.build_report_sections(temp_db)
    pdf_bytes = pdf_report.render_pdf(sections)
    assert pdf_bytes.startswith(b"%PDF")
    assert len(pdf_bytes) > 0


def test_render_pdf_con_datos_reales_produce_pdf_valido(temp_db):
    _seed_holding(temp_db)
    _seed_paper_position(temp_db)
    sections = pdf_report.build_report_sections(temp_db, config={
        "pdf_currency": "DUAL", "pdf_include_paper_trading": 1,
        "pdf_include_risk_metrics": 1, "pdf_include_yield_caucion": 1, "pdf_include_opportunities": 1,
    })
    pdf_bytes = pdf_report.render_pdf(sections)
    assert pdf_bytes.startswith(b"%PDF")


def test_generate_portfolio_pdf_punto_de_entrada_unico(temp_db):
    _seed_holding(temp_db)
    pdf_bytes = pdf_report.generate_portfolio_pdf(temp_db)
    assert pdf_bytes.startswith(b"%PDF")


def test_generate_portfolio_pdf_escribe_a_disco_si_se_pide(temp_db, tmp_path):
    _seed_holding(temp_db)
    output_path = str(tmp_path / "reporte.pdf")
    pdf_bytes = pdf_report.generate_portfolio_pdf(temp_db, output_path=output_path)
    with open(output_path, "rb") as f:
        written = f.read()
    assert written == pdf_bytes


def test_render_pdf_kind_desconocido_se_omite_sin_romper(temp_db):
    sections = [{"title": "Rara", "kind": "seccion_inventada", "data": {}}]
    pdf_bytes = pdf_report.render_pdf(sections)
    assert pdf_bytes.startswith(b"%PDF")
