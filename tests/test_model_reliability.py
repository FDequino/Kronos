"""Tests de portfolio/accuracy.py::compute_model_reliability_summary -- ver
conversación/auditoría: "la baja confiabilidad del modelo (11/13 instrumentos
con <50% de acierto direccional) visible en TODAS las pantallas de señales,
no solo internamente". Deliberadamente más simple que compute_signal_validation
("Sin validar"): solo mira si el acierto ya calculado es peor que 50%, sin
importar cuántos pronósticos hagan falta para el umbral de "validado" de
Ajustes."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from portfolio import accuracy, db


def _temp_conn(tmp_path):
    db_path = str(tmp_path / "test_model_reliability.db")
    return db.get_connection(db_path)


def _add_instrument(conn, symbol):
    return db.add_instrument(conn, symbol=symbol, type_="accion", market="byma", price_source="data912")


def test_sin_instrumentos_con_cache_devuelve_cero(tmp_path):
    conn = _temp_conn(tmp_path)
    _add_instrument(conn, "GGAL")

    result = accuracy.compute_model_reliability_summary(conn)

    assert result == {"evaluated_count": 0, "low_accuracy_count": 0}
    conn.close()


def test_cuenta_solo_los_que_tienen_directional_hit_pct(tmp_path):
    conn = _temp_conn(tmp_path)
    iid1 = _add_instrument(conn, "GGAL")
    iid2 = _add_instrument(conn, "YPFD")
    iid3 = _add_instrument(conn, "AAPL")
    db.upsert_accuracy_cache(conn, iid1, result_json=json.dumps({"directional_hit_pct": 30.0}))
    db.upsert_accuracy_cache(conn, iid2, result_json=json.dumps({"directional_hit_pct": 70.0}))
    # pending (sin directional_hit_pct todavia) -- no debe contarse ni como bueno ni como malo.
    db.upsert_accuracy_cache(conn, iid3, result_json=json.dumps({"pending": True, "directional_hit_pct": None}))

    result = accuracy.compute_model_reliability_summary(conn)

    assert result == {"evaluated_count": 2, "low_accuracy_count": 1}
    conn.close()


def test_instrumento_sin_cache_se_omite(tmp_path):
    conn = _temp_conn(tmp_path)
    _add_instrument(conn, "GGAL")  # nunca se abrio su detalle -- sin accuracy_cache.
    iid2 = _add_instrument(conn, "YPFD")
    db.upsert_accuracy_cache(conn, iid2, result_json=json.dumps({"directional_hit_pct": 20.0}))

    result = accuracy.compute_model_reliability_summary(conn)

    assert result == {"evaluated_count": 1, "low_accuracy_count": 1}
    conn.close()


def test_exactamente_50_no_es_bajo(tmp_path):
    conn = _temp_conn(tmp_path)
    iid = _add_instrument(conn, "GGAL")
    db.upsert_accuracy_cache(conn, iid, result_json=json.dumps({"directional_hit_pct": 50.0}))

    result = accuracy.compute_model_reliability_summary(conn)

    assert result == {"evaluated_count": 1, "low_accuracy_count": 0}
    conn.close()
