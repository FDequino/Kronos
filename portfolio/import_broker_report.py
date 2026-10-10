"""
Importa el export de bróker (formato Balanz, hoja "Boletos de Compra Venta" +
"Boletos de FCI" + "Dividendos" + "Rentas") hacia el portfolio real.

Las cauciones se derivan de la misma hoja de boletos emparejando cada pata
"Cauc Col Ctdo" (apertura, Numero=N) con su pata "Cauc Col Fut" (vencimiento,
Numero=N+1) -- asi se evita depender de un segundo archivo.

Uso: python -m portfolio.import_broker_report <informe.xlsx>
"""
import sys

import pandas as pd

from portfolio import db

ESPECIE_TO_TYPE = {
    "CEDEAR": "cedear",
    "Acciones": "accion",
    "Título Públicos": "bono",
    "LETRAS": "bono",
    "Títulos Privados": "on",
}

# Mapeo especifico de este broker: codigo interno de fondo -> datos legibles.
# Es dato de importacion puntual, no logica de producto (no viola "nada hardcodeado").
FUND_INFO = {
    24914: {"symbol": "BALANZ2", "name": "Balanz Performance 2 Cl. A", "currency": "ARS"},
    27032: {"symbol": "ALLARIA-USD", "name": "Allaria Dolar Ahorro Plus Clase A", "currency": "USD"},
    14667: {"symbol": "IAM-USD", "name": "IAM Renta Dolares Clase A", "currency": "USD"},
}

FEE_COLUMNS = ["Arancel", "Derecho Bolsa", "Derecho Mercado", "Iva Arancel", "Iva Derecho", "Iva Arancel Contraparte"]


def _parse_ticker(instrumento):
    return instrumento.split(" - ", 1)[0].strip()


def _get_or_create_instrument(conn, cache, symbol, type_, *, name=None, currency="ARS",
                                market="byma", price_source="data912", price_source_params=None):
    key = (symbol, market)
    if key in cache:
        return cache[key]
    existing = db.find_instrument(conn, symbol, market)
    if existing:
        cache[key] = existing["id"]
        return existing["id"]
    inst_id = db.add_instrument(conn, symbol, type_, market, price_source, name=name,
                                 currency=currency, price_source_params=price_source_params)
    cache[key] = inst_id
    return inst_id


def _load_boletos(xlsx_path):
    df = pd.ExcelFile(xlsx_path).parse("Boletos de Compra Venta")
    df["Fec. Concert."] = pd.to_datetime(df["Fec. Concert."], dayfirst=True, errors="coerce")
    df["Fec. Liq."] = pd.to_datetime(df["Fec. Liq."], dayfirst=True, errors="coerce")
    df["Número"] = df["Número"].astype(int)
    return df


def _load_fx_rates(xlsx_path):
    """Fecha -> cotizacion DOLAR LOCAL, para convertir boletos operados en U$S a pesos."""
    fx = pd.ExcelFile(xlsx_path).parse("Histórico tipo de cambio")
    fx = fx[fx["Descripción"] == "DOLAR LOCAL"]
    fx["Fecha"] = pd.to_datetime(fx["Fecha"], dayfirst=True, errors="coerce")
    return dict(zip(fx["Fecha"].dt.date, fx["Cotización"]))


