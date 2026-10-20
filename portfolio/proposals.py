"""Motor de propuestas (Fase 8): evalua 4 fuentes, en orden de confianza, y
persiste cada propuesta nueva/vigente en la tabla `proposals` -- nunca ejecuta
nada solo, "Aceptar" solo precarga el formulario de transaccion (ver
transactions.js del lado frontend). Orden de confianza (de mas a menos cierto):

1. Reglas del usuario (stop-loss/take-profit ya tocado por el precio).
2. Eventos deterministicos (vencimiento de bono/LECAP, caucion vencida sin renovar).
3. Rebalanceo (desvio de target_allocation mas alla de un umbral configurable).
4. Señales estadisticas YA VALIDADAS (Kronos validado -- origen 'senal_kronos',
   o tecnico con edge -- origen 'senal_tecnica') -- nunca una señal sin
   validar, por mas "razonable" que parezca (mismo criterio que ya aplica
   Señales/Posiciones desde Fase 2/5). Son 2 origenes DISTINTOS a proposito
   (ver auditoria 19/10/2026, hallazgo 1.3): sync_proposals dedupea por
   (symbol, origen), y Kronos/RSI pueden validarse para el mismo instrumento
   el mismo dia apuntando a direcciones opuestas -- con un origen compartido,
   la segunda señal pisaba el texto de la primera sin actualizar su
   tipo_sugerido, dejando una propuesta con la etiqueta de una señal y el
   texto de la otra, ademas de duplicar la fila.

Las lineas de tendencia (ver trendlines.py) NO entran a este motor: una
ruptura de soporte/resistencia es un evento informativo, no una propuesta,
porque no hay backtest propio que mida si esa señal tiene edge real (mismo
principio ya aplicado al RSI: una heuristica sin medir no se trata como señal
confiable)."""
import json
from datetime import timedelta

from portfolio import accuracy, db, fixed_income
from portfolio.formatting import fmt_es
from portfolio.tz import now_ar

COOLDOWN_DAYS = 7
EVENT_HORIZON_DAYS = 7

# Mismo umbral que technical-signal.js::EDGE_SIGNIFICANT_PTS -- duplicado
# deliberado del lado servidor porque hoy no hay un signal_config compartido
# para esto (mismo patron ya aceptado en market-hours.js vs. quote_worker.py).
EDGE_SIGNIFICANT_PTS = 5

TYPE_LABELS = {
    'accion': 'Acciones', 'cedear': 'CEDEARs', 'bono': 'Bonos', 'on': 'Obligaciones Negociables',
    'fondo': 'Fondos', 'moneda': 'Moneda', 'caucion': 'Cauciones', 'caucion_vencida': 'Cauciones vencidas',
}


def _sl_tp_candidates(conn, holdings_native):
    """Compara contra el precio NATIVO (quotes_latest crudo), nunca contra un
    valor ya convertido a la moneda de exhibicion -- ver la nota de
    evaluate_all. `nivel` tambien esta en moneda nativa (ver schema.sql)."""
    alerts = db.list_price_alerts(conn, activa=True)
    if not alerts:
        return []
    by_id = {h['instrument']['id']: h['instrument'] for h in holdings_native}
    candidates = []
    for a in alerts:
        instrument = by_id.get(a['instrument_id']) or db.get_instrument(conn, a['instrument_id'])
        if not instrument:
            continue
        quote = db.get_quote(conn, a['instrument_id'])
        price = quote['price'] if quote else None
        if price is None:
            continue
        crossed = (a['tipo'] == 'sl' and price <= a['nivel']) or (a['tipo'] == 'tp' and price >= a['nivel'])
        if not crossed:
            continue
        label = 'Stop-loss' if a['tipo'] == 'sl' else 'Take-profit'
        candidates.append({
            'origen': 'sl_tp',
            'symbol': instrument['symbol'],
            'tipo_sugerido': 'venta',
            'texto': (
                f"{label} de {instrument['symbol']} tocado: precio {fmt_es(price)} "
                f"vs. nivel {fmt_es(a['nivel'])} ({instrument['currency']})."
            ),
            'evidencia': f"Alerta #{a['id']} configurada por vos, activa desde {a['created_at']}.",
        })
    return candidates


def _event_candidates(conn, instruments):
    candidates = []
    events = fixed_income.upcoming_fixed_income_events(instruments, horizon_days=EVENT_HORIZON_DAYS)
    seen = set()
    for e in events:
        if e['kind'] != 'vencimiento' or e['symbol'] in seen:
            continue
        seen.add(e['symbol'])
        candidates.append({
            'origen': 'evento',
            'symbol': e['symbol'],
            'tipo_sugerido': None,
            'texto': f"{e['symbol']} vence en {e['dias']} día{'s' if e['dias'] != 1 else ''} ({e['date']}) -- decidí si reinvertís el capital.",
            'evidencia': "Calendario de renta fija (vencimientos próximos).",
        })
    # Nota: ya no se genera una propuesta por "caución vencida sin renovar".
    # Confirmado contra datos reales del broker (datos_broker/): el broker NO
    # cuenta una caución vencida como posición -- esa plata ya se convirtió en
    # otra cosa (retiro, reinversión, nueva caución) apenas vence, es el ciclo
    # de vida normal del instrumento, no un evento que requiera una decisión.
    return candidates


