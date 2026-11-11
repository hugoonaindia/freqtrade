# Bolsa · Alpaca Paper

Aplicación independiente de acciones estadounidenses: panel local, estrategias diarias, backtest y motor propio `SwingEngine`. Solo utiliza Alpaca Paper. El motor cripto, CCXT y FreqAI no forman parte de esta aplicación.

## Arrancar en Windows, macOS o Linux

Necesitas Python 3.11 o posterior. Desde esta carpeta:

```sh
python -m pip install -r requirements.txt
python -m equities
```

Abre http://127.0.0.1:8081. En macOS también puedes usar `Abrir Bolsa.command`. Opcionalmente instala la app con `python -m pip install .` y arranca con `bolsa-paper`.

## Conexión y operación

Las claves de Alpaca Paper se guardan en `.secrets/alpaca.json` o en `APCA_API_KEY_ID` y `APCA_API_SECRET_KEY`. Las claves, configuración local e historial de campaña están excluidos de Git y de Docker.

La app arranca en pausa. GO PAPER activa una campaña con hasta el 100% del capital disponible según señales, stop individual del 2% y parada global al perder un 2% desde el patrimonio de activación. La primera campaña requiere cuenta sin posiciones ni órdenes. Las reglas de ejemplo necesitan evaluación antes de usarse como sistema propio.

Los stops no garantizan una pérdida máxima del 2%. La supervisión global necesita el proceso abierto y conexión; los stops GTC permanecen en Alpaca. Reiniciar conserva los límites y vuelve a pausar las entradas.

## Documentación

- [Arquitectura modular](docs/arquitectura-modular.md)
- [Uso y conexión](docs/alpaca.md)
- [Perfil autónomo y límites](docs/autonomous-paper.md)
- [Evaluación histórica](docs/backtesting-equities.md)
- [Mediciones de rendimiento](docs/performance.md)
- [Instalación y límites para Raspberry Pi](packaging/raspberrypi/README.md)
- [Empaquetado macOS](packaging/macos/README.md)

## Pruebas y rendimiento

```sh
python -m unittest discover -s tests_equities
python scripts/benchmark_equities.py
```

Las pruebas y el benchmark usan datos simulados y no envían órdenes.

## Docker en Linux

El servidor escucha únicamente en localhost. Para usar este Dockerfile en Linux se requiere `--network host`; no expongas el servicio fuera del equipo. Persiste `/data`, con permisos para UID 10001, para conservar claves, ajustes y campaña. En Windows se recomienda el inicio nativo descrito arriba.

## Estructura

`equities/`: módulos de la aplicación y panel. `tests_equities/`: pruebas. `docs/`: guías. `packaging/`: despliegue Raspberry Pi y macOS. `scripts/`: benchmark.

## Licencia e historial

Se conserva la licencia GPL original. Los componentes retirados son recuperables en el historial Git y en la referencia local `backup/antes-limpieza-2026-10-18`.
