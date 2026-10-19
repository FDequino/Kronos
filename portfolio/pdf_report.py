"""Reporte PDF configurable del portfolio (ver conversacion, "Reporte
Ejecutivo"/rediseño completo) -- arma un PDF con las secciones que el
usuario eligio en Ajustes (Resumen y Tenencias siempre, + Riesgo/Paper
Trading/Rendimiento-Caucion/Oportunidades opcionales via app_settings) y en
la moneda elegida (USD/ARS/DUAL).

Separacion deliberada en 2 pasos (para que sea testeable sin parsear bytes
de PDF):
  1. build_report_sections(conn, config) -- PURA logica/datos: reusa las
     MISMAS funciones de acceso a datos que ya usa el resto de la app
     (db.get_holdings_with_quotes, risk.compute_portfolio_risk_metrics,
     paper_trading.compute_daily_report, db.get_cauciones_resumen,
     live_signals.compute_today_signals, monitor.current_prices_for_symbols)
     -- nunca una segunda fuente de verdad calculada distinto para el mismo
     numero. Esto es lo que testean la mayoria de los tests de este modulo.
  2. render_pdf(sections, output_path) -- reportlab puro, solo maquetacion,
     sin logica de negocio.

reportlab (pure Python, sin dependencias de sistema como weasyprint/Pango/
Cairo) agregado a requirements.txt -- la unica razon para elegirlo sobre las
alternativas.

Nunca toca portfolio/telegram_bot.py ni ninguna ruta/plantilla web -- ese
cableado (job programado, boton "Enviar Reporte de Prueba Ahora", tab
Notificaciones) lo hace otra parte de la conversacion, llamando a
generate_portfolio_pdf() de aca como punto de entrada unico."""
import io
from xml.sax.saxutils import escape as _xml_escape

import matplotlib
matplotlib.use("Agg")  # backend sin display -- este modulo corre en un proceso servidor/job, nunca una ventana real
import matplotlib.pyplot as plt

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from model.client import ChronosServiceClient
from portfolio import db, fetchers, live_signals, monitor, paper_trading, risk
from portfolio.tz import now_ar, today_ar

PDF_CURRENCY_USD = "USD"
PDF_CURRENCY_ARS = "ARS"
PDF_CURRENCY_DUAL = "DUAL"
VALID_PDF_CURRENCIES = (PDF_CURRENCY_USD, PDF_CURRENCY_ARS, PDF_CURRENCY_DUAL)


# --- Datos (pura logica, ver docstring del modulo) ---------------------------

def _resolve_rate(conn):
    """USD/ARS vigente (ver portfolio/fetchers.py::resolve_usd_rate) -- None
    si la fuente elegida en Preferencias no respondio (nunca se inventa una
    cotizacion para poder seguir armando el reporte: la seccion que la
    necesite simplemente muestra None en el lado que no se pudo convertir)."""
    settings = db.get_app_settings(conn)
    try:
        return fetchers.resolve_usd_rate(settings)
    except Exception:
        return None


def _dual_value(value_native, native_currency, rate):
    """(valor_ars, valor_usd) a partir de un valor en su moneda NATIVA + la
    cotizacion vigente -- mismo espiritu que portfolio/live_signals.py::_dual_price,
    reescrito aca porque ese toma USD como entrada fija y este necesita
    partir de CUALQUIERA de las 2 (holdings nativos pueden ser ARS o USD
    segun el instrumento, ver portfolio/risk.py::compute_portfolio_risk_metrics
    para el mismo cuidado de nunca mezclar monedas sin convertir)."""
    if value_native is None:
        return None, None
    if native_currency == "ARS":
        valor_ars = value_native
        valor_usd = (value_native / rate) if rate else None
    else:
        valor_usd = value_native
        valor_ars = (value_native * rate) if rate else None
    return valor_ars, valor_usd


def _resumen_ejecutivo_section(conn, rate):
    """Block 1 (ver conversacion): Patrimonio Total (USD CCL y ARS),
    Resultado del Dia ($ y %) y Capital en Caucion -- todo REUSANDO funciones
    que ya existen (nunca una cuenta paralela).

    Resultado del Dia: compara el patrimonio ARS de HOY (en vivo, ver
    live_signals.total_portfolio_value_ars) contra el ultimo
    portfolio_snapshots.valor_total que NO sea de hoy (ver
    portfolio/routes.py::_record_portfolio_snapshot -- un snapshot por dia,
    se graba cuando se refrescan cotizaciones desde la Web UI). Si todavia no
    hay un snapshot previo (instalacion nueva, o nadie abrio la app ayer),
    se muestra None -- NUNCA se inventa un 0% de referencia, mismo criterio
    ya establecido en paper_trading.compute_daily_report."""
    total_ars = live_signals.total_portfolio_value_ars(conn)
    total_usd = (total_ars / rate) if rate else None

    today_str = today_ar().isoformat()
    snapshots = db.list_portfolio_snapshots(conn, limit=2)
    previous = next((s for s in snapshots if s["date"] != today_str), None)
    if previous and previous.get("valor_total"):
        resultado_dia_ars = total_ars - previous["valor_total"]
        resultado_dia_pct = resultado_dia_ars / previous["valor_total"] * 100
    else:
        resultado_dia_ars = None
        resultado_dia_pct = None

    cauciones = db.get_cauciones_resumen(conn)
    capital_caucion_ars = sum(c.get("valor_actual") or 0.0 for c in cauciones if not c.get("vencido"))

    return {
        "title": "Resumen Ejecutivo", "kind": "resumen_ejecutivo",
        "data": {
            "patrimonio_total_ars": total_ars, "patrimonio_total_usd": total_usd,
            "resultado_dia_ars": resultado_dia_ars, "resultado_dia_pct": resultado_dia_pct,
            "capital_caucion_ars": capital_caucion_ars,
        },
    }


