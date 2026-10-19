-- Catalogo de instrumentos de mercado. No hay tickers hardcodeados en codigo:
-- cualquier accion/cedear/bono/ON/moneda se da de alta como una fila nueva aca.
CREATE TABLE IF NOT EXISTS instruments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    name TEXT,
    type TEXT NOT NULL CHECK (type IN ('accion', 'cedear', 'bono', 'on', 'moneda', 'fondo', 'cripto')),
    market TEXT NOT NULL,              -- byma, binance, fx
    currency TEXT NOT NULL DEFAULT 'ARS',
    price_source TEXT NOT NULL CHECK (price_source IN ('data912', 'dolarapi', 'binance', 'manual')),
    price_source_params TEXT,          -- JSON libre: p.ej. {"endpoint": "arg_cedears"} o {"pair": "BTCUSDT"}
    -- Datos de la emision, solo relevantes para bono/on (ver portfolio/fixed_income.py).
    -- Nullable: sin cargar = "sin datos todavia", nunca se asume un valor.
    maturity_date TEXT,
    coupon_rate REAL,               -- % anual; NULL o 0 = cupon cero
    coupon_frequency INTEGER,       -- pagos/año (1,2,4,12); NULL = cupon cero / bullet puro
    rate_type TEXT CHECK (rate_type IN ('fija', 'cer')),  -- NULL = sin clasificar
    -- Fase 11: en que cuenta/broker/custodio vive esta posicion -- NULL = NH
    -- Capital (la unica cuenta que existia antes de esto, nunca se re-etiqueta
    -- lo ya cargado, NULL es simplemente "no se especifico otra cuenta").
    cuenta TEXT,
    -- Precio de EJECUCION (ver conversacion: "que tome los precios de la
    -- plataforma que usa" -- PPI/IOL/etc.) -- NULL = sigue como siempre
    -- (price_source de arriba: data912/dolarapi/binance/manual). Si se
    -- setea, fetchers.py::update_quote ignora price_source para este
    -- instrumento y pide la cotizacion a ESTA cuenta puntual (nunca "PPI"
    -- en abstracto -- si hay 2 cuentas del mismo broker, esto saca la
    -- ambiguedad de cual usar). ON DELETE SET NULL: borrar la cuenta de
    -- broker nunca rompe el instrumento, solo lo vuelve a data912/etc.
    broker_account_id INTEGER REFERENCES broker_accounts(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (symbol, market)
);

-- Movimientos de compra/venta. Las tenencias actuales se derivan sumando esto,
-- nunca se guardan como numero fijo.
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    instrument_id INTEGER NOT NULL REFERENCES instruments(id),
    fecha TEXT NOT NULL,
    tipo TEXT NOT NULL CHECK (tipo IN ('compra', 'venta')),
    cantidad REAL NOT NULL CHECK (cantidad > 0),
    precio REAL NOT NULL CHECK (precio >= 0),
    comision REAL NOT NULL DEFAULT 0,
    notas TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_transactions_instrument ON transactions(instrument_id);

-- Plazos fijos: producto bancario, no de mercado. Interes simple (estandar en
-- plazo fijo tradicional argentino): devengado = monto * tna * dias/365.
CREATE TABLE IF NOT EXISTS plazos_fijos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    banco TEXT NOT NULL,
    tipo TEXT NOT NULL CHECK (tipo IN ('tradicional', 'uva')),
    monto REAL NOT NULL CHECK (monto > 0),
    tna REAL NOT NULL CHECK (tna >= 0),
    fecha_inicio TEXT NOT NULL,
    plazo_dias INTEGER NOT NULL CHECK (plazo_dias > 0),
    fecha_vencimiento TEXT NOT NULL,
    estado TEXT NOT NULL DEFAULT 'activo' CHECK (estado IN ('activo', 'vencido', 'renovado', 'cancelado')),
    notas TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Cauciones: prestamo colateralizado de muy corto plazo via el broker (dias).
-- No es un activo que Kronos pueda pronosticar, pero suma al valor total de la
-- cartera -- interes simple igual que plazos_fijos.
CREATE TABLE IF NOT EXISTS cauciones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    numero_boleto TEXT,
    fecha_apertura TEXT NOT NULL,
    fecha_vencimiento TEXT NOT NULL,
    plazo_dias INTEGER NOT NULL CHECK (plazo_dias > 0),
    monto_contado REAL NOT NULL CHECK (monto_contado > 0),
    monto_futuro REAL NOT NULL,
    tasa REAL NOT NULL,
    interes_bruto REAL NOT NULL,
    moneda TEXT NOT NULL DEFAULT 'ARS',
    -- Gastos del boleto de liquidacion (Fut), SIN el 21% de IVA sobre ellos
    -- (se suma aparte donde haga falta el neto real, ver portfolio/cash.py) --
    -- monto_futuro sigue siendo "bruto" (antes de este costo), consistente
    -- con como ya se usa en el resto de la app (TNA/devengado).
    comision REAL NOT NULL DEFAULT 0,
    notas TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Dividendos, rentas (cupones) y amortizaciones cobradas. Puramente informativo
-- (registro de ingresos), no participa en el calculo de costo/holdings.
CREATE TABLE IF NOT EXISTS rentas_dividendos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    instrument_id INTEGER REFERENCES instruments(id),
    symbol TEXT NOT NULL,
    fecha TEXT NOT NULL,
    tipo TEXT NOT NULL CHECK (tipo IN ('dividendo', 'renta', 'amortizacion')),
    moneda TEXT NOT NULL,
    bruto REAL NOT NULL,
    gastos REAL NOT NULL DEFAULT 0,
    neto REAL NOT NULL,
    notas TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_rentas_dividendos_instrument ON rentas_dividendos(instrument_id);

-- Fase 10: cuenta de caja real -- antes no existia ningun concepto de "efectivo"
-- en el sistema (compras/ventas/cauciones/plazos fijos eran eventos aislados,
-- sin contrapartida de caja). monto va SIEMPRE firmado: positivo = entra
-- plata a la cuenta, negativo = sale. origen distingue flujos EXTERNOS
-- (aporte/retiro -- la plata cruza el limite de la cuenta de inversion, lo
-- unico que cuenta para XIRR) de flujos INTERNOS (compra/venta/dividendo/etc,
-- plata que se mueve DENTRO de la cuenta, no cambia cuanto "pusiste" vos).
CREATE TABLE IF NOT EXISTS cash_movements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha TEXT NOT NULL,
    moneda TEXT NOT NULL CHECK (moneda IN ('ARS', 'USD')),
    monto REAL NOT NULL,
    origen TEXT NOT NULL CHECK (origen IN (
        'aporte', 'retiro', 'aporte_inferido', 'transferencia',
        'compra', 'venta', 'dividendo', 'renta', 'amortizacion',
        'caucion_apertura', 'caucion_vencimiento',
        'plazo_fijo_apertura', 'plazo_fijo_vencimiento',
        'prestamo_otorgado', 'prestamo_cobro', 'gasto_cuenta'
    )),
    tipo_cambio REAL,              -- solo para 'transferencia' ARS<->USD
    referencia_tabla TEXT,         -- ej. 'transactions', 'cauciones' -- de donde salio este movimiento, si aplica
    referencia_id INTEGER,
    notas TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_cash_movements_fecha ON cash_movements(fecha);

-- Fase 10: prestamos a terceros -- un activo mas de la cartera, con su propio
-- plan de pagos. capital/tasa en la moneda nativa del prestamo (ARS/USD);
-- CER-UVA ajusta sobre capital ARS, ver portfolio/loans.py.
CREATE TABLE IF NOT EXISTS loans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    deudor TEXT NOT NULL,
    moneda TEXT NOT NULL CHECK (moneda IN ('ARS', 'USD')),
    capital REAL NOT NULL CHECK (capital > 0),
    fecha_inicio TEXT NOT NULL,
    tasa_anual REAL NOT NULL,           -- TNA, igual convencion que plazos_fijos/cauciones
    ajusta_cer INTEGER NOT NULL DEFAULT 0,
    plan_pago TEXT NOT NULL CHECK (plan_pago IN ('bullet', 'frances', 'aleman', 'libre')),
    plazo_meses INTEGER,                -- NULL si plan_pago='libre' (sin cronograma fijo)
    estado TEXT NOT NULL DEFAULT 'vigente' CHECK (estado IN ('vigente', 'pagado', 'incobrable')),
    -- 'prestado': vos le prestaste a "deudor" (un activo). 'recibido': "deudor"
    -- (en este caso, el prestamista) te prestó a vos (un pasivo) -- mismo
    -- capital/tasa/plan/cronograma, pero resta del patrimonio en vez de sumar
    -- (ver portfolio/routes.py::_compute_currency_exposure).
    direccion TEXT NOT NULL DEFAULT 'prestado' CHECK (direccion IN ('prestado', 'recibido')),
    notas TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Cada cobro real (capital + interes, nunca inventado) -- lo que todavia no
-- se cobro se calcula como devengado a partir de loans + estos pagos, no se
-- guarda un cronograma futuro como si ya hubiera pasado.
CREATE TABLE IF NOT EXISTS loan_payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    loan_id INTEGER NOT NULL REFERENCES loans(id) ON DELETE CASCADE,
    fecha TEXT NOT NULL,
    capital REAL NOT NULL DEFAULT 0,
    interes REAL NOT NULL DEFAULT 0,
    notas TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_loan_payments_loan ON loan_payments(loan_id);

-- Fase 12: movimientos futuros cargados a mano por el usuario (aportes/
-- retiros/gastos planeados) -- nunca inferidos, son la pata "estimada" de la
-- proyeccion patrimonial (ver portfolio/projection.py), separada de la pata
-- "contractual" (prestamos/bonos ya pactados) y la "probabilistica" (Kronos).
CREATE TABLE IF NOT EXISTS planned_movements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha TEXT NOT NULL,
    tipo TEXT NOT NULL CHECK (tipo IN ('aporte', 'retiro', 'gasto', 'otro')),
    monto REAL NOT NULL,
    moneda TEXT NOT NULL CHECK (moneda IN ('ARS', 'USD')),
    notas TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Pagarés/comprobantes escaneados de un préstamo -- el archivo en si vive en
-- disco (portfolio/loan_documents/, ver portfolio/loans.py), esta tabla solo
-- guarda el metadata + el nombre de archivo real para poder servirlo de vuelta.
CREATE TABLE IF NOT EXISTS loan_documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    loan_id INTEGER NOT NULL REFERENCES loans(id) ON DELETE CASCADE,
    nombre_original TEXT NOT NULL,
    nombre_archivo TEXT NOT NULL,        -- nombre real en disco (con prefijo random, evita colisiones)
    content_type TEXT,
    size_bytes INTEGER,
    notas TEXT,
    uploaded_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_loan_documents_loan ON loan_documents(loan_id);

-- Cache de velas historicas (alimenta Kronos y los calculos de rendimiento).
CREATE TABLE IF NOT EXISTS price_history (
    instrument_id INTEGER NOT NULL REFERENCES instruments(id),
    timestamp TEXT NOT NULL,
    open REAL, high REAL, low REAL, close REAL, volume REAL,
    source TEXT,
    PRIMARY KEY (instrument_id, timestamp)
);

-- Ultima cotizacion conocida por instrumento (se pisa en cada fetch).
CREATE TABLE IF NOT EXISTS quotes_latest (
    instrument_id INTEGER PRIMARY KEY REFERENCES instruments(id),
    price REAL NOT NULL,
    bid REAL,
    ask REAL,
    fetched_at TEXT NOT NULL,
    source TEXT
);

-- Config unica de senales + defaults de forecast. Nada hardcodeado en Python:
-- se edita via GET|PUT /api/portfolio/signal-config.
CREATE TABLE IF NOT EXISTS signal_config (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    buy_threshold_pct REAL NOT NULL DEFAULT 5.0,
    sell_threshold_pct REAL NOT NULL DEFAULT -5.0,
    freshness_hours REAL NOT NULL DEFAULT 12.0,
    -- El unico motor real hoy es Chronos-Bolt (ver portfolio/forecasting.py
    -- y services/predictor/) -- este default es solo la etiqueta grabada en
    -- cada forecast nuevo (list_backtest_anchors filtra por ella), no selecciona
    -- motor. Una instalacion nueva arranca ya con el default correcto; una DB
    -- existente que todavia diga 'kronos-base' se corrige via
    -- PUT /api/portfolio/signal-config, no reescribiendo filas a mano.
    default_model_key TEXT NOT NULL DEFAULT 'chronos-bolt-small',
    default_lookback INTEGER NOT NULL DEFAULT 400,
    -- Horizonte corto + muchas muestras en vez de un horizonte largo con una
    -- sola corrida: un pronostico a 120 dias con un unico punto no es confiable
    -- (ver tests/test_kronos_ars_bias.py) y la distribucion de sample_count
    -- muestras (ver portfolio/signals.py) es mas honesta que un % puntual.
    default_pred_len INTEGER NOT NULL DEFAULT 15,
    default_sample_count INTEGER NOT NULL DEFAULT 60,
    default_temperature REAL NOT NULL DEFAULT 1.0,
    default_top_p REAL NOT NULL DEFAULT 0.9,
    -- Recalibrado para sample_count>1 (las muestras se corren en batch: el
    -- costo no escala linealmente con sample_count, ver benchmark real en el
    -- plan de Fase 1 -- pred_len=15/sample_count=60 midio ~68s en MPS).
    benchmark_seconds_per_step REAL NOT NULL DEFAULT 0.076,
    -- Riesgo base por tipo (0-100) y peso de la volatilidad -- YA NO SE USAN
    -- (ver portfolio/risk.py, reescrito para vol/drawdown/CVaR reales en vez
    -- de un score 0-100 blendeado). Quedan sin tocar para no hacer una
    -- migracion destructiva en SQLite por columnas que nadie mas referencia.
    risk_weight_volatility REAL NOT NULL DEFAULT 0.6,
    risk_baseline_moneda REAL NOT NULL DEFAULT 10,
    risk_baseline_bono REAL NOT NULL DEFAULT 30,
    risk_baseline_on REAL NOT NULL DEFAULT 40,
    risk_baseline_fondo REAL NOT NULL DEFAULT 50,
    risk_baseline_cedear REAL NOT NULL DEFAULT 55,
    risk_baseline_accion REAL NOT NULL DEFAULT 65,
    -- Umbrales de volatilidad anualizada (%) para banda bajo/medio/alto por
    -- instrumento, y meses minimos de historial para un CVaR95 confiable --
    -- ver portfolio/risk.py.
    risk_vol_low_pct REAL NOT NULL DEFAULT 25.0,
    risk_vol_high_pct REAL NOT NULL DEFAULT 50.0,
    risk_cvar_min_months INTEGER NOT NULL DEFAULT 12,
    -- Señal técnica (RSI) y filtro fundamental (P/E + margen) -- segunda
    -- opinión independiente de Kronos, ver portfolio/indicators.py y
    -- portfolio/fundamentals.py.
    ta_rsi_oversold REAL NOT NULL DEFAULT 35.0,
    ta_rsi_overbought REAL NOT NULL DEFAULT 70.0,
    fa_max_pe REAL NOT NULL DEFAULT 25.0,
    fa_min_margin_pct REAL NOT NULL DEFAULT 8.0,
    -- Umbrales para marcar una señal de Kronos como "habilitada" (ver
    -- portfolio/accuracy.py::compute_signal_validation) -- sin esto, cualquier
    -- pronostico corrido se pintaba verde/rojo sin medir si ese ticker viene
    -- acertando de verdad.
    min_directional_hit_pct REAL NOT NULL DEFAULT 55.0,
    min_non_overlapping_forecasts INTEGER NOT NULL DEFAULT 30,
    -- Tasa de referencia de caucion (TNA %) para comparar "evolucion vs.
    -- caucion" -- no existe ninguna fuente publica de esto (confirmado), se
    -- carga a mano desde Preferencias. NULL = "sin configurar", nunca se
    -- inventa un valor.
    caucion_reference_tna REAL,
    -- API key OPCIONAL para el Agente NLP de Noticias y Sentimiento (ver
    -- portfolio/nlp_agent.py) -- NULL = "sin configurar", el agente cae a
    -- VADER (analisis de sentimiento local, sin costo, sin red mas alla de
    -- yfinance) sin romper nada. Mismo patron que caucion_reference_tna:
    -- nunca se asume ni se inventa una key, se carga a mano si se quiere la
    -- version con LLM.
    openai_api_key TEXT,
    anthropic_api_key TEXT,
    -- API key OPCIONAL para titulares de noticias via Finnhub (ver
    -- portfolio/nlp_agent.py::_fetch_news_headlines) -- yfinance.Ticker().news
    -- devuelve SIEMPRE una lista vacia en esta cuenta/version (confirmado
    -- repetidas veces), asi que sin esta key el agente de sentimiento nunca
    -- tiene titulares que analizar (ni VADER ni el LLM opcional de arriba
    -- llegan a correr). NULL = sigue intentando yfinance primero (mismo
    -- comportamiento de siempre, nunca una regresion para quien no cargue
    -- la key).
    finnhub_api_key TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
INSERT OR IGNORE INTO signal_config (id) VALUES (1);

-- Agente NLP de Noticias y Sentimiento de Eventos (ver portfolio/nlp_agent.py,
-- conversacion). Un registro por CORRIDA del analisis (no un upsert por
-- ticker): se guarda historial para poder ver como evoluciono el sentimiento
-- de un activo a lo largo del tiempo, no solo el ultimo valor -- a
-- diferencia de signal_config/app_settings (fila unica id=1), esto es un
-- log append-only.
CREATE TABLE IF NOT EXISTS news_sentiment_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    event_date TEXT,                 -- fecha del evento detectado (ej. earnings) -- NULL si no hay evento, solo sentimiento de noticias
    sentiment_score REAL NOT NULL,   -- -1.0 (muy negativo) a +1.0 (muy positivo), 0.0 = neutral/sin datos
    risk_level TEXT NOT NULL CHECK (risk_level IN ('BAJO', 'MEDIO', 'ALTO')),
    summary TEXT,                    -- resumen humano de por que se clasifico asi (titulares vistos, evento detectado, etc.)
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_news_sentiment_log_ticker_created
    ON news_sentiment_log (ticker, created_at DESC);

-- Cache de fundamentales (P/E, margen, etc.) via yfinance -- data912 no expone
-- nada de esto. Mismo espiritu que quotes_latest: nunca pegarle a la fuente
-- externa en cada carga de pantalla.
CREATE TABLE IF NOT EXISTS fundamentals_latest (
    instrument_id INTEGER PRIMARY KEY REFERENCES instruments(id),
    pe_ratio REAL,
    forward_pe REAL,
    profit_margin REAL,
    debt_to_equity REAL,
    dividend_yield REAL,
    market_cap REAL,
    target_mean_price REAL,
    available INTEGER NOT NULL DEFAULT 0,
    not_applicable INTEGER NOT NULL DEFAULT 0,
    reason TEXT,
    fetched_at TEXT NOT NULL
);

-- Motor de Factor Investing Multi-Factor (ver conversacion): cache SEMANAL de
-- los 3 factores (Momentum, Low Vol, Value) y el score compuesto por simbolo
-- -- mismo espiritu que fundamentals_latest/technical_cache (nunca
-- recalcular en cada request). symbol como PK (NO instrument_id): este motor
-- rankea un universo de MERCADO (portfolio/backtest.py::UNIVERSE, CEDEARs),
-- no necesariamente activos que el usuario ya tiene en su catalogo personal
-- (instruments) -- por eso no reusa fundamentals_latest, que SI esta atado
-- a ese catalogo.
CREATE TABLE IF NOT EXISTS factor_scores (
    symbol TEXT PRIMARY KEY,
    momentum_raw REAL,
    low_vol_raw REAL,
    value_raw REAL,
    composite_score REAL,
    rank INTEGER,
    computed_at TEXT NOT NULL
);

-- Cache de la columna "Tecnico" de Posiciones (Fase 9): RSI/señal/edge/n por
-- instrumento, con TTL diario -- mismo espiritu que fundamentals_latest. Antes
-- el Dashboard recalculaba esto (chart+backtest completo) por cada tenencia
-- en vivo, en cada carga de pagina (~18-30s con 36 instrumentos, ~108
-- requests) -- el calculo en si no cambia mas de una vez por dia (depende de
-- price_history, que se actualiza con el cierre del mercado).
CREATE TABLE IF NOT EXISTS technical_cache (
    instrument_id INTEGER PRIMARY KEY REFERENCES instruments(id) ON DELETE CASCADE,
    rsi REAL,
    tecnico TEXT,
    edge REAL,
    n INTEGER,
    fundamental_pass INTEGER,
    fetched_at TEXT NOT NULL
);

-- Cache de accuracy.compute_accuracy por instrumento (ver conversacion --
-- mismo espiritu que technical_cache arriba, TTL diario): perfilado en vivo
-- (py-spy dump contra el proceso real en kronos.deglia.xyz) mostro que
-- dashboard_bundle recalculaba esto desde cero, en CPU (nunca red), por
-- cada tenencia con un forecast -- con 24 tenencias eso solo ya explicaba
-- ~24s de los 38-70s totales. compute_signal_validation (que SI depende de
-- signal_config, a diferencia de compute_accuracy) nunca se cachea -- se
-- recalcula fresco contra el resultado cacheado en cada request, es
-- aritmetica sobre escalares, no el costo real.
CREATE TABLE IF NOT EXISTS accuracy_cache (
    instrument_id INTEGER PRIMARY KEY REFERENCES instruments(id) ON DELETE CASCADE,
    result_json TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);

-- Preferencias de visualizacion (no son parametros de señales): moneda en la
-- que se muestra la cartera y que variante de BNA se usa para convertir.
CREATE TABLE IF NOT EXISTS app_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    display_currency TEXT NOT NULL DEFAULT 'ARS' CHECK (display_currency IN ('ARS', 'USD')),
    bna_rate_variant TEXT NOT NULL DEFAULT 'divisa_venta'
        CHECK (bna_rate_variant IN ('billete_compra', 'billete_venta', 'divisa_compra', 'divisa_venta')),
    -- Fuente de la cotizacion USD usada para convertir la cartera -- "bna" usa
    -- bna_rate_variant de arriba; "mep"/"ccl" van directo a dolarapi (ver
    -- portfolio/fetchers.py::resolve_usd_rate).
    usd_rate_source TEXT NOT NULL DEFAULT 'bna' CHECK (usd_rate_source IN ('bna', 'mep', 'ccl')),
    -- Ajusta los valores ARS mostrados a "pesos de hoy" via el indice de
    -- inflacion acumulada (ver portfolio/history.py::adjust_for_inflation).
    -- Se ignora si display_currency = 'USD'.
    ars_inflation_adjusted INTEGER NOT NULL DEFAULT 0,
    -- Interruptor maestro de ejecucion real de ordenes (ver conversacion,
    -- "diseñemos el interruptor" -- disenado ANTES de que exista ninguna
    -- funcion que ejecute una orden real, a proposito). Arranca en 0 y se
    -- queda en 0 hasta que el usuario lo prenda a mano desde Ajustes >
    -- Integraciones -- ver portfolio/broker_client.py::ensure_auto_trading_enabled,
    -- el punto unico de chequeo que CUALQUIER codigo futuro de ordenes
    -- tiene que llamar primero.
    auto_trading_enabled INTEGER NOT NULL DEFAULT 0,
    -- Tope GENERAL de exposicion para la ejecucion real (ver conversacion:
    -- "tope general, no por orden" -- nunca un limite por operacion
    -- individual, siempre contra el total que el bot tenga abierto).
    -- auto_trading_limit_mode elige si el tope efectivo sale de
    -- auto_trading_limit_pct (% del patrimonio, se ajusta solo) o
    -- auto_trading_limit_ars (monto fijo) -- ver
    -- portfolio/broker_client.py::compute_auto_trading_limit_ars.
    auto_trading_limit_mode TEXT NOT NULL DEFAULT 'pct' CHECK (auto_trading_limit_mode IN ('pct', 'fixed')),
    auto_trading_limit_pct REAL,
    auto_trading_limit_ars REAL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
INSERT OR IGNORE INTO app_settings (id) VALUES (1);

-- Indice de inflacion (IPC mensual) via api.argentinadatos.com, para ajustar
-- valores ARS a moneda constante y como benchmark de "evolucion vs inflacion".
CREATE TABLE IF NOT EXISTS inflation_index_history (
    fecha TEXT PRIMARY KEY,
    valor_mensual_pct REAL NOT NULL
);

-- Historico de cierre diario de un benchmark externo (SPY) via yfinance, para
-- comparar la cartera contra un indice en USD.
CREATE TABLE IF NOT EXISTS benchmark_price_history (
    symbol TEXT NOT NULL,
    fecha TEXT NOT NULL,
    close REAL NOT NULL,
    PRIMARY KEY (symbol, fecha)
);

-- OHLCV completo (no solo cierre, a diferencia de benchmark_price_history
-- arriba) de un ticker de Wall Street via yfinance -- usado por
-- portfolio/features.py para el meta-modelo: para un CEDEAR cuyo simbolo
-- coincide con su activo subyacente real (ver conversacion, verificado para
-- todo UNIVERSE de portfolio/backtest.py: SPY/QQQ/AAPL/MSFT/GOOGL/META/
-- AMZN/NVDA/TSLA/MELI), los indicadores tecnicos (RSI/MACD/ATR/RVOL) se
-- calculan sobre esta serie real en vez de la serie sintetica ARS/CCL
-- (que puede traer ruido propio de BYMA -- spread, liquidez -- ajeno al
-- activo real). Ejecucion/precio de entrada en el broker local NO usa esta
-- tabla, sigue viniendo de data912 (price_history), sin cambios.
CREATE TABLE IF NOT EXISTS underlying_price_history (
    symbol TEXT NOT NULL,
    fecha TEXT NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume REAL,
    PRIMARY KEY (symbol, fecha)
);

-- Cache de forecasts + estado del job en background. instrument_id es NULL para
-- simbolos del Explorador que todavia no estan en la cartera (no ensucia el
-- catalogo instruments con tickers meramente consultados); symbol/market siempre
-- estan para poder buscar "ultimo forecast" en ambos casos.
CREATE TABLE IF NOT EXISTS forecasts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    instrument_id INTEGER REFERENCES instruments(id),
    symbol TEXT NOT NULL,
    market TEXT NOT NULL,
    asset_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'done', 'error')),
    model_key TEXT NOT NULL,
    lookback INTEGER NOT NULL,
    pred_len INTEGER NOT NULL,
    temperature REAL NOT NULL,
    top_p REAL NOT NULL,
    sample_count INTEGER NOT NULL,
    expected_duration_seconds REAL,
    result_json TEXT,
    signal TEXT CHECK (signal IN ('comprar', 'mantener', 'vender')),
    pred_change_pct REAL,
    uncertainty_pct REAL,
    error_message TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    started_at TEXT,
    finished_at TEXT,
    as_of_date TEXT
);

