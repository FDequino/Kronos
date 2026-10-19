import json
import os
import sqlite3
from datetime import datetime, timedelta

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "portfolio.db")
SCHEMA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")


def get_connection(db_path=None):
    conn = sqlite3.connect(db_path or DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    with open(SCHEMA_PATH) as f:
        conn.executescript(f.read())
    _migrate(conn)
    return conn


def _migrate(conn):
    """Altera tablas ya creadas cuando schema.sql no alcanza (CREATE TABLE IF NOT
    EXISTS no agrega columnas nuevas a una tabla preexistente)."""
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(forecasts)").fetchall()}
    if "as_of_date" not in cols:
        conn.execute("ALTER TABLE forecasts ADD COLUMN as_of_date TEXT")
        conn.commit()

    # Fase 11: sumar 'cripto' al CHECK de instruments.type -- SQLite no permite
    # ALTER sobre un CHECK existente, hay que reconstruir la tabla (patron
    # estandar: tabla nueva, copiar filas, drop, rename -- el rename ya
    # actualiza sqlite_sequence solo). Se detecta leyendo el SQL real de la
    # tabla en sqlite_master en vez de una columna, porque esto no es una
    # columna nueva. Probado primero contra una copia de portfolio.db real
    # (PRAGMA foreign_key_check limpio, mismos conteos de fila en las 8 tablas
    # que referencian instruments.id) antes de dejarlo correr aca.
    instruments_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='instruments'"
    ).fetchone()[0]
    if "'cripto'" not in instruments_sql:
        # PRAGMA foreign_keys debe ir en su propio conn.execute(), no embebido
        # en el executescript de abajo -- SQLite no permite cambiar este
        # pragma dentro de una transaccion ya abierta, y executescript corre
        # todo su contenido como una sola transaccion implicita (confirmado:
        # con el PRAGMA adentro del script, el DROP TABLE de mas abajo fallaba
        # con "FOREIGN KEY constraint failed" porque el OFF nunca se aplicaba).
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.executescript("""
            CREATE TABLE instruments_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                name TEXT,
                type TEXT NOT NULL CHECK (type IN ('accion', 'cedear', 'bono', 'on', 'moneda', 'fondo', 'cripto')),
                market TEXT NOT NULL,
                currency TEXT NOT NULL DEFAULT 'ARS',
                price_source TEXT NOT NULL CHECK (price_source IN ('data912', 'dolarapi', 'binance', 'manual')),
                price_source_params TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                maturity_date TEXT,
                coupon_rate REAL,
                coupon_frequency INTEGER,
                rate_type TEXT CHECK (rate_type IN ('fija', 'cer')),
                cuenta TEXT,
                UNIQUE (symbol, market)
            );
            INSERT INTO instruments_new (id, symbol, name, type, market, currency, price_source, price_source_params, created_at, maturity_date, coupon_rate, coupon_frequency, rate_type, cuenta)
            SELECT id, symbol, name, type, market, currency, price_source, price_source_params, created_at, maturity_date, coupon_rate, coupon_frequency, rate_type, NULL
            FROM instruments;
            DROP TABLE instruments;
            ALTER TABLE instruments_new RENAME TO instruments;
        """)
        conn.execute("PRAGMA foreign_keys=ON")
        conn.commit()

    instrument_cols = {row["name"] for row in conn.execute("PRAGMA table_info(instruments)").fetchall()}
    new_instrument_cols = {
        "maturity_date": "TEXT",
        "coupon_rate": "REAL",
        "coupon_frequency": "INTEGER",
        "rate_type": "TEXT",
        "cuenta": "TEXT",
        # Ver conversacion, portfolio/history.py::_ensure_history -- bug real
        # encontrado al perfilar el dashboard en la VPS: algunos instrumentos
        # (LETRAS/bonos de muy corto plazo, ej. S16O6/TZX27/TZXY7) nunca
        # tuvieron historial en data912 -- _ensure_history reintentaba el
        # fetch de red (timeout=20s) en CADA carga del dashboard porque
        # get_price_history seguia vacio despues de cada intento fallido, sin
        # ningun lugar donde recordar "esto ya se probo y no existe". Con 3-5
        # instrumentos asi, eso solo explicaba +60-100s por carga.
        "history_backfill_failed_at": "TEXT",
    }
    for col, ddl in new_instrument_cols.items():
        if col not in instrument_cols:
            conn.execute(f"ALTER TABLE instruments ADD COLUMN {col} {ddl}")
    conn.commit()

    signal_cols = {row["name"] for row in conn.execute("PRAGMA table_info(signal_config)").fetchall()}
    new_signal_cols = {
        "ta_rsi_oversold": "REAL NOT NULL DEFAULT 35.0",
        "ta_rsi_overbought": "REAL NOT NULL DEFAULT 70.0",
        "fa_max_pe": "REAL NOT NULL DEFAULT 25.0",
        "fa_min_margin_pct": "REAL NOT NULL DEFAULT 8.0",
        "min_directional_hit_pct": "REAL NOT NULL DEFAULT 55.0",
        "min_non_overlapping_forecasts": "INTEGER NOT NULL DEFAULT 30",
        "caucion_reference_tna": "REAL",
        "risk_vol_low_pct": "REAL NOT NULL DEFAULT 25.0",
        "risk_vol_high_pct": "REAL NOT NULL DEFAULT 50.0",
        "risk_cvar_min_months": "INTEGER NOT NULL DEFAULT 12",
        # Umbral de desvio (en puntos porcentuales) vs. target_allocation para
        # que el motor de propuestas (Fase 8) sugiera rebalanceo -- ver
        # portfolio/proposals.py.
        "proposals_rebalance_threshold_pct": "REAL NOT NULL DEFAULT 5.0",
        # Edge ratio minimo (|mediana pronosticada| / ancho de banda p10-p90,
        # ambos en %) para que una señal comprar/vender se mantenga como tal
        # -- por debajo de esto, el movimiento proyectado es chico frente a
        # la incertidumbre del propio modelo y la señal se degrada a
        # "mantener" (ver portfolio/signals.py::compute_signal). 0.2 es un
        # punto de partida razonable (dejar manifiesto el supuesto, no fue
        # ajustado contra datos historicos todavia), ajustable como cualquier
        # otro umbral via PUT /api/portfolio/signal-config.
        "min_signal_edge_ratio": "REAL NOT NULL DEFAULT 0.2",
        # Arquitectura Dual CORE/SATELITE (ver portfolio/config.py) -- % del
        # capital asignado al motor Core institucional; el resto
        # (100 - esto) es Satelite, nunca se guarda por separado para que no
        # puedan desincronizarse. Dinamico de verdad: se lee/escribe via
        # PUT /api/portfolio/signal-config o /capital, /set_core en Telegram,
        # nunca un valor fijo en un .py. Default 20.0 (antes 70.0, ver
        # conversacion): el Satelite demostro Sharpe 1.77 Out-Of-Sample
        # consistente, el Core todavia no tiene edge positivo confirmado --
        # el punto de partida de una instalacion nueva debe reflejar eso,
        # no al reves. Esto solo afecta instalaciones NUEVAS -- una DB ya
        # migrada mantiene el valor que ya tenia guardado (se actualiza por
        # separado con un UPDATE explicito si corresponde, nunca reescribiendo
        # el DEFAULT de esta migracion).
        "core_allocation_pct": "REAL NOT NULL DEFAULT 20.0",
        # Refactorizacion financiera en USD CCL (ver conversacion): salida del
        # Motor Satelite totalmente configurable por el usuario -- antes eran
        # constantes fijas en portfolio/short_term_engine.py
        # (SATELLITE_TAKE_PROFIT_PCT/SATELLITE_STOP_LOSS_PCT), que ahora solo
        # quedan como default de referencia. satellite_stop_loss_pct es un
        # TECHO (ver portfolio/backtest.py::simulate_position_exit,
        # stop_is_ceiling=True): el stop real es
        # min(satellite_stop_loss_pct, 1.8xATR), nunca pierde MAS que esto en
        # una sola operacion, aunque el activo sea muy volatil (decision
        # explicita del usuario, ver conversacion -- antes era un piso
        # (max), lo que podia dejar stops de hasta ~10% en nombres volatiles
        # sin ningun techo).
        "satellite_take_profit_pct": "REAL NOT NULL DEFAULT 4.0",
        "satellite_stop_loss_pct": "REAL NOT NULL DEFAULT 9.0",
        # Umbral de seleccion del Motor Core (ver conversacion) -- antes
        # CORE_MIN_EFFICIENCY_SCORE era una constante fija en
        # portfolio/signals.py (recalibrada 3 veces a mano en esta misma
        # conversacion: 0.30 -> 130.0 -> 80.0 -> 68.0 -> vuelta a 80.0).
        # Ahora es dinamica como el resto de la config de ambos motores --
        # signals.py sigue declarando CORE_MIN_EFFICIENCY_SCORE=80.0 como
        # default de referencia, nunca el valor real que opera.
        "core_min_efficiency_score": "REAL NOT NULL DEFAULT 80.0",
    }
    for col, ddl in new_signal_cols.items():
        if col not in signal_cols:
            conn.execute(f"ALTER TABLE signal_config ADD COLUMN {col} {ddl}")
    conn.commit()

    settings_cols = {row["name"] for row in conn.execute("PRAGMA table_info(app_settings)").fetchall()}
    new_settings_cols = {
        "usd_rate_source": "TEXT NOT NULL DEFAULT 'bna'",
        "ars_inflation_adjusted": "INTEGER NOT NULL DEFAULT 0",
        # Fase 14: chat_id autorizado del bot de Telegram -- NULL hasta que el
        # dueño mande /start por primera vez (ver portfolio/telegram_bot.py).
        "telegram_chat_id": "TEXT",
        "telegram_daily_summary_hour": "INTEGER NOT NULL DEFAULT 9",
        # Paper Trading / dinero ficticio (ver conversacion) -- flag global,
        # default True (el enunciado lo pide asi): con esto en 0, el scanner
        # intradia (portfolio/monitor.py) sigue chequeando salidas de
        # posiciones YA abiertas, pero no abre ninguna posicion nueva.
        "paper_trading_enabled": "INTEGER NOT NULL DEFAULT 1",
        # Capital virtual de arranque (USD) -- arbitrario, declarado como tal
        # (no hay "capital real" al que anclarlo, es dinero ficticio),
        # ajustable via PUT /api/portfolio/paper-trading/settings.
        "paper_trading_initial_capital_usd": "REAL NOT NULL DEFAULT 10000.0",
        # Reporte PDF configurable (ver conversacion) -- sin CHECK en la
        # columna (mismo criterio que pairs_allocation_pct/etc mas arriba:
        # SQLite permite CHECK en ADD COLUMN pero este proyecto no lo usa en
        # migraciones, valida en Python para no tener que reconstruir la
        # tabla si el valor permitido cambia). pdf_currency: 'USD'/'ARS'/'DUAL'.
        # Los 3 include_* son booleanos (0/1), default 1 (reporte completo
        # hasta que el usuario recorte algo a proposito).
        "pdf_currency": "TEXT NOT NULL DEFAULT 'DUAL'",
        "pdf_include_paper_trading": "INTEGER NOT NULL DEFAULT 1",
        "pdf_include_risk_metrics": "INTEGER NOT NULL DEFAULT 1",
        "pdf_include_yield_caucion": "INTEGER NOT NULL DEFAULT 1",
        # Rediseño ejecutivo del PDF (ver conversacion) -- "Señales y
        # Oportunidades del Día" (Block 3) llama a live_signals.compute_today_signals,
        # que para el Motor Core consulta a Chronos-Bolt (red, puede tardar o
        # fallar si el servicio no esta arriba) -- flag propio (default 1,
        # mismo criterio que los demas include_*) para que el usuario pueda
        # sacarla del reporte automatico si no quiere pagar ese costo/riesgo
        # en cada envio programado.
        "pdf_include_opportunities": "INTEGER NOT NULL DEFAULT 1",
        # Programacion horaria del envio (ver conversacion, portfolio/telegram_bot.py)
        # -- 'morning'/'afternoon'/'both'. pdf_report_time en formato "HH:MM"
        # (string, no INTEGER como telegram_daily_summary_hour de arriba: ese
        # solo guarda la hora en punto, este pide minutos tambien, ej "09:30").
        "pdf_report_schedule": "TEXT NOT NULL DEFAULT 'morning'",
        "pdf_report_time": "TEXT NOT NULL DEFAULT '09:30'",
        # Horario de la tarde, antes una constante fija (PDF_REPORT_AFTERNOON_TIME
        # en portfolio/telegram_bot.py) -- ahora configurable igual que la mañana
        # de arriba. Default '17:30' a proposito: una instalacion ya migrada no
        # debe ver cambiar el horario de un dia para el otro solo por este agregado.
        "pdf_report_time_afternoon": "TEXT NOT NULL DEFAULT '17:30'",
        # Fin de semana: el PDF programado no tenia NINGUN gate de dia habil (a
        # diferencia del scanner de Paper Trading, ver portfolio/monitor.py::
        # is_trading_day) -- terminaba mandando un "Resultado del Dia" sabado/
        # domingo sin rueda detras, dato viejo disfrazado de actual. Default 1
        # (activado) porque ese comportamiento siempre fue un bug, no una
        # eleccion deliberada del usuario.
        "pdf_report_weekdays_only": "INTEGER NOT NULL DEFAULT 1",
        # Fecha (YYYY-MM-DD, hora Argentina) del ultimo envio exitoso por
        # franja -- el scheduler (ver portfolio/telegram_bot.py) corre cada
        # pocos minutos y consulta esto para no mandar el mismo reporte 2
        # veces el mismo dia solo porque el check volvio a pasar por la hora
        # configurada. NULL = nunca se mando. "Enviar Reporte de Prueba
        # Ahora" (envio manual) NO actualiza estas columnas -- son solo del
        # scheduler automatico, un test manual no debe bloquear el envio
        # automatico de ese dia.
        "pdf_report_last_sent_morning": "TEXT",
        "pdf_report_last_sent_afternoon": "TEXT",
        # Login (ver conversacion) -- NULL = todavia no se configuro ninguna
        # contraseña, la app muestra /setup en vez de /login hasta que se
        # defina una. Nunca se guarda en texto plano (werkzeug.security.
        # generate_password_hash, ver portfolio/auth.py).
        "app_login_password_hash": "TEXT",
        # Clave para firmar las cookies de sesion de Flask -- generada una
        # sola vez por instalacion (ver mas abajo), nunca hardcodeada en el
        # codigo fuente (séria un secreto compartido por todos los que
        # clonen el repo).
        "app_secret_key": "TEXT",
    }
    for col, ddl in new_settings_cols.items():
        if col not in settings_cols:
            conn.execute(f"ALTER TABLE app_settings ADD COLUMN {col} {ddl}")
    conn.commit()

    # Secret key de sesion: se genera UNA vez por instalacion si todavia no
    # existe (nunca se pisa una ya generada, eso invalidaria toda sesion
    # activa). Importado aca adentro (no al tope del modulo) para no sumarle
    # una dependencia de import a db.py que nada mas usa esta unica linea.
    row = conn.execute("SELECT app_secret_key FROM app_settings WHERE id = 1").fetchone()
    if row is not None and not row["app_secret_key"]:
        import secrets
        conn.execute(
            "UPDATE app_settings SET app_secret_key = ? WHERE id = 1", (secrets.token_hex(32),)
        )
        conn.commit()

    # Gastos del boleto de liquidacion (Fut) -- NUNCA incluia el costo real de
    # la caucion (gastos + 21% IVA sobre esos gastos, mismo patron ya visto en
    # transactions): monto_futuro siempre fue "bruto" (antes de estos costos),
    # consistente con como ya se usa para TNA/devengado, pero para la cuenta
    # de caja real (Fase 10) hace falta el NETO de verdad. Default 0 (no se
    # inventa un costo para cauciones cargadas antes de este campo).
    caucion_cols = {row["name"] for row in conn.execute("PRAGMA table_info(cauciones)").fetchall()}
    if "comision" not in caucion_cols:
        conn.execute("ALTER TABLE cauciones ADD COLUMN comision REAL NOT NULL DEFAULT 0")
        conn.commit()

    # Prestamos cargados antes de distinguir direccion eran todos "prestado"
    # (la unica forma que existia) -- default seguro, nunca se infiere otra cosa.
    loan_cols = {row["name"] for row in conn.execute("PRAGMA table_info(loans)").fetchall()}
    if "direccion" not in loan_cols:
        conn.execute("ALTER TABLE loans ADD COLUMN direccion TEXT NOT NULL DEFAULT 'prestado'")
        conn.commit()

    # Defaults viejos (horizonte 120 pasos, una sola muestra) quedaban con un
    # sesgo bajista estructural en ARS -- ver tests/test_kronos_ars_bias.py.
    # Solo se pisan si siguen en el valor de fabrica original (si alguien ya
    # los cambio a mano via PUT /signal-config, se respeta esa eleccion).
    stale_defaults = conn.execute(
        "SELECT default_pred_len, default_sample_count, benchmark_seconds_per_step "
        "FROM signal_config WHERE id = 1"
    ).fetchone()
    if stale_defaults:
        updates = {}
        if stale_defaults["default_pred_len"] == 120:
            updates["default_pred_len"] = 15
        if stale_defaults["default_sample_count"] == 1:
            updates["default_sample_count"] = 60
        if stale_defaults["benchmark_seconds_per_step"] == 0.41:
            updates["benchmark_seconds_per_step"] = 0.076
        if updates:
            cols = ", ".join(f"{k} = ?" for k in updates)
            conn.execute(f"UPDATE signal_config SET {cols} WHERE id = 1", tuple(updates.values()))
            conn.commit()

    # calibration_cache paso de PK instrument_id a PK (symbol, market) --ver
    # conversacion, --scope byma necesita poder cachear simbolos SIN alta en
    # `instruments`. SQLite no permite ALTER de una PRIMARY KEY existente, asi
    # que se reconstruye la tabla (mismo patron que el rebuild de `instruments`
    # mas arriba) -- es solo un snapshot derivado/regenerable (se recalcula
    # corriendo el batch de nuevo), nunca datos que el usuario cargo a mano,
    # asi que recrearla vacia en vez de migrar fila por fila es seguro.
    calibration_cache_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='calibration_cache'"
    ).fetchone()
    if calibration_cache_sql and "PRIMARY KEY (symbol, market)" not in calibration_cache_sql[0]:
        conn.execute("DROP TABLE calibration_cache")
        conn.executescript("""
            CREATE TABLE calibration_cache (
                symbol TEXT NOT NULL,
                market TEXT NOT NULL,
                instrument_id INTEGER REFERENCES instruments(id) ON DELETE SET NULL,
                mape REAL,
                directional_hit_pct REAL,
                forecasts_evaluated INTEGER NOT NULL DEFAULT 0,
                calibration_pct REAL,
                calibration_n INTEGER,
                anchors_run INTEGER NOT NULL DEFAULT 0,
                anchors_failed INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (symbol, market)
            );
        """)
        conn.commit()

    # Agente NLP (ver conversacion, portfolio/nlp_agent.py) -- columnas
    # nuevas y nullable, mismo patron simple que forecasts.as_of_date arriba
    # (sin CHECK que reconstruir).
    signal_config_cols = {row["name"] for row in conn.execute("PRAGMA table_info(signal_config)").fetchall()}
    if "openai_api_key" not in signal_config_cols:
        conn.execute("ALTER TABLE signal_config ADD COLUMN openai_api_key TEXT")
        conn.commit()
    if "anthropic_api_key" not in signal_config_cols:
        conn.execute("ALTER TABLE signal_config ADD COLUMN anthropic_api_key TEXT")
        conn.commit()

    # Interruptor maestro de ejecucion real de ordenes (ver conversacion,
    # portfolio/broker_client.py::ensure_auto_trading_enabled) -- arranca
    # en 0 para instalaciones existentes, mismo criterio que toda columna
    # nueva de seguridad: nunca default-habilitada.
    app_settings_cols = {row["name"] for row in conn.execute("PRAGMA table_info(app_settings)").fetchall()}
    if "auto_trading_enabled" not in app_settings_cols:
        conn.execute("ALTER TABLE app_settings ADD COLUMN auto_trading_enabled INTEGER NOT NULL DEFAULT 0")
        conn.commit()

    # Pairs Trading / Factor Investing (ver conversacion) -- asignacion
    # NOMINAL/informativa para las tarjetas de estos 2 motores nuevos, nunca
    # mezclada con core_allocation_pct (ese 100% sigue siendo exclusivo de
    # Core/Satelite, ver portfolio/config.py::get_capital_allocation -- esta
    # app no tiene todavia un backtest validado de estos 2 motores nuevos
    # como para que compitan por la MISMA porcion de capital ya probada).
    # NULL = "sin configurar", cae al default de portfolio/config.py.
    if "pairs_allocation_pct" not in signal_config_cols:
        conn.execute("ALTER TABLE signal_config ADD COLUMN pairs_allocation_pct REAL")
        conn.commit()
    if "factor_allocation_pct" not in signal_config_cols:
        conn.execute("ALTER TABLE signal_config ADD COLUMN factor_allocation_pct REAL")
        conn.commit()

    # Sincronizacion autonoma de Propuestas (ver conversacion,
    # portfolio/telegram_bot.py::_proposals_sync_job) -- columna A PROPOSITO
    # separada de `estado`: una propuesta queda en estado='nueva' hasta que el
    # USUARIO la ve/acepta/descarta desde la UI, pero el job de notificacion
    # corre solo en segundo plano y no debe re-mandar la misma propuesta en
    # cada corrida solo porque nadie la miro todavia. NULL = todavia no
    # notificada por Telegram.
    proposals_cols = {row["name"] for row in conn.execute("PRAGMA table_info(proposals)").fetchall()}
    if "telegram_notified_at" not in proposals_cols:
        conn.execute("ALTER TABLE proposals ADD COLUMN telegram_notified_at TEXT")
        conn.commit()