def _holdings_section(conn, pdf_currency, rate):
    holdings = db.get_holdings_with_quotes(conn)
    rows = []
    for h in holdings:
        native_currency = h["instrument"]["currency"]
        valor_ars, valor_usd = _dual_value(h.get("valor_actual"), native_currency, rate)
        row = {
            "symbol": h["instrument"]["symbol"], "type": h["instrument"]["type"],
            "cantidad": h.get("cantidad"), "pnl": h.get("pnl"),
        }
        if pdf_currency in (PDF_CURRENCY_ARS, PDF_CURRENCY_DUAL):
            row["valor_ars"] = valor_ars
        if pdf_currency in (PDF_CURRENCY_USD, PDF_CURRENCY_DUAL):
            row["valor_usd"] = valor_usd
        rows.append(row)
    return {"title": "Tenencias", "kind": "holdings_table", "data": {"rows": rows, "currency": pdf_currency}}


def _risk_section(conn):
    holdings = db.get_holdings_with_quotes(conn)
    cauciones = db.get_cauciones_resumen(conn)
    config = db.get_signal_config(conn)
    metrics = risk.compute_portfolio_risk_metrics(conn, holdings, cauciones, config)
    return {"title": "Métricas de Riesgo", "kind": "risk_metrics", "data": metrics}


def _paper_trading_section(conn):
    """Block 2 (ver conversacion): tabla de posiciones de Paper Trading con
    precio actual y PnL% real -- a diferencia del resumen agregado de antes
    (compute_daily_report solo, sin current_prices), aca se completa cada
    posicion abierta con su precio EN VIVO via
    portfolio/monitor.py::current_prices_for_symbols (misma fuente que usa
    el scanner intradia real, nunca una segunda fuente de precio). Si un
    simbolo puntual no responde, esa fila muestra "—" en Valor Actual/PnL%
    (degradacion por fila, nunca rompe el reporte entero)."""
    report = paper_trading.compute_daily_report(conn)
    open_positions = report["open_positions"]
    pairs = sorted({(p["symbol"], p["asset_type"]) for p in open_positions})
    try:
        prices = monitor.current_prices_for_symbols(conn, pairs) if pairs else {}
    except Exception:
        prices = {}

    positions_detail = []
    for p in open_positions:
        price = prices.get(p["symbol"])
        price_usd = price[0] if price else None
        valor_actual_usd = (price_usd * p["quantity"]) if price_usd is not None else None
        pnl_pct = ((price_usd / p["entry_price_usd"] - 1) * 100) if price_usd is not None and p["entry_price_usd"] else None
        positions_detail.append({
            "symbol": p["symbol"], "engine": p["engine"], "tactic": p.get("tactic"),
            "entry_price_usd": p["entry_price_usd"], "current_price_usd": price_usd,
            "valor_actual_usd": valor_actual_usd, "pnl_pct": pnl_pct,
        })

    data = dict(report)
    data["positions_detail"] = positions_detail
    return {"title": "Paper Trading", "kind": "paper_trading_summary", "data": data}


def _yield_caucion_section(conn):
    cauciones = db.get_cauciones_resumen(conn)
    vigentes = [c for c in cauciones if not c.get("vencido")]
    total_actual = sum(c.get("valor_actual") or 0.0 for c in vigentes)
    return {
        "title": "Rendimiento / Caución", "kind": "yield_caucion",
        "data": {"cauciones": vigentes, "total_valor_actual": total_actual, "n_vigentes": len(vigentes)},
    }


def _opportunities_section(conn):
    """Block 3 (ver conversacion): señales de Core + Satelite de HOY, mismo
    criterio/numeros que GET /api/signals y el boton "¿Qué compramos hoy?"
    de Telegram (ver portfolio/live_signals.py::compute_today_signals --
    nunca una segunda logica de deteccion). Solo Core/Satelite (tienen un
    precio de entrada sugerido simple en USD/ARS, que es lo que pide el
    enunciado) -- Pairs/Factor/Canje/Dolar Sintetico/Carry Trade son alertas
    de otro tipo (sin "precio de entrada" en ese mismo formato), quedan
    fuera de este listado compacto.

    El Motor Core consulta a Chronos-Bolt (red) -- si el servicio no
    responde, esta funcion NUNCA rompe el reporte entero: degrada a
    "no disponible" con el motivo, igual criterio que el resto del modulo
    (ver _resolve_rate). Gateado por su propio flag
    (pdf_include_opportunities) justamente por este costo/riesgo de red."""
    try:
        client = ChronosServiceClient()
        account_value = live_signals.total_portfolio_value_ars(conn)
        result = live_signals.compute_today_signals(conn, client, account_value=account_value)
        return {
            "title": "Señales y Oportunidades del Día", "kind": "oportunidades_dia",
            "data": {
                "available": True,
                "core_signals": result["core_signals"], "satellite_signals": result["satellite_signals"],
                "spy_above_ema50": result["spy_above_ema50"], "market_regime": result["market_regime"],
            },
        }
    except Exception as e:
        return {
            "title": "Señales y Oportunidades del Día", "kind": "oportunidades_dia",
            "data": {"available": False, "error": str(e)},
        }


