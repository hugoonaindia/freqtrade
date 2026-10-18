# Bolsa con Alpaca Paper

Este repositorio incorpora `equities`, una aplicación independiente para acciones estadounidenses mediante Alpaca Paper. **No convierte el motor CCXT de Freqtrade ni hace compatibles sus estrategias, FreqAI, backtesting o FreqUI con acciones.** Dispone de su propio panel web local y seis estrategias de ejemplo.

## Arranque

Desde la raíz del proyecto, con Python 3.11 o posterior:

```sh
python3 -m equities
```

Abre http://127.0.0.1:8081. No requiere instalar dependencias de Freqtrade. El panel permite consultar cuenta, posiciones, últimas 100 órdenes, seleccionar estrategia, previsualizar señales con sus motivos y ejecutar un ciclo Paper. No envía órdenes al iniciar. Solo escucha en localhost; usa exactamente esa dirección IP.

Alternativa en macOS: doble clic en `Abrir Bolsa.command`.

## Credenciales

Se leen desde `.secrets/alpaca.json`, excluido de Git, con permisos de propietario:

```json
{"key": "TU_CLAVE_PAPER", "secret": "TU_SECRETO_PAPER"}
```

También admite `APCA_API_KEY_ID` y `APCA_API_SECRET_KEY` en entorno. Nunca uses claves reales: el destino de operaciones está fijado a `https://paper-api.alpaca.markets`. No existe opción para cambiar a dinero real. Claves y respuestas completas de autenticación no se envían al navegador.

## Estrategias disponibles

```sh
python3 -m equities --strategy sma --symbols AAPL,MSFT --budget 500 --port 8081
```

| Opción `--strategy` | Entrada | Salida | Historial mínimo |
| --- | --- | --- | --- |
| `sma` | Media 5 cruza de abajo hacia arriba media 20 | Cruce inverso | 21 sesiones |
| `mean_reversion` | Z-score de 20 cierres ≤ −2 | Cierre alcanza media de 20 | 20 sesiones |
| `bollinger` | Cierre previo bajo banda inferior 20/2 y actual vuelve dentro | Cierre alcanza banda media; salida tiene prioridad ante recuperación que sobrepase media | 21 sesiones |
| `breakout` | Cierre supera máximo de **20 sesiones anteriores**, excluyendo actual | Cierre pierde mínimo de 10 anteriores | 21 sesiones |
| `momentum` | Mayor rentabilidad positiva de 63 sesiones del universo configurado | Vende posiciones que dejan de ser ganadoras; sale de todas si ninguna rentabilidad positiva | 64 sesiones por símbolo |
| `macd` | MACD 12/26 cruza hacia arriba señal EMA9 | Cruce inverso | 35 sesiones |

SMA requiere **cruce nuevo**: estar por encima no basta. Esto sustituye regla inicial de estado de medias. Cruces desde igualdad cuentan. MACD usa EMA inicializada con media simple de primer período. Z-score y bandas usan desviación estándar poblacional; sin dispersión mantienen posición. Ruptura exige máximos/mínimos válidos, no solo cierres.

Momentum compara símbolos configurados, no busca automáticamente todo mercado. Decide en cada nueva sesión diaria; empates se resuelven por símbolo alfabético. Exige mismas 64 fechas de sesión, sin duplicados y en orden, para todos símbolos. Si falta historial válido o fechas no coinciden, no rota. Órdenes abiertas de cada símbolo siguen bloqueando sus operaciones. Una venta enviada no aporta efectivo a compras del mismo ciclo: puede requerir otro ciclo cuando Alpaca refleje ejecución.

Selector del panel cambia estrategia de sesión **sin enviar órdenes** y limpia análisis anterior. Vista previa y ejecución incluyen estrategia y revisión de selección; motor las comprueba bajo mismo bloqueo que ciclo. Si otra pestaña cambia selección, petición antigua se rechaza con HTTP 409, incluso si después vuelve a estrategia original. Peticiones sin selección reciben HTTP 400. Se requiere recargar pestañas anteriores a esta actualización. No combina seis estrategias ni crea seis carteras. Selección no se guarda: reiniciar recupera `--strategy` o `sma` por defecto. Con `--auto` selector queda bloqueado. Estrategia seleccionada puede vender posiciones preexistentes de símbolos configurados: usa cuenta Paper dedicada.