def _row_to_dict(row):
    return dict(row) if row is not None else None


# --- Instrumentos ---------------------------------------------------------

def add_instrument(conn, symbol, type_, market, price_source, name=None,
                    currency="ARS", price_source_params=None,
                    maturity_date=None, coupon_rate=None, coupon_frequency=None, rate_type=None, cuenta=None):
    params_json = json.dumps(price_source_params) if price_source_params else None
    cur = conn.execute(
        """INSERT INTO instruments (symbol, name, type, market, currency, price_source, price_source_params,
               maturity_date, coupon_rate, coupon_frequency, rate_type, cuenta)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (symbol, name, type_, market, currency, price_source, params_json,
         maturity_date, coupon_rate, coupon_frequency, rate_type, cuenta),
    )
    conn.commit()
    return cur.lastrowid


def get_instrument(conn, instrument_id):
    row = conn.execute("SELECT * FROM instruments WHERE id = ?", (instrument_id,)).fetchone()
    return _row_to_dict(row)


def find_instrument(conn, symbol, market):
    row = conn.execute(
        "SELECT * FROM instruments WHERE symbol = ? AND market = ?", (symbol, market)
    ).fetchone()
    return _row_to_dict(row)


def list_instruments(conn, type_=None):
    if type_:
        rows = conn.execute("SELECT * FROM instruments WHERE type = ? ORDER BY symbol", (type_,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM instruments ORDER BY type, symbol").fetchall()
    return [_row_to_dict(r) for r in rows]


def update_instrument(conn, instrument_id, **fields):
    if not fields:
        return get_instrument(conn, instrument_id)
    if "price_source_params" in fields and fields["price_source_params"] is not None:
        fields["price_source_params"] = json.dumps(fields["price_source_params"])
    columns = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE instruments SET {columns} WHERE id = ?", (*fields.values(), instrument_id))
    conn.commit()
    return get_instrument(conn, instrument_id)


def delete_instrument(conn, instrument_id):
    conn.execute("DELETE FROM instruments WHERE id = ?", (instrument_id,))
    conn.commit()


# --- Transacciones ----------------------------------------------------------

def add_transaction(conn, instrument_id, fecha, tipo, cantidad, precio, comision=0, notas=None):
    cur = conn.execute(
        """INSERT INTO transactions (instrument_id, fecha, tipo, cantidad, precio, comision, notas)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (instrument_id, fecha, tipo, cantidad, precio, comision, notas),
    )
    conn.commit()
    return cur.lastrowid


