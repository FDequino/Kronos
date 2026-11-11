"""Motor de la estrategia turn-of-month sobre acciones de EEUU (ver
OBJETIVO_MARCO.md, investigación 2026-11-11: efecto real sobre los
constituyentes actuales del S&P 500, Ariel 1987 -- últimos 3 + primeros 3
días hábiles del mes -- promediando el retorno simultáneo de todo el
universo). Validado con DSR=0.9689 (Sharpe 1.79, Max DD -19.2%, anualizado
36.5%) en la MITAD MODERNA del historial (1994 en adelante) -- el historial
completo desde 1962 da un DSR agregado más alto (0.9989) pero está inflado
por un único cuarto anómalo (1978-1994) y NO se usa acá a propósito (ver
OBJETIVO_MARCO.md para el detalle completo de por qué).

Caveats declarados, sin exagerar (ver también OBJETIVO_MARCO.md):
1. Sesgo de supervivencia: usa los constituyentes de HOY del S&P 500
   aplicados hacia atrás en el tiempo, no una lista point-in-time histórica
   (no disponible gratis). Menos severo en 32 años que en 64, pero sigue
   presente.
2. El proyecto NO tiene todavía ningún broker conectado para acciones de
   EEUU (IOL/PPI son BYMA/Argentina, Binance es cripto) -- esto es Paper
   Trading puro, sin camino de ejecución real todavía (ver
   live_strategy_engine.py, que a propósito no tiene un builder real para
   esta estrategia).
3. Es un efecto de mercado agregado (promedio de ~500 tickers simultáneos
   en los días de la ventana), no una selección de acciones individuales.

Descargar el historial completo de yfinance de ~500 tickers tarda varios
minutos y Yahoo rate-limita pedidos muy seguidos (bug real encontrado y
corregido en el screening de swing trading de hoy: sin pausa entre pedidos,
Yahoo reporta tickers activos como "possibly delisted" y devuelve NaN) --
por eso el resultado se cachea en `equity_strategy_cache` con TTL diario
(ver _load_or_refresh_cache), nunca se recalcula en cada llamada."""
from datetime import datetime, timedelta

import pandas as pd
import yfinance as yf

CACHE_TTL_HOURS = 20
FETCH_SLEEP_SECONDS = 0.1
MIN_HISTORY_ROWS = 500
MODERN_START_DATE = "1994-07-01"  # ver docstring del modulo -- excluye a proposito el cuarto anomalo 1962-1994

STRATEGY_NAME = "turn_of_month_sp500"

