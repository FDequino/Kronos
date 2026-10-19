import os
from dotenv import load_dotenv
load_dotenv()
import pandas as pd
import numpy as np
import json
import time
from flask import Flask, render_template, request, jsonify, redirect, session, url_for
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix
import sys
import warnings
import datetime
warnings.filterwarnings('ignore')

# Add project root directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from examples.fetch_data import fetch_binance, fetch_yfinance
    LIVE_FETCH_AVAILABLE = True
except ImportError:
    LIVE_FETCH_AVAILABLE = False

from portfolio.routes import bp as portfolio_bp
from portfolio import auth, config as capital_config
from portfolio import db as portfolio_db
from portfolio import live_signals, quote_worker, scanner, telegram_bot
from portfolio.tz import now_ar, today_ar
from nav import NAV_ITEMS
from model import runtime as model_runtime
from model.client import ChronosServiceClient

MODEL_AVAILABLE = model_runtime.MODEL_AVAILABLE
if not MODEL_AVAILABLE:
    print("Warning: Kronos model cannot be imported, will use simulated data for demonstration")

app = Flask(__name__)
CORS(app)
app.register_blueprint(portfolio_bp)

# Caddy es el UNICO frente publico (ver conversacion, "un solo panel de
# login") -- confia en exactamente 1 hop de proxy para X-Forwarded-For/-Proto,
# nunca mas: sin esto, request.remote_addr seria siempre 127.0.0.1 (la IP de
# Caddy, no la del cliente real), inutilizando el rate-limit de /login de
# mas abajo (todo el mundo compartiria el mismo balde). x_proto=1 ademas le
# dice a Flask que la conexion original fue HTTPS aunque Caddy la reenvie
# por HTTP en loopback -- necesario para que SESSION_COOKIE_SECURE no
# rompa el login detras del proxy.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

# secret_key para firmar la cookie de sesion del login (ver conversacion,
# portfolio/auth.py) -- leida de SQLite (generada una vez por instalacion en
# portfolio/db.py::_migrate), nunca hardcodeada aca ni en una env var que
# alguien pueda llevarse clonando el repo.
_auth_conn = portfolio_db.get_connection()
try:
    app.secret_key = portfolio_db.get_app_settings(_auth_conn)["app_secret_key"]
finally:
    _auth_conn.close()

# Única capa de autenticación (ver conversación: se sacó el Basic Auth de
# Caddy a propósito para no pedir 2 logins -- esto es lo que la reemplaza en
# seguridad, no solo en comodidad): cookie nunca por HTTP plano, nunca
# legible por JS, nunca enviada cross-site salvo navegación top-level.
app.config.update(
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    PERMANENT_SESSION_LIFETIME=datetime.timedelta(days=30),
)

# Rutas que tienen que funcionar SIN sesion -- login/setup (obvio, son las
# que inician la sesion) y los assets estaticos (CSS/JS de la propia pagina
# de login no cargarian si tambien quedaran detras del gate).
_AUTH_EXEMPT_ENDPOINTS = {'login', 'setup_password', 'static'}

# Rate limit de /login (ver conversación: con el Basic Auth de Caddy afuera,
# esta es la ÚNICA barrera -- sin esto, nada frena un intento de fuerza
# bruta contra la contraseña). En memoria, por IP real (ver ProxyFix arriba)
# -- alcanza para una instalación de un solo proceso; si el deploy pasara a
# ser multi-worker, esto necesitaría un store compartido (Redis/DB) en vez
# de un dict en memoria.
_LOGIN_MAX_ATTEMPTS = 5
_LOGIN_WINDOW_SECONDS = 300  # 5 minutos
_login_failures = {}  # ip -> [timestamp, ...] de intentos fallidos recientes


def _login_rate_limited(ip):
    now = time.time()
    recent = [t for t in _login_failures.get(ip, []) if now - t < _LOGIN_WINDOW_SECONDS]
    _login_failures[ip] = recent
    return len(recent) >= _LOGIN_MAX_ATTEMPTS


def _register_login_failure(ip):
    _login_failures.setdefault(ip, []).append(time.time())


@app.before_request
def _require_login():
    if request.endpoint in _AUTH_EXEMPT_ENDPOINTS or request.endpoint is None:
        return None
    conn = portfolio_db.get_connection()
    try:
        if not auth.is_configured(conn):
            return redirect(url_for('setup_password'))
    finally:
        conn.close()
    if not session.get('logged_in'):
        return redirect(url_for('login', next=request.path))
    return None


