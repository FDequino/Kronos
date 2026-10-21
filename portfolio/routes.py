"""Blueprint con todos los endpoints /api/portfolio/*."""
import json
import os
import queue
import sqlite3
from datetime import datetime

import pandas as pd
from flask import Blueprint, Response, jsonify, request, send_from_directory, stream_with_context

from portfolio import accuracy, benchmarks, broker_client, byma_universe, cash, db, execution_router, fetchers, fixed_income, forecast_worker, forecasting, fundamentals, history, indicators, loans, monitor, nlp_agent, paper_trading, projection, proposals, quote_worker, risk, risk_engine, strategy_registry, trendlines, xirr
from portfolio import config as capital_config
from portfolio.tz import today_ar

bp = Blueprint('portfolio', __name__, url_prefix='/api/portfolio')


# --- Instrumentos --------------------------------------------------------------

@bp.route('/instruments', methods=['GET', 'POST'])
def portfolio_instruments():
    conn = db.get_connection()
    try:
        if request.method == 'POST':
            data = request.get_json()
            try:
                instrument_id = db.add_instrument(
                    conn,
                    symbol=data['symbol'].strip().upper(),
                    type_=data['type'],
                    market=data['market'],
                    price_source=data['price_source'],
                    name=data.get('name'),
                    currency=data.get('currency', 'ARS'),
                    price_source_params=data.get('price_source_params'),
                    maturity_date=data.get('maturity_date'),
                    coupon_rate=data.get('coupon_rate'),
                    coupon_frequency=data.get('coupon_frequency'),
                    rate_type=data.get('rate_type'),
                    cuenta=data.get('cuenta'),
                    broker_account_id=data.get('broker_account_id'),
                )
            except Exception as e:
                return jsonify({'error': str(e)}), 400
            return jsonify({'success': True, 'id': instrument_id})

        return jsonify(db.list_instruments(conn))
    finally:
        conn.close()


@bp.route('/instruments/<int:instrument_id>', methods=['PUT', 'DELETE'])
def instrument_detail_route(instrument_id):
    conn = db.get_connection()
    try:
        instrument = db.get_instrument(conn, instrument_id)
        if not instrument:
            return jsonify({'error': 'Instrumento no encontrado'}), 404

        if request.method == 'DELETE':
            try:
                db.delete_instrument(conn, instrument_id)
            except sqlite3.IntegrityError:
                return jsonify({'error': 'No se puede eliminar: tiene transacciones u otros datos asociados'}), 400
            return jsonify({'success': True})

        data = request.get_json()
        fields = {}
        for key in ('symbol', 'name', 'type', 'market', 'currency', 'price_source', 'price_source_params',
                    'maturity_date', 'coupon_rate', 'coupon_frequency', 'rate_type', 'cuenta', 'broker_account_id'):
            if key in data:
                fields[key] = data[key].strip().upper() if key == 'symbol' else data[key]
        try:
            updated = db.update_instrument(conn, instrument_id, **fields)
        except Exception as e:
            return jsonify({'error': str(e)}), 400
        return jsonify(updated)
    finally:
        conn.close()


@bp.route('/instruments/<int:instrument_id>/quote', methods=['PUT'])
def set_manual_quote(instrument_id):
    data = request.get_json()
    conn = db.get_connection()
    try:
        instrument = db.get_instrument(conn, instrument_id)
        if not instrument:
            return jsonify({'error': 'Instrumento no encontrado'}), 404
        try:
            price = float(data['price'])
        except (KeyError, TypeError, ValueError):
            return jsonify({'error': 'price invalido'}), 400
        db.upsert_quote(conn, instrument_id, price, source='manual')
        return jsonify({'success': True})
    finally:
        conn.close()


# --- Transacciones --------------------------------------------------------------

@bp.route('/transactions', methods=['GET', 'POST'])
def portfolio_transactions():
    conn = db.get_connection()
    try:
        if request.method == 'POST':
            data = request.get_json()
            try:
                tx_id = db.add_transaction(
                    conn,
                    instrument_id=int(data['instrument_id']),
                    fecha=data['fecha'],
                    tipo=data['tipo'],
                    cantidad=float(data['cantidad']),
                    precio=float(data['precio']),
                    comision=float(data.get('comision', 0)),
                    notas=data.get('notas'),
                    # Fase 5 del plan de unificacion (ver conversacion):
                    # trazabilidad propuesta->transaccion, solo si vino de
                    # "Aceptar" una propuesta y el usuario no cambio de
                    # instrumento despues (ver transactions.js).
                    proposal_id=data.get('proposal_id'),
                )
            except Exception as e:
                return jsonify({'error': str(e)}), 400
            history.invalidate_reconstruction_cache()
            return jsonify({'success': True, 'id': tx_id})

        instrument_id = request.args.get('instrument_id', type=int)
        return jsonify(db.list_transactions(conn, instrument_id))
    finally:
        conn.close()


@bp.route('/transactions/<int:transaction_id>', methods=['PUT', 'DELETE'])
def transaction_detail_route(transaction_id):
    conn = db.get_connection()
    try:
        tx = db.get_transaction(conn, transaction_id)
        if not tx:
            return jsonify({'error': 'Transacción no encontrada'}), 404

        if request.method == 'DELETE':
            db.delete_transaction(conn, transaction_id)
            history.invalidate_reconstruction_cache()
            return jsonify({'success': True})

        data = request.get_json()
        fields = {}
        if 'instrument_id' in data:
            fields['instrument_id'] = int(data['instrument_id'])
        for key in ('fecha', 'tipo', 'notas'):
            if key in data:
                fields[key] = data[key]
        for key in ('cantidad', 'precio', 'comision'):
            if key in data:
                fields[key] = float(data[key])
        try:
            updated = db.update_transaction(conn, transaction_id, **fields)
        except Exception as e:
            return jsonify({'error': str(e)}), 400
        history.invalidate_reconstruction_cache()
        return jsonify(updated)
    finally:
        conn.close()


# --- Plazos fijos -----------------------------------------------------------

@bp.route('/plazos-fijos', methods=['GET', 'POST'])
def portfolio_plazos_fijos():
    conn = db.get_connection()
    try:
        if request.method == 'POST':
            data = request.get_json()
            try:
                pf_id = db.add_plazo_fijo(
                    conn,
                    banco=data['banco'],
                    tipo=data['tipo'],
                    monto=float(data['monto']),
                    tna=float(data['tna']),
                    fecha_inicio=data['fecha_inicio'],
                    plazo_dias=int(data['plazo_dias']),
                    notas=data.get('notas'),
                )
            except Exception as e:
                return jsonify({'error': str(e)}), 400
            return jsonify({'success': True, 'id': pf_id})

        return jsonify(db.get_plazos_fijos_resumen(conn))
    finally:
        conn.close()


@bp.route('/plazos-fijos/<int:plazo_fijo_id>', methods=['PUT', 'DELETE'])
def plazo_fijo_detail_route(plazo_fijo_id):
    conn = db.get_connection()
    try:
        pf = db.get_plazo_fijo(conn, plazo_fijo_id)
        if not pf:
            return jsonify({'error': 'Plazo fijo no encontrado'}), 404

        if request.method == 'DELETE':
            db.delete_plazo_fijo(conn, plazo_fijo_id)
            return jsonify({'success': True})

        data = request.get_json()
        fields = {}
        for key in ('banco', 'tipo', 'fecha_inicio', 'estado', 'notas'):
            if key in data:
                fields[key] = data[key]
        for key in ('monto', 'tna'):
            if key in data:
                fields[key] = float(data[key])
        if 'plazo_dias' in data:
            fields['plazo_dias'] = int(data['plazo_dias'])
        try:
            updated = db.update_plazo_fijo(conn, plazo_fijo_id, **fields)
        except Exception as e:
            return jsonify({'error': str(e)}), 400
        return jsonify(updated)
    finally:
        conn.close()


# --- Holdings / cotizaciones --------------------------------------------------

MONEY_FIELDS = ("costo_promedio", "costo_total", "precio_actual", "valor_actual", "pnl")


def _apply_display_currency(holdings, settings):
    """Convierte los campos monetarios a settings.display_currency usando la
    cotizacion elegida (BNA/MEP/CCL, ver resolve_usd_rate). pnl_pct no se
    toca: es un ratio, no cambia de escala."""
    target = settings["display_currency"]
    try:
        rate = fetchers.resolve_usd_rate(settings)
    except Exception:
        rate = None  # la fuente elegida no respondio -- mostramos en la moneda nativa sin convertir

    for h in holdings:
        native = h["instrument"]["currency"]
        if rate is None or native == target:
            continue
        if native == "ARS" and target == "USD":
            factor = 1 / rate
        elif native == "USD" and target == "ARS":
            factor = rate
        else:
            continue
        for field in MONEY_FIELDS:
            if h.get(field) is not None:
                h[field] *= factor
        h["moneda_mostrada"] = target
    return holdings


def _attach_risk(conn, holdings, config):
    for h in holdings:
        rows = db.get_price_history(conn, h["instrument"]["id"], limit=61)
        closes = [r["close"] for r in sorted(rows, key=lambda r: r["timestamp"])]
        h["risk"] = risk.compute_instrument_risk(closes, config)
        if h["risk"] is not None:
            h["risk"]["explanation"] = risk.explain_instrument_risk(h["instrument"]["symbol"], h["risk"])
    return holdings


@bp.route('/holdings')
def portfolio_holdings():
    conn = db.get_connection()
    try:
        holdings = db.get_holdings_with_quotes(conn)
        settings = db.get_app_settings(conn)
        holdings = _apply_display_currency(holdings, settings)
        holdings = _attach_risk(conn, holdings, db.get_signal_config(conn))
        return jsonify(holdings)
    finally:
        conn.close()


@bp.route('/portfolio-risk')
def portfolio_risk():
    conn = db.get_connection()
    try:
        holdings = db.get_holdings_with_quotes(conn)
        holdings = [h for h in holdings if h["valor_actual"] is not None]
        # Solo cauciones VIGENTES: una vencida ya no es capital en caucion --
        # esa plata se convirtio en otra cosa (retiro, reinversion, una nueva
        # caucion) o, si no, el broker real tampoco la cuenta como posicion
        # (confirmado contra el export real de la cuenta, ver datos_broker/).
        cauciones = [c for c in db.get_cauciones_resumen(conn) if not c['vencido']]
        config = db.get_signal_config(conn)
        portfolio = risk.compute_portfolio_risk_metrics(conn, holdings, cauciones, config)
        portfolio["explanation"] = risk.explain_portfolio_risk(portfolio)
        return jsonify(portfolio)
    finally:
        conn.close()


