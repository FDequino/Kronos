# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Un único usuario (el dueño del repo), gestionando su propia cartera de inversión personal. No es una herramienta multi-tenant ni pensada para terceros por ahora.

## Product Purpose

Plataforma personal que unifica (1) un modelo de forecasting de IA (Kronos, foundation model open-source para K-lines) con (2) datos reales de mercado en vivo (BYMA, dólar argentino, cripto) y (3) un registro de cartera propia (acciones, CEDEARs, bonos, obligaciones negociables, plazos fijos, USD), para decidir y hacer seguimiento de inversiones reales. El objetivo final declarado por el usuario incluye automatizar señales de compra/venta a partir de los forecasts.

## Positioning

Ningún bróker ni app de trading argentina combina, en una sola herramienta personal: forecasting de un foundation model (no indicadores técnicos genéricos) + datos en vivo multi-fuente (BYMA via data912, USD via dolarapi, cripto via Binance) + registro de cartera mixta (mercado + productos bancarios como plazo fijo) sin atarse a un bróker específico.

## Operating Context

- Corre localmente como una app Flask (`webui/app.py`) servida en `localhost:7070`, sin autenticación (uso personal, no desplegado públicamente), con un worker en background (un thread + `queue.Queue`, sin Celery/Redis) para correr forecasts de Kronos sin bloquear el servidor.
- Tres superficies hoy: el predictor Kronos original (carga CSV o trae datos en vivo con cualquier par de Binance, corre el modelo libremente, fuera de la cartera), la gestión de cartera (`/portfolio#resumen|instrumentos|transacciones|plazos-fijos`), y dentro de esa misma página, Señales (forecast + señal automática por holding) y Explorador BYMA — este último lista el universo completo de BYMA (~1300 instrumentos: acciones, CEDEARs, bonos) por defecto, no solo tras buscar; el buscador y el filtro por tipo acotan esa lista ya visible, con columnas ordenables y forecast on-demand al entrar al detalle de cualquiera. Cualquier símbolo (propio, desde Instrumentos/Transacciones, o del Explorador) abre en una página de detalle dedicada (`#detalle/<símbolo>`) organizada en pestañas (Pronóstico/Posición/Riesgo) — nunca una expansión dentro de una grilla ni una sola columna larga.
- Buscador único fijo arriba (no se va con el scroll) que filtra la tabla de la vista activa; Tenencias además tiene chips para filtrar por tipo de instrumento, y Transacciones/Dividendos tienen un filtro de rango de fechas — todo client-side, sin pegarle de nuevo al backend.
- Cada métrica calculada (hoy: riesgo, por instrumento y de cartera) lleva un ícono de ayuda que muestra, en un popover, la frase explicativa en lenguaje llano *y* la fórmula sustituida con los números reales de esa instancia — nunca solo el número final sin cómo se llegó a él.
- Pestaña "Precisión" en el detalle de cada instrumento: compara lo que Kronos predijo en pronósticos pasados contra el precio real que efectivamente tuvo lugar (MAPE y % de acierto direccional), usando solo pronósticos cuya fecha predicha ya pasó. Si todavía no pasó tiempo suficiente, lo dice explícitamente en vez de mostrar un número o gráfico vacío.
- Resumen deja de ser una foto estática: cada KPI (Valor Total, P&L, Posiciones, Cotización USD) expande su propio detalle *en el lugar* al hacer click, nunca navega a otra parte; el riesgo de cartera muestra directo sus mayores contribuyentes; hay un resumen narrativo de una línea; y seis gráficos (asignación por tipo, riesgo, evolución real-vs-teórica, P&L absoluto, P&L %, distribución de riesgo), todos con ApexCharts (tooltips y leyendas reales, no SVG a mano).
- El historial de valor de cartera se **reconstruye retroactivamente** desde la primera transacción real (no solo desde que se agregó la funcionalidad), usando `transactions` + `price_history` ya importados, con una serie teórica adicional de "qué hubiera valido si Kronos hubiera tenido razón" donde haya pronósticos que cubran esa fecha. Instrumentos sin histórico de precios disponible se listan explícitamente, nunca se omiten en silencio.
- Notificaciones (vencimientos, dividendos cobrados, señales de venta) viven en un ícono de campana en la barra superior, accesible desde cualquier pantalla, no solo en Resumen.
- Todo monto en pantalla lleva su símbolo de moneda explícito ($ o USD) vía un helper centralizado, nunca un número ambiguo.
- Persistencia en SQLite (`portfolio/portfolio.db`), sin servidor de base de datos externo. Los forecasts se cachean (tabla `forecasts`) con un umbral de frescura configurable (`signal_config.freshness_hours`) para no recalcular en cada carga de página.
- Fuentes de datos externas, todas gratuitas y sin API key: `data912.com` (BYMA: acciones, CEDEARs, bonos, obligaciones negociables — actualiza cada ~20s, no tick-by-tick), `dolarapi.com` (cotizaciones de dólar: oficial/blue/MEP/CCL/cripto/mayorista/tarjeta), API pública de Binance (cripto).
- Los plazos fijos no cotizan en mercado: se cargan a mano (banco, monto, TNA, plazo) y el devengado se calcula con interés simple, no se trae de ninguna API.
- Modelo único de forecasting: Kronos-base (102.3M params) en todos los casos — en este hardware (Apple M4, MPS) correr el modelo grande no es significativamente más lento que el chico (~49s vs ~28s para pred_len=120), así que no se justifica mantener dos tamaños. Benchmark real guardado en `signal_config.benchmark_seconds_per_step` para estimar el progreso.

