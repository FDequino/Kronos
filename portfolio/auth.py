"""Login de la app (ver conversacion) -- un solo usuario compartido, sin
cuentas individuales: esto es una herramienta personal (una cartera, un
dueño), no un SaaS multi-tenant, así que una sola contraseña protegiendo
toda la instalación es proporcional al riesgo real, no una mitad de
solución. La sesión se firma con app_settings.app_secret_key (generada una
vez por instalación, ver portfolio/db.py::_migrate) -- nunca una clave
hardcodeada en el código fuente, eso sería un secreto compartido por
cualquiera que clone el repo.

Mientras app_login_password_hash sea NULL (instalación nueva, o una
existente que recién suma esta columna) la app muestra /setup en vez de
/login -- nunca arranca con una contraseña por default embebida en el
código, que sería tan inseguro como no tener login."""
from werkzeug.security import check_password_hash, generate_password_hash

from portfolio import db


def is_configured(conn):
    settings = db.get_app_settings(conn)
    return bool(settings.get("app_login_password_hash"))


def set_password(conn, password):
    db.update_app_settings(conn, app_login_password_hash=generate_password_hash(password))


def verify_password(conn, password):
    settings = db.get_app_settings(conn)
    stored_hash = settings.get("app_login_password_hash")
    if not stored_hash:
        return False
    return check_password_hash(stored_hash, password)
