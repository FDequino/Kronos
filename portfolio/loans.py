"""Prestamos a terceros -- o de terceros a uno (Fase 10 + direccion): un
activo (o pasivo) mas de la cartera, con su propio plan de pagos. Mismo
principio de "nunca inventar un numero" que el resto del proyecto -- el
devengado se calcula desde el capital/tasa/plan reales, nunca se asume un
pago que todavia no se registro.
"""
import os
import uuid
from datetime import datetime

from portfolio import db
from portfolio.tz import today_ar

# Documentos (pagares, comprobantes) -- el archivo vive en disco, afuera de la
# base, al lado de portfolio.db (mismo criterio: la DB es sqlite, no blobs).
DOCUMENTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "loan_documents")


def save_document_file(loan_id, original_filename, file_stream):
    """Guarda el archivo subido con un nombre random (evita colisiones y
    sobreescrituras) pero conserva la extension original para que el
    navegador lo abra bien al descargarlo."""
    os.makedirs(DOCUMENTS_DIR, exist_ok=True)
    ext = os.path.splitext(original_filename)[1]
    stored_name = f"{loan_id}_{uuid.uuid4().hex}{ext}"
    dest = os.path.join(DOCUMENTS_DIR, stored_name)
    file_stream.save(dest)
    return stored_name, os.path.getsize(dest)


def delete_document_file(stored_name):
    path = os.path.join(DOCUMENTS_DIR, stored_name)
    if os.path.exists(path):
        os.remove(path)


def _months_between(d1, d2):
    return (d2.year - d1.year) * 12 + (d2.month - d1.month) + (1 if d2.day >= d1.day else 0) - (1 if d2.day < d1.day else 0)


def _add_months(d, months):
    month_index = d.month - 1 + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    import calendar
    day = min(d.day, calendar.monthrange(year, month)[1])
    return d.replace(year=year, month=month, day=day)


def compute_schedule(loan, as_of=None):
    """Cronograma teorico de cuotas (fecha, capital, interes, cuota, saldo)
    segun el plan de pago -- "teorico" porque no sabe que cuotas ya se
    pagaron de verdad (eso se cruza en compute_loan_status). bullet/libre no
    tienen cronograma fijo (bullet: un unico pago al final; libre: sin
    fechas pactadas, se devenga pero no hay cuotas)."""
    inicio = datetime.fromisoformat(loan["fecha_inicio"]).date()
    capital = loan["capital"]
    tna = loan["tasa_anual"] / 100
    plan = loan["plan_pago"]

    if plan == "bullet":
        if not loan["plazo_meses"]:
            return []
        vencimiento = _add_months(inicio, loan["plazo_meses"])
        dias = (vencimiento - inicio).days
        interes = capital * tna * dias / 365
        return [{"fecha": vencimiento.isoformat(), "capital": capital, "interes": round(interes, 2), "cuota": round(capital + interes, 2)}]

    if plan == "libre":
        return []

    if not loan["plazo_meses"] or loan["plazo_meses"] <= 0:
        return []
    n = loan["plazo_meses"]
    r = tna / 12
    schedule = []
    saldo = capital

    if plan == "frances":
        # Cuota fija (capital + interes), interes decreciente sobre saldo.
        cuota = capital * r / (1 - (1 + r) ** -n) if r else capital / n
        for i in range(1, n + 1):
            interes_mes = saldo * r
            capital_mes = cuota - interes_mes
            saldo = max(0.0, saldo - capital_mes)
            fecha = _add_months(inicio, i)
            schedule.append({"fecha": fecha.isoformat(), "capital": round(capital_mes, 2), "interes": round(interes_mes, 2), "cuota": round(cuota, 2)})

    elif plan == "aleman":
        # Capital fijo por cuota, interes decreciente sobre saldo -> cuota total decreciente.
        capital_mes = capital / n
        for i in range(1, n + 1):
            interes_mes = saldo * r
            fecha = _add_months(inicio, i)
            schedule.append({"fecha": fecha.isoformat(), "capital": round(capital_mes, 2), "interes": round(interes_mes, 2), "cuota": round(capital_mes + interes_mes, 2)})
            saldo = max(0.0, saldo - capital_mes)

    return schedule