def _record_portfolio_snapshot(conn):
    """Un punto de historial real por dia, siempre en ARS -- ver schema.sql.
    No calcula riesgo: portfolio_snapshots.risk_score queda sin usar (la tabla
    en si no la lee ningun frontend, confirmado) y recalcular riesgo en cada
    refresh de cotizaciones seria trabajo de mas sin beneficio."""
    settings = db.get_app_settings(conn)
    ars_settings = {**settings, 'display_currency': 'ARS'}
    holdings = _apply_display_currency(db.get_holdings_with_quotes(conn), ars_settings)
    cauciones = [c for c in db.get_cauciones_resumen(conn) if not c['vencido']]  # ver nota en portfolio_risk()

    valor_total = sum(h['valor_actual'] or 0 for h in holdings) + sum(c['valor_actual'] for c in cauciones)
    pnl_total = sum(h['pnl'] or 0 for h in holdings)

    db.upsert_portfolio_snapshot(
        conn, date=today_ar().isoformat(),
        valor_total=valor_total, pnl_total=pnl_total,
        risk_score=None,
    )


@bp.route('/stream')
def stream():
    """SSE: un evento `quote` por cada cotizacion que QuoteWorker actualiza
    (Fase 7) -- el browser se suscribe con EventSource nativo, sin libreria
    nueva. La conexion queda abierta mientras dure la pestaña; `unsubscribe`
    en el finally saca la cola cuando el cliente se desconecta (generator
    cerrado por Werkzeug), para no acumular colas muertas en _subscribers.

    El primer `yield` es inmediato (comentario SSE, ignorado por EventSource)
    para que el servidor de desarrollo mande los headers ya -- sin esto, con
    el mercado cerrado (tick cada 1h) la conexion quedaria sin mandar ni un
    byte durante esa hora entera, bloqueada en q.get(). Despues, un heartbeat
    cada 25s (mismo mecanismo) evita que algun proxy/balanceador intermedio
    corte la conexion por inactividad -- practica estandar de SSE, no
    especifica de esta app."""
    def gen():
        q = quote_worker.subscribe()
        yield ": connected\n\n"
        try:
            while True:
                try:
                    event = q.get(timeout=25)
                    yield f"event: quote\ndata: {json.dumps(event)}\n\n"
                except queue.Empty:
                    yield ": keep-alive\n\n"
        finally:
            quote_worker.unsubscribe(q)

    return Response(stream_with_context(gen()), mimetype='text/event-stream')


@bp.route('/refresh-quotes', methods=['POST'])
def portfolio_refresh_quotes():
    conn = db.get_connection()
    try:
        errors = fetchers.update_all_quotes(conn)
        _record_portfolio_snapshot(conn)
        return jsonify({'success': True, 'errors': errors})
    finally:
        conn.close()


@bp.route('/history')
def portfolio_history():
    conn = db.get_connection()
    try:
        return jsonify(db.list_portfolio_snapshots(conn))
    finally:
        conn.close()


@bp.route('/history/reconstructed')
def portfolio_history_reconstructed():
    conn = db.get_connection()
    try:
        return jsonify(history.reconstruct_portfolio_value(conn))
    finally:
        conn.close()


@bp.route('/target-allocation', methods=['GET', 'PUT'])
def target_allocation():
    """Asignacion objetivo por clase de activo -- una guia, no se fuerza que
    sume 100. PUT acepta {"targets": {type: pct_o_null}}; null borra el
    objetivo de esa clase."""
    conn = db.get_connection()
    try:
        if request.method == 'PUT':
            targets = (request.get_json() or {}).get('targets', {})
            for type_, pct in targets.items():
                if pct is None:
                    db.delete_target_allocation(conn, type_)
                else:
                    db.upsert_target_allocation(conn, type_, pct)
        return jsonify(db.list_target_allocation(conn))
    finally:
        conn.close()


@bp.route('/benchmarks')
def portfolio_benchmarks():
    """TWR de la cartera (excluye aportes/retiros) + comparación contra
    caución/inflación/SPY -- ver portfolio/benchmarks.py."""
    conn = db.get_connection()
    try:
        return jsonify(benchmarks.compute_benchmarks(conn))
    finally:
        conn.close()


@bp.route('/bna-rate-current')
def bna_rate_current():
    conn = db.get_connection()
    try:
        settings = db.get_app_settings(conn)
        rates = fetchers.fetch_bna_rates()
        return jsonify({'variant': settings['bna_rate_variant'], 'rate': rates[settings['bna_rate_variant']], 'rates': rates})
    except Exception as e:
        return jsonify({'error': str(e)}), 502
    finally:
        conn.close()


@bp.route('/usd-mep-rate')
def usd_mep_rate():
    """Cotizacion USD MEP puntual -- usada solo por el encabezado del Resumen
    (Fase 4), que siempre muestra el patrimonio en MEP sin importar la fuente
    USD elegida en Preferencias para el resto de la cartera."""
    try:
        return jsonify({'rate': fetchers.fetch_dolarapi_casa('bolsa')['venta']})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


# --- Preferencias de la app (moneda, BNA) --------------------------------------

@bp.route('/bna-rates')
def bna_rates():
    try:
        return jsonify(fetchers.fetch_bna_rates())
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@bp.route('/settings', methods=['GET', 'PUT'])
def app_settings():
    conn = db.get_connection()
    try:
        if request.method == 'PUT':
            settings = db.update_app_settings(conn, **request.get_json())
        else:
            settings = db.get_app_settings(conn)
        return jsonify(settings)
    finally:
        conn.close()


@bp.route('/settings/telegram/users', methods=['GET', 'POST', 'DELETE'])
def telegram_allowed_users():
    """Soporte multi-usuario del bot de Telegram (ver conversacion) --
    GET lista, POST agrega (body: chat_id requerido, label/role opcionales),
    DELETE revoca (?id=<id>, mismo path para los 3 metodos, pedido explicito)."""
    conn = db.get_connection()
    try:
        if request.method == 'POST':
            data = request.get_json(silent=True) or {}
            chat_id = data.get('chat_id')
            if not chat_id:
                return jsonify({'error': "falta 'chat_id'"}), 400
            try:
                user = db.add_telegram_allowed_user(
                    conn, chat_id=chat_id, label=data.get('label'), role=data.get('role', 'viewer'),
                )
            except ValueError as e:
                return jsonify({'error': str(e)}), 400
            return jsonify(user)
        if request.method == 'DELETE':
            user_id = request.args.get('id')
            if not user_id:
                return jsonify({'error': "falta 'id'"}), 400
            try:
                user_id = int(user_id)
            except ValueError:
                return jsonify({'error': "'id' debe ser numerico"}), 400
            db.delete_telegram_allowed_user(conn, user_id)
            return jsonify({'success': True})
        return jsonify(db.list_telegram_allowed_users(conn))
    finally:
        conn.close()


# --- Cauciones ------------------------------------------------------------------

@bp.route('/cauciones', methods=['GET', 'POST'])
def portfolio_cauciones():
    conn = db.get_connection()
    try:
        if request.method == 'POST':
            data = request.get_json()
            try:
                caucion_id = db.add_caucion(
                    conn,
                    fecha_apertura=data['fecha_apertura'],
                    fecha_vencimiento=data['fecha_vencimiento'],
                    plazo_dias=int(data['plazo_dias']),
                    monto_contado=float(data['monto_contado']),
                    monto_futuro=float(data['monto_futuro']),
                    tasa=float(data['tasa']),
                    interes_bruto=float(data['interes_bruto']),
                    moneda=data.get('moneda', 'ARS'),
                    numero_boleto=data.get('numero_boleto'),
                    notas=data.get('notas'),
                )
            except Exception as e:
                return jsonify({'error': str(e)}), 400
            return jsonify({'success': True, 'id': caucion_id})

        return jsonify(db.get_cauciones_resumen(conn))
    finally:
        conn.close()


@bp.route('/cauciones/<int:caucion_id>', methods=['PUT', 'DELETE'])
def caucion_detail_route(caucion_id):
    conn = db.get_connection()
    try:
        caucion = db.get_caucion(conn, caucion_id)
        if not caucion:
            return jsonify({'error': 'Caución no encontrada'}), 404

        if request.method == 'DELETE':
            db.delete_caucion(conn, caucion_id)
            return jsonify({'success': True})

        data = request.get_json()
        fields = {}
        for key in ('numero_boleto', 'fecha_apertura', 'fecha_vencimiento', 'moneda', 'notas'):
            if key in data:
                fields[key] = data[key]
        for key in ('monto_contado', 'monto_futuro', 'tasa', 'interes_bruto'):
            if key in data:
                fields[key] = float(data[key])
        if 'plazo_dias' in data:
            fields['plazo_dias'] = int(data['plazo_dias'])
        try:
            updated = db.update_caucion(conn, caucion_id, **fields)
        except Exception as e:
            return jsonify({'error': str(e)}), 400
        return jsonify(updated)
    finally:
        conn.close()


# --- Rentas y dividendos --------------------------------------------------------

@bp.route('/rentas-dividendos')
def portfolio_rentas_dividendos():
    conn = db.get_connection()
    try:
        return jsonify(db.list_rentas_dividendos(conn))
    finally:
        conn.close()


# --- Forecasts / señales --------------------------------------------------------

def _progress_pct(row):
    if row['status'] == 'done':
        return 100
    if row['status'] in ('queued', 'error'):
        return 0
    if row['started_at'] and row['expected_duration_seconds']:
        elapsed = (datetime.utcnow() - datetime.fromisoformat(row['started_at'])).total_seconds()
        return max(0, min(99, round(elapsed / row['expected_duration_seconds'] * 100)))
    return 0


def _forecast_response(row, conn=None):
    response = {k: v for k, v in row.items() if k != 'result_json'}
    response['progress_pct'] = _progress_pct(row)
    if row['status'] == 'done' and row['result_json']:
        response['result'] = json.loads(row['result_json'])
        # "validation" le dice al frontend si esta señal ya acumuló evidencia
        # suficiente como para pintarse verde/rojo, o si todavía es "sin
        # validar" (ver accuracy.compute_signal_validation) -- instrumentos del
        # Explorador sin alta en la cartera (instrument_id None) no tienen
        # historial coherente de forecasts propios, quedan sin este campo.
        if conn is not None and row['instrument_id'] is not None:
            acc = _get_cached_accuracy(conn, row['instrument_id'])
            response['validation'] = accuracy.compute_signal_validation(acc, db.get_signal_config(conn))
    return response


