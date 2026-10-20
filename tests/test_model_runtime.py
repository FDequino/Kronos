"""Tests de model/runtime.py -- solo las ramas que no requieren cargar pesos
reales (ver conversacion/auditoria 19/10/2026, hallazgo 2.7: mensajes de
error en ingles, ahora en español). El resto del modulo (carga real de
Kronos) ya tiene su propia cobertura en tests/test_model_v2.py (excluido de
la corrida rapida por ser pesado/GPU)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from model import runtime as model_runtime


def test_load_model_con_key_invalida_lanza_value_error_en_espanol():
    with pytest.raises(ValueError, match="Modelo no soportado"):
        model_runtime.load_model("no-existe-este-modelo")


def test_status_sin_predictor_cargado_es_coherente():
    # No fuerza un estado global (ver docstring del modulo: nunca carga
    # pesos al importar) -- solo valida la forma de la respuesta cuando
    # nada se cargo todavia en este proceso de test.
    status = model_runtime.status()
    assert isinstance(status["available"], bool)
    assert isinstance(status["loaded"], bool)
    assert isinstance(status["message"], str)
    assert status["message"]  # nunca un mensaje vacio