def build_report_sections(conn, config=None):
    """Arma la lista de secciones segun la config de app_settings (o la que
    se pase explicita, para tests) -- Resumen y Tenencias SIEMPRE aparecen
    (son la base del reporte), las demas solo si su flag esta activo. Nunca
    incluye una seccion con el flag en 0, nunca omite una con el flag en 1."""
    config = config if config is not None else db.get_app_settings(conn)
    pdf_currency = config.get("pdf_currency") or PDF_CURRENCY_DUAL
    if pdf_currency not in VALID_PDF_CURRENCIES:
        pdf_currency = PDF_CURRENCY_DUAL
    rate = _resolve_rate(conn)

    sections = [_resumen_ejecutivo_section(conn, rate), _holdings_section(conn, pdf_currency, rate)]
    if config.get("pdf_include_risk_metrics"):
        sections.append(_risk_section(conn))
    if config.get("pdf_include_paper_trading"):
        sections.append(_paper_trading_section(conn))
    if config.get("pdf_include_yield_caucion"):
        sections.append(_yield_caucion_section(conn))
    if config.get("pdf_include_opportunities"):
        sections.append(_opportunities_section(conn))
    return sections


# --- Render PDF (reportlab + matplotlib, sin logica de negocio) -------------
#
# Rediseño ejecutivo completo (ver conversacion: "se ve desordenado,
# esteticamente feo y con lenguaje tecnico dificil de entender... vamos a
# rediseñarlo por completo desde cero"). Paleta ejecutiva pedida
# explicitamente (navy/azul corporativo/gris claro/verde/rojo), header+pie
# de pagina en banda de color en CADA pagina, tarjetas de resumen grandes
# con esquinas redondeadas (ROUNDEDCORNERS, reportlab >= 3.6.3), jerga
# tecnica traducida a castellano llano (motor/tactica), numeros con
# separador de miles "." y decimales "," (es-AR).

COLOR_NAVY = colors.HexColor("#0F172A")
COLOR_BLUE = colors.HexColor("#2563EB")
COLOR_LIGHT_GRAY = colors.HexColor("#F8FAFC")
COLOR_GREEN = colors.HexColor("#16A34A")
COLOR_RED = colors.HexColor("#DC2626")
# Complementarios (misma familia visual que la paleta pedida, no inventados
# sueltos: slate-200/slate-500 de la misma escala que #0F172A=slate-900).
COLOR_BORDER = colors.HexColor("#E2E8F0")
COLOR_TEXT_MUTED = colors.HexColor("#64748B")
COLOR_ROW_ALT = colors.HexColor("#F1F5F9")
CHART_PALETTE = ["#2563EB", "#16A34A", "#F59E0B", "#DC2626", "#7C3AED", "#0891B2", "#64748B"]

DISCLAIMER_TEXT = "Generado automáticamente por Kronos a partir de datos cargados por el usuario -- no es asesoramiento financiero."
HEADER_TITLE = "KRONOS — Reporte Diario de Cartera"
HEADER_HEIGHT = 1.7 * cm

# --- Traduccion de jerga tecnica a castellano llano (ver conversacion) ------

ENGINE_LABELS = {"core": "Fondo de Mediano Plazo", "satellite": "Estrategia Corto Plazo"}
TACTIC_LABELS = {
    "mean_reversion": "Rebote en Soporte",
    "volume_breakout": "Ruptura por Volumen",
    "atr_breakout": "Ruptura por Volatilidad",
    "yield_sweep": "Tasa Pasiva Caución",
}
INSTRUMENT_TYPE_LABELS = {
    "accion": "Acción", "cedear": "CEDEAR", "bono": "Bono", "on": "Obligación Negociable",
    "cripto": "Cripto", "fondo": "Fondo Común de Inversión",
}


def _engine_label(key):
    return ENGINE_LABELS.get(key, key)


def _tactic_label(key):
    return TACTIC_LABELS.get(key, key) if key else "—"


def _instrument_type_label(key):
    return INSTRUMENT_TYPE_LABELS.get(key, key)


# --- Formato de numeros es-AR (miles ".", decimales ",") --------------------

def _fmt_es_number(value, decimals=2):
    if value is None:
        return None
    s = f"{value:,.{decimals}f}"
    # swap via placeholder para no pisar el "," de miles con el "." decimal
    return s.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def _fmt_usd(value, decimals=2):
    n = _fmt_es_number(value, decimals)
    return f"USD {n}" if n is not None else "—"


def _fmt_ars(value, decimals=0):
    n = _fmt_es_number(value, decimals)
    return f"$ {n} ARS" if n is not None else "—"


def _fmt_pct(value, decimals=2, signed=True):
    if value is None:
        return "—"
    n = _fmt_es_number(value, decimals)
    sign = "+" if signed and value >= 0 else ""
    return f"{sign}{n}%"