@bp.route('/forecasts', methods=['POST'])
def create_forecast():
    data = request.get_json()
    conn = db.get_connection()
    try:
        instrument = db.get_instrument(conn, data.get('instrument_id'))
    finally:
        conn.close()
    if not instrument:
        return jsonify({'error': 'Instrumento no encontrado'}), 404

    forecast_id = forecast_worker.submit_forecast(
        instrument_id=instrument['id'], symbol=instrument['symbol'],
        market=instrument['market'], asset_type=instrument['type'],
        sample_count=data.get('sample_count'),
    )
    conn = db.get_connection()
    try:
        row = db.get_forecast(conn, forecast_id)
        response = _forecast_response(row, conn)
    finally:
        conn.close()
    return jsonify(response), 202


@bp.route('/forecasts/run-all', methods=['POST'])
def run_all_forecasts():
    data = request.get_json(silent=True) or {}
    forecast_ids = forecast_worker.enqueue_all_holdings(force=bool(data.get('force')))
    return jsonify({'forecast_ids': forecast_ids})


@bp.route('/instruments/<int:instrument_id>/backtest', methods=['POST'])
def instrument_backtest(instrument_id):
    conn = db.get_connection()
    try:
        instrument = db.get_instrument(conn, instrument_id)
    finally:
        conn.close()
    if not instrument:
        return jsonify({'error': 'Instrumento no encontrado'}), 404
    forecast_ids = forecast_worker.enqueue_backtest(instrument_id)
    return jsonify({'forecast_ids': forecast_ids})


@bp.route('/portfolio-backtest', methods=['POST'])
def portfolio_backtest():
    forecast_ids = forecast_worker.enqueue_portfolio_backtest()
    return jsonify({'forecast_ids': forecast_ids})


@bp.route('/forecasts/latest')
def latest_forecast():
    conn = db.get_connection()
    try:
        row = db.get_latest_forecast(
            conn,
            instrument_id=request.args.get('instrument_id', type=int),
            symbol=request.args.get('symbol'),
            market=request.args.get('market'),
        )
        response = _forecast_response(row, conn) if row else None
    finally:
        conn.close()
    return jsonify(response)


@bp.route('/forecasts/latest-batch', methods=['POST'])
def latest_forecast_batch():
    """Fase 1 (ver conversación, fix de ventaAlerts -- N+1 real que frenaba
    TODAS las vistas, no solo Resumen: webui/static/js/portfolio/alerts.js
    pedía 1 /forecasts/latest POR TENENCIA en paralelo para armar las alertas
    de señal de venta, saturando el pool de conexiones del browser igual que
    el bug ya documentado de Explorador en DESIGN.md). Misma lógica EXACTA
    que GET /forecasts/latest + _forecast_response por instrumento (nunca una
    segunda implementación) -- la única diferencia es que esto resuelve N
    instrument_id en una sola conexión/response en vez de N round-trips HTTP.
    POST (no GET) porque la lista de ids puede ser larga -- más prolijo en el
    body que armar una query string gigante."""
    data = request.get_json(silent=True) or {}
    instrument_ids = data.get('instrument_ids') or []
    conn = db.get_connection()
    try:
        result = {}
        for instrument_id in instrument_ids:
            row = db.get_latest_forecast(conn, instrument_id=instrument_id)
            result[str(instrument_id)] = _forecast_response(row, conn) if row else None
        return jsonify(result)
    finally:
        conn.close()


@bp.route('/forecasts/<int:forecast_id>')
def get_forecast(forecast_id):
    conn = db.get_connection()
    try:
        row = db.get_forecast(conn, forecast_id)
        if not row:
            return jsonify({'error': 'Forecast no encontrado'}), 404
        response = _forecast_response(row, conn)
    finally:
        conn.close()
    return jsonify(response)


@bp.route('/model-reliability')
def model_reliability():
    """Conteo agregado de instrumentos con acierto direccional < 50% hoy
    (ver conversación/auditoría: "la baja confiabilidad del modelo...
    visible en TODAS las pantallas de señales") -- usado por Comparador y
    Detalle para un banner informativo, no reemplaza el "Sin validar" por
    instrumento que ya mostraba cada uno."""
    conn = db.get_connection()
    try:
        return jsonify(accuracy.compute_model_reliability_summary(conn))
    finally:
        conn.close()


@bp.route('/suggested-allocation')
def suggested_allocation():
    """Fase 0 del plan de unificacion (ver conversacion): asignacion de
    capital OPTIMA SUGERIDA entre motores (Risk Parity/Min. Varianza/Max.
    Sharpe sobre la covarianza real entre ellos, ver
    portfolio/risk.py::compute_dynamic_strategy_allocation), junto con la
    asignacion ACTUALMENTE CONFIGURADA -- para que el usuario pueda comparar,
    nunca para auto-aplicar nada. 'suggestion' es None si todavia no corrio
    nunca `python3 -m portfolio.backtest allocation` (nunca se inventa un
    resultado). 'current_weights' se recalcula FRESCO en cada request (es
    barato, solo lee signal_config) para reflejar la configuracion de HOY
    aunque haya cambiado desde la ultima corrida pesada."""
    conn = db.get_connection()
    try:
        suggestion = db.get_suggested_allocation(conn)
        allocation = capital_config.get_capital_allocation(conn)
        current_weights = {
            "core": allocation["core_pct"], "satellite": allocation["satellite_pct"],
            "pairs": capital_config.get_pairs_allocation_pct(conn),
            "factor": capital_config.get_factor_allocation_pct(conn),
        }
        return jsonify({"suggestion": suggestion, "current_weights": current_weights})
    finally:
        conn.close()


@bp.route('/instruments/<int:instrument_id>/accuracy')
def instrument_accuracy(instrument_id):
    conn = db.get_connection()
    try:
        instrument = db.get_instrument(conn, instrument_id)
        if not instrument:
            return jsonify({'error': 'Instrumento no encontrado'}), 404
        result = _get_cached_accuracy(conn, instrument_id)
        if result is None:
            return jsonify(None)
        result['validation'] = accuracy.compute_signal_validation(result, db.get_signal_config(conn))
        if not result.get('pending'):
            result['explanation'] = accuracy.explain_accuracy(instrument['symbol'], result)
        return jsonify(result)
    finally:
        conn.close()


@bp.route('/forecasts-history')
def forecasts_history():
    """Historial completo de pronosticos pasados -- ver portfolio/accuracy.py.
    Con instrument_id: cada corrida de ese instrumento (resuelta o pendiente)
    + calibracion p10-p90 sobre las no-superpuestas ya resueltas. Sin
    instrument_id ("Todos"): agregado por instrumento, solo los que ya
    corrieron algun forecast."""
    conn = db.get_connection()
    try:
        instrument_id = request.args.get('instrument_id', type=int)
        limit = request.args.get('limit', type=int)

        if instrument_id:
            instrument = db.get_instrument(conn, instrument_id)
            if not instrument:
                return jsonify({'error': 'Instrumento no encontrado'}), 404
            forecasts = db.list_forecasts_for_instrument(conn, instrument_id)
            price_history = db.get_price_history(conn, instrument_id)
            return jsonify({
                'symbol': instrument['symbol'],
                'instrument_id': instrument_id,
                'forecasts': accuracy.build_forecast_history(forecasts, price_history, limit=limit),
                'calibration': accuracy.compute_calibration(forecasts, price_history, conn=conn),
                'price_history': accuracy.price_history_candles(price_history),
            })

        summaries = []
        for instrument in db.list_instruments(conn):
            forecasts = db.list_forecasts_for_instrument(conn, instrument['id'])
            if not forecasts:
                continue
            price_history = db.get_price_history(conn, instrument['id'])
            acc = _get_cached_accuracy(conn, instrument['id'])
            acc = acc if (acc and not acc.get('pending')) else None
            calibration = accuracy.compute_calibration(forecasts, price_history, conn=conn)
            summaries.append({
                'instrument_id': instrument['id'],
                'symbol': instrument['symbol'],
                'mape': acc['mape'] if acc else None,
                'directional_hit_pct': acc['directional_hit_pct'] if acc else None,
                'forecasts_evaluated': acc['forecasts_evaluated'] if acc else 0,
                'calibration_pct': calibration['coverage_pct'] if calibration else None,
                'calibration_n': calibration['n'] if calibration else 0,
            })
        return jsonify({'instruments': summaries})
    finally:
        conn.close()


FUNDAMENTALS_FRESHNESS_HOURS = 24


@bp.route('/instruments/<int:instrument_id>/chart')
def instrument_chart(instrument_id):
    """Grafico + indicadores tecnicos (SMA/EMA/RSI/MACD/Bollinger) + señal técnica
    "segunda opinión", independiente de Kronos -- no espera a que corra el modelo,
    se arma al instante con el price_history que ya tenemos."""
    conn = db.get_connection()
    try:
        instrument = db.get_instrument(conn, instrument_id)
        if not instrument:
            return jsonify({'error': 'Instrumento no encontrado'}), 404
        config = db.get_signal_config(conn)
        try:
            payload = forecasting.build_chart_payload(
                conn, instrument_id=instrument_id, symbol=instrument['symbol'],
                market=instrument['market'], asset_type=instrument['type'],
                lookback=config['default_lookback'],
            )
        except Exception as e:
            return jsonify({'error': f'No se pudo construir el gráfico: {e}'}), 502

        signal, explanation = indicators.latest_technical_signal(payload['indicators'], config)
        payload['technical_signal'] = signal
        payload['technical_explanation'] = explanation
        # current_quote (ver conversación, Fase 7): el precio+variación del
        # encabezado usaba el último close de price_history -- la MISMA
        # fuente que el gráfico, que se backfillea perezoso solo al correr un
        # pronóstico y puede quedar días atrás de la cotización real (bug
        # investigado: GOOGL mostraba un precio viejo acá mientras
        # Instrumentos/Tenencias, que sí usan quotes_latest, mostraban el
        # correcto). El gráfico se queda en price_history a propósito (es su
        # fuente histórica real) -- solo el encabezado pasa a usar
        # quotes_latest, igual que el resto de la app. None si todavía no se
        # cargó ninguna cotización para este instrumento (nunca se inventa un
        # precio).
        payload['current_quote'] = db.get_quote(conn, instrument_id)
        return jsonify(payload)
    finally:
        conn.close()


