"""Tests de portfolio/auth.py (ver conversacion, login de un solo usuario)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from portfolio import auth, db


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_auth.db")
    monkeypatch.setattr(db, "DB_PATH", db_path)
    conn = db.get_connection(db_path)
    yield conn
    conn.close()


def test_instalacion_nueva_no_esta_configurada(temp_db):
    assert auth.is_configured(temp_db) is False


def test_secret_key_se_genera_solo_en_la_migracion(temp_db):
    settings = db.get_app_settings(temp_db)
    assert settings["app_secret_key"]
    assert len(settings["app_secret_key"]) >= 32


def test_secret_key_no_se_pisa_en_una_conexion_nueva(tmp_path, monkeypatch):
    """Una segunda conexion a la MISMA base no debe generar una secret key
    distinta -- eso invalidaria toda sesion activa."""
    db_path = str(tmp_path / "test_auth_persist.db")
    monkeypatch.setattr(db, "DB_PATH", db_path)
    conn1 = db.get_connection(db_path)
    key1 = db.get_app_settings(conn1)["app_secret_key"]
    conn1.close()

    conn2 = db.get_connection(db_path)
    key2 = db.get_app_settings(conn2)["app_secret_key"]
    conn2.close()

    assert key1 == key2


def test_set_password_nunca_guarda_en_texto_plano(temp_db):
    auth.set_password(temp_db, "una-contraseña-real")
    stored = db.get_app_settings(temp_db)["app_login_password_hash"]
    assert "una-contraseña-real" not in stored
    assert auth.is_configured(temp_db) is True


def test_verify_password_correcta(temp_db):
    auth.set_password(temp_db, "una-contraseña-real")
    assert auth.verify_password(temp_db, "una-contraseña-real") is True


def test_verify_password_incorrecta(temp_db):
    auth.set_password(temp_db, "una-contraseña-real")
    assert auth.verify_password(temp_db, "otra-cosa") is False


def test_verify_password_sin_configurar_nunca_pasa(temp_db):
    """Sin contraseña configurada, verify_password siempre da False -- nunca
    un string vacio/None matcheando por accidente contra un hash vacio."""
    assert auth.verify_password(temp_db, "") is False
    assert auth.verify_password(temp_db, "cualquier-cosa") is False
