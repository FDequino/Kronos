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
    /api/v2/...). test_connection/get_balance/get_holdings CONFIRMADOS
    2026-10-20 contra la cuenta real del usuario (login, saldo y tenencias
    respondieron correctamente). Ejecución real de órdenes (get_order_budget/
    confirm_order/cancel_order, agregada la misma fecha) confirmada contra
    documentación pública + el código fuente real del SDK `iol-api` (PyPI,
    `pip download iol-api`) -- mismo criterio que PPI más abajo -- pero
    TODAVÍA sin probar contra una orden real (ver docstring de IOLAdapter).
    IOL no tiene un endpoint de presupuesto real como sí tiene PPI -- el
    "budget" de IOL es una estimación local, declarado explícito.
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

Seguridad (corregido 2026-10-20, ver conversación/auditoría -- antes
guardaba api_key/api_secret en TEXTO PLANO, encontrado con credenciales
reales de IOL en la fila activa): portfolio/db.py cifra/descifra
api_key/api_secret de forma transparente en el borde de
add_broker_account/update_broker_account/get_broker_account/
list_broker_accounts (ver portfolio/secrets_crypto.py, Fernet con clave en
.env, fuera de portfolio.db) -- este módulo siempre trabaja con el valor ya
en texto plano EN MEMORIA (nunca ve el valor cifrado), igual que antes;
nunca se devuelven sin enmascarar desde ninguna función pensada para
mostrarse (ver _mask_secret/list_accounts_masked). signal_config.
openai_api_key/anthropic_api_key/finnhub_api_key (corregido 2026-10-20,
misma pasada): mismo esquema, cifrado/descifrado transparente en el borde
de get_signal_config/update_signal_config.

Arquitectura: cada broker implementa la MISMA interfaz (BrokerAdapter:
test_connection/get_balance/get_holdings) para que MultiBrokerManager
pueda agregar entre todos sin conocer el detalle de cada uno -- agregar un
broker real nuevo en el futuro es escribir UN adapter más, nunca tocar el
manager.