@bp.route('/instruments/<int:instrument_id>/overlays')
def instrument_overlays(instrument_id):
    """Lineas de soporte/resistencia + alertas de precio (Fase 8) -- liviano a
    proposito: a diferencia de /chart, no recalcula SMA/EMA/RSI/MACD ni la
    señal tecnica, solo lo necesario para dibujar overlays sobre el grafico de
    velas ya existente (usado tanto si hay un pronostico de Kronos corrido
    como si no, ver instrument-detail.js). No hay cache propia: el algoritmo de
    pivotes confirmados es una funcion causal del historico (nunca repinta un
    pivote ya mostrado), asi que recalcular en cada pedido es seguro y barato.

    Se acota a `default_lookback` velas (misma ventana "reciente" que ya usa
    Kronos) -- confirmado empiricamente que SIN este corte, un ajuste lineal
    (Theil-Sen) sobre años de historico ARS nominal (que crece ordenes de
    magnitud por inflacion/devaluacion, ver el sesgo ya documentado en
    model/kronos.py) extrapola a valores absurdos (hasta negativos) en vez de
    una linea de soporte/resistencia leible."""
    conn = db.get_connection()
    try:
        instrument = db.get_instrument(conn, instrument_id)
        if not instrument:
            return jsonify({'error': 'Instrumento no encontrado'}), 404
        config = db.get_signal_config(conn)
        price_history = db.get_price_history(conn, instrument_id, limit=config['default_lookback'])
        candles = [
            {
                'time': int(datetime.fromisoformat(row['timestamp']).timestamp()),
                'open': row['open'], 'high': row['high'], 'low': row['low'], 'close': row['close'],
            }
            for row in price_history
        ]
        # current_price (ver conversación/auditoría, hallazgo 1.6: "los
        # motores calculan con la vela de ayer") -- precio EN VIVO para el
        # mensaje de ruptura, no el último close de price_history (puede
        # quedar días atrás, ver portfolio/trendlines.py::_detect_breakout).
        quote = db.get_quote(conn, instrument_id)
        return jsonify({
            'trendlines': trendlines.compute_trendlines(candles, current_price=quote['price'] if quote else None),
            'price_alerts': db.list_price_alerts(conn, instrument_id=instrument_id),
        })
    finally:
        conn.close()


# --- Propuestas (Fase 8) ---------------------------------------------------------

@bp.route('/proposals')
def portfolio_proposals():
    conn = db.get_connection()
    try:
        items = proposals.sync_proposals(conn)
        estado = request.args.get('estado')
        if estado:
            items = [p for p in items if p['estado'] == estado]
        return jsonify(items)
    finally:
        conn.close()


@bp.route('/proposals/<int:proposal_id>', methods=['PUT'])
def proposal_detail_route(proposal_id):
    conn = db.get_connection()
    try:
        proposal = db.get_proposal(conn, proposal_id)
        if not proposal:
            return jsonify({'error': 'Propuesta no encontrada'}), 404
        data = request.get_json()
        estado = data.get('estado')
        if estado not in ('vista', 'aceptada', 'descartada'):
            return jsonify({'error': 'estado invalido'}), 400
        updated = db.update_proposal(conn, proposal_id, estado=estado)
        return jsonify(updated)
    finally:
        conn.close()


# --- Decisiones de ejecucion REAL (ver portfolio/execution_router.py) -------------
# A diferencia de /proposals (aceptar ahi solo cambia un estado en la base,
# nunca ejecuta nada -- ver portfolio/order_execution.py), aceptar/rechazar
# ACA SI llama a resolve_decision, que ejecuta de verdad contra el broker.

@bp.route('/execution-decisions')
def execution_decisions_route():
    conn = db.get_connection()
    try:
        estado = request.args.get('estado')
        items = db.list_execution_decisions(conn, status=estado)
        return jsonify(items)
    finally:
        conn.close()


@bp.route('/execution-decisions/<int:decision_id>', methods=['PUT'])
def execution_decision_detail_route(decision_id):
    """Body: {"aprobada": true|false} -- lo que llegue primero entre esto y
    el boton de Telegram gana (ver execution_router.resolve_decision,
    already_resolved=True si el otro canal ya actuo)."""
    conn = db.get_connection()
    try:
        data = request.get_json() or {}
        if 'aprobada' not in data:
            return jsonify({'error': 'falta "aprobada" (true/false)'}), 400
        try:
            result = execution_router.resolve_decision(
                conn, decision_id, approved=bool(data['aprobada']), via='web',
            )
        except ValueError as e:
            return jsonify({'error': str(e)}), 404
        return jsonify(result)
    finally:
        conn.close()


# --- Alertas de precio (stop-loss / take-profit, Fase 8) -------------------------

@bp.route('/price-alerts', methods=['GET', 'POST'])
def portfolio_price_alerts():
    conn = db.get_connection()
    try:
        if request.method == 'POST':
            data = request.get_json()
            try:
                alert_id = db.add_price_alert(
                    conn,
                    instrument_id=int(data['instrument_id']),
                    tipo=data['tipo'],
                    nivel=float(data['nivel']),
                )
            except Exception as e:
                return jsonify({'error': str(e)}), 400
            return jsonify({'success': True, 'id': alert_id})

        instrument_id = request.args.get('instrument_id', type=int)
        return jsonify(db.list_price_alerts(conn, instrument_id=instrument_id))
    finally:
        conn.close()


@bp.route('/price-alerts/<int:alert_id>', methods=['PUT', 'DELETE'])
def price_alert_detail_route(alert_id):
    conn = db.get_connection()
    try:
        alert = db.get_price_alert(conn, alert_id)
        if not alert:
            return jsonify({'error': 'Alerta no encontrada'}), 404

        if request.method == 'DELETE':
            db.delete_price_alert(conn, alert_id)
            return jsonify({'success': True})

        data = request.get_json()
        fields = {}
        if 'nivel' in data:
            fields['nivel'] = float(data['nivel'])
        if 'activa' in data:
            fields['activa'] = 1 if data['activa'] else 0
        updated = db.update_price_alert(conn, alert_id, **fields)
        return jsonify(updated)
    finally:
        conn.close()


# --- Prestamos (Fase 10) ---------------------------------------------------------

def _loan_response(conn, loan):
    status = loans.compute_loan_status(conn, loan)
    response = {**loan, **status}
    response['explanation'] = loans.explain_loan_status(loan, status)
    return response


@bp.route('/loans', methods=['GET', 'POST'])
def portfolio_loans():
    conn = db.get_connection()
    try:
        if request.method == 'POST':
            data = request.get_json()
            try:
                loan_id = db.add_loan(
                    conn, deudor=data['deudor'], moneda=data.get('moneda', 'ARS'),
                    capital=float(data['capital']), fecha_inicio=data['fecha_inicio'],
                    tasa_anual=float(data['tasa_anual']), plan_pago=data['plan_pago'],
                    ajusta_cer=bool(data.get('ajusta_cer', False)),
                    plazo_meses=int(data['plazo_meses']) if data.get('plazo_meses') else None,
                    direccion=data.get('direccion', 'prestado'),
                    notas=data.get('notas'),
                )
            except Exception as e:
                return jsonify({'error': str(e)}), 400
            return jsonify({'success': True, 'id': loan_id})

        return jsonify([_loan_response(conn, loan) for loan in db.list_loans(conn)])
    finally:
        conn.close()


@bp.route('/loans/<int:loan_id>', methods=['GET', 'PUT', 'DELETE'])
def loan_detail_route(loan_id):
    conn = db.get_connection()
    try:
        loan = db.get_loan(conn, loan_id)
        if not loan:
            return jsonify({'error': 'Préstamo no encontrado'}), 404

        if request.method == 'DELETE':
            db.delete_loan(conn, loan_id)
            return jsonify({'success': True})

        if request.method == 'GET':
            return jsonify(_loan_response(conn, loan))

        data = request.get_json()
        fields = {}
        for key in ('deudor', 'moneda', 'fecha_inicio', 'plan_pago', 'estado', 'direccion', 'notas'):
            if key in data:
                fields[key] = data[key]
        if 'capital' in data:
            fields['capital'] = float(data['capital'])
        if 'tasa_anual' in data:
            fields['tasa_anual'] = float(data['tasa_anual'])
        if 'plazo_meses' in data:
            fields['plazo_meses'] = int(data['plazo_meses']) if data['plazo_meses'] else None
        if 'ajusta_cer' in data:
            fields['ajusta_cer'] = int(bool(data['ajusta_cer']))
        try:
            updated = db.update_loan(conn, loan_id, **fields)
        except Exception as e:
            return jsonify({'error': str(e)}), 400
        return jsonify(_loan_response(conn, updated))
    finally:
        conn.close()


@bp.route('/loans/<int:loan_id>/payments', methods=['GET', 'POST'])
def loan_payments_route(loan_id):
    conn = db.get_connection()
    try:
        loan = db.get_loan(conn, loan_id)
        if not loan:
            return jsonify({'error': 'Préstamo no encontrado'}), 404

        if request.method == 'POST':
            data = request.get_json()
            try:
                payment_id = db.add_loan_payment(
                    conn, loan_id=loan_id, fecha=data['fecha'],
                    capital=float(data.get('capital', 0)), interes=float(data.get('interes', 0)),
                    notas=data.get('notas'),
                )
            except Exception as e:
                return jsonify({'error': str(e)}), 400
            return jsonify({'success': True, 'id': payment_id})

        return jsonify(db.list_loan_payments(conn, loan_id))
    finally:
        conn.close()


@bp.route('/loans/<int:loan_id>/payments/<int:payment_id>', methods=['DELETE'])
def loan_payment_detail_route(loan_id, payment_id):
    conn = db.get_connection()
    try:
        db.delete_loan_payment(conn, payment_id)
        return jsonify({'success': True})
    finally:
        conn.close()


# --- Documentos de prestamos (pagares, comprobantes) -----------------------------

@bp.route('/loans/<int:loan_id>/documents', methods=['GET', 'POST'])
def loan_documents_route(loan_id):
    conn = db.get_connection()
    try:
        loan = db.get_loan(conn, loan_id)
        if not loan:
            return jsonify({'error': 'Préstamo no encontrado'}), 404

        if request.method == 'POST':
            file = request.files.get('file')
            if not file or not file.filename:
                return jsonify({'error': 'No se recibió ningún archivo'}), 400
            stored_name, size_bytes = loans.save_document_file(loan_id, file.filename, file)
            doc_id = db.add_loan_document(
                conn, loan_id=loan_id, nombre_original=file.filename, nombre_archivo=stored_name,
                content_type=file.content_type, size_bytes=size_bytes,
            )
            return jsonify({'success': True, 'id': doc_id})

        return jsonify(db.list_loan_documents(conn, loan_id))
    finally:
        conn.close()


@bp.route('/loans/<int:loan_id>/documents/<int:document_id>/download')
def loan_document_download(loan_id, document_id):
    conn = db.get_connection()
    try:
        doc = db.get_loan_document(conn, document_id)
        if not doc or doc['loan_id'] != loan_id:
            return jsonify({'error': 'Documento no encontrado'}), 404
        return send_from_directory(
            loans.DOCUMENTS_DIR, doc['nombre_archivo'],
            as_attachment=True, download_name=doc['nombre_original'],
        )
    finally:
        conn.close()


