"""Formato es-AR (coma decimal) para numeros embebidos en texto explicativo.

El resto de la UI ya formatea con Intl.NumberFormat('es-AR') en el frontend
(coma decimal) -- estos textos se arman en Python con f-strings que usan el
separador "." por defecto, y quedaban inconsistentes (ej. "MAPE 10.17%" al
lado de montos como "$10.416.931,52").
"""


def fmt_es(value, decimals=1, signed=False):
    if value is None:
        return "—"
    spec = f"{'+' if signed else ''}.{decimals}f"
    return f"{value:{spec}}".replace(".", ",")
