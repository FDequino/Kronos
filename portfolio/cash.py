"""Cuenta de caja real (Fase 10). Antes de esto, compras/ventas/cauciones/
plazos fijos eran eventos aislados sin contrapartida de efectivo en ningun
lado -- este modulo reconstruye retroactivamente todo el historial como una
cuenta de caja por moneda (ARS/USD, separadas: no se asume ningun tipo de
cambio para mezclarlas), y expone el saldo actual + el historial.

Reconstruccion, no inferencia de aportes reales: el saldo inicial (antes de
la primera operacion) se asume 0. Si en algun punto el saldo reconstruido
daria negativo (hubo un aporte externo que no quedo registrado en ningun
lado -- plazos_fijos/cauciones/transactions no tienen forma de decir "esta
plata vino de afuera"), se inserta un 'aporte_inferido' por el monto exacto
que falta para no quedar en negativo, justo antes de la operacion que lo
hubiera dejado asi. Queda marcado como INFERIDO (no como aporte real
confirmado) -- nunca se inventa silenciosamente que fue un aporte "normal"."""
from portfolio import db, tz

# IVA sobre comisiones de mercado (Argentina): confirmado contra boletos
# reales en datos_broker/Boletos.xlsx (ej. SNDK: comision $4.463,87, iva real
# $937,42 -- exactamente 21,00%, mismo ratio en otros ~10 boletos chequeados).
# transactions/cauciones solo guardan la comision SIN iva (nunca se modelo
# en ningun lado del proyecto hasta ahora) -- sin sumar esto, la caja
# reconstruida quedaba sistematicamente de mas (~21% de cada comision
# "perdido" de la cuenta pero nunca restado del modelo).
IVA_SOBRE_COMISION = 0.21


def _transaction_events(conn):
    events = []
    for t in db.list_transactions(conn):
        instrument = db.get_instrument(conn, t["instrument_id"])
        if not instrument:
            continue
        moneda = instrument["currency"]
        bruto = t["cantidad"] * t["precio"]
        costo = t["comision"] * (1 + IVA_SOBRE_COMISION)
        if t["tipo"] == "compra":
            monto = -(bruto + costo)
            origen = "compra"
        else:
            monto = bruto - costo
            origen = "venta"
        events.append((t["fecha"], moneda, monto, origen, "transactions", t["id"]))
    return events


def _caucion_events(conn):
    events = []
    for c in db.list_cauciones(conn):
        # monto_futuro es BRUTO (convencion ya establecida para TNA/devengado,
        # ver schema.sql) -- el neto real que efectivamente entra a la caja
        # descuenta comision + iva, igual que las transacciones.
        neto_vencimiento = c["monto_futuro"] - c["comision"] * (1 + IVA_SOBRE_COMISION)
        events.append((c["fecha_apertura"], c["moneda"], -c["monto_contado"], "caucion_apertura", "cauciones", c["id"]))
        events.append((c["fecha_vencimiento"], c["moneda"], neto_vencimiento, "caucion_vencimiento", "cauciones", c["id"]))
    return events


def _plazo_fijo_events(conn):
    # plazos_fijos no tiene campo de moneda (asume ARS, mismo criterio ya
    # usado en el resto de la app -- ver bug reportado en la auditoria,
    # fuera de alcance de esta fase agregar el campo).
    events = []
    for pf in db.list_plazos_fijos(conn):
        events.append((pf["fecha_inicio"], "ARS", -pf["monto"], "plazo_fijo_apertura", "plazos_fijos", pf["id"]))
        if pf["estado"] == "cancelado":
            continue  # retiro anticipado -- no sabemos el monto real de rescate, no se inventa
        monto_vencimiento = pf["monto"] * (1 + pf["tna"] * pf["plazo_dias"] / 365)
        events.append((pf["fecha_vencimiento"], "ARS", monto_vencimiento, "plazo_fijo_vencimiento", "plazos_fijos", pf["id"]))
    return events


def _renta_dividendo_events(conn):
    events = []
    for r in db.list_rentas_dividendos(conn):
        events.append((r["fecha"], r["moneda"], r["neto"], r["tipo"], "rentas_dividendos", r["id"]))
    return events