@bp.route('/loans/<int:loan_id>/documents/<int:document_id>', methods=['DELETE'])
def loan_document_detail_route(loan_id, document_id):
    conn = db.get_connection()
    try:
        doc = db.get_loan_document(conn, document_id)
        if doc and doc['loan_id'] == loan_id:
            loans.delete_document_file(doc['nombre_archivo'])
            db.delete_loan_document(conn, document_id)
        return jsonify({'success': True})
    finally:
        conn.close()


@bp.route('/planned-movements', methods=['GET', 'POST'])
def planned_movements_route():
    conn = db.get_connection()
    try:
        if request.method == 'POST':
            data = request.get_json()
            try:
                movement_id = db.add_planned_movement(
                    conn, fecha=data['fecha'], tipo=data['tipo'],
                    monto=float(data['monto']), moneda=data.get('moneda', 'ARS'),
                    notas=data.get('notas'),
                )
            except Exception as e:
                return jsonify({'error': str(e)}), 400
            return jsonify({'success': True, 'id': movement_id})
        return jsonify(db.list_planned_movements(conn))
    finally:
        conn.close()


@bp.route('/planned-movements/<int:movement_id>', methods=['DELETE'])
def planned_movement_detail_route(movement_id):
    conn = db.get_connection()
    try:
        db.delete_planned_movement(conn, movement_id)
        return jsonify({'success': True})
    finally:
        conn.close()


@bp.route('/projection')
def portfolio_projection():
    conn = db.get_connection()
    try:
        return jsonify(projection.compute_projection(conn))
    finally:
        conn.close()


@bp.route('/cash-summary')
def portfolio_cash_summary():
    moneda = request.args.get('moneda', 'ARS')
    conn = db.get_connection()
    try:
        return jsonify({
            'saldo': cash.get_cash_balance(conn),
            'flow': cash.monthly_net_flow(conn, moneda=moneda),
            'recientes': cash.recent_movements(conn, moneda=moneda, limit=5),
        })
    finally:
        conn.close()


def _compute_ganancia(conn):
    """Fase 10, item 3 -- "¿Cuánto gané?": ganancia total por moneda NATIVA
    (patrimonio actual - aportes netos, nunca mezclado con la otra moneda,
    mismo criterio que get_cash_balance/currency_exposure) + XIRR real (ver
    xirr.py). El TWR de cartera ya existe (benchmarks.compute_benchmarks,
    panel de arriba en Resumen) y el desglose de dividendos/cauciones/
    comisiones del período ya existe en el frontend (cascada de Fase 5) --
    este endpoint no los duplica, solo agrega lo que faltaba."""
    holdings = db.get_holdings_with_quotes(conn)
    cauciones_vigentes = [c for c in db.get_cauciones_resumen(conn) if not c['vencido']]
    # _compute_currency_exposure ya suma préstamos vigentes a sus baldes (Fase
    # 10) -- no se vuelve a sumar acá para no contarlos dos veces.
    exposure = _compute_currency_exposure(conn, holdings, cauciones_vigentes)
    patrimonio_ars = exposure['ars_fija']['valor'] + exposure['ars_cer']['valor']
    patrimonio_usd = exposure['usd']['valor']

    cash_balance = cash.get_cash_balance(conn)
    patrimonio_ars += cash_balance['ARS']
    patrimonio_usd += cash_balance['USD']

    result = {}
    for moneda, patrimonio in (('ARS', patrimonio_ars), ('USD', patrimonio_usd)):
        flows = cash.external_cash_flows(conn, moneda=moneda) + cash.loan_capital_flows(conn, moneda=moneda)
        aportes_netos = sum(f['monto'] for f in flows)
        result[moneda] = {
            'patrimonio_actual': round(patrimonio, 2),
            'aportes_netos': round(aportes_netos, 2),
            'ganancia_total': round(patrimonio - aportes_netos, 2),
            'xirr': xirr.compute_portfolio_xirr(conn, moneda, patrimonio),
        }
    return result


@bp.route('/ganancia')
def portfolio_ganancia():
    conn = db.get_connection()
    try:
        return jsonify(_compute_ganancia(conn))
    finally:
        conn.close()


@bp.route('/instruments/<int:instrument_id>/risk')
def instrument_risk_route(instrument_id):
    """Riesgo (volatilidad/caída máxima) de CUALQUIER instrumento con
    historial, este o no en cartera -- antes solo se podía ver si ya era una
    tenencia (ver _attach_risk en /holdings). Reusa risk.compute_instrument_risk,
    sin duplicar logica."""
    conn = db.get_connection()
    try:
        instrument = db.get_instrument(conn, instrument_id)
        if not instrument:
            return jsonify({'error': 'Instrumento no encontrado'}), 404
        config = db.get_signal_config(conn)
        rows = db.get_price_history(conn, instrument_id, limit=61)
        closes = [r['close'] for r in sorted(rows, key=lambda r: r['timestamp'])]
        result = risk.compute_instrument_risk(closes, config)
        if result is None:
            return jsonify({'available': False, 'reason': 'Sin historial de precios suficiente.'})
        result['available'] = True
        result['explanation'] = risk.explain_instrument_risk(instrument['symbol'], result)
        return jsonify(result)
    finally:
        conn.close()


@bp.route('/instruments/<int:instrument_id>/technical-backtest')
def instrument_technical_backtest(instrument_id):
    conn = db.get_connection()
    try:
        instrument = db.get_instrument(conn, instrument_id)
        if not instrument:
            return jsonify({'error': 'Instrumento no encontrado'}), 404
        config = db.get_signal_config(conn)
        price_history = db.get_price_history(conn, instrument_id)
        return jsonify(indicators.backtest_rsi_signal(price_history, config))
    finally:
        conn.close()


@bp.route('/instruments/<int:instrument_id>/fundamentals')
def instrument_fundamentals(instrument_id):
    conn = db.get_connection()
    try:
        instrument = db.get_instrument(conn, instrument_id)
        if not instrument:
            return jsonify({'error': 'Instrumento no encontrado'}), 404

        cached = db.get_fundamentals(conn, instrument_id)
        stale = True
        if cached and cached.get('fetched_at'):
            age_hours = (datetime.utcnow() - datetime.fromisoformat(cached['fetched_at'])).total_seconds() / 3600
            stale = age_hours >= FUNDAMENTALS_FRESHNESS_HOURS
        if not cached or stale or request.args.get('refresh') == '1':
            data = fundamentals.fetch_fundamentals(instrument['symbol'], instrument['type'])
            db.upsert_fundamentals(conn, instrument_id, data)
            cached = db.get_fundamentals(conn, instrument_id)

        config = db.get_signal_config(conn)
        cached['explanation'] = fundamentals.explain_fundamentals(instrument['symbol'], cached, config)
        return jsonify(cached)
    finally:
        conn.close()


@bp.route('/instruments/<int:instrument_id>/news')
def instrument_news(instrument_id):
    """Noticias y sentimiento de ESTE instrumento puntual (ver
    conversación: "dentro de cada instrumento las que le afectan") --
    reusa el mismo cache de 6hs que ya usa el filtro de riesgo del Motor
    Satélite (assess_event_risk), nunca pega a Finnhub/yfinance en cada
    carga de pantalla. ?refresh=1 lo fuerza (mismo criterio que
    /fundamentals de arriba)."""
    conn = db.get_connection()
    try:
        instrument = db.get_instrument(conn, instrument_id)
        if not instrument:
            return jsonify({'error': 'Instrumento no encontrado'}), 404
        assessment = nlp_agent.assess_event_risk(
            conn, instrument['symbol'], use_cache=request.args.get('refresh') != '1',
        )
        return jsonify(assessment)
    finally:
        conn.close()


@bp.route('/instruments/<int:instrument_id>/news-history')
def instrument_news_history(instrument_id):
    """Historial de los últimos N días de evaluaciones de ESTE instrumento
    (ver conversación: "las noticias no se guardan en ningún lado?" --
    SÍ se guardan, news_sentiment_log es append-only, pero hasta ahora
    db.list_recent_sentiment no tenía ninguna ruta que la expusiera --
    código muerto, aunque ya probado). ?days= (default 7, ver
    db.list_recent_sentiment) -- incluye la evaluación de HOY si ya corrió."""
    conn = db.get_connection()
    try:
        instrument = db.get_instrument(conn, instrument_id)
        if not instrument:
            return jsonify({'error': 'Instrumento no encontrado'}), 404
        days = request.args.get('days', default=7, type=int)
        return jsonify(db.list_recent_sentiment(conn, instrument['symbol'], days=days))
    finally:
        conn.close()


@bp.route('/news-today')
def news_today():
    """Subtab "Noticias" de Decisiones (ver conversación) -- agrega la
    última evaluación de HOY por símbolo, de cualquier instrumento que haya
    sido evaluado (por una señal de hoy o por alguien mirando su página de
    detalle) -- nunca el historial de días anteriores."""
    conn = db.get_connection()
    try:
        return jsonify(db.list_today_sentiment(conn))
    finally:
        conn.close()


@bp.route('/instruments/<int:instrument_id>/fixed-income')
def instrument_fixed_income(instrument_id):
    """TIR + duration (o motivo de no disponible) -- ver portfolio/fixed_income.py."""
    conn = db.get_connection()
    try:
        instrument = db.get_instrument(conn, instrument_id)
        if not instrument:
            return jsonify({'error': 'Instrumento no encontrado'}), 404
        quote = db.get_quote(conn, instrument_id)
        price = quote['price'] if quote else None
        summary = fixed_income.compute_fixed_income_summary(instrument, price)
        summary['explanation'] = fixed_income.explain_fixed_income(instrument['symbol'], summary)
        return jsonify(summary)
    finally:
        conn.close()


@bp.route('/fixed-income-calendar')
def fixed_income_calendar():
    conn = db.get_connection()
    try:
        instruments = [i for i in db.list_instruments(conn) if i['type'] in ('bono', 'on')]
        return jsonify(fixed_income.upcoming_fixed_income_events(instruments))
    finally:
        conn.close()