def _fmt_qty(value, decimals=4):
    n = _fmt_es_number(value, decimals)
    return n if n is not None else "—"


def _signed_color(value):
    if value is None:
        return COLOR_NAVY
    return COLOR_GREEN if value >= 0 else COLOR_RED


# --- Estilos -----------------------------------------------------------------

def _build_styles():
    styles = getSampleStyleSheet()
    styles["Heading2"].textColor = COLOR_NAVY
    styles["Heading2"].fontSize = 13
    styles["Heading2"].spaceBefore = 2
    styles["Heading2"].spaceAfter = 8
    styles["Normal"].fontSize = 9
    styles["Normal"].textColor = COLOR_NAVY
    styles.add(ParagraphStyle("Meta", fontSize=8.5, textColor=COLOR_TEXT_MUTED, spaceAfter=0))
    styles.add(ParagraphStyle("CardLabel", fontSize=8, textColor=COLOR_TEXT_MUTED, fontName="Helvetica-Bold", spaceAfter=2))
    styles.add(ParagraphStyle("CardValue", fontSize=15, textColor=COLOR_NAVY, fontName="Helvetica-Bold", leading=18))
    styles.add(ParagraphStyle("CardValueSub", fontSize=10.5, textColor=COLOR_TEXT_MUTED, fontName="Helvetica", leading=13))
    styles.add(ParagraphStyle("CellWrap", fontSize=8.5, textColor=COLOR_NAVY, fontName="Helvetica", leading=10.5))
    styles.add(ParagraphStyle("CellWrapMuted", fontSize=8, textColor=COLOR_TEXT_MUTED, fontName="Helvetica", leading=10))
    # Header de tabla (texto BLANCO) -- deliberadamente una ParagraphStyle
    # propia, nunca reusar CellWrap aca: una vez que el contenido de una
    # celda es un Paragraph, su textColor/fontName propios ganan SIEMPRE
    # sobre los comandos TEXTCOLOR/FONTNAME del TableStyle (gotcha real de
    # reportlab, encontrado armando este reporte -- el header quedaba texto
    # navy sobre fondo navy, invisible).
    styles.add(ParagraphStyle("CellHeader", fontSize=8.5, textColor=colors.white, fontName="Helvetica-Bold", leading=10.5))
    return styles


def _cell(text, style, color=None):
    """Celda de tabla ENCAPSULADA en Paragraph (ver conversacion: "nunca un
    desborde de texto, colWidths explicitos") -- con esto el texto largo
    (ej. un simbolo o nombre de tactica) envuelve dentro del ancho de
    columna en vez de desbordar la celda.

    Escapa el texto (xml.sax.saxutils.escape) antes de pasarlo a Paragraph --
    Paragraph interpreta su contenido como mini-XML, asi que un "&" literal
    (ej. el header "P&L") rompe el parseo si no se escapa (bug real
    encontrado armando este reporte: salia "P&L;" en vez de "P&L"). Para
    celdas que necesitan markup real (ej. <br/> para 2 renglones), armar el
    Paragraph directo en el call site en vez de pasar por aca."""
    s = style if color is None else ParagraphStyle(f"{style.name}_c{id(color)}", parent=style, textColor=color)
    return Paragraph(_xml_escape(str(text)), s)


def _image_flowable(png_buf, width):
    """Image flowable con la ALTURA calculada a partir del aspect ratio
    real del PNG -- los graficos de abajo usan bbox_inches='tight' al
    guardar (recorta al contenido real, incluida la leyenda que queda
    afuera del eje), asi que su proporcion final no es necesariamente la
    del figsize declarado; forzar un alto fijo los deformaria."""
    reader = ImageReader(png_buf)
    native_width, native_height = reader.getSize()
    png_buf.seek(0)
    return Image(png_buf, width=width, height=width * (native_height / native_width))


def _zebra_table_style(n_data_rows, numeric_cols=(), total_row=False, col_widths=None):
    """Estilo comun reusado por todas las tablas del reporte -- header navy
    con texto blanco, filas alternadas (zebra, gris claro) para seguir una
    fila larga con la vista, columnas numericas alineadas a la derecha, y
    opcionalmente una ultima fila de TOTAL resaltada. Esquinas redondeadas
    suaves en el header (pedido explicito de diseño)."""
    cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), COLOR_NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 8.5),
        ("LINEBELOW", (0, 0), (-1, 0), 1, COLOR_NAVY),
        ("GRID", (0, 1), (-1, -1), 0.4, COLOR_BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("ROUNDEDCORNERS", [6, 6, 0, 0]),
    ]
    for col in numeric_cols:
        cmds.append(("ALIGN", (col, 0), (col, -1), "RIGHT"))
    last_zebra_row = n_data_rows - (2 if total_row else 1)
    for i in range(1, last_zebra_row + 1):
        if (i - 1) % 2 == 1:
            cmds.append(("BACKGROUND", (0, i), (-1, i), COLOR_ROW_ALT))
    if total_row:
        cmds.append(("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#DBEAFE")))
        cmds.append(("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"))
        cmds.append(("LINEABOVE", (0, -1), (-1, -1), 1, COLOR_BLUE))
    return cmds