def list_transactions(conn, instrument_id=None):
    if instrument_id:
        rows = conn.execute(
            "SELECT * FROM transactions WHERE instrument_id = ? ORDER BY fecha", (instrument_id,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM transactions ORDER BY fecha").fetchall()
    return [_row_to_dict(r) for r in rows]


def get_transaction(conn, transaction_id):
    row = conn.execute("SELECT * FROM transactions WHERE id = ?", (transaction_id,)).fetchone()
    return _row_to_dict(row)


def update_transaction(conn, transaction_id, **fields):
    if not fields:
        return get_transaction(conn, transaction_id)
    columns = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE transactions SET {columns} WHERE id = ?", (*fields.values(), transaction_id))
    conn.commit()
    return get_transaction(conn, transaction_id)


def delete_transaction(conn, transaction_id):
    conn.execute("DELETE FROM transactions WHERE id = ?", (transaction_id,))
    conn.commit()


def get_holdings(conn):
    """Tenencia actual y costo promedio ponderado por instrumento, derivados de transactions."""
    instruments = list_instruments(conn)
    holdings = []
    for inst in instruments:
        txs = list_transactions(conn, inst["id"])
        cantidad = 0.0
        costo_total = 0.0
        for tx in txs:
            if tx["tipo"] == "compra":
                costo_total += tx["cantidad"] * tx["precio"] + tx["comision"]
                cantidad += tx["cantidad"]
            else:
                if cantidad > 0:
                    costo_total -= (costo_total / cantidad) * tx["cantidad"]
                cantidad -= tx["cantidad"]
                costo_total -= tx["comision"]
        if cantidad <= 1e-9:
            continue
        costo_promedio = costo_total / cantidad if cantidad else 0.0
        holdings.append({
            "instrument": inst,
            "cantidad": cantidad,
            "costo_promedio": costo_promedio,
            "costo_total": costo_total,
        })
    return holdings


# --- Plazos fijos -----------------------------------------------------------

def add_plazo_fijo(conn, banco, tipo, monto, tna, fecha_inicio, plazo_dias, notas=None):
    fecha_vencimiento = (
        datetime.fromisoformat(fecha_inicio) + timedelta(days=plazo_dias)
    ).date().isoformat()
    cur = conn.execute(
        """INSERT INTO plazos_fijos (banco, tipo, monto, tna, fecha_inicio, plazo_dias, fecha_vencimiento, notas)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (banco, tipo, monto, tna, fecha_inicio, plazo_dias, fecha_vencimiento, notas),
    )
    conn.commit()
    return cur.lastrowid


def list_plazos_fijos(conn, estado=None):
    if estado:
        rows = conn.execute(
            "SELECT * FROM plazos_fijos WHERE estado = ? ORDER BY fecha_vencimiento", (estado,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM plazos_fijos ORDER BY fecha_vencimiento").fetchall()
    return [_row_to_dict(r) for r in rows]


def get_plazo_fijo(conn, plazo_fijo_id):
    row = conn.execute("SELECT * FROM plazos_fijos WHERE id = ?", (plazo_fijo_id,)).fetchone()
    return _row_to_dict(row)


def update_plazo_fijo(conn, plazo_fijo_id, **fields):
    if not fields:
        return get_plazo_fijo(conn, plazo_fijo_id)
    if "fecha_inicio" in fields or "plazo_dias" in fields:
        current = get_plazo_fijo(conn, plazo_fijo_id)
        fecha_inicio = fields.get("fecha_inicio", current["fecha_inicio"])
        plazo_dias = fields.get("plazo_dias", current["plazo_dias"])
        fields["fecha_vencimiento"] = (
            datetime.fromisoformat(fecha_inicio) + timedelta(days=plazo_dias)
        ).date().isoformat()
    columns = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE plazos_fijos SET {columns} WHERE id = ?", (*fields.values(), plazo_fijo_id))
    conn.commit()
    return get_plazo_fijo(conn, plazo_fijo_id)


def delete_plazo_fijo(conn, plazo_fijo_id):
    conn.execute("DELETE FROM plazos_fijos WHERE id = ?", (plazo_fijo_id,))
    conn.commit()


def get_plazos_fijos_resumen(conn, as_of=None):
    """Devengado a la fecha con interes simple: monto * tna * dias_transcurridos / 365."""
    as_of = as_of or datetime.now().date()
    resumen = []
    for pf in list_plazos_fijos(conn, estado="activo"):
        inicio = datetime.fromisoformat(pf["fecha_inicio"]).date()
        vencimiento = datetime.fromisoformat(pf["fecha_vencimiento"]).date()
        dias_transcurridos = min((as_of - inicio).days, pf["plazo_dias"])
        dias_transcurridos = max(dias_transcurridos, 0)
        devengado = pf["monto"] * pf["tna"] * dias_transcurridos / 365
        resumen.append({
            "plazo_fijo": pf,
            "dias_transcurridos": dias_transcurridos,
            "devengado": devengado,
            "valor_actual": pf["monto"] + devengado,
            "vencido": as_of >= vencimiento,
        })
    return resumen


# --- Cauciones ------------------------------------------------------------------

def add_caucion(conn, *, fecha_apertura, fecha_vencimiento, plazo_dias, monto_contado,
                 monto_futuro, tasa, interes_bruto, moneda="ARS", numero_boleto=None,
                 comision=0, notas=None):
    cur = conn.execute(
        """INSERT INTO cauciones (numero_boleto, fecha_apertura, fecha_vencimiento, plazo_dias,
               monto_contado, monto_futuro, tasa, interes_bruto, moneda, comision, notas)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (numero_boleto, fecha_apertura, fecha_vencimiento, plazo_dias, monto_contado,
         monto_futuro, tasa, interes_bruto, moneda, comision, notas),
    )
    conn.commit()
    return cur.lastrowid


def list_cauciones(conn):
    rows = conn.execute("SELECT * FROM cauciones ORDER BY fecha_vencimiento DESC").fetchall()
    return [_row_to_dict(r) for r in rows]


def get_caucion(conn, caucion_id):
    row = conn.execute("SELECT * FROM cauciones WHERE id = ?", (caucion_id,)).fetchone()
    return _row_to_dict(row)


def update_caucion(conn, caucion_id, **fields):
    if not fields:
        return get_caucion(conn, caucion_id)
    columns = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE cauciones SET {columns} WHERE id = ?", (*fields.values(), caucion_id))
    conn.commit()
    return get_caucion(conn, caucion_id)


def delete_caucion(conn, caucion_id):
    conn.execute("DELETE FROM cauciones WHERE id = ?", (caucion_id,))
    conn.commit()


def get_cauciones_resumen(conn, as_of=None):
    """Devengado proporcional al tiempo transcurrido sobre el interes bruto pactado."""
    as_of = as_of or datetime.now().date()
    resumen = []
    for c in list_cauciones(conn):
        inicio = datetime.fromisoformat(c["fecha_apertura"]).date()
        vencimiento = datetime.fromisoformat(c["fecha_vencimiento"]).date()
        vencido = as_of >= vencimiento
        dias_transcurridos = max(min((as_of - inicio).days, c["plazo_dias"]), 0)
        devengado = c["interes_bruto"] if vencido else c["interes_bruto"] * dias_transcurridos / c["plazo_dias"]
        resumen.append({
            "caucion": c,
            "dias_transcurridos": dias_transcurridos,
            "devengado": devengado,
            "valor_actual": c["monto_contado"] + devengado,
            "vencido": vencido,
        })
    return resumen


# --- Rentas y dividendos --------------------------------------------------------

def add_renta_dividendo(conn, *, symbol, fecha, tipo, moneda, bruto, neto,
                          gastos=0, instrument_id=None, notas=None):
    # Normalizar el simbolo "$" (como viene de algunos exports de broker) a
    # "ARS" -- el resto de la app (filtros de Transacciones, Dashboard) espera
    # el codigo, nunca el simbolo. Bug real: con "$" guardado literal, el KPI
    # de "Cobrado (12 meses, ARS)" siempre daba $0 porque nunca matcheaba.
    if moneda == "$":
        moneda = "ARS"
    cur = conn.execute(
        """INSERT INTO rentas_dividendos (instrument_id, symbol, fecha, tipo, moneda, bruto, gastos, neto, notas)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (instrument_id, symbol, fecha, tipo, moneda, bruto, gastos, neto, notas),
    )
    conn.commit()
    return cur.lastrowid


def list_rentas_dividendos(conn):
    rows = conn.execute("SELECT * FROM rentas_dividendos ORDER BY fecha DESC").fetchall()
    return [_row_to_dict(r) for r in rows]


# --- Caja (Fase 10) -------------------------------------------------------------

def add_cash_movement(conn, *, fecha, moneda, monto, origen, tipo_cambio=None,
                       referencia_tabla=None, referencia_id=None, notas=None):
    cur = conn.execute(
        """INSERT INTO cash_movements
               (fecha, moneda, monto, origen, tipo_cambio, referencia_tabla, referencia_id, notas)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (fecha, moneda, monto, origen, tipo_cambio, referencia_tabla, referencia_id, notas),
    )
    conn.commit()
    return cur.lastrowid


def list_cash_movements(conn, moneda=None):
    if moneda:
        rows = conn.execute(
            "SELECT * FROM cash_movements WHERE moneda = ? ORDER BY fecha, id", (moneda,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM cash_movements ORDER BY fecha, id").fetchall()
    return [_row_to_dict(r) for r in rows]


def delete_cash_movement(conn, movement_id):
    conn.execute("DELETE FROM cash_movements WHERE id = ?", (movement_id,))
    conn.commit()


def clear_cash_movements(conn, origen=None):
    """Para el backfill (Fase 10): se puede re-correr sin duplicar -- borra lo
    que ya se habia generado automaticamente (todo excepto 'aporte'/'retiro'/
    'aporte_inferido' manuales, que el usuario pudo haber cargado a mano)."""
    if origen:
        conn.execute("DELETE FROM cash_movements WHERE origen = ?", (origen,))
    else:
        conn.execute(
            "DELETE FROM cash_movements WHERE origen NOT IN ('aporte', 'retiro', 'transferencia', 'gasto_cuenta')"
        )
    conn.commit()


# --- Prestamos (Fase 10) --------------------------------------------------------

def add_loan(conn, *, deudor, moneda, capital, fecha_inicio, tasa_anual, plan_pago,
             ajusta_cer=False, plazo_meses=None, estado='vigente', direccion='prestado', notas=None):
    cur = conn.execute(
        """INSERT INTO loans
               (deudor, moneda, capital, fecha_inicio, tasa_anual, ajusta_cer,
                plan_pago, plazo_meses, estado, direccion, notas)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (deudor, moneda, capital, fecha_inicio, tasa_anual, int(ajusta_cer),
         plan_pago, plazo_meses, estado, direccion, notas),
    )
    conn.commit()
    return cur.lastrowid


def list_loans(conn):
    rows = conn.execute("SELECT * FROM loans ORDER BY fecha_inicio DESC").fetchall()
    return [_row_to_dict(r) for r in rows]


def get_loan(conn, loan_id):
    row = conn.execute("SELECT * FROM loans WHERE id = ?", (loan_id,)).fetchone()
    return _row_to_dict(row)


def update_loan(conn, loan_id, **fields):
    if not fields:
        return get_loan(conn, loan_id)
    columns = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE loans SET {columns} WHERE id = ?", (*fields.values(), loan_id))
    conn.commit()
    return get_loan(conn, loan_id)


def delete_loan(conn, loan_id):
    conn.execute("DELETE FROM loans WHERE id = ?", (loan_id,))
    conn.commit()


def add_loan_payment(conn, *, loan_id, fecha, capital=0, interes=0, notas=None):
    cur = conn.execute(
        "INSERT INTO loan_payments (loan_id, fecha, capital, interes, notas) VALUES (?, ?, ?, ?, ?)",
        (loan_id, fecha, capital, interes, notas),
    )
    conn.commit()
    return cur.lastrowid


def list_loan_payments(conn, loan_id):
    rows = conn.execute(
        "SELECT * FROM loan_payments WHERE loan_id = ? ORDER BY fecha", (loan_id,)
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def delete_loan_payment(conn, payment_id):
    conn.execute("DELETE FROM loan_payments WHERE id = ?", (payment_id,))
    conn.commit()


def add_loan_document(conn, *, loan_id, nombre_original, nombre_archivo, content_type=None, size_bytes=None, notas=None):
    cur = conn.execute(
        """INSERT INTO loan_documents (loan_id, nombre_original, nombre_archivo, content_type, size_bytes, notas)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (loan_id, nombre_original, nombre_archivo, content_type, size_bytes, notas),
    )
    conn.commit()
    return cur.lastrowid


def list_loan_documents(conn, loan_id):
    rows = conn.execute(
        "SELECT * FROM loan_documents WHERE loan_id = ? ORDER BY uploaded_at", (loan_id,)
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_loan_document(conn, document_id):
    row = conn.execute("SELECT * FROM loan_documents WHERE id = ?", (document_id,)).fetchone()
    return _row_to_dict(row)


def delete_loan_document(conn, document_id):
    conn.execute("DELETE FROM loan_documents WHERE id = ?", (document_id,))
    conn.commit()


# --- Cuentas de broker externas (Multi-Broker, ver conversacion) ------------

def add_broker_account(conn, *, broker_name, alias=None, api_key=None, api_secret=None,
                        account_number=None, is_active=True):
    cur = conn.execute(
        """INSERT INTO broker_accounts (broker_name, alias, api_key, api_secret, account_number, is_active)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (broker_name, alias, api_key, api_secret, account_number, int(is_active)),
    )
    conn.commit()
    return cur.lastrowid


def list_broker_accounts(conn):
    rows = conn.execute("SELECT * FROM broker_accounts ORDER BY created_at DESC").fetchall()
    return [_row_to_dict(r) for r in rows]


def get_broker_account(conn, account_id):
    row = conn.execute("SELECT * FROM broker_accounts WHERE id = ?", (account_id,)).fetchone()
    return _row_to_dict(row)


def update_broker_account(conn, account_id, **fields):
    if not fields:
        return get_broker_account(conn, account_id)
    if "is_active" in fields:
        fields["is_active"] = int(bool(fields["is_active"]))
    columns = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE broker_accounts SET {columns} WHERE id = ?", (*fields.values(), account_id))
    conn.commit()
    return get_broker_account(conn, account_id)


def delete_broker_account(conn, account_id):
    conn.execute("DELETE FROM broker_accounts WHERE id = ?", (account_id,))
    conn.commit()


def add_planned_movement(conn, *, fecha, tipo, monto, moneda, notas=None):
    cur = conn.execute(
        "INSERT INTO planned_movements (fecha, tipo, monto, moneda, notas) VALUES (?, ?, ?, ?, ?)",
        (fecha, tipo, monto, moneda, notas),
    )
    conn.commit()
    return cur.lastrowid


def list_planned_movements(conn):
    rows = conn.execute("SELECT * FROM planned_movements ORDER BY fecha").fetchall()
    return [_row_to_dict(r) for r in rows]


def delete_planned_movement(conn, movement_id):
    conn.execute("DELETE FROM planned_movements WHERE id = ?", (movement_id,))
    conn.commit()


# --- Cotizaciones -------------------------------------------------------------

def upsert_quote(conn, instrument_id, price, source, bid=None, ask=None, fetched_at=None):
    fetched_at = fetched_at or datetime.now().isoformat()
    conn.execute(
        """INSERT INTO quotes_latest (instrument_id, price, bid, ask, fetched_at, source)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(instrument_id) DO UPDATE SET
               price = excluded.price, bid = excluded.bid, ask = excluded.ask,
               fetched_at = excluded.fetched_at, source = excluded.source""",
        (instrument_id, price, bid, ask, fetched_at, source),
    )
    conn.commit()


def get_quote(conn, instrument_id):
    row = conn.execute("SELECT * FROM quotes_latest WHERE instrument_id = ?", (instrument_id,)).fetchone()
    return _row_to_dict(row)


def upsert_fundamentals(conn, instrument_id, data, fetched_at=None):
    fetched_at = fetched_at or datetime.now().isoformat()
    conn.execute(
        """INSERT INTO fundamentals_latest
               (instrument_id, pe_ratio, forward_pe, profit_margin, debt_to_equity,
                dividend_yield, market_cap, target_mean_price, available, not_applicable,
                reason, fetched_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(instrument_id) DO UPDATE SET
               pe_ratio = excluded.pe_ratio, forward_pe = excluded.forward_pe,
               profit_margin = excluded.profit_margin, debt_to_equity = excluded.debt_to_equity,
               dividend_yield = excluded.dividend_yield, market_cap = excluded.market_cap,
               target_mean_price = excluded.target_mean_price, available = excluded.available,
               not_applicable = excluded.not_applicable, reason = excluded.reason,
               fetched_at = excluded.fetched_at""",
        (
            instrument_id, data.get("pe_ratio"), data.get("forward_pe"), data.get("profit_margin"),
            data.get("debt_to_equity"), data.get("dividend_yield"), data.get("market_cap"),
            data.get("target_mean_price"), int(bool(data.get("available"))),
            int(bool(data.get("not_applicable"))), data.get("reason"), fetched_at,
        ),
    )
    conn.commit()


def get_fundamentals(conn, instrument_id):
    row = conn.execute(
        "SELECT * FROM fundamentals_latest WHERE instrument_id = ?", (instrument_id,)
    ).fetchone()
    return _row_to_dict(row)


# --- Motor de Factor Investing (ver conversacion, portfolio/factor_investing.py) --

def upsert_factor_score(conn, symbol, momentum_raw, low_vol_raw, value_raw, composite_score, rank, computed_at=None):
    computed_at = computed_at or datetime.now().isoformat()
    conn.execute(
        """INSERT INTO factor_scores
               (symbol, momentum_raw, low_vol_raw, value_raw, composite_score, rank, computed_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(symbol) DO UPDATE SET
               momentum_raw = excluded.momentum_raw, low_vol_raw = excluded.low_vol_raw,
               value_raw = excluded.value_raw, composite_score = excluded.composite_score,
               rank = excluded.rank, computed_at = excluded.computed_at""",
        (symbol, momentum_raw, low_vol_raw, value_raw, composite_score, rank, computed_at),
    )
    conn.commit()


def list_factor_scores(conn):
    rows = conn.execute("SELECT * FROM factor_scores ORDER BY rank").fetchall()
    return [_row_to_dict(r) for r in rows]


def get_factor_scores_latest_computed_at(conn):
    row = conn.execute("SELECT MAX(computed_at) AS max_computed_at FROM factor_scores").fetchone()
    return row["max_computed_at"] if row else None


def delete_factor_scores(conn):
    """Usado solo por tests -- limpia la cache semanal para forzar un recalculo."""
    conn.execute("DELETE FROM factor_scores")
    conn.commit()


def get_technical_cache(conn, instrument_id):
    row = conn.execute(
        "SELECT * FROM technical_cache WHERE instrument_id = ?", (instrument_id,)
    ).fetchone()
    return _row_to_dict(row)


def upsert_technical_cache(conn, instrument_id, *, rsi, tecnico, edge, n, fundamental_pass, fetched_at=None):
    fetched_at = fetched_at or datetime.now().isoformat()
    conn.execute(
        """INSERT INTO technical_cache (instrument_id, rsi, tecnico, edge, n, fundamental_pass, fetched_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(instrument_id) DO UPDATE SET
               rsi = excluded.rsi, tecnico = excluded.tecnico, edge = excluded.edge,
               n = excluded.n, fundamental_pass = excluded.fundamental_pass, fetched_at = excluded.fetched_at""",
        (instrument_id, rsi, tecnico, edge, n, fundamental_pass, fetched_at),
    )
    conn.commit()


def get_accuracy_cache(conn, instrument_id):
    row = conn.execute(
        "SELECT * FROM accuracy_cache WHERE instrument_id = ?", (instrument_id,)
    ).fetchone()
    return _row_to_dict(row)


def upsert_accuracy_cache(conn, instrument_id, *, result_json, fetched_at=None):
    fetched_at = fetched_at or datetime.now().isoformat()
    conn.execute(
        """INSERT INTO accuracy_cache (instrument_id, result_json, fetched_at)
           VALUES (?, ?, ?)
           ON CONFLICT(instrument_id) DO UPDATE SET
               result_json = excluded.result_json, fetched_at = excluded.fetched_at""",
        (instrument_id, result_json, fetched_at),
    )
    conn.commit()


# --- Historico de cotizaciones de dolar (fx_rate_history) ----------------------

def upsert_fx_rates(conn, casa, rows):
    """rows: iterable de {"fecha": "YYYY-MM-DD", "compra": ..., "venta": ...}."""
    conn.executemany(
        """INSERT INTO fx_rate_history (fecha, casa, compra, venta) VALUES (?, ?, ?, ?)
           ON CONFLICT(fecha, casa) DO UPDATE SET
               compra = excluded.compra, venta = excluded.venta""",
        [(r["fecha"], casa, r.get("compra"), r.get("venta")) for r in rows],
    )
    conn.commit()


def get_fx_rate_history(conn, casa):
    """{fecha: venta} para toda la serie guardada de esa casa, ordenado por fecha."""
    rows = conn.execute(
        "SELECT fecha, venta FROM fx_rate_history WHERE casa = ? ORDER BY fecha", (casa,)
    ).fetchall()
    return {r["fecha"]: r["venta"] for r in rows}


def get_fx_rate_latest_fetched_date(conn, casa):
    row = conn.execute(
        "SELECT MAX(fecha) AS max_fecha FROM fx_rate_history WHERE casa = ?", (casa,)
    ).fetchone()
    return row["max_fecha"] if row else None


def get_fx_rate_earliest_fetched_date(conn, casa):
    """Primera fecha con cobertura real de esta casa (ver
    portfolio/backtest.py::fetch_ticker_history -- CCL arranca en 2013,
    varias acciones locales tienen historial de precio desde 2001; sin este
    corte, build_model_input degrada TODA la serie a ARS por faltarle CCL a
    un puñado de filas viejas, ver conversacion). None si la tabla esta
    vacia (todavia no se corrio ensure_ccl_history ni una vez)."""
    row = conn.execute(
        "SELECT MIN(fecha) AS min_fecha FROM fx_rate_history WHERE casa = ?", (casa,)
    ).fetchone()
    return row["min_fecha"] if row else None


# --- Indice de inflacion -------------------------------------------------------

def upsert_inflation_rates(conn, rows):
    conn.executemany(
        """INSERT INTO inflation_index_history (fecha, valor_mensual_pct) VALUES (?, ?)
           ON CONFLICT(fecha) DO UPDATE SET valor_mensual_pct = excluded.valor_mensual_pct""",
        [(r["fecha"], r["valor_mensual_pct"]) for r in rows],
    )
    conn.commit()


def get_inflation_history(conn):
    """[(fecha, valor_mensual_pct)] ordenado -- toda la serie guardada."""
    rows = conn.execute(
        "SELECT fecha, valor_mensual_pct FROM inflation_index_history ORDER BY fecha"
    ).fetchall()
    return [(r["fecha"], r["valor_mensual_pct"]) for r in rows]


def get_inflation_latest_fetched_date(conn):
    row = conn.execute("SELECT MAX(fecha) AS max_fecha FROM inflation_index_history").fetchone()
    return row["max_fecha"] if row else None


# --- Historico de benchmarks (ej. SPY) -----------------------------------------

def upsert_benchmark_prices(conn, symbol, rows):
    conn.executemany(
        """INSERT INTO benchmark_price_history (symbol, fecha, close) VALUES (?, ?, ?)
           ON CONFLICT(symbol, fecha) DO UPDATE SET close = excluded.close""",
        [(symbol, r["fecha"], r["close"]) for r in rows],
    )
    conn.commit()


def get_benchmark_history(conn, symbol):
    rows = conn.execute(
        "SELECT fecha, close FROM benchmark_price_history WHERE symbol = ? ORDER BY fecha", (symbol,)
    ).fetchall()
    return {r["fecha"]: r["close"] for r in rows}


def get_benchmark_latest_fetched_date(conn, symbol):
    row = conn.execute(
        "SELECT MAX(fecha) AS max_fecha FROM benchmark_price_history WHERE symbol = ?", (symbol,)
    ).fetchone()
    return row["max_fecha"] if row else None


# --- OHLCV de subyacente Wall Street (ver schema.sql, portfolio/features.py) ----

def upsert_underlying_ohlcv(conn, symbol, rows):
    conn.executemany(
        """INSERT INTO underlying_price_history (symbol, fecha, open, high, low, close, volume)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(symbol, fecha) DO UPDATE SET
               open = excluded.open, high = excluded.high, low = excluded.low,
               close = excluded.close, volume = excluded.volume""",
        [(symbol, r["fecha"], r["open"], r["high"], r["low"], r["close"], r["volume"]) for r in rows],
    )
    conn.commit()


def get_underlying_ohlcv_history(conn, symbol):
    """Lista de dicts (fecha/open/high/low/close/volume) ordenada
    cronologicamente -- mismo criterio del resto de este modulo (sin
    pandas aca, ver get_benchmark_history), el caller arma el DataFrame
    (ver portfolio/fetchers.py)."""
    rows = conn.execute(
        "SELECT fecha, open, high, low, close, volume FROM underlying_price_history "
        "WHERE symbol = ? ORDER BY fecha", (symbol,)
    ).fetchall()
    return [dict(r) for r in rows]


def get_underlying_latest_fetched_date(conn, symbol):
    row = conn.execute(
        "SELECT MAX(fecha) AS max_fecha FROM underlying_price_history WHERE symbol = ?", (symbol,)
    ).fetchone()
    return row["max_fecha"] if row else None


# --- Agente NLP de noticias/sentimiento (ver schema.sql, portfolio/nlp_agent.py) --

def insert_news_sentiment(conn, *, ticker, sentiment_score, risk_level, summary=None, event_date=None):
    """Log append-only (ver schema.sql) -- cada corrida del agente inserta
    una fila nueva, nunca pisa la anterior (a diferencia de signal_config)."""
    cur = conn.execute(
        """INSERT INTO news_sentiment_log (ticker, event_date, sentiment_score, risk_level, summary)
           VALUES (?, ?, ?, ?, ?)""",
        (ticker, event_date, sentiment_score, risk_level, summary),
    )
    conn.commit()
    return cur.lastrowid


def get_latest_sentiment(conn, ticker):
    """Ultima fila conocida para este ticker, o None si nunca se corrio el
    agente sobre el -- nunca se inventa un sentimiento sin evidencia."""
    row = conn.execute(
        # id DESC como desempate: created_at (datetime('now')) solo tiene
        # resolucion de SEGUNDOS en SQLite -- 2 inserts del mismo ticker en el
        # mismo segundo (ej. en un test) empatan en created_at, y el id
        # autoincrement SI refleja el orden real de insercion.
        "SELECT * FROM news_sentiment_log WHERE ticker = ? ORDER BY created_at DESC, id DESC LIMIT 1", (ticker,)
    ).fetchone()
    return _row_to_dict(row) if row else None


def list_recent_sentiment(conn, ticker, days=7):
    """Historial de los ultimos `days` dias para este ticker, mas reciente
    primero -- para ver como evoluciono el sentimiento, no solo el ultimo
    valor (ver news_sentiment_log, log append-only)."""
    rows = conn.execute(
        """SELECT * FROM news_sentiment_log
           WHERE ticker = ? AND created_at >= datetime('now', ?)
           ORDER BY created_at DESC, id DESC""",
        (ticker, f"-{int(days)} days"),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


# --- Asignacion objetivo --------------------------------------------------------

def upsert_target_allocation(conn, type_, target_pct):
    conn.execute(
        """INSERT INTO target_allocation (type, target_pct) VALUES (?, ?)
           ON CONFLICT(type) DO UPDATE SET target_pct = excluded.target_pct""",
        (type_, target_pct),
    )
    conn.commit()


def delete_target_allocation(conn, type_):
    conn.execute("DELETE FROM target_allocation WHERE type = ?", (type_,))
    conn.commit()


def list_target_allocation(conn):
    rows = conn.execute("SELECT type, target_pct FROM target_allocation").fetchall()
    return {r["type"]: r["target_pct"] for r in rows}


def get_holdings_with_quotes(conn):
    """Holdings + cotizacion actual + P&L. precio_actual/valor_actual/pnl quedan None si no hay quote aun."""
    holdings = get_holdings(conn)
    for h in holdings:
        quote = get_quote(conn, h["instrument"]["id"])
        if quote:
            valor_actual = h["cantidad"] * quote["price"]
            h["precio_actual"] = quote["price"]
            h["valor_actual"] = valor_actual
            h["pnl"] = valor_actual - h["costo_total"]
            h["pnl_pct"] = (h["pnl"] / h["costo_total"] * 100) if h["costo_total"] else None
            h["cotizado_at"] = quote["fetched_at"]
        else:
            h["precio_actual"] = None
            h["valor_actual"] = None
            h["pnl"] = None
            h["pnl_pct"] = None
            h["cotizado_at"] = None
    return holdings


# --- Historico de precios -----------------------------------------------------

def get_price_history(conn, instrument_id, limit=None):
    query = "SELECT * FROM price_history WHERE instrument_id = ? ORDER BY timestamp"
    if limit:
        query = f"SELECT * FROM ({query} DESC LIMIT {int(limit)}) ORDER BY timestamp"
    rows = conn.execute(query, (instrument_id,)).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_price_history_as_of(conn, instrument_id, as_of_date, limit=None):
    """Igual que get_price_history pero solo con velas hasta as_of_date (inclusive) --
    para que un backtest nunca vea datos que todavia no existian en esa fecha."""
    query = "SELECT * FROM price_history WHERE instrument_id = ? AND timestamp <= ? ORDER BY timestamp"
    if limit:
        query = f"SELECT * FROM ({query} DESC LIMIT {int(limit)}) ORDER BY timestamp"
    rows = conn.execute(query, (instrument_id, f"{as_of_date}T23:59:59")).fetchall()
    return [_row_to_dict(r) for r in rows]


# --- Config de senales ----------------------------------------------------------

def get_signal_config(conn):
    return _row_to_dict(conn.execute("SELECT * FROM signal_config WHERE id = 1").fetchone())


def update_signal_config(conn, **fields):
    if not fields:
        return get_signal_config(conn)
    columns = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(
        f"UPDATE signal_config SET {columns}, updated_at = datetime('now') WHERE id = 1",
        tuple(fields.values()),
    )
    conn.commit()
    return get_signal_config(conn)


# --- Backtest Core/Satelite (ver portfolio/backtest.py, GET /api/backtest/summary) --

def save_dual_engine_backtest_summary(conn, *, run_at, lookback_days, core_metrics,
                                       satellite_metrics, global_metrics=None, capital_allocation=None):
    """Sobreescribe la fila unica (id=1) con el ultimo resultado de
    run_dual_engine_backtest -- ver schema.sql, mismo patron que
    signal_config/app_settings (ultimo valor conocido, no historial)."""
    conn.execute(
        """INSERT INTO dual_engine_backtest_summary
               (id, run_at, lookback_days, core_metrics_json, satellite_metrics_json,
                global_metrics_json, capital_allocation_json)
           VALUES (1, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET
               run_at = excluded.run_at, lookback_days = excluded.lookback_days,
               core_metrics_json = excluded.core_metrics_json,
               satellite_metrics_json = excluded.satellite_metrics_json,
               global_metrics_json = excluded.global_metrics_json,
               capital_allocation_json = excluded.capital_allocation_json""",
        (run_at, lookback_days, json.dumps(core_metrics), json.dumps(satellite_metrics),
         json.dumps(global_metrics) if global_metrics is not None else None,
         json.dumps(capital_allocation) if capital_allocation is not None else None),
    )
    conn.commit()


def get_dual_engine_backtest_summary(conn):
    """None si todavia no corrio nunca portfolio/backtest.py dual (nunca se
    inventa un resultado -- ver GET /api/backtest/summary)."""
    row = conn.execute("SELECT * FROM dual_engine_backtest_summary WHERE id = 1").fetchone()
    if row is None:
        return None
    data = _row_to_dict(row)
    data["core_metrics"] = json.loads(data.pop("core_metrics_json"))
    data["satellite_metrics"] = json.loads(data.pop("satellite_metrics_json"))
    global_json = data.pop("global_metrics_json")
    data["global_metrics"] = json.loads(global_json) if global_json else None
    allocation_json = data.pop("capital_allocation_json")
    data["capital_allocation"] = json.loads(allocation_json) if allocation_json else None
    return data


# --- Cache de "Oportunidades del Dia" (ver schema.sql, GET /api/signals) ------

def save_dual_engine_signals_cache(conn, *, run_at, result):
    """Sobreescribe la fila unica (id=1) -- mismo patron que
    save_dual_engine_backtest_summary arriba."""
    conn.execute(
        """INSERT INTO dual_engine_signals_cache (id, run_at, result_json)
           VALUES (1, ?, ?)
           ON CONFLICT(id) DO UPDATE SET
               run_at = excluded.run_at, result_json = excluded.result_json""",
        (run_at, json.dumps(result)),
    )
    conn.commit()


def get_dual_engine_signals_cache(conn):
    """None si todavia no se calculo nunca (nunca se inventa un resultado)."""
    row = conn.execute("SELECT * FROM dual_engine_signals_cache WHERE id = 1").fetchone()
    if row is None:
        return None
    data = _row_to_dict(row)
    data["result"] = json.loads(data.pop("result_json"))
    return data


# --- Usuarios autorizados de Telegram (ver portfolio/telegram_bot.py) ----------

def list_telegram_allowed_users(conn):
    rows = conn.execute("SELECT * FROM telegram_allowed_users ORDER BY created_at").fetchall()
    return [_row_to_dict(r) for r in rows]


def get_telegram_allowed_user(conn, user_id):
    row = conn.execute("SELECT * FROM telegram_allowed_users WHERE id = ?", (user_id,)).fetchone()
    return _row_to_dict(row) if row else None


def get_telegram_allowed_user_by_chat_id(conn, chat_id):
    row = conn.execute(
        "SELECT * FROM telegram_allowed_users WHERE chat_id = ?", (str(chat_id),)
    ).fetchone()
    return _row_to_dict(row) if row else None


def add_telegram_allowed_user(conn, *, chat_id, label=None, role="viewer"):
    """Lanza ValueError (nunca una excepcion generica de sqlite3) si el rol
    no es valido o si el chat_id ya estaba autorizado -- para que la API
    pueda devolver un 400 con el motivo real."""
    if role not in ("admin", "viewer"):
        raise ValueError(f"role debe ser 'admin' o 'viewer', se recibio {role!r}")
    chat_id = str(chat_id).strip()
    if not chat_id:
        raise ValueError("chat_id no puede estar vacio")
    try:
        cur = conn.execute(
            "INSERT INTO telegram_allowed_users (chat_id, label, role) VALUES (?, ?, ?)",
            (chat_id, label, role),
        )
    except sqlite3.IntegrityError:
        raise ValueError(f"El chat_id {chat_id!r} ya esta autorizado")
    conn.commit()
    return get_telegram_allowed_user(conn, cur.lastrowid)


def delete_telegram_allowed_user(conn, user_id):
    conn.execute("DELETE FROM telegram_allowed_users WHERE id = ?", (user_id,))
    conn.commit()


# --- Preferencias de la app (moneda, variante BNA) -----------------------------

def get_app_settings(conn):
    return _row_to_dict(conn.execute("SELECT * FROM app_settings WHERE id = 1").fetchone())


def update_app_settings(conn, **fields):
    if not fields:
        return get_app_settings(conn)
    columns = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(
        f"UPDATE app_settings SET {columns}, updated_at = datetime('now') WHERE id = 1",
        tuple(fields.values()),
    )
    conn.commit()
    return get_app_settings(conn)


# --- Forecasts (jobs + cache) --------------------------------------------------

def insert_forecast_job(conn, *, instrument_id, symbol, market, asset_type, model_key,
                         lookback, pred_len, temperature, top_p, sample_count,
                         expected_duration_seconds, as_of_date=None):
    cur = conn.execute(
        """INSERT INTO forecasts (instrument_id, symbol, market, asset_type, model_key,
               lookback, pred_len, temperature, top_p, sample_count, expected_duration_seconds,
               as_of_date)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (instrument_id, symbol, market, asset_type, model_key, lookback, pred_len,
         temperature, top_p, sample_count, expected_duration_seconds, as_of_date),
    )
    conn.commit()
    return cur.lastrowid


def mark_forecast_running(conn, forecast_id):
    conn.execute(
        "UPDATE forecasts SET status = 'running', started_at = datetime('now') WHERE id = ?",
        (forecast_id,),
    )
    conn.commit()


def mark_forecast_done(conn, forecast_id, *, result_json, signal, pred_change_pct, uncertainty_pct):
    conn.execute(
        """UPDATE forecasts SET status = 'done', finished_at = datetime('now'),
               result_json = ?, signal = ?, pred_change_pct = ?, uncertainty_pct = ?
           WHERE id = ?""",
        (result_json, signal, pred_change_pct, uncertainty_pct, forecast_id),
    )
    conn.commit()


def mark_forecast_error(conn, forecast_id, error_message):
    conn.execute(
        "UPDATE forecasts SET status = 'error', finished_at = datetime('now'), error_message = ? WHERE id = ?",
        (error_message, forecast_id),
    )
    conn.commit()


def get_forecast(conn, forecast_id):
    return _row_to_dict(conn.execute("SELECT * FROM forecasts WHERE id = ?", (forecast_id,)).fetchone())


def get_latest_forecast(conn, instrument_id=None, symbol=None, market=None):
    """Solo pronosticos 'en vivo' (as_of_date NULL) -- uno de backtest nunca debe
    aparecer como si fuera la senial actual."""
    if instrument_id is not None:
        row = conn.execute(
            """SELECT * FROM forecasts WHERE instrument_id = ? AND as_of_date IS NULL
               ORDER BY created_at DESC LIMIT 1""",
            (instrument_id,),
        ).fetchone()
    else:
        row = conn.execute(
            """SELECT * FROM forecasts WHERE symbol = ? AND market = ? AND instrument_id IS NULL
               AND as_of_date IS NULL ORDER BY created_at DESC LIMIT 1""",
            (symbol, market),
        ).fetchone()
    return _row_to_dict(row)


def list_forecasts_for_instrument(conn, instrument_id):
    rows = conn.execute(
        "SELECT * FROM forecasts WHERE instrument_id = ? AND status = 'done' ORDER BY created_at",
        (instrument_id,),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def list_backtest_anchors(conn, instrument_id, model_key=None):
    """Fechas as_of_date ya cubiertas (en cualquier estado salvo error) para no
    re-encolar backtests duplicados -- filtrado por model_key: un anchor ya
    corrido con un motor viejo (ej. 'kronos-base') no debe bloquear que se
    corra ese mismo anchor con el motor actual (ej. 'chronos-bolt-small'),
    son corridas distintas que no comparten MAPE/acierto direccional. Sin
    model_key (None) devuelve la cobertura cruda de cualquier motor, para
    llamadores que todavia no necesiten distinguir."""
    query = """SELECT DISTINCT as_of_date FROM forecasts
               WHERE instrument_id = ? AND as_of_date IS NOT NULL AND status != 'error'"""
    params = [instrument_id]
    if model_key is not None:
        query += " AND model_key = ?"
        params.append(model_key)
    rows = conn.execute(query, params).fetchall()
    return {r["as_of_date"] for r in rows}


def upsert_portfolio_snapshot(conn, date, valor_total, pnl_total, risk_score):
    conn.execute(
        """INSERT INTO portfolio_snapshots (date, valor_total, pnl_total, risk_score)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(date) DO UPDATE SET
               valor_total = excluded.valor_total,
               pnl_total = excluded.pnl_total,
               risk_score = excluded.risk_score""",
        (date, valor_total, pnl_total, risk_score),
    )
    conn.commit()


def list_portfolio_snapshots(conn, limit=365):
    rows = conn.execute(
        "SELECT * FROM portfolio_snapshots ORDER BY date DESC LIMIT ?", (limit,)
    ).fetchall()
    return [_row_to_dict(r) for r in reversed(rows)]


def list_pending_forecasts(conn):
    """queued/running al reiniciar el proceso son huerfanos (el thread que los corria murio)."""
    rows = conn.execute("SELECT * FROM forecasts WHERE status IN ('queued', 'running')").fetchall()
    return [_row_to_dict(r) for r in rows]


# --- Propuestas (Fase 8) --------------------------------------------------------

def add_proposal(conn, *, origen, symbol, texto, evidencia, tipo_sugerido=None, estado='nueva', cooldown_until=None):
    cur = conn.execute(
        """INSERT INTO proposals (origen, symbol, tipo_sugerido, texto, evidencia, estado, cooldown_until)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (origen, symbol, tipo_sugerido, texto, evidencia, estado, cooldown_until),
    )
    conn.commit()
    return cur.lastrowid


def list_proposals(conn, estado=None):
    if estado:
        rows = conn.execute(
            "SELECT * FROM proposals WHERE estado = ? ORDER BY created_at DESC", (estado,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM proposals ORDER BY created_at DESC").fetchall()
    return [_row_to_dict(r) for r in rows]


def get_proposal(conn, proposal_id):
    row = conn.execute("SELECT * FROM proposals WHERE id = ?", (proposal_id,)).fetchone()
    return _row_to_dict(row)


def update_proposal(conn, proposal_id, **fields):
    if not fields:
        return get_proposal(conn, proposal_id)
    columns = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(
        f"UPDATE proposals SET {columns}, updated_at = datetime('now') WHERE id = ?",
        (*fields.values(), proposal_id),
    )
    conn.commit()
    return get_proposal(conn, proposal_id)


# --- Alertas de precio (SL/TP, Fase 8) -------------------------------------------

def add_price_alert(conn, *, instrument_id, tipo, nivel):
    cur = conn.execute(
        "INSERT INTO price_alerts (instrument_id, tipo, nivel) VALUES (?, ?, ?)",
        (instrument_id, tipo, nivel),
    )
    conn.commit()
    return cur.lastrowid


def list_price_alerts(conn, instrument_id=None, activa=None):
    clauses, params = [], []
    if instrument_id is not None:
        clauses.append("instrument_id = ?")
        params.append(instrument_id)
    if activa is not None:
        clauses.append("activa = ?")
        params.append(1 if activa else 0)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(f"SELECT * FROM price_alerts {where} ORDER BY created_at DESC", params).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_price_alert(conn, alert_id):
    row = conn.execute("SELECT * FROM price_alerts WHERE id = ?", (alert_id,)).fetchone()
    return _row_to_dict(row)


def update_price_alert(conn, alert_id, **fields):
    if not fields:
        return get_price_alert(conn, alert_id)
    columns = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE price_alerts SET {columns} WHERE id = ?", (*fields.values(), alert_id))
    conn.commit()
    return get_price_alert(conn, alert_id)


def delete_price_alert(conn, alert_id):
    conn.execute("DELETE FROM price_alerts WHERE id = ?", (alert_id,))
    conn.commit()


# --- Paper Trading / dinero ficticio (ver portfolio/paper_trading.py) ----------

def list_paper_positions(conn):
    rows = conn.execute("SELECT * FROM paper_positions ORDER BY created_at").fetchall()
    return [_row_to_dict(r) for r in rows]


def get_open_paper_position(conn, symbol, engine):
    row = conn.execute(
        "SELECT * FROM paper_positions WHERE symbol = ? AND engine = ?", (symbol, engine)
    ).fetchone()
    return _row_to_dict(row) if row else None


def insert_paper_position(conn, *, symbol, asset_type, engine, tactic, quantity,
                           entry_price_usd, entry_price_ars, entry_date,
                           take_profit_price_usd, take_profit_price_ars,
                           stop_loss_price_usd, stop_loss_price_ars, max_hold_days):
    """UNIQUE(symbol, engine) en el schema es la ultima linea de defensa
    contra una posicion duplicada -- IntegrityError se traduce a ValueError
    para que el llamador (que ya deberia haber chequeado con
    get_open_paper_position) nunca reciba una excepcion generica de sqlite3."""
    try:
        cur = conn.execute(
            """INSERT INTO paper_positions
                   (symbol, asset_type, engine, tactic, quantity, entry_price_usd, entry_price_ars,
                    entry_date, take_profit_price_usd, take_profit_price_ars,
                    stop_loss_price_usd, stop_loss_price_ars, max_hold_days)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (symbol, asset_type, engine, tactic, quantity, entry_price_usd, entry_price_ars,
             entry_date, take_profit_price_usd, take_profit_price_ars,
             stop_loss_price_usd, stop_loss_price_ars, max_hold_days),
        )
    except sqlite3.IntegrityError:
        raise ValueError(f"Ya hay una posicion de Paper Trading abierta en {symbol!r} ({engine})")
    conn.commit()
    row = conn.execute("SELECT * FROM paper_positions WHERE id = ?", (cur.lastrowid,)).fetchone()
    return _row_to_dict(row)


def delete_paper_position(conn, position_id):
    conn.execute("DELETE FROM paper_positions WHERE id = ?", (position_id,))
    conn.commit()


def sum_open_paper_positions_cost_usd(conn):
    row = conn.execute("SELECT COALESCE(SUM(entry_price_usd * quantity), 0) AS total FROM paper_positions").fetchone()
    return float(row["total"])


def insert_paper_trade(conn, *, position_id, symbol, asset_type, engine, tactic, quantity,
                        entry_price_usd, entry_price_ars, entry_date,
                        exit_price_usd, exit_price_ars, exit_date, exit_reason,
                        pnl_usd, pnl_pct, pnl_ars):
    cur = conn.execute(
        """INSERT INTO paper_trades
               (position_id, symbol, asset_type, engine, tactic, quantity,
                entry_price_usd, entry_price_ars, entry_date,
                exit_price_usd, exit_price_ars, exit_date, exit_reason,
                pnl_usd, pnl_pct, pnl_ars)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (position_id, symbol, asset_type, engine, tactic, quantity,
         entry_price_usd, entry_price_ars, entry_date,
         exit_price_usd, exit_price_ars, exit_date, exit_reason,
         pnl_usd, pnl_pct, pnl_ars),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM paper_trades WHERE id = ?", (cur.lastrowid,)).fetchone()
    return _row_to_dict(row)


def list_paper_trades(conn, limit=None):
    sql = "SELECT * FROM paper_trades ORDER BY exit_date DESC, id DESC"
    params = ()
    if limit is not None:
        sql += " LIMIT ?"
        params = (limit,)
    rows = conn.execute(sql, params).fetchall()
    return [_row_to_dict(r) for r in rows]


def sum_paper_trades_pnl_usd(conn):
    row = conn.execute("SELECT COALESCE(SUM(pnl_usd), 0) AS total FROM paper_trades").fetchone()
    return float(row["total"])


def upsert_paper_snapshot(conn, *, date, equity_usd):
    conn.execute(
        """INSERT INTO paper_trading_snapshots (date, equity_usd) VALUES (?, ?)
           ON CONFLICT(date) DO UPDATE SET equity_usd = excluded.equity_usd""",
        (date, equity_usd),
    )
    conn.commit()


def get_latest_paper_snapshot_before(conn, date):
    row = conn.execute(
        "SELECT * FROM paper_trading_snapshots WHERE date < ? ORDER BY date DESC LIMIT 1", (date,)
    ).fetchone()
    return _row_to_dict(row) if row else None


# --- Calibración de Kronos por instrumento (ver portfolio/calibrate_batch.py) --
#
# Clave (symbol, market) -- no instrument_id: --scope byma calibra simbolos
# sin alta en `instruments` (instrument_id va NULL para esos, igual que
# `forecasts` ya hace para el Explorador).

def upsert_calibration_cache(conn, *, symbol, market, instrument_id, mape, directional_hit_pct,
                              forecasts_evaluated, calibration_pct, calibration_n,
                              anchors_run, anchors_failed):
    conn.execute(
        """INSERT INTO calibration_cache
               (symbol, market, instrument_id, mape, directional_hit_pct, forecasts_evaluated,
                calibration_pct, calibration_n, anchors_run, anchors_failed, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
           ON CONFLICT(symbol, market) DO UPDATE SET
               instrument_id = excluded.instrument_id,
               mape = excluded.mape,
               directional_hit_pct = excluded.directional_hit_pct,
               forecasts_evaluated = excluded.forecasts_evaluated,
               calibration_pct = excluded.calibration_pct,
               calibration_n = excluded.calibration_n,
               anchors_run = excluded.anchors_run,
               anchors_failed = excluded.anchors_failed,
               updated_at = datetime('now')""",
        (symbol, market, instrument_id, mape, directional_hit_pct, forecasts_evaluated,
         calibration_pct, calibration_n, anchors_run, anchors_failed),
    )
    conn.commit()


def list_calibration_cache(conn):
    rows = conn.execute("SELECT * FROM calibration_cache ORDER BY updated_at DESC").fetchall()
    return [_row_to_dict(r) for r in rows]


def get_calibration_cache(conn, symbol, market):
    row = conn.execute(
        "SELECT * FROM calibration_cache WHERE symbol = ? AND market = ?", (symbol, market)
    ).fetchone()
    return _row_to_dict(row) if row else None


# --- Forecasts/anchors por símbolo, sin instrument_id (universo BYMA) ----------

def list_forecasts_by_symbol(conn, symbol, market):
    rows = conn.execute(
        "SELECT * FROM forecasts WHERE instrument_id IS NULL AND symbol = ? AND market = ? AND status = 'done' ORDER BY created_at",
        (symbol, market),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def list_backtest_anchors_by_symbol(conn, symbol, market, model_key=None):
    """Igual que list_backtest_anchors, pero para forecasts SIN instrument_id
    (ver conversación, --scope byma)."""
    query = """SELECT DISTINCT as_of_date FROM forecasts
               WHERE instrument_id IS NULL AND symbol = ? AND market = ?
                 AND as_of_date IS NOT NULL AND status != 'error'"""
    params = [symbol, market]
    if model_key is not None:
        query += " AND model_key = ?"
        params.append(model_key)
    rows = conn.execute(query, params).fetchall()
    return {r["as_of_date"] for r in rows}
