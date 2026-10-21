# Objetivo Marco

<!-- Definido 2026-10-20, sobre commit 8ce737c, despues de un relevamiento
completo del proyecto (codigo + infraestructura local y VPS corridos y
verificados ese mismo dia, no asumidos de conversaciones anteriores). Vara
contra la que se evalua toda decision de que construir despues. -->

## Vara de éxito

Rendimiento medido en **USD**, nunca en ARS ni en términos absolutos sin
comparación. Benchmark compuesto, ponderado exactamente como la cartera real:

- Porción argentina (acciones, CEDEARs, bonos, ON, fondos en ARS) → **Merval** en USD.
- Porción US/CEDEAR subyacente → **S&P 500**.
- Ponderación = el peso real de cada porción en la cartera al momento de medir, no un 50/50 fijo.

No alcanza con ganar plata: una estrategia que gana en USD pero le pierde al
benchmark de su propia porción no se considera exitosa.

## Horizonte de evaluación

**Continuo (rolling 3/6/12 meses), sin fecha de corte fija.** No se espera a
una fecha calendario para juzgar el proyecto — cada estrategia/sleeve se
evalúa contra su propio historial acumulado en todo momento, y pasa o no
pasa el gate de "lista para plata real" (ver abajo) cuando la evidencia lo
sostiene, sea cuando sea.

## Qué está validado hoy (evidencia real, actualizado 2026-10-20) — punto de partida

**Bug crítico encontrado y corregido en el propio gate de validación (DSR)**: `deflated_sharpe_ratio` comparaba su `sr_hat` (siempre sin anualizar) contra un `sr0` construido con Sharpes YA anualizados — un desfasaje de escala (~√252) que hacía que el DSR saliera pegado a 0 CASI SIEMPRE, sin importar si había edge real o no. Esto afectó las 3 corridas de CPCV+DSR del proyecto (meta-modelo Core, backtest unificado, y la nueva validación por estrategia). Corregido agregando un campo `sharpe_ratio_raw` (sin anualizar) usado específicamente para alimentar el DSR, con test de regresión (`tests/test_strategy_cpcv_validation.py`). **Tras la corrección, el veredicto de Core NO cambia** (sigue sin edge significativo, DSR=0.0073 — la conclusión original era correcta aunque el número estaba mal calculado), **pero el veredicto de 3 de los 4 candidatos nuevos SÍ cambia, de "no probado" a VALIDADO**.

**Candidatos que GANAN plata en USD y ahora pasaron el gate estadístico completo (DSR≥0.95, con comisión real y muestra razonable):**

| Estrategia | Resultado real (USD, anual) | Muestra | DSR (corregido) | Estado |
|---|---|---|---|---|
| Carry Trade funding rate BTCUSDT | **+6.9%**, Sharpe 6.64 | 87 rebalanceos, ~7 años reales de Binance | **1.0000** | **VALIDADO** — falta paper trading + drawdown real + batir benchmark |
| Carry Trade funding rate ETHUSDT | **+8.5%**, Sharpe 6.54 | 84 rebalanceos, ~7 años reales de Binance | **1.0000** | **VALIDADO** — ídem |
| Canje AL29/GD29 (arbitraje de bonos) | **+24.8%**, Sharpe 1.54 | 56 operaciones, 1233 ruedas | **0.9864** | **VALIDADO** — ídem |
| Factor Investing (Momentum+Low-Vol+Value en CEDEARs) | **+15.3%**, Sharpe 0.73 | 0 trades discretos (rotación), 600 ruedas | 0.2188 | NO significativo todavía (menos consistente entre sub-períodos que los otros 3) |

**Confirmado que NO funciona (o no se puede medir todavía):**

- **Core (Chronos-Bolt point-forecast + meta-modelo)**: -17% anual real, **Deflated Sharpe Ratio = 0.0073 (recalculado tras corregir el bug de escala arriba) — sigue NO significativo**, aunque el margen real (SR_hat 0.072 vs. SR0 0.099) es más chico de lo que el 0.0000 original sugería. Confirmado además por literatura externa: modelos de forecasting tipo Chronos/TimesFM/Moirai apenas superan un random walk en acciones líquidas — no es un problema de nuestra implementación, es un límite estructural conocido del enfoque. **No vale la pena seguir iterando esta táctica sin una idea de fondo distinta** (ver "Cómo hacer que el resto funcione" más abajo).
- **Satélite (reglas técnicas de corto plazo)**: con su tasa de acierto real (35.6%, no el 58.3% desactualizado que usaba antes — bug de sizing ya corregido en el código), el propio sistema de gestión de riesgo (Kelly) decide no operar — queda en ~0%. No pierde, pero tampoco gana.
- **Pairs Trading**: solo 1 operación en 600 ruedas con los 3 pares default — muestra insuficiente para sacar conclusión alguna.
- **Carry LECAP/CER**: la pata de LECAP no tiene historial descargable en ninguna fuente gratuita conocida (solo el estado de HOY). Sí se probó algo relacionado con datos reales: sostener un bono CER (TX26) sin rotar, 2021-2026, en dólares reales → **-7.7% anual, Max Drawdown -74%**. Quedarse en CER a largo plazo sin rotar no es un carry seguro.
- **Dólar Sintético (ROFEX)**: sin ningún historial descargable, ni gratis ni de fácil acceso. No se puede backtestear hoy.
- **Factor investing, pairs trading, arbitraje CEDEAR/subyacente, RL trader, ejecución de órdenes real (IOLAdapter)**: código existe y corre, pero (salvo Factor Investing, ya promovido arriba) **ninguno está conectado a ningún endpoint/bot en vivo** (confirmado por grep sobre `routes.py`/`webui/app.py`/`telegram_bot.py`).