def _styled_table(data, col_widths, numeric_cols=(), total_row=False, extra_cmds=None):
    table = Table(data, colWidths=col_widths, hAlign="LEFT", repeatRows=1)
    cmds = _zebra_table_style(len(data), numeric_cols=numeric_cols, total_row=total_row)
    if extra_cmds:
        cmds.extend(extra_cmds)
    table.setStyle(TableStyle(cmds))
    return table


# --- Block 1: Tarjeta de Resumen (ver conversacion) -------------------------

CARD_GAP = 0.4 * cm
USABLE_WIDTH = A4[0] - 4 * cm  # 2cm de margen a cada lado, ver render_pdf


def _stat_card_flowable(label, value_lines, width, styles):
    """Una tarjeta de resumen: fondo gris claro, esquinas redondeadas, label
    chico en mayuscula + 1-2 lineas de valor grande (coloreadas segun
    corresponda, ej. verde/rojo para Resultado del Día)."""
    content = [[Paragraph(label.upper(), styles["CardLabel"])]]
    for i, (text, color) in enumerate(value_lines):
        style_name = "CardValue" if i == 0 else "CardValueSub"
        content.append([_cell(text, styles[style_name], color=color)])

    card = Table(content, colWidths=[width])
    card.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), COLOR_LIGHT_GRAY),
        ("ROUNDEDCORNERS", [10, 10, 10, 10]),
        ("LEFTPADDING", (0, 0), (-1, -1), 14),
        ("RIGHTPADDING", (0, 0), (-1, -1), 14),
        ("TOPPADDING", (0, 0), (-1, 0), 12),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 2),
        ("TOPPADDING", (0, 1), (-1, -1), 2),
        ("BOTTOMPADDING", (0, -1), (-1, -1), 12),
    ]))
    return card


def _resumen_ejecutivo_flowable(section, styles):
    d = section["data"]
    card_width = (USABLE_WIDTH - 2 * CARD_GAP) / 3

    patrimonio_card = _stat_card_flowable(
        "Patrimonio Total",
        [(_fmt_usd(d["patrimonio_total_usd"]), COLOR_NAVY), (_fmt_ars(d["patrimonio_total_ars"]), None)],
        card_width, styles,
    )
    resultado_color = _signed_color(d["resultado_dia_ars"])
    if d["resultado_dia_ars"] is None:
        resultado_lines = [("Sin datos de ayer todavía", COLOR_TEXT_MUTED)]
    else:
        resultado_lines = [
            (f"{_fmt_ars(d['resultado_dia_ars'])}", resultado_color),
            (_fmt_pct(d["resultado_dia_pct"]), resultado_color),
        ]
    resultado_card = _stat_card_flowable("Resultado del Día", resultado_lines, card_width, styles)
    caucion_card = _stat_card_flowable(
        "Capital en Caución", [(_fmt_ars(d["capital_caucion_ars"]), COLOR_NAVY)], card_width, styles,
    )

    row = Table([[patrimonio_card, resultado_card, caucion_card]], colWidths=[card_width] * 3, spaceAfter=0)
    row.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ("LEFTPADDING", (1, 0), (1, 0), CARD_GAP), ("LEFTPADDING", (2, 0), (2, 0), CARD_GAP),
    ]))
    return [Paragraph(section["title"], styles["Heading2"]), row]


# --- Graficos (matplotlib) ---------------------------------------------------

def _holdings_allocation_chart(rows):
    """Donut de distribucion de cartera por tipo de instrumento -- usa
    valor_usd si esta disponible (DUAL/USD), si no valor_ars (modo ARS); son
    proporciones relativas, la moneda elegida no cambia el resultado. None
    si no hay ningun valor usable (ej. cartera vacia) -- nunca dibuja un
    grafico vacio/enganoso."""
    totals_by_type = {}
    for r in rows:
        value = r.get("valor_usd") if r.get("valor_usd") is not None else r.get("valor_ars")
        if not value:
            continue
        totals_by_type[_instrument_type_label(r["type"])] = totals_by_type.get(_instrument_type_label(r["type"]), 0.0) + value
    if not totals_by_type:
        return None

    items = sorted(totals_by_type.items(), key=lambda kv: -kv[1])
    labels = [k for k, _ in items]
    values = [v for _, v in items]
    palette = [CHART_PALETTE[i % len(CHART_PALETTE)] for i in range(len(labels))]

    def _autopct(pct):
        return f"{pct:.0f}%" if pct >= 5 else ""

    fig, ax = plt.subplots(figsize=(4.6, 3.6), dpi=150)
    wedges, _, autotexts = ax.pie(
        values, autopct=_autopct, startangle=90, colors=palette,
        wedgeprops=dict(width=0.42, edgecolor="white", linewidth=1.5), pctdistance=0.78,
        textprops={"fontsize": 8},
    )
    plt.setp(autotexts, size=7.5, weight="bold", color="white")
    ax.legend(wedges, labels, loc="center left", bbox_to_anchor=(1.0, 0.5), fontsize=8, frameon=False)
    ax.set_title("Distribución por Tipo de Instrumento", fontsize=10, fontweight="bold", color="#0F172A")
    ax.axis("equal")
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", transparent=False, bbox_inches="tight", dpi=150)
    plt.close(fig)
    buf.seek(0)
    return buf


