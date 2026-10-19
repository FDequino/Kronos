"""Fase 12: proyeccion patrimonial consolidada.

Tres fuentes, nunca mezcladas en un solo numero sin decir de donde sale cada
parte (instruccion explicita del usuario):

- CONTRACTUAL (alta certeza, ya pactado): cuotas futuras de prestamos
  vigentes + vencimientos/cupones de bonos/ON en cartera.
- ESTIMADO (lo carga el usuario a mano): planned_movements.
- PROBABILISTICO (Kronos): solo para el horizonte REAL de cada pronostico
  (pred_len dias, tipicamente ~15-20) -- nunca se extrapola la distribucion
  de Kronos a 3/6/12 meses, seria inventar una confianza que el modelo no
  tiene a esos plazos (seria el mismo tipo de sesgo que ya se corrigio en
  Fase 1 para el horizonte de pronostico individual).
"""
from datetime import datetime, timedelta

from portfolio import db, fixed_income
from portfolio import loans as loans_module
from portfolio.tz import today_ar

HORIZONTES_DIAS = {"1m": 30, "3m": 90, "6m": 180, "12m": 365}


def _contractual_flows(conn, horizon_days, as_of):
    """Flujos YA PACTADOS dentro del horizonte -- cuotas de prestamos
    vigentes (signo: prestado=entra, recibido=sale) + eventos de bonos/ON en
    cartera (sin monto exacto: requeriria exponer el importe por unidad desde
    fixed_income._cash_flow_schedule, fuera de alcance de esta pasada -- se
    informa el evento con fecha, nunca un monto inventado)."""
    limite = as_of + timedelta(days=horizon_days)
    flows = []

    for loan in db.list_loans(conn):
        if loan["estado"] != "vigente":
            continue
        status = loans_module.compute_loan_status(conn, loan)
        signo = 1 if loan["direccion"] == "prestado" else -1
        for cuota in status["cronograma"]:
            fecha_cuota = datetime.fromisoformat(cuota["fecha"]).date()
            if as_of < fecha_cuota <= limite:
                flows.append({
                    "fecha": cuota["fecha"], "moneda": loan["moneda"],
                    "monto": round(signo * cuota["cuota"], 2),
                    "origen": f"Cuota de préstamo {'a' if loan['direccion'] == 'prestado' else 'de'} {loan['deudor']}",
                })

    instrumentos = [i for i in db.list_instruments(conn) if i["type"] in ("bono", "on")]
    for evento in fixed_income.upcoming_fixed_income_events(instrumentos, as_of=as_of, horizon_days=horizon_days):
        flows.append({
            "fecha": evento["date"], "moneda": "ARS", "monto": None,
            "origen": f"{'Vencimiento' if evento['kind'] == 'vencimiento' else 'Cupón'} de {evento['symbol']} (monto exacto: ver detalle del instrumento)",
        })

    flows.sort(key=lambda f: f["fecha"])
    return flows


def _estimado_flows(conn, horizon_days, as_of):
    limite = as_of + timedelta(days=horizon_days)
    SIGNO = {"aporte": 1, "retiro": -1, "gasto": -1, "otro": 1}
    flows = []
    for m in db.list_planned_movements(conn):
        fecha = datetime.fromisoformat(m["fecha"]).date()
        if as_of < fecha <= limite:
            flows.append({
                "fecha": m["fecha"], "moneda": m["moneda"],
                "monto": round(SIGNO[m["tipo"]] * m["monto"], 2),
                "origen": m["notas"] or m["tipo"].capitalize(),
            })
    flows.sort(key=lambda f: f["fecha"])
    return flows


def _flows_total_by_moneda(flows):
    total = {"ARS": 0.0, "USD": 0.0}
    for f in flows:
        if f["monto"] is not None:
            total[f["moneda"]] += f["monto"]
    return {k: round(v, 2) for k, v in total.items()}


def _kronos_scenario(conn):
    """Contribucion probabilistica de Kronos -- SOLO sobre tenencias con
    pronostico validado, agregada en el horizonte REAL de esos pronosticos
    (pred_len dias de cada uno, tipicamente ~15). Nunca se extrapola a mas
    plazo del que el modelo realmente cubre."""
    from portfolio import accuracy

    holdings = [h for h in db.get_holdings_with_quotes(conn) if h["instrument"]["type"] not in ("fondo", "cripto")]
    config = db.get_signal_config(conn)
    valor_hoy = 0.0
    conservador = 0.0
    base = 0.0
    optimista = 0.0
    pred_lens = []
    incluidos = []

    for h in holdings:
        if not h.get("valor_actual"):
            continue
        instrument_id = h["instrument"]["id"]
        row = db.get_latest_forecast(conn, instrument_id=instrument_id)
        if not row or row["status"] != "done" or not row["result_json"]:
            continue
        import json
        result = json.loads(row["result_json"])
        distribution = result.get("distribution")
        if not distribution:
            continue
        forecasts = db.list_forecasts_for_instrument(conn, instrument_id)
        price_history = db.get_price_history(conn, instrument_id)
        acc = accuracy.compute_accuracy(forecasts, price_history, conn=conn)
        validation = accuracy.compute_signal_validation(acc, config)
        if not validation["validated"]:
            continue

        valor = h["valor_actual"]
        valor_hoy += valor
        conservador += valor * (1 + distribution["p10_pct"] / 100)
        base += valor * (1 + distribution["median_pct"] / 100)
        optimista += valor * (1 + distribution["p90_pct"] / 100)
        pred_lens.append(row.get("pred_len") or config["default_pred_len"])
        incluidos.append(h["instrument"]["symbol"])

    if not incluidos:
        return {"available": False, "reason": "Ninguna tenencia tiene una señal de Kronos validada hoy."}

    horizonte_dias = round(sum(pred_lens) / len(pred_lens))
    return {
        "available": True,
        "horizonte_dias": horizonte_dias,
        "valor_hoy": round(valor_hoy, 2),
        "conservador": round(conservador, 2),
        "base": round(base, 2),
        "optimista": round(optimista, 2),
        "instrumentos_incluidos": incluidos,
    }


def compute_projection(conn):
    as_of = today_ar()
    horizontes = {}
    for label, dias in HORIZONTES_DIAS.items():
        contractual = _contractual_flows(conn, dias, as_of)
        estimado = _estimado_flows(conn, dias, as_of)
        horizontes[label] = {
            "contractual": contractual,
            "contractual_total": _flows_total_by_moneda(contractual),
            "estimado": estimado,
            "estimado_total": _flows_total_by_moneda(estimado),
        }
    return {
        "as_of": as_of.isoformat(),
        "horizontes": horizontes,
        "kronos": _kronos_scenario(conn),
    }
