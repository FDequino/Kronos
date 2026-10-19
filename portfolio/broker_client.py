"""Soporte Multi-Broker (ver conversacion) -- gestor unificado de cuentas de
bróker EXTERNAS (PPI, IOL, Balanz, Matrix) con API propia, para consultar
saldo y tenencias en vivo, agregados entre todas las cuentas activas.
Distinto de portfolio/import_broker_report.py (que importa un Excel
exportado a mano, sin credenciales, offline) -- esto es una conexión API en
vivo, opcional, nunca requerida para que el resto de la app funcione.

HONESTIDAD DECLARADA (ver conversación, nunca fabricar una integración que
no existe): se investigaron las 4 APIs públicas ANTES de escribir esto
(WebSearch/WebFetch reales, no de memoria):
  - IOL (InvertirOnline): API REST pública y documentada CONFIRMADA
    (api.invertironline.com, token en la raíz /token, recursos bajo
    /api/v2/...). Adapter implementado de buena fe contra esa
    documentación pública, pero NUNCA PROBADO con credenciales reales (no
    se tienen) -- declarado en el docstring del adapter, nunca se afirma
    que "funciona de verdad".
  - PPI (Portfolio Personal Inversiones): API REST pública y documentada
    CONFIRMADA (itatppi.github.io/ppi-official-api-docs). La documentación
    pública sola no alcanzaba para confirmar el mecanismo de autenticación
    de los requests posteriores al login (quedó como NotImplementedError
    en una ronda anterior) -- se terminó de confirmar leyendo el CÓDIGO
    FUENTE REAL del SDK oficial (`pip download ppi_client`, paquete de
    PyPI publicado por PPI mismo, api@portfoliopersonal.com, v1.3.0), no
    de memoria ni adivinado. Ver docstring de PPIAdapter para el detalle
    completo del mecanismo.
  - Balanz y Matrix: NO se encontró documentación pública de una API REST
    (búsqueda real, no asumido) -- quedan como stubs honestos que
    devuelven "integración pendiente, sin documentación pública
    verificada" en vez de fabricar un endpoint que no existe.

Seguridad declarada: api_key/api_secret se guardan en TEXTO PLANO en
broker_accounts (mismo criterio ya aceptado en este proyecto para
signal_config.openai_api_key/anthropic_api_key -- app local de un solo
usuario, sin esquema de cifrado-en-reposo todavía) -- nunca se devuelven
sin enmascarar desde ninguna función pensada para mostrarse (ver
_mask_secret/list_accounts_masked).

Arquitectura: cada broker implementa la MISMA interfaz (BrokerAdapter:
test_connection/get_balance/get_holdings) para que MultiBrokerManager
pueda agregar entre todos sin conocer el detalle de cada uno -- agregar un
broker real nuevo en el futuro es escribir UN adapter más, nunca tocar el
manager."""
import requests

from portfolio import db

CONNECTION_OK = "ok"
CONNECTION_FAILED = "failed"
CONNECTION_PENDING = "pending_integration"

BROKER_NAMES = ("PPI", "IOL", "Balanz", "Matrix")


def _mask_secret(value):
    """Nunca se devuelve un secreto completo a ningún caller -- solo los
    últimos 4 caracteres, el resto reemplazado por '****' (mismo espíritu
    que cualquier UI de tarjetas de crédito)."""
    if not value:
        return None
    if len(value) <= 4:
        return "****"
    return "****" + value[-4:]


def mask_account(account):
    """Copia de un dict de broker_accounts con api_key/api_secret
    enmascarados -- usar SIEMPRE que la cuenta vaya a mostrarse (UI, logs,
    respuesta de API), nunca el dict crudo de db.py."""
    if account is None:
        return None
    masked = dict(account)
    masked["api_key"] = _mask_secret(account.get("api_key"))
    masked["api_secret"] = _mask_secret(account.get("api_secret"))
    return masked


class BrokerAdapter:
    """Interfaz base -- cada adapter real/stub hereda esto. broker_name debe
    matchear EXACTO el valor guardado en broker_accounts.broker_name."""
    broker_name = None

    def __init__(self, account):
        self.account = account  # dict de broker_accounts SIN enmascarar (uso interno del adapter, nunca se expone)

    def test_connection(self):
        raise NotImplementedError

    def get_balance(self):
        raise NotImplementedError

    def get_holdings(self):
        raise NotImplementedError