@app.route('/setup', methods=['GET', 'POST'])
def setup_password():
    conn = portfolio_db.get_connection()
    try:
        if auth.is_configured(conn):
            # Ya se configuro desde otra pestaña/sesion mientras esta seguia
            # en /setup -- no tiene sentido dejar pisar la contraseña ya
            # elegida sin pasar por /login primero.
            return redirect(url_for('login'))
        error = None
        if request.method == 'POST':
            password = request.form.get('password', '')
            confirm = request.form.get('password_confirm', '')
            if len(password) < 8:
                error = 'La contraseña tiene que tener al menos 8 caracteres.'
            elif password != confirm:
                error = 'Las dos contraseñas no coinciden.'
            else:
                auth.set_password(conn, password)
                session['logged_in'] = True
                return redirect(url_for('portfolio_page'))
        return render_template('auth.html', mode='setup', error=error)
    finally:
        conn.close()


@app.route('/login', methods=['GET', 'POST'])
def login():
    conn = portfolio_db.get_connection()
    try:
        if not auth.is_configured(conn):
            return redirect(url_for('setup_password'))
        error = None
        if request.method == 'POST':
            ip = request.remote_addr
            if _login_rate_limited(ip):
                error = 'Demasiados intentos fallidos — esperá unos minutos antes de volver a probar.'
            else:
                password = request.form.get('password', '')
                if auth.verify_password(conn, password):
                    _login_failures.pop(ip, None)
                    session.permanent = True
                    session['logged_in'] = True
                    return redirect(request.args.get('next') or url_for('portfolio_page'))
                _register_login_failure(ip)
                error = 'Contraseña incorrecta.'
        return render_template('auth.html', mode='login', error=error)
    finally:
        conn.close()


@app.route('/logout', methods=['POST'])
def logout():
    session.clear()
    return redirect(url_for('login'))


# 30 dias -- herramienta de un solo usuario, no hay valor real en forzar un
# re-login frecuente (ver conversacion); "Cerrar sesión" en el menu de
# usuario sigue disponible para cuando SI haga falta cortarla a mano.
app.permanent_session_lifetime = datetime.timedelta(days=30)

# Cliente unico reusado entre requests (mismo patron que
# portfolio/forecasting.py::_predictor_client) -- no abre conexion hasta el
# primer request a Chronos-Bolt, asi que importar este modulo nunca falla
# aunque el microservicio todavia no este arriba.
_dual_engine_client = ChronosServiceClient()


@app.context_processor
def inject_nav():
    return {'nav_items': NAV_ITEMS}

def load_data_files():
    """Scan data directory and return available data files"""
    data_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')
    data_files = []
    
    if os.path.exists(data_dir):
        for file in os.listdir(data_dir):
            if file.endswith(('.csv', '.feather')):
                file_path = os.path.join(data_dir, file)
                file_size = os.path.getsize(file_path)
                data_files.append({
                    'name': file,
                    'path': file_path,
                    'size': f"{file_size / 1024:.1f} KB" if file_size < 1024*1024 else f"{file_size / (1024*1024):.1f} MB"
                })
    
    return data_files

def load_data_file(file_path):
    """Load data file"""
    try:
        if file_path.endswith('.csv'):
            df = pd.read_csv(file_path)
        elif file_path.endswith('.feather'):
            df = pd.read_feather(file_path)
        else:
            return None, "Unsupported file format"
        
        # Check required columns
        required_cols = ['open', 'high', 'low', 'close']
        if not all(col in df.columns for col in required_cols):
            return None, f"Missing required columns: {required_cols}"
        
        # Process timestamp column
        if 'timestamps' in df.columns:
            df['timestamps'] = pd.to_datetime(df['timestamps'])
        elif 'timestamp' in df.columns:
            df['timestamps'] = pd.to_datetime(df['timestamp'])
        elif 'date' in df.columns:
            # If column name is 'date', rename it to 'timestamps'
            df['timestamps'] = pd.to_datetime(df['date'])
        else:
            # If no timestamp column exists, create one
            df['timestamps'] = pd.date_range(start='2024-01-01', periods=len(df), freq='1H')
        
        # Ensure numeric columns are numeric type
        for col in ['open', 'high', 'low', 'close']:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        
        # Process volume column (optional)
        if 'volume' in df.columns:
            df['volume'] = pd.to_numeric(df['volume'], errors='coerce')
        
        # Process amount column (optional, but not used for prediction)
        if 'amount' in df.columns:
            df['amount'] = pd.to_numeric(df['amount'], errors='coerce')
        
        # Remove rows containing NaN values
        df = df.dropna()
        
        return df, None
        
    except Exception as e:
        return None, f"Failed to load file: {str(e)}"

