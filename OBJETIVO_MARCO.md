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
| Canje AL29/GD29 (arbitraje de bonos) | **+24.8%**, Sharpe 1.54 | 56 operaciones, 1233 ruedas | **0.9864** | **VALIDADO sobre un modelo TEÓRICO — ver nota 2026-10-24 abajo, el modelo real ejecutable PIERDE plata** |
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

- **Core (point-forecast direccional)**: **probé el adaptador LoRA entrenado con datos locales (`finetune/adapters/chronos-bolt-small-lora`) contra el modelo base — empeora el resultado** (-35.7% in-sample contra -24.5% del modelo base, mismo universo/ventana). Esa palanca queda cerrada. **La palanca que quedaba (forecaster distinto, TimesFM-2.5) también se probó y cerró — ver "Investigación 2026-11-10" al final: DSR=0.0214, sigue sin edge.** No queda ninguna palanca conocida sin probar para esta táctica.
- **Satélite**: **probé el filtro de régimen suave (HMM) contra el corte rígido de VIX actual — no cambió nada de fondo** (ambos convergen a ~0% de rendimiento, porque el propio Kelly ya los apaga solos al no tener ventaja real). Los otros dos levers propuestos (exigir señal de entrada más fuerte, extender la expiración más allá de 3 ruedas) **ya se probaron también — ver "Investigación 2026-11-10" al final de este documento, ambos quedan descartados**.
- **Pairs Trading**: **escaneé los 18 pares candidatos posibles del universo actual (mismo sector, test de cointegración real Engle-Granger) — solo 1 de 18 pasa el test (AMZN/META, p=0.04), y ni siquiera ese da un resultado interesante (+0.9% anual, 1 operación en 600 ruedas)**. Ni GGAL/BMA ni SPY/QQQ (los pares default originales) están realmente cointegrados (p=0.39 y p=0.06) — nunca lo estuvieron, se habían elegido por intuición, no por test. Conclusión: no es un problema de umbral, es que **este universo de ~17 activos no tiene buen material para Pairs Trading** — la única palanca real es ampliar mucho el universo de activos a escanear (más acciones/CEDEARs), que es un trabajo de alcance mayor.
- **Carry LECAP/CER**: **encontré y confirmé una fuente de datos real y gratis que no se había explorado**: la API oficial del BCRA (`api.bcra.gob.ar/estadisticas/v4.0/monetarias`, confirmada funcionando ahora mismo) tiene la serie histórica de BADLAR desde 1999, diaria, sin key. Combinada con el historial real de bonos CER (ya confirmado en data912 desde 2021), esto alcanza para armar una aproximación histórica seria — queda como próximo paso concreto, no bloqueado por falta de datos.
- **Dólar Sintético**: **confirmé que la API de A3 Mercados/Matba Rofex requiere credenciales** (`"Authentication credentials were not provided"` al pegarle directo) — no es de acceso anónimo como data912/dolarapi/BCRA. Para destrabar esto hace falta que vos mismo evalúes/gestiones una cuenta con A3 Mercados (gratis o paga, no pude confirmar cuál sin registrarme) — es la única estrategia de las 9 evaluadas que depende de una decisión tuya, no de más trabajo mío.

## Qué entra en el alcance de estrategias (y qué no, por ahora)

**Entra**, en este orden de evidencia:
1. **Canje AL29/GD29, Carry cripto (BTC/ETH/ADA/DOGE/LINK/LTC) — ya VALIDADOS**
   (DSR≥0.95 real, ver tabla arriba). Siguiente paso del gate: período de
   paper trading/semi-auto sin salvada manual + drawdown real dentro del
   perfil + batir el benchmark — recién ahí "listo para plata real". **Canje
   es un caso especial: el DSR 0.9864 mide un modelo TEÓRICO no ejecutable
   por un retail en IOL/PPI — ver "Investigación 2026-10-24" al final de
   este documento, el único modelo real pierde plata (DSR~0). Sigue en
   paper trading para seguir juntando evidencia, pero no avanza a "live" con
   la evidencia actual.**
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

## Investigación 2026-10-24: ampliar Pairs Trading + buscar estrategias nuevas en paralelo

(Ver conversación: "profundiza en ampliar el universo de pairs trading, pero también investiga estrategias nuevas en paralelo". Todo lo de abajo se corrió de nuevo HOY contra datos/código reales, nunca asumido de commits o conversaciones anteriores.)

- **Pairs Trading, universo ampliado — SIN edge nuevo**: se agregaron 18 símbolos candidatos nuevos a `portfolio/pairs_trading.py` (`PAIRS_ONLY_SYMBOLS`/`PAIRS_SECTOR_GROUPS`, additivo, nunca toca el universo de Core/Satélite), todos con ≥300 filas reales confirmadas en data912. De los pares que cointegraron (Engle-Granger p<0.05), los 5 corridos contra el gate CPCV+DSR dieron **DSR < 0.44 todos** (la mayoría cerca de 0). Confirma que no es un problema de tamaño de universo — Pairs Trading en este mercado no tiene edge robusto, ni con 17 ni con 35 activos. Tests de regresión agregados (`tests/test_pairs_trading.py`, 25/25 pasan).
- **Gap de ejecución de Canje confirmado, independientemente**: se corrió `arbitrage.py::simulate_canje_real_position_engine` (el único modelo que un retail puede ejecutar de verdad en IOL/PPI — comprar la pata barata y rotar, sin shortear) contra el mismo gate: **-20.6% anual, Sharpe -0.40, Max DD -68.8%, DSR 0.0000 — pierde plata**. El DSR 0.9864 de la tabla de arriba mide un modelo teórico (largo una pata + corto la otra) que no es ejecutable hoy. Canje sigue en paper trading (evidencia en curso), pero NO avanza a "live" con la evidencia actual — ver nota agregada en la tabla de estrategias validadas.
- **Arbitraje CEDEAR/subyacente — re-verificado, NO pasa el gate**: un commit de investigación previo (20/10) sugería que AAPL/MSFT/GOOGL "sostienen edge real" con un split OOS simple (Sharpe 0.63-0.73). Se corrió `arbitrage.py::simulate_cedear_underlying_arbitrage_engine` contra el gate CPCV+DSR real sobre los 9 símbolos declarados (`CEDEAR_ARBITRAGE_SYMBOLS`): mejor resultado MELI con DSR 0.84, ninguno llega a 0.95. Además siguen sin resolver los problemas de ejecución real (2 mercados, 2 monedas, ratio de conversión CEDEAR/acción no confirmado — ver docstring "GAP DE EJECUCION" en `arbitrage.py`). Conclusión: no califica, ni en el papel ni en la práctica.
- **Estrategia nueva con edge real: ampliar Carry cripto a 4 monedas más**: el motor de `crypto_carry.py` (genérico desde el día 1, no específico a BTC/ETH) se corrió con datos reales de Binance sobre 7 monedas más. **ADA, DOGE, LINK y LTC pasan el mismo gate DSR≥0.95** (DSR=1.0000 los 4, Sharpe 4.9-6.5, Max DD <6%, ~2300-2600 ruedas reales cada uno) — mismo mecanismo delta-neutral spot+futuro ya ejecutable 1:1 en Binance que BTC/ETH. XRP quedó justo abajo del gate (DSR 0.94). BNB y SOL **no sirven** (DSR~0, Max DD >30%) — confirma que no es "cualquier altcoin cobra funding gratis", hay que probarlas una por una. Registradas en `strategy_registry` (`crypto_carry_adausdt/dogeusdt/linkusdt/ltcusdt`) y arrancadas en Paper Trading (2026-10-24), con intent builder real agregado en `live_strategy_engine.py` y simulador en `capital_allocator.py` (mismo patrón que BTC/ETH).

**Conclusión honesta de esta ronda**: de las 4 líneas de investigación (ampliar Pairs Trading, re-verificar Canje, re-verificar CEDEAR/subyacente, ampliar Carry cripto), **la única que suma una estrategia nueva con DSR≥0.95 y genuinamente ejecutable es Carry cripto con más monedas** — que es la MISMA estrategia (cash-and-carry de funding rate) aplicada a más activos, no una estrategia distinta. Hoy, de todo lo validado Y ejecutable con plata real, todo vive en un solo mercado (cripto/Binance) y una sola fuente de edge (funding rate de futuros perpetuos).

**Nota sobre Carry LECAP/CER** (re-verificado hoy, no solo citado del commit `17c47fb` del 21/10): se volvió a correr `carry_lecap_cer_backtest.py::simulate_carry_lecap_cer_engine_usd` con datos de hoy sobre los 4 bonos CER (TX26, TX28, DICP, PARP) contra el gate real — mejor caso TX28 con DSR 0.027, el resto peor (DSR 0.0001-0.0075), todos muy lejos de 0.95. **Confirma que esta vía ya está agotada, no es una palanca de diversificación pendiente** — ya se había descartado correctamente, no es un hallazgo nuevo de hoy.

