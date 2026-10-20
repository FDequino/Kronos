"""Cifrado en reposo para secretos guardados en la base (ver conversacion/
auditoria 2026-10-20: broker_accounts.api_key/api_secret se guardaban en
TEXTO PLANO en portfolio.db, confirmado con credenciales reales de IOL en la
fila activa -- el usuario ya cambio esa contraseña, pero el mecanismo de
guardado seguia sin cifrar y tenia que arreglarse de raiz).

Fernet (cifrado simetrico autenticado, libreria `cryptography`) con una
clave generada una sola vez y guardada en .env (gitignoreado, ver commit
e72a4b4) -- FUERA de portfolio.db a proposito: si la clave viviera en el
mismo sqlite que el dato cifrado, cifrar no protegeria nada (quien lee el
archivo de la base ya tendria la clave al lado). Proporcional a una app
local de un solo usuario -- no es un KMS ni hay rotacion de claves, es la
diferencia real entre "se filtro portfolio.db y ya esta" vs. "portfolio.db
solo no alcanza, hace falta ademas el .env de esta maquina".

Prefijo "enc:v1:" en el valor persistido distingue un secreto ya cifrado con
este esquema de uno legado en texto plano -- permite migrar sin adivinar
(ver portfolio/db.py::_migrate) y nunca cifrar dos veces un valor que ya lo
esta."""
import os

from cryptography.fernet import Fernet, InvalidToken
from dotenv import find_dotenv, load_dotenv, set_key

ENC_PREFIX = "enc:v1:"
_ENV_KEY_NAME = "KRONOS_SECRET_KEY"

_fernet = None


def _env_path():
    """Mismo .env que ya usa webui/app.py (load_dotenv() sin argumentos, raiz
    del repo) -- find_dotenv busca hacia arriba desde el cwd; si no existe
    ninguno todavia, se crea en la raiz del repo (2 niveles arriba de este
    archivo: portfolio/secrets_crypto.py -> raiz)."""
    path = find_dotenv(usecwd=True)
    if path:
        return path
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, ".env")
    open(path, "a").close()
    return path


def _get_fernet():
    global _fernet
    if _fernet is not None:
        return _fernet
    load_dotenv(_env_path())
    key = os.environ.get(_ENV_KEY_NAME)
    if not key:
        # Primera vez que corre esto en esta instalacion: genera y persiste
        # la clave en .env -- nunca hardcodeada en el codigo fuente (mismo
        # criterio que portfolio/auth.py::app_secret_key).
        key = Fernet.generate_key().decode()
        set_key(_env_path(), _ENV_KEY_NAME, key)
        os.environ[_ENV_KEY_NAME] = key
    _fernet = Fernet(key.encode() if isinstance(key, str) else key)
    return _fernet


def is_encrypted(value):
    return isinstance(value, str) and value.startswith(ENC_PREFIX)


def encrypt_secret(value):
    """None pasa igual (varios secretos son opcionales, ver
    broker_accounts.api_key/api_secret nullable). Nunca re-cifra un valor
    que ya tiene el prefijo (evita doble-cifrado si algun caller llama dos
    veces sobre el mismo dict, ver migracion)."""
    if value is None:
        return None
    if is_encrypted(value):
        return value
    token = _get_fernet().encrypt(value.encode()).decode()
    return ENC_PREFIX + token


def decrypt_secret(value):
    """None pasa igual. Un valor SIN el prefijo se asume legado en texto
    plano (fila pre-migracion, o la migracion de portfolio/db.py::_migrate
    todavia no corrio sobre esta conexion) -- se devuelve tal cual, nunca se
    intenta descifrar un string que nunca se cifro."""
    if value is None:
        return value
    if not is_encrypted(value):
        return value
    token = value[len(ENC_PREFIX):]
    try:
        return _get_fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        raise ValueError(
            "No se pudo descifrar el secreto -- KRONOS_SECRET_KEY de .env no "
            "coincide con la que lo cifro (¿se perdio o se regenero el .env?)."
        )