def import_instruments_and_transactions(conn, boletos, cache, fx_rates):
    n = 0
    for _, row in boletos[boletos["Tipo"].isin(["COMPRA", "VENTA", "LICOMPRA"])].iterrows():
        instr_type = ESPECIE_TO_TYPE.get(row["Especie"])
        if not instr_type:
            print(f"  aviso: especie desconocida '{row['Especie']}', se omite {row['Instrumento']}")
            continue

        symbol = _parse_ticker(row["Instrumento"])
        name = row["Instrumento"].split(" - ", 1)[-1].strip()
        # Las letras (LETRAS) cotizan en data912 bajo /live/arg_notes, no /live/arg_bonds.
        params = {"endpoint": "arg_notes"} if row["Especie"] == "LETRAS" else None
        # Algunos bonos/ON (ej. YMCXD) se liquidan y cotizan en U$S -- es una
        # propiedad real del instrumento. Las acciones/CEDEARs en cambio
        # siempre cotizan en pesos en data912 aunque UNA compra puntual se
        # haya liquidado en dolar-billete (ej. IBM el 14/07) -- no confundir
        # la moneda de liquidacion de un boleto con la moneda del instrumento.
        currency = "USD" if instr_type in ("bono", "on") and row["Moneda"] != "$" else "ARS"
        inst_id = _get_or_create_instrument(conn, cache, symbol, instr_type, name=name,
                                             currency=currency, price_source_params=params)

        cantidad = row["Cantidad"]
        comision = sum(
            abs(v) for v in (pd.to_numeric(row[c], errors="coerce") for c in FEE_COLUMNS) if pd.notna(v)
        )
        if instr_type in ("bono", "on"):
            # El ratio del broker (1.0484 = 104.84% del nominal) ya es "precio
            # por 1 unidad de nominal" -- la misma convencion que usamos para
            # todo instrumento en el sistema. data912 cotiza bonos/ON "por cada
            # 100 nominales" (105.09); esa normalizacion vive en fetchers.py
            # (fetch_data912_quote/historical), no aca.
            precio = row["Precio"]
        else:
            # "Bruto" viene en la moneda de la columna "Moneda": casi siempre
            # pesos ($), pero algunos CEDEARs se operan en U$S -- hay que
            # pasarlos a pesos con la cotizacion del dia antes de dividir por
            # la cantidad (data912 cotiza acciones/CEDEARs siempre en pesos).
            bruto_ars = abs(row["Bruto"])
            if row["Moneda"] != "$":
                bruto_ars *= fx_rates[row["Fec. Concert."].date()]
            precio = bruto_ars / abs(cantidad)

        db.add_transaction(
            conn, instrument_id=inst_id,
            fecha=row["Fec. Concert."].date().isoformat(),
            tipo="venta" if cantidad < 0 else "compra",
            cantidad=abs(cantidad), precio=precio, comision=comision,
            notas=f"Boleto {row['Número']}",
        )
        n += 1
    return n


def import_funds(conn, xlsx_path, cache):
    df = pd.ExcelFile(xlsx_path).parse("Boletos de FCI")
    df["Fec. Concert."] = pd.to_datetime(df["Fec. Concert."], dayfirst=True, errors="coerce")

    n = 0
    for _, row in df.iterrows():
        info = FUND_INFO.get(int(row["Fondo"]))
        if not info:
            print(f"  aviso: fondo desconocido codigo {row['Fondo']}, se omite")
            continue

        inst_id = _get_or_create_instrument(
            conn, cache, info["symbol"], "fondo", name=info["name"],
            currency=info["currency"], market="fondo", price_source="manual",
        )
        cantidad = row["Cantidad"]
        db.add_transaction(
            conn, instrument_id=inst_id,
            fecha=row["Fec. Concert."].date().isoformat(),
            tipo="venta" if cantidad < 0 else "compra",
            cantidad=abs(cantidad), precio=row["Valor Cuotaparte"], comision=0,
            notas="Boletos de FCI",
        )
        n += 1
    return n


def import_cauciones(conn, boletos):
    ctdo_rows = boletos[boletos["Tipo"] == "Cauc Col Ctdo"]
    fut_rows = boletos[boletos["Tipo"] == "Cauc Col Fut"].set_index("Número")

    n = 0
    for _, ctdo in ctdo_rows.iterrows():
        fut_numero = ctdo["Número"] + 1
        if fut_numero not in fut_rows.index:
            print(f"  aviso: no encontre la pata futura de la caucion {ctdo['Número']}")
            continue
        fut = fut_rows.loc[fut_numero]

        fecha_apertura = ctdo["Fec. Liq."].date()
        fecha_vencimiento = fut["Fec. Liq."].date()
        plazo_dias = max((fecha_vencimiento - fecha_apertura).days, 1)
        monto_contado = abs(ctdo["Bruto"])
        interes_bruto = float(fut["Interés"])
        tasa = interes_bruto / monto_contado / plazo_dias * 365 * 100

        db.add_caucion(
            conn, numero_boleto=str(ctdo["Número"]),
            fecha_apertura=fecha_apertura.isoformat(), fecha_vencimiento=fecha_vencimiento.isoformat(),
            plazo_dias=plazo_dias, monto_contado=monto_contado, monto_futuro=float(fut["Bruto"]),
            tasa=tasa, interes_bruto=interes_bruto,
        )
        n += 1
    return n