def save_prediction_results(file_path, prediction_type, prediction_results, actual_data, input_data, prediction_params):
    """Save prediction results to file"""
    try:
        # Create prediction results directory
        results_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'prediction_results')
        os.makedirs(results_dir, exist_ok=True)
        
        # Generate filename
        timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f'prediction_{timestamp}.json'
        filepath = os.path.join(results_dir, filename)
        
        # Prepare data for saving
        save_data = {
            'timestamp': datetime.datetime.now().isoformat(),
            'file_path': file_path,
            'prediction_type': prediction_type,
            'prediction_params': prediction_params,
            'input_data_summary': {
                'rows': len(input_data),
                'columns': list(input_data.columns),
                'price_range': {
                    'open': {'min': float(input_data['open'].min()), 'max': float(input_data['open'].max())},
                    'high': {'min': float(input_data['high'].min()), 'max': float(input_data['high'].max())},
                    'low': {'min': float(input_data['low'].min()), 'max': float(input_data['low'].max())},
                    'close': {'min': float(input_data['close'].min()), 'max': float(input_data['close'].max())}
                },
                'last_values': {
                    'open': float(input_data['open'].iloc[-1]),
                    'high': float(input_data['high'].iloc[-1]),
                    'low': float(input_data['low'].iloc[-1]),
                    'close': float(input_data['close'].iloc[-1])
                }
            },
            'prediction_results': prediction_results,
            'actual_data': actual_data,
            'analysis': {}
        }
        
        # If actual data exists, perform comparison analysis
        if actual_data and len(actual_data) > 0:
            # Calculate continuity analysis
            if len(prediction_results) > 0 and len(actual_data) > 0:
                last_pred = prediction_results[0]  # First prediction point
            first_actual = actual_data[0]      # First actual point
                
            save_data['analysis']['continuity'] = {
                    'last_prediction': {
                        'open': last_pred['open'],
                        'high': last_pred['high'],
                        'low': last_pred['low'],
                        'close': last_pred['close']
                    },
                    'first_actual': {
                        'open': first_actual['open'],
                        'high': first_actual['high'],
                        'low': first_actual['low'],
                        'close': first_actual['close']
                    },
                    'gaps': {
                        'open_gap': abs(last_pred['open'] - first_actual['open']),
                        'high_gap': abs(last_pred['high'] - first_actual['high']),
                        'low_gap': abs(last_pred['low'] - first_actual['low']),
                        'close_gap': abs(last_pred['close'] - first_actual['close'])
                    },
                    'gap_percentages': {
                        'open_gap_pct': (abs(last_pred['open'] - first_actual['open']) / first_actual['open']) * 100,
                        'high_gap_pct': (abs(last_pred['high'] - first_actual['high']) / first_actual['high']) * 100,
                        'low_gap_pct': (abs(last_pred['low'] - first_actual['low']) / first_actual['low']) * 100,
                        'close_gap_pct': (abs(last_pred['close'] - first_actual['close']) / first_actual['close']) * 100
                    }
                }
        
        # Save to file
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(save_data, f, indent=2, ensure_ascii=False)
        
        print(f"Prediction results saved to: {filepath}")
        return filepath
        
    except Exception as e:
        print(f"Failed to save prediction results: {e}")
        return None

def _candles_json(frame, timestamps):
    """OHLC rows -> lightweight-charts candle objects. `time` as epoch seconds
    is the same convention already used by portfolio/forecasting.py::_candles_json
    for every other lightweight-charts series in this app."""
    candles = []
    for i, (_, row) in enumerate(frame.iterrows()):
        if i >= len(timestamps):
            break
        candles.append({
            'time': int(pd.Timestamp(timestamps[i]).timestamp()),
            'open': float(row['open']),
            'high': float(row['high']),
            'low': float(row['low']),
            'close': float(row['close']),
        })
    return candles