**Bug de datos real encontrado y corregido**: el histórico de precios de SPY y YPFD tenía saltos falsos de hasta -66% (cambios de ratio de CEDEAR nunca ajustados hacia atrás por la fuente) que contaminaban cualquier backtest que los usara — confirmado, corregido en `portfolio/backtest.py::_fix_price_discontinuities` con tests, cuidando de NO tocar una caída real conocida (GGAL/CEPU, crisis PASO 12/08/2019).

## Cómo hacer que el resto funcione (investigación 2026-10-20, probado con datos/código reales)

- **Core (point-forecast direccional)**: **probé el adaptador LoRA entrenado con datos locales (`finetune/adapters/chronos-bolt-small-lora`) contra el modelo base — empeora el resultado** (-35.7% in-sample contra -24.5% del modelo base, mismo universo/ventana). Esa palanca queda cerrada. Lo único que no se probó todavía porque requiere integrar una pieza nueva (no solo correr código que ya existía): comparar contra un modelo distinto (Moirai-2.0/TimesFM-2.5, que la literatura muestra ganándole a Chronos en varios tickers) — es la única palanca que queda con respaldo externo, pero es trabajo de integración real, no una corrida de una tarde.
- **Satélite**: **probé el filtro de régimen suave (HMM) contra el corte rígido de VIX actual — no cambió nada de fondo** (ambos convergen a ~0% de rendimiento, porque el propio Kelly ya los apaga solos al no tener ventaja real). Quedan sin probar los otros dos levers propuestos (exigir señal de entrada más fuerte, extender la expiración más allá de 3 ruedas) — son los que faltan intentar antes de dar esto por agotado.
- **Pairs Trading**: **escaneé los 18 pares candidatos posibles del universo actual (mismo sector, test de cointegración real Engle-Granger) — solo 1 de 18 pasa el test (AMZN/META, p=0.04), y ni siquiera ese da un resultado interesante (+0.9% anual, 1 operación en 600 ruedas)**. Ni GGAL/BMA ni SPY/QQQ (los pares default originales) están realmente cointegrados (p=0.39 y p=0.06) — nunca lo estuvieron, se habían elegido por intuición, no por test. Conclusión: no es un problema de umbral, es que **este universo de ~17 activos no tiene buen material para Pairs Trading** — la única palanca real es ampliar mucho el universo de activos a escanear (más acciones/CEDEARs), que es un trabajo de alcance mayor.
- **Carry LECAP/CER**: **encontré y confirmé una fuente de datos real y gratis que no se había explorado**: la API oficial del BCRA (`api.bcra.gob.ar/estadisticas/v4.0/monetarias`, confirmada funcionando ahora mismo) tiene la serie histórica de BADLAR desde 1999, diaria, sin key. Combinada con el historial real de bonos CER (ya confirmado en data912 desde 2021), esto alcanza para armar una aproximación histórica seria — queda como próximo paso concreto, no bloqueado por falta de datos.
- **Dólar Sintético**: **confirmé que la API de A3 Mercados/Matba Rofex requiere credenciales** (`"Authentication credentials were not provided"` al pegarle directo) — no es de acceso anónimo como data912/dolarapi/BCRA. Para destrabar esto hace falta que vos mismo evalúes/gestiones una cuenta con A3 Mercados (gratis o paga, no pude confirmar cuál sin registrarme) — es la única estrategia de las 9 evaluadas que depende de una decisión tuya, no de más trabajo mío.

## Qué entra en el alcance de estrategias (y qué no, por ahora)