def _compute_currency_exposure(conn, holdings, cauciones):
    """Agrupa la cartera (holdings + cauciones + préstamos vigentes) en 3
    baldes: ARS tasa fija / ARS CER / USD -- ver portfolio/fixed_income.py y
    el campo instruments.rate_type.

    Los 3 montos se guardan en su moneda nativa (para mostrarlos tal cual),
    pero el % se calcula sobre un total convertido todo a ARS -- sumar pesos y
    dolares crudos para un total daria un % sin sentido (las unidades no son
    la misma, aunque los baldes individuales si estan bien separados).

    Extraido a funcion propia (antes vivia inline en el endpoint) para que
    /dashboard-bundle (Fase 7) pueda reusar el mismo calculo sin duplicar la
    logica de baldes.

    La tasa para este % es SIEMPRE MEP (decision explicita, no sigue "Fuente
    USD" de Preferencias): antes usaba resolve_usd_rate(settings), que podia
    ser BNA/CCL segun esa preferencia -- eso hacia que el "USD" de este panel
    y el patrimonio del encabezado (que siempre fue MEP) quedaran calculados
    con tasas distintas sin decirlo (bug reportado en la auditoria). "Fuente
    USD" sigue aplicando al toggle general ARS/USD de Tenencias/Posiciones,
    pero no a este panel ni a la columna P&L USD (que ya usaba MEP)."""
    try:
        usd_rate = fetchers.fetch_dolarapi_casa('bolsa')['venta']
    except Exception:
        usd_rate = None

    buckets = {'ars_fija': 0.0, 'ars_cer': 0.0, 'usd': 0.0}
    for h in holdings:
        if h['valor_actual'] is None:
            continue
        instrument = h['instrument']
        if instrument['currency'] == 'USD':
            buckets['usd'] += h['valor_actual']
        elif instrument.get('rate_type') == 'cer':
            buckets['ars_cer'] += h['valor_actual']
        else:
            buckets['ars_fija'] += h['valor_actual']
    for c in cauciones:
        if c['caucion']['moneda'] == 'USD':
            buckets['usd'] += c['valor_actual']
        else:
            buckets['ars_fija'] += c['valor_actual']
    for loan in db.list_loans(conn):
        if loan['estado'] != 'vigente':
            continue
        capital_pendiente = loans.compute_loan_status(conn, loan)['capital_pendiente']
        # 'recibido' = pediste prestado vos -- un pasivo, resta del patrimonio
        # en vez de sumar (ver columna 'direccion' en loans, Fase 10).
        signo = 1 if loan['direccion'] == 'prestado' else -1
        monto = signo * capital_pendiente
        if loan['moneda'] == 'USD':
            buckets['usd'] += monto
        elif loan['ajusta_cer']:
            buckets['ars_cer'] += monto
        else:
            buckets['ars_fija'] += monto

    total_ars = buckets['ars_fija'] + buckets['ars_cer'] + (buckets['usd'] * usd_rate if usd_rate else 0)
    pct_available = usd_rate is not None or buckets['usd'] == 0
    return {
        key: {
            'valor': round(value, 2),
            'pct': round(
                (value * usd_rate if key == 'usd' else value) / total_ars * 100, 1
            ) if total_ars and pct_available else None,
        }
        for key, value in buckets.items()
    }


@bp.route('/currency-exposure')
def currency_exposure():
    conn = db.get_connection()
    try:
        holdings = db.get_holdings_with_quotes(conn)
        cauciones = [c for c in db.get_cauciones_resumen(conn) if not c['vencido']]  # ver nota en portfolio_risk()
        return jsonify(_compute_currency_exposure(conn, holdings, cauciones))
    finally:
        conn.close()


TECHNICAL_CACHE_FRESHNESS_HOURS = 24

# Cache de accuracy.compute_accuracy (ver conversacion, accuracy_cache en
# schema.sql) -- mismo TTL diario que TECHNICAL_CACHE_FRESHNESS_HOURS arriba,
# mismo motivo: el resultado no cambia mas de una vez por dia (depende de
# price_history/forecasts, que se actualizan con el cierre del mercado).
ACCURACY_CACHE_FRESHNESS_HOURS = 24


def _get_cached_accuracy(conn, instrument_id):
    cached = db.get_accuracy_cache(conn, instrument_id)
    stale = True
    if cached and cached.get('fetched_at'):
        age_hours = (datetime.utcnow() - datetime.fromisoformat(cached['fetched_at'])).total_seconds() / 3600
        stale = age_hours >= ACCURACY_CACHE_FRESHNESS_HOURS
    if cached and not stale:
        return json.loads(cached['result_json'])
    # Las consultas pesadas (todo el historial de forecasts + price_history)
    # solo corren en cache-miss -- antes corrian SIEMPRE, aunque el resultado
    # fuera a salir cacheado igual.
    forecasts = db.list_forecasts_for_instrument(conn, instrument_id)
    price_history = db.get_price_history(conn, instrument_id)
    acc = accuracy.compute_accuracy(forecasts, price_history, conn=conn)
    db.insert_accuracy_cache(conn, instrument_id, result_json=json.dumps(acc))
    return acc


def _compute_technical_cache_row(conn, instrument, config):
    """Misma logica que technical-signal.js::buildTechnicalRow, del lado
    servidor -- indicators.py/forecasting.py siguen siendo la unica fuente de
    verdad (Señales sigue llamando esos mismos endpoints en vivo), esto solo
    orquesta ese mismo calculo una vez y lo cachea (ver technical_cache,
    Fase 9) para que el Dashboard no dispare ~3 requests por tenencia en cada
    carga de pagina (antes: ~18-30s con 36 instrumentos).

    Para los CEDEARs ya mapeados a un subyacente real (ver conversacion:
    "el edge tecnico de estos simbolos se mide sobre la serie ruidosa del
    CEDEAR, con 5-30x menos muestra que la real disponible del subyacente")
    el RSI/tecnico/edge se calculan sobre esa serie real en vez de
    price_history -- MISMO criterio ya aplicado al forecasting de Kronos
    (ver forecasting.build_model_input), pero el grafico que arma
    build_chart_payload (historical+indicators para el candlestick de
    Detalle) sigue en ARS/CEDEAR sin tocar, es solo display. latest_technical_signal
    solo compara el valor de RSI contra umbrales (0-100, nivel/moneda-agnostico)
    -- no hace falta reconvertir ningun precio."""
    instrument_id = instrument['id']
    rsi = None
    tecnico = None
    underlying_rows = None
    if instrument['type'] == 'cedear' and instrument['symbol'] in forecasting.CEDEAR_DIRECT_UNDERLYING_SYMBOLS:
        underlying_rows = forecasting.get_underlying_price_rows_for_technical(conn, instrument['symbol'])
    try:
        if underlying_rows is not None:
            indicators_dict = indicators.compute_all(pd.DataFrame(underlying_rows))
        else:
            payload = forecasting.build_chart_payload(
                conn, instrument_id=instrument_id, symbol=instrument['symbol'],
                market=instrument['market'], asset_type=instrument['type'],
                lookback=config['default_lookback'],
            )
            indicators_dict = payload['indicators']
        rsi_series = indicators_dict.get('rsi_14') or []
        rsi = rsi_series[-1] if rsi_series else None
        tecnico, _ = indicators.latest_technical_signal(indicators_dict, config)
    except Exception:
        pass  # sin historico suficiente (ej. fondos) -- rsi/tecnico quedan None, nunca se inventa

    edge = None
    n = None
    if tecnico:
        price_history = underlying_rows if underlying_rows is not None else db.get_price_history(conn, instrument_id)
        if len(price_history) >= indicators.BACKTEST_MIN_ROWS:
            backtest = indicators.backtest_rsi_signal(price_history, config)
            if not backtest.get('pending'):
                bucket = backtest['by_signal'].get(tecnico)
                if bucket:
                    edge = bucket['edge_vs_base_rate_pct']
                    n = bucket['count']

    fundamental_pass = None
    fund = db.get_fundamentals(conn, instrument_id)
    if fund and fund.get('available') and fund.get('pe_ratio') is not None and fund.get('profit_margin') is not None:
        fundamental_pass = fund['pe_ratio'] < config['fa_max_pe'] and fund['profit_margin'] > config['fa_min_margin_pct'] / 100

    return {'rsi': rsi, 'tecnico': tecnico, 'edge': edge, 'n': n, 'fundamental_pass': fundamental_pass}


def _get_technical_row(conn, instrument, config):
    cached = db.get_technical_cache(conn, instrument['id'])
    stale = True
    if cached and cached.get('fetched_at'):
        age_hours = (datetime.utcnow() - datetime.fromisoformat(cached['fetched_at'])).total_seconds() / 3600
        stale = age_hours >= TECHNICAL_CACHE_FRESHNESS_HOURS
    if not cached or stale:
        row = _compute_technical_cache_row(conn, instrument, config)
        db.upsert_technical_cache(conn, instrument['id'], **row)
        cached = db.get_technical_cache(conn, instrument['id'])
    return cached