def _loan_events(conn):
    events = []
    for loan in db.list_loans(conn):
        events.append((loan["fecha_inicio"], loan["moneda"], -loan["capital"], "prestamo_otorgado", "loans", loan["id"]))
        for p in db.list_loan_payments(conn, loan["id"]):
            monto = p["capital"] + p["interes"]
            if monto:
                events.append((p["fecha"], loan["moneda"], monto, "prestamo_cobro", "loan_payments", p["id"]))
    return events


def backfill_cash_movements(conn):
    """Recalcula cash_movements desde cero a partir de lo que ya existe en
    transactions/cauciones/plazos_fijos/rentas_dividendos/loans -- se puede
    re-correr las veces que haga falta (ej. despues de cargar una
    transaccion vieja): primero borra todo lo generado automaticamente,
    nunca toca aportes/retiros/transferencias ya cargados (a mano o desde
    datos reales del broker, ver datos_broker/) -- esos se tratan como
    hechos confirmados y se tienen en cuenta al reconstruir el saldo, pero
    no se vuelven a insertar."""
    manuales = db.list_cash_movements(conn)  # sobreviven a clear_cash_movements
    db.clear_cash_movements(conn)

    manual_events = [
        (m["fecha"], m["moneda"], m["monto"], m["origen"], None, None) for m in manuales
    ]
    auto_events = (
        _transaction_events(conn) + _caucion_events(conn) + _plazo_fijo_events(conn)
        + _renta_dividendo_events(conn) + _loan_events(conn)
    )
    events = [(*e, True) for e in auto_events] + [(*e, False) for e in manual_events]
    events.sort(key=lambda e: (e[0], e[3]))

    saldo = {"ARS": 0.0, "USD": 0.0}
    inferidos = []
    for fecha, moneda, monto, origen, tabla, ref_id, necesita_insert in events:
        if saldo[moneda] + monto < -0.01:  # tolerancia de centavos por redondeo
            faltante = -(saldo[moneda] + monto)
            db.add_cash_movement(
                conn, fecha=fecha, moneda=moneda, monto=faltante, origen="aporte_inferido",
                notas="Generado por el backfill: sin este monto, la caja reconstruida quedaba en negativo -- "
                      "significa que hubo un aporte externo no registrado en ningun otro lado. Confirmá o corregí el monto real si lo sabés.",
            )
            saldo[moneda] += faltante
            inferidos.append({"fecha": fecha, "moneda": moneda, "monto": round(faltante, 2)})

        if necesita_insert:
            db.add_cash_movement(
                conn, fecha=fecha, moneda=moneda, monto=monto, origen=origen,
                referencia_tabla=tabla, referencia_id=ref_id,
            )
        saldo[moneda] += monto

    return {"saldo_final": saldo, "aportes_inferidos": inferidos}


def get_cash_balance(conn, as_of_date=None):
    """Saldo actual (o a una fecha pasada) por moneda, sumando todos los
    movimientos -- ARS y USD nunca se mezclan ni se convierten aca."""
    movimientos = db.list_cash_movements(conn)
    saldo = {"ARS": 0.0, "USD": 0.0}
    for m in movimientos:
        if as_of_date and m["fecha"] > as_of_date:
            continue
        saldo[m["moneda"]] += m["monto"]
    return saldo


def external_cash_flows(conn, moneda=None):
    """Los unicos movimientos que importan para XIRR: aporte/retiro/
    aporte_inferido/transferencia son plata que CRUZA el limite de la
    cuenta -- compra/venta/dividendo/etc. son movimientos INTERNOS, no
    cambian cuanto pusiste vos (ver xirr.py)."""
    EXTERNAL = ("aporte", "retiro", "aporte_inferido", "transferencia")
    movimientos = db.list_cash_movements(conn, moneda=moneda)
    return [m for m in movimientos if m["origen"] in EXTERNAL]