CREATE INDEX IF NOT EXISTS idx_forecasts_instrument ON forecasts(instrument_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_forecasts_symbol ON forecasts(symbol, market, created_at DESC);

-- Serie historica diaria de cotizaciones de dolar (CCL por defecto) via
-- api.argentinadatos.com -- gratis, sin API key, cubre desde 2013. Se usa para
-- convertir series ARS de acciones/CEDEARs a USD antes de alimentarlas a
-- Kronos (ver portfolio/forecasting.py): en ARS, la tendencia estructural
-- (inflacion/devaluacion) sesga la normalizacion por ventana del modelo.
CREATE TABLE IF NOT EXISTS fx_rate_history (
    fecha TEXT NOT NULL,
    casa TEXT NOT NULL,
    compra REAL,
    venta REAL,
    PRIMARY KEY (fecha, casa)
);

-- Asignacion objetivo por clase de activo (accion/cedear/bono/on/fondo/moneda/
-- caucion) -- no es singleton, una fila por clase; una clase sin fila
-- significa "sin objetivo configurado", nunca un 0% inventado. Es una guia,
-- no se fuerza que sumen 100 (ver portfolio/routes.py).
CREATE TABLE IF NOT EXISTS target_allocation (
    type TEXT PRIMARY KEY,
    target_pct REAL NOT NULL CHECK (target_pct >= 0 AND target_pct <= 100)
);

-- Historial real de valor de cartera, un punto por dia (se pisa si hay mas de un
-- refresh el mismo dia). Siempre en ARS, nunca en la moneda de visualizacion
-- elegida, para que el historico no salte si cambia el toggle o la cotizacion BNA.
CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    date TEXT PRIMARY KEY,
    valor_total REAL NOT NULL,
    pnl_total REAL NOT NULL,
    risk_score REAL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Motor de propuestas (Fase 8): acciones sugeridas (SL/TP tocado, vencimiento,
-- rebalanceo, señal validada) que el usuario acepta o descarta a mano -- nunca
-- se ejecuta nada solo. Se re-sincroniza en cada GET /proposals (ver
-- portfolio/proposals.py::sync_proposals): una fila nueva/vista se expira sola
-- si la condicion que la genero ya no aplica; aceptada/descartada quedan como
-- estan (el usuario ya actuo sobre esa, no se le vuelve a insistir).
CREATE TABLE IF NOT EXISTS proposals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    origen TEXT NOT NULL,              -- sl_tp / evento / rebalanceo / senal
    symbol TEXT NOT NULL,              -- ticker real, o una etiqueta de clase para rebalanceo (sin instrumento puntual)
    tipo_sugerido TEXT,                -- compra/venta para precargar el formulario; NULL = solo informativa
    texto TEXT NOT NULL,
    evidencia TEXT NOT NULL,
    estado TEXT NOT NULL DEFAULT 'nueva' CHECK (estado IN ('nueva','vista','aceptada','descartada','expirada')),
    cooldown_until TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Stop-loss / take-profit por instrumento. `nivel` esta en la moneda NATIVA
-- del instrumento (la misma que el grafico de velas) -- NUNCA en la moneda de
-- exhibicion que el usuario eligio en Preferencias (ver portfolio/proposals.py,
-- que compara contra quotes_latest crudo, no contra un valor ya convertido).
CREATE TABLE IF NOT EXISTS price_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    instrument_id INTEGER NOT NULL REFERENCES instruments(id) ON DELETE CASCADE,
    tipo TEXT NOT NULL CHECK (tipo IN ('sl', 'tp')),
    nivel REAL NOT NULL,
    activa INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Ultimo resultado consolidado de portfolio/backtest.py::run_dual_engine_backtest
-- (Motor CORE vs SATELITE vs blend GLOBAL) -- fila UNICA (id=1), se
-- sobreescribe en cada corrida (misma logica que signal_config/app_settings:
-- "ultimo valor conocido", no un historial). Sirve a GET /api/backtest/summary
-- (ver webui/app.py) sin tener que correr el backtest real (varios minutos,
-- llamadas a Chronos-Bolt) en cada request HTTP.
CREATE TABLE IF NOT EXISTS dual_engine_backtest_summary (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    run_at TEXT NOT NULL,
    lookback_days INTEGER NOT NULL,
    core_metrics_json TEXT NOT NULL,
    satellite_metrics_json TEXT NOT NULL,
    global_metrics_json TEXT,
    capital_allocation_json TEXT
);

-- Ultimo resultado de GET /api/signals ("Oportunidades del Dia", ver
-- portfolio/live_signals.py::compute_today_signals) -- mismo patron que
-- dual_engine_backtest_summary arriba: fila UNICA (id=1), se sobreescribe en
-- cada corrida. compute_today_signals puede tardar varios segundos (consulta
-- a Chronos-Bolt para Core si el filtro de regimen pasa) -- antes se
-- recalculaba en CADA entrada a la pestaña Decisiones sin guardar nada (ver
-- conversacion, Fase 7). El boton "Actualizar señales de hoy" sigue
-- forzando una corrida nueva.
CREATE TABLE IF NOT EXISTS dual_engine_signals_cache (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    run_at TEXT NOT NULL,
    result_json TEXT NOT NULL
);

-- Soporte multi-usuario del bot de Telegram (ver conversacion) -- reemplaza
-- el bootstrap de un solo chat_id en app_settings.telegram_chat_id (que
-- sigue existiendo en la tabla, pero deja de ser la fuente de autorizacion
-- real, ver portfolio/telegram_bot.py). role es solo informativo por ahora
-- (no hay comandos restringidos por rol todavia) -- se declara para que la
-- UI pueda etiquetar "quien es quien" sin forzar a implementar permisos
-- diferenciados antes de que haga falta.
CREATE TABLE IF NOT EXISTS telegram_allowed_users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id TEXT NOT NULL UNIQUE,
    label TEXT,
    role TEXT NOT NULL DEFAULT 'viewer' CHECK (role IN ('admin', 'viewer')),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Paper Trading / dinero ficticio (ver conversacion): simula la ejecucion de
-- las señales de ambos motores (portfolio/live_signals.py) sobre un
-- patrimonio VIRTUAL propio, nunca sobre holdings/transactions reales --
-- sirve para validar el edge de Core/Satelite con el tiempo sin arriesgar
-- plata de verdad. paper_positions son UNICAMENTE posiciones abiertas (se
-- borra la fila al cerrarse, nunca queda un status "closed" dando vueltas);
-- paper_trades es el historial INMUTABLE de cada operacion ya cerrada
-- (entrada + salida + pnl), fuente de verdad del reporte de rendimiento
-- (ver portfolio/paper_trading.py). UNIQUE(symbol, engine) evita abrir una
-- segunda posicion ficticia sobre el mismo activo+motor mientras la primera
-- sigue abierta.
CREATE TABLE IF NOT EXISTS paper_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    asset_type TEXT NOT NULL,
    engine TEXT NOT NULL CHECK (engine IN ('core', 'satellite')),
    tactic TEXT,
    quantity REAL NOT NULL,
    entry_price_usd REAL NOT NULL,
    entry_price_ars REAL NOT NULL,
    entry_date TEXT NOT NULL,
    take_profit_price_usd REAL NOT NULL,
    take_profit_price_ars REAL NOT NULL,
    stop_loss_price_usd REAL NOT NULL,
    stop_loss_price_ars REAL NOT NULL,
    max_hold_days INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (symbol, engine)
);

