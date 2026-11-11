# Backtesting de bolsa

El panel permite elegir «Mi perfil», con capital disponible y stops del 2%, o el perfil histórico de presupuesto fijo. En CLI usa `--profile swing`. Las reglas del perfil nuevo y sus límites de simulación diaria están en [Perfil autónomo Paper](autonomous-paper.md). Los detalles de presupuesto fijo descritos más abajo corresponden al perfil `budget`.

En panel local, «Laboratorio histórico» ejecuta las seis estrategias con históricos Alpaca IEX, de forma independiente de la estrategia Paper. No envía órdenes ni cambia configuración de automatización. En la ejecución normal del servidor, el trabajo corre en un proceso y cliente de datos propios; solo permite un backtest simultáneo. El último informe queda disponible en el panel hasta reiniciar o comenzar otro; los datos y resultados se archivan además en el registro local. Descarga JSON para consultarlo fuera de la app.

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

## Calidad de la simulación y trazabilidad

El backtest diario es exploratorio y no equivale a la ejecución en Alpaca. Resuelve primero gaps y ventas de apertura; después aproxima entradas y stops intradía. Una caída posterior no puede cambiar una venta ya ejecutada a la apertura. Las operaciones incluyen la fase y el motivo de salida; el resultado swing cuenta los eventos con recorrido intradía desconocido.

Las velas diarias no demuestran un fill dentro de los 120 segundos que permite Paper. No se han incorporado cotizaciones históricas ni simulación por minutos. El stop simulado usa el fill estimado; el OTO inicial de Paper usa el límite de compra y puede quedar más cerca del fill real. El supervisor registra ambas distancias sin cancelar una protección vigente para ampliarla.

La aplicación conserva en `.equities-state/analytics.sqlite` las campañas, decisiones, cotizaciones usadas, snapshots de cuenta y posiciones, intenciones de órdenes y cambios de estado reconciliados con Alpaca. El panel conserva una vista corta; la base no elimina eventos al superar 100. La reconciliación periódica conserva los estados observados, no todos los eventos intermedios de la bolsa. No reconstruye operaciones anteriores a esta versión.

Los backtests del panel archivan datos de entrada e informe comprimidos en `research.sqlite` al usar el proceso aislado, configuración, identificador y huellas SHA-256 de datos y código. La descarga JSON incluye esa procedencia. `GET /api/analytics` permite consultar recuentos y los últimos eventos de la campaña actual. La base se guarda localmente y queda excluida de Git. No se guardan claves ni cabeceras de autenticación.

Las dos mitades del período siguen siendo un diagnóstico; no son una evaluación fuera de muestra. Quedan pendientes validación temporal independiente, ejecución intradía y tratamiento de dividendos. Estos cambios no demuestran rentabilidad ni garantizan una pérdida máxima del 2% ante gaps o deslizamiento.

## Validación común del histórico

El motor de señales y el backtest usan `history_validation.validate_daily`: fecha con zona horaria, una barra por sesión y OHLC completos, positivos, finitos y coherentes. En la descarga, ambos contrastan todas las sesiones desde la primera barra disponible hasta el final solicitado con el calendario de Alpaca. Rechazan huecos internos, ausencia de la última sesión y fechas fuera del calendario; no rellenan precios ni descartan duplicados. Cada estrategia mantiene su mínimo de calentamiento. Un histórico inválido bloquea el cálculo y la operación de ese ciclo; la supervisión de riesgo sigue siendo independiente de los históricos de señales.

Esto unifica las comprobaciones estructurales, pero no certifica la exactitud del proveedor. Continúan pendientes la espera de consolidación tras el cierre, los reintentos de consultas, la selección de fuente y la contabilidad completa de dividendos y splits.

## Fuera de muestra en el panel

Selecciona «Tipo de evaluación → Fuera de muestra · reserva temporal», indica la fecha inicial del tramo reservado y fija la estrategia antes de ejecutar. El tramo de desarrollo termina el día anterior; el reservado empieza en la primera sesión bursátil disponible desde la fecha elegida. Se requieren al menos dos sesiones de evaluación en cada tramo y calentamiento suficiente. Ambos parten del mismo capital y de una cartera vacía; no se trasladan posiciones, stops ni pérdidas de campaña entre tramos.

Las reglas, símbolos y costes son los mismos. Los indicadores pueden usar sesiones anteriores para calentarse; las señales nunca usan sesiones futuras. No se optimiza ni se escoge la estrategia por su resultado reservado. Se muestran métricas separadas; curvas y tablas detalladas corresponden al tramo fuera de muestra para la estrategia elegida. El JSON contiene también el desarrollo completo y el registro guarda la especificación y los datos utilizados.

Esta es una reserva temporal, no walk-forward ni una prueba estadística. El programa no puede saber si ya has visto los datos o resultados de esa reserva. Cambiar las reglas y repetir sobre el mismo tramo lo contamina; una evaluación final requiere otro período intacto. La simulación con velas diarias conserva sus limitaciones de ejecución.

## Reserva anual obligatoria

El backtest de desarrollo ya no permite utilizar el último año disponible para ajustar reglas. El servidor calcula un límite móvil según la fecha de Nueva York y reserva un año natural más siete días de margen para fines de semana y festivos. El panel muestra la fecha máxima de desarrollo y propone la reserva correspondiente; la ejecución por terminal aplica el mismo límite.