def _top_movers_chart(rows, n=5):
    """Barras horizontales con los N mayores ganadores + N mayores
    perdedores por P&L (verde/rojo) -- None si no hay ninguna posicion con
    P&L calculable."""
    valid = [r for r in rows if r.get("pnl") is not None]
    if not valid:
        return None

    ordered = sorted(valid, key=lambda r: r["pnl"])
    losers = ordered[:n]
    gainers = ordered[-n:]
    seen = set()
    combined = []
    for r in losers + gainers:
        if r["symbol"] not in seen:
            seen.add(r["symbol"])
            combined.append(r)
    combined.sort(key=lambda r: r["pnl"])

    symbols = [r["symbol"] for r in combined]
    pnls = [r["pnl"] for r in combined]
    bar_colors = ["#16A34A" if v >= 0 else "#DC2626" for v in pnls]

    fig, ax = plt.subplots(figsize=(4.6, 3.6), dpi=150)
    ax.barh(symbols, pnls, color=bar_colors)
    ax.axvline(0, color="#0F172A", linewidth=0.8)
    ax.set_title("Mayores Ganancias y Pérdidas (P&L)", fontsize=10, fontweight="bold", color="#0F172A")
    ax.tick_params(labelsize=8)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", transparent=False, bbox_inches="tight", dpi=150)
    plt.close(fig)
    buf.seek(0)
    return buf


# --- Tenencias ---------------------------------------------------------------

def _holdings_table_flowable(section, styles):
    rows = section["data"]["rows"]
    currency = section["data"]["currency"]
    flowables = [Paragraph(section["title"], styles["Heading2"])]
    if not rows:
        flowables.append(Paragraph("Sin tenencias cargadas todavía.", styles["Normal"]))
        return flowables

    def _sort_key(r):
        value = r.get("valor_usd") if r.get("valor_usd") is not None else r.get("valor_ars")
        return -(value or 0.0)
    rows_sorted = sorted(rows, key=_sort_key)

    header = [_cell("Símbolo", styles["CellHeader"]), _cell("Tipo", styles["CellHeader"]), _cell("Cantidad", styles["CellHeader"])]
    numeric_cols = [2]
    col_widths = [2.6 * cm, 3.2 * cm, 2.4 * cm]
    if currency in (PDF_CURRENCY_ARS, PDF_CURRENCY_DUAL):
        header.append(_cell("Valor ARS", styles["CellHeader"]))
        numeric_cols.append(len(header) - 1)
        col_widths.append(3.2 * cm)
    if currency in (PDF_CURRENCY_USD, PDF_CURRENCY_DUAL):
        header.append(_cell("Valor USD", styles["CellHeader"]))
        numeric_cols.append(len(header) - 1)
        col_widths.append(3.0 * cm)
    header.append(_cell("P&L", styles["CellHeader"]))
    pnl_col = len(header) - 1
    numeric_cols.append(pnl_col)
    col_widths.append(USABLE_WIDTH - sum(col_widths))

    data = [header]
    total_ars = total_usd = total_pnl = 0.0
    for r in rows_sorted:
        row = [
            _cell(r["symbol"], styles["CellWrap"]), _cell(_instrument_type_label(r["type"]), styles["CellWrapMuted"]),
            _cell(_fmt_qty(r.get("cantidad")), styles["CellWrap"]),
        ]
        if currency in (PDF_CURRENCY_ARS, PDF_CURRENCY_DUAL):
            row.append(_cell(_fmt_ars(r.get("valor_ars"), 2), styles["CellWrap"]))
            total_ars += r.get("valor_ars") or 0.0
        if currency in (PDF_CURRENCY_USD, PDF_CURRENCY_DUAL):
            row.append(_cell(_fmt_usd(r.get("valor_usd")), styles["CellWrap"]))
            total_usd += r.get("valor_usd") or 0.0
        row.append(_cell(_fmt_usd(r.get("pnl")), styles["CellWrap"], color=_signed_color(r.get("pnl"))))
        total_pnl += r.get("pnl") or 0.0
        data.append(row)

    total_row = [_cell("TOTAL", styles["CellWrap"]), "", ""]
    if currency in (PDF_CURRENCY_ARS, PDF_CURRENCY_DUAL):
        total_row.append(_cell(_fmt_ars(total_ars, 2), styles["CellWrap"]))
    if currency in (PDF_CURRENCY_USD, PDF_CURRENCY_DUAL):
        total_row.append(_cell(_fmt_usd(total_usd), styles["CellWrap"]))
    total_row.append(_cell(_fmt_usd(total_pnl), styles["CellWrap"], color=_signed_color(total_pnl)))
    data.append(total_row)

    flowables.append(_styled_table(data, col_widths, numeric_cols=numeric_cols, total_row=True))

    allocation_png = _holdings_allocation_chart(rows_sorted)
    movers_png = _top_movers_chart(rows_sorted)
    chart_cells = []
    if allocation_png is not None:
        chart_cells.append(_image_flowable(allocation_png, width=8.2 * cm))
    if movers_png is not None:
        chart_cells.append(_image_flowable(movers_png, width=8.2 * cm))
    if chart_cells:
        flowables.append(Spacer(1, 10))
        chart_row = Table([chart_cells], hAlign="LEFT")
        chart_row.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("ALIGN", (0, 0), (-1, -1), "CENTER")]))
        flowables.append(chart_row)
    return flowables