def build_prediction_chart_data(df, pred_df, lookback, pred_len, actual_df=None, historical_start_idx=0):
    """Build the 3 OHLC series (historical / prediction / actual) the Predictor
    chart renders with lightweight-charts (ver webui/static/js/charts/predictor-forecast-chart.js).
    Reemplaza al Plotly Figure que armaba esta misma funcion antes: mismos 3
    tramos candlestick, misma logica de timestamps continuos, solo cambia el
    formato de salida (JSON plano en vez de un Figure serializado)."""
    # Use specified historical data start position, not always from the beginning of df
    if historical_start_idx + lookback + pred_len <= len(df):
        # Display lookback historical points + pred_len prediction points starting from specified position
        historical_df = df.iloc[historical_start_idx:historical_start_idx+lookback]
    else:
        # If data is insufficient, adjust to maximum available range
        available_lookback = min(lookback, len(df) - historical_start_idx)
        historical_df = df.iloc[historical_start_idx:historical_start_idx+available_lookback]

    historical_timestamps = list(historical_df['timestamps']) if 'timestamps' in historical_df.columns else list(historical_df.index)
    historical = _candles_json(historical_df, historical_timestamps)

    # Add prediction data (candlestick chart)
    pred_timestamps = None
    prediction = []
    if pred_df is not None and len(pred_df) > 0:
        # Calculate prediction data timestamps - ensure continuity with historical data
        if 'timestamps' in df.columns and len(historical_df) > 0:
            # Start from the last timestamp of historical data, create prediction timestamps with the same time interval
            last_timestamp = historical_df['timestamps'].iloc[-1]
            time_diff = df['timestamps'].iloc[1] - df['timestamps'].iloc[0] if len(df) > 1 else pd.Timedelta(hours=1)

            pred_timestamps = pd.date_range(
                start=last_timestamp + time_diff,
                periods=len(pred_df),
                freq=time_diff
            )
        else:
            # If no timestamps, use index
            pred_timestamps = range(len(historical_df), len(historical_df) + len(pred_df))

        prediction = _candles_json(pred_df, list(pred_timestamps))

    # Add actual data for comparison (if exists)
    actual = []
    if actual_df is not None and len(actual_df) > 0:
        # Actual data should be in the same time period as prediction data
        if pred_timestamps is not None:
            # Actual data should use the same timestamps as prediction data to ensure time alignment
            actual_timestamps = pred_timestamps
        elif 'timestamps' in df.columns:
            # If no prediction timestamps, calculate from the last timestamp of historical data
            if len(historical_df) > 0:
                last_timestamp = historical_df['timestamps'].iloc[-1]
                time_diff = df['timestamps'].iloc[1] - df['timestamps'].iloc[0] if len(df) > 1 else pd.Timedelta(hours=1)
                actual_timestamps = pd.date_range(
                    start=last_timestamp + time_diff,
                    periods=len(actual_df),
                    freq=time_diff
                )
            else:
                actual_timestamps = range(len(historical_df), len(historical_df) + len(actual_df))
        else:
            actual_timestamps = range(len(historical_df), len(historical_df) + len(actual_df))

        actual = _candles_json(actual_df, list(actual_timestamps))

    return {'historical': historical, 'prediction': prediction, 'actual': actual}

@app.route('/')
def index():
    """Home page"""
    return render_template('index.html')

@app.route('/portfolio')
def portfolio_page():
    """Portfolio management page"""
    return render_template('portfolio.html')


@app.route('/api/signals')
def api_signals():
    """GET /api/signals -- señales de HOY de ambos motores (Core y Satelite,
    ver portfolio/live_signals.py), con montos sugeridos segun el patrimonio
    real de la cuenta y la asignacion de capital vigente (portfolio/config.py).
    Core consulta a Chronos-Bolt solo si el filtro de regimen (SPY>EMA50)
    pasa -- puede tardar varios segundos cuando hay candidatos que evaluar.

    Cacheado por dia habil ART (ver schema.sql::dual_engine_signals_cache,
    Fase 7) -- antes se recalculaba entero en CADA entrada a la pestaña
    Decisiones, aunque nada hubiera cambiado desde la ultima vez en el mismo
    dia. ?force=1 (el boton "Actualizar señales de hoy") salta el cache y
    corre de nuevo, mismo criterio que ya usa /api/backtest/summary para no
    inventar un resultado viejo como si fuera nuevo."""
    conn = portfolio_db.get_connection()
    try:
        force = request.args.get('force') == '1'
        if not force:
            cached = portfolio_db.get_dual_engine_signals_cache(conn)
            if cached and cached['run_at'][:10] == today_ar().isoformat():
                return jsonify({**cached['result'], 'run_at': cached['run_at'], 'cached': True})
        account_value = live_signals.total_portfolio_value_ars(conn)
        result = live_signals.compute_today_signals(
            conn, _dual_engine_client, universe=scanner.get_scanner_universe(conn), account_value=account_value,
        )
        run_at = now_ar().isoformat()
        portfolio_db.save_dual_engine_signals_cache(conn, run_at=run_at, result=result)
        return jsonify({**result, 'run_at': run_at, 'cached': False})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()