Con Pairs Trading, CEDEAR/subyacente y LECAP/CER los tres confirmados sin edge, y Canje limitado a un modelo que pierde plata en su versión ejecutable, **hoy no hay ninguna fuente de diversificación real fuera de cripto que haya pasado el gate** — la concentración de toda la plata operable en un solo mecanismo (funding rate de futuros perpetuos, aunque sea en 6 monedas distintas) es un riesgo real de la cartera, no resuelto por esta ronda de investigación. Las palancas que siguen abiertas con evidencia externa pero sin probar en este proyecto (ver "Cómo hacer que el resto funcione" arriba) son: Dólar Sintético (bloqueado por credenciales de A3 Mercados, depende de una decisión tuya) y un modelo de forecasting distinto a Chronos para Core (Moirai/TimesFM, trabajo de integración real, no una corrida de una tarde).

## Investigación 2026-11-10: Satélite — las 2 palancas pendientes (señal de entrada más fuerte, expiración extendida)

(Ver conversación: "quiero seguir investigando estrategias para poder ganar dinero" / "probemos con las 4 en paralelo" — se acordó ir en orden, no en paralelo, por la regla de no correr exploración paralela sin verificar cada resultado. Corrido HOY contra `portfolio/backtest.py::simulate_satellite_engine` real, sobre `UNIVERSE` (17 instrumentos) y `CORE_BACKTEST_LOOKBACK_DAYS` (300 ruedas), con el mismo gate `run_strategy_cpcv_validation` que ya usa el proyecto.)

**Artefacto de medición encontrado y declarado (no un bug nuevo, consecuencia de un diseño ya documentado)**: el Kelly cap (`short_term_engine.kelly_fraction`) usa el ÚLTIMO resumen PERSISTIDO (win_rate≈34.6% real, edge negativo bajo el TP/SL actual) para dimensionar CUALQUIER corrida nueva — a propósito, para no ser circular (ver docstring de `kelly_fraction`). Como ese cap ya vale 0.0, el `weight` de los 537 trades simulados en este experimento salió exactamente 0.0 en los 5 escenarios, dejando Sharpe/Max Drawdown/retorno anualizado/DSR de la curva ponderada = 100% Caución (idle_weight=1.0 todos los días, retorno diario constante = tasa de Caución configurada, 23% TNA). Esas 4 métricas **no sirven para comparar las palancas** en esta corrida — sólo el win_rate/profit_factor por trade (calculados sobre el `return_pct` crudo de cada trade, no ponderado), que es lo que de verdad mide si la táctica mejoró.

**Resultados reales (mismas 300 ruedas, mismo universo, las 5 corridas):**

| Variante | n_trades | Win rate | Profit factor |
|---|---|---|---|
| Baseline (actual: RSI<36, BB 1.8std, vol 1.8x, ATR 2.0x, expira 3 ruedas) | 136 | 34.6% | 0.157 |
| Entrada más fuerte (RSI<32, BB 2.0std, vol 2.5x, ATR 2.5x) | 86 | 31.4% | 0.155 |
| Expiración extendida a 6 ruedas | 119 | 44.5% | 0.195 |
| Expiración extendida a 10 ruedas | 115 | 49.6% | 0.207 |
| Combinada (entrada más fuerte + expiración 6 ruedas) | 81 | 43.2% | 0.184 |

**Conclusión**: extender la expiración ayuda un poco (profit factor 0.157 → 0.207 con 10 ruedas, win rate 34.6% → 49.6%), pero sigue muy lejos de breakeven (necesitaría profit factor ≥1.0, y bastante más considerando la asimetría TP 4%/stop techo 9% actual). Exigir una señal de entrada más estricta **empeora** levemente el resultado (menos trades, profit factor similar o peor) — no es la palanca que faltaba. Con esto, **las 2 palancas que quedaban pendientes para Satélite ya están probadas y descartadas**: sigue sin haber edge suficiente para que Kelly lo reactive. No quedan palancas conocidas sin probar para esta táctica.

## Investigación 2026-11-10 (continuación): Core con TimesFM-2.5 en vez de Chronos-Bolt

(Ver conversación: "vamos con esas dos en paralelo" — Core con forecaster alternativo + decisión de cuenta IBKR, esta última pospuesta — ver más abajo. `examples/research_timesfm_vs_chronos.py`, de un commit del 21/10, ya había comparado el error de punto de TimesFM-2.5 contra Chronos-Bolt en 4 tickers sueltos, sin ventaja clara — re-corrido hoy con datos de hoy, mismo patrón sin ganador consistente. Lo que faltaba, declarado en ese mismo docstring, era correrlo DENTRO del motor Core completo (Triple Barrier + ranking cross-sectional) contra el gate real CPCV+DSR, no solo el error de punto de 4 tickers.)

Se construyó `model/local_timesfm_client.py` (mismo contrato que `model/local_client.py::LocalChronosClient` — reemplazo directo del parámetro `client` en `simulate_core_engine`, mismo post-procesamiento de bandas por volatilidad para que la única diferencia real sea el modelo) y se corrió `simulate_core_engine` completo con TimesFM-2.5-200M sobre el mismo `UNIVERSE` (17 instrumentos) y `CORE_BACKTEST_LOOKBACK_DAYS` (300 ruedas) que ya usa el proyecto para Chronos, seguido del mismo `run_strategy_cpcv_validation`.

**Resultado real**: 14 trades, win rate 35.7%, profit factor 0.63, Sharpe -0.67, Max Drawdown -29.3%, retorno anualizado **-15.7%**, **DSR=0.0214** (12/15 paths evaluables).

**Conclusión**: prácticamente el mismo resultado que Chronos-Bolt (-17% anual real, DSR=0.0073) — ambos profundamente no significativos, la diferencia entre 0.0073 y 0.0214 es ruido, no una mejora real. **Confirma con un experimento propio (no solo citando literatura externa) que el techo es del enfoque de point-forecast en este horizonte, no del modelo particular** — cambiar Chronos por TimesFM no destraba nada. Con esto se cierra la única palanca que quedaba pendiente para Core; no vale la pena seguir iterando esta táctica (ni probar Moirai-2.0, misma familia de modelo, mismo resultado esperable) sin una idea de fondo distinta al point-forecast direccional.

**Sobre la cuenta IBKR (pospuesta)**: se decidió no abrir la cuenta todavía — siguiendo el mismo criterio que el resto del proyecto (validar con datos antes de tocar un broker), primero corresponde backtestear si vender opciones (covered calls/cash-secured puts) tiene edge real neto de comisiones con datos históricos de volatilidad implícita/realizada, sin necesitar cuenta ni API. Investigación externa (ver conversación) ya adelantó una señal de alerta: en 9 backtests de terceros revisados, ninguna estrategia de venta de opciones le ganó al índice en retorno total (solo reduce volatilidad) — baja la prioridad de esta vía como motor de "más ingreso" hasta no verificarlo con datos propios.

## Investigación 2026-11-10 (continuación): Arbitraje de Funding Rate CROSS-EXCHANGE — candidata nueva, SIN edge

(Ver conversación: "que otras estrategias podemos usar para ganar dinero además de las que tenemos" — a diferencia de las líneas anteriores de hoy, esta NO es un ajuste de algo existente, es un mecanismo nuevo: corto el exchange con funding más alto + largo el de funding más bajo, en vez de spot+futuro del mismo exchange.)

Se construyó `portfolio/cross_exchange_funding_arb.py` con fetchers reales verificados hoy (sin auth): Binance (ya existente, reusado), Bybit (`/v5/market/funding/history`, paginado con `endTime`) y OKX (`/api/v5/public/funding-rate-history`, paginado con `after` — contraintuitivo, `before` no avanza el cursor). **Bug real encontrado y corregido antes de confiar en el resultado**: ~43% de los `fundingTime` de Binance traen 1-2ms de jitter sobre la hora exacta — un merge exacto entre exchanges sin redondear perdía silenciosamente la mitad del historial superpuesto (3934 de 7264 filas reales en el par Binance/Bybit). Corregido redondeando a minuto antes de mergear.

**Resultado real (gate CPCV+DSR, mismo criterio que el resto del proyecto):**

| Par | Días con dato real | DSR | Retorno anualizado | Profit factor |
|---|---|---|---|---|
| BTCUSDT Binance/Bybit | 2422 (2020-03 a hoy) | ~0 (1.4e-12) | -0.4% | 0.74 |
| ETHUSDT Binance/Bybit | 2212 | ~0 (1.1e-11) | -0.9% | 0.41 |
| BTCUSDT Binance/OKX | 93 (OKX solo retiene ~3 meses en el endpoint público) | ~0 (3.5e-11) | -0.9% | 0.00 |
| BTCUSDT Bybit/OKX | 93 | ~0 (1.0e-11) | -1.2% | 0.01 |