def _rebalance_candidates(conn, holdings_display, cauciones, config, display_currency='ARS'):
    """holdings_display YA paso por _apply_display_currency -- las magnitudes
    tienen que estar en una unica moneda comparable para que sumar/dividir de
    sentido (mismo criterio que buildAllocationSegments del lado frontend).
    display_currency es SOLO para que el texto diga en qué moneda está el
    monto sugerido (ver conversación/auditoría, hallazgo 3: "'Bonos: comprar
    aprox. 577' sin unidades" -- era un monto de dinero sin la moneda al
    lado, fácil de confundir con una cantidad de nominales)."""
    targets = db.list_target_allocation(conn)
    if not targets:
        return []
    threshold = config['proposals_rebalance_threshold_pct']

    by_type = {}
    for h in holdings_display:
        if h['valor_actual'] is None:
            continue
        t = h['instrument']['type']
        by_type[t] = by_type.get(t, 0) + h['valor_actual']
    # Solo vigentes -- una vencida ya no es capital en caución (ver nota en
    # evaluate_all), mismo criterio que buildAllocationSegments del frontend.
    for c in cauciones:
        if c['vencido']:
            continue
        by_type['caucion'] = by_type.get('caucion', 0) + c['valor_actual']
    total = sum(by_type.values())
    if not total:
        return []

    candidates = []
    for type_, target_pct in targets.items():
        actual_pct = by_type.get(type_, 0) / total * 100
        desvio = actual_pct - target_pct
        if abs(desvio) < threshold:
            continue
        monto = abs(desvio) / 100 * total
        label = TYPE_LABELS.get(type_, type_)
        accion = 'venta' if desvio > 0 else 'compra'
        candidates.append({
            'origen': 'rebalanceo',
            'symbol': label,
            'tipo_sugerido': accion,
            'texto': (
                f"{label}: objetivo {fmt_es(target_pct, decimals=0)}%, actual {fmt_es(actual_pct, decimals=1)}% "
                f"(desvío {fmt_es(desvio, decimals=1, signed=True)}pp) -> "
                f"{('vender' if accion == 'venta' else 'comprar')} aprox. {display_currency} {fmt_es(monto, decimals=0)}."
            ),
            'evidencia': f"Asignación objetivo configurada en Preferencias; umbral {fmt_es(threshold, decimals=0)}pp.",
        })
    return candidates


def _validated_signal_candidates(conn, holdings_native, config):
    """Nunca una señal sin validar. El check de Kronos y el tecnico-con-edge
    reusan las MISMAS cachés diarias que ya paga Resumen/Dashboard
    (_get_cached_accuracy, _get_technical_row -- Fase 9/10, ver routes.py) en
    vez de recalcular chart+backtest por holding en cada GET a este endpoint
    -- antes esto era el costo real detras de "Propuestas carga lento" (ver
    conversación): recalculaba todo de cero en cada entrada a la pestaña,
    aunque Resumen ya lo hubiera calculado minutos antes para el mismo dia."""
    from portfolio.routes import _get_cached_accuracy, _get_technical_row  # import tardío: evita ciclo routes<->proposals

    candidates = []
    for h in holdings_native:
        instrument = h['instrument']
        if instrument['type'] in ('fondo', 'cripto'):
            continue
        instrument_id = instrument['id']

        acc = _get_cached_accuracy(conn, instrument_id)
        validation = accuracy.compute_signal_validation(acc, config)
        if validation.get('validated'):
            forecast_row = db.get_latest_forecast(conn, instrument_id=instrument_id)
            if forecast_row and forecast_row['status'] == 'done':
                result = json.loads(forecast_row['result_json'])
                signal = result.get('signal')
                if signal and signal != 'mantener':
                    # 'senal_kronos', NUNCA 'senal' a secas (ver auditoria
                    # 19/10/2026, hallazgo 1.3): sync_proposals dedupea por
                    # (symbol, origen) -- con el mismo origen que el candidato
                    # tecnico de mas abajo, un instrumento con AMBAS señales
                    # vigentes el mismo dia (ej. Kronos compra + RSI vende)
                    # producia 2 filas con la misma key, y en corridas
                    # siguientes una pisaba el texto/evidencia de la otra sin
                    # tocar su tipo_sugerido -- la propuesta quedaba con la
                    # etiqueta de una señal y el texto de la otra (bug real
                    # reproducido con IBM: etiqueta VENDER, texto "sugiere
                    # comprar"), ademas de la fila duplicada en si.
                    candidates.append({
                        'origen': 'senal_kronos',
                        'symbol': instrument['symbol'],
                        'tipo_sugerido': signal,
                        'texto': f"Kronos validado sugiere {signal} en {instrument['symbol']}.",
                        'evidencia': validation['reason'],
                    })

        technical = _get_technical_row(conn, instrument, config)
        tecnico, edge, n = technical['tecnico'], technical['edge'], technical['n']
        if not tecnico or tecnico == 'mantener':
            continue
        # Edge NEGATIVO (no solo "no significativo"): seguir historicamente
        # esta señal salio peor que la tasa base -- no es una sugerencia
        # invertida valida (eso apostaria a una anti-correlacion nunca medida),
        # es simplemente "no hay evidencia a favor de actuar". Solo se propone
        # cuando el edge es positivo Y significativo.
        if edge is None or edge < EDGE_SIGNIFICANT_PTS:
            continue
        candidates.append({
            # 'senal_tecnica', distinto del candidato Kronos de arriba --
            # ver comentario en ese bloque (hallazgo 1.3 de la auditoria).
            'origen': 'senal_tecnica',
            'symbol': instrument['symbol'],
            'tipo_sugerido': 'compra' if tecnico == 'comprar' else 'venta',
            'texto': f"RSI técnico sugiere {tecnico} en {instrument['symbol']}: edge {fmt_es(edge, signed=True)}pts sobre {n} casos.",
            'evidencia': f"Backtest RSI vs. tasa base: edge {fmt_es(edge, signed=True)} puntos sobre {n} señales pasadas.",
        })
    return candidates


