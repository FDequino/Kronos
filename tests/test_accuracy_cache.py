"""Tests de portfolio/routes.py::_get_cached_accuracy -- bug real encontrado
perfilando el dashboard en la VPS (py-spy dump del proceso real): dashboard_bundle
recalculaba accuracy.compute_accuracy desde cero (CPU, nunca red) por cada
tenencia con forecasts, en cada carga de pagina -- con 24 tenencias eso solo
ya explicaba ~24s de los 38-70s totales observados. Mismo TTL diario que
technical_cache (ver ACCURACY_CACHE_FRESHNESS_HOURS)."""
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from portfolio import db, routes


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_accuracy_cache.db")
    monkeypatch.setattr(db, "DB_PATH", db_path)
    conn = db.get_connection(db_path)
    yield conn
    conn.close()


def _add_instrument(conn, symbol="GGAL"):
    return db.add_instrument(conn, symbol=symbol, type_="accion", market="byma", price_source="data912")


def test_primer_llamado_calcula_y_cachea(temp_db, monkeypatch):
    iid = _add_instrument(temp_db)
    compute = MagicMock(return_value={"mape": 4.2, "forecasts_evaluated": 3})
    monkeypatch.setattr(routes.accuracy, "compute_accuracy", compute)

    result = routes._get_cached_accuracy(temp_db, iid)

    compute.assert_called_once()
    assert result == {"mape": 4.2, "forecasts_evaluated": 3}
    cached = db.get_accuracy_cache(temp_db, iid)
    assert cached is not None


def test_segundo_llamado_dentro_de_la_ventana_no_recalcula(temp_db, monkeypatch):
    """El bug real: sin esto, dashboard_bundle recalculaba esto en CADA
    carga de pagina para cada tenencia con forecasts."""
    iid = _add_instrument(temp_db)
    compute = MagicMock(return_value={"mape": 4.2, "forecasts_evaluated": 3})
    monkeypatch.setattr(routes.accuracy, "compute_accuracy", compute)

    routes._get_cached_accuracy(temp_db, iid)
    result2 = routes._get_cached_accuracy(temp_db, iid)
    result3 = routes._get_cached_accuracy(temp_db, iid)

    compute.assert_called_once()
    assert result2 == result3 == {"mape": 4.2, "forecasts_evaluated": 3}


def test_recalcula_pasadas_las_24_horas(temp_db, monkeypatch):
    iid = _add_instrument(temp_db)
    old_fetch = (datetime.utcnow() - timedelta(hours=25)).isoformat()
    db.insert_accuracy_cache(temp_db, iid, result_json='{"mape": 1.0}', fetched_at=old_fetch)
    compute = MagicMock(return_value={"mape": 9.9, "forecasts_evaluated": 10})
    monkeypatch.setattr(routes.accuracy, "compute_accuracy", compute)

    result = routes._get_cached_accuracy(temp_db, iid)

    compute.assert_called_once()
    assert result == {"mape": 9.9, "forecasts_evaluated": 10}


def test_no_fetch_pesado_de_forecasts_price_history_en_cache_hit(temp_db, monkeypatch):
    """Las consultas de todo el historial de forecasts/price_history son
    innecesarias cuando el resultado ya esta cacheado -- antes corrian
    siempre, aunque el resultado fuera a salir del cache igual."""
    iid = _add_instrument(temp_db)
    db.insert_accuracy_cache(temp_db, iid, result_json='{"mape": 1.0}')
    list_forecasts = MagicMock()
    monkeypatch.setattr(db, "list_forecasts_for_instrument", list_forecasts)

    routes._get_cached_accuracy(temp_db, iid)

    list_forecasts.assert_not_called()


def test_insert_accuracy_cache_no_pisa_la_fila_anterior(temp_db):
    """Append-only (ver conversación, Fase 3 de unificación): antes era
    upsert_accuracy_cache (ON CONFLICT DO UPDATE), que pisaba la fila
    anterior -- imposible ver si el acierto de un instrumento viene cayendo
    con el tiempo."""
    iid = _add_instrument(temp_db)
    db.insert_accuracy_cache(temp_db, iid, result_json='{"mape": 1.0}')
    db.insert_accuracy_cache(temp_db, iid, result_json='{"mape": 2.0}')

    history = db.get_accuracy_cache_history(temp_db, iid)

    assert len(history) == 2
    assert [json.loads(row["result_json"])["mape"] for row in history] == [2.0, 1.0]


def test_get_accuracy_cache_devuelve_la_mas_reciente(temp_db):
    iid = _add_instrument(temp_db)
    old_fetch = (datetime.utcnow() - timedelta(hours=1)).isoformat()
    new_fetch = datetime.utcnow().isoformat()
    db.insert_accuracy_cache(temp_db, iid, result_json='{"mape": 1.0}', fetched_at=old_fetch)
    db.insert_accuracy_cache(temp_db, iid, result_json='{"mape": 2.0}', fetched_at=new_fetch)

    cached = db.get_accuracy_cache(temp_db, iid)

    assert json.loads(cached["result_json"])["mape"] == 2.0


def test_get_accuracy_cache_history_respeta_limit(temp_db):
    iid = _add_instrument(temp_db)
    for i in range(5):
        db.insert_accuracy_cache(temp_db, iid, result_json=f'{{"mape": {i}}}')

    history = db.get_accuracy_cache_history(temp_db, iid, limit=2)

    assert len(history) == 2


def test_get_accuracy_cache_history_no_mezcla_instrumentos(temp_db):
    iid1 = _add_instrument(temp_db, "GGAL")
    iid2 = _add_instrument(temp_db, "YPFD")
    db.insert_accuracy_cache(temp_db, iid1, result_json='{"mape": 1.0}')
    db.insert_accuracy_cache(temp_db, iid2, result_json='{"mape": 2.0}')
    db.insert_accuracy_cache(temp_db, iid2, result_json='{"mape": 3.0}')

    history1 = db.get_accuracy_cache_history(temp_db, iid1)
    history2 = db.get_accuracy_cache_history(temp_db, iid2)

    assert len(history1) == 1
    assert len(history2) == 2


def test_cada_instrumento_tiene_su_propio_cache(temp_db, monkeypatch):
    iid1 = _add_instrument(temp_db, "GGAL")
    iid2 = _add_instrument(temp_db, "YPFD")
    compute = MagicMock(side_effect=[{"mape": 1.0}, {"mape": 2.0}])
    monkeypatch.setattr(routes.accuracy, "compute_accuracy", compute)

    r1 = routes._get_cached_accuracy(temp_db, iid1)
    r2 = routes._get_cached_accuracy(temp_db, iid2)

    assert r1 == {"mape": 1.0}
    assert r2 == {"mape": 2.0}
    assert compute.call_count == 2