def import_dividendos_y_rentas(conn, xlsx_path, cache):
    n = 0
    xls = pd.ExcelFile(xlsx_path)

    div = xls.parse("Dividendos")
    div["fechaConcertacion"] = pd.to_datetime(div["fechaConcertacion"], dayfirst=True, errors="coerce")
    for _, row in div.iterrows():
        symbol = row["especieAbreviatura"]
        db.add_renta_dividendo(
            conn, instrument_id=cache.get((symbol, "byma")), symbol=symbol,
            fecha=row["fechaConcertacion"].date().isoformat(), tipo="dividendo",
            moneda=row["moneda"], bruto=float(row["brutoTotal"]),
            gastos=float(row["arancelMonto"] or 0), neto=float(row["neto"]),
        )
        n += 1

    tipo_renta_map = {"Renta": "renta", "Amortización": "amortizacion"}
    rentas = xls.parse("Rentas")
    rentas["fechaConcertacion"] = pd.to_datetime(rentas["fechaConcertacion"], dayfirst=True, errors="coerce")
    for _, row in rentas.iterrows():
        symbol = row["especieAbreviatura"]
        tipo = tipo_renta_map.get(row["tpMovimiento"], "renta")
        inst_id = cache.get((symbol, "byma"))
        db.add_renta_dividendo(
            conn, instrument_id=inst_id, symbol=symbol,
            fecha=row["fechaConcertacion"].date().isoformat(), tipo=tipo,
            moneda=row["moneda"], bruto=float(row["brutoTotal"]),
            gastos=float(row["arancelMonto"] or 0), neto=float(row["neto"]),
        )
        n += 1

        # Una amortizacion es el bono/letra venciendo y pagando -- sin esto la
        # tenencia queda fantasma (Boletos de Compra Venta no registra el cierre).
        # La cantidad nominal esta en "posicion", no en "cantidadEspecie" (que da 0 aca).
        cantidad = float(row["posicion"] or 0)
        if tipo == "amortizacion" and inst_id and cantidad > 0:
            precio_cierre = float(row["brutoAmortizacion"]) / cantidad
            db.add_transaction(
                conn, instrument_id=inst_id, fecha=row["fechaConcertacion"].date().isoformat(),
                tipo="venta", cantidad=cantidad, precio=precio_cierre, comision=0,
                notas="Amortizacion (vencimiento) importada de hoja Rentas",
            )

    return n


def main():
    if len(sys.argv) < 2:
        print("Uso: python -m portfolio.import_broker_report <informe.xlsx>")
        sys.exit(1)

    xlsx_path = sys.argv[1]
    boletos = _load_boletos(xlsx_path)
    fx_rates = _load_fx_rates(xlsx_path)
    cache = {}

    conn = db.get_connection()
    try:
        print(f"Transacciones de mercado: {import_instruments_and_transactions(conn, boletos, cache, fx_rates)}")
        print(f"Transacciones de fondos: {import_funds(conn, xlsx_path, cache)}")
        print(f"Cauciones: {import_cauciones(conn, boletos)}")
        print(f"Dividendos/rentas: {import_dividendos_y_rentas(conn, xlsx_path, cache)}")

        print("\nHoldings resultantes:")
        for h in db.get_holdings(conn):
            print(f"  {h['instrument']['symbol']:15s} cantidad={h['cantidad']:.4f}  costo_prom={h['costo_promedio']:.2f}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