def compute_loan_status(conn, loan):
    """Compara el cronograma teorico contra los pagos YA registrados
    (loan_payments) para decidir: capital pendiente, interes devengado a
    hoy, proximo vencimiento, si esta atrasado. Nunca asume que una cuota
    teorica ya se cobro -- solo cuenta lo que esta en loan_payments."""
    as_of = today_ar()
    pagos = db.list_loan_payments(conn, loan["id"])
    capital_cobrado = sum(p["capital"] for p in pagos)
    interes_cobrado = sum(p["interes"] for p in pagos)
    capital_pendiente = round(loan["capital"] - capital_cobrado, 2)

    schedule = compute_schedule(loan, as_of)

    if loan["plan_pago"] in ("bullet", "libre") or not schedule:
        inicio = datetime.fromisoformat(loan["fecha_inicio"]).date()
        dias = (as_of - inicio).days
        interes_devengado_total = loan["capital"] * (loan["tasa_anual"] / 100) * max(dias, 0) / 365
        interes_pendiente_devengado = round(interes_devengado_total - interes_cobrado, 2)
        proximo = schedule[0] if schedule else None
        vencido_hace_dias = None
        if proximo:
            fecha_venc = datetime.fromisoformat(proximo["fecha"]).date()
            if as_of > fecha_venc and capital_pendiente > 0.01:
                vencido_hace_dias = (as_of - fecha_venc).days
        return {
            "capital_pendiente": capital_pendiente,
            "interes_devengado_pendiente": max(interes_pendiente_devengado, 0.0),
            "proximo_vencimiento": proximo["fecha"] if proximo else None,
            "proximo_monto": proximo["cuota"] if proximo else None,
            "vencido_hace_dias": vencido_hace_dias,
            # bullet/libre no tienen cronograma de cuotas -- hay un solo
            # vencimiento posible, nunca varios impagos a la vez.
            "cuotas_atrasadas": 1 if vencido_hace_dias else 0,
            "cronograma": schedule,
        }

    # frances/aleman: cuantas cuotas ya deberian haberse pagado (por fecha) vs. cuantas se pagaron de verdad
    cuotas_vencidas = [c for c in schedule if datetime.fromisoformat(c["fecha"]).date() <= as_of]
    cuotas_futuras = [c for c in schedule if datetime.fromisoformat(c["fecha"]).date() > as_of]
    n_pagos_registrados = len(pagos)
    n_cuotas_vencidas = len(cuotas_vencidas)
    atrasado = n_pagos_registrados < n_cuotas_vencidas
    # Antes solo se exponia la cuota impaga MAS VIEJA (vencido_hace_dias) --
    # un prestamo con 2+ cuotas vencidas sin pagar (ej. Sep Y Oct impagas)
    # reportaba exactamente lo mismo que uno con una sola, ocultando en
    # silencio cuantas cuotas hay atrasadas de verdad (bug real, encontrado
    # al investigar notificaciones duplicadas de prestamos -- ver conversacion,
    # Fase 7). cuotas_atrasadas expone ese conteo sin cambiar el significado
    # de vencido_hace_dias (sigue siendo la cuota impaga mas vieja).
    cuotas_atrasadas = max(n_cuotas_vencidas - n_pagos_registrados, 0)
    vencido_hace_dias = None
    if atrasado:
        primera_impaga = cuotas_vencidas[n_pagos_registrados]
        vencido_hace_dias = (as_of - datetime.fromisoformat(primera_impaga["fecha"]).date()).days

    proximo = cuotas_futuras[0] if cuotas_futuras else None
    return {
        "capital_pendiente": max(capital_pendiente, 0.0),
        "interes_devengado_pendiente": None,  # ya reflejado en las cuotas del cronograma, no se duplica
        "proximo_vencimiento": proximo["fecha"] if proximo else None,
        "proximo_monto": proximo["cuota"] if proximo else None,
        "vencido_hace_dias": vencido_hace_dias,
        "cuotas_atrasadas": cuotas_atrasadas,
        "cronograma": schedule,
    }


def explain_loan_status(loan, status):
    verbo = "te debe" if loan.get("direccion", "prestado") == "prestado" else "le debés"
    base = f"{loan['deudor']} {verbo} {status['capital_pendiente']:,.2f} {loan['moneda']} de capital pendiente."
    if status["vencido_hace_dias"]:
        cuotas = status.get("cuotas_atrasadas") or 1
        detalle_cuotas = f" ({cuotas} cuotas impagas)" if cuotas > 1 else ""
        base += f" Atrasado {status['vencido_hace_dias']} día(s){detalle_cuotas}."
    elif status["proximo_vencimiento"]:
        base += f" Próximo vencimiento {status['proximo_vencimiento']} por {status['proximo_monto']:,.2f}."
    return base