Símbolos predeterminados son ejemplos técnicos, no recomendaciones. Solo posiciones largas, sin incremento de posición existente, compras con efectivo y acciones enteras. Una venta puede dejar resto fraccionario. Todas estrategias son ejemplos sin rentabilidad validada, stop-loss, límite global de pérdidas ni evaluación fiscal. Backtester disponible en laboratorio del panel y CLI; consulta [Backtesting de bolsa](backtesting-equities.md).

Reloj de Alpaca debe indicar sesión regular abierta al ejecutar y antes de enviar cada orden. Calendario oficial determina última sesión completada, incluidos festivos y cierres anticipados. Cada símbolo debe tener exactamente cierre de esa sesión; si falta, no opera. Con mercado abierto excluye día en curso; vista previa tras cierre puede usar sesión recién terminada cuando sus datos ya estén disponibles. Si calendario falla, no opera. Consulta últimos 400 días naturales con barras ajustadas IEX; rechaza respuestas paginadas.

Presupuesto es estimación por nueva posición, basada en cierre anterior y margen del 5%; órdenes de mercado pueden ejecutarse a otro precio. Compras se limitan al menor entre efectivo libre de reservas, poder de compra y poder de compra sin margen. Descuenta parte pendiente de compras limitadas y reserva importe completo de órdenes por importe nominal. Si una compra pendiente tiene coste desconocido o faltan datos monetarios, bloquea nuevas compras; ventas de posiciones largas siguen permitidas. Nunca suma poder de compra con margen al efectivo. Feed IEX no representa todas operaciones de todas bolsas; permisos dependen de cuenta Alpaca.

## Automatización opcional

```sh
python3 -m equities --auto --symbols AAPL,MSFT --budget 500
```

Evalúa cada 60 segundos mientras proceso sigue abierto, con estrategia elegida mediante `--strategy`. No inicia con sistema. Cualquier error detiene trabajador automático. API y panel publican estado detenido y aviso incluso si cuenta no responde; panel actualiza estado cada 15 segundos. Revisa órdenes en Alpaca antes de reiniciar. Sin `--auto`, solo opera al pulsar botón de ejecución del panel.

Órdenes abiertas bloquean nuevas órdenes del mismo símbolo. Identificador determinista por símbolo, sesión y dirección, compartido entre estrategias para evitar duplicados al cambiar selección, permite rechazar duplicados en Alpaca. Intenciones se guardan antes del envío en `.equities-state/submissions.json`; un timeout no se reintenta automáticamente. No borres ese archivo para reintentar sin reconciliar con Alpaca. Un bloqueo de proceso impide dos instancias en esta carpeta; no compartas cuenta con otras copias ejecutándose.

## Verificación

```sh
python3 -m unittest discover -s tests_equities
```

Pruebas aisladas cubren seis reglas, calentamiento de indicadores, clasificación de momentum, cambio de estrategia, selector HTTP y límites de efectivo, sesión cerrada, rechazo de cripto, posiciones, señales, persistencia, errores de red, endpoint Paper y protecciones HTTP. Comprobación real de cuenta y datos es independiente: pruebas unitarias no prueban ejecución ni aceptación de órdenes del bróker.

Referencias oficiales: [Paper Trading](https://docs.alpaca.markets/us/docs/paper-trading), [órdenes](https://docs.alpaca.markets/us/docs/working-with-orders), [datos](https://docs.alpaca.markets/us/docs/about-market-data-api).

## Backtesting

Disponible en «Laboratorio histórico» del panel, independiente de Paper. Compara seis estrategias y reducción de pérdidas. Consulta [reglas, métricas y límites](backtesting-equities.md).