CREATE TABLE IF NOT EXISTS paper_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id INTEGER,
    symbol TEXT NOT NULL,
    asset_type TEXT NOT NULL,
    engine TEXT NOT NULL CHECK (engine IN ('core', 'satellite')),
    tactic TEXT,
    quantity REAL NOT NULL,
    entry_price_usd REAL NOT NULL,
    entry_price_ars REAL NOT NULL,
    entry_date TEXT NOT NULL,
    exit_price_usd REAL NOT NULL,
    exit_price_ars REAL NOT NULL,
    exit_date TEXT NOT NULL,
    exit_reason TEXT NOT NULL CHECK (exit_reason IN ('take_profit', 'stop_loss', 'max_hold', 'manual')),
    pnl_usd REAL NOT NULL,
    pnl_pct REAL NOT NULL,
    pnl_ars REAL NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_paper_trades_exit_date ON paper_trades(exit_date DESC);

-- Equity virtual de cierre de rueda, un valor por dia (se pisa si se
-- recalcula el mismo dia, mismo criterio que portfolio_snapshots) -- unica
-- forma honesta de calcular "resultado del dia" del reporte de cierre sin
-- inventar un baseline (ver portfolio/paper_trading.py::compute_daily_report:
-- sin un snapshot de ayer, resultado_dia queda en None, nunca en 0).
CREATE TABLE IF NOT EXISTS paper_trading_snapshots (
    date TEXT PRIMARY KEY,
    equity_usd REAL NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Snapshot de calibracion/precision de Kronos por instrumento (ver
-- portfolio/calibrate_batch.py) -- MAPE/acierto direccional/cobertura
-- p10-p90 YA se pueden calcular al vuelo en cualquier momento desde
-- forecasts + price_history (ver portfolio/accuracy.py); esta tabla es
-- solo la ULTIMA FOTO persistida de una corrida del batch secuencial
-- (progreso + resultado por activo), para inspeccionar sin recalcular todo.
-- No reemplaza el calculo en vivo de GET /api/portfolio/forecasts-history
-- (routes.py sigue calculando fresco en cada request, sin leer de aca).
--
-- Clave (symbol, market) en vez de instrument_id -- --scope byma (ver
-- conversacion) calibra TODO el universo buscable de BYMA, la enorme
-- mayoria sin alta en `instruments` (instrument_id queda NULL para esos,
-- igual que ya hace `forecasts` para el Explorador). instrument_id se
-- guarda solo quedar, cuando existe, como referencia.
CREATE TABLE IF NOT EXISTS calibration_cache (
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

-- Soporte Multi-Broker (ver conversacion) -- cuentas de bróker EXTERNAS con
-- API propia (PPI, IOL, Balanz, Matrix), para consultar saldo/tenencias en
-- vivo vía portfolio/broker_client.py::MultiBrokerManager. Distinto de
-- portfolio/import_broker_report.py (que importa un EXCEL exportado a mano,
-- sin credenciales, offline) -- esto es una conexión API en vivo, opcional,
-- nunca requerida para que el resto de la app funcione.
--
-- Seguridad declarada (no ocultada): api_key/api_secret se guardan en TEXTO
-- PLANO, mismo criterio YA aceptado en este proyecto para
-- signal_config.openai_api_key/anthropic_api_key (app local de un solo
-- usuario, sin un esquema de cifrado-en-reposo todavía) -- nunca se
-- devuelven sin enmascarar en ninguna respuesta de API (ver
-- broker_client.py::_mask_secret).
CREATE TABLE IF NOT EXISTS broker_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    broker_name TEXT NOT NULL CHECK (broker_name IN ('PPI', 'IOL', 'Balanz', 'Matrix')),
    alias TEXT,
    api_key TEXT,
    api_secret TEXT,
    account_number TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Ledger de ejecucion real (ver conversacion: "empezá a desarrollar la
-- ejecución real" -- portfolio/order_execution.py::place_real_order es el
-- UNICO escritor de esta tabla). Es, a la vez, el historial de lo que el
-- bot realmente mando a un broker Y la fuente de datos que
-- place_real_order usa para calcular "cuanto tiene abierto el bot ahora"
-- contra el tope general de broker_client.py::compute_auto_trading_limit_ars
-- -- sin esta tabla, ese tope no tenia contra que numero compararse.
-- monto_ars: SIEMPRE positivo, independiente de compra/venta (operacion lo
-- distingue) -- "cuanto moviste", no un signo de flujo de caja.
CREATE TABLE IF NOT EXISTS auto_trading_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    instrument_id INTEGER NOT NULL REFERENCES instruments(id),
    broker_account_id INTEGER NOT NULL REFERENCES broker_accounts(id),
    symbol TEXT NOT NULL,
    operacion TEXT NOT NULL CHECK (operacion IN ('compra', 'venta')),
    cantidad REAL NOT NULL CHECK (cantidad > 0),
    precio REAL NOT NULL CHECK (precio >= 0),
    monto_ars REAL NOT NULL CHECK (monto_ars >= 0),
    -- 'confirmada' es la UNICA que cuenta como exposicion real para el tope
    -- -- 'rechazada'/'error' existen para dejar un registro de un intento
    -- que no llego a confirmarse (nunca se borra un intento fallido, para
    -- poder auditar despues por que no se ejecuto).
    estado TEXT NOT NULL CHECK (estado IN ('confirmada', 'rechazada', 'error')),
    external_id TEXT NOT NULL,
    ppi_order_id TEXT,
    error_detalle TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_auto_trading_orders_broker_account ON auto_trading_orders(broker_account_id);