**Entra**, en este orden de evidencia:
1. **Canje AL29/GD29, Carry cripto BTC, Carry cripto ETH — ya VALIDADOS**
   (DSR≥0.95 real, ver tabla arriba). Siguiente paso del gate: período de
   paper trading/semi-auto sin salvada manual + drawdown real dentro del
   perfil + batir el benchmark — recién ahí "listo para plata real".
   **Factor Investing** mostró retorno real positivo pero todavía NO pasó el
   DSR (0.2188) — sigue en investigación, no pasa a la siguiente etapa del
   gate todavía.
2. Caución/Yield como sleeve de baja de volatilidad, candidato a full-auto
   temprano.
3. Core (Chronos point-forecast) y Satélite (reglas técnicas): **descartados
   para seguir iterando tal como están** (DSR no significativo confirmado,
   corroborado por literatura externa sobre el límite de este enfoque) — ver
   "Cómo hacer que el resto funcione" para las palancas concretas que sí
   tienen evidencia detrás antes de resucitarlos.

**No entra todavía** (no por descartado, sino por falta de evidencia,
conexión, o datos): pairs trading (muestra insuficiente), carry LECAP/CER y
dólar sintético (sin historial descargable todavía), arbitraje CEDEAR/
subyacente, RL trader, ejecución automática de órdenes contra un broker real.
Se reconsidera cuando tengan backtest con el mismo rigor y, eventualmente,
conexión real.

## Perfiles de riesgo (configurable — estos son el default, no un techo fijo)

| Perfil | Drawdown máx. tolerado | Concentración máx. por instrumento | Concentración máx. por tipo de activo | Entra renta variable con señal NO validada (DSR<0.95) |
|---|---|---|---|---|
| Conservador | 10% | 15% | 40% | No |
| Moderado | 20% | 25% | 50% | Solo en semi-auto, con tu aprobación explícita por operación |
| Agresivo | 35% | 35% | 70% | Solo en semi-auto, con tu aprobación explícita por operación |

Ninguna señal basada en Chronos/meta-modelo entra en **full-auto** en ningún
perfil hasta pasar el gate de "lista para plata real" de abajo — el perfil
de riesgo decide cuánto y con cuánta concentración, no si se salta la
validación.

## Qué significa "listo para plata real" (gate, por estrategia/sleeve — no global)

Una estrategia/sleeve puede pasar a operar con plata real (en el modo de
automatización que corresponda) cuando, **todo junto**:

1. **Deflated Sharpe Ratio ≥ 0.95** en el backtest con CPCV (ya construido, se corre de nuevo cada vez que cambia el código o se acumula evidencia nueva).
2. **Período mínimo de paper trading / semi-auto corrido en vivo**, sin que una intervención manual tuya haya tenido que salvar el resultado (si necesitaste pisar la señal para no perder, no pasó el gate).
3. **Drawdown real observado durante ese período dentro del límite del perfil de riesgo activo.**
4. **Retorno en USD durante ese período superior al benchmark ponderado de esa porción** (Merval o S&P 500 según corresponda) — no alcanza con terminar en positivo.

Mientras una estrategia no cumpla las 4, queda en modo investigación/papel,
sin importar cuánto tiempo lleve construida.

## Automatización: full-auto y semi-auto, configurable por estrategia/sleeve

No es una decisión global de "todo full-auto" o "todo semi-auto" — es un
toggle por estrategia/sleeve, con este default:

- **Default de cualquier estrategia nueva: semi-auto** (te pide aprobación por operación) hasta que pase el gate de arriba.
- **Una vez que una estrategia pasa el gate, se habilita el toggle a full-auto para esa estrategia puntual** — vos decidís si la pasás a full-auto o la dejás en semi-auto indefinidamente, la plataforma no fuerza ninguna de las dos.
- La cartera real actual (32 instrumentos, en otro broker) sigue sin tocarse: cualquier automatización nueva se prueba primero aislada (como ya está hoy la cuenta IOL de test, sin ningún instrumento real enlazado) antes de proponer conectarla a dinero real.

## Qué es "terminado" para este proyecto

No existe una versión "terminada" de una vez — el criterio de completitud es,
para cada pieza:

- **Infraestructura/datos/UI**: terminada cuando corre en producción (VPS) sin bugs conocidos sin resolver y con los tests reales pasando (hoy: 596/596).
- **Una estrategia de inversión**: terminada cuando pasa el gate de "listo para plata real" de arriba — antes de eso es, por definición, research.
- **El proyecto en su conjunto**: nunca "terminado" en el sentido de dejar de iterar — el criterio de "suficientemente bueno para seguir usándolo con plata real" es que al menos una estrategia esté operando en modo configurado (full-auto o semi-auto) y su rendimiento rolling le gane al benchmark ponderado.

## Infraestructura "grado-fondo" construida (2026-10-20/21, ver plan `valiant-launching-ullman.md`)

Las 4 piezas del estado final (ver sección "Estado final" más arriba) ya están implementadas y con tests (635 pasando):