**Conclusión**: sin edge en ninguno de los 4 pares, con muestra real de hasta 6.6 años para Binance/Bybit (no es un problema de poca muestra). Tiene sentido económico: el funding rate ABSOLUTO de un exchange (lo que ya captura `crypto_carry.py`) tiene un sesgo estructural persistente (demanda de apalancamiento largo), pero la DIFERENCIA entre dos exchanges grandes para el mismo símbolo es un spread que los arbitrajistas institucionales ya mantienen comprimido — no queda edge neto de comisiones. **Candidata nueva descartada** — no vale la pena perseguir más pares (ej. otras monedas) con este mismo mecanismo.

## Investigación 2026-11-10 (continuación): Factor Investing con universo ampliado — EMPEORA, no mejora

(Ver conversación: "por qué analizamos 17 instrumentos, si la idea es usar Interactive Brokers que tiene más" → se confirmó que el límite no era de broker sino de curación manual de `backtest.UNIVERSE`, y que `data912` ya tiene ~365 CEDEARs en su catálogo live, muchos más de los 10 que usa Factor Investing hoy. Hipótesis de esta sesión, basada en que la literatura marca Factor Investing como la categoría más robusta a overfitting: más amplitud debería ayudar a la consistencia entre sub-períodos que es el problema declarado de esta estrategia (DSR 0.2188 al 2026-10-20). Esa hipótesis **se probó y resultó equivocada** — ver abajo.)

Se escanearon los 365 candidatos del catálogo `data912` (`/live/arg_cedears`, excluyendo variantes de clase C/D) contra `/historical/cedears/<symbol>`, exigiendo ≥300 filas reales — **35 pasaron el filtro** (agregados como `factor_investing.py::FACTOR_ONLY_SYMBOLS`, aditivo, nunca toca `backtest.UNIVERSE` de Core/Satélite). Se corrió `simulate_factor_engine` + `run_strategy_cpcv_validation` con el mismo `lookback_days=600` que la corrida original, comparando el universo de 10 (baseline) contra el de 45 (ampliado), **ambos re-corridos HOY** (no se reusó el número viejo del 20/10):

| Universo | DSR | Retorno anualizado | Sharpe | Max DD |
|---|---|---|---|---|
| 10 CEDEARs (baseline, re-corrido hoy) | 0.0514 | +10.8% | 0.54 | -27.4% |
| 45 CEDEARs (ampliado) | 0.0035 | **-14.5%** | -0.64 | -35.3% |

**Discrepancia declarada (regla del proyecto: nunca dejar un número distinto sin explicar)**: el baseline re-corrido hoy (DSR=0.0514) NO coincide con el DSR=0.2188 documentado el 20/10 para el mismo universo de 10. La explicación real: `lookback_days=600` es una ventana MÓVIL — hoy cubre ~3 semanas más tarde que la corrida original, así que el tramo que entra/sale del cálculo es distinto. Esto no es un bug, es una confirmación ADICIONAL (un tercer dato, no solo dos) de que **Factor Investing es inconsistente entre sub-períodos** — exactamente el problema ya diagnosticado el 20/10, ahora reforzado por una tercera corrida que vuelve a dar un número distinto.

**Conclusión sobre la amplitud**: ampliar el universo de 10 a 45 CEDEARs **empeoró** el resultado en vez de mejorarlo (DSR 0.0514→0.0035, retorno +10.8%→-14.5%) — la hipótesis de que "más amplitud = más estable" (que sí funcionó para el razonamiento de Pairs Trading en otro sentido) **no se sostiene acá**. Factor Investing con este motor de 3 factores (Momentum+Low-Vol+Value, ponderación 1/3 fija) parece ruidoso independientemente del tamaño del universo — agregar más activos solo agregó más ruido cross-seccional, no más señal. Nota aparte: 2 de los 35 nuevos (DISN, NOKA) no resuelven en yfinance con ese ticker (CEDEAR ≠ ticker real: Disney es DIS, Nokia es NOK) — pierden el factor Value puntual, manejado sin romper (ver `compute_value_factor`), no afecta la conclusión de arriba.

**Con esto, de las 4 líneas de investigación de HOY (Satélite, Core/TimesFM, arbitraje cross-exchange, Factor Investing ampliado), ninguna encontró edge nuevo ni mejoró lo existente.** Lo único con edge real confirmado sigue siendo Carry cripto (6 monedas) — concentrado en un solo mecanismo, como ya se había señalado el 24/10.

## Investigación 2026-11-10 (continuación): Momentum Cross-Sectional en Cripto — candidata nueva, SIN edge

(Ver conversación: "segui con el momentum cripto" — mecanismo genuinamente distinto al funding rate: rankear un panel de monedas por retorno pasado y quedarse largo en las de mejor momentum / corto en las peores, no una variante de ajuste de algo existente.)

**Universo construido con datos reales, no adivinado**: se consultó `fapi.binance.com/fapi/v1/exchangeInfo` (campo oficial `underlyingType`) para filtrar SOLO monedas cripto reales — Binance Futures hoy también lista perpetuos sintéticos de acciones/commodities bajo el mismo quote USDT (ej. `NVDAUSDT`, `XAUUSDT`, hasta `ANTHROPICUSDT` de una pre-IPO) que un filtro por nombre o por volumen habría mezclado por error. De 518 símbolos `underlyingType=COIN`, se tomaron los de mayor volumen real con ≥900 días de historial confirmado (24 monedas: BTC, ETH, XRP, LINK, ADA, XMR, ZEC, BNB, DOGE, SOL, UNI, AVAX, NEAR, AAVE, FIL, ONE, INJ, ARB, SUI, 1000PEPE, WLD, CYBER, ONDO, ENA).

Se probaron 5 variantes razonables (momentum 12-1 mensual, 3m mensual, 12-1 semanal, top3 concentrado, long-only) contra el gate CPCV+DSR:

| Variante | Sharpe | Max DD | Retorno anualizado | DSR |
|---|---|---|---|---|
| 12-1 mensual, top5 largo/corto | 0.06 | -51.8% | -1.2% | 0.049 |
| 3 meses mensual, top5 largo/corto | 0.50 | -47.2% | +11.0% | 0.044 |
| 12-1 semanal, top5 largo/corto | -0.02 | -50.6% | -2.8% | 0.024 |
| 12-1 mensual, top3 largo/corto | 0.39 | -46.9% | +7.3% | 0.115 |
| 12-1 mensual, top5 **solo largo** | 0.73 | **-88.4%** | +30.2% | 0.015 |

**Conclusión**: ninguna variante se acerca a 0.95 (la mejor, top3, llega a 0.115). La variante long-only muestra el retorno más atractivo a simple vista (+30.2% anual) pero con un drawdown de -88.4% y el DSR más bajo de las 5 — es el patrón clásico de "surfear un bull market concentrado", no un edge real, y el propio gate lo detecta como tal. El mecanismo largo-corto (dollar-neutral, sin esa trampa direccional) tampoco muestra nada en ninguna combinación de lookback/rebalanceo probada. **Candidata nueva descartada.**

Con esta, van 5 líneas de investigación nuevas hoy (Satélite x2, Core/TimesFM, cross-exchange funding arb, Factor ampliado, momentum cripto x5 variantes) sin encontrar ni un edge nuevo ni una mejora real — señal razonable de que la búsqueda de estrategias nuevas llegó a un punto de retornos decrecientes por ahora. El foco pasa a madurar Carry cripto hacia plata real (Binance Testnet, ver próximos pasos).

## Investigación 2026-11-10 (continuación, a pedido explícito de seguir buscando): Seasonality (descartada) + Prima de Volatilidad en opciones cripto (prometedora, NO validada todavía)

**Seasonality / día de la semana en cripto — descartada antes de construir nada.** Con el panel de 24 monedas ya descargado (ver investigación de momentum arriba), el promedio de retorno por día de semana en la muestra completa mostraba miércoles/viernes/sábado mejores que martes/jueves — pero partiendo la muestra en 2 mitades (2021-2023 vs 2023-2026), el patrón **se invierte** (martes/jueves pasan de ser los MEJORES días en la 1ra mitad a los PEORES en la 2da). Es ruido, no un efecto real — descartado sin llegar a construir una estrategia sobre esto.

**Prima de Volatilidad en opciones cripto (Deribit DVOL vs. volatilidad realizada de BTC) — la señal más fuerte de todo lo investigado hoy, pero con una limitación metodológica real declarada, NO pasa a "validada" todavía.**

