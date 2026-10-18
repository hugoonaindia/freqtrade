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

El motor usa el perfil autónomo de varios días con stops individuales del 2% y límite global del 2% desde activación. Reinicia con entradas en pausa y conserva el capital de referencia; GO PAPER habilita las entradas protegidas. La supervisión global requiere el proceso abierto y conexión. Los stops GTC permanecen alojados en Alpaca. Consulta [Perfil autónomo Paper](../../docs/autonomous-paper.md).