## Capabilities and Constraints

- Tipos de instrumento soportados: acción, CEDEAR, bono, obligación negociable, fondo común (precio manual, sin histórico), moneda (USD/cripto), plazo fijo, caución — extensible sin tocar código (alta de instrumento = fila nueva en la base, no hardcodeo de tickers). Cauciones se muestran para tener el panorama patrimonial completo aunque Kronos no pueda pronosticarlas (no cotizan, son un préstamo de muy corto plazo). Dividendos y rentas se registran aparte como ingresos, no como instrumentos.
- Tenencias y costo promedio siempre derivados de las transacciones registradas (compra/venta), nunca almacenados como número fijo, para que nunca se desincronicen.
- Bimoneda real: cada instrumento tiene su moneda nativa (ARS o USD); el usuario elige en qué moneda ver toda la cartera y con qué variante de cotización del Banco Nación (billete/divisa × compra/venta, default divisa venta) se hace la conversión — nunca un tipo de cambio inventado o promediado.
- Riesgo por instrumento y de cartera: metodología explícita y configurable (`signal_config`), nunca una caja negra — volatilidad histórica anualizada combinada con un riesgo base por tipo de instrumento, más una penalidad de concentración a nivel cartera. Se degrada con gracia (usa solo el riesgo base) cuando no hay histórico suficiente (fondos, instrumentos recién cargados).
- Señal de compra/venta/mantener: heurística de umbral configurable (`signal_config`) sobre el % que pronostica Kronos, nunca presentada como asesoramiento financiero real — la UI lo deja explícito en cada tarjeta. La incertidumbre (cuando `sample_count>1`) es la dispersión real entre las muestras del modelo, no un número inventado.
- Pendiente (explícitamente no construido aún): editar/borrar instrumentos y transacciones, progreso real del forecast vía streaming (hoy es una barra estimada por tiempo, no un stream paso a paso), indicador MACD en el gráfico (solo SMA/EMA/RSI por ahora).
- Sin requerimiento de accesibilidad específico más allá de buenas prácticas estándar (no se estableció ninguno adicional).

## Evidence on Hand

- Repo real en `/Users/francodequino/Desktop/Proyectos/Finanzas/Kronos`, clon de `github.com/shiyu-coder/Kronos` (modelo open-source, MIT) con una WebUI propia agregada en `webui/` y un módulo de cartera propio en `portfolio/`.
- No hay logo, marca, ni assets de identidad visual definidos todavía — es un proyecto propio sin marca externa que respetar.

## Product Principles

1. Nada hardcodeado: instrumentos, fuentes de precio y parámetros viven en datos (SQLite), no en código — agregar un activo nuevo nunca implica un deploy.
2. Las cifras de cartera (tenencias, costos, P&L, devengado) se calculan siempre desde el registro transaccional fuente, nunca se cachean como verdad independiente.
3. Es una herramienta de decisión financiera real, no una demo — la seriedad y precisión visual importan tanto como la funcional (el usuario explícitamente rechazó una estética de "hecho por hacer" con iconos informales).
4. Datos en vivo vienen de fuentes gratuitas sin autenticación; cuando una fuente no pueda dar un dato (p. ej. histórico de obligaciones negociables), se declara la limitación en vez de inventar un sustituto.
