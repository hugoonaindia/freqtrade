# Raspberry Pi · despliegue preparado

Objetivo: Raspberry Pi OS de 64 bits con Python 3.11 o posterior. No requiere entorno gráfico ni librerías de cálculo científico. La única dependencia obligatoria es tzdata. La compatibilidad ARM64 se incluye en CI; la validación física sigue pendiente hasta disponer de tu placa y su sistema operativo.

## Instalar

En el ordenador de desarrollo, crea el wheel:

```sh
python -m pip wheel --no-deps . --wheel-dir dist
```

Copia el wheel y esta carpeta a la Raspberry. En la Raspberry:

```sh
sudo apt update
sudo apt install python3-venv ca-certificates
sudo sh install.sh /ruta/al/freqtrade_equities-1.0.0-py3-none-any.whl
```

El instalador verifica ARM64 y Python, crea el usuario de servicio `bolsa`, instala un entorno virtual en `/opt/freqtrade-equities/venv` y verifica/activa la unidad systemd. No copia claves ni habilita entradas. No uses el instalador para actualizar una campaña que esté operando: pausa y revisa antes las órdenes existentes; la reinstalación debe realizarse con el servicio detenido.

Guarda tus credenciales Paper en `/var/lib/freqtrade-equities/.secrets/alpaca.json`, con propietario `bolsa:bolsa` y permiso 0600. No incluyas las claves en comandos, logs, Git ni en el wheel. Después reinicia el servicio. El estado, campañas, SQLite y configuración se guardan en `/var/lib/freqtrade-equities`, separado de la instalación.

## Acceder y verificar

El servidor escucha solo en loopback. Desde tu ordenador abre un túnel SSH:

```sh
ssh -N -L 8081:127.0.0.1:8081 TU_USUARIO@raspberrypi.local
```

Abre `http://127.0.0.1:8081` en tu ordenador. No expongas el puerto a Internet. Si el puerto 8081 de tu ordenador está ocupado por otra instancia, ciérrala antes de abrir el túnel. Conserva el mismo puerto local para los controles de origen del panel.

En la Raspberry:

```sh
systemctl status freqtrade-equities.service
journalctl -u freqtrade-equities.service -n 50 --no-pager
curl --fail http://127.0.0.1:8081/api/health
```

Comprueba que el reloj esté sincronizado antes de activar una campaña. El servicio espera red y sincronización, pero estos targets no garantizan por sí solos que el reloj sea correcto; revisa `timedatectl status` y la conectividad real con Alpaca.

## Recursos y fallos

Los backtests del servidor usan un proceso independiente creado con spawn. En Linux el worker tiene un máximo de 384 MiB de espacio virtual, 600 segundos de CPU y 30 minutos de tiempo de trabajo; su prioridad baja con nice 10. No es una reserva de RAM ni una medición del consumo real. Configura `EQUITIES_BACKTEST_MEMORY_MB` entre 128 y 2048 si tu placa necesita otro límite. El informe transferido al panel se limita a 16 MiB.

La unidad limita el conjunto del servicio a 768 MiB de memoria con aviso/reclamación desde 640 MiB y a 64 tareas. Los límites deben ajustarse tras medir en tu placa. El worker puede abortar pruebas grandes con un error claro sin ejecutar órdenes. El kernel y systemd pueden detener todo el servicio si este alcanza su límite conjunto: el aislamiento no garantiza supervivencia ante agotamiento global del equipo. El reinicio conserva la campaña y vuelve a pausar entradas; revisa posiciones antes de reanudar.

Antes de iniciar investigación se exigen 512 MiB libres; cada archivo de datos comprimido mantiene un margen de 256 MiB y espacio para la escritura SQLite/WAL. Esto es una comprobación preventiva, no una cuota ni una garantía frente a otros procesos que llenen el disco. Revisa periódicamente `df -h /var/lib/freqtrade-equities` y conserva copias del estado.

La investigación escribe en `research.sqlite`, separada de `analytics.sqlite` del supervisor, para no mantener ocupado el registro operativo mientras archiva históricos. Las cotizaciones se validan y muestrean por página; no se conserva en memoria todo el flujo bruto de una sesión. Se limita el total leído y no se fabrican datos para completar un trabajo. Solo hay un backtest a la vez. Si la Pi debe dedicarse exclusivamente a operar, puedes ejecutar la investigación en otro equipo y conservar el mismo formato de informe; los ensayos anuales intradía pueden seguir siendo costosos.

El estado de campaña, la configuración y las reservas de órdenes se escriben mediante un archivo temporal privado, fsync y reemplazo atómico; en Linux también se sincroniza el directorio. Esto reduce el riesgo de estado incompleto ante cortes eléctricos, sin garantizar la integridad de una tarjeta o alimentación defectuosas. Una reserva se persiste antes de enviar la orden.

Se evitan escrituras de snapshots idénticos de cuenta, posiciones y stops durante cinco minutos; los cambios se registran inmediatamente y las observaciones sin cambios mantienen ese intervalo de heartbeat. Los fills y transiciones de órdenes no se omiten por este ahorro. El panel admite hasta 16 conexiones simultáneas, con tiempo de espera de socket de 10 segundos.

## Parada, actualización y copia

Para mantenimiento, pausa entradas y verifica las órdenes pendientes antes de detener el servicio. Cerrar la app detiene la parada global y la cancelación por plazo; los stops ya enviados siguen en Alpaca. Una entrada GTC pendiente no caduca sola porque la aplicación se haya apagado.

```sh
sudo systemctl stop freqtrade-equities.service
```

Con el servicio detenido, realiza una copia consistente de `.equities-state` y `settings.json`. No copies una SQLite en uso mediante una simple copia del archivo: usa la API de backup de SQLite o detén primero el servicio. Las claves requieren su propia copia privada. Conserva versión del wheel y configuración para poder restaurar; nunca borres una campaña para eludir su límite de pérdida. Instala el wheel nuevo con el servicio detenido y luego arranca:

```sh
sudo systemctl start freqtrade-equities.service
```

Verifica health, estado de campaña, posiciones y órdenes antes de GO PAPER. No se instala un watchdog que reinicie automáticamente ante un error de estrategia: las entradas deben permanecer bloqueadas para revisión.

Referencias: [Raspberry Pi OS y entornos virtuales](https://www.raspberrypi.com/documentation/usage/terminal/), [aislamiento y directorios de systemd](https://www.freedesktop.org/software/systemd/man/257/systemd.exec.html).