@app.route('/api/config/allocation', methods=['GET', 'POST'])
def api_config_allocation():
    """GET/POST /api/config/allocation -- consulta o ajusta dinamicamente
    core_pct/satellite_pct (ver portfolio/config.py -- satellite_pct nunca se
    guarda aparte, siempre es 100 - core_pct)."""
    conn = portfolio_db.get_connection()
    try:
        if request.method == 'POST':
            data = request.get_json(silent=True) or {}
            if 'core_pct' not in data:
                return jsonify({'error': "falta 'core_pct' en el body"}), 400
            try:
                allocation = capital_config.set_core_allocation_pct(conn, data['core_pct'])
            except ValueError as e:
                return jsonify({'error': str(e)}), 400
        else:
            allocation = capital_config.get_capital_allocation(conn)
        return jsonify(allocation)
    finally:
        conn.close()


@app.route('/api/backtest/summary')
def api_backtest_summary():
    """GET /api/backtest/summary -- ultimo resultado consolidado de
    `python3 -m portfolio.backtest dual` (Core/Satelite/Global, ver
    schema.sql::dual_engine_backtest_summary). No vuelve a correr el
    backtest en el request (tarda minutos y llama a Chronos-Bolt) -- sirve
    el ultimo snapshot persistido, o available=False si todavia no corrio
    nunca (nunca se inventa un resultado)."""
    conn = portfolio_db.get_connection()
    try:
        summary = portfolio_db.get_dual_engine_backtest_summary(conn)
        if summary is None:
            return jsonify({
                'available': False,
                'message': 'Todavia no se corrio el backtest comparativo (python3 -m portfolio.backtest dual).',
            })
        return jsonify({'available': True, **summary})
    finally:
        conn.close()


@app.route('/api/data-files')
def get_data_files():
    """Get available data file list"""
    data_files = load_data_files()
    return jsonify(data_files)

@app.route('/api/load-data', methods=['POST'])
def load_data():
    """Load data file"""
    try:
        data = request.get_json()
        file_path = data.get('file_path')
        
        if not file_path:
            return jsonify({'error': 'File path cannot be empty'}), 400
        
        df, error = load_data_file(file_path)
        if error:
            return jsonify({'error': error}), 400
        
        # Detect data time frequency
        def detect_timeframe(df):
            if len(df) < 2:
                return "Unknown"
            
            time_diffs = []
            for i in range(1, min(10, len(df))):  # Check first 10 time differences
                diff = df['timestamps'].iloc[i] - df['timestamps'].iloc[i-1]
                time_diffs.append(diff)
            
            if not time_diffs:
                return "Unknown"
            
            # Calculate average time difference
            avg_diff = sum(time_diffs, pd.Timedelta(0)) / len(time_diffs)
            
            # Convert to readable format
            if avg_diff < pd.Timedelta(minutes=1):
                return f"{avg_diff.total_seconds():.0f} seconds"
            elif avg_diff < pd.Timedelta(hours=1):
                return f"{avg_diff.total_seconds() / 60:.0f} minutes"
            elif avg_diff < pd.Timedelta(days=1):
                return f"{avg_diff.total_seconds() / 3600:.0f} hours"
            else:
                return f"{avg_diff.days} days"
        
        # Return data information
        data_info = {
            'rows': len(df),
            'columns': list(df.columns),
            'start_date': df['timestamps'].min().isoformat() if 'timestamps' in df.columns else 'N/A',
            'end_date': df['timestamps'].max().isoformat() if 'timestamps' in df.columns else 'N/A',
            'price_range': {
                'min': float(df[['open', 'high', 'low', 'close']].min().min()),
                'max': float(df[['open', 'high', 'low', 'close']].max().max())
            },
            'prediction_columns': ['open', 'high', 'low', 'close'] + (['volume'] if 'volume' in df.columns else []),
            'timeframe': detect_timeframe(df)
        }
        
        return jsonify({
            'success': True,
            'data_info': data_info,
            'message': f'Successfully loaded data, total {len(df)} rows'
        })
        
    except Exception as e:
        return jsonify({'error': f'Failed to load data: {str(e)}'}), 500