- **Registro de Estrategias** (`portfolio/strategy_registry.py`): tabla `strategies` real, sembrada con los resultados de hoy — `canje_al29_gd29`/`crypto_carry_btcusdt`/`crypto_carry_ethusdt` en `validated`; `core`/`satellite`/`factor_investing` en `research`. `promote_to_live()` es la decisión explícita (nunca automática) para pasar a plata real, y exige que haya una línea de base (`baseline_sharpe`) guardada.
- **Motor de Riesgo** (`portfolio/risk_engine.py`): ya conectado a `portfolio/proposals.py` — las propuestas de `senal_kronos`/`senal_tecnica` (Core/Satélite) ya no se generan mientras esas estrategias estén en `research`. El circuit breaker mide el drawdown REAL de la cartera en USD contra el límite del perfil activo; al implementarlo encontramos y corregimos **otro caso real del bug de datos de SPY/YPFD, esta vez en el reconstructor de valor de cartera que alimenta el Resumen** (mostraba -44% de caída falsa, el real es -11.15%).
- **Asignador de Capital Dinámico** (`portfolio/capital_allocator.py`): corrida real (risk parity) sobre las 3 estrategias validadas da **BTC 51.8% / ETH 45.8% / Canje 2.4%**, Sharpe esperado de la mezcla 6.2 — ya persistido en el Registro.
- **Monitor de Salud de Estrategia** (`portfolio/strategy_health.py`): compara el Sharpe reciente real de cada estrategia `live` contra su línea de base; si cae por debajo del 30%, la baja sola a `semi_auto` (nunca a `off`) y avisa por Telegram. Sin estrategias `live` todavía (todas en `validated`/`research`), no tiene nada que vigilar aún — queda listo para cuando la primera pase el resto del gate.

**UI construida (2026-10-21)**: subpestaña "Estrategias" dentro de Decisiones (junto a Propuestas/Oportunidades) — tabla con estado/DSR/capital/modo por estrategia + banner del circuit breaker, y las 2 acciones humanas (cambiar modo, promover a live). Verificado en navegador real (Playwright): se ve el estado real sembrado hoy, y un cambio de modo persiste tras recargar. De paso encontramos y corregimos **otro bug real**: las columnas nuevas de `baseline_sharpe`/`baseline_annualized_return_pct` se habían agregado a `schema.sql` después de que la tabla `strategies` ya existiera localmente — `promote_to_live` tiraba un error interno en vez del mensaje esperado. Ya migrado (`portfolio/db.py::_migrate`), con test de regresión.

**Corrección de flujo (2026-10-21)**: el flujo real es **Investigación → Validada (DSR≥0.95) → Paper Trading (automático, plata ficticia, 100% del tiempo necesario, cero riesgo) → Real (con brokers)** — no "semi-auto" como paso intermedio con plata real. Controles de pausar/continuar cada etapa viven en **Ajustes → Estrategia y Motores** (no en Decisiones, que quedó solo-lectura). Límite declarado: Canje/Carry BTC/Carry ETH hoy solo tienen simulador de backtest, no una señal diaria en vivo — Paper Trading para esas 3 no tiene todavía qué simular día a día; generar esa señal es el siguiente paso explícito, no construido aún.

**Desplegado al VPS (2026-10-21)**: todo lo de arriba ya está en producción (`kronos.deglia.xyz`) — Registro de Estrategias sembrado con los mismos resultados reales, Asignador de Capital corrido ahí mismo (mismo resultado: BTC 51.8%/ETH 45.8%/Canje 2.4%), circuit breaker midiendo el drawdown real de producción (-12.1%, dentro del límite), fix del Resumen (salto falso de SPY/YPFD) ya corrigiendo el gráfico real. Backup de la base de producción tomado antes de migrar (`/opt/kronos/backups/pre_deploy_20261021_140115/`). Sin errores nuevos en los logs post-deploy. Fase 5 (ejecución real contra un bróker) sigue explícitamente pospuesta hasta que haya una estrategia `live` con intención real de operar sola.

## Próximos pasos inmediatos (dado que hay urgencia de semanas, no indefinida)

1. Correr Carry Trade cripto y Canje AL29/GD29 con el mismo proceso CPCV+DSR que ya existe para el motor principal — la infraestructura de validación ya está construida, falta aplicarla a estas dos.
2. Diseñar y conectar el sleeve de Caución/Yield como el primer candidato real a full-auto (más simple: no depende de un forecast).
3. Decidir, con el resultado de (1), si vale la pena seguir iterando el meta-modelo (features/horizonte/universo) o si el motor de señal actual necesita un cambio más de fondo antes de volver a correr CPCV.
4. Recién con (1)-(3) resueltos, dimensionar qué significa "semi-auto" en términos concretos de UI/bot (qué aprobás, dónde, con qué fricción).