# --- Metricas de riesgo / Caucion (tablas clave-valor) ----------------------

def _key_value_table_flowable(title, pairs, styles):
    data = [[_cell("Indicador", styles["CellHeader"]), _cell("Valor", styles["CellHeader"])]]
    for label, value, color in pairs:
        data.append([_cell(label, styles["CellWrap"]), _cell(value, styles["CellWrap"], color=color)])
    table = _styled_table(data, [9 * cm, USABLE_WIDTH - 9 * cm], numeric_cols=(1,))
    return [Paragraph(title, styles["Heading2"]), table]


def _risk_flowable(section, styles):
    m = section["data"]
    pairs = [
        ("Volatilidad anualizada", _fmt_pct(m.get("annualized_vol_pct"), signed=False), None),
        ("Caída máxima (Drawdown)", _fmt_pct(m.get("max_drawdown_pct"), signed=False), COLOR_RED if m.get("max_drawdown_pct") else None),
        ("Pérdida esperada en el 5% peor de los meses", _fmt_pct(m.get("cvar95_monthly_pct"), signed=False), None),
        ("Concentración en las 5 posiciones más grandes", _fmt_pct(m.get("top5_concentration_pct"), signed=False), None),
    ]
    return [KeepTogether(_key_value_table_flowable(section["title"], pairs, styles))]


def _paper_trading_flowable(section, styles):
    d = section["data"]
    summary_pairs = [
        ("Patrimonio virtual", _fmt_usd(d.get("equity_usd")), None),
        ("Resultado del día", _fmt_pct(d.get("resultado_dia_pct")), _signed_color(d.get("resultado_dia_pct"))),
        ("Posiciones abiertas", str(d.get("open_positions_count", 0)), None),
        ("Operaciones cerradas hoy", str(d.get("trades_closed_today_count", 0)), None),
    ]
    flowables = _key_value_table_flowable(section["title"], summary_pairs, styles)

    positions = d.get("positions_detail") or []
    if positions:
        flowables.append(Spacer(1, 10))
        header = [
            _cell("Activo", styles["CellHeader"]), _cell("Estrategia", styles["CellHeader"]),
            _cell("Precio Entrada USD", styles["CellHeader"]), _cell("Valor Actual", styles["CellHeader"]),
            _cell("Resultado PnL %", styles["CellHeader"]),
        ]
        col_widths = [2.6 * cm, 5.2 * cm, 3.2 * cm, 3.2 * cm]
        col_widths.append(USABLE_WIDTH - sum(col_widths))
        data = [header]
        for p in positions:
            estrategia = _engine_label(p["engine"])
            if p.get("tactic"):
                estrategia += f" · {_tactic_label(p['tactic'])}"
            data.append([
                _cell(p["symbol"], styles["CellWrap"]), _cell(estrategia, styles["CellWrapMuted"]),
                _cell(_fmt_usd(p["entry_price_usd"]), styles["CellWrap"]),
                _cell(_fmt_usd(p["valor_actual_usd"]), styles["CellWrap"]),
                _cell(_fmt_pct(p["pnl_pct"]), styles["CellWrap"], color=_signed_color(p["pnl_pct"])),
            ])
        flowables.append(_styled_table(data, col_widths, numeric_cols=(2, 3, 4)))
    elif d.get("open_positions_count", 0) == 0:
        flowables.append(Paragraph("Sin posiciones abiertas de Paper Trading hoy.", styles["Normal"]))
    return [KeepTogether(flowables[:2])] + flowables[2:]


def _yield_caucion_flowable(section, styles):
    d = section["data"]
    pairs = [
        ("Cauciones vigentes", str(d.get("n_vigentes", 0)), None),
        ("Capital colocado", _fmt_ars(d.get("total_valor_actual"), 2), None),
    ]
    return [KeepTogether(_key_value_table_flowable(section["title"], pairs, styles))]


# --- Block 3: Señales y Oportunidades del Día -------------------------------