El modo fuera de muestra exige al menos un año natural entre las fechas solicitadas y comprueba además que las sesiones efectivamente evaluadas abarquen un año. Si no se cumple, pide adelantar la reserva; nunca reduce la duración silenciosamente. El tramo de desarrollo tampoco puede entrar en la reserva anual reciente. Las fechas y límites quedan guardados en el informe. Una ejecución anterior no se convierte retrospectivamente en una reserva intacta: si ya se han visto sus resultados, hay que tratarlo como datos conocidos.

## Ejecución intradía y reconciliación

La opción «Minutos + cotizaciones · escenario» del panel prueba una estrategia fijada antes de ejecutar. Puede usarse en desarrollo y en la reserva anual. Descarga precios nominales sin ajustar, cotizaciones de los primeros cuatro minutos de las sesiones necesarias y barras de un minuto de las sesiones con entradas o posiciones. Cachea las lecturas por símbolo/sesión dentro del trabajo. Los datos usados se archivan con el informe. Se rechazan splits dentro del período, minutos ausentes, paginación incompleta y falta de cotizaciones iniciales válidas; nunca se cambia silenciosamente al modelo diario.

Las cotizaciones con book retirado, tamaño cero o mercado cruzado no se consideran ejecutables y su recuento queda registrado. Se conserva la primera cotización válida de cada intervalo de 100 ms, con su instante original: esto limita memoria, pero puede omitir oportunidades breves. El trabajo limita las lecturas a cinco millones de registros; la frecuencia se limita para respetar el servicio. Reintenta consultas transitorias hasta tres veces; no reintenta envíos ni cancelaciones.

Las señales proceden de sesiones anteriores. Se modelan latencia configurable, límite de compra, caducidad estricta a 120 segundos y costes. El precio de compra nunca supera el límite. La capacidad se estima con una fracción configurable del ask visible (en lotes de 100 acciones) y del volumen del minuto; es una hipótesis retrospectiva de liquidez, no prioridad real en cola. Solo se estima un fill por entrada; si es parcial, se supone cancelación del resto y protección tras 30 segundos. Las carreras de fill/cancelación reales pueden producir otros resultados. Los stops se activan con mínimos del minuto y los recorridos ambiguos se cuentan, sin pretender conocer los ticks. El stop OTO inicial se calcula desde el límite, igual que Paper. La distancia desde el fill puede ser menor del 2%.

Las salidas de estrategia usan bid histórico. Los stops y cierres globales aplican un spread supuesto configurable cuando no hay bid simultáneo, además del deslizamiento. La parada global se comprueba en apertura y cierre de cada minuto; no reproduce exactamente el supervisor de 30 segundos. Las curvas y métricas principales son diarias; se añade caída intradía muestreada. Tampoco se modela la liquidez de las salidas ni su ejecución parcial: la salida completa sigue siendo una hipótesis y puede subestimar el impacto de posiciones grandes.

En Paper se vuelve a comprobar el reloj después de obtener la cotización y su edad antes de enviar. La caducidad se calcula desde `submitted_at` del broker, con `created_at` como alternativa, y se solicita cancelación al cumplir 120 segundos; su confirmación depende del broker y de la supervisión periódica. Cerrar la aplicación no cancela por sí mismo una entrada GTC. No se promete caducidad estricta broker-side. Los fills posteriores al plazo quedan señalados. No se sustituye ni amplía una protección OTO vigente: Alpaca documenta limitaciones para reemplazar estas órdenes.

En Diario se muestran contexto y calidad de cada orden: hora de cotización, envío local, timestamps del broker, demora, spread, deslizamiento con signo y distancia del stop inicial desde el fill. Los fills individuales se reconcilian desde `/v2/account/activities/FILL`, paginados y deduplicados desde la activación de la campaña, incluidos parciales. Se vuelve a leer el período de campaña para recoger correcciones tardías; si supera 2.000 actividades por pasada se bloquean entradas y se requiere revisión. Las tablas muestran los últimos 100; la base conserva todos. Una hora de observación no se presenta como hora de ejecución. Si falta una referencia, la métrica queda vacía; las salidas sin cotización nueva no se comparan con la cotización antigua de entrada.

Los escenarios intradía no reconstruyen el libro de órdenes ni garantizan reproducir fills. Para contrastarlos con la realidad hay que mantener Paper y comparar su registro; esta actualización no activa campañas ni envía operaciones de prueba.

### Fuente histórica configurable

El panel ofrece SIP consolidado (predeterminado) e IEX; la fuente elegida se usa tanto para señales históricas como para cotizaciones y ejecución del backtest. Se ha comprobado acceso a barras y cotizaciones SIP antiguas con esta cuenta, sin cambiar la suscripción. Paper sigue con IEX en tiempo real; sus señales y referencias pueden diferir del backtest SIP. No se presenta una comparación entre fuentes distintas como equivalencia exacta.

Las consultas SIP excluyen los últimos 16 minutos y se exige esperar 16 minutos tras el cierre para usar la sesión completa actual. El final solicitado en fines de semana se limita a la última sesión terminada en la descarga. Esto evita consultar accidentalmente el tramo reciente restringido de SIP. Referencia: [Market Data FAQ de Alpaca](https://docs.alpaca.markets/us/docs/market-data-faq). Para fills individuales y estados: [Account Activities](https://docs.alpaca.markets/us/v1.4.2/docs/account-activities) y [Orders at Alpaca](https://docs.alpaca.markets/us/docs/orders-at-alpaca).