@app.route('/api/fetch-live-data', methods=['POST'])
def fetch_live_data():
    """Fetch fresh K-line data from a live online source and save it as a local CSV"""
    if not LIVE_FETCH_AVAILABLE:
        return jsonify({'error': 'Live fetch dependencies not installed (requests/yfinance)'}), 500

    try:
        data = request.get_json()
        source = data.get('source')

        data_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')
        os.makedirs(data_dir, exist_ok=True)

        if source == 'binance':
            symbol = (data.get('symbol') or '').strip().upper()
            interval = data.get('interval', '1h')
            limit = int(data.get('limit', 1000))
            if not symbol:
                return jsonify({'error': 'symbol is required for binance'}), 400
            df = fetch_binance(symbol, interval, limit)
            filename = f"{symbol}_{interval}.csv"
        elif source == 'yfinance':
            ticker = (data.get('ticker') or '').strip()
            period = data.get('period', '2y')
            interval = data.get('interval', '1d')
            if not ticker:
                return jsonify({'error': 'ticker is required for yfinance'}), 400
            df = fetch_yfinance(ticker, period, interval)
            safe_ticker = ticker.replace('.', '_').replace('-', '_')
            filename = f"{safe_ticker}_{interval}.csv"
        else:
            return jsonify({'error': 'source must be "binance" or "yfinance"'}), 400

        file_path = os.path.join(data_dir, filename)
        df.to_csv(file_path, index=False)

        return jsonify({
            'success': True,
            'file_path': file_path,
            'file_name': filename,
            'rows': len(df),
            'message': f'Fetched {len(df)} rows and saved as {filename}'
        })

    except Exception as e:
        return jsonify({'error': f'Failed to fetch live data: {str(e)}'}), 500

