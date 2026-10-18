# Rendimiento · revisión del 18 de octubre de 2026

Se midieron y optimizaron el panel Alpaca, los cálculos históricos, las lecturas del ciclo Paper y el empaquetado del repositorio. El motor cripto conserva sus optimizaciones existentes; no se ha certificado su rendimiento con datos reales en esta revisión.

## Mediciones

Windows, Python 3.12.10. Backtest sintético de cuatro símbolos y 1.000 barras, seis estrategias por comparación. Mediana de tres ejecuciones por versión, sin perfilador:

| Operación | Antes | Después | Mejora |
| --- | ---: | ---: | ---: |
| Comparación con presupuesto fijo | 3,348 s | 0,923 s | 3,63 veces |
| Comparación con perfil swing | 1,999 s | 0,479 s | 4,17 veces |
| Respuesta del panel desde Alpaca | 1,392 s | 0,516 s | 2,70 veces |

El panel se midió con tres consultas de lectura por versión; la latencia depende de la red y de Alpaca. Las dos comparaciones históricas generan informes JSON completos idénticos antes y después, verificados por SHA-256. Esto comprueba equivalencia en la carga medida, no rentabilidad ni fidelidad de ejecución bursátil.

## Cambios

- Índice ordenado de sesiones y búsqueda binaria en ventanas, sin ordenar y recorrer todo el histórico en cada sesión.
- Validación completa de los datos una vez al preparar el backtest, compartida entre las seis simulaciones. El motor de operación sigue validando las entradas recibidas.
- Ventanas limitadas a los datos que necesita cada regla; MACD conserva todo su calentamiento y la misma aritmética.
- Lecturas del panel en paralelo, con un máximo de cuatro trabajadores. Las peticiones simultáneas comparten únicamente la lectura en curso; el siguiente sondeo obtiene datos nuevos. No se cachean lecturas de riesgo ni órdenes.
- El ciclo Paper reutiliza sus lecturas frescas de reconciliación únicamente cuando no ha realizado escrituras al bróker. No reutiliza datos de ciclos anteriores.
- HTML y scripts cargados una vez por proceso; tablas y catálogo de estrategias se actualizan al cambiar su contenido. Las pestañas ocultas dejan de sondear el estado del panel; el supervisor sigue ejecutándose en el servidor.
- Se excluyen 11.409 archivos de documentación generada (985,5 MiB) del contexto Docker y de las descargas de CI. La comprobación de acciones solo necesita sus carpetas de código y pruebas. La documentación permanece en Git.
- El contexto Docker excluye también claves, sesiones y builds locales.

## Validación y reproducción

169 pruebas de acciones pasan, incluyendo concurrencia, frescura de datos y conservación de los límites del 2%. Se verificaron estilo Python, sintaxis JavaScript y YAML de los workflows. La descarga selectiva se comprobó en una copia aislada: conserva fuentes, pruebas y documentación necesaria. Docker no está instalado en este equipo; no se ha construido una imagen ni ejecutado las matrices remotas de CI en esta revisión.

```sh
python scripts/benchmark_equities.py --sessions 1000 --symbols 4 --repeats 3
python -m unittest discover -s tests_equities
```

Las mediciones usan datos sintéticos y no envían órdenes.
