"""XIRR (tasa interna de retorno, con fechas reales) -- a diferencia del TWR
que ya expone benchmarks.py (que mide el rendimiento de las DECISIONES de
inversión, sin importar cuándo/cuánto aportaste), el XIRR mide el rendimiento
de TU BOLSILLO: tiene en cuenta el momento y el tamaño de cada aporte/retiro
real (ver portfolio/cash.py::external_cash_flows).

Nunca se inventa un numero: hacen falta al menos 2 flujos de signo distinto
(un aporte y, en algun momento, un valor de cierre positivo) para que exista
una solucion matematica -- si no, se devuelve None con el motivo."""
from datetime import datetime


def _xnpv(rate, cash_flows, t0):
    return sum(
        monto / (1 + rate) ** ((fecha - t0).days / 365)
        for fecha, monto in cash_flows
    )


def compute_xirr(cash_flows, low=-0.9999, high=10.0, iterations=200, tol=1e-7):
    """cash_flows: [(date, monto)] -- monto NEGATIVO = plata que salio de tu
    bolsillo hacia la inversion (aporte), POSITIVO = plata que volvio
    (retiro, o el valor actual de la cartera tratado como un retiro
    hipotetico al final, para cerrar el calculo). Biseccion sobre
    NPV(rate) = 0 -- requiere que haya al menos un flujo negativo y uno
    positivo (sino no hay raiz real)."""
    if len(cash_flows) < 2:
        return None
    montos = [m for _, m in cash_flows]
    if not (any(m < 0 for m in montos) and any(m > 0 for m in montos)):
        return None

    cash_flows = sorted(cash_flows, key=lambda cf: cf[0])
    t0 = cash_flows[0][0]

    f_low = _xnpv(low, cash_flows, t0)
    f_high = _xnpv(high, cash_flows, t0)
    if f_low * f_high > 0:
        return None  # sin raiz en el rango -- caso patologico, no se fuerza un numero

    for _ in range(iterations):
        mid = (low + high) / 2
        f_mid = _xnpv(mid, cash_flows, t0)
        if abs(f_mid) < tol:
            return mid
        if f_low * f_mid < 0:
            high, f_high = mid, f_mid
        else:
            low, f_low = mid, f_mid
    return (low + high) / 2


def compute_portfolio_xirr(conn, moneda, valor_actual_hoy):
    """XIRR de la cuenta de caja real (ver cash.py) en una moneda -- los
    aportes/retiros reales + el valor actual de la cartera como si se
    liquidara hoy (unico flujo positivo final que no es un movimiento real,
    necesario para que el calculo tenga un punto de cierre).

    Signo: cash_movements.monto esta en la perspectiva de LA CUENTA (aporte
    = positivo, entra plata a la cuenta) -- XIRR necesita la perspectiva DEL
    INVERSOR (aporte = negativo, sale plata de tu bolsillo). Se invierte
    aca, no en cash.py (ese modulo describe la cuenta, no al inversor)."""
    from portfolio import cash

    flows = cash.external_cash_flows(conn, moneda=moneda) + cash.loan_capital_flows(conn, moneda=moneda)
    if not flows:
        return {"available": False, "reason": f"Sin aportes/retiros registrados en {moneda}."}

    cash_flows = [(datetime.fromisoformat(f["fecha"]).date(), -f["monto"]) for f in flows]
    from portfolio.tz import today_ar
    cash_flows.append((today_ar(), valor_actual_hoy))

    rate = compute_xirr(cash_flows)
    if rate is None:
        return {"available": False, "reason": "No se pudo resolver una tasa real con estos flujos (se necesita al menos un aporte y un retiro/valor de cierre de signo contrario)."}
    return {"available": True, "xirr_pct": round(rate * 100, 2)}