class IOLAdapter(BrokerAdapter):
    """InvertirOnline -- ver HONESTIDAD DECLARADA arriba del módulo:
    implementado contra documentación pública real, NUNCA probado con
    credenciales reales. api_key/api_secret de broker_accounts se
    interpretan aquí como username/password (el login real de IOL es
    usuario+contraseña de la cuenta, no un par key/secret clásico -- se
    reusan esos 2 campos genéricos de la tabla para no duplicar el esquema
    por broker)."""
    broker_name = "IOL"
    BASE_URL = "https://api.invertironline.com"
    TIMEOUT = 15

    def _get_token(self):
        resp = requests.post(
            f"{self.BASE_URL}/token",
            data={
                "username": self.account.get("api_key"), "password": self.account.get("api_secret"),
                "grant_type": "password",
            },
            timeout=self.TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        # Nombre exacto del campo del token no quedó 100% confirmado en la
        # documentación pública consultada -- se prueban los 2 nombres más
        # comunes en un flujo OAuth2 password grant real en vez de asumir
        # uno solo a ciegas (ver docstring del módulo).
        token = data.get("access_token") or data.get("bearer_token")
        if not token:
            raise ValueError(f"Respuesta de /token sin campo de token reconocido: {list(data.keys())}")
        return token

    def _headers(self):
        return {"Authorization": f"Bearer {self._get_token()}"}

    def test_connection(self):
        try:
            self._get_token()
            return {"status": CONNECTION_OK, "detail": "Token obtenido correctamente (IOL)"}
        except Exception as e:
            return {"status": CONNECTION_FAILED, "detail": str(e)}

    def get_balance(self):
        resp = requests.get(f"{self.BASE_URL}/api/v2/estadocuenta", headers=self._headers(), timeout=self.TIMEOUT)
        resp.raise_for_status()
        return resp.json()

    def get_holdings(self):
        resp = requests.get(
            f"{self.BASE_URL}/api/v2/portafolio/argentina", headers=self._headers(), timeout=self.TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()


class PPIAdapter(BrokerAdapter):
    """Portfolio Personal Inversiones -- mecanismo COMPLETO, confirmado
    contra el código fuente real del SDK oficial `ppi_client` (PyPI, ver
    HONESTIDAD DECLARADA arriba del módulo):

    - AuthorizedClient/ClientKey son headers FIJOS que identifican la
      APLICACIÓN cliente, no la cuenta del usuario -- el SDK oficial usa
      siempre los mismos 2 valores, para cualquier usuario, en producción
      (ppi_client/ppi_api_client.py::PPIClient.__init__). Reusarlos acá es
      tan legítimo como usar el SDK mismo: identifican "una app hablando el
      protocolo público de PPI", no un secreto por cuenta.
    - ApiKey/ApiSecret (de ESTA cuenta, broker_accounts.api_key/api_secret)
      SOLO viajan en el login (POST LoginApi) -- nunca de nuevo después.
    - La respuesta de LoginApi trae accessToken/refreshToken; todo request
      posterior lleva Authorization: Bearer <accessToken>, además de
      AuthorizedClient/ClientKey (nunca ApiKey/ApiSecret de nuevo). Sin
      cache de token entre requests (cada llamada a este adapter es un
      objeto nuevo de vida corta, ver _adapter_for) -- un login de más por
      consulta, mismo costo que ya paga IOLAdapter._get_token().
    - accountNumber (el número de cuenta de PPI, NO la ApiKey) viaja como
      query param en balance/tenencias -- es broker_accounts.account_number
      (columna ya existía en el esquema, UI ya la pedía desde antes).
    - Base URL de producción: clientapi.portfoliopersonal.com/api (el
      adapter anterior tenía clientapi_sandbox.portfoliopersonal.com, ni el
      host ni el path matcheaban el real -- nunca se hubiera conectado)."""
    broker_name = "PPI"
    BASE_URL = "https://clientapi.portfoliopersonal.com/api"
    AUTHORIZED_CLIENT = "API_CLI_PYTHON"
    CLIENT_KEY = "pp19PythonApp12"
    TIMEOUT = 15

    def _base_headers(self):
        return {"AuthorizedClient": self.AUTHORIZED_CLIENT, "ClientKey": self.CLIENT_KEY}

    def _login(self):
        headers = {
            **self._base_headers(),
            "ApiKey": self.account.get("api_key") or "",
            "ApiSecret": self.account.get("api_secret") or "",
        }
        resp = requests.post(f"{self.BASE_URL}/1.0/Account/LoginApi", headers=headers, timeout=self.TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        token = data.get("accessToken")
        if not token:
            raise ValueError(f"LoginApi respondió sin accessToken: {list(data.keys())}")
        return token

    def _auth_headers(self):
        return {**self._base_headers(), "Authorization": f"Bearer {self._login()}"}

    def _account_number(self):
        account_number = self.account.get("account_number")
        if not account_number:
            raise ValueError(
                "PPI requiere 'Número de cuenta' (el número de cuenta en PPI, no la ApiKey) -- "
                "completalo en Integraciones > Cuentas de bróker."
            )
        return account_number

    def test_connection(self):
        try:
            self._login()
            return {"status": CONNECTION_OK, "detail": "LoginApi respondió correctamente (PPI)"}
        except Exception as e:
            return {"status": CONNECTION_FAILED, "detail": str(e)}

    def get_balance(self):
        resp = requests.get(
            f"{self.BASE_URL}/1.0/Account/AvailableBalance",
            params={"accountNumber": self._account_number()}, headers=self._auth_headers(), timeout=self.TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()

    def get_holdings(self):
        resp = requests.get(
            f"{self.BASE_URL}/1.0/Account/BalancesAndPositions",
            params={"accountNumber": self._account_number()}, headers=self._auth_headers(), timeout=self.TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()


class _PendingIntegrationAdapter(BrokerAdapter):
    """Balanz y Matrix (ver HONESTIDAD DECLARADA arriba): búsqueda real no
    encontró documentación pública de una API REST para ninguno de los 2 --
    stub honesto, nunca fabrica un endpoint que no se verificó que existe."""

    def test_connection(self):
        return {
            "status": CONNECTION_PENDING,
            "detail": f"{self.broker_name}: integración pendiente, sin documentación pública de API verificada.",
        }

    def get_balance(self):
        raise NotImplementedError(f"{self.broker_name}: integración pendiente, sin documentación pública verificada.")

    def get_holdings(self):
        raise NotImplementedError(f"{self.broker_name}: integración pendiente, sin documentación pública verificada.")


class BalanzAdapter(_PendingIntegrationAdapter):
    broker_name = "Balanz"


class MatrixAdapter(_PendingIntegrationAdapter):
    broker_name = "Matrix"


_ADAPTERS_BY_BROKER = {cls.broker_name: cls for cls in (IOLAdapter, PPIAdapter, BalanzAdapter, MatrixAdapter)}


def _adapter_for(account):
    adapter_cls = _ADAPTERS_BY_BROKER.get(account["broker_name"])
    if adapter_cls is None:
        raise ValueError(f"broker_name desconocido: {account['broker_name']!r}")
    return adapter_cls(account)


class MultiBrokerManager:
    """Punto de entrada único -- CRUD delgado sobre portfolio/db.py (nunca
    duplica esa lógica) + agregación de saldo/tenencias entre TODAS las
    cuentas is_active=1, degradando con gracia si una cuenta puntual falla
    (mismo criterio de resiliencia que portfolio/pairs_trading.py::scan_pairs:
    una cuenta problemática se omite, nunca rompe la agregación completa)."""

    def list_accounts(self, conn, masked=True):
        accounts = db.list_broker_accounts(conn)
        return [mask_account(a) for a in accounts] if masked else accounts

    def add_account(self, conn, *, broker_name, alias=None, api_key=None, api_secret=None,
                     account_number=None, is_active=True):
        if broker_name not in BROKER_NAMES:
            raise ValueError(f"broker_name debe ser uno de {BROKER_NAMES}, se recibió {broker_name!r}")
        account_id = db.add_broker_account(
            conn, broker_name=broker_name, alias=alias, api_key=api_key, api_secret=api_secret,
            account_number=account_number, is_active=is_active,
        )
        return mask_account(db.get_broker_account(conn, account_id))

    def update_account(self, conn, account_id, **fields):
        if "broker_name" in fields and fields["broker_name"] not in BROKER_NAMES:
            raise ValueError(f"broker_name debe ser uno de {BROKER_NAMES}, se recibió {fields['broker_name']!r}")
        return mask_account(db.update_broker_account(conn, account_id, **fields))

    def delete_account(self, conn, account_id):
        db.delete_broker_account(conn, account_id)

    def set_active(self, conn, account_id, is_active):
        return mask_account(db.update_broker_account(conn, account_id, is_active=is_active))

    def test_connection(self, conn, account_id):
        """Nunca deja una excepción sin capturar: SIEMPRE devuelve un dict
        {status, detail} -- status en {ok, failed, pending_integration}."""
        account = db.get_broker_account(conn, account_id)
        if account is None:
            return {"status": CONNECTION_FAILED, "detail": "Cuenta no encontrada"}
        try:
            adapter = _adapter_for(account)
            return adapter.test_connection()
        except Exception as e:
            return {"status": CONNECTION_FAILED, "detail": str(e)}

    def get_unified_balance(self, conn):
        """Agrega el saldo de TODAS las cuentas activas -- devuelve
        {"accounts": [{account_id, broker_name, alias, balance|None, error|None}, ...]},
        nunca rompe la agregación completa por una cuenta que falle."""
        return self._aggregate(conn, "get_balance", "balance")

    def get_unified_holdings(self, conn):
        """Mismo criterio que get_unified_balance, para tenencias."""
        return self._aggregate(conn, "get_holdings", "holdings")

    def _aggregate(self, conn, method_name, result_key):
        results = []
        for account in db.list_broker_accounts(conn):
            if not account["is_active"]:
                continue
            entry = {
                "account_id": account["id"], "broker_name": account["broker_name"],
                "alias": account["alias"], result_key: None, "error": None,
            }
            try:
                adapter = _adapter_for(account)
                entry[result_key] = getattr(adapter, method_name)()
            except Exception as e:
                entry["error"] = str(e)
            results.append(entry)
        return {"accounts": results}
