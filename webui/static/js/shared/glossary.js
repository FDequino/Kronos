// Glosario único para términos financieros/técnicos de la app. Cada término
// se explica una sola vez acá y se reutiliza con glossaryTerm(key, label) en
// cualquier pantalla -- antes el Dashboard tenía 0 tooltips y cada pantalla
// explicaba (o no) sus propios términos de forma inconsistente (plan Fase 4).
const TERMS = {
    patrimonio_mep: 'Valor total de la cartera (holdings + cauciones + caja disponible, menos préstamos recibidos) convertido a dólares al tipo de cambio MEP (bolsa), sin importar la fuente de USD elegida en Preferencias para el resto de la cartera.',
    dolar_mep: 'Dólar MEP (o "bolsa"): surge de comprar un bono en pesos y venderlo simultáneamente en dólares en el mercado local. Distinto del oficial y del CCL.',
    twr: 'Rendimiento ponderado por tiempo: mide solo la performance de las inversiones, excluyendo el efecto de cuándo depositaste o retiraste dinero.',
    xirr: 'Tasa interna de retorno con fechas reales: a diferencia del TWR, sí tiene en cuenta el momento y el tamaño de cada aporte/retiro real — mide el rendimiento de tu bolsillo, no de la decisión de inversión en sí.',
    vs_caucion: 'Diferencia entre el TWR de tu cartera y lo que hubieras ganado con cauciones en el mismo período, a la tasa de referencia cargada en Preferencias.',
    vs_inflacion: 'Diferencia entre el TWR de tu cartera y la inflación acumulada (IPC) en el mismo período.',
    aportes_retiros: 'Compras (aportes) y ventas (retiros). El TWR los excluye a propósito: depositar plata no es "ganar".',
    aportes_retiros_caja: 'Depósitos y retiros reales de la cuenta (ver Caja) — transferencias que entraron o salieron de tu bolsillo, no compras/ventas de instrumentos.',
    benchmark_caucion: 'Lo que hubiera rendido invertir en cauciones a la tasa de referencia cargada, en vez de en tu cartera.',
    spy_benchmark: 'ETF que sigue al índice S&P 500 (500 empresas grandes de EE.UU.), en dólares. Se usa como referencia de mercado internacional.',
    rebasado_100: 'Todas las curvas arrancan en 100 el día que empieza el período elegido, para comparar crecimiento % aunque estén en unidades distintas.',
    kronos_sin_validar: 'Kronos no se pinta en verde/rojo todavía: hace falta más historial de pronósticos pasados para confiar en que acierta más de lo normal.',
    acierto_direccion: 'De los pronósticos ya resueltos, qué % acertó si el precio iba a subir o bajar.',
    pronosticos_no_superpuestos: 'Pronósticos que no se pisan entre sí en el tiempo — si dos cubren casi los mismos días, se descarta el que se superpone para no inflar la muestra.',
    volatilidad_anualizada: 'Cuánto se mueve el precio típicamente en un año. Más alto = más vaivén, no necesariamente más pérdida.',
    caida_maxima: 'Drawdown: la mayor caída desde un máximo previo hasta el mínimo posterior. Cuánto se llegó a perder desde la cima en el peor momento.',
    cvar95: 'De los peores meses (el 5% más malo), cuál es la pérdida promedio — no es lo mismo que el peor mes histórico.',
    mora_prestamos: 'Capital pendiente de préstamos vigentes cuya cuota ya venció y todavía no se registró el pago correspondiente.',
    top5_concentracion: 'Qué % de la cartera representan las 5 posiciones más grandes (incluye cauciones y préstamos). Más alto = menos diversificado.',
    ars_tasa_fija: 'Todo lo que cotiza en pesos y no ajusta por CER (bonos a tasa fija, LECAP, CEDEARs, acciones, cauciones) — un CEDEAR sigue al dólar económicamente, pero no hay cobertura cambiaria explícita en este balde, por eso cae acá y no en "USD".',
    ars_cer: 'Instrumentos en pesos cuyo capital se ajusta por CER (índice que sigue a la inflación).',
    objetivo_asignacion: '% que decidiste que cada clase de activo debería representar en tu cartera (se carga en "Editar objetivo").',
    rebalanceo: 'Vender lo que está por encima de tu objetivo y comprar lo que está por debajo, para volver a la asignación definida.',
    origen_sl_tp: 'Se disparó porque el precio tocó un nivel de Stop-loss/Take-profit que vos mismo cargaste en el gráfico del instrumento.',
    origen_evento: 'Un vencimiento (bono, caución, plazo fijo) está por llegar — es un hecho que ya pasó o va a pasar, no una predicción.',
    origen_rebalanceo: 'La composición actual de la cartera se alejó del objetivo que configuraste en Preferencias más allá del umbral permitido.',
    // senal_kronos/senal_tecnica (ver auditoria 19/10/2026, hallazgo 1.3):
    // son 2 fuentes independientes que pueden validarse para el mismo
    // activo apuntando a direcciones opuestas -- origen_senal queda de
    // fallback para propuestas viejas, ya no se genera.
    origen_senal_kronos: 'El pronóstico de Kronos para este activo ya tiene suficiente historial propio acertando como para confiar en la dirección que sugiere — nunca se muestra sin validar acá.',
    origen_senal_tecnica: 'El RSI (independiente de Kronos) ya tiene suficiente historial propio de edge en este patrón como para confiar en la dirección que sugiere — nunca se muestra sin validar acá.',
    origen_senal: 'Kronos o el técnico (RSI) ya tienen suficiente historial propio acertando en este activo como para confiar en la señal — nunca se muestra sin validar acá.',
    peso_posicion: 'Qué % del valor total de la cartera (incluye cauciones) representa esta posición.',
    rango_p10_p90: 'Rango donde Kronos ubica el 80% de sus simulaciones. Cuanto más ancho, menos preciso es el pronóstico.',
    lecap: 'Letra del Tesoro a tasa fija sin cupón — se compra a descuento y se cobra el valor nominal completo al vencimiento.',
    bono_cer: 'Bono cuyo capital se ajusta por CER. Su TIR no se calcula localmente — viene de una fuente externa, porque requeriría proyectar inflación futura.',
    bono_tasa_fija: 'Bono que paga un cupón de interés fijo en pesos, sin ajuste por inflación.',
    teorico_kronos: 'Qué hubiera valido la cartera si, en cada decisión, hubieras seguido la señal de Kronos de ese momento en vez de tu decisión real.',
    costo_promedio: 'Precio promedio pagado por unidad, ponderado por cantidad, de todas las compras de este instrumento.',
    pnl: 'Ganancia o pérdida no realizada: diferencia entre el valor actual y el costo total pagado.',
    pnl_pct: 'La misma ganancia o pérdida, como % sobre el costo total pagado.',
    tecnico_senal: 'Señal basada en RSI, independiente de Kronos. Se pinta en gris si no tiene suficiente edge demostrado en el histórico.',
    kronos_sin_pronostico: 'Todavía no se corrió un pronóstico de Kronos para este activo.',
    pe_ratio: 'Precio/Ganancias: cuántas veces la ganancia anual por acción estás pagando al comprar. Rango típico de mercado: 10-25. Por debajo puede ser una oportunidad (o un negocio con problemas); por encima de 25-30 suele reflejar expectativa de alto crecimiento o sobrevaluación. El color acá usa tu umbral configurado en Preferencias (Fundamental -> "P/E máximo").',
    pe_futuro: 'P/E pero usando la ganancia estimada a futuro en vez de la ya reportada — si es menor al P/E actual, el mercado espera que la ganancia crezca. Mismo rango de referencia que el P/E (10-25 típico, >40 señal de posible sobrevaluación), pero sin un umbral propio configurable: es una guía general, no un corte exacto.',
    margen_neto: 'Ganancia neta como % de las ventas totales — cuánto le queda de cada peso vendido después de todos los costos. El color acá usa tu umbral configurado en Preferencias (Fundamental -> "Margen neto mínimo"); en general, >15% es sólido, 5-15% es normal, <5% es ajustado.',
    deuda_patrimonio: 'Deuda total dividido patrimonio neto, en % — cuánta deuda tiene la empresa en relación a lo que vale por sí misma. Por debajo de 100% es conservador, 100-200% es moderado, por encima de 200% es apalancamiento alto (más riesgo si suben las tasas o caen las ventas).',
    dividend_yield: 'Dividendos pagados en el último año como % del precio actual de la acción. No es una señal de compra/venta por sí sola: un yield alto puede ser una empresa que reparte bien las ganancias, o una cuyo precio cayó fuerte (el yield sube aunque el dividendo no haya cambiado).',
    cap_mercado: 'Valor total de la empresa en bolsa: precio de la acción × cantidad de acciones en circulación. Es una medida de tamaño, no de si conviene comprar o vender.',
    umbral_fundamental: 'Límite configurado en Preferencias para decidir si el P/E o el margen de una empresa pasan el filtro fundamental.',
    rsi14: 'Índice de Fuerza Relativa sobre 14 velas: mide si un activo viene subiendo o bajando "demasiado rápido". Va de 0 a 100.',
    sobreventa_sobrecompra: 'Sobreventa: RSI tan bajo que históricamente suele rebotar (compra). Sobrecompra: tan alto que suele corregir (venta). Umbrales configurables en Preferencias.',
    pasos_horizonte: 'Cantidad de días hacia adelante que pronostica Kronos en esta corrida (configurable).',
    mediana_pronostico: 'Valor central de todas las simulaciones de Kronos: la mitad dieron un resultado mejor, la mitad peor.',
    prob_suba: 'De todas las simulaciones de Kronos, qué % terminó en valores por encima del precio actual.',
    ccl: 'Contado con liquidación: dólar que surge de comprar un activo en pesos acá y venderlo afuera en dólares. Es el que usa Kronos para convertir series antes de pronosticar.',
    sma20: 'Promedio móvil simple de los últimos 20 cierres — suaviza el precio para ver la tendencia sin el ruido día a día.',
    ema20: 'Promedio móvil exponencial de 20 períodos — como la SMA pero reacciona más rápido a cambios recientes.',
    bollinger: 'Bandas a una distancia (en desvíos estándar) de un promedio móvil — tocar el borde suele leerse como sobrecomprado/sobrevendido.',
    macd: 'Diferencia entre dos promedios móviles exponenciales (12 y 26) — cruces contra su propia señal suelen leerse como cambio de tendencia.',
    edge_tecnico: 'Cuánto mejor (o peor) acertó esta señal que simplemente asumir la tendencia natural del activo (tasa base). Cerca de 0 = la señal no aporta nada.',
    tasa_base: 'Qué tan seguido sube (o baja) el activo sin mirar ninguna señal — el punto de comparación para medir si una señal aporta algo.',
    acierto_crudo: '% de aciertos sin comparar contra la tasa base — por sí solo no dice si la señal sirve.',
    ventana_backtest: 'Cada señal pasada se compara contra el retorno real de los días siguientes (configurable), sin mirar nunca datos futuros al momento de generarla.',
    mape: 'Error porcentual absoluto promedio: en promedio, qué tan lejos (en %) estuvo cada pronóstico pasado del precio real.',
    predicho_en_su_momento: 'El pronóstico tal como se generó en su momento, sin ajustar con información que todavía no existía en esa fecha.',
    tir: 'Tasa Interna de Retorno: la tasa anual que hace que el precio pagado hoy equivalga al valor presente de los pagos futuros del bono.',
    duration: 'Duration de Macaulay: plazo promedio en que recuperás tu inversión. La modificada estima cuánto se mueve el precio por cada punto que se mueve la tasa.',
    bono_bullet: 'Modelo de bono que paga cupones periódicos y devuelve todo el capital de una sola vez al vencimiento.',
    comision_tx: 'Lo que cobró el broker por esta operación — puede incluir derechos de mercado e IVA según cómo lo haya cargado tu broker.',
    renta_tipo: 'Renta: pago periódico de interés. Amortización: devolución de capital. Dividendo: reparto de ganancias de una acción o CEDEAR.',
    tna: 'Tasa Nominal Anual: tasa de interés anual sin componer (sin reinversión de intereses).',
    devengado: 'Interés ya "ganado" hasta la fecha, proporcional al tiempo transcurrido, aunque todavía no lo cobraste.',
    pf_tipo: 'Tradicional: tasa fija en pesos. UVA: el capital se ajusta por inflación (UVA) además de pagar una tasa.',
    caucion: 'Préstamo colateralizado de muy corto plazo vía el broker: le prestás tu plata (o títulos) al mercado por unos días a cambio de una tasa.',
    dias_caucion: 'Días transcurridos sobre el plazo total pactado.',
    boleto_caucion: 'Número de operación que identifica esta caución en el mercado.',
    heuristica: 'Regla simple basada en umbrales configurables, no un modelo estadístico entrenado — se explicita para no confundirla con Kronos.',
    cuotapartes: 'Unidad de participación en un fondo común de inversión — equivalente a "acciones" del fondo.',
    fondos_comunes: 'Fondos Comunes de Inversión: Kronos no tiene histórico de precio diario propio para pronosticarlos.',
    byma: 'Bolsas y Mercados Argentinos: el mercado donde cotizan acciones y CEDEARs locales.',
    cedear: 'Certificado que representa una acción extranjera, pero cotiza y se liquida en pesos en BYMA.',
    on_generic: 'Obligación Negociable: deuda emitida por una empresa (no el Estado), similar a un bono.',
    clase_taxonomia: 'LECAP: letra a tasa fija sin cupón. Bono CER: ajusta capital por inflación. Bono tasa fija: paga cupón fijo en pesos. ON: deuda de una empresa. CEDEAR: certificado de una acción extranjera. Acción: acción local.',
    var_pct_explorer: 'Variación % contra el cierre del día hábil anterior, informado por data912.',
    notif_cobro: 'Cobraste un dividendo, renta o amortización en los últimos días.',
    notif_vence: 'Un bono, ON, plazo fijo, caución o cuota de préstamo vence o paga cupón dentro de los próximos días.',
    notif_vencido: 'Un préstamo vigente tiene una cuota ya vencida sin pago registrado todavía.',
    notif_bono_vencido: 'Un bono/ON en tu cartera ya pasó su fecha de vencimiento y sigue ahí -- revisá si corresponde darlo de baja.',
    notif_senal: 'Kronos tiene una señal de venta activa y validada para este activo.',
    fuente_usd: 'Fuente de la cotización USD usada para convertir la cartera: BNA, MEP o CCL. No afecta el encabezado del Dashboard, que siempre muestra MEP.',
    ajuste_inflacion: 'Si está activo, los montos en ARS se muestran ajustados por inflación acumulada ("pesos de hoy") en vez de pesos nominales de cada fecha.',
    tasa_caucion_ref: 'Tasa que cargás vos a mano para comparar tu cartera contra "invertir en cauciones" — no existe una fuente pública gratuita de esta tasa.',
    abanico_pronostico: 'El rango p10-p90 de cada pronóstico pasado, dibujado desde la fecha en que se generó. El ancho es una interpolación lineal (el modelo solo da un rango para el cambio total del horizonte, no un rango por día).',
    calibracion_kronos: 'De los pronósticos pasados ya resueltos, qué % tuvo el precio real dentro de su propio rango p10-p90. Si el modelo está bien calibrado, debería rondar 80%.',
    notif_sltp: 'Un stop-loss o take-profit que configuraste vos mismo ya fue tocado por el precio.',
    propuesta_origen: 'De dónde viene la sugerencia, ordenado por confianza: regla propia (SL/TP) > evento ya confirmado (vencimiento) > rebalanceo vs. objetivo configurado > señal estadística ya validada.',
    stop_loss: 'Nivel de precio por debajo del cual sugerís vender para limitar una pérdida. Lo cargás vos; Kronos no lo sugiere.',
    take_profit: 'Nivel de precio por encima del cual sugerís vender para asegurar una ganancia. Lo cargás vos; Kronos no lo sugiere.',
    tendencia_soporte_resistencia: 'Líneas trazadas sobre máximos/mínimos confirmados del precio (Theil-Sen, con al menos 3 toques). Una ruptura se marca como evento informativo — no genera una propuesta de compra/venta porque todavía no se midió si esta señal acierta más que el azar.',
    // ver conversación/auditoría, hallazgo 3: "el cartel 'BAJO' en cada
    // noticia" no tenía ninguna explicación -- umbrales reales de
    // portfolio/nlp_agent.py::classify_risk_level, nunca un número aparte.
    riesgo_evento_noticia: 'Combina 2 cosas: si hay un balance (earnings) próximo, y qué tan extremo es el sentimiento de los titulares recientes. ALTO: balance dentro de 48hs, o sentimiento muy marcado. MEDIO: balance dentro de la semana, o sentimiento notable. BAJO: ninguna de las dos.',
    // ver conversación/auditoría, hallazgo 3: "precio de bonos 3,73 por 1 VN
    // en Transacciones vs. 414,45 por 100 VN en Instrumentos, sin aclarar"
    // -- misma convención de mercado que fetchers.py::_bono_on_divisor ya
    // normaliza puertas adentro, acá solo se explicita para el usuario.
    precio_bono_unidad: 'Los bonos/ON cotizan "cada 100 nominales" en el mercado (ej. $414,45 = lo que cuestan 100 de valor nominal) — en algunas pantallas de la app ese mismo precio se muestra dividido por 100 (por 1 nominal), para que sea comparable con una cantidad real de unidades. Si ves números muy distintos para el mismo bono en 2 lugares, no es un error: es la misma cotización en 2 convenciones distintas (÷100 entre sí).',
};

export function glossaryText(key) {
    return TERMS[key] || '';
}

export function glossaryTerm(key, label) {
    const text = TERMS[key];
    if (!text) return label;
    return `<span class="glossary-term" tabindex="0">${label}<span class="glossary-term-panel">${text}</span></span>`;
}