def loan_capital_flows(conn, moneda=None):
    """El capital de un prestamo (Fase 10 + direccion) es, para patrimonio/
    ganancia/XIRR, un movimiento externo igual de real que un aporte/retiro a
    la cuenta del broker -- aunque no pase por cash_movements (ese ledger es
    solo de la cuenta de NH Capital, y los prestamos a terceros son plata que
    puede salir/entrar de cualquier lado).

    Dos flujos por prestamo, no uno: el capital ORIGINAL en fecha_inicio
    (equivalente a un aporte si 'prestado' -- saliste de tu bolsillo --, o a
    un retiro si 'recibido' -- entro plata de afuera) MAS un flujo opuesto en
    la fecha real de cada loan_payment.capital (la devolucion/el pago de
    capital que efectivamente se registro). Sin el segundo flujo, el capital
    YA COBRADO/PAGADO se veria como ganancia o perdida pura en vez de neutro
    (bug real: un prestamo con una cuota de capital ya cobrada inflaba
    ganancia_total exactamente por el monto de esa cuota). Con ambos flujos,
    la suma neta de este prestamo da siempre exacto capital_pendiente (con
    signo), nunca el capital total original -- consistente con que eso es lo
    unico que sigue "afuera" de tu bolsillo hoy."""
    rows = db.list_loans(conn)
    if moneda:
        rows = [r for r in rows if r["moneda"] == moneda]
    flows = []
    for loan in rows:
        signo = 1 if loan["direccion"] == "prestado" else -1
        flows.append({"fecha": loan["fecha_inicio"], "monto": signo * loan["capital"]})
        for payment in db.list_loan_payments(conn, loan["id"]):
            if payment["capital"]:
                flows.append({"fecha": payment["fecha"], "monto": -signo * payment["capital"]})
    return flows


# --- Import del extracto real del broker ----------------------------------
#
# backfill_cash_movements() de arriba DERIVA el flujo de caja a partir de
# transactions/cauciones/etc -- funciona, pero depende de que esos datos
# esten completos y de aproximaciones (IVA, bruto vs. neto) que no siempre
# cierran exacto contra la realidad (confirmado: con todas las fuentes
# cruzadas igual quedaba una diferencia de ~$568.000 sin explicar). El
# broker expone su PROPIO extracto de cuenta (uno por moneda: "Cuenta $",
# "Cuenta U$S", "Cuenta USD" -- local/exterior en USD van separados), con
# el neto YA CALCULADO por el broker para cada movimiento y un saldo
# corrido que confirma la cuenta completa. Si este extracto esta
# disponible, es preferible a la derivacion: es la fuente de verdad, no una
# reconstruccion.
_SKIP_OPERACIONES = ("Bloqueo Monetario por Suscripción", "Desbloqueo Monetario por Liquidación de Suscripción")


def _parse_ledger_date(s):
    d, m, y = s.split("/")
    return f"{y}-{int(m):02d}-{int(d):02d}"


def _categorize_ledger_row(operacion, descripcion):
    if operacion == "Recibo de Cobro":
        return "aporte"
    if operacion == "Comprobante de Pago":
        return "retiro"
    if operacion in ("Movimiento Manual", "Gasto de Custodia - Caja de Valores"):
        return "gasto_cuenta"
    if operacion == "Dividendo en efectivo":
        return "dividendo"
    if operacion == "Renta":
        return "renta"
    if operacion == "Amortización":
        return "amortizacion"
    if operacion == "Liquidación de Suscripción":
        return "compra"  # alta de cuotapartes de un FCI
    if operacion == "Liquidación de Rescate":
        return "venta"  # baja de cuotapartes de un FCI
    if operacion == "Boleto":
        if "Cauc Col Ctdo" in descripcion:
            return "caucion_apertura"
        if "Cauc Col Fut" in descripcion:
            return "caucion_vencimiento"
        if "VENTA" in descripcion:
            return "venta"
        if "COMPRA" in descripcion or "LICOMPRA" in descripcion:
            return "compra"
    return None  # tipo no reconocido -- se reporta, nunca se inserta a ciegas