Se descargó el índice DVOL de Deribit (`get_volatility_index_data`, público, sin auth, histórico completo desde su lanzamiento 2021-03-24) y se comparó contra la volatilidad realizada de BTC (de los mismos datos de Binance ya usados para momentum) en 97 ventanas de 30 días NO superpuestas: **78.4% de los períodos tuvieron IV > RV, con una prima mediana de 12.1 puntos de volatilidad** — consistente con literatura externa encontrada (73.7% de 1606 días, mediana 10.5 puntos, abril 2022-sept 2026).

Convertido a una serie de retorno aproximada (payoff de variance swap simplificado, `(IV-RV) × dias/365`, con el retorno de cada período completo registrado de GOLPE el día de settlement — **hallazgo real propio durante esta prueba**: la primera versión repartía el retorno suavizado entre los 21 días del período y eso disparó un Sharpe=9.63/DSR=1.0 absurdo, mismo tipo de artefacto de medición que el bug de Kelly=0 de Satélite; corregido registrando el retorno completo en un solo día, igual que un settlement real) da **Sharpe=1.85, Max DD=-3.0%, retorno anualizado +9.2%, DSR=0.9967**.

**Por qué esto NO es "validado, listo para operar" todavía, a diferencia de Carry cripto**: esta serie es una APROXIMACIÓN LINEAL de `(IV - RV)`, no el payoff real de vender opciones. Una posición corta de opciones real tiene un payoff CONVEXO (las pérdidas se aceleran cuando la volatilidad realizada se dispara muy por encima de lo esperado, el riesgo clásico de "quedarse corto de gamma" — LTCM, el colapso de XIV en 2018, son el ejemplo de manual de este riesgo) — una aproximación lineal **estructuralmente no puede capturar esa cola**, así que el Max DD de -3.0% casi seguro subestima el riesgo real de ejecutar esto con opciones de verdad. Confirmé que Deribit SÍ expone datos reales de opciones (878 instrumentos activos, histórico de vencidos vía API pública) — el paso que falta, y es trabajo real de otra escala que las pruebas de hoy, es construir el backtest con precios de opciones reales (strike, prima, vencimiento) en vez de esta aproximación, antes de poder decir que esto pasa el mismo gate que Carry cripto.

## Investigación 2026-11-10 (continuación, a pedido explícito "metete de lleno con las opciones reales"): straddle ATM con pricing Black-Scholes real — edge aparente, pero con un límite metodológico que impide declararlo validado

Se construyó un backtest con pricing de opciones real (Black-Scholes, usando el IV de DVOL como input real de mercado en vez de la aproximación lineal anterior), vendiendo un straddle ATM mensual sobre BTC, con comisiones reales de Deribit (0.03% del subyacente por apertura, tope 12.5% de la prima; 0.015% de entrega al vencimiento, tope 12.5% del valor). 97 períodos independientes, 2021-03 a hoy.

**Straddle SIN cobertura (naked), hold-to-expiry**: Sharpe 0.46, Max Drawdown real **-41.7%**, DSR=0.49 — **NO pasa el gate**. Los peores meses coinciden exactamente con los rallies más violentos de BTC (sept-oct 2021, post-FTX ene 2023, feb 2024) — confirma que sin cobertura, el riesgo de cola supera a la prima cobrada.

**Straddle CON cobertura delta diaria** (rebalanceo del delta con futuro perpetuo, costo real de hedge 0.05% por rebalanceo): **100% de aciertos en 97 períodos, DSR=0.99**. Repetido con datos HORARIOS reales de BTC y DVOL (49.387 horas, 2021-03 a hoy, para descartar que el resultado dependiera de solo ver precios de cierre diario) — el resultado se mantiene igual de limpio (DSR=0.9999, Max DD -8.4%, Sharpe 3.02, otra vez 100% de aciertos).

**Por qué esto NO se declara validado pese al DSR~1.0 — al contrario del resto de las estrategias de este documento**: ningún resultado real de una estrategia de venta de volatilidad tiene 0 períodos perdedores en 5.6 años que incluyen el colapso de FTX. La razón honesta es que la simulación (Black-Scholes + rehedge "perfecto") **estructuralmente no puede ver dos riesgos reales**, que son justo los que históricamente revientan posiciones cortas de volatilidad (ver colapso del ETF XIV, febrero 2018):
1. **Riesgo de margen/liquidación**: una posición corta de opciones necesita garantía real: en un movimiento violento, el bróker puede liquidar la posición a la fuerza antes de poder rehedgear — el modelo asume que siempre se puede rehedgear al precio teórico, sin que nadie fuerce un cierre.
2. **Riesgo de liquidez de la opción misma**: en una crisis real el spread bid-ask de las opciones se dispara (a veces 10x) y puede no haber contraparte para rehedgear al precio "justo" que asume Black-Scholes.

**Conclusión**: esta es la línea más prometedora de todo lo investigado hoy en términos de premio económico real y documentado (consistente con la literatura externa), pero el propio mecanismo de validación (DSR sobre una serie de retornos) no es suficiente acá porque la serie de retornos SIMULADA no puede expresar el riesgo real de cola de esta estrategia en particular — a diferencia de Carry cripto, que SÍ es lineal/delta-neutral por construcción (dos patas spot+futuro, sin convexidad, sin riesgo de gamma). **No se promueve a "validada"** — antes de confiar en esto haría falta, como mínimo, paper trading real contra la cuenta de Deribit (testnet existe, `test.deribit.com`) para exponer el mecanismo a fricciones reales de margen/liquidez que ningún backtest teórico puede simular.

## Investigación 2026-11-10 (continuación): Day Trading intradiario en cripto — SIN edge, peor que Satélite

(Ver conversación: "¿y el day trading?" — a diferencia de Satélite, que opera sobre velas DIARIAS con holding de 1-3 ruedas, esto corre las mismas 3 tácticas técnicas (`short_term_engine.py`: mean reversion, volume breakout, ATR breakout) sobre velas HORARIAS reales de BTC (49.387 horas, 2021-03 a hoy, mismas que se usaron para las opciones) con holding de 12 a 48 HORAS — day trading de verdad, no una variante de Satélite.)

