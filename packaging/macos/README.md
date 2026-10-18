# Freqtrade Bolsa para macOS

Aplicación nativa para Apple Silicon, macOS 12 o posterior. Incluye intérprete Python, motor de acciones Alpaca Paper, panel y backtester. No incluye motor cripto de Freqtrade ni credenciales.

Construcción desde raíz del proyecto, con Python 3.11, PyInstaller 6, certifi, Pillow y herramientas de Xcode instalados:

```sh
python3 packaging/macos/build.py
```

Resultado: `.macos-build/Freqtrade Bolsa.app`, firmado localmente ad hoc. La distribución a otros equipos requiere gestionar firma y notarización según destino.

Datos de usuario: `~/Library/Application Support/Freqtrade Bolsa`. Claves en `.secrets/alpaca.json`, configuración en `settings.json`, protección de órdenes repetidas en `.equities-state/submissions.json`. No borrar este último archivo durante migraciones.

Configuración de arranque:

```json
{"auto": true, "strategy": "sma", "symbols": ["AAPL", "MSFT"], "budget": 500, "port": 8081}
```

Sin configuración, arranca en modo manual. Solo usa Alpaca Paper. Cerrar última ventana o salir detiene motor y automatización; no cancela órdenes ya enviadas a Alpaca. Registro: `~/Library/Logs/Freqtrade Bolsa/backend.log`.

No ejecutar al mismo tiempo el lanzador del repositorio y la aplicación. El puerto ocupado impide el segundo arranque. Copiar las claves al equipo de destino por canal seguro; nunca incluirlas en el paquete.

## Modo de cierre manual

Por petición del usuario, arranque automático y operación por estrategia desactivados. Entradas bloqueadas también en bróker. Panel permite consultar señales y cerrar posiciones largas de acciones de toda cuenta Paper con sesión abierta. El cierre usa liquidación nativa de posición, no órdenes de venta con cantidad libre; símbolos con órdenes pendientes se omiten. Pulsar botón no garantiza ejecución inmediata: revisar respuesta y órdenes Alpaca.
