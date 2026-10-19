"""Tests de Perfiles de Inversion (ver conversacion, portfolio/config.py).
Corre contra una DB temporal propia (mismo patron que tests/test_paper_trading.py),
nunca toca portfolio/portfolio.db real. No hay llamadas de red en este modulo
(todo vive en signal_config), asi que no hace falta mockear nada externo."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from portfolio import config as capital_config
from portfolio import db


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_risk_profiles.db")
    monkeypatch.setattr(db, "DB_PATH", db_path)
    conn = db.get_connection(db_path)
    yield conn
    conn.close()


def test_list_risk_profiles_devuelve_los_3_en_orden():
    profiles = capital_config.list_risk_profiles()
    assert [p["id"] for p in profiles] == ["conservador", "moderado", "agresivo"]
    for p in profiles:
        assert set(p) >= {
            "id", "label", "core_pct", "stop_loss_pct", "take_profit_pct",
            "expected_return_usd_low_pct", "expected_return_usd_high_pct",
            "sharpe_low", "sharpe_high", "max_drawdown_pct",
        }


def test_list_risk_profiles_devuelve_copia_nunca_el_preset_interno():
    profiles = capital_config.list_risk_profiles()
    profiles[0]["core_pct"] = 999.0
    assert capital_config.RISK_PROFILES["conservador"]["core_pct"] == 70.0


def test_valores_exactos_de_cada_perfil_segun_el_pedido():
    by_id = {p["id"]: p for p in capital_config.list_risk_profiles()}
    assert by_id["conservador"]["core_pct"] == 70.0
    assert by_id["conservador"]["stop_loss_pct"] == 4.0
    assert by_id["conservador"]["take_profit_pct"] == 3.5
    assert by_id["moderado"]["core_pct"] == 20.0
    assert by_id["moderado"]["stop_loss_pct"] == 9.0
    assert by_id["moderado"]["take_profit_pct"] == 4.0
    assert by_id["agresivo"]["core_pct"] == 0.0
    assert by_id["agresivo"]["stop_loss_pct"] == 12.0
    assert by_id["agresivo"]["take_profit_pct"] == 6.0


def test_perfil_activo_por_defecto_es_moderado_en_db_fresca(temp_db):
    # DB recien creada -- core_allocation_pct/satellite_take_profit_pct/
    # satellite_stop_loss_pct vienen con los DEFAULT de la migracion
    # (20.0/4.0/9.0), que coinciden exacto con el preset "moderado".
    assert capital_config.get_active_risk_profile(temp_db) == "moderado"


def test_activar_perfil_conservador_persiste_los_3_parametros(temp_db):
    result = capital_config.activate_risk_profile(temp_db, "conservador")
    assert result["active_profile"] == "conservador"
    assert result["core_pct"] == 70.0
    assert result["satellite_pct"] == 30.0
    assert result["stop_loss_pct"] == 4.0
    assert result["take_profit_pct"] == 3.5

    # Se escribio via las MISMAS funciones que ya usan la API/Telegram --
    # confirmado leyendolas de nuevo de forma independiente.
    allocation = capital_config.get_capital_allocation(temp_db)
    exit_params = capital_config.get_satellite_exit_params(temp_db)
    assert allocation["core_pct"] == 70.0
    assert exit_params == {"stop_loss_pct": 4.0, "take_profit_pct": 3.5}
    assert capital_config.get_active_risk_profile(temp_db) == "conservador"


def test_activar_perfil_agresivo(temp_db):
    result = capital_config.activate_risk_profile(temp_db, "agresivo")
    assert result["core_pct"] == 0.0
    assert result["satellite_pct"] == 100.0
    assert capital_config.get_active_risk_profile(temp_db) == "agresivo"


def test_cambiar_un_parametro_a_mano_despues_de_activar_degrada_a_personalizado(temp_db):
    capital_config.activate_risk_profile(temp_db, "moderado")
    assert capital_config.get_active_risk_profile(temp_db) == "moderado"

    # El usuario toca el slider de asignacion a mano, por fuera de un perfil.
    capital_config.set_core_allocation_pct(temp_db, 55.0)
    assert capital_config.get_active_risk_profile(temp_db) == "personalizado"


def test_activar_perfil_invalido_rechaza_sin_tocar_nada(temp_db):
    original = capital_config.get_capital_allocation(temp_db)
    with pytest.raises(ValueError):
        capital_config.activate_risk_profile(temp_db, "ultra_agresivo")
    assert capital_config.get_capital_allocation(temp_db) == original


def test_persistencia_real_en_sqlite_entre_conexiones(tmp_path, monkeypatch):
    """No alcanza con que el mismo `conn` lea lo que acaba de escribir --
    confirma que una conexion NUEVA contra el mismo archivo ve el perfil
    activado (persistencia real en disco, no solo en la sesion/cache de
    sqlite3 en memoria)."""
    db_path = str(tmp_path / "test_risk_profiles_persist.db")
    monkeypatch.setattr(db, "DB_PATH", db_path)
    conn1 = db.get_connection(db_path)
    capital_config.activate_risk_profile(conn1, "agresivo")
    conn1.close()

    conn2 = db.get_connection(db_path)
    try:
        assert capital_config.get_active_risk_profile(conn2) == "agresivo"
        assert capital_config.get_capital_allocation(conn2)["core_pct"] == 0.0
    finally:
        conn2.close()