INTERRUPTOR MAESTRO DE EJECUCION REAL (ver conversación, "diseñemos el
interruptor" -- diseñado a propósito ANTES de que exista ninguna función
que ejecute una orden real; hoy BrokerAdapter solo tiene test_connection/
get_balance/get_holdings, las tres de solo lectura -- "operar de verdad"
sigue siendo 100% manual, ver proposals.js::accept). El día que se agregue
`place_order`/`confirm_order` a algún adapter, esa función (y la ruta HTTP
que la exponga) DEBE empezar llamando ensure_auto_trading_enabled(conn) y
dejar que la excepción se propague si no está habilitado -- nunca envuelta
en un try/except silencioso, nunca asumido encendido. app_settings.auto_trading_enabled
arranca en 0 (ver schema.sql) y se prende a mano desde Ajustes >
Integraciones -- este módulo es el único lugar que lo chequea, para que no
haya un segundo camino que lo pase por alto."""
import requests

from portfolio import db

CONNECTION_OK = "ok"
CONNECTION_FAILED = "failed"
CONNECTION_PENDING = "pending_integration"


class AutoTradingDisabledError(Exception):
    """Ver INTERRUPTOR MAESTRO DE EJECUCION REAL en el docstring del módulo."""


def ensure_auto_trading_enabled(conn):
    """Punto único de chequeo para CUALQUIER código futuro que vaya a
    ejecutar una orden real contra un bróker. Sin caller todavía (no existe
    código de ejecución real aún) -- existe para que, el día que se escriba
    esa primera línea, el gate ya esté ahí en vez de tener que acordarse de
    agregarlo bajo presión."""
    settings = db.get_app_settings(conn)
    if not settings.get("auto_trading_enabled"):
        raise AutoTradingDisabledError(
            "Ejecución real de órdenes deshabilitada -- activala en Ajustes > Integraciones "
            "si de verdad querés que este código mande una orden real a un bróker."
        )


def compute_auto_trading_limit_ars(conn, patrimonio_ars):
    """Tope GENERAL de exposición para la ejecución real (ver conversación:
    "tope general, no por orden" -- nunca un límite por operación
    individual, siempre contra el total que el bot tenga abierto).
    Resuelve el modo elegido (% del patrimonio o un monto fijo) a un número
    concreto en ARS. Sin caller todavía, mismo criterio que
    ensure_auto_trading_enabled: el código futuro de órdenes tiene que
    sumar lo que el bot ya tiene abierto + la orden nueva y comparar eso
    contra este número antes de confirmar."""
    settings = db.get_app_settings(conn)
    if settings.get("auto_trading_limit_mode") == "fixed":
        return settings.get("auto_trading_limit_ars") or 0.0
    pct = settings.get("auto_trading_limit_pct")
    if not pct:
        return 0.0
    return patrimonio_ars * pct / 100.0


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

    def get_current_price(self, symbol, asset_type):
        """Precio de EJECUCIÓN de un símbolo puntual (ver conversación: "que
        tome los precios de la plataforma que usa" -- usado por
        fetchers.py::update_quote cuando un instrumento tiene
        broker_account_id seteado, en vez de data912/etc. genérico).
        Devuelve {"price": float, "bid": float|None, "ask": float|None}."""
        raise NotImplementedError

    # --- Ejecución real de órdenes (ver portfolio/order_execution.py) --------
    # Default NotImplementedError para todo bróker que no la tenga escrita
    # todavía (hoy: solo PPIAdapter la implementa) -- order_execution.py
    # nunca chequea "qué bróker es esto" a mano, solo llama estos métodos y
    # deja que la excepción explique por qué no se pudo.

    def get_order_budget(self, **kwargs):
        raise NotImplementedError(f"{self.broker_name}: ejecución real de órdenes no implementada todavía.")

    def confirm_order(self, **kwargs):
        raise NotImplementedError(f"{self.broker_name}: ejecución real de órdenes no implementada todavía.")

    def cancel_order(self, **kwargs):
        raise NotImplementedError(f"{self.broker_name}: ejecución real de órdenes no implementada todavía.")


class IOLAdapter(BrokerAdapter):
    """InvertirOnline -- ver HONESTIDAD DECLARADA arriba del módulo.
    test_connection/get_balance/get_holdings YA CONFIRMADOS contra la cuenta
    real del usuario (2026-10-20: login, saldo y tenencias respondieron
    correctamente) -- dejan de ser "nunca probado". get_order_budget/
    confirm_order/cancel_order (agregados en esta misma fecha) SÍ siguen sin
    probar contra la red real (ver disclaimer de cada uno) -- confirmados
    solo contra documentación pública + el código fuente real del SDK
    `iol-api` (PyPI, ver docstring de confirm_order), nunca contra una orden
    real (eso requeriría ejecutar de verdad, fuera de alcance de una
    auditoría). api_key/api_secret de broker_accounts se interpretan aquí
    como username/password (el login real de IOL es usuario+contraseña de
    la cuenta, no un par key/secret clásico -- se reusan esos 2 campos
    genéricos de la tabla para no duplicar el esquema por broker)."""
    broker_name = "IOL"
    BASE_URL = "https://api.invertironline.com"
    TIMEOUT = 15

    # Confirmado contra el tarifario público de IOL (ver conversación
    # 2026-10-20) y contra iol_api/constants.py::Tarifas (SDK real,
    # coincide): Gold 0.50%, Platinum 0.30%, Black 0.10%, todos + IVA (21%).
    # El usuario confirmó que opera en el tramo más bajo (Gold) tanto acá
    # como en PPI -- usado SOLO para la estimación local de get_order_budget
    # (ver ese método: IOL no tiene un endpoint real de presupuesto).
    _GOLD_COMMISSION_PCT = 0.50 * 1.21

    # plazo/tipoOrden confirmados contra el código fuente real del SDK
    # `iol-api` (PyPI, iol_api/constants.py::Plazo/TipoDeOrden) -- NUNCA
    # adivinados. Mapean desde el vocabulario genérico de este proyecto
    # (settlement/operation_type, el mismo que ya usa PPIAdapter) al
    # vocabulario propio de IOL.
    _SETTLEMENT_TO_PLAZO = {"INMEDIATA": "t0", "A-24HS": "t1", "A-48HS": "t2"}
    _OPERATION_TYPE_TO_TIPO_ORDEN = {"PRECIO-LIMITE": "precioLimite", "PRECIO-DE-MERCADO": "precioMercado"}
    _VALID_OPERATIONS = ("Compra", "Venta")

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

    def get_current_price(self, symbol, asset_type):
        """Confirmado contra iol.apidocs.ar (WebFetch real, ver conversación
        -- no de memoria): GET /api/v2/{mercado}/Titulos/{simbolo}/Cotizacion,
        respuesta con "ultimoPrecio" y un array "puntas" para bid/ask. Mismo
        disclaimer que el resto del adapter: nunca probado con credenciales
        reales. mercado="bCBA" para todo lo que maneja esta app (todo listado
        en BYMA/Argentina) -- IOL no necesita un "type" como PPI, el símbolo +
        mercado ya alcanzan."""
        resp = requests.get(
            f"{self.BASE_URL}/api/v2/bCBA/Titulos/{symbol}/Cotizacion",
            headers=self._headers(), timeout=self.TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        price = data.get("ultimoPrecio")
        if price is None:
            raise ValueError(f"Cotización de IOL sin 'ultimoPrecio': {list(data.keys())}")
        # "puntas" (array de puntas de compra/venta) -- nombres de sub-campo
        # NO confirmados contra la documentación consultada, así que nunca se
        # inventan: si la forma no es la esperada, bid/ask quedan None en vez
        # de un número adivinado.
        puntas = data.get("puntas") or []
        punta = puntas[0] if puntas else {}
        bid = punta.get("precioCompra") if isinstance(punta, dict) else None
        ask = punta.get("precioVenta") if isinstance(punta, dict) else None
        return {"price": price, "bid": bid, "ask": ask}

    # --- Ejecución real de órdenes (ver portfolio/order_execution.py) --------
    #
    # Confirmado 2026-10-20 contra DOS fuentes reales (nunca de memoria):
    # 1. iol.apidocs.ar (WebFetch real) -- confirma que existen operaciones
    #    "POST Comprar"/"POST Vender" bajo la sección "Operar", con un
    #    payload {mercado, simbolo, cantidad, precio, plazo, validez,
    #    tipoOrden, monto, idFuente} y respuesta {ok, messages}.
    # 2. El código FUENTE REAL del paquete `iol-api` (PyPI, `pip download
    #    iol-api` -- mismo criterio que ya se usó para PPIAdapter con
    #    `ppi_client`) -- confirma el path EXACTO (api/v2/operar/Comprar,
    #    api/v2/operar/Vender -- bajo /api/v2/, NO /api/operar/ como sugería
    #    el resumen de la página de docs: se prioriza el SDK real sobre el
    #    resumen de una página, mismo criterio que "nunca de memoria ni
    #    adivinado"), los valores reales de plazo ('t0'/'t1'/'t2') y
    #    tipoOrden ('precioLimite'/'precioMercado'), que 'cantidad' y 'monto'
    #    son MUTUAMENTE EXCLUYENTES en una compra (nunca los 2 juntos), que
    #    una venta SIEMPRE es por 'cantidad' (la API no admite 'monto' para
    #    vender), que 'validez' es un timestamp ISO completo (no un enum --
    #    el SDK por defecto usa el fin del día de hoy, se replica igual acá),
    #    y que 'idFuente' NO aparece en el payload real que arma ese SDK --
    #    se omite en vez de inventar un valor para un campo no confirmado.
    #    El mismo SDK confirma además GET/DELETE api/v2/operaciones/{numero}
    #    para consultar/cancelar una orden ya enviada.
    #
    # DIFERENCIA HONESTA con PPI, importante para quien use esto desde
    # order_execution.py: IOL NO TIENE un endpoint de presupuesto/simulación
    # real (ninguna de las 2 fuentes confirma uno bajo "operar") -- Comprar/
    # Vender ES el único paso, y ya ejecuta de verdad. Por eso
    # get_order_budget() de acá NUNCA toca la red: devuelve una estimación
    # LOCAL (monto + comisión estimada al tramo Gold, el que confirmó el
    # usuario que usa) marcada explícita como estimación, nunca como una
    # cotización real del bróker -- a diferencia de PPI, en IOL no existe
    # forma de "consultar sin comprometerse", el único punto sin retorno es
    # confirm_order.
    #
    # NUNCA probado contra una cuenta real (ejecutar una orden real para
    # "probar que funciona" no es aceptable) -- mismo disclaimer que el
    # resto de los métodos de ejecución de este proyecto hasta que alguien
    # lo valide a propósito con una orden mínima real.

    def _validez_fin_de_dia(self):
        """Default del SDK real `iol-api` (OrdenCompra/OrdenVenta.set_validez):
        hoy a las 23:59:59, formateado como ISO con milisegundos + 'Z'. No
        hay un caller hoy que pase una validez propia -- se podría exponer
        como parámetro más adelante si hace falta, nunca se inventa un valor
        'sin vencimiento' que la API no documenta."""
        from datetime import datetime
        dt = datetime.now()
        fin_de_dia = datetime(dt.year, dt.month, dt.day, 23, 59, 59, 999000)
        return fin_de_dia.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    def _map_settlement_to_plazo(self, settlement):
        plazo = self._SETTLEMENT_TO_PLAZO.get(settlement)
        if plazo is None:
            raise ValueError(
                f"IOL: sin mapeo de settlement={settlement!r} a plazo (t0/t1/t2) -- "
                f"valores soportados: {list(self._SETTLEMENT_TO_PLAZO)}"
            )
        return plazo

    def _map_operation_type_to_tipo_orden(self, operation_type):
        tipo_orden = self._OPERATION_TYPE_TO_TIPO_ORDEN.get(operation_type)
        if tipo_orden is None:
            raise ValueError(
                f"IOL: sin mapeo de operation_type={operation_type!r} a tipoOrden -- "
                f"valores soportados: {list(self._OPERATION_TYPE_TO_TIPO_ORDEN)}"
            )
        return tipo_orden

    def get_order_budget(self, *, symbol, asset_type, operation, quantity, price,
                          quantity_type="Papeles", operation_type="PRECIO-LIMITE",
                          operation_term="HASTA-SU-EJECUCIÓN", settlement="INMEDIATA"):
        """IOL no tiene un endpoint de presupuesto real -- ver nota arriba de
        la clase. Nunca toca la red: devuelve una estimación LOCAL (monto =
        cantidad*precio, comisión estimada al tramo Gold confirmado del
        usuario) explícitamente marcada como estimación, no una cotización
        del bróker. confirm_order es el único punto sin retorno para IOL."""
        if operation not in self._VALID_OPERATIONS:
            raise ValueError(f"IOL: operation debe ser 'Compra' o 'Venta', recibido {operation!r}")
        monto = quantity * price
        comision_estimada = monto * self._GOLD_COMMISSION_PCT / 100.0
        return {
            "estimated": True,
            "monto_estimado": monto,
            "comision_estimada": comision_estimada,
            "comision_estimada_pct": self._GOLD_COMMISSION_PCT,
            "disclaimer": (
                "IOL no expone un endpoint de presupuesto real (confirmado contra "
                "documentación pública y el código fuente del SDK oficial 'iol-api') "
                "-- esto es una estimación LOCAL con la comisión del tramo Gold, "
                "nunca una cotización real del bróker. confirm_order es el paso que "
                "de verdad ejecuta la orden."
            ),
        }

    def confirm_order(self, *, symbol, asset_type, operation, quantity, price, external_id, budget,
                       quantity_type="Papeles", operation_type="PRECIO-LIMITE",
                       operation_term="HASTA-SU-EJECUCIÓN", settlement="INMEDIATA"):
        """Ejecuta de verdad -- POST api/v2/operar/Comprar o .../Vender (API
        real de IOL, ver nota de confirmación arriba de la clase). A
        diferencia de PPI, acá NO hay un budget previo que "confirmar": este
        es el ÚNICO paso que toca la red para una orden de IOL, así que es
        el punto sin retorno real. `external_id`/`budget` se reciben por la
        misma firma compartida con PPI (ver order_execution.py) pero IOL no
        tiene un campo equivalente confirmado para ninguno de los 2 -- se
        reciben y se ignoran a propósito, nunca se inventa dónde mandarlos."""
        if operation not in self._VALID_OPERATIONS:
            raise ValueError(f"IOL: operation debe ser 'Compra' o 'Venta', recibido {operation!r}")
        plazo = self._map_settlement_to_plazo(settlement)
        tipo_orden = self._map_operation_type_to_tipo_orden(operation_type)
        body = {
            "mercado": "bCBA",  # ver get_current_price -- todo lo que maneja esta app es BYMA/Argentina
            "simbolo": symbol, "precio": price, "plazo": plazo, "tipoOrden": tipo_orden,
            "validez": self._validez_fin_de_dia(),
        }
        if operation == "Compra":
            # 'cantidad' y 'monto' son mutuamente excluyentes en una compra
            # real de IOL (confirmado contra el SDK) -- este proyecto solo
            # tiene un caller real hoy (quantity_type="Papeles" fijo en
            # order_execution.py), 'Dinero' no se adivina sin un caller que
            # lo ejercite de verdad.
            if quantity_type != "Papeles":
                raise ValueError(
                    f"IOL: quantity_type={quantity_type!r} no soportado todavía para compra -- "
                    "solo 'Papeles' (cantidad de títulos) está confirmado contra un caller real."
                )
            body["cantidad"] = quantity
            path = "api/v2/operar/Comprar"
        else:
            if quantity_type != "Papeles":
                raise ValueError("IOL: una venta siempre es por cantidad de papeles -- la API no admite 'monto' para vender.")
            body["cantidad"] = quantity
            path = "api/v2/operar/Vender"

        resp = requests.post(f"{self.BASE_URL}/{path}", json=body, headers=self._headers(), timeout=self.TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict) and data.get("ok") is False:
            raise ValueError(f"IOL rechazó la orden: {data.get('messages')}")
        return data

    def cancel_order(self, *, order_id, external_id=None):
        """DELETE api/v2/operaciones/{numero} -- confirmado contra el código
        fuente real del SDK `iol-api` (método delete_operaciones). IOL
        identifica la orden por su propio número de operación, no por un id
        externo como PPI -- external_id se recibe por firma compartida y se
        ignora a propósito (nunca se inventa un uso para él)."""
        resp = requests.delete(
            f"{self.BASE_URL}/api/v2/operaciones/{order_id}", headers=self._headers(), timeout=self.TIMEOUT,
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

    # Enum confirmado contra la documentación oficial (itatppi.github.io,
    # WebFetch real -- ver conversación) para el parámetro "type" de
    # MarketData/Current. moneda/cripto no tienen equivalente en PPI -- se
    # omiten a propósito (get_current_price tira ValueError en vez de
    # adivinar uno).
    _ASSET_TYPE_MAP = {
        "accion": "ACCIONES",
        "cedear": "CEDEARS",
        "bono": "BONOS",
        "on": "ON",
        "fondo": "FCI",
    }

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

    def get_current_price(self, symbol, asset_type):
        """MarketData/Current -- confirmado contra la documentación oficial
        (WebFetch real: ppi.marketdata.current(ticker, type, settlement) ->
        GET 1.0/MarketData/Current?ticker=...&type=...&settlement=...,
        respuesta con campo "price"). settlement="A-48HS" es el valor del
        ejemplo oficial para acciones -- puede no ser el correcto para todo
        instrumento/mercado, pero es el único confirmado contra una fuente
        real en vez de adivinado. Sin bid/ask: la documentación confirma que
        ese dato vive en MarketData/Book (otro endpoint), no en Current --
        nunca se inventan acá."""
        ppi_type = self._ASSET_TYPE_MAP.get(asset_type)
        if ppi_type is None:
            raise ValueError(f"PPI: sin mapeo de tipo de instrumento para '{asset_type}'")
        resp = requests.get(
            f"{self.BASE_URL}/1.0/MarketData/Current",
            params={"ticker": symbol, "type": ppi_type, "settlement": "A-48HS"},
            headers=self._auth_headers(), timeout=self.TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        price = data.get("price")
        if price is None:
            raise ValueError(f"MarketData/Current de PPI sin 'price': {list(data.keys())}")
        return {"price": price, "bid": None, "ask": None}

    # --- Ejecución real de órdenes (ver conversación, "empezá a desarrollar
    # la ejecución real") -- SOLO llamable a través de
    # portfolio/order_execution.py::place_real_order, que chequea
    # ensure_auto_trading_enabled + compute_auto_trading_limit_ars ANTES de
    # llegar acá. Estos 3 métodos NUNCA deben llamarse directo desde una
    # ruta HTTP ni desde ningún otro lugar -- son el detalle de PPI, no el
    # gate (ver docstring del módulo).
    #
    # Valores de ejemplo confirmados contra la documentación oficial
    # (WebFetch real, itatppi.github.io -- no adivinados):
    # ppi.orders.budget(OrderBudget(account_number, 10000, 150, "GGAL",
    # "ACCIONES", "Dinero", "PRECIO-LIMITE", "HASTA-SU-EJECUCIÓN", None,
    # "Compra", "INMEDIATA")). Mapeando esa tupla posicional contra el orden
    # REAL de campos del dataclass OrderBudget del SDK (accountNumber,
    # quantity, price, ticker, instrumentType, quantityType, operationType,
    # operationTerm, operationMaxDate, operation, settlement,
    # activationPrice): quantityType="Dinero"|"Papeles" (orden por monto o
    # por cantidad de papeles), operationType="PRECIO-LIMITE"|
    # "PRECIO-DE-MERCADO" (el TIPO DE PRECIO, no compra/venta pese al
    # nombre -- fácil de confundir), operation="Compra"|"Venta" (esto SÍ es
    # la dirección), operationTerm="HASTA-SU-EJECUCIÓN", settlement=
    # "INMEDIATA" (o "A-24HS"/"A-48HS"/"A-72HS", mismos valores que
    # get_current_price). NUNCA probado contra una cuenta real -- mismo
    # disclaimer que el resto del adapter.
    _VALID_OPERATIONS = ("Compra", "Venta")

    def get_order_budget(self, *, symbol, asset_type, operation, quantity, price,
                          quantity_type="Papeles", operation_type="PRECIO-LIMITE",
                          operation_term="HASTA-SU-EJECUCIÓN", settlement="INMEDIATA"):
        """Presupuesto de una orden -- NO ejecuta nada todavía (equivalente a
        ppi.orders.budget), solo estima costo/comisión y devuelve los
        disclaimers que confirm_order va a necesitar aceptar."""
        ppi_type = self._ASSET_TYPE_MAP.get(asset_type)
        if ppi_type is None:
            raise ValueError(f"PPI: sin mapeo de tipo de instrumento para '{asset_type}'")
        if operation not in self._VALID_OPERATIONS:
            raise ValueError(f"PPI: operation debe ser 'Compra' o 'Venta', recibido {operation!r}")
        body = {
            "accountNumber": self._account_number(),
            "quantity": quantity, "price": price, "ticker": symbol,
            "instrumentType": ppi_type, "quantityType": quantity_type,
            "operationType": operation_type, "operationTerm": operation_term,
            "operationMaxDate": None, "operation": operation, "settlement": settlement,
            "activationPrice": None,
        }
        resp = requests.post(
            f"{self.BASE_URL}/1.0/Order/Budget", json=body, headers=self._auth_headers(), timeout=self.TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()

    def confirm_order(self, *, symbol, asset_type, operation, quantity, price, external_id, budget,
                       quantity_type="Papeles", operation_type="PRECIO-LIMITE",
                       operation_term="HASTA-SU-EJECUCIÓN", settlement="INMEDIATA"):
        """CONFIRMA (ejecuta de verdad) la orden presupuestada por
        get_order_budget -- los parámetros de la orden se vuelven a pasar
        explícitos (nunca se leen de vuelta de `budget`: la forma exacta de
        esa respuesta no está confirmada contra documentación real, los
        disclaimers sí). disclaimers: mismo patrón que el ejemplo oficial
        (Disclaimer(disclaimer['code'], True) -- confirmado que la clave se
        llama 'code', no inventada)."""
        ppi_type = self._ASSET_TYPE_MAP.get(asset_type)
        if ppi_type is None:
            raise ValueError(f"PPI: sin mapeo de tipo de instrumento para '{asset_type}'")
        if operation not in self._VALID_OPERATIONS:
            raise ValueError(f"PPI: operation debe ser 'Compra' o 'Venta', recibido {operation!r}")
        disclaimers = [
            {"code": d["code"], "accepted": True}
            for d in (budget.get("disclaimers") or []) if isinstance(d, dict) and "code" in d
        ]
        body = {
            "accountNumber": self._account_number(),
            "quantity": quantity, "price": price, "ticker": symbol,
            "instrumentType": ppi_type, "quantityType": quantity_type,
            "operationType": operation_type, "operationTerm": operation_term,
            "operationMaxDate": None, "operation": operation, "settlement": settlement,
            "disclaimers": disclaimers, "externalId": external_id, "activationPrice": None,
        }
        resp = requests.post(
            f"{self.BASE_URL}/1.0/Order/Confirm", json=body, headers=self._auth_headers(), timeout=self.TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()

    def cancel_order(self, *, order_id, external_id=None):
        """Cancela una orden activa (equivalente a ppi.orders.cancel_order)."""
        body = {"accountNumber": self._account_number(), "orderID": order_id, "externalID": external_id}
        resp = requests.post(
            f"{self.BASE_URL}/1.0/Order/Cancel", json=body, headers=self._auth_headers(), timeout=self.TIMEOUT,
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