@bp.route('/dashboard-bundle')
def dashboard_bundle():
    """Fase 7 (precondicion de realtime): todo lo que el Dashboard de Resumen
    necesita en una sola request, en vez de las ~13 que hacia por separado
    (holdings+cauciones+settings del caller, mas los 9 fetches propios de
    renderResumenDashboard, mas 1 /forecasts/latest por cada tenencia) --
    mismo shape que esas respuestas por separado, solo que juntas.

    Fase 9: tambien incluye technical_by_instrument (columna "Tecnico" +
    tarjeta de decidir), antes resuelto con 3-4 requests POR tenencia desde
    el browser (instrumentChart/technicalBacktest/fundamentals, ~18-30s con
    36 instrumentos) -- ver _get_technical_row, que cachea el resultado por
    dia en vez de recalcularlo en cada carga de pagina."""
    conn = db.get_connection()
    try:
        settings = db.get_app_settings(conn)
        config = db.get_signal_config(conn)
        holdings = db.get_holdings_with_quotes(conn)
        cauciones = db.get_cauciones_resumen(conn)  # cruda: el frontend la usa para mostrar el historial completo
        # Solo vigentes para cualquier total/balde (patrimonio, exposicion,
        # riesgo) -- una vencida ya no es capital en caucion (ver nota en
        # portfolio_risk() mas arriba, confirmado contra datos reales del
        # broker en datos_broker/).
        cauciones_vigentes = [c for c in cauciones if not c['vencido']]
        # Se calcula ANTES de _apply_display_currency: esa funcion muta
        # valor_actual in-place a la moneda de exhibicion elegida, pero
        # _compute_currency_exposure bucketiza por instrument['currency']
        # (la moneda NATIVA) -- si se le pasara post-conversion, un holding
        # USD con display_currency=ARS quedaria con un valor en escala ARS
        # metido en el balde "usd", inflando patrimonio (bug real, detectado
        # al verificar el bundle contra la version pre-Fase-7).
        currency_exposure_data = _compute_currency_exposure(conn, holdings, cauciones_vigentes)
        # compute_portfolio_risk_metrics necesita holdings en moneda NATIVA
        # (ver su propio docstring) -- se calcula ANTES de _apply_display_currency,
        # que muta valor_actual in-place a la moneda de exhibicion elegida.
        portfolio_risk_data = risk.compute_portfolio_risk_metrics(
            conn, [h for h in holdings if h['valor_actual'] is not None], cauciones_vigentes, config,
        )
        portfolio_risk_data['explanation'] = risk.explain_portfolio_risk(portfolio_risk_data)

        holdings = _apply_display_currency(holdings, settings)
        holdings = _attach_risk(conn, holdings, config)

        try:
            usd_mep = {'rate': fetchers.fetch_dolarapi_casa('bolsa')['venta']}
        except Exception:
            usd_mep = None

        forecasts_by_instrument = {}
        technical_by_instrument = {}
        for h in holdings:
            instrument_id = h['instrument']['id']
            row = db.get_latest_forecast(conn, instrument_id=instrument_id)
            forecasts_by_instrument[instrument_id] = _forecast_response(row, conn) if row else None
            technical_by_instrument[instrument_id] = _get_technical_row(conn, h['instrument'], config)

        # Préstamos (Fase 10): se exponen acá para que Asignación, la cascada
        # de ingresos (intereses cobrados) y "Para decidir esta semana" los
        # usen sin pedirlos aparte -- loan_payments viene aplanado (todos los
        # préstamos juntos) con la moneda del préstamo padre ya adjunta, para
        # poder convertir a USD igual que dividendos/cauciones en la cascada.
        raw_loans = db.list_loans(conn)
        loans_data = [_loan_response(conn, loan) for loan in raw_loans]
        loan_payments_data = [
            {**payment, 'moneda': loan['moneda'], 'direccion': loan['direccion']}
            for loan in raw_loans
            for payment in db.list_loan_payments(conn, loan['id'])
        ]

        return jsonify({
            'holdings': holdings,
            'cauciones': cauciones,
            'settings': settings,
            'benchmarks': benchmarks.compute_benchmarks(conn),
            'target_allocation': db.list_target_allocation(conn),
            'portfolio_risk': portfolio_risk_data,
            'currency_exposure': currency_exposure_data,
            'fixed_income_events': fixed_income.upcoming_fixed_income_events(
                [i for i in db.list_instruments(conn) if i['type'] in ('bono', 'on')]
            ),
            'plazos_fijos': db.get_plazos_fijos_resumen(conn),
            'signal_config': config,
            'history_data': history.reconstruct_portfolio_value(conn),
            'usd_mep_rate': usd_mep,
            'forecasts_by_instrument': forecasts_by_instrument,
            'technical_by_instrument': technical_by_instrument,
            'rentas_dividendos': db.list_rentas_dividendos(conn),
            'transactions': db.list_transactions(conn),
            'ganancia': _compute_ganancia(conn),
            'loans': loans_data,
            'loan_payments': loan_payments_data,
        })
    finally:
        conn.close()


# --- Config de señales ----------------------------------------------------------

@bp.route('/signal-config', methods=['GET', 'PUT'])
def signal_config():
    conn = db.get_connection()
    try:
        if request.method == 'PUT':
            config = db.update_signal_config(conn, **request.get_json())
        else:
            config = db.get_signal_config(conn)
        # openai_api_key/anthropic_api_key/finnhub_api_key NUNCA en texto
        # plano en la respuesta (ver conversación, "APIs de IA" en
        # Integraciones) -- este endpoint lo piden core-satellite.js/
        # proposals.js/ajustes.js solo por la asignación Core/Satélite, así
        # que la key viajaría de más en cada una de esas cargas si no se
        # enmascarara acá. Mismo criterio que broker_client.mask_account
        # para las cuentas de bróker.
        config = dict(config)
        for field in ('openai_api_key', 'anthropic_api_key', 'finnhub_api_key'):
            config[f'{field}_set'] = bool(config.get(field))
            config[field] = broker_client._mask_secret(config.get(field))
        # Sugerencia de TNA de caución desde las cauciones reales del
        # usuario (ver conversación/auditoría, hallazgo 2.2) -- computada,
        # nunca se guarda sola: caucion_reference_tna sigue siendo manual.
        config['caucion_reference_tna_suggested'] = db.compute_caucion_reference_tna(conn)
        return jsonify(config)
    finally:
        conn.close()


@bp.route('/satellite-exit-params', methods=['GET', 'PUT'])
def satellite_exit_params():
    """GET/PUT de take_profit_pct/stop_loss_pct del Motor Satelite (ver
    portfolio/config.py::get_satellite_exit_params -- stop_loss_pct es un
    TECHO, no un piso, ver conversacion). Endpoint dedicado y validado en
    vez de pasar por PUT /signal-config a secas, para que un valor invalido
    (negativo, > 100, no numerico) devuelva un 400 con el motivo real."""
    conn = db.get_connection()
    try:
        if request.method == 'PUT':
            data = request.get_json(silent=True) or {}
            try:
                params = capital_config.set_satellite_exit_params(
                    conn,
                    take_profit_pct=data.get('take_profit_pct'),
                    stop_loss_pct=data.get('stop_loss_pct'),
                )
            except ValueError as e:
                return jsonify({'error': str(e)}), 400
        else:
            params = capital_config.get_satellite_exit_params(conn)
        return jsonify(params)
    finally:
        conn.close()


@bp.route('/core-params', methods=['GET', 'PUT'])
def core_params():
    """GET/PUT de min_efficiency_score del Motor Core (ver
    portfolio/config.py::get_core_params/set_core_min_efficiency_score).
    Endpoint dedicado y validado, mismo criterio que /satellite-exit-params."""
    conn = db.get_connection()
    try:
        if request.method == 'PUT':
            data = request.get_json(silent=True) or {}
            if 'min_efficiency_score' not in data:
                return jsonify({'error': "falta 'min_efficiency_score'"}), 400
            try:
                params = capital_config.set_core_min_efficiency_score(conn, data['min_efficiency_score'])
            except ValueError as e:
                return jsonify({'error': str(e)}), 400
        else:
            params = capital_config.get_core_params(conn)
        return jsonify(params)
    finally:
        conn.close()


@bp.route('/risk-profiles')
def risk_profiles():
    """Lista los 3 perfiles preconfigurados (Conservador/Moderado/Agresivo,
    ver portfolio/config.py::RISK_PROFILES) + cual esta activo AHORA MISMO --
    el activo se DERIVA de los parametros reales (core_allocation_pct/
    satellite_take_profit_pct/satellite_stop_loss_pct), nunca un flag
    guardado aparte (ver docstring de get_active_risk_profile)."""
    conn = db.get_connection()
    try:
        return jsonify({
            'profiles': capital_config.list_risk_profiles(conn),
            'active_profile': capital_config.get_active_risk_profile(conn),
        })
    finally:
        conn.close()


@bp.route('/risk-profiles/activate', methods=['POST'])
def activate_risk_profile():
    """Aplica los 3 parametros del perfil elegido (ver
    portfolio/config.py::activate_risk_profile) -- equivalente a cambiar
    asignacion Core/Satelite + Take Profit + Stop Loss del Satelite a mano,
    uno por uno, con los valores del preset."""
    data = request.get_json(silent=True) or {}
    profile_id = data.get('profile_id')
    if not profile_id:
        return jsonify({'error': "falta 'profile_id'"}), 400
    conn = db.get_connection()
    try:
        try:
            result = capital_config.activate_risk_profile(conn, profile_id)
        except ValueError as e:
            return jsonify({'error': str(e)}), 400
        return jsonify(result)
    finally:
        conn.close()


# --- Registro de Estrategias / Motor de Riesgo (ver OBJETIVO_MARCO.md, plan ----
# "grado-fondo" 2026-10-20/21) -- solo lectura del ultimo estado ya persistido
# + 2 acciones humanas (cambiar modo de automatizacion, promover a live).
# NUNCA dispara una re-simulacion desde un click (ver portfolio/
# capital_allocator.py/strategy_health.py, pensados para cron, tardan
# minutos) -- mismo criterio ya declarado en run_suggested_allocation_report.

@bp.route('/strategies')
def list_strategies():
    conn = db.get_connection()
    try:
        return jsonify({
            'strategies': strategy_registry.list_strategies(conn),
            'circuit_breaker': risk_engine.check_circuit_breaker(conn),
        })
    finally:
        conn.close()


@bp.route('/strategies/<name>/automation-mode', methods=['POST'])
def set_strategy_automation_mode(name):
    data = request.get_json(silent=True) or {}
    mode = data.get('mode')
    if not mode:
        return jsonify({'error': "falta 'mode'"}), 400
    conn = db.get_connection()
    try:
        try:
            result = strategy_registry.set_automation_mode(conn, name, mode)
        except ValueError as e:
            return jsonify({'error': str(e)}), 400
        return jsonify(result)
    finally:
        conn.close()


@bp.route('/strategies/<name>/promote-to-live', methods=['POST'])
def promote_strategy_to_live(name):
    conn = db.get_connection()
    try:
        try:
            result = strategy_registry.promote_to_live(conn, name)
        except ValueError as e:
            return jsonify({'error': str(e)}), 400
        return jsonify(result)
    finally:
        conn.close()


@bp.route('/strategies/<name>/paper-trading/start', methods=['POST'])
def start_strategy_paper_trading(name):
    conn = db.get_connection()
    try:
        try:
            result = strategy_registry.start_paper_trading(conn, name)
        except ValueError as e:
            return jsonify({'error': str(e)}), 400
        return jsonify(result)
    finally:
        conn.close()


@bp.route('/strategies/<name>/paper-trading/pause', methods=['POST'])
def toggle_strategy_paper_trading_pause(name):
    data = request.get_json(silent=True) or {}
    paused = data.get('paused')
    if paused is None:
        return jsonify({'error': "falta 'paused' (true/false)"}), 400
    conn = db.get_connection()
    try:
        try:
            fn = strategy_registry.pause_paper_trading if paused else strategy_registry.resume_paper_trading
            result = fn(conn, name)
        except ValueError as e:
            return jsonify({'error': str(e)}), 400
        return jsonify(result)
    finally:
        conn.close()


#  --- Paper Trading / dinero ficticio (ver portfolio/paper_trading.py) ---------

@bp.route('/paper-trading/settings', methods=['GET', 'PUT'])
def paper_trading_settings():
    """GET/PUT del flag paper_trading_enabled y el capital virtual de
    arranque -- mismo criterio que /satellite-exit-params: PUT puede mandar
    uno solo de los dos campos, nunca pisa el que no vino."""
    conn = db.get_connection()
    try:
        if request.method == 'PUT':
            data = request.get_json(silent=True) or {}
            if 'enabled' in data:
                paper_trading.set_enabled(conn, bool(data['enabled']))
            if 'initial_capital_usd' in data:
                try:
                    paper_trading.set_initial_capital_usd(conn, data['initial_capital_usd'])
                except ValueError as e:
                    return jsonify({'error': str(e)}), 400
        return jsonify({
            'enabled': paper_trading.is_enabled(conn),
            'initial_capital_usd': paper_trading.get_initial_capital_usd(conn),
        })
    finally:
        conn.close()