# Constituyentes ACTUALES del S&P 500 (snapshot 2026-11-10, ver docstring --
# sesgo de supervivencia declarado). '.' reemplazado por '-' para el formato
# de simbolo que usa yfinance (ej. BRK.B -> BRK-B).
SP500_TICKERS = (
    'MMM', 'AOS', 'ABT', 'ABBV', 'ACN', 'ADBE', 'AMD', 'AES', 'AFL', 'A',
    'APD', 'ABNB', 'AKAM', 'ALB', 'ARE', 'ALGN', 'ALLE', 'LNT', 'ALL', 'GOOGL',
    'GOOG', 'MO', 'AMZN', 'AMCR', 'AEE', 'AEP', 'AXP', 'AIG', 'AMT', 'AWK',
    'AMP', 'AME', 'AMGN', 'APH', 'ADI', 'AON', 'APA', 'APO', 'AAPL', 'AMAT',
    'APP', 'APTV', 'ACGL', 'ADM', 'ARES', 'ANET', 'AJG', 'AIZ', 'T', 'ATO',
    'ADSK', 'ADP', 'AZO', 'AVY', 'AXON', 'BKR', 'BALL', 'BAC', 'BAX', 'BDX',
    'BRK-B', 'BBY', 'TECH', 'BIIB', 'BLK', 'BX', 'XYZ', 'BE', 'BNY', 'BA',
    'BKNG', 'BSX', 'BMY', 'AVGO', 'BR', 'BRO', 'BF-B', 'BG', 'BXP', 'CHRW',
    'CDNS', 'CPT', 'COF', 'CAH', 'CCL', 'CARR', 'CVNA', 'CASY', 'CAT', 'CBOE',
    'CBRE', 'CDW', 'COR', 'CNC', 'CNP', 'CF', 'CRL', 'SCHW', 'CHTR', 'CVX',
    'CMG', 'CB', 'CHD', 'CIEN', 'CI', 'CINF', 'CTAS', 'CSCO', 'C', 'CFG',
    'CLX', 'CME', 'CMS', 'KO', 'CTSH', 'COHR', 'COIN', 'CL', 'CMCSA', 'FIX',
    'COP', 'ED', 'STZ', 'CEG', 'COO', 'CPRT', 'GLW', 'CPAY', 'CSGP', 'COST',
    'CRH', 'CRWD', 'CCI', 'CSX', 'CMI', 'CVS', 'DHR', 'DRI', 'DDOG', 'DVA',
    'DECK', 'DE', 'DELL', 'DAL', 'DVN', 'DXCM', 'FANG', 'DLR', 'DG', 'DLTR',
    'D', 'DPZ', 'DASH', 'DOV', 'DOW', 'DHI', 'DTE', 'DUK', 'DD', 'ETN',
    'EBAY', 'ECHO', 'ECL', 'EIX', 'EW', 'ELV', 'EME', 'EMR', 'ETR', 'EOG',
    'EQT', 'EFX', 'EQIX', 'ERIE', 'ESS', 'EL', 'EG', 'EVRG', 'P', 'ES',
    'EXC', 'EXE', 'EXPE', 'EXPD', 'EXR', 'XOM', 'FFIV', 'FDS', 'FICO', 'FAST',
    'FRT', 'FDX', 'FDXF', 'FERG', 'FIS', 'FITB', 'FSLR', 'FE', 'FISV', 'FLEX',
    'F', 'FTNT', 'FTV', 'FOXA', 'FOX', 'BEN', 'FCX', 'GRMN', 'IT', 'GE',
    'GEHC', 'GEV', 'GEN', 'GNRC', 'GD', 'GIS', 'GM', 'GPC', 'GILD', 'GPN',
    'GL', 'GDDY', 'GS', 'HAL', 'HIG', 'HAS', 'HCA', 'DOC', 'HSIC', 'HSY',
    'HPE', 'HLT', 'HD', 'HONA', 'HON', 'HRL', 'HST', 'HWM', 'HPQ', 'HUBB',
    'HUM', 'HBAN', 'HII', 'IBM', 'IEX', 'IDXX', 'ITW', 'ILMN', 'INCY', 'IR',
    'PODD', 'INTC', 'IBKR', 'ICE', 'IFF', 'IP', 'INTU', 'ISRG', 'IVZ', 'INVH',
    'IQV', 'IRM', 'JBHT', 'JBL', 'JKHY', 'J', 'JNJ', 'JCI', 'JPM', 'KDP',
    'KEY', 'KEYS', 'KMB', 'KIM', 'KMI', 'KKR', 'KLAC', 'KHC', 'KR', 'LHX',
    'LH', 'LRCX', 'LVS', 'LDOS', 'LEN', 'LII', 'LLY', 'LIN', 'LYV', 'LMT',
    'L', 'LOW', 'LULU', 'LITE', 'LYB', 'MTB', 'MPC', 'MAR', 'MRSH', 'MLM',
    'MRVL', 'MAS', 'MA', 'MKC', 'MCD', 'MCK', 'MDT', 'MRK', 'META', 'MET',
    'MTD', 'MGM', 'MCHP', 'MU', 'MSFT', 'MAA', 'MRNA', 'MDLZ', 'MPWR', 'MNST',
    'MCO', 'MS', 'MOS', 'MSI', 'MSCI', 'NDAQ', 'NTAP', 'NFLX', 'NEM', 'NWSA',
    'NWS', 'NEE', 'NKE', 'NI', 'NDSN', 'NSC', 'NTRS', 'NOC', 'NCLH', 'NRG',
    'NUE', 'NVDA', 'NVR', 'NXPI', 'ORLY', 'OXY', 'OKTA', 'ODFL', 'OMC', 'ON',
    'OKE', 'ORCL', 'OTIS', 'PCAR', 'PKG', 'PLTR', 'PANW', 'PH', 'PAYX', 'PYPL',
    'PNR', 'PEP', 'PFE', 'PCG', 'PM', 'PSX', 'PNW', 'PNC', 'PPG', 'PPL',
    'PFG', 'PG', 'PGR', 'PLD', 'PRU', 'PTC', 'PEG', 'PSA', 'PHM', 'Q',
    'QCOM', 'PWR', 'DGX', 'RL', 'RJF', 'RDDT', 'O', 'REG', 'REGN', 'RF',
    'RSG', 'RMD', 'RVTY', 'HOOD', 'ROK', 'ROL', 'ROP', 'ROST', 'RCL', 'RTX',
    'SPGI', 'CRM', 'SNDK', 'SBAC', 'SLB', 'STX', 'SRE', 'NOW', 'SHW', 'SPG',
    'SKYD', 'SWKS', 'SJM', 'SW', 'SNA', 'SOLV', 'SO', 'LUV', 'SWK', 'SBUX',
    'STT', 'STLD', 'STE', 'SYK', 'SMCI', 'SYF', 'SNPS', 'SYY', 'TMUS', 'TROW',
    'TTWO', 'TPR', 'TRGP', 'TGT', 'TEL', 'TDY', 'TER', 'TSLA', 'TXN', 'TPL',
    'TXT', 'TMO', 'TJX', 'TKO', 'TSCO', 'TT', 'TDG', 'TRV', 'TRMB', 'TFC',
    'TWLO', 'TYL', 'TSN', 'USB', 'UBER', 'UDR', 'ULTA', 'UNP', 'UAL', 'UPS',
    'URI', 'UNH', 'UHS', 'VLO', 'VEEV', 'VTR', 'VLTO', 'VRSN', 'VRSK', 'VZ',
    'VRTX', 'VRT', 'VTRS', 'VICI', 'V', 'VST', 'VMRK', 'VMC', 'VYLR', 'WRB',
    'GWW', 'WAB', 'WMT', 'DIS', 'WM', 'WAT', 'WEC', 'WFC', 'WELL', 'WST',
    'WDC', 'WY', 'WSM', 'WMB', 'WTW', 'WDAY', 'WYNN', 'XEL', 'XYL', 'YUM',
    'ZBRA', 'ZBH', 'ZTS',
)


