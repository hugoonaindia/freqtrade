# Monolito modular

Una instalación, un paquete Python y un servicio. El punto de entrada es
`equities.__main__`; compone el adaptador Alpaca, el motor, los controles y el
servidor. El panel se sirve desde ese mismo paquete. No se requieren servicios
de red internos ni una base de datos externa.

## Responsabilidades

| Módulos | Responsabilidad |
| --- | --- |
| `strategies`, `guards`, `history_validation`, `temporal_policy` | Reglas, validación y calendario; sin acceso directo al broker ni a SQLite |
| `engine`, `swing` | Orquestación de señales, campaña, riesgo, protección y reconciliación |
| `broker`, `dashboard_reader` | Acceso a Alpaca y lecturas del panel |
| `backtest`, `swing_backtest`, `execution_replay`, `out_of_sample`, `backtest_data`, `backtest_metrics` | Investigación histórica y evaluación |
| `backtest_jobs`, `backtest_process` | Ciclo de vida y aislamiento del trabajo histórico |
| `analytics`, `execution_quality`, `durable_state`, `paths`, `process_lock` | Auditoría, persistencia y recursos locales |
| `controls`, `automation`, `server` | Acciones del operador, automatización y API local |
| `dashboard.html`, `dashboard_ui.js`, `backtest_ui.js` | Presentación |

## Límites

Las reglas reciben datos y producen decisiones. El motor aplica las restricciones
y utiliza el adaptador; las estrategias no envían órdenes. La interfaz solicita
acciones a los controles, sin guardar credenciales ni decidir el riesgo.

La investigación no recibe la instancia del motor operativo. En el servicio normal
usa un proceso hijo limitado y una base `research.sqlite`, separada de
`analytics.sqlite`. Sigue siendo parte de la misma aplicación y despliegue.
El empaquetado congelado y las pruebas con adaptadores personalizados pueden usar
el modo de hilo; no ofrecen ese mismo aislamiento de memoria.

`test_architecture.py` comprueba que los módulos de reglas y métricas no importen
la capa de aplicación, HTTP, SQLite ni procesos externos. Es una barrera de
dependencias, no una certificación de toda la arquitectura.

Se mantienen los archivos actuales para evitar cambios de importación sin beneficio
funcional. `swing` todavía concentra riesgo y reconciliación: una futura extracción
de esos componentes debe conservar las invariantes de campaña, reserva de órdenes
y recuperación, con sus pruebas. No es necesario convertirlos en microservicios.

## Validación y alcance

Las pruebas automatizadas cubren errores de datos, límites temporales, órdenes
inciertas, ejecuciones parciales, riesgo, persistencia y caída del proceso de
investigación. El benchmark usa datos sintéticos y comprueba resultados repetibles.

Una prueba local corta de lecturas concurrentes comprueba disponibilidad del panel
e integridad SQLite sin activar una campaña. No sustituye la prueba de varios días
en ARM, con el almacenamiento y alimentación definitivos, ni la comprobación de
órdenes Paper en sesión de mercado. Los resultados de Windows no acreditan los
límites de memoria de Linux ni la recuperación tras un corte eléctrico físico.
