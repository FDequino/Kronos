# Design System & Frontend Audit — Kronos / Mi Cartera

Diagnóstico del `webui/` actual: qué sistema de diseño existe de hecho (no el
aspiracional), dónde vive, y qué inconsistencias visuales arrastra. Basado en
inspección directa de `webui/templates/`, `webui/static/css/` y
`webui/static/js/` el 2026-10-15.

> Nota: ya existe un `DESIGN.md` en la raíz (más centrado en producto/UX de
> "Mi Cartera"). Este documento es el complemento *técnico*: tokens, CSS,
> JS de charts y deuda visual concreta encontrada en el código.

---

## a) Paleta de colores

### Fuente de verdad: `webui/static/css/tokens.css`

El tema es **dark-first**: `:root` define directamente la paleta oscura (no
hay un bloque `[data-theme="dark"]` separado). El claro vive aparte, en
`webui/static/css/theme-light.css`, como override explícito bajo
`:root[data-theme="light"]` — comentado en el propio archivo como *"Paleta
clara propia, no es el tema oscuro invertido"*.

| Token | Oscuro (default) | Claro (`[data-theme="light"]`) | Uso |
|---|---|---|---|
| `--bg-sidebar` | `#0b0d11` | `#ffffff` | Fondo de sidebar |
| `--bg-content` | `#12151c` | `#f3f4f6` | Fondo de página |
| `--bg-elevated` | `#1a1e27` | `#ffffff` | Panels, cards, modal |
| `--bg-elevated-hover` | `#20242f` | `#eef0f3` | Hover de filas/botones |
| `--border` | `#242933` | `#e3e5ea` | Bordes sutiles |
| `--border-strong` | `#323847` | `#c7cbd4` | Bordes de inputs/botones secundarios |
| `--text-primary` | `#e7e9ee` | `#14161a` | Texto principal |
| `--text-secondary` | `#8a93a3` | `#585f6b` | Texto secundario |
| `--text-tertiary` | `#828b9c` | `#6b7280` | Labels, hints |
| `--accent` / `--accent-strong` | `#4c7dff` / `#759bff` | `#3b6fe0` / `#2851b8` | Acento de marca, links, botón primario |
| `--positive` | `#30c177` | `#1f9d57` | Ganancia, señal "comprar" |
| `--negative` | `#ef5b5b` | `#d63939` | Pérdida, señal "vender" |
| `--warning` | `#e0a63c` | `#b5790a` | Alertas, SMA en gráficos |

Cada color semántico (`positive`/`negative`/`warning`/`accent`) tiene su
variante `-dim` (alpha ~0.10–0.14) para fondos de badges (`.status.success`,
`.status.error`, etc.) — ese patrón color/color-dim es consistente en todo
`tokens.css` y se respeta bien en `status.css`.

### Colores de señales comprar/vender/mantener

No existen clases `.signal-buy` / `.signal-sell` dedicadas. El sistema de
señales (`comprar`/`vender`/`mantener`, ver `portfolio/signals.py` y
`webui/static/js/portfolio/technical-signal.js`) se pinta reutilizando las
clases **genéricas** `.cell-positive` / `.cell-negative` / `.cell-muted`
(`table.css:333-334`):

```css
.cell-positive { color: var(--positive); }
.cell-negative { color: var(--negative); }
```

