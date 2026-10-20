"""Tests del Motor de Propuestas (ver conversacion, portfolio/proposals.py).
Foco: el bug real encontrado en la auditoria del 19/10/2026 (hallazgo 1.3) --
Kronos validado y el tecnico con edge compartian el mismo origen ('senal'),
asi que sync_proposals (que dedupea por (symbol, origen)) los trataba como
UNA sola propuesta cuando en realidad son 2 fuentes independientes que pueden
apuntar a direcciones opuestas el mismo dia -- la propuesta quedaba con la
etiqueta de una señal y el texto de la otra, ademas de duplicarse en la
corrida en que ambas aparecian por primera vez.

DB temporal propia (mismo patron que el resto del suite), red/Kronos siempre
mockeados via monkeypatch sobre portfolio.routes (import tardio dentro de
_validated_signal_candidates, ver ese modulo)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from portfolio import accuracy as accuracy_module
from portfolio import db, proposals


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_proposals.db")
    monkeypatch.setattr(db, "DB_PATH", db_path)
    conn = db.get_connection(db_path)
    yield conn
    conn.close()


def _add_holding(conn, symbol="IBM", type_="cedear"):
    instrument_id = db.add_instrument(conn, symbol, type_, "byma", "data912")
    db.add_transaction(conn, instrument_id, "2026-01-01", "compra", 1, 100)
    return instrument_id


def _mock_validated_kronos(monkeypatch, instrument_id, signal="comprar"):
    """Fuerza el camino 'Kronos validado' de _validated_signal_candidates:
    accuracy validada + un forecast en vivo con `signal`."""
    import portfolio.routes as routes

    monkeypatch.setattr(routes, "_get_cached_accuracy", lambda conn, iid: {"forecasts_evaluated": 42, "directional_hit_pct": 56.1})
    monkeypatch.setattr(
        accuracy_module, "compute_signal_validation",
        lambda acc, config: {"validated": True, "reason": f"{acc['directional_hit_pct']}% de acierto directional sobre {acc['forecasts_evaluated']} pronósticos no superpuestos."},
    )
    forecast_row = {"status": "done", "result_json": f'{{"signal": "{signal}"}}'}
    monkeypatch.setattr(db, "get_latest_forecast", lambda conn, **kw: forecast_row if kw.get("instrument_id") == instrument_id else None)


def _mock_technical_edge(monkeypatch, tecnico="vender", edge=12.0, n=50):
    """Fuerza el camino 'tecnico con edge' de _validated_signal_candidates."""
    import portfolio.routes as routes

    monkeypatch.setattr(routes, "_get_technical_row", lambda conn, instrument, config: {"tecnico": tecnico, "edge": edge, "n": n})


def _config(conn):
    return db.get_signal_config(conn)


# --- _validated_signal_candidates: origenes distintos para Kronos vs. tecnico --

def test_senal_kronos_y_senal_tecnica_son_origenes_distintos(temp_db, monkeypatch):
    instrument_id = _add_holding(temp_db, "IBM")
    _mock_validated_kronos(monkeypatch, instrument_id, signal="comprar")
    _mock_technical_edge(monkeypatch, tecnico="vender", edge=12.0, n=50)

    holdings = db.get_holdings_with_quotes(temp_db)
    candidates = proposals._validated_signal_candidates(temp_db, holdings, _config(temp_db))

    origenes = sorted(c["origen"] for c in candidates if c["symbol"] == "IBM")
    assert origenes == ["senal_kronos", "senal_tecnica"]


def test_senal_kronos_y_senal_tecnica_nunca_se_pisan_el_tipo_sugerido(temp_db, monkeypatch):
    # Direcciones OPUESTAS a proposito (el caso real de IBM en la auditoria).
    instrument_id = _add_holding(temp_db, "IBM")
    _mock_validated_kronos(monkeypatch, instrument_id, signal="comprar")
    _mock_technical_edge(monkeypatch, tecnico="vender", edge=12.0, n=50)

    holdings = db.get_holdings_with_quotes(temp_db)
    candidates = [c for c in proposals._validated_signal_candidates(temp_db, holdings, _config(temp_db)) if c["symbol"] == "IBM"]

    kronos = next(c for c in candidates if c["origen"] == "senal_kronos")
    tecnica = next(c for c in candidates if c["origen"] == "senal_tecnica")
    assert kronos["tipo_sugerido"] == "comprar"
    assert "comprar" in kronos["texto"]
    assert tecnica["tipo_sugerido"] == "venta"
    assert "vender" in tecnica["texto"]


# --- sync_proposals: la colision real (ver auditoria, hallazgo 1.3) ----------

def test_sync_proposals_no_duplica_ni_mezcla_cuando_kronos_y_tecnico_divergen(temp_db, monkeypatch):
    """Regresion directa del bug: ANTES del fix, ambos candidatos salian con
    origen='senal' -- sync_proposals (dedup por (symbol, origen)) insertaba
    las 2 filas en la misma corrida (active_by_key es una foto de ANTES del
    loop, nunca se actualiza con los inserts del propio loop) y, en corridas
    siguientes, una pisaba texto/evidencia de la otra sin tocar tipo_sugerido.
    Con origenes distintos, cada una vive en su propia fila consistente."""
    monkeypatch.setattr(proposals, "evaluate_all", lambda conn: [
        {"origen": "senal_kronos", "symbol": "IBM", "tipo_sugerido": "comprar",
         "texto": "Kronos validado sugiere comprar en IBM.", "evidencia": "56,1% de acierto sobre 42 pronósticos."},
        {"origen": "senal_tecnica", "symbol": "IBM", "tipo_sugerido": "venta",
         "texto": "RSI técnico sugiere vender en IBM: edge +12,0pts sobre 50 casos.", "evidencia": "Backtest RSI vs. tasa base: edge +12,0 puntos sobre 50 señales pasadas."},
    ])

    proposals.sync_proposals(temp_db)
    ibm_rows = [p for p in db.list_proposals(temp_db) if p["symbol"] == "IBM"]

    assert len(ibm_rows) == 2  # nunca colapsan en una sola fila con datos mezclados
    by_origen = {p["origen"]: p for p in ibm_rows}
    assert by_origen["senal_kronos"]["tipo_sugerido"] == "comprar"
    assert "comprar" in by_origen["senal_kronos"]["texto"]
    assert by_origen["senal_tecnica"]["tipo_sugerido"] == "venta"
    assert "vender" in by_origen["senal_tecnica"]["texto"]

    # Segunda corrida (mismos candidatos): no se vuelve a duplicar.
    proposals.sync_proposals(temp_db)
    ibm_rows_again = [p for p in db.list_proposals(temp_db) if p["symbol"] == "IBM"]
    assert len(ibm_rows_again) == 2


def test_sync_proposals_actualiza_texto_sin_tocar_la_otra_senal(temp_db, monkeypatch):
    """Al cambiar el texto/evidencia de UNA señal (ej. nuevo % de acierto),
    la OTRA fila (origen distinto) queda intacta -- antes, al compartir
    origen, una actualizacion asi pisaba la fila equivocada."""
    monkeypatch.setattr(proposals, "evaluate_all", lambda conn: [
        {"origen": "senal_kronos", "symbol": "IBM", "tipo_sugerido": "comprar",
         "texto": "Kronos validado sugiere comprar en IBM.", "evidencia": "56,1% de acierto sobre 42 pronósticos."},
        {"origen": "senal_tecnica", "symbol": "IBM", "tipo_sugerido": "venta",
         "texto": "RSI técnico sugiere vender en IBM: edge +12,0pts sobre 50 casos.", "evidencia": "Backtest RSI vs. tasa base: edge +12,0 puntos sobre 50 señales pasadas."},
    ])
    proposals.sync_proposals(temp_db)

    monkeypatch.setattr(proposals, "evaluate_all", lambda conn: [
        {"origen": "senal_kronos", "symbol": "IBM", "tipo_sugerido": "comprar",
         "texto": "Kronos validado sugiere comprar en IBM.", "evidencia": "58,0% de acierto sobre 43 pronósticos."},
        {"origen": "senal_tecnica", "symbol": "IBM", "tipo_sugerido": "venta",
         "texto": "RSI técnico sugiere vender en IBM: edge +12,0pts sobre 50 casos.", "evidencia": "Backtest RSI vs. tasa base: edge +12,0 puntos sobre 50 señales pasadas."},
    ])
    proposals.sync_proposals(temp_db)

    ibm_rows = {p["origen"]: p for p in db.list_proposals(temp_db) if p["symbol"] == "IBM"}
    assert len(ibm_rows) == 2
    assert "58,0%" in ibm_rows["senal_kronos"]["evidencia"]
    assert ibm_rows["senal_tecnica"]["evidencia"] == "Backtest RSI vs. tasa base: edge +12,0 puntos sobre 50 señales pasadas."


def test_sync_proposals_expira_senal_que_ya_no_aplica_sin_tocar_la_otra(temp_db, monkeypatch):
    monkeypatch.setattr(proposals, "evaluate_all", lambda conn: [
        {"origen": "senal_kronos", "symbol": "IBM", "tipo_sugerido": "comprar",
         "texto": "Kronos validado sugiere comprar en IBM.", "evidencia": "56,1% de acierto sobre 42 pronósticos."},
        {"origen": "senal_tecnica", "symbol": "IBM", "tipo_sugerido": "venta",
         "texto": "RSI técnico sugiere vender en IBM: edge +12,0pts sobre 50 casos.", "evidencia": "Backtest RSI vs. tasa base: edge +12,0 puntos sobre 50 señales pasadas."},
    ])
    proposals.sync_proposals(temp_db)

    # Al dia siguiente, el tecnico ya no tiene edge (deja de proponerse) pero Kronos sigue.
    monkeypatch.setattr(proposals, "evaluate_all", lambda conn: [
        {"origen": "senal_kronos", "symbol": "IBM", "tipo_sugerido": "comprar",
         "texto": "Kronos validado sugiere comprar en IBM.", "evidencia": "56,1% de acierto sobre 42 pronósticos."},
    ])
    proposals.sync_proposals(temp_db)

    ibm_rows = {p["origen"]: p for p in db.list_proposals(temp_db) if p["symbol"] == "IBM"}
    assert ibm_rows["senal_kronos"]["estado"] in ("nueva", "vista")
    assert ibm_rows["senal_tecnica"]["estado"] == "expirada"


# --- _rebalance_candidates: monto sugerido con la moneda explicita (ver
# conversacion/auditoria 19/10/2026, hallazgo 3: "'Bonos: comprar aprox. 577'
# sin unidades") ---------------------------------------------------------

def test_rebalance_candidates_incluye_la_moneda_en_el_monto_sugerido(temp_db):
    db.upsert_target_allocation(temp_db, "bono", 50.0)
    config = db.get_signal_config(temp_db)
    holdings_display = [
        {"valor_actual": 100.0, "instrument": {"type": "bono"}},
        {"valor_actual": 900.0, "instrument": {"type": "accion"}},
    ]
    candidates = proposals._rebalance_candidates(temp_db, holdings_display, [], config, display_currency="USD")
    assert len(candidates) == 1
    assert "USD" in candidates[0]["texto"]
    assert "aprox. USD" in candidates[0]["texto"]


def test_rebalance_candidates_default_ars_si_no_se_pasa_moneda(temp_db):
    db.upsert_target_allocation(temp_db, "bono", 50.0)
    config = db.get_signal_config(temp_db)
    holdings_display = [
        {"valor_actual": 100.0, "instrument": {"type": "bono"}},
        {"valor_actual": 900.0, "instrument": {"type": "accion"}},
    ]
    candidates = proposals._rebalance_candidates(temp_db, holdings_display, [], config)
    assert "aprox. ARS" in candidates[0]["texto"]


# --- _suggest_cantidad: Fase 2 del plan de unificacion (monto sugerido) -------

def _insert_price_history(conn, instrument_id, closes, start="2026-01-01"):
    import pandas as pd

    dates = pd.date_range(start, periods=len(closes))
    for d, c in zip(dates, closes):
        conn.execute(
            "INSERT INTO price_history (instrument_id, timestamp, open, high, low, close, volume) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (instrument_id, d.isoformat(), c, c * 1.02, c * 0.98, c, 1000),
        )
    conn.commit()


def _holding(instrument_id, cantidad=10.0, precio_actual=100.0, type_="cedear"):
    valor_actual = cantidad * precio_actual if precio_actual else None
    return {
        "instrument": {"id": instrument_id, "type": type_},
        "cantidad": cantidad,
        "precio_actual": precio_actual,
        "valor_actual": valor_actual,
    }


def test_suggest_cantidad_venta_es_la_tenencia_real_completa(temp_db):
    instrument_id = _add_holding(temp_db, "IBM")
    h = _holding(instrument_id, cantidad=7.0)
    assert proposals._suggest_cantidad(temp_db, h, "venta") == 7.0


def test_suggest_cantidad_compra_sin_precio_no_inventa_nada(temp_db):
    instrument_id = _add_holding(temp_db, "IBM")
    h = _holding(instrument_id, precio_actual=None)
    assert proposals._suggest_cantidad(temp_db, h, "compra") is None


def test_suggest_cantidad_compra_sin_historico_suficiente_no_inventa_nada(temp_db):
    instrument_id = _add_holding(temp_db, "IBM")
    _insert_price_history(temp_db, instrument_id, [100.0] * 5)  # menos que _ATR_MIN_ROWS
    h = _holding(instrument_id)
    assert proposals._suggest_cantidad(temp_db, h, "compra") is None


def test_suggest_cantidad_compra_con_historico_da_cantidad_positiva(temp_db):
    instrument_id = _add_holding(temp_db, "IBM")
    closes = [100.0 + (i % 5) for i in range(30)]  # algo de ATR real, sin tendencia
    _insert_price_history(temp_db, instrument_id, closes)
    h = _holding(instrument_id, cantidad=10.0, precio_actual=100.0, type_="bono")  # bono: fraccionable
    cantidad = proposals._suggest_cantidad(temp_db, h, "compra")
    assert cantidad is not None
    assert cantidad > 0


def test_suggest_cantidad_compra_redondea_entero_para_accion_cedear(temp_db):
    instrument_id = _add_holding(temp_db, "IBM")
    closes = [100.0 + (i % 5) for i in range(30)]
    _insert_price_history(temp_db, instrument_id, closes)
    h = _holding(instrument_id, cantidad=10.0, precio_actual=100.0, type_="cedear")
    cantidad = proposals._suggest_cantidad(temp_db, h, "compra")
    assert cantidad is None or cantidad == float(int(cantidad))


def test_suggest_cantidad_otro_tipo_no_sugiere_nada(temp_db):
    instrument_id = _add_holding(temp_db, "IBM")
    h = _holding(instrument_id)
    assert proposals._suggest_cantidad(temp_db, h, "mantener") is None
