# Backtesting de bolsa

El panel permite elegir «Mi perfil», con capital disponible y stops del 2%, o el perfil histórico de presupuesto fijo. En CLI usa `--profile swing`. Las reglas del perfil nuevo y sus límites de simulación diaria están en [Perfil autónomo Paper](autonomous-paper.md). Los detalles de presupuesto fijo descritos más abajo corresponden al perfil `budget`.

En panel local, «Laboratorio histórico» ejecuta las seis estrategias con históricos Alpaca IEX, de forma independiente de la estrategia Paper. No envía órdenes ni cambia configuración de automatización. El trabajo corre en un hilo y cliente de datos propios; solo permite un backtest simultáneo y conserva último informe en memoria hasta reiniciar o comenzar otro. Descarga JSON para conservarlo.

## Uso

1. Abre http://127.0.0.1:8081 y recarga si pestaña era anterior a actualización.
2. Introduce entre 1 y 8 símbolos, fechas terminadas, capital y entrada estimada.
3. Establece comisión y deslizamiento por lado en puntos básicos: 5 pb equivale a 0,05%.
4. Pulsa «Comparar estrategias». Consulta progreso, tabla de protección, curvas y operaciones.
5. Usa selector del gráfico para cambiar estrategia o ver caída desde máximo. Este selector no controla Paper.
6. Descarga informe JSON completo. Tabla de operaciones muestra últimas 100; JSON incluye todas.

Alternativa por terminal, también compatible con Paper funcionando:

```sh
python3 -m equities.backtest_cli --symbols AAPL,MSFT \
  --start 2021-01-01 --end 2025-12-31 \
  --capital 10000 --budget 500 --fee-bps 5 --slippage-bps 5 \
  --output .equities-state/backtests/comparacion.json
```

Claves se leen igual que módulo Paper; cliente histórico solo usa GET. Permisos de datos y disponibilidad IEX dependen de Alpaca. Si falta una sesión del período o dentro del calentamiento disponible, trabajo falla en vez de ocultar huecos. Calendario incluye festivos y cierres anticipados. Máximo 10 años por prueba; se descargan hasta 400 días previos y se exigen al menos 64 sesiones previas al inicio común.

## Ejecución simulada

Se reutilizan reglas de seis estrategias. Cada día usa exclusivamente barras terminadas anteriores, dentro de ventana de 400 días naturales, para calcular señal. Orden se simula en apertura de sesión siguiente con deslizamiento adverso y comisión. Nunca compra al cierre que generó señal.

Sin cortos, margen ni aumento de posiciones existentes. Cantidades enteras sobre precios ajustados por splits. Se reserva entrada estimada usando cierre previo más 5%, como Paper. Un gap de apertura puede hacer que gasto supere entrada estimada; cantidad se reduce si falta efectivo para precio de apertura más costes. Efectivo no puede ser negativo. Compras no usan ingresos de ventas del mismo ciclo. Si varias acciones compiten por efectivo, orden de símbolos es alfabético.

Final no fuerza liquidación: patrimonio incluye posiciones abiertas valoradas a cierre. Coste de liquidarlas no está descontado. «Órdenes» cuenta compras/ventas, «operaciones cerradas» solo ventas realizadas; acierto es indefinido si no hubo ventas. No se modelan retrasos, rechazos, liquidez, ejecuciones parciales o restricciones de cuenta Paper.

## Referencias y exposición

- **Mantener misma asignación:** compra al principio hasta entrada estimada por acción, limitada por reparto equitativo de capital; resto queda en efectivo.
- **Mantener todo capital:** reparte capital inicial a partes iguales entre símbolos, descontando costes y redondeando a unidades enteras.

Mismas fechas, capital, precios y costes. Referencia principal permite comparar una estrategia de asignación pequeña sin enfrentarla únicamente a cartera totalmente invertida. Exposición de cada cartera cambia con precios y decisiones; no se afirma que tengan idéntico riesgo. Referencias no rebalancean.

## Métricas

- **Rentabilidad:** patrimonio final / capital inicial - 1, sobre cartera completa, incluido efectivo.
- **Caída máxima:** peor pérdida desde máximo anterior del patrimonio diario al cierre, incluyendo capital inicial como primer máximo. No mide mínimo intradía.
- **Reducción en puntos porcentuales:** caída de referencia menos caída de estrategia. Positivo indica protección; negativo, empeoramiento.
- **Reducción relativa:** reducción / caída de referencia. Si referencia nunca cayó, se muestra indefinida, no 100%.
- **Recuperación:** máximo de sesiones consecutivas y días naturales bajo máximo; estado al final indica si sigue bajo máximo. Un episodio no recuperado permanece censurado por fin del período.
- **Exposición:** media del valor invertido / patrimonio al cierre. Días invertidos son sesiones con posición al cierre.
- **Captura bajista/alcista diaria:** rentabilidad media de estrategia en días negativos/positivos de referencia totalmente invertida dividida por media de referencia esos días. No es captura mensual geométrica. Captura bajista negativa significa que estrategia ganó en días de caída de referencia; ausencia de esos días deja ratio indefinido.
- **Peor día/mes:** mínimo retorno diario y mensual, incluyendo meses parciales.
- **Volatilidad:** desviación estándar muestral de retornos diarios por raíz de 252. No se supone rentabilidad libre de riesgo ni se calcula Sharpe ficticio.
- **Regímenes:** cada día se clasifica usando retorno de 20 sesiones previas de cesta equiponderada de activos; >2% alcista, <-2% bajista, resto lateral. No usa retornos futuros para etiquetar. Retorno de grupo compone días seleccionados, no representa una inversión continua separada.
- **Mitades/años:** diagnósticos cronológicos con reglas fijas. Segunda mitad continúa cartera existente. No hay entrenamiento/optimización; no se anuncia como validación fuera de muestra.

## Límites de interpretación

Datos ajustados por splits son una representación sintética: cantidades no reconstruyen acciones nominales originales antes/después de eventos corporativos. Dividendos, impuestos e interés de efectivo están excluidos en estrategias y referencias; resultados son de precios, no rentabilidad total. Feed IEX tiene cobertura parcial del mercado. Universo seleccionado hoy puede tener sesgo de supervivencia. Resultados históricos no validan rentabilidad futura ni fidelidad exacta de ejecución Paper.

Referencias: [históricos de Alpaca](https://docs.alpaca.markets/us/docs/about-market-data-api), [calendario](https://docs.alpaca.markets/us/reference/legacycalendar), [evaluación de carteras](https://www.cfainstitute.org/insights/professional-learning/refresher-readings/2026/portfolio-performance-evaluation).
