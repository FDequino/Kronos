"""Test de portfolio/db.py::_migrate -- bug real encontrado probando el
endpoint real de promover a live (auditoria 2026-10-21): baseline_sharpe/
baseline_annualized_return_pct se agregaron a schema.sql DESPUES de que la
tabla 'strategies' ya existia en la base local -- CREATE TABLE IF NOT EXISTS
no agrega columnas a una tabla preexistente, promote_to_live tiraba KeyError
en vez del ValueError declarado."""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from portfolio import db


def test_migra_una_tabla_strategies_vieja_sin_las_columnas_de_baseline(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_strategies_migration.db")
    monkeypatch.setattr(db, "DB_PATH", db_path)

    # Simula una base real creada ANTES de que existieran baseline_sharpe/
    # baseline_annualized_return_pct -- misma tecnica que la tabla
    # instruments_new en _migrate (tabla minima, sin las columnas nuevas).
    raw_conn = sqlite3.connect(db_path)
    raw_conn.execute("""
        CREATE TABLE strategies (
            name TEXT PRIMARY KEY,
            status TEXT NOT NULL DEFAULT 'research',
            last_dsr REAL,
            last_validated_at TEXT,
            automation_mode TEXT NOT NULL DEFAULT 'off',
            capital_pct REAL NOT NULL DEFAULT 0.0,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    raw_conn.execute("INSERT INTO strategies (name) VALUES ('canje_al29_gd29')")
    raw_conn.commit()
    raw_conn.close()

    conn = db.get_connection(db_path)
    try:
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(strategies)").fetchall()}
        assert "baseline_sharpe" in cols
        assert "baseline_annualized_return_pct" in cols
        # La fila ya existente sigue intacta, con las columnas nuevas en NULL.
        row = conn.execute("SELECT * FROM strategies WHERE name='canje_al29_gd29'").fetchone()
        assert row["baseline_sharpe"] is None
    finally:
        conn.close()


def test_migra_el_check_viejo_de_status_sin_paper_trading(tmp_path, monkeypatch):
    """Bug de la misma clase (auditoria 2026-10-21, correccion del flujo a
    Validada -> Paper Trading -> Live): una base real creada ANTES de que
    'paper_trading' se sumara al CHECK de status tiraria un IntegrityError al
    primer intento de start_paper_trading. SQLite no permite ALTER sobre un
    CHECK existente -- confirma que el rebuild de tabla (mismo patron que
    'cripto' en instruments) preserva filas existentes y permite el valor
    nuevo."""
    db_path = str(tmp_path / "test_strategies_status_migration.db")
    monkeypatch.setattr(db, "DB_PATH", db_path)

    raw_conn = sqlite3.connect(db_path)
    raw_conn.execute("""
        CREATE TABLE strategies (
            name TEXT PRIMARY KEY,
            status TEXT NOT NULL DEFAULT 'research' CHECK (status IN ('research', 'validated', 'live')),
            last_dsr REAL,
            last_validated_at TEXT,
            automation_mode TEXT NOT NULL DEFAULT 'off' CHECK (automation_mode IN ('full_auto', 'semi_auto', 'off')),
            capital_pct REAL NOT NULL DEFAULT 0.0,
            baseline_sharpe REAL,
            baseline_annualized_return_pct REAL,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    raw_conn.execute("INSERT INTO strategies (name, status, last_dsr) VALUES ('canje_al29_gd29', 'validated', 0.99)")
    raw_conn.commit()
    raw_conn.close()

    conn = db.get_connection(db_path)
    try:
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(strategies)").fetchall()}
        assert "paper_trading_paused" in cols

        row = conn.execute("SELECT * FROM strategies WHERE name='canje_al29_gd29'").fetchone()
        assert row["status"] == "validated"
        assert row["last_dsr"] == 0.99
        assert row["paper_trading_paused"] == 0

        # El CHECK viejo rechazaria esto -- confirma que el rebuild aplico de verdad.
        conn.execute("UPDATE strategies SET status='paper_trading' WHERE name='canje_al29_gd29'")
        conn.commit()
    finally:
        conn.close()