@app.route('/api/predict', methods=['POST'])
def predict():
    """Perform prediction"""
    try:
        data = request.get_json()
        file_path = data.get('file_path')
        lookback = int(data.get('lookback', 400))
        pred_len = int(data.get('pred_len', 120))
        
        # Get prediction quality parameters
        temperature = float(data.get('temperature', 1.0))
        top_p = float(data.get('top_p', 0.9))
        sample_count = int(data.get('sample_count', 1))
        
        if not file_path:
            return jsonify({'error': 'File path cannot be empty'}), 400
        
        # Load data
        df, error = load_data_file(file_path)
        if error:
            return jsonify({'error': error}), 400
        
        if len(df) < lookback:
            return jsonify({'error': f'Insufficient data length, need at least {lookback} rows'}), 400
        
        # Perform prediction
        predictor = model_runtime.get_predictor()
        if MODEL_AVAILABLE and predictor is not None:
            try:
                # Use real Kronos model
                # Only use necessary columns: OHLCV, excluding amount
                required_cols = ['open', 'high', 'low', 'close']
                if 'volume' in df.columns:
                    required_cols.append('volume')
                
                # Process time period selection
                start_date = data.get('start_date')
                
                if start_date:
                    # Custom time period - fix logic: use data within selected window
                    start_dt = pd.to_datetime(start_date)
                    
                    # Find data after start time
                    mask = df['timestamps'] >= start_dt
                    time_range_df = df[mask]
                    
                    # Ensure sufficient data: lookback + pred_len
                    if len(time_range_df) < lookback + pred_len:
                        return jsonify({'error': f'Insufficient data from start time {start_dt.strftime("%Y-%m-%d %H:%M")}, need at least {lookback + pred_len} data points, currently only {len(time_range_df)} available'}), 400
                    
                    # Use first lookback data points within selected window for prediction
                    x_df = time_range_df.iloc[:lookback][required_cols]
                    x_timestamp = time_range_df.iloc[:lookback]['timestamps']
                    
                    # Use last pred_len data points within selected window as actual values
                    y_timestamp = time_range_df.iloc[lookback:lookback+pred_len]['timestamps']
                    
                    # Calculate actual time period length
                    start_timestamp = time_range_df['timestamps'].iloc[0]
                    end_timestamp = time_range_df['timestamps'].iloc[lookback+pred_len-1]
                    time_span = end_timestamp - start_timestamp
                    
                    prediction_type = f"Kronos model prediction (within selected window: first {lookback} data points for prediction, last {pred_len} data points for comparison, time span: {time_span})"
                else:
                    # Use latest data
                    x_df = df.iloc[:lookback][required_cols]
                    x_timestamp = df.iloc[:lookback]['timestamps']
                    y_timestamp = df.iloc[lookback:lookback+pred_len]['timestamps']
                    prediction_type = "Kronos model prediction (latest data)"
                
                # Ensure timestamps are Series format, not DatetimeIndex, to avoid .dt attribute error in Kronos model
                if isinstance(x_timestamp, pd.DatetimeIndex):
                    x_timestamp = pd.Series(x_timestamp, name='timestamps')
                if isinstance(y_timestamp, pd.DatetimeIndex):
                    y_timestamp = pd.Series(y_timestamp, name='timestamps')
                
                pred_df = predictor.predict(
                    df=x_df,
                    x_timestamp=x_timestamp,
                    y_timestamp=y_timestamp,
                    pred_len=pred_len,
                    T=temperature,
                    top_p=top_p,
                    sample_count=sample_count
                )
                
            except Exception as e:
                return jsonify({'error': f'Kronos model prediction failed: {str(e)}'}), 500
        else:
            return jsonify({'error': 'Kronos model not loaded, please load model first'}), 400
        
        # Prepare actual data for comparison (if exists)
        actual_data = []
        actual_df = None
        
        if start_date:  # Custom time period
            # Fix logic: use data within selected window
            # Prediction uses first 400 data points within selected window
            # Actual data should be last 120 data points within selected window
            start_dt = pd.to_datetime(start_date)
            
            # Find data starting from start_date
            mask = df['timestamps'] >= start_dt
            time_range_df = df[mask]
            
            if len(time_range_df) >= lookback + pred_len:
                # Get last 120 data points within selected window as actual values
                actual_df = time_range_df.iloc[lookback:lookback+pred_len]
                
                for i, (_, row) in enumerate(actual_df.iterrows()):
                    actual_data.append({
                        'timestamp': row['timestamps'].isoformat(),
                        'open': float(row['open']),
                        'high': float(row['high']),
                        'low': float(row['low']),
                        'close': float(row['close']),
                        'volume': float(row['volume']) if 'volume' in row else 0,
                        'amount': float(row['amount']) if 'amount' in row else 0
                    })
        else:  # Latest data
            # Prediction uses first 400 data points
            # Actual data should be 120 data points after first 400 data points
            if len(df) >= lookback + pred_len:
                actual_df = df.iloc[lookback:lookback+pred_len]
                for i, (_, row) in enumerate(actual_df.iterrows()):
                    actual_data.append({
                        'timestamp': row['timestamps'].isoformat(),
                        'open': float(row['open']),
                        'high': float(row['high']),
                        'low': float(row['low']),
                        'close': float(row['close']),
                        'volume': float(row['volume']) if 'volume' in row else 0,
                        'amount': float(row['amount']) if 'amount' in row else 0
                    })
        
        # Create chart - pass historical data start position
        if start_date:
            # Custom time period: find starting position of historical data in original df
            start_dt = pd.to_datetime(start_date)
            mask = df['timestamps'] >= start_dt
            historical_start_idx = df[mask].index[0] if len(df[mask]) > 0 else 0
        else:
            # Latest data: start from beginning
            historical_start_idx = 0
        
        chart_data = build_prediction_chart_data(df, pred_df, lookback, pred_len, actual_df, historical_start_idx)
        
        # Prepare prediction result data - fix timestamp calculation logic
        if 'timestamps' in df.columns:
            if start_date:
                # Custom time period: use selected window data to calculate timestamps
                start_dt = pd.to_datetime(start_date)
                mask = df['timestamps'] >= start_dt
                time_range_df = df[mask]
                
                if len(time_range_df) >= lookback:
                    # Calculate prediction timestamps starting from last time point of selected window
                    last_timestamp = time_range_df['timestamps'].iloc[lookback-1]
                    time_diff = df['timestamps'].iloc[1] - df['timestamps'].iloc[0]
                    future_timestamps = pd.date_range(
                        start=last_timestamp + time_diff,
                        periods=pred_len,
                        freq=time_diff
                    )
                else:
                    future_timestamps = []
            else:
                # Latest data: calculate from last time point of entire data file
                last_timestamp = df['timestamps'].iloc[-1]
                time_diff = df['timestamps'].iloc[1] - df['timestamps'].iloc[0]
                future_timestamps = pd.date_range(
                    start=last_timestamp + time_diff,
                    periods=pred_len,
                    freq=time_diff
                )
        else:
            future_timestamps = range(len(df), len(df) + pred_len)
        
        prediction_results = []
        for i, (_, row) in enumerate(pred_df.iterrows()):
            prediction_results.append({
                'timestamp': future_timestamps[i].isoformat() if i < len(future_timestamps) else f"T{i}",
                'open': float(row['open']),
                'high': float(row['high']),
                'low': float(row['low']),
                'close': float(row['close']),
                'volume': float(row['volume']) if 'volume' in row else 0,
                'amount': float(row['amount']) if 'amount' in row else 0
            })
        
        # Save prediction results to file
        try:
            save_prediction_results(
                file_path=file_path,
                prediction_type=prediction_type,
                prediction_results=prediction_results,
                actual_data=actual_data,
                input_data=x_df,
                prediction_params={
                    'lookback': lookback,
                    'pred_len': pred_len,
                    'temperature': temperature,
                    'top_p': top_p,
                    'sample_count': sample_count,
                    'start_date': start_date if start_date else 'latest'
                }
            )
        except Exception as e:
            print(f"Failed to save prediction results: {e}")
        
        return jsonify({
            'success': True,
            'prediction_type': prediction_type,
            'chart': chart_data,
            'prediction_results': prediction_results,
            'actual_data': actual_data,
            'has_comparison': len(actual_data) > 0,
            'message': f'Prediction completed, generated {pred_len} prediction points' + (f', including {len(actual_data)} actual data points for comparison' if len(actual_data) > 0 else '')
        })
        
    except Exception as e:
        return jsonify({'error': f'Prediction failed: {str(e)}'}), 500