def import_broker_ledger(conn, files):
    """files: {"ARS": "ruta/Cuenta $.xlsx", "USD": ["ruta/Cuenta U$S.xlsx", "ruta/Cuenta USD.xlsx"]}
    (USD puede venir repartido en mas de un extracto -- local/exterior --
    ambos son la MISMA moneda para esta cuenta de caja, se suman).

    Reemplaza por completo los cash_movements con origen EN EL RANGO DE
    FECHAS que cubre el extracto -- fuera de ese rango (operaciones mas
    nuevas que el extracto mas reciente) se deja lo que ya hubiera
    (manual o de backfill_cash_movements), para no perder movimientos
    recientes que el extracto todavia no capturo."""
    import openpyxl

    por_moneda = {}
    for moneda, rutas in files.items():
        rutas = [rutas] if isinstance(rutas, str) else rutas
        rows = []
        for ruta in rutas:
            wb = openpyxl.load_workbook(ruta, data_only=True)
            rows.extend(list(wb.active.iter_rows(values_only=True))[1:])
        por_moneda[moneda] = rows

    sin_categoria = []
    fechas_cubiertas = {"ARS": None, "USD": None}
    eventos_por_moneda = {"ARS": [], "USD": []}

    for moneda, rows in por_moneda.items():
        for r in rows:
            _cod, _fecha_liq, fecha_concertacion, descripcion, operacion, _subtitulo, _boleto, monto, _saldo = r
            if operacion is None or any(s in operacion for s in _SKIP_OPERACIONES):
                continue
            origen = _categorize_ledger_row(operacion, descripcion or "")
            fecha = _parse_ledger_date(fecha_concertacion)
            if origen is None:
                sin_categoria.append({"fecha": fecha, "moneda": moneda, "descripcion": descripcion, "monto": monto})
                continue
            eventos_por_moneda[moneda].append((fecha, monto, origen, descripcion))
            if fechas_cubiertas[moneda] is None or fecha > fechas_cubiertas[moneda]:
                fechas_cubiertas[moneda] = fecha

    for moneda, cubierta_hasta in fechas_cubiertas.items():
        if cubierta_hasta is None:
            continue
        for m in db.list_cash_movements(conn, moneda=moneda):
            if m["fecha"] <= cubierta_hasta:
                db.delete_cash_movement(conn, m["id"])

    insertados = 0
    for moneda, eventos in eventos_por_moneda.items():
        for fecha, monto, origen, descripcion in eventos:
            db.add_cash_movement(conn, fecha=fecha, moneda=moneda, monto=monto, origen=origen, notas=f"Extracto del broker: {descripcion}")
            insertados += 1

    return {"insertados": insertados, "fechas_cubiertas": fechas_cubiertas, "sin_categoria": sin_categoria}


def recent_movements(conn, moneda="ARS", limit=5):
    """Los últimos N movimientos de caja (orden más reciente primero), para
    la lista que acompaña al gráfico de columnas en Patrimonio -- mismo dato
    crudo que ya usa get_cash_balance, solo sin agregar."""
    movimientos = db.list_cash_movements(conn, moneda=moneda)
    return list(reversed(movimientos[-limit:])) if movimientos else []


def monthly_net_flow(conn, meses=6, moneda="ARS"):
    """Ingresos y egresos de caja netos por mes calendario, para el gráfico de
    columnas apiladas de Patrimonio (Fase 15) -- separa ingreso/egreso en vez
    de un solo neto para poder mostrar las dos barras (verde/roja) por mes,
    igual que ya se hace en otros lados del dashboard con cascadas de
    ingresos/costos. Solo 'moneda' pedida, nunca se mezcla ARS/USD en una
    misma barra."""
    hoy = tz.today_ar()
    meses_labels = []
    buckets = {}
    for i in range(meses - 1, -1, -1):
        total_mes = hoy.year * 12 + (hoy.month - 1) - i
        y, m = divmod(total_mes, 12)
        key = f"{y:04d}-{m + 1:02d}"
        meses_labels.append(key)
        buckets[key] = {"ingresos": 0.0, "egresos": 0.0}

    movimientos = db.list_cash_movements(conn, moneda=moneda)
    primer_mes = meses_labels[0]
    for m in movimientos:
        key = m["fecha"][:7]
        if key < primer_mes or key not in buckets:
            continue
        if m["monto"] >= 0:
            buckets[key]["ingresos"] += m["monto"]
        else:
            buckets[key]["egresos"] += m["monto"]

    return {
        "moneda": moneda,
        "meses": meses_labels,
        "ingresos": [round(buckets[k]["ingresos"], 2) for k in meses_labels],
        "egresos": [round(buckets[k]["egresos"], 2) for k in meses_labels],
    }
