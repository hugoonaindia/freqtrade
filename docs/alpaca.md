# Bolsa con Alpaca Paper

`equities` es una aplicación independiente para acciones estadounidenses, con panel web, seis estrategias base y laboratorio histórico. No convierte el motor CCXT ni FreqAI en sistemas de acciones.

## Arranque

Desde la raíz del proyecto, con Python 3.11 o posterior:

```sh
python -m equities
```

En Windows instala primero `python -m pip install tzdata`. Abre `http://127.0.0.1:8081`. No necesita instalar las dependencias del motor cripto. En macOS también puedes usar `Abrir Bolsa.command`.

## Conexión

Las claves se leen de `.secrets/alpaca.json`, excluido de Git:

```json
{"key": "TU_CLAVE_PAPER", "secret": "TU_SECRETO_PAPER"}
```

También se admiten `APCA_API_KEY_ID` y `APCA_API_SECRET_KEY`. El destino de operaciones está fijado a Alpaca Paper. Las claves se cargan al iniciar; reinicia tras cambiarlas. No se envían al navegador.

## Tu perfil autónomo

Consulta [Perfil autónomo Paper de varios días](autonomous-paper.md) para las reglas completas:

- Hasta el 100% del capital disponible, sin apalancamiento. Reparto entre símbolos; momentum asigna al líder. Redondeo a acciones enteras y margen operativo del 1%.
- Stop individual del 2% mediante orden OTO con protección GTC adjunta.
- Parada global al perder un 2% desde el patrimonio de activación. Referencia y bloqueo se conservan entre reinicios.
- Señales cada 60 segundos y supervisor de riesgo cada 30 segundos mientras el proceso siga abierto.
- Reinicio en pausa. GO PAPER registra o reanuda la campaña; `--auto` se conserva por compatibilidad y no habilita entradas al arrancar.

La primera campaña exige cuenta Paper sin posiciones ni órdenes. Parar pausa entradas y solicita cancelar compras pendientes en el siguiente ciclo de supervisión; mantiene el supervisor y los stops. El botón de cierre solicita cancelar órdenes y liquidar, confirmando antes las cancelaciones. El panel muestra umbrales, estado y diario.

La selección y los símbolos se guardan. No se permite cambiar el universo con posiciones u órdenes abiertas. Cotizaciones de entrada antiguas, spreads superiores al 1%, falta de protección o respuestas ambiguas bloquean nuevas entradas. No borres los archivos de `.equities-state` para reintentar una orden.

## Estrategias base

SMA 5/20 y MACD 12/26/9 operan cruces nuevos; reversión a la media usa z-score de 20 cierres; Bollinger exige recuperar la banda inferior; ruptura compara máximos de 20 sesiones y mínimos de 10; momentum elige el líder positivo de 63 sesiones, con fechas alineadas y desempate alfabético. Son ejemplos sin rentabilidad validada. Elige y evalúa tus reglas antes de activar.

El laboratorio permite comparar las seis estrategias en el perfil swing o con presupuesto fijo. Consulta [Backtesting de bolsa](backtesting-equities.md). El presupuesto fijo no limita el capital del perfil autónomo.

## Límites

El 2% es un umbral, no una pérdida máxima garantizada. Un stop puede ejecutarse peor por gaps, falta de liquidez, suspensiones o retrasos. No protege fuera de sesión regular. La supervisión global necesita equipo despierto, red y proceso abierto; los stops GTC permanecen en Alpaca al cerrar la app. Usa cuenta Paper dedicada y evita movimientos externos durante una campaña.

Las intenciones de entrada se guardan antes del envío y no se reintentan tras respuestas ambiguas. El cierre de posiciones pendiente de confirmación requiere revisión antes de repetirlo. Un bloqueo de proceso evita dos instancias en esta carpeta.

## Pruebas

```sh
python -m unittest discover -s tests_equities
```

Las pruebas usan un bróker simulado y no envían órdenes a Alpaca.
