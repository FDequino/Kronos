-- Catalogo de instrumentos de mercado. No hay tickers hardcodeados en codigo:
-- cualquier accion/cedear/bono/ON/moneda se da de alta como una fila nueva aca.
CREATE TABLE IF NOT EXISTS instruments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    name TEXT,
    type TEXT NOT NULL CHECK (type IN ('accion', 'cedear', 'bono', 'on', 'moneda', 'fondo')),
    market TEXT NOT NULL,              -- byma, binance, fx
    currency TEXT NOT NULL DEFAULT 'ARS',
    price_source TEXT NOT NULL CHECK (price_source IN ('data912', 'dolarapi', 'binance', 'manual')),
    price_source_params TEXT,          -- JSON libre: p.ej. {"endpoint": "arg_cedears"} o {"pair": "BTCUSDT"}
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
    default_model_key TEXT NOT NULL DEFAULT 'kronos-base',
    default_lookback INTEGER NOT NULL DEFAULT 400,
    default_pred_len INTEGER NOT NULL DEFAULT 120,
    default_sample_count INTEGER NOT NULL DEFAULT 1,
    default_temperature REAL NOT NULL DEFAULT 1.0,
    default_top_p REAL NOT NULL DEFAULT 0.9,
    benchmark_seconds_per_step REAL NOT NULL DEFAULT 0.41,
    -- Riesgo base por tipo (0-100, mayor = mas riesgoso) y el peso que la
    -- volatilidad historica tiene sobre ese base -- ver portfolio/risk.py.
    risk_weight_volatility REAL NOT NULL DEFAULT 0.6,
    risk_baseline_moneda REAL NOT NULL DEFAULT 10,
    risk_baseline_bono REAL NOT NULL DEFAULT 30,
    risk_baseline_on REAL NOT NULL DEFAULT 40,
    risk_baseline_fondo REAL NOT NULL DEFAULT 50,
    risk_baseline_cedear REAL NOT NULL DEFAULT 55,
    risk_baseline_accion REAL NOT NULL DEFAULT 65,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
INSERT OR IGNORE INTO signal_config (id) VALUES (1);

-- Preferencias de visualizacion (no son parametros de señales): moneda en la
-- que se muestra la cartera y que variante de BNA se usa para convertir.
CREATE TABLE IF NOT EXISTS app_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    display_currency TEXT NOT NULL DEFAULT 'ARS' CHECK (display_currency IN ('ARS', 'USD')),
    bna_rate_variant TEXT NOT NULL DEFAULT 'divisa_venta'
        CHECK (bna_rate_variant IN ('billete_compra', 'billete_venta', 'divisa_compra', 'divisa_venta')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
INSERT OR IGNORE INTO app_settings (id) VALUES (1);

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