@app.route('/api/load-model', methods=['POST'])
def load_model():
    """Load Kronos model"""
    data = request.get_json()
    model_key = data.get('model_key', 'kronos-small')
    device = data.get('device', 'cpu')

    try:
        model_config = model_runtime.load_model(model_key, device)
        return jsonify({
            'success': True,
            'message': f'Model loaded successfully: {model_config["name"]} ({model_config["params"]}) on {device}',
            'model_info': {
                'name': model_config['name'],
                'params': model_config['params'],
                'context_length': model_config['context_length'],
                'description': model_config['description']
            }
        })
    except (RuntimeError, ValueError) as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        return jsonify({'error': f'Model loading failed: {str(e)}'}), 500

@app.route('/api/available-models')
def get_available_models():
    """Get available model list"""
    return jsonify({
        'models': model_runtime.AVAILABLE_MODELS,
        'model_available': MODEL_AVAILABLE
    })

@app.route('/api/model-status')
def get_model_status():
    """Get model status"""
    return jsonify(model_runtime.status())

if __name__ == '__main__':
    print("Starting Kronos Web UI...")
    print(f"Model availability: {MODEL_AVAILABLE}")
    if MODEL_AVAILABLE:
        print("Tip: You can load Kronos model through /api/load-model endpoint")
    else:
        print("Tip: Will use simulated data for demonstration")

    # DEBUG (ver conversación, auditoría de seguridad): antes se dejaba
    # debug=True SIEMPRE, incluso en producción -- el debugger interactivo
    # de Werkzeug (ejecución de código arbitrario si una excepción no
    # atrapada llega a mostrarlo) quedaba activo en la instalación real,
    # mitigado solo porque Caddy es el único frente público. Ahora
    # debug/reloader se prenden SOLO si FLASK_DEBUG=1 en el entorno (nunca
    # seteado en el .env de la VPS) -- en desarrollo local seguís pudiendo
    # pedirlo a mano si lo necesitás.
    DEBUG = os.environ.get('FLASK_DEBUG', '0') == '1'

    # QuoteWorker (Fase 7) arranca aca, no al importar portfolio.quote_worker
    # (a diferencia de forecast_worker.py) -- CON el reloader de Werkzeug
    # (solo si DEBUG=True) este script corre COMPLETO como subproceso hijo
    # con WERKZEUG_RUN_MAIN=true ya seteado desde antes de que Python
    # arranque, y el proceso original (donde esa variable todavia no existe
    # en este punto) queda vivo solo para vigilar cambios de archivo y
    # relanzar el hijo -- nunca sirve requests de verdad. Arrancar el poller
    # con un simple "al importar" (como forecast_worker.py) lo arrancaria en
    # los DOS procesos, duplicando las llamadas a data912. SIN reloader
    # (producción) no hay proceso duplicado -- arranca derecho, una sola vez.
    if not DEBUG or os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
        quote_worker.start()
        telegram_bot.start()

    # Host/puerto configurables por env (default sin cambios: 0.0.0.0:7070,
    # el de siempre en dev local) -- el deploy de la VPS los pisa a
    # 127.0.0.1:8090 via /opt/kronos/app/.env, mismo criterio "nunca
    # expuesto directo" que ya usan el resto de los servicios de ese server
    # (Caddy hace de unico frente publico).
    app.run(
        debug=DEBUG,
        use_reloader=DEBUG,
        host=os.environ.get('KRONOS_HOST', '0.0.0.0'),
        port=int(os.environ.get('KRONOS_PORT', 7070)),
        threaded=True,
    )