def _opportunities_flowable(section, styles):
    d = section["data"]
    flowables = [Paragraph(section["title"], styles["Heading2"])]
    if not d.get("available"):
        flowables.append(Paragraph(
            "No se pudieron calcular las señales de hoy (el servicio de pronóstico no respondió a tiempo).",
            styles["Normal"],
        ))
        return [KeepTogether(flowables)]

    notes = []
    if not d["spy_above_ema50"]:
        notes.append("El mercado global no acompaña hoy -- el Fondo de Mediano Plazo no opera, el capital queda en Caución.")
    if d["market_regime"] == "alta_volatilidad":
        notes.append("Volatilidad alta -- la Estrategia Corto Plazo no abre posiciones nuevas hoy.")

    all_signals = (
        [("core", s) for s in d["core_signals"]] + [("satellite", s) for s in d["satellite_signals"]]
    )
    if not all_signals:
        flowables.append(Paragraph("Ninguna oportunidad detectada hoy.", styles["Normal"]))
        for n in notes:
            flowables.append(Paragraph(n, styles["Meta"]))
        return [KeepTogether(flowables)]

    header = [
        _cell("Motor", styles["CellHeader"]), _cell("Símbolo", styles["CellHeader"]),
        _cell("Entrada", styles["CellHeader"]), _cell("Ganancia Objetivo", styles["CellHeader"]),
        _cell("Límite de Pérdida", styles["CellHeader"]),
    ]
    col_widths = [3.4 * cm, 2.2 * cm, 3.6 * cm]
    col_widths += [(USABLE_WIDTH - sum(col_widths)) / 2] * 2
    data = [header]
    for engine, s in all_signals:
        estrategia = _engine_label(engine)
        tactics = s.get("tactics") or []
        if tactics:
            estrategia += " · " + ", ".join(_tactic_label(t) for t in tactics)
        # _fmt_usd/_fmt_ars nunca producen "&"/"<"/">" (solo numero + prefijo/
        # sufijo fijo), por eso es seguro armar el <br/> directo aca sin pasar
        # por _cell (que lo escaparia como texto literal, ver su docstring).
        entrada = Paragraph(f"{_fmt_usd(s['entry_price_usd'])}<br/>({_fmt_ars(s['entry_price_ars'], 2)})", styles["CellWrap"])
        objetivo = f"{_fmt_usd(s['take_profit_price_usd'])} ({_fmt_pct(s['take_profit_pct'])})"
        limite = f"{_fmt_usd(s['initial_stop_price_usd'])} ({_fmt_pct(-abs(s['initial_stop_pct']))})"
        data.append([
            _cell(estrategia, styles["CellWrapMuted"]), _cell(s["symbol"], styles["CellWrap"]),
            entrada,
            _cell(objetivo, styles["CellWrap"], color=COLOR_GREEN), _cell(limite, styles["CellWrap"], color=COLOR_RED),
        ])
    flowables.append(_styled_table(data, col_widths))
    for n in notes:
        flowables.append(Spacer(1, 4))
        flowables.append(Paragraph(n, styles["Meta"]))
    return [KeepTogether(flowables)]


_FLOWABLE_BUILDERS = {
    "resumen_ejecutivo": _resumen_ejecutivo_flowable,
    "holdings_table": _holdings_table_flowable,
    "risk_metrics": _risk_flowable,
    "paper_trading_summary": _paper_trading_flowable,
    "yield_caucion": _yield_caucion_flowable,
    "oportunidades_dia": _opportunities_flowable,
}


def _draw_header_footer(canvas, doc):
    """Banda de encabezado (titulo + fecha/hora + numero de pagina, pedido
    explicito de layout) y pie de pagina (disclaimer) en CADA pagina --
    dibujado directo en el canvas (onFirstPage/onLaterPages), no como
    flowable, para que se repita automaticamente sin volver a declararlo en
    cada seccion."""
    canvas.saveState()
    page_height = A4[1]
    canvas.setFillColor(COLOR_NAVY)
    canvas.rect(0, page_height - HEADER_HEIGHT, A4[0], HEADER_HEIGHT, fill=1, stroke=0)
    canvas.setFillColor(colors.white)
    canvas.setFont("Helvetica-Bold", 13)
    canvas.drawString(2 * cm, page_height - HEADER_HEIGHT + 0.6 * cm, HEADER_TITLE)
    canvas.setFont("Helvetica", 8.5)
    canvas.drawRightString(
        A4[0] - 2 * cm, page_height - HEADER_HEIGHT + 0.65 * cm,
        f"{now_ar().strftime('%d/%m/%Y %H:%M')} ART",
    )
    canvas.drawRightString(
        A4[0] - 2 * cm, page_height - HEADER_HEIGHT + 0.3 * cm,
        f"Página {canvas.getPageNumber()}",
    )

    canvas.setStrokeColor(COLOR_BORDER)
    canvas.setLineWidth(0.5)
    canvas.line(2 * cm, 1.3 * cm, A4[0] - 2 * cm, 1.3 * cm)
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(COLOR_TEXT_MUTED)
    canvas.drawCentredString(A4[0] / 2, 0.9 * cm, DISCLAIMER_TEXT)
    canvas.restoreState()


def render_pdf(sections, output_path=None):
    """Arma el PDF (reportlab) a partir de la lista de secciones YA
    resueltas (ver build_report_sections) -- devuelve los bytes del PDF;
    si se pasa output_path, ADEMAS lo escribe a disco ahi."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        topMargin=HEADER_HEIGHT + 0.6 * cm, bottomMargin=1.8 * cm, leftMargin=2 * cm, rightMargin=2 * cm,
    )
    styles = _build_styles()

    story = []
    for section in sections:
        builder = _FLOWABLE_BUILDERS.get(section["kind"])
        if builder is None:
            continue  # kind desconocido -- se omite, nunca rompe el reporte entero por una seccion rara
        story.extend(builder(section, styles))
        story.append(Spacer(1, 16))

    doc.build(story, onFirstPage=_draw_header_footer, onLaterPages=_draw_header_footer)
    pdf_bytes = buffer.getvalue()
    if output_path:
        with open(output_path, "wb") as f:
            f.write(pdf_bytes)
    return pdf_bytes


def generate_portfolio_pdf(conn, output_path=None, config=None):
    """Punto de entrada unico -- combina build_report_sections + render_pdf.
    Este es el que debe llamar portfolio/telegram_bot.py (job programado y
    el boton 'Enviar Reporte de Prueba Ahora') y cualquier ruta web nueva."""
    sections = build_report_sections(conn, config=config)
    return render_pdf(sections, output_path=output_path)
