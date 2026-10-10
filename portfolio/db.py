import json
import os
import sqlite3
from datetime import datetime, timedelta

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "portfolio.db")
SCHEMA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")


def get_connection(db_path=None):
    conn = sqlite3.connect(db_path or DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    with open(SCHEMA_PATH) as f:
        conn.executescript(f.read())
    _migrate(conn)
    return conn


def _migrate(conn):
    """Altera tablas ya creadas cuando schema.sql no alcanza (CREATE TABLE IF NOT
    EXISTS no agrega columnas nuevas a una tabla preexistente)."""
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(forecasts)").fetchall()}
    if "as_of_date" not in cols:
        conn.execute("ALTER TABLE forecasts ADD COLUMN as_of_date TEXT")
        conn.commit()


def _row_to_dict(row):
    return dict(row) if row is not None else None


# --- Instrumentos ---------------------------------------------------------

def add_instrument(conn, symbol, type_, market, price_source, name=None,
                    currency="ARS", price_source_params=None):
    params_json = json.dumps(price_source_params) if price_source_params else None
    cur = conn.execute(
        """INSERT INTO instruments (symbol, name, type, market, currency, price_source, price_source_params)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (symbol, name, type_, market, currency, price_source, params_json),
    )
    conn.commit()
    return cur.lastrowid


def get_instrument(conn, instrument_id):
    row = conn.execute("SELECT * FROM instruments WHERE id = ?", (instrument_id,)).fetchone()
    return _row_to_dict(row)


def find_instrument(conn, symbol, market):
    row = conn.execute(
        "SELECT * FROM instruments WHERE symbol = ? AND market = ?", (symbol, market)
    ).fetchone()
    return _row_to_dict(row)


def list_instruments(conn, type_=None):
    if type_:
        rows = conn.execute("SELECT * FROM instruments WHERE type = ? ORDER BY symbol", (type_,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM instruments ORDER BY type, symbol").fetchall()
    return [_row_to_dict(r) for r in rows]


def update_instrument(conn, instrument_id, **fields):
    if not fields:
        return get_instrument(conn, instrument_id)
    if "price_source_params" in fields and fields["price_source_params"] is not None:
        fields["price_source_params"] = json.dumps(fields["price_source_params"])
    columns = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE instruments SET {columns} WHERE id = ?", (*fields.values(), instrument_id))
    conn.commit()
    return get_instrument(conn, instrument_id)


def delete_instrument(conn, instrument_id):
    conn.execute("DELETE FROM instruments WHERE id = ?", (instrument_id,))
    conn.commit()


# --- Transacciones ----------------------------------------------------------

def add_transaction(conn, instrument_id, fecha, tipo, cantidad, precio, comision=0, notas=None):
    cur = conn.execute(
        """INSERT INTO transactions (instrument_id, fecha, tipo, cantidad, precio, comision, notas)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (instrument_id, fecha, tipo, cantidad, precio, comision, notas),
    )
    conn.commit()
    return cur.lastrowid


def list_transactions(conn, instrument_id=None):
    if instrument_id:
        rows = conn.execute(
            "SELECT * FROM transactions WHERE instrument_id = ? ORDER BY fecha", (instrument_id,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM transactions ORDER BY fecha").fetchall()
    return [_row_to_dict(r) for r in rows]


def get_transaction(conn, transaction_id):
    row = conn.execute("SELECT * FROM transactions WHERE id = ?", (transaction_id,)).fetchone()
    return _row_to_dict(row)


def update_transaction(conn, transaction_id, **fields):
    if not fields:
        return get_transaction(conn, transaction_id)
    columns = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE transactions SET {columns} WHERE id = ?", (*fields.values(), transaction_id))
    conn.commit()
    return get_transaction(conn, transaction_id)


def delete_transaction(conn, transaction_id):
    conn.execute("DELETE FROM transactions WHERE id = ?", (transaction_id,))
    conn.commit()


def get_holdings(conn):
    """Tenencia actual y costo promedio ponderado por instrumento, derivados de transactions."""
    instruments = list_instruments(conn)
    holdings = []
    for inst in instruments:
        txs = list_transactions(conn, inst["id"])
        cantidad = 0.0
        costo_total = 0.0
        for tx in txs:
            if tx["tipo"] == "compra":
                costo_total += tx["cantidad"] * tx["precio"] + tx["comision"]
                cantidad += tx["cantidad"]
            else:
                if cantidad > 0:
                    costo_total -= (costo_total / cantidad) * tx["cantidad"]
                cantidad -= tx["cantidad"]
                costo_total -= tx["comision"]
        if cantidad <= 1e-9:
            continue
        costo_promedio = costo_total / cantidad if cantidad else 0.0
        holdings.append({
            "instrument": inst,
            "cantidad": cantidad,
            "costo_promedio": costo_promedio,
            "costo_total": costo_total,
        })
    return holdings


# --- Plazos fijos -----------------------------------------------------------

def add_plazo_fijo(conn, banco, tipo, monto, tna, fecha_inicio, plazo_dias, notas=None):
    fecha_vencimiento = (
        datetime.fromisoformat(fecha_inicio) + timedelta(days=plazo_dias)
    ).date().isoformat()
    cur = conn.execute(
        """INSERT INTO plazos_fijos (banco, tipo, monto, tna, fecha_inicio, plazo_dias, fecha_vencimiento, notas)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (banco, tipo, monto, tna, fecha_inicio, plazo_dias, fecha_vencimiento, notas),
    )
    conn.commit()
    return cur.lastrowid


def list_plazos_fijos(conn, estado=None):
    if estado:
        rows = conn.execute(
            "SELECT * FROM plazos_fijos WHERE estado = ? ORDER BY fecha_vencimiento", (estado,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM plazos_fijos ORDER BY fecha_vencimiento").fetchall()
    return [_row_to_dict(r) for r in rows]


def get_plazo_fijo(conn, plazo_fijo_id):
    row = conn.execute("SELECT * FROM plazos_fijos WHERE id = ?", (plazo_fijo_id,)).fetchone()
    return _row_to_dict(row)


def update_plazo_fijo(conn, plazo_fijo_id, **fields):
    if not fields:
        return get_plazo_fijo(conn, plazo_fijo_id)
    if "fecha_inicio" in fields or "plazo_dias" in fields:
        current = get_plazo_fijo(conn, plazo_fijo_id)
        fecha_inicio = fields.get("fecha_inicio", current["fecha_inicio"])
        plazo_dias = fields.get("plazo_dias", current["plazo_dias"])
        fields["fecha_vencimiento"] = (
            datetime.fromisoformat(fecha_inicio) + timedelta(days=plazo_dias)
        ).date().isoformat()
    columns = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE plazos_fijos SET {columns} WHERE id = ?", (*fields.values(), plazo_fijo_id))
    conn.commit()
    return get_plazo_fijo(conn, plazo_fijo_id)


def delete_plazo_fijo(conn, plazo_fijo_id):
    conn.execute("DELETE FROM plazos_fijos WHERE id = ?", (plazo_fijo_id,))
    conn.commit()


def get_plazos_fijos_resumen(conn, as_of=None):
    """Devengado a la fecha con interes simple: monto * tna * dias_transcurridos / 365."""
    as_of = as_of or datetime.now().date()
    resumen = []
    for pf in list_plazos_fijos(conn, estado="activo"):
        inicio = datetime.fromisoformat(pf["fecha_inicio"]).date()
        vencimiento = datetime.fromisoformat(pf["fecha_vencimiento"]).date()
        dias_transcurridos = min((as_of - inicio).days, pf["plazo_dias"])
        dias_transcurridos = max(dias_transcurridos, 0)
        devengado = pf["monto"] * pf["tna"] * dias_transcurridos / 365
        resumen.append({
            "plazo_fijo": pf,
            "dias_transcurridos": dias_transcurridos,
            "devengado": devengado,
            "valor_actual": pf["monto"] + devengado,
            "vencido": as_of >= vencimiento,
        })
    return resumen


# --- Cauciones ------------------------------------------------------------------

def add_caucion(conn, *, fecha_apertura, fecha_vencimiento, plazo_dias, monto_contado,
                 monto_futuro, tasa, interes_bruto, moneda="ARS", numero_boleto=None, notas=None):
    cur = conn.execute(
        """INSERT INTO cauciones (numero_boleto, fecha_apertura, fecha_vencimiento, plazo_dias,
               monto_contado, monto_futuro, tasa, interes_bruto, moneda, notas)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (numero_boleto, fecha_apertura, fecha_vencimiento, plazo_dias, monto_contado,
         monto_futuro, tasa, interes_bruto, moneda, notas),
    )
    conn.commit()
    return cur.lastrowid


def list_cauciones(conn):
    rows = conn.execute("SELECT * FROM cauciones ORDER BY fecha_vencimiento DESC").fetchall()
    return [_row_to_dict(r) for r in rows]


def get_caucion(conn, caucion_id):
    row = conn.execute("SELECT * FROM cauciones WHERE id = ?", (caucion_id,)).fetchone()
    return _row_to_dict(row)


def update_caucion(conn, caucion_id, **fields):
    if not fields:
        return get_caucion(conn, caucion_id)
    columns = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE cauciones SET {columns} WHERE id = ?", (*fields.values(), caucion_id))
    conn.commit()
    return get_caucion(conn, caucion_id)


def delete_caucion(conn, caucion_id):
    conn.execute("DELETE FROM cauciones WHERE id = ?", (caucion_id,))
    conn.commit()


def get_cauciones_resumen(conn, as_of=None):
    """Devengado proporcional al tiempo transcurrido sobre el interes bruto pactado."""
    as_of = as_of or datetime.now().date()
    resumen = []
    for c in list_cauciones(conn):
        inicio = datetime.fromisoformat(c["fecha_apertura"]).date()
        vencimiento = datetime.fromisoformat(c["fecha_vencimiento"]).date()
        vencido = as_of >= vencimiento
        dias_transcurridos = max(min((as_of - inicio).days, c["plazo_dias"]), 0)
        devengado = c["interes_bruto"] if vencido else c["interes_bruto"] * dias_transcurridos / c["plazo_dias"]
        resumen.append({
            "caucion": c,
            "dias_transcurridos": dias_transcurridos,
            "devengado": devengado,
            "valor_actual": c["monto_contado"] + devengado,
            "vencido": vencido,
        })
    return resumen


# --- Rentas y dividendos --------------------------------------------------------

def add_renta_dividendo(conn, *, symbol, fecha, tipo, moneda, bruto, neto,
                          gastos=0, instrument_id=None, notas=None):
    cur = conn.execute(
        """INSERT INTO rentas_dividendos (instrument_id, symbol, fecha, tipo, moneda, bruto, gastos, neto, notas)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (instrument_id, symbol, fecha, tipo, moneda, bruto, gastos, neto, notas),
    )
    conn.commit()
    return cur.lastrowid


def list_rentas_dividendos(conn):
    rows = conn.execute("SELECT * FROM rentas_dividendos ORDER BY fecha DESC").fetchall()
    return [_row_to_dict(r) for r in rows]


# --- Cotizaciones -------------------------------------------------------------

def upsert_quote(conn, instrument_id, price, source, bid=None, ask=None, fetched_at=None):
    fetched_at = fetched_at or datetime.now().isoformat()
    conn.execute(
        """INSERT INTO quotes_latest (instrument_id, price, bid, ask, fetched_at, source)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(instrument_id) DO UPDATE SET
               price = excluded.price, bid = excluded.bid, ask = excluded.ask,
               fetched_at = excluded.fetched_at, source = excluded.source""",
        (instrument_id, price, bid, ask, fetched_at, source),
    )
    conn.commit()


def get_quote(conn, instrument_id):
    row = conn.execute("SELECT * FROM quotes_latest WHERE instrument_id = ?", (instrument_id,)).fetchone()
    return _row_to_dict(row)


def get_holdings_with_quotes(conn):
    """Holdings + cotizacion actual + P&L. precio_actual/valor_actual/pnl quedan None si no hay quote aun."""
    holdings = get_holdings(conn)
    for h in holdings:
        quote = get_quote(conn, h["instrument"]["id"])
        if quote:
            valor_actual = h["cantidad"] * quote["price"]
            h["precio_actual"] = quote["price"]
            h["valor_actual"] = valor_actual
            h["pnl"] = valor_actual - h["costo_total"]
            h["pnl_pct"] = (h["pnl"] / h["costo_total"] * 100) if h["costo_total"] else None
            h["cotizado_at"] = quote["fetched_at"]
        else:
            h["precio_actual"] = None
            h["valor_actual"] = None
            h["pnl"] = None
            h["pnl_pct"] = None
            h["cotizado_at"] = None
    return holdings


# --- Historico de precios -----------------------------------------------------

def get_price_history(conn, instrument_id, limit=None):
    query = "SELECT * FROM price_history WHERE instrument_id = ? ORDER BY timestamp"
    if limit:
        query = f"SELECT * FROM ({query} DESC LIMIT {int(limit)}) ORDER BY timestamp"
    rows = conn.execute(query, (instrument_id,)).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_price_history_as_of(conn, instrument_id, as_of_date, limit=None):
    """Igual que get_price_history pero solo con velas hasta as_of_date (inclusive) --
    para que un backtest nunca vea datos que todavia no existian en esa fecha."""
    query = "SELECT * FROM price_history WHERE instrument_id = ? AND timestamp <= ? ORDER BY timestamp"
    if limit:
        query = f"SELECT * FROM ({query} DESC LIMIT {int(limit)}) ORDER BY timestamp"
    rows = conn.execute(query, (instrument_id, f"{as_of_date}T23:59:59")).fetchall()
    return [_row_to_dict(r) for r in rows]


# --- Config de senales ----------------------------------------------------------

def get_signal_config(conn):
    return _row_to_dict(conn.execute("SELECT * FROM signal_config WHERE id = 1").fetchone())


def update_signal_config(conn, **fields):
    if not fields:
        return get_signal_config(conn)
    columns = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(
        f"UPDATE signal_config SET {columns}, updated_at = datetime('now') WHERE id = 1",
        tuple(fields.values()),
    )
    conn.commit()
    return get_signal_config(conn)


# --- Preferencias de la app (moneda, variante BNA) -----------------------------

def get_app_settings(conn):
    return _row_to_dict(conn.execute("SELECT * FROM app_settings WHERE id = 1").fetchone())


def update_app_settings(conn, **fields):
    if not fields:
        return get_app_settings(conn)
    columns = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(
        f"UPDATE app_settings SET {columns}, updated_at = datetime('now') WHERE id = 1",
        tuple(fields.values()),
    )
    conn.commit()
    return get_app_settings(conn)


# --- Forecasts (jobs + cache) --------------------------------------------------

def insert_forecast_job(conn, *, instrument_id, symbol, market, asset_type, model_key,
                         lookback, pred_len, temperature, top_p, sample_count,
                         expected_duration_seconds, as_of_date=None):
    cur = conn.execute(
        """INSERT INTO forecasts (instrument_id, symbol, market, asset_type, model_key,
               lookback, pred_len, temperature, top_p, sample_count, expected_duration_seconds,
               as_of_date)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (instrument_id, symbol, market, asset_type, model_key, lookback, pred_len,
         temperature, top_p, sample_count, expected_duration_seconds, as_of_date),
    )
    conn.commit()
    return cur.lastrowid


def mark_forecast_running(conn, forecast_id):
    conn.execute(
        "UPDATE forecasts SET status = 'running', started_at = datetime('now') WHERE id = ?",
        (forecast_id,),
    )
    conn.commit()


def mark_forecast_done(conn, forecast_id, *, result_json, signal, pred_change_pct, uncertainty_pct):
    conn.execute(
        """UPDATE forecasts SET status = 'done', finished_at = datetime('now'),
               result_json = ?, signal = ?, pred_change_pct = ?, uncertainty_pct = ?
           WHERE id = ?""",
        (result_json, signal, pred_change_pct, uncertainty_pct, forecast_id),
    )
    conn.commit()


def mark_forecast_error(conn, forecast_id, error_message):
    conn.execute(
        "UPDATE forecasts SET status = 'error', finished_at = datetime('now'), error_message = ? WHERE id = ?",
        (error_message, forecast_id),
    )
    conn.commit()


def get_forecast(conn, forecast_id):
    return _row_to_dict(conn.execute("SELECT * FROM forecasts WHERE id = ?", (forecast_id,)).fetchone())


def get_latest_forecast(conn, instrument_id=None, symbol=None, market=None):
    """Solo pronosticos 'en vivo' (as_of_date NULL) -- uno de backtest nunca debe
    aparecer como si fuera la senial actual."""
    if instrument_id is not None:
        row = conn.execute(
            """SELECT * FROM forecasts WHERE instrument_id = ? AND as_of_date IS NULL
               ORDER BY created_at DESC LIMIT 1""",
            (instrument_id,),
        ).fetchone()
    else:
        row = conn.execute(
            """SELECT * FROM forecasts WHERE symbol = ? AND market = ? AND instrument_id IS NULL
               AND as_of_date IS NULL ORDER BY created_at DESC LIMIT 1""",
            (symbol, market),
        ).fetchone()
    return _row_to_dict(row)


def list_forecasts_for_instrument(conn, instrument_id):
    rows = conn.execute(
        "SELECT * FROM forecasts WHERE instrument_id = ? AND status = 'done' ORDER BY created_at",
        (instrument_id,),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def list_backtest_anchors(conn, instrument_id):
    """Fechas as_of_date ya cubiertas (en cualquier estado) para no re-encolar backtests duplicados."""
    rows = conn.execute(
        """SELECT DISTINCT as_of_date FROM forecasts
           WHERE instrument_id = ? AND as_of_date IS NOT NULL AND status != 'error'""",
        (instrument_id,),
    ).fetchall()
    return {r["as_of_date"] for r in rows}


def upsert_portfolio_snapshot(conn, date, valor_total, pnl_total, risk_score):
    conn.execute(
        """INSERT INTO portfolio_snapshots (date, valor_total, pnl_total, risk_score)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(date) DO UPDATE SET
               valor_total = excluded.valor_total,
               pnl_total = excluded.pnl_total,
               risk_score = excluded.risk_score""",
        (date, valor_total, pnl_total, risk_score),
    )
    conn.commit()


def list_portfolio_snapshots(conn, limit=365):
    rows = conn.execute(
        "SELECT * FROM portfolio_snapshots ORDER BY date DESC LIMIT ?", (limit,)
    ).fetchall()
    return [_row_to_dict(r) for r in reversed(rows)]


def list_pending_forecasts(conn):
    """queued/running al reiniciar el proceso son huerfanos (el thread que los corria murio)."""
    rows = conn.execute("SELECT * FROM forecasts WHERE status IN ('queued', 'running')").fetchall()
    return [_row_to_dict(r) for r in rows]