def _is_turn_of_month_day(ts):
    dom = ts.day
    return dom >= ts.days_in_month - 2 or dom <= 3


def _fetch_fresh_daily_returns():
    """Descarga el historial completo (yfinance period='max') de todo
    SP500_TICKERS, arma el retorno diario promedio de los días de ventana
    turn-of-month, recortado a MODERN_START_DATE en adelante. Nunca se llama
    directo desde fuera del módulo -- siempre a través de
    _load_or_refresh_cache (TTL), para no volver a pegarle a Yahoo en cada
    llamada al simulador."""
    import time

    all_close = {}
    for symbol in SP500_TICKERS:
        try:
            hist = yf.Ticker(symbol).history(period="max", auto_adjust=True)
            if not hist.empty and len(hist) >= MIN_HISTORY_ROWS:
                all_close[symbol] = hist["Close"]
        except Exception:
            pass
        time.sleep(FETCH_SLEEP_SECONDS)

    if not all_close:
        return pd.Series(dtype=float)

    close_df = pd.DataFrame(all_close)
    ret_df = close_df.pct_change(fill_method=None) * 100
    ret_df.index = pd.to_datetime(ret_df.index).tz_localize(None)
    is_tom = ret_df.index.map(_is_turn_of_month_day)
    tom_ret = ret_df[is_tom].mean(axis=1).dropna()
    return tom_ret[tom_ret.index >= MODERN_START_DATE]


def _load_or_refresh_cache(conn):
    row = conn.execute(
        "SELECT MAX(fetched_at) AS latest FROM equity_strategy_cache WHERE strategy_name = ?",
        (STRATEGY_NAME,),
    ).fetchone()
    latest = row["latest"] if row else None
    stale = latest is None or (
        datetime.now() - datetime.fromisoformat(latest) > timedelta(hours=CACHE_TTL_HOURS)
    )

    if stale:
        tom_ret = _fetch_fresh_daily_returns()
        if len(tom_ret):
            fetched_at = datetime.now().isoformat()
            conn.execute("DELETE FROM equity_strategy_cache WHERE strategy_name = ?", (STRATEGY_NAME,))
            conn.executemany(
                """INSERT INTO equity_strategy_cache (strategy_name, trade_date, daily_return_pct, fetched_at)
                   VALUES (?, ?, ?, ?)""",
                [(STRATEGY_NAME, d.strftime("%Y-%m-%d"), float(v), fetched_at) for d, v in tom_ret.items()],
            )
            conn.commit()
            return tom_ret
        # descarga fallo o vino vacia -- sigue usando el cache viejo si existe, nunca lo borra por un fallo de red

    rows = conn.execute(
        "SELECT trade_date, daily_return_pct FROM equity_strategy_cache WHERE strategy_name = ? ORDER BY trade_date",
        (STRATEGY_NAME,),
    ).fetchall()
    if not rows:
        return pd.Series(dtype=float)
    idx = pd.to_datetime([r["trade_date"] for r in rows])
    return pd.Series([r["daily_return_pct"] for r in rows], index=idx)


def simulate_turn_of_month_engine(conn):
    """Mismo shape de salida que el resto de los motores del proyecto (ver
    portfolio/crypto_carry.py::simulate_funding_rate_carry_engine). idle_weight
    es siempre 0.0 -- fuera de la ventana turn-of-month este sleeve está en
    cash, pero eso ya lo refleja la ausencia de esos días en daily_returns_pct
    (mismo criterio que turn_of_month_broad.py, el script de investigación
    original). "trades" vacío a propósito: es un retorno agregado de mercado
    (promedio simultáneo de ~500 tickers), no posiciones individuales con
    entrada/salida discreta."""
    daily_returns = _load_or_refresh_cache(conn)
    idle_weight = pd.Series(0.0, index=daily_returns.index)
    return {
        "engine": STRATEGY_NAME,
        "daily_returns_pct": daily_returns,
        "idle_weight": idle_weight,
        "trades": [],
    }