def evaluate_all(conn):
    """Arma las 4 listas de candidatos. CRITICO (ver lección de Fase 7): cada
    fuente recibe la vista de holdings en la moneda que su matemática
    necesita -- SL/TP y señales comparan contra el precio NATIVO (quotes_latest
    crudo), rebalanceo compara % sobre una única moneda comparable (la de
    exhibición elegida en Preferencias). Nunca se reusa una lista ya mutada
    por _apply_display_currency para un cálculo que esperaba el valor nativo."""
    from portfolio.routes import _apply_display_currency  # import tardío: evita ciclo routes<->proposals

    settings = db.get_app_settings(conn)
    config = db.get_signal_config(conn)
    holdings_native = db.get_holdings_with_quotes(conn)
    cauciones = db.get_cauciones_resumen(conn)
    instruments = db.list_instruments(conn)

    # Copia superficial antes de convertir: _apply_display_currency muta
    # valor_actual/precio_actual/etc. IN PLACE -- sin esta copia, holdings_native
    # quedaría con los mismos valores ya convertidos (el bug real de Fase 7).
    holdings_display = [dict(h, instrument=dict(h['instrument'])) for h in holdings_native]
    holdings_display = _apply_display_currency(holdings_display, settings)

    candidates = []
    candidates += _sl_tp_candidates(conn, holdings_native)
    candidates += _event_candidates(conn, instruments)
    candidates += _rebalance_candidates(conn, holdings_display, cauciones, config, settings.get('display_currency', 'ARS'))
    candidates += _validated_signal_candidates(conn, holdings_native, config)
    return candidates


def sync_proposals(conn):
    """Corre el motor y reconcilia contra lo ya persistido: una propuesta
    nueva/vista cuya condición ya no se cumple pasa a 'expirada' -- aceptada/
    descartada quedan como están (el usuario ya actuó, no se le vuelve a
    insistir con la misma)."""
    candidates = evaluate_all(conn)
    now = now_ar().replace(tzinfo=None)
    existing = db.list_proposals(conn)
    active_by_key = {(p['symbol'], p['origen']): p for p in existing if p['estado'] in ('nueva', 'vista')}

    # Cooldown real: antes se grababa cooldown_until al crear una propuesta
    # pero nunca se leia -- una recien descartada podia reaparecer en la
    # proxima sincronizacion (bug reportado en la auditoria). Por cada
    # (symbol, origen), la descartada mas reciente con cooldown vigente
    # bloquea que se vuelva a crear una igual hasta que pase esa fecha.
    cooldown_by_key = {}
    for p in existing:
        if p['estado'] != 'descartada' or not p['cooldown_until']:
            continue
        key = (p['symbol'], p['origen'])
        if key not in cooldown_by_key or p['created_at'] > cooldown_by_key[key]['created_at']:
            cooldown_by_key[key] = p

    seen_keys = set()
    for c in candidates:
        key = (c['symbol'], c['origen'])
        seen_keys.add(key)
        if key in active_by_key:
            p = active_by_key[key]
            if p['texto'] != c['texto'] or p['evidencia'] != c['evidencia']:
                db.update_proposal(conn, p['id'], texto=c['texto'], evidencia=c['evidencia'])
            continue
        cooldown = cooldown_by_key.get(key)
        if cooldown and cooldown['cooldown_until'] > now.isoformat():
            continue
        db.add_proposal(
            conn, origen=c['origen'], symbol=c['symbol'], texto=c['texto'],
            evidencia=c['evidencia'], tipo_sugerido=c.get('tipo_sugerido'),
            cooldown_until=(now + timedelta(days=COOLDOWN_DAYS)).isoformat(),
        )

    for key, p in active_by_key.items():
        if key not in seen_keys:
            db.update_proposal(conn, p['id'], estado='expirada')

    return db.list_proposals(conn)