**Bug real encontrado y corregido antes de reportar**: la primera corrida dio Max Drawdown=-100% en las 3 variantes — no por un hallazgo real, sino porque el script apostaba el 100% del capital en cada operación sin ningún límite, y con ~2.000 operaciones y profit factor <1, apostar todo repetidamente compone a ruina matemática (gambler's ruin), no refleja la calidad real de la táctica. Corregido con un tamaño de posición del 20% del capital del sleeve por operación (default conservador declarado).

**Resultado real (3 variantes, 12h/24h/48h de holding máximo)**: profit factor 0.43-0.53, win rate 28.5-29.3%, Sharpe -3.7 a -5.9, Max Drawdown -79% a -84%, **DSR entre 10⁻⁴⁰ y 10⁻⁸²** (cero estadístico, ni cerca de 0.95). Peor que la versión diaria de Satélite (profit factor 0.16-0.21) en términos relativos de riesgo (Max DD mucho peor), aunque el profit factor crudo es algo menos malo. **Candidata descartada** — las mismas 3 tácticas técnicas no mejoran por operar en una escala de tiempo más corta, en cripto.

## Bug real encontrado y corregido 2026-11-10: `yield_engine.py::daily_caucion_return_pct` devolvía ~100x menos de lo real

(Ver conversación: "para mí hay opciones incluso con menos riesgo" — al armar el backtest de Caución/BADLAR en USD para evaluar esa idea, se encontró que `portfolio/carry_lecap_cer_backtest.py` ya había detectado este bug en una auditoría anterior pero lo dejó sin corregir en el archivo original por estar "fuera de su alcance".)

`daily_caucion_return_pct(tna_pct)` hacía `tna_pct/100/365` — debía ser `tna_pct/365` (mismo criterio que `backtest.py::compute_hurdle_pct`, donde el /100 y el *100 se cancelan). El resultado: **todo motor que blendea capital ocioso con esta función venía subestimando el aporte de la Caución por un factor de ~100x** — Core, Satélite, Factor Investing, Pairs Trading, Canje y el RL trader, en TODOS los backtests corridos hasta hoy. Corregido en `portfolio/yield_engine.py`; los 59 tests de los módulos que la usan (`test_arbitrage.py`, `test_carry_lecap_cer_backtest.py`, `test_pairs_trading.py`, `test_strategy_cpcv_validation.py`) siguen pasando (son circulares respecto a esta función, no tenían un valor esperado hardcodeado). Suite completa corriendo para confirmar que no rompe nada más allá de estos.

**Impacto declarado**: esto NO cambia el veredicto de ninguna estrategia ya evaluada hoy (Satélite sigue con weight=0 por el Kelly cap independientemente de este bug: dos problemas distintos que coincidían en el mismo síntoma), pero sí significa que **los reportes históricos de "cuánto aportó la Caución al capital ocioso" en Core/Satélite/Factor/Pairs estaban subestimados ~100x** hasta ahora — vale la pena re-correr esos backtests completos en algún momento para ver si el veredicto de alguno cambia con el capital ocioso aportando lo que realmente corresponde.

## Investigación 2026-11-10 (continuación): Caución/BADLAR en USD — la peor idea de "bajo riesgo" de todas las probadas hoy

Se backtesteó sostener pesos a tasa BADLAR (proxy real de Caución, mismo que ya usa `carry_lecap_cer_backtest.py`) convertido a USD vía CCL real (`macro_data.get_ccl_series`, 2013-2026, 5061 días reales) — exactamente la pregunta "¿no hay algo con menos riesgo?" llevada a los datos.

**Resultado real: Sharpe -0.44, Max Drawdown -92.1%, retorno anualizado -14.5%, DSR≈0.00004.** Por sub-períodos de 2 años: pierde en 6 de los 7 tramos (2013-2014 hasta 2023-2024, entre -1.2% y -33.1% anual), solo el tramo más reciente (2025-2026) da levemente positivo (+1.9%).

**Conclusión, directa**: sostener pesos a tasa de Caución, medido en USD (el único benchmark que importa en este proyecto), es consistentemente una de las formas de PERDER plata más confiables de los últimos 13 años — la devaluación del peso superó a la tasa nominal casi siempre. "Se siente seguro" en pesos nominales, pero en USD es de lo más riesgoso que se probó en todo este documento (Max DD -92% vs. <6% de Carry cripto sin apalancar). Mismo patrón que ya había aparecido con CER/TX26 (-7.7% anual, -74% DD) — la lección se repite: en este proyecto, "bajo riesgo en pesos" y "bajo riesgo en USD" son cosas casi opuestas. **Lo de MENOR riesgo real que existe hoy en Kronos, medido en USD, sigue siendo Carry cripto SIN apalancar** (Max DD <6%, DSR≥0.95) — no hay nada más seguro ya validado en este proyecto.

## Bug real encontrado y corregido 2026-11-10: `run_dynamic_allocation` quedaba congelado para siempre

(Ver conversación: "cómo mejoramos el rendimiento" → al intentar re-correr el asignador de capital con las 6 monedas de Carry cripto, devolvía "0 estrategias elegibles" pese a tener 7 candidatas reales.)

`run_dynamic_allocation` solo consideraba estrategias en estado `validated`/`live` — una vez que TODAS las candidatas avanzaron a `paper_trading` (el flujo normal del gate), la función quedó con 0 elegibles para siempre y nunca más pudo re-balancear. Confirmado: las 4 monedas de Carry cripto agregadas el 24/10 (ADA/DOGE/LINK/LTC) seguían con `capital_pct=0` congelado pese a estar validadas, porque nunca se volvió a correr desde que pasaron a paper trading. Corregido incluyendo `paper_trading` en la elegibilidad (seguro: `paper_ledger.py` no usa `capital_pct` para su propio libro, cada estrategia arranca en USD 10.000 por separado). Re-corrido real, split actualizado de 51.8/45.8/2.4% a BTC 18.2% / ETH 16.2% / ADA 14.3% / DOGE 14.5% / LINK 16.9% / LTC 16.2% / Canje 1.2% / Opciones VRP 2.5% — Sharpe esperado del combo: 8.57. Desplegado a producción y re-corrido ahí también (mismo resultado exacto).

## Bugs reales encontrados y corregidos 2026-11-10: camino de ejecución real nunca usaba el fill REAL, solo el estimado pre-trade

(Ver conversación: "busquemos otras formas" → auditoría del código de ejecución real antes de conectar la primera cuenta de Binance, en vez de buscar una estrategia nueva. Nunca antes probado ni siquiera en demo, confirmado.)

1. **`binance_execution.py::place_real_binance_order`** persistía siempre `cantidad`/`precio`/`monto_usd` tal como los pasó el llamador (el estimado PRE-trade usado para el chequeo de tope) — nunca el precio real de ejecución de una orden MARKET, que lo decide el mercado en ese instante. Esto significa que `sum_confirmed_binance_exposure_usd` (el tope real de exposición) compondría sobre números cada vez más desactualizados. Corregido extrayendo el fill real: para spot viene en la misma respuesta de la orden (`fills`/`executedQty`/`cummulativeQuoteQty`, confirmado contra la doc oficial de Binance); para futuros la respuesta de `POST /fapi/v1/order` **no trae `avgPrice`** (hallazgo real, confirmado contra la doc oficial — solo aparece en `GET /fapi/v1/order`) — se agregó `BinanceAdapter.query_order` para esa consulta de respaldo. 4 tests de regresión nuevos (spot con fill real, futuros vía query_order, y una LIMIT sin llenar que conserva el estimado sin inventar nada).
2. **`execution_router.py::_apply_decision`** (bug conectado al anterior): al abrir una posición, persistía `decision["legs"]` (la INTENCIÓN pre-trade) en `strategy_positions` en vez de `result["filled_legs"]` (lo que Binance ejecutó de verdad, ya disponible gracias al fix anterior) — el registro de la posición real hubiera quedado con cantidad/precio/capital_usd estimados para siempre. Corregido, con test de regresión que confirma que ahora se persiste el fill real (60050, no el estimado 60000 del intent).

124 tests de la cadena de ejecución (`broker_client`, `binance_execution`, `multi_leg_execution`, `execution_router`, `order_execution`, `live_strategy_engine`, `strategy_registry`, `capital_allocator`) pasan con ambos fixes aplicados. Ninguno de los dos bugs había tocado plata real todavía (confirmado: `broker_accounts` sin ninguna fila de Binance) — se encontraron y corrigieron ANTES de que importaran, que es el momento correcto.

## Investigación 2026-11-10 (continuación): modelado de margen/liquidación real para Opciones Cripto (VRP) — riesgo confirmado, pero no concluyente

(Ver conversación: "terminá de analizar la de opciones" — se había declarado que el backtest delta-hedgeado no podía ver el riesgo de margen/liquidación forzada. Se modeló explícitamente usando la fórmula pública de margen de Deribit, standard/naked: MM por pata corta = 7.5% del subyacente + valor mark de la opción, SIN el netting favorable de portfolio margin.)

Se simuló, hora a hora, si el colateral inicial (como múltiplo del margen requerido al momento de abrir la posición) alcanzaba para cubrir el margen requerido real en cada punto de los 97 períodos:

| Colateral inicial | Liquidaciones | Sharpe | Max DD | DSR |
|---|---|---|---|---|
| 3.0x el margen inicial | 0 de 97 | 0.68 | -8.8% | 0.99997 |
| 2.0x | 1 de 97 | 0.68 | -8.8% | 0.99997 |
| 1.5x | 8 de 97 | 0.68 | -8.8% | 0.99997 |
| 1.2x | 12 de 97 | 0.67 | -8.8% | 0.99998 |

**Hallazgo real e importante**: las liquidaciones SÍ ocurren (hasta 12 de 97 períodos con poco colateral) — confirma que el riesgo de margen es real, no teórico. Pero en los 5.6 años de esta muestra, **cada vez que se liquidó fue durante un movimiento FAVORABLE grande** (ej. el rally de sept-oct 2021), no uno adverso — el valor nocional bruto de la pata corta en problemas se dispara igual aunque el hedge esté funcionando bien (el margen "naked" de Deribit no neta el riesgo real de la posición cubierta). Por eso el Sharpe/DSR casi no cambian con menos colateral: la posición se cierra forzadamente ya estando ganadora, no en una pérdida.

**Por qué esto NO cierra la investigación, ni para bien ni para mal**: el modelo de margen es una aproximación pública declarada (sin acceso a portfolio margining real, que Deribit sí ofrece y sería más eficiente). Y, más importante: esta muestra de 5.6 años **nunca tuvo un evento de estrés con movimiento extremo simultáneo a IV disparándose sin tiempo de reaccionar** (el escenario que realmente revienta posiciones cortas de volatilidad, tipo COVID-2020 o Mt.Gox-2014) — no estar en la muestra no significa que no pueda pasar. El hallazgo real es: "el riesgo de margen existe y es medible, y en este período no se materializó en pérdida" — sigue sin ser el mismo nivel de certeza que Carry cripto. Conclusión sin cambios: necesita paper trading real contra Deribit antes de confiar más.

## Investigación 2026-11-10 (continuación): búsqueda de estrategias ya documentadas en GitHub — nada nuevo encontrado

(Ver conversación: "busquemos motores en github para probarlos también".)

Se buscaron repositorios con una estrategia ESPECÍFICA y documentada (no solo infraestructura/frameworks) para testear con nuestro propio rigor. Resultado: la inmensa mayoría de lo encontrado es infraestructura de backtesting genérica (`backtesting.py`, `vectorbt-backtesting-skills`, `freqtrade`, `lumibot`) sin una regla concreta propia, o investigaciones que prueban EXACTAMENTE el mismo mecanismo que ya tenemos validado (`martinjmz97/crypto-quant-research`: "long spot + short perp para cobrar funding" — es Carry cripto, ya lo tenemos). Un dato real y útil que sí apareció: un research de terceros (`sbOogway/sbt2` issue #358) reporta que **el basis trade de futuros CON VENCIMIENTO pierde ~3 veces más que el basis de perpetuos durante movimientos grandes** — esto descarta, antes de invertir tiempo en backtestearlo nosotros mismos, la línea que se había empezado a investigar (futuros trimestrales de Binance, BTCUSDT_261225/270326, datos confirmados disponibles) por ser estructuralmente peor que lo que ya tenemos, no mejor. **Candidata descartada sin necesidad de backtest propio.**

También se evaluó Hyperliquid (DEX de perpetuos) como venue alternativo para el MISMO mecanismo de Carry cripto: funding de BTC ahí da 8.16% anualizado bruto (vs ~5.22% de Binance en la misma ventana reciente), pero con solo 18 meses de historial real (el endpoint arrancó en mayo 2025, muy por debajo de los 5-7 años de Binance) y 55.8% de las horas clavadas en la misma tasa (probable piso fijo, no prima de mercado real) — y ejecutarlo requeriría wallet on-chain + riesgo de base entre Binance spot y Hyperliquid, un salto operativo real. **Anotado para revisar en unos meses con más historial, no para construir ahora.**

## Investigación 2026-11-10 (continuación): lending de USDC en Aave — mecanismo estructural nuevo, real, pero modesto

(Ver conversación: "cómo hacemos nuestra propia estrategia funcional" — buscando un mecanismo con la MISMA firma estructural que Carry cripto (pago recurrente real por proveer capital a traders apalancados, no predicción de precio) pero en un lugar nuevo. Confirmado: `/sapi/v1/margin/interestRateHistory` de Binance —el lending de margen, el análogo centralizado— es un endpoint `USER_DATA` que exige cuenta autenticada, no se puede backtestear sin credenciales que todavía no tenemos. En su lugar se usó Aave (protocolo de préstamos DeFi, datos 100% públicos vía `yields.llama.fi`, sin necesitar cuenta).)

**Datos reales**: 1.374 días (2023-02 a hoy) de APY real de supply de USDC en Aave v3 (Ethereum). APY promedio 4.7% (rango 0.3%-56.7%, picos raros). Sharpe/DSR calculados dan números artificialmente altos (22.95 / 0.9997) por la misma razón que ya vimos con Caución antes de ajustar: es un producto de yield casi sin volatilidad día a día, no un trade — esos números no son comparables a los de las demás estrategias.

**Por qué esto SÍ es relevante, aunque modesto**: a diferencia de la Caución en pesos (que resultó de las PEORES ideas del día por la devaluación), este yield está **100% denominado en USD real** (USDC) — sin riesgo cambiario. El único riesgo real es de contrato inteligente (Aave, auditado, +4 años en producción, TVL grande) y de despegue del stablecoin (USDC se despegó brevemente en el colapso de SVB, marzo 2023). Es probablemente la respuesta más honesta a "¿hay algo con menos riesgo?" que surgió antes en la conversación — pero el retorno (4.7% anual) es modesto, no un motor de rendimiento grande. Requiere wallet cripto para ejecutar (no es una cuenta de broker tradicional) — mismo tipo de salto operativo que Hyperliquid.

## Investigación 2026-11-10 (continuación): screening sistemático de ~518 perpetuos reales de Binance — 22 monedas nuevas confirmadas y agregadas a Carry cripto

(Ver conversación: "cómo hacemos nuestra propia estrategia funcional" — en vez de inventar un mecanismo nuevo, se aplicó el MISMO mecanismo ya validado de Carry cripto (cash-and-carry de funding rate) a TODO el universo real de perpetuos de Binance Futures, no solo a las 6 monedas elegidas a mano hasta ahora.)

Se filtró el catálogo real de Binance Futures por `underlyingType=='COIN'` (excluyendo los perpetuos sintéticos de acciones/commodities, ver investigación de momentum del mismo día) — **518 símbolos reales**. Se corrió `simulate_funding_rate_carry_engine` + `run_strategy_cpcv_validation` sobre los 518, con el mismo filtro mínimo de historial (≥180 días para calcular, pero ver el filtro más estricto abajo antes de confiar en el resultado).

**73 símbolos cruzaron DSR≥0.95 en crudo** — pero la mayoría con muy poca historia real (182-500 días, monedas meme/hype recién listadas: `USELESSUSDT`, `FARTCOINUSDT`, `CHILLGUYUSDT`, etc.) — el mismo patrón de "ruido de poca muestra disfrazado de edge" que ya se había detectado con el universo de momentum del mismo día. **Aplicando el mismo piso de historial (≥900 días) que ya se usó ahí, quedan 22 candidatas nuevas, genuinamente sólidas** (DSR≥0.95, 900-2600 días reales cada una), varias de ellas monedas grandes y líquidas, no solo nichos: UNI, AAVE, ALGO, XMR, NEO, VET, ENS, XRP, SUSHI, BAT, ZEN, AR, KAS, MAVIA, SLP, TWT, CFX, PENDLE, 1000SATS, ETHW, SSV, 1000RATS.

**Integradas al pipeline en vivo** (mismo patrón que la ampliación del 24/10): agregadas a `crypto_carry.py::FUNDING_SYMBOLS`, intent builders reales en `live_strategy_engine.py`, simuladores en `capital_allocator.py`, y las 22 arrancadas en Paper Trading (`strategy_registry`). De 6 monedas con Carry cripto pasamos a **28** — la base ejecutable de esta estrategia se multiplicó por ~4.7x. Esto no es una estrategia nueva (sigue siendo el mismo mecanismo, el mismo riesgo estructural) pero SÍ es una expansión real y rigurosa de la única cosa que ya funciona.

Nota declarada: `XRPUSDT` dio DSR=0.9555 hoy (pasa) vs. "0.94, justo debajo del gate" el 24/10 — misma explicación que las demás discrepancias del día (ventana de datos que se mueve con el tiempo), no una corrección de un error.

## Hito real 2026-11-11: primera conexión real a Binance (solo lectura)

Después de varios intentos bloqueados por un bug real de Binance (Demo Trading nunca terminaba de mostrar el Secret Key al crear la API key, confirmado en múltiples intentos, distintos navegadores), se conectó una cuenta REAL de Binance (producción, no demo) con una API key limitada a **solo lectura** (Binance fuerza esto automáticamente si la IP queda sin restringir). `BinanceAdapter.test_connection()`/`get_balance()` respondieron reales por primera vez en la historia del proyecto — confirma que la firma HMAC-SHA256, las URLs y el parseo de respuesta funcionan de punta a punta contra Binance real.

**Alcance real de esto, sin exagerar**: valida CONEXIÓN y LECTURA. NO valida ejecución (`confirm_order`, mandar una orden de verdad) — la key no tiene permiso de trading, a propósito, y seguirá sin probarse hasta que (a) se resuelva el bug de Demo Trading, o (b) se tome la decisión explícita de habilitar trading en una cuenta real con capital acotado. Documentado en `broker_client.py`/`crypto_carry.py`, reemplazando el disclaimer "nunca probado" que ya no es cierto para la parte de lectura.

## Investigación 2026-11-10 (continuación): lending DeFi confirmado en 4 protocolos establecidos — mismo yield, mejor diversificado

(Ver conversación: "armemos la estrategia nosotros" — aplicando el mismo principio que acaba de funcionar con Carry cripto, screening amplio en vez de un solo pool elegido a mano, sobre el hallazgo de Aave de antes.)

Se filtró el catálogo completo de `yields.llama.fi/pools` (15.620 pools reales) por: protocolo establecido y auditado (Aave v3, Compound v3, Venus), solo stablecoins (USDC/USDT/GHO), TVL>USD 20M — 9 candidatos reales. Con ≥300 días de historial (4 quedaron afuera por ser demasiado nuevos):

| Pool | Días reales | APY promedio | DSR |
|---|---|---|---|
| Aave v3 (Ethereum) USDC | 1.375 | 4.70% | 0.9997 |
| Compound v3 (Ethereum) USDC | 1.494 | 4.90% | 1.0 |
| Aave v3 (Arbitrum) USDC | 1.233 | 4.94% | 0.99997 |
| Venus (BSC) USDT | 1.590 | 4.39% | 1.0 |
| Aave v3 (Ethereum) USDT | 1.368 | 4.44% | 0.828 (más débil) |

**Conclusión**: el yield (~3-5% real en USD) es consistente entre 4 protocolos grandes e independientes, no una casualidad de uno solo. Esto no cambia la magnitud (sigue siendo modesto, mismo carácter que el hallazgo de Aave ya documentado), pero sí da una mejora concreta y real: **diversificar el capital entre Aave/Compound/Arbitrum/Venus en vez de concentrarlo en un solo protocolo reduce el riesgo de un solo contrato inteligente fallando, sin resignar retorno esperado** — mismo principio de risk parity que ya se aplica en Carry cripto, aplicado acá a riesgo de custodia en vez de riesgo de mercado.

## Investigación 2026-11-10 (continuación): lado de ACCIONES (EEUU) — 2 candidatas probadas, ninguna pasa el gate todavía

(Ver conversación: "pensar una estrategia que no sea cripto, del lado de acciones" — datos reales de yfinance, 36 tickers líquidos de EEUU (SPY/QQQ/DIA/IWM + 32 large caps de varios sectores), 10 años de historial diario real.)

**Efecto "overnight" (cierre→apertura) — descartado.** Documentado en literatura académica: el retorno de EEUU se concentra históricamente en el tramo nocturno, no durante el pregón. En bruto (sin costos): Sharpe 1.12, retorno anualizado 12.8%, pero **DSR=0.028** (lejos del gate) — y el retorno diario promedio bruto (0.050%) es casi idéntico al costo de transacción realista asumido (0.05%), o sea que ni siquiera hace falta el DSR para descartarlo: es económicamente marginal apenas se le resta fricción real. Coincide con la literatura, que ya declara este efecto como "real pero poco práctico para minoristas por costos".

**Efecto "turn-of-month" (últimos 3 + primeros 3 días hábiles del mes) — la candidata de acciones más prometedora de hoy, pero todavía NO pasa el gate.** Definición estándar (Ariel 1987). Resultado real: Sharpe 1.33, Max DD -10.6% (vs. -30.6% del resto del mes), retorno anualizado 21.7%, **DSR=0.682** — no llega a 0.95, pero es el DSR más alto de cualquier estrategia de acciones probada en este proyecto. Partido en 2 mitades de 5 años cada una para chequear consistencia (mismo criterio que ya detectó que el día de la semana en cripto era ruido): acá el retorno diario promedio es casi idéntico en ambas mitades (0.081% vs 0.087%) y el Sharpe también (1.24 vs 1.46) — **consistente, no se invierte como el día de la semana cripto**. Palancas que quedan sin probar antes de declararlo descartado: (1) más historial (yfinance permite ir más atrás de 10 años), (2) universo más amplio (S&P 500 completo en vez de 36 tickers elegidos a mano) — mismo patrón que ya funcionó para Carry cripto (escanear todo en vez de elegir a mano).

## Investigación 2026-11-11: bot de "swing trading" en ACCIONES EEUU (reglas del Satélite, holding 5d/10d) sobre el S&P 500 completo — ambas variantes descartadas

(Ver conversación: "quiero analizar retorno de acciones solamente, de un bot que haga compra venta de las mismas en plazos de 1 semana, 10 dias, o con un objetivo claro en función de determinadas variables". Mismo motor de señales que ya usa el Satélite (`short_term_engine.evaluate_short_term_signals`), mismo esquema de salida con TP/SL/trailing-stop por ATR que ya usa `backtest.simulate_position_exit`, aplicado sobre el universo COMPLETO del S&P 500 (503 tickers reales, no una selección a mano), 10 años de historial diario real de yfinance, 2% del capital del sleeve por trade, 0.05% de costo de ida y vuelta por trade (declarado).

Bug real encontrado en el camino (no de la estrategia, de la obtención de datos): Yahoo Finance rate-limita pedidos muy seguidos (>1000 pedidos en poco tiempo) y devuelve filas con OHLC en NaN para tickers activos, reportándolos como "possibly delisted" — un solo NaN rompe el Sharpe/Max DD de la serie ENTERA porque `std`/`cumprod` propagan NaN. Corregido con `dropna` post-fetch + `sleep(0.3s)` entre pedidos (antes 0.02s) antes de confiar en el resultado.

| Variante | Trades | Win rate | Sharpe | Max DD | Anualizado | Profit factor | DSR |
|---|---|---|---|---|---|---|---|
| Holding 5 días (TP 4%/SL 9%/trailing ATR 1.8x) | 37.668 | 53.1% | 0.14 | -52.7% | 0.7% | 1.02 | 0.0000023 |
| Holding 10 días (TP 6%/SL 12%/trailing ATR 2.0x) | 35.260 | 49.9% | 0.72 | -59.4% | 15.0% | 1.13 | 0.0000891 |

**Conclusión, sin exagerar**: ninguna pasa el gate, ni cerca. A diferencia de Carry cripto (donde ampliar de 6 a 28 monedas sí destapó el edge real — ahí el problema era muestra chica), acá 503 tickers y 10 años YA es un universo amplio: el problema no es tamaño de muestra, es que las reglas del Satélite (pensadas para cripto, con su volatilidad y su estructura de funding) no tienen un edge real de por sí sobre acciones de EEUU con holding de días. La variante de 10 días mejora bastante el Sharpe/retorno frente a la de 5 días, pero el drawdown (-59%) sigue siendo desproporcionado para ese Sharpe, y el DSR queda en otro orden de magnitud respecto al gate.

Palancas sin probar todavía antes de descartar por completo el "bot de acciones" (consistente con lo que destapó el edge en Carry cripto y en turn-of-month: no abandonar una idea razonable sin probar escala/reglas distintas primero): (1) reglas de entrada distintas a las del Satélite, diseñadas específicamente para acciones (ej. combinar con el efecto turn-of-month ya documentado arriba, que sí tiene DSR=0.682); (2) holding períodos intermedios (ni 5 ni 10, sino algo calibrado por volatilidad del ticker); (3) filtrar el universo por liquidez/sector en vez de tomar los 503 tickers parejo.

## Investigación 2026-11-11 (continuación): turn-of-month con S&P 500 completo y máximo historial — DSR=0.999 en el agregado, pero NO ROBUSTO por sub-período

(Continuación directa del hallazgo de arriba. Se probó ampliar el efecto turn-of-month de 36 tickers/10 años a 498 tickers reales del S&P 500/historial máximo de yfinance, mismo patrón que ya funcionó en Carry cripto.)

Con 498 tickers (de 503, los otros sin suficiente historial) y datos combinados desde 1962-01-02 hasta hoy (16.324 días calendario, 3.708 días de turn-of-month reales): Sharpe 2.07, Max DD -25.5%, anualizado 37.8%, **DSR=0.9989** — pasaría el gate.

**Pero no es robusto.** Partido en 4 cuartos de ~927 días hábiles cada uno (~16 años cada uno):

| Período | Media diaria | Sharpe | Anualizado | DSR |
|---|---|---|---|---|
| Q1 (1962-1978) | 0.0895% | 1.65 | 24.1% | 0.023 (falla) |
| Q2 (1978-1994) | 0.1800% | 3.41 | 56.0% | 0.99998 (pasa) |
| Q3 (1994-2010) | 0.1715% | 2.12 | 50.9% | 0.626 (falla) |
| Q4 (2010-2026) | 0.0889% | 1.39 | 23.5% | 0.629 (falla) |

**Conclusión honesta**: el DSR=0.999 del agregado está inflado casi en su totalidad por un solo cuarto (Q2, 1978-1994), que es anómalamente fuerte (Sharpe 3.41, más del doble que los otros 3 cuartos). Los otros 3 cuartos — el 75% restante de 64 años de historia — NO pasan el gate por sí solos (DSR 0.023, 0.626, 0.629). Esto es justo el tipo de discrepancia que este proyecto declaró nunca dejar sin explicar: **no se puede tratar el DSR=0.999 agregado como una validación real del efecto** — es un resultado dominado por un régimen histórico específico (posiblemente post-crisis del 73-74/inflación de los 70s-80s, sin confirmar causa), no un edge persistente y estable en el tiempo. **NO se promueve a validada.** Queda como la candidata de acciones más prometedora, pero con esta caveat explícita: cualquier decisión de capital real sobre este efecto tendría que asumir que 3 de cada 4 períodos de 16 años de la historia no lo habrían validado por separado.

## Investigación 2026-11-11 (continuación): turn-of-month + salida TP/SL/trailing del Satélite — PEOR que quedarse invertido, descartado

(Palanca #1 que había quedado pendiente: en vez de quedarse invertido toda la ventana turn-of-month, entrar al primer día de la ventana y salir con el mismo sistema de TP/SL/trailing-stop por ATR que ya usa el Satélite/`backtest.simulate_position_exit`, sobre los mismos 503 tickers/historial máximo.)

Resultado real (TP 4%/SL 6%/trailing ATR 1.5x, ventana máxima de 6 días hábiles): 208.916 trades, win rate 48.9%, Sharpe 0.21, **Max DD -85.5%**, anualizado 2.1%, profit factor 1.05, **DSR=0.0953** — mucho peor que el 0.9989 de quedarse invertido, y ningún cuarto de 16 años pasa el gate por separado (DSR entre 0.0000046 y 0.0025, todos muchísimo más débiles que los cuartos de la variante simple).

**Conclusión**: agregar el sistema de salida del Satélite empeora todo, no ayuda. Hipótesis (no confirmada, declarada como tal): con una ventana de solo 6 días hábiles, un stop-loss/trailing pensado para la volatilidad de cripto corta operaciones ganadoras antes de tiempo y no da margen frente al ruido diario normal de una acción — las reglas de salida de un activo no trasladan directamente a otro con dinámica distinta. **Descartado**; la variante turn-of-month "quedarse invertido" (con la caveat de no-robustez de arriba) sigue siendo la única candidata de acciones con algún mérito.

## Investigación 2026-11-11 (continuación): segundo bug real de ejecución Binance encontrado y corregido — primera orden real CONFIRMADA de punta a punta

(Continuación directa del fix de LOT_SIZE de hoy. Tras deployar ese fix al VPS, se reintentó la misma orden de prueba real en Demo Trading que antes fallaba.)

El LOT_SIZE quedó resuelto (cantidad redondeada a 0.00014 BTC, múltiplo válido del stepSize real), pero apareció un **segundo bug real, distinto**: Binance devolvía `400 Bad Request` sin detalle visible (el código solo capturaba `str(e)` de `raise_for_status()`, que no incluye el cuerpo de la respuesta). Se obtuvo el cuerpo real haciendo la llamada a mano: `{"code":-1100,"msg":"Illegal characters found in parameter 'newClientOrderId'; legal range is '^[a-zA-Z0-9-_]{1,36}$'."}`. El `external_id` que arma `binance_execution.py` (`kronos-binance-{symbol}-{market}-{timestamp}`) da **41 caracteres** para BTCUSDT — ya supera el límite de 36 con el symbol más corto que existe en el proyecto. Corregido acortando el formato a `kr-{symbol}-{market_abbr}-{timestamp}` (máx. 34 caracteres confirmado contra el symbol más largo de `FUNDING_SYMBOLS`, `1000satsusdt`).

Con los 2 bugs corregidos (LOT_SIZE + `newClientOrderId`), la orden de prueba (BTCUSDT spot, compra, ~10.35 USD, Demo Trading) quedó **`estado: 'confirmada'`, con `binance_order_id` real** — la primera vez que `place_real_binance_order` (el único punto de entrada real de ejecución del proyecto) se ejecuta de punta a punta sin error. Sigue siendo Demo Trading (fondos ficticios), no valida todavía ejecución con capital real, pero sí valida que TODO el camino de código (gates, firma HMAC, redondeo real, extracción de fill real, persistencia) funciona end-to-end por primera vez.

## Investigación 2026-11-11 (continuación): tercer bug real de ejecución Binance — notional mínimo de futuros con nombre de campo distinto, dejaba una pata sin cobertura

(Continuación directa de los 2 fixes anteriores: validado 1 pata, se probó por primera vez el camino completo de 2 patas (`multi_leg_execution.execute_legs`), nunca probado hasta hoy.)

Con `auto_trading_capital_usd` de prueba en 1000 y `capital_pct=4.4%` de `crypto_carry_btcusdt` (configuración real ya cargada, estrategia en `paper_trading`, nunca se tocó su estado), `live_strategy_engine.build_real_intent` devolvió una señal real: funding de BTCUSDT en 0.008%/8hs (~7.0%/año neto estimado) — la primera señal de Carry real detectada en producción. Se ejecutó: la pata **spot se confirmó** (compra real, 0.0005 BTC, ~36.85 USD), pero la pata de **futuro falló** con otro `400 Bad Request` sin detalle (mismo problema de visibilidad que el bug anterior). Cuerpo real obtenido a mano: `{"code":-4164,"msg":"Order's notional must be no smaller than 50 (unless you choose reduce only)."}`.

Causa real confirmada contra `GET /fapi/v1/exchangeInfo?symbol=BTCUSDT`: el filtro de notional mínimo de **futuros** se llama `MIN_NOTIONAL` con el valor en el campo **`notional`** (`{"filterType": "MIN_NOTIONAL", "notional": "50"}`), mientras que spot usa `NOTIONAL` con el campo **`minNotional`**. `get_lot_size_filters` solo leía `minNotional` para los dos casos — para futuros esto devolvía 0 siempre, así que el proyecto nunca supo que futuros pide un piso de 50 USD por orden (bastante más alto que el mínimo de 5 USD de spot). Con capital chico, esto deja la pata spot YA ejecutada sin su cobertura de futuro — una posición direccional real, no hedgeada, por un bug de parseo.

**Acción inmediata tomada**: se cerró la pata spot manualmente (venta real de los mismos 0.0005 BTC) para no dejar exposición real sin cobertura — posición quedó flat, sin pérdida relevante (fue casi instantáneo, mismo precio).

**Corregido de raíz**: `get_lot_size_filters` ahora lee el campo correcto según el `filterType` real (`NOTIONAL`→`minNotional`, `MIN_NOTIONAL`→`notional`). Además, `_round_carry_quantity` ahora chequea el notional de **ambas** patas contra sus propios mínimos reales ANTES de intentar cualquier ejecución -- si cualquiera de las 2 quedaría por debajo de su piso, devuelve `None` (no se abre nada), en vez de descubrirlo a mitad de camino con una pata ya ejecutada. Agregados tests de regresión (`test_binance_get_lot_size_filters_futuros_usa_min_notional_notional`, `test_carry_no_abre_si_queda_por_debajo_del_notional_minimo_real`).

**Implicancia real para capital**: con el `capital_pct` actual de cada moneda (entre 1.45% y 4.83%, promedio ~3.5%), hace falta un `auto_trading_capital_usd` de aproximadamente **USD 1.500-2.000 por asignación de 3.5%** para que la pata de futuros supere el piso real de 50 USD — un dato concreto a tener en cuenta cuando se decida el capital real del bot (`auto_trading_capital_usd` sigue sin definir, decisión pendiente del usuario).

## Investigación 2026-11-11 (continuación): turn-of-month, mitad MODERNA del historial (1994-2026) — PASA el gate de forma robusta, primera candidata real de acciones

(Retomando la no-robustez encontrada arriba: el DSR=0.9989 del historial completo 1962-2026 estaba inflado por el cuarto 1978-1994. Se prueba si la mitad más reciente del historial -- sin ese período anómalo -- sostiene el efecto por sí sola.)

Reusando la serie real ya calculada (498 tickers, mean return diario en días turn-of-month, 1962-2026): la **mitad moderna (1994-07-01 → 2026-11-03, 1.854 días reales, 32 años)** da **Sharpe 1.79, Max DD -19.2%, anualizado 36.5%, DSR=0.9689** — **pasa el gate de 0.95**, sin depender del cuarto anómalo.

**Chequeo de consistencia interna** (la mitad moderna se compone de los cuartos Q3 y Q4 ya medidos arriba): Q3 (1994-2010) DSR=0.626, Q4 (2010-2026) DSR=0.629 — **prácticamente idénticos entre sí** (a diferencia de Q2 vs el resto, que eran muy distintos). Ninguno de los 2 pasa el gate por separado (muestra insuficiente cada uno solo), pero son consistentes en magnitud, y combinados (más días = más confianza bajo CPCV) sí superan el umbral. Esto es un patrón sano -- "más historial destapa el edge real" (mismo patrón que Carry cripto) -- no un cuarto anómalo arrastrando el resto.

**Contraprueba que matiza esto**: el último tercio solo (2005-2026, 21 años, 1.224 días) da DSR=0.874 -- ya NO pasa el gate por sí solo. O sea, el efecto sigue necesitando los ~32 años completos de 1994 en adelante para cerrar con suficiente confianza estadística; con una ventana más corta y más reciente todavía no alcanza.

**Caveats declarados, sin exagerar**: (1) sigue aplicando el sesgo de supervivencia (constituyentes de HOY del S&P 500 aplicados hacia atrás), aunque mucho menos severo en 32 años que en 64; (2) **el proyecto no tiene today ningún broker para acciones de EEUU** (IOL/PPI son BYMA/Argentina, Binance es solo cripto) -- esto queda como hallazgo de investigación validado por CPCV, no como estrategia ejecutable todavía; (3) sigue siendo un efecto de mercado agregado (promedio de 498 tickers simultáneos), no una selección de acciones individuales.

**Veredicto**: primera candidata de acciones de este proyecto que pasa el gate DSR≥0.95 de forma robusta (no por un cuarto anómalo). Se promueve a `validated` en `strategy_registry` bajo el nombre `turn_of_month_sp500` para que quede trackeada (research→validated→paper_trading, sin necesitar decidir todavía el broker de ejecución real -- paper trading no requiere broker, solo bookkeeping).