`technicalCellHtml()` en `technical-signal.js` decide la clase en JS según
`edge > 0` (no según el string `'comprar'`/`'vender'` directamente), y solo
colorea si el edge supera un umbral (`EDGE_SIGNIFICANT_PTS = 5`); si no,
cae a `.cell-muted` (texto gris) aunque exista señal. Es una decisión de
diseño intencional y documentada en el propio archivo ("no mentir con color
sobre una señal sin edge real"), no un bug — pero vale remarcarlo: el mismo
verde/rojo se usa para P&L, para señales técnicas y para edge de backtest,
sin distinción visual entre esos tres significados.

### Tipografías y escala

```css
--font-sans: 'IBM Plex Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
--font-mono: 'IBM Plex Mono', 'SF Mono', Consolas, monospace;
```

Escala modular de 6 pasos (`--step--1` a `--step-4`, de 0.8125rem a 1.75rem),
pensada para densidad de dashboard financiero más que para jerarquía
editorial. `.num` fuerza `font-variant-numeric: tabular-nums` para que
columnas de números financieros alineen — aplicado de forma consistente en
tablas y stat tiles.

---

## b) Componentes visuales recurrentes

| Componente | Archivo CSS | Notas |
|---|---|---|
| **Panel** | `components/panel.css` | Contenedor base (`--bg-elevated` + `--border` + `--radius`). `.panel-sticky-head` resuelve headers pegajosos sobre tabla con scroll de página (reemplazó un patrón viejo de `.table-scroll` con `70vh` + doble scroll, según comentario en el propio CSS). |
| **Tabla** | `components/table.css` | `.table-toolbar`, `.row-actions`, `.cell-positive/negative/muted`. Base de casi todas las vistas de datos (Holdings, Señales, Transacciones). |
| **Botones** | `components/buttons.css` | `.btn-primary` (fill accent) / `.btn-secondary` (outline). Estado `:active` con `scale(0.97)`, `:disabled` con opacidad — consistente, sin variantes huérfanas. |
| **Status / Alerts** | `components/status.css`, `components/alerts.css` | `.status.success/error/info/warning` sobre el patrón `color` + `color-dim` + borde con alpha manual (`rgba(...)` repetido en vez de un token `--*-border`). |
| **Badges / Pills** | `search-bar.css` (`.search-bar-pill`) | Pills de filtro activo; no hay un componente "badge" genérico reusado entre filtros y señales. |
| **Risk gauge** | `components/risk-gauge.css` | Medio velocímetro SVG por tile (`.gauge-tile`), grid de 3 con fallback a 1 columna en mobile. |
| **Forecast card** | `components/forecast-card.css` | Tarjeta de resultado de Kronos/Chronos-Bolt (ver `webui/static/js/portfolio/forecast-card.js`). |
| **Instrument detail** | `components/instrument-detail.css` | Vista de detalle con tabs técnicos (RSI/MACD/BB) + accuracy histórico. |
| **Sidebar / Subtabs / Segmented toggle** | `sidebar.css`, `subtabs.css`, `segmented-toggle.css` | Navegación; subtabs son sticky y miden su propia altura vía `shared/sticky-offset.js` (`--subtabs-h`), consumida por `panel.css` y `layout.css` — acoplamiento intencional y documentado, pero implícito (nada en el nombre de la variable avisa que depende de un `ResizeObserver` externo). |
| **Modal / Tooltip** | `modal.css`, `info-tooltip.css` | Estándar, sin hallazgos. |

---

## c) Gráficos: cómo se renderizan velas y predicciones

Dos librerías conviven, con responsabilidades bien separadas:

- **Lightweight Charts (TradingView)** — el motor principal. Usado en:
  - `candlestick-chart.js`: velas OHLC (`CandlestickSeries`), línea de
    predicción de Kronos/Chronos-Bolt (`LineSeries`, `LineStyle.Dashed`,
    color `PALETTE.accent`), SMA/EMA, Bollinger Bands, panes separados para
    RSI y MACD (cada indicador es un `createChart` independiente, no un
    pane dentro del mismo chart), y el **fan chart** de incertidumbre
    (`p10_pct`/`p90_pct` como líneas punteadas alrededor de la media) usado
    en `renderAccuracyChart`/forecast con distribución de cuantiles.
  - `portfolio-history-chart.js`: evolución de cartera (cartera real vs.
    teórica).
  - `benchmark-comparison-chart.js`: cartera vs. caución vs. inflación vs.
    SPY, con selector de período (1M/3M/YTD/1A/desde inicio).
  - `risk-gauge.js`: usa Lightweight Charts para el arco del velocímetro
    (no SVG puro a pesar de lo que sugiere `risk-gauge.css`).
- **ApexCharts** — un único uso: `allocation-donut.js` (dona de
  composición de cartera por clase de activo). Es la única dependencia de
  ApexCharts en todo el frontend; `apex-theme.js` centraliza su theming.

**Cómo llega la predicción al chart**: el backend (`portfolio/forecasting.py`
→ `model/client.py::ChronosServiceClient`) devuelve media + cuantiles del
microservicio Chronos-Bolt; el frontend no sabe (ni necesita saber) que el
motor cambió de Kronos autoregresivo a Chronos-Bolt por cuantiles — consume
el mismo contrato de forecast ya armado del lado Python. La reconstrucción
de open/high/low sintéticos (`open=high=low=close`, documentada como tal en
`services/predictor/main.py`) ocurre en `model/client.py`, nunca en JS.

---

## d) Puntos de dolor estéticos / inconsistencias detectadas

Hallazgos concretos en el código actual, no apreciaciones subjetivas:

1. **Los gráficos no reaccionan al cambio de tema (dark/light).**
   `shared/theme.js` dispara un evento `theme-changed` al togglear el tema,
   pero solo dos módulos lo escuchan: `holdings.js` y `instrument-detail.js`
   (este último sí re-renderiza el candlestick chart). **Ningún otro
   chart** — `portfolio-history-chart.js`, `benchmark-comparison-chart.js`,
   `risk-gauge.js`, la dona de `allocation-donut.js` — se suscribe al
   evento. Si el usuario cambia a tema claro con esas vistas abiertas,
   quedan con grid/texto pensados para fondo oscuro sobre un panel ahora
   claro.

2. **Tres `PALETTE` independientes, con colores hardcodeados en hex en vez
   de leer `tokens.css`.** `candlestick-chart.js`, `benchmark-comparison-chart.js`
   y `portfolio-history-chart.js` definen cada uno su propio objeto
   `PALETTE` con los mismos valores literales repetidos a mano
   (`'#242933'`, `'#8a93a3'`, `'#30c177'`, `'#4c7dff'`...). Esto es, en
   parte, *la causa raíz* del punto 1: como Lightweight Charts no puede leer
   `var(--accent)` directamente, alguien copió el valor numérico una vez por
   archivo — y si `tokens.css` cambia un color, hay que recordar
   actualizarlo en 3 lugares más, a mano, sincronizados.

3. **Colores de marca hardcodeados fuera de `tokens.css`.**
   - `components/header-bar.css:34-35` define el badge de notificaciones
     con `background: #c0392b; color: #fff;` en vez de
     `var(--negative)` / un token de texto-sobre-color. Es un rojo
     *distinto* al `--negative` (`#ef5b5b`) del resto de la app — dos rojos
     de "alerta" conviviendo sin que sea una decisión declarada.
   - `components/grid12.css` tiene 6 ocurrencias de `#fff` hardcodeado y
     usa `!important` (es, junto con `base.css` y `allocation-chart.css`,
     uno de los 3 únicos archivos CSS que recurren a `!important` en todo
     el proyecto) — perfil de un componente más viejo o menos integrado al
     sistema de tokens que el resto.

4. **`index.html` (la página "Predictor Kronos", separada de
   `portfolio.html`) concentra 43 de los 43 `style="..."` inline de todo
   `webui/templates/`.** Son en su mayoría `margin-bottom: Npx` repetidos
   a mano en vez de una clase utilitaria o espaciado por `gap` en el
   contenedor. Es la vista heredada del Kronos original (probar el modelo
   "libre" con CSV propio o Binance, fuera de la cartera) y visualmente no
   comparte el nivel de pulido ni el patrón class-based de `portfolio.html`
   — tiene sentido como producto (son superficies distintas: "jugar con el
   modelo" vs. "mi cartera real"), pero como UI es la pieza visiblemente
   menos integrada al design system actual.

5. **Mismo color, tres significados distintos sin distinción visual.**
   `--positive`/`--negative` se usan simultáneamente para P&L de cartera,
   para la columna de señal técnica (`cell-positive`/`cell-negative` en
   `technical-signal.js`) y para el edge vs. tasa base del backtest. No hay
   bug funcional (cada contexto es legible por separado), pero un usuario
   escaneando la tabla de Señales no tiene una pista visual de que "verde"
   en la columna Técnico y "verde" en la columna P&L no miden lo mismo.

6. **Acoplamiento implícito entre CSS y JS vía variable CSS calculada,
   documentado solo a medias.** `--subtabs-h` (consumida por
   `.panel-sticky-head` y `.page-sticky-head` en `panel.css`) depende de que
   `shared/sticky-offset.js` la setee con un `ResizeObserver` en tiempo de
   ejecución. `.panel-sticky-head` sí tiene un comentario apuntando a ese
   archivo; `.page-sticky-head`, que usa la misma variable un poco más abajo,
   no lo repetía — quien tocara el sticky header leyendo solo esa segunda
   regla no tenía ninguna pista de la dependencia. *(Corregido: ver commit
   que agrega el comentario faltante en `.page-sticky-head`.)*

### Resumen priorizado

| # | Hallazgo | Impacto | Estado |
|---|---|---|---|
| 1-2 | Charts no seguían el tema (3 `PALETTE` hardcodeadas + `apex-theme.js` con un 4to mapa DARK/LIGHT propio) | Visual, solo en tema claro | **Corregido** — `shared/chart-palette.js` (nuevo) lee `getComputedStyle` de tokens.css en vivo; `candlestick-chart.js`, `portfolio-history-chart.js`, `benchmark-comparison-chart.js` y `apex-theme.js` (de donde cuelga `allocation-donut.js`) lo consumen y se suscriben a `theme-changed`, reaplicando color en el chart ya creado (`chart.applyOptions`/`series.applyOptions`) sin re-pedir datos. |
| 3 | Rojo duplicado en header-bar + `!important`/hex en grid12 | Bajo, cosmético | **Corregido** — `header-bar.css` usa `var(--negative)`; el `!important` de `grid12.css` se reemplazó por selectores de igual especificidad (gana por orden de cascada); los `#fff` del hero-card se centralizaron en una única variable local `--hero-fg` con el porqué documentado (no es un token de tema: blanco fijo sobre superficie de acento). |
| 4 | `index.html` con estilos inline | Bajo, página secundaria | **Corregido** — los 43 `style="..."` se reemplazaron por clases utilitarias (`.space-b`, `.hidden-block`, `.btn-block`, etc., ver el propio `index.html`), mismo patrón class-based que `portfolio.html`. |
| 5 | Semántica de color compartida sin distinción | Bajo, confusión potencial de usuario | Pendiente — ya mitigado parcialmente por el umbral `EDGE_SIGNIFICANT_PTS`, sin cambios adicionales en esta ronda |
| 6 | Acoplamiento CSS/JS implícito (`--subtabs-h`) documentado solo a medias | Bajo, riesgo de regresión futura | **Corregido** — se agregó el comentario faltante en `.page-sticky-head` apuntando a `sticky-offset.js` |

---

*Generado por inspección directa del código en `webui/` — no incluye
capturas de pantalla ni auditoría de accesibilidad (contraste, foco,
lectores de pantalla), que quedan fuera de este alcance.*
