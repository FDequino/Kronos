"""Tests de portfolio/secrets_crypto.py (ver conversacion/auditoria
2026-10-20: cifrado en reposo de broker_accounts.api_key/api_secret, antes
en texto plano)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from portfolio import secrets_crypto


def test_encrypt_decrypt_roundtrip():
    original = "ContraseñaRealDeIOL123"
    encrypted = secrets_crypto.encrypt_secret(original)
    assert encrypted != original
    assert encrypted.startswith(secrets_crypto.ENC_PREFIX)
    assert original not in encrypted
    assert secrets_crypto.decrypt_secret(encrypted) == original


def test_encrypt_none_pasa_igual():
    assert secrets_crypto.encrypt_secret(None) is None


def test_decrypt_none_pasa_igual():
    assert secrets_crypto.decrypt_secret(None) is None


def test_decrypt_valor_legado_en_texto_plano_se_devuelve_tal_cual():
    """Una fila pre-migracion no tiene el prefijo -- decrypt_secret nunca
    intenta descifrar algo que nunca se cifro, lo devuelve tal cual."""
    assert secrets_crypto.decrypt_secret("password_viejo_sin_cifrar") == "password_viejo_sin_cifrar"


def test_encrypt_no_re_cifra_un_valor_ya_cifrado():
    encrypted_once = secrets_crypto.encrypt_secret("miclave")
    encrypted_twice = secrets_crypto.encrypt_secret(encrypted_once)
    assert encrypted_once == encrypted_twice
    assert secrets_crypto.decrypt_secret(encrypted_twice) == "miclave"


def test_is_encrypted():
    assert secrets_crypto.is_encrypted(secrets_crypto.encrypt_secret("x")) is True
    assert secrets_crypto.is_encrypted("texto_plano") is False
    assert secrets_crypto.is_encrypted(None) is False


def test_dos_valores_distintos_dan_ciphertexts_distintos():
    """Fernet agrega nonce/timestamp -- ni siquiera el mismo valor cifrado
    dos veces da el mismo resultado (pero ambos descifran igual)."""
    a = secrets_crypto.encrypt_secret("mismo_valor")
    b = secrets_crypto.encrypt_secret("mismo_valor")
    assert a != b
    assert secrets_crypto.decrypt_secret(a) == secrets_crypto.decrypt_secret(b) == "mismo_valor"
