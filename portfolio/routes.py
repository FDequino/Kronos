"""Blueprint con todos los endpoints /api/portfolio/*."""
import json
import sqlite3
from datetime import datetime

from flask import Blueprint, jsonify, request

from portfolio import accuracy, byma_universe, db, fetchers, forecast_worker, history, risk

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
        for key in ('symbol', 'name', 'type', 'market', 'currency', 'price_source', 'price_source_params'):
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
                )
            except Exception as e:
                return jsonify({'error': str(e)}), 400
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
    tasa BNA elegida. pnl_pct no se toca: es un ratio, no cambia de escala."""
    target = settings["display_currency"]
    try:
        rate = fetchers.fetch_bna_rates()[settings["bna_rate_variant"]]
    except Exception:
        rate = None  # BNA no respondio -- mostramos en la moneda nativa sin convertir

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
        h["risk"] = risk.compute_instrument_risk(closes, h["instrument"]["type"], config)
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
        holdings = _attach_risk(conn, db.get_holdings_with_quotes(conn), db.get_signal_config(conn))
        holdings = [h for h in holdings if h["valor_actual"] is not None]
        portfolio = risk.compute_portfolio_risk(holdings)
        if portfolio is None:
            return jsonify(None)
        portfolio["explanation"] = risk.explain_portfolio_risk(portfolio)
        return jsonify(portfolio)
    finally:
        conn.close()


def _record_portfolio_snapshot(conn):
    """Un punto de historial real por dia, siempre en ARS -- ver schema.sql."""
    settings = db.get_app_settings(conn)
    ars_settings = {**settings, 'display_currency': 'ARS'}
    holdings = _apply_display_currency(db.get_holdings_with_quotes(conn), ars_settings)
    cauciones = db.get_cauciones_resumen(conn)

    valor_total = sum(h['valor_actual'] or 0 for h in holdings) + sum(c['valor_actual'] for c in cauciones)
    pnl_total = sum(h['pnl'] or 0 for h in holdings)

    risked = _attach_risk(conn, holdings, db.get_signal_config(conn))
    risked = [h for h in risked if h['valor_actual'] is not None]
    portfolio_risk = risk.compute_portfolio_risk(risked)

    db.upsert_portfolio_snapshot(
        conn, date=datetime.utcnow().date().isoformat(),
        valor_total=valor_total, pnl_total=pnl_total,
        risk_score=portfolio_risk['score'] if portfolio_risk else None,
    )


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


def _forecast_response(row):
    response = {k: v for k, v in row.items() if k != 'result_json'}
    response['progress_pct'] = _progress_pct(row)
    if row['status'] == 'done' and row['result_json']:
        response['result'] = json.loads(row['result_json'])
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
    finally:
        conn.close()
    return jsonify(_forecast_response(row)), 202


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
    finally:
        conn.close()
    return jsonify(_forecast_response(row) if row else None)


@bp.route('/forecasts/<int:forecast_id>')
def get_forecast(forecast_id):
    conn = db.get_connection()
    try:
        row = db.get_forecast(conn, forecast_id)
    finally:
        conn.close()
    if not row:
        return jsonify({'error': 'Forecast no encontrado'}), 404
    return jsonify(_forecast_response(row))


@bp.route('/instruments/<int:instrument_id>/accuracy')
def instrument_accuracy(instrument_id):
    conn = db.get_connection()
    try:
        instrument = db.get_instrument(conn, instrument_id)
        if not instrument:
            return jsonify({'error': 'Instrumento no encontrado'}), 404
        forecasts = db.list_forecasts_for_instrument(conn, instrument_id)
        price_history = db.get_price_history(conn, instrument_id)
        result = accuracy.compute_accuracy(forecasts, price_history)
        if result is None:
            return jsonify(None)
        if not result.get('pending'):
            result['explanation'] = accuracy.explain_accuracy(instrument['symbol'], result)
        return jsonify(result)
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
        return jsonify(config)
    finally:
        conn.close()


# --- Explorador BYMA ------------------------------------------------------------

@bp.route('/explorer/search')
def explorer_search():
    try:
        results = byma_universe.search(
            request.args.get('q', ''),
            type_filter=request.args.get('type'),
            limit=request.args.get('limit', default=20, type=int),
        )
    except Exception as e:
        return jsonify({'error': f'data912 no respondió: {e}'}), 502
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