def _paper_positions_with_live_prices(conn):
    """Posiciones abiertas + precio actual y pnl no realizado -- mismo
    criterio de precio que el scanner intradia (portfolio/monitor.py), pero
    solo para los simbolos con una posicion abierta (nunca recorre todo el
    universo en un request de pantalla)."""
    positions = paper_trading.list_open_positions(conn)
    pairs = {(p['symbol'], p['asset_type']) for p in positions}
    prices = monitor.current_prices_for_symbols(conn, pairs)
    out = []
    for p in positions:
        p = dict(p)
        price = prices.get(p['symbol'])
        if price is not None:
            p['current_price_usd'], p['current_price_ars'] = price
            p['unrealized_pnl_usd'] = round((price[0] - p['entry_price_usd']) * p['quantity'], 2)
            p['unrealized_pnl_pct'] = round((price[0] / p['entry_price_usd'] - 1) * 100, 2)
        else:
            p['current_price_usd'] = p['current_price_ars'] = None
            p['unrealized_pnl_usd'] = p['unrealized_pnl_pct'] = None
        out.append(p)
    return out


@bp.route('/paper-trading/positions')
def paper_trading_positions():
    conn = db.get_connection()
    try:
        return jsonify(_paper_positions_with_live_prices(conn))
    finally:
        conn.close()


@bp.route('/paper-trading/trades')
def paper_trading_trades():
    conn = db.get_connection()
    try:
        limit = request.args.get('limit', default=100, type=int)
        return jsonify(paper_trading.list_closed_trades(conn, limit=limit))
    finally:
        conn.close()


@bp.route('/paper-trading/summary')
def paper_trading_summary():
    """Patrimonio virtual total, resultado del día y resumen de
    trades/posiciones -- misma funcion que arma el Reporte de Cierre de
    Rueda de Telegram (ver portfolio/paper_trading.py::compute_daily_report),
    para que la pantalla web y el bot nunca muestren numeros distintos."""
    conn = db.get_connection()
    try:
        positions = paper_trading.list_open_positions(conn)
        pairs = {(p['symbol'], p['asset_type']) for p in positions}
        prices = monitor.current_prices_for_symbols(conn, pairs)
        return jsonify(paper_trading.compute_daily_report(conn, prices))
    finally:
        conn.close()


# --- Cuentas de broker externas (Multi-Broker, ver conversacion) -------------
# MultiBrokerManager ya enmascara api_key/api_secret (ver
# broker_client.py::mask_account) antes de devolverlos -- estas rutas NUNCA
# llaman a db.list_broker_accounts/db.get_broker_account directo, siempre a
# traves del manager, para que un secreto completo no se filtre a un log de
# acceso HTTP ni a la pantalla de Ajustes.

_broker_manager = broker_client.MultiBrokerManager()


@bp.route('/broker-accounts', methods=['GET', 'POST'])
def broker_accounts():
    conn = db.get_connection()
    try:
        if request.method == 'POST':
            data = request.get_json(silent=True) or {}
            try:
                account = _broker_manager.add_account(
                    conn,
                    broker_name=data.get('broker_name'),
                    alias=data.get('alias'),
                    api_key=data.get('api_key'),
                    api_secret=data.get('api_secret'),
                    account_number=data.get('account_number'),
                    is_active=data.get('is_active', True),
                    is_testnet=data.get('is_testnet', False),
                )
            except (ValueError, KeyError) as e:
                return jsonify({'error': str(e)}), 400
            return jsonify(account)
        return jsonify(_broker_manager.list_accounts(conn))
    finally:
        conn.close()


@bp.route('/broker-accounts/<int:account_id>', methods=['GET', 'PUT', 'DELETE'])
def broker_account_detail(account_id):
    conn = db.get_connection()
    try:
        existing = db.get_broker_account(conn, account_id)
        if not existing:
            return jsonify({'error': 'Cuenta de broker no encontrada'}), 404
        if request.method == 'DELETE':
            _broker_manager.delete_account(conn, account_id)
            return jsonify({'success': True})
        if request.method == 'GET':
            return jsonify(broker_client.mask_account(existing))
        data = request.get_json(silent=True) or {}
        fields = {}
        for key in ('broker_name', 'alias', 'api_key', 'api_secret', 'account_number', 'is_active', 'is_testnet'):
            if key in data:
                fields[key] = data[key]
        try:
            updated = _broker_manager.update_account(conn, account_id, **fields)
        except ValueError as e:
            return jsonify({'error': str(e)}), 400
        return jsonify(updated)
    finally:
        conn.close()


@bp.route('/broker-accounts/<int:account_id>/toggle-active', methods=['POST'])
def broker_account_toggle_active(account_id):
    conn = db.get_connection()
    try:
        existing = db.get_broker_account(conn, account_id)
        if not existing:
            return jsonify({'error': 'Cuenta de broker no encontrada'}), 404
        updated = _broker_manager.set_active(conn, account_id, not existing['is_active'])
        return jsonify(updated)
    finally:
        conn.close()


@bp.route('/broker-accounts/<int:account_id>/test-connection', methods=['POST'])
def broker_account_test_connection(account_id):
    conn = db.get_connection()
    try:
        return jsonify(_broker_manager.test_connection(conn, account_id))
    finally:
        conn.close()


@bp.route('/broker-accounts/unified-balance')
def broker_accounts_unified_balance():
    conn = db.get_connection()
    try:
        return jsonify(_broker_manager.get_unified_balance(conn))
    finally:
        conn.close()


@bp.route('/broker-accounts/unified-holdings')
def broker_accounts_unified_holdings():
    conn = db.get_connection()
    try:
        return jsonify(_broker_manager.get_unified_holdings(conn))
    finally:
        conn.close()


@bp.route('/settings/telegram/status')
def telegram_bot_status():
    """Estado del bot (ver conversacion) -- nunca expone el token completo,
    solo si esta configurado (ultimos 4 caracteres) y si el hilo de polling
    esta corriendo. Import de telegram_bot DIFERIDO (dentro de la funcion,
    no al tope del archivo): ese modulo importa este archivo
    (portfolio.routes, para _compute_ganancia/_compute_currency_exposure) --
    un import circular al tope de ESTE archivo rompería el arranque de Flask."""
    from portfolio import telegram_bot
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    return jsonify({
        'configured': bool(token),
        'token_suffix': token[-4:] if token else None,
        'running': telegram_bot._application is not None,
    })


@bp.route('/settings/telegram/test-message', methods=['POST'])
def telegram_test_message():
    """Manda un mensaje de prueba a TODOS los usuarios autorizados (ver
    portfolio/telegram_bot.py::send_test_message -- HTTP directo a la API de
    Telegram, nunca via el Application que ya esta en polling). Devuelve el
    detalle por usuario, nunca un generico 'listo' que oculte un fallo
    puntual (ej. alguien bloqueo el chat con el bot)."""
    from portfolio import telegram_bot
    conn = db.get_connection()
    try:
        users = db.list_telegram_allowed_users(conn)
    finally:
        conn.close()
    if not users:
        return jsonify({'error': 'Todavía no hay usuarios autorizados para probar.'}), 400
    results = []
    for user in users:
        ok, motivo = telegram_bot.send_test_message(user['chat_id'])
        results.append({'chat_id': user['chat_id'], 'label': user['label'], 'ok': ok, 'detail': motivo})
    return jsonify({'results': results})


@bp.route('/settings/telegram/test-pdf-report', methods=['POST'])
def telegram_test_pdf_report():
    """Boton "Enviar Reporte de Prueba Ahora" (ver conversacion) -- genera el
    PDF con la config ACTUAL de app_settings y lo manda YA a todos los
    usuarios autorizados via telegram_bot.send_pdf_report_now (HTTP directo,
    mismo criterio que telegram_test_message de arriba). NO actualiza
    pdf_report_last_sent_morning/afternoon -- un test manual no debe bloquear
    el envio automatico programado de ese dia."""
    from portfolio import telegram_bot
    conn = db.get_connection()
    try:
        result = telegram_bot.send_pdf_report_now(conn)
    finally:
        conn.close()
    return jsonify(result)


@bp.route('/reports/pdf/download')
def pdf_report_download():
    """Descarga directa del Reporte PDF desde la web (ver conversación/
    auditoría 19/10/2026, hallazgo 2.6: "no hay vista previa ni descarga,
    solo envío por Telegram") -- misma generación que usa el envío por
    Telegram (pdf_report.generate_portfolio_pdf con la config ACTUAL de
    app_settings), sin depender de tener Telegram configurado. Puede tardar
    ~1 minuto (arma gráficos + tablas con reportlab), el frontend avisa eso
    mientras espera."""
    from portfolio import pdf_report
    conn = db.get_connection()
    try:
        pdf_bytes = pdf_report.generate_portfolio_pdf(conn)
    except Exception as e:
        return jsonify({'error': f'Fallo la generación del PDF: {e}'}), 500
    finally:
        conn.close()
    filename = f"kronos_reporte_{today_ar().isoformat()}.pdf"
    return Response(
        pdf_bytes, mimetype='application/pdf',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'},
    )


# --- Explorador BYMA ------------------------------------------------------------

@bp.route('/explorer/search')
def explorer_search():
    query = request.args.get('q', '')
    type_filter = request.args.get('type')
    limit = request.args.get('limit', default=20, type=int)
    try:
        results = byma_universe.search(query, type_filter=type_filter, limit=limit)
    except Exception as e:
        return jsonify({'error': f'data912 no respondió: {e}'}), 502

    conn = db.get_connection()
    try:
        results = byma_universe.augment_with_catalog_name_matches(conn, results, query, type_filter, limit)
    finally:
        conn.close()
    return jsonify(results)


@bp.route('/explorer/forecast', methods=['POST'])
def explorer_forecast():
    data = request.get_json()
    symbol = (data.get('symbol') or '').strip().upper()
    asset_type = data.get('type')
    if not symbol or not asset_type:
        return jsonify({'error': 'symbol y type son requeridos'}), 400

    forecast_id = forecast_worker.submit_forecast(
        instrument_id=None, symbol=symbol, market='byma', asset_type=asset_type,
        sample_count=data.get('sample_count'),
    )
    conn = db.get_connection()
    try:
        row = db.get_forecast(conn, forecast_id)
    finally:
        conn.close()
    return jsonify(_forecast_response(row)), 202
