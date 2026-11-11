#!/bin/sh
set -eu
if [ "$(id -u)" -ne 0 ]; then
    echo "Ejecuta este instalador con sudo y la ruta a tu wheel." >&2
    exit 1
fi
case "$(uname -m)" in aarch64|arm64) ;; *) echo "Se requiere Linux ARM de 64 bits." >&2; exit 1 ;; esac
[ "$#" -eq 1 ] || { echo "Uso: sudo sh install.sh /ruta/freqtrade_equities.whl" >&2; exit 1; }
wheel_path=$(readlink -f -- "$1")
case "$wheel_path" in *.whl) ;; *) echo "Se requiere un archivo .whl" >&2; exit 1 ;; esac
[ -f "$wheel_path" ] || { echo "No existe el wheel." >&2; exit 1; }
python3 -c 'import sys; assert sys.version_info >= (3,11), "Requiere Python >= 3.11"'
command -v systemctl >/dev/null
if systemctl is-active --quiet freqtrade-equities.service; then
    echo "El servicio está activo. Pausa entradas, revisa órdenes y detén el servicio antes de instalar." >&2
    exit 1
fi
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
[ -f "$script_dir/freqtrade-equities.service" ]
if ! id bolsa >/dev/null 2>&1; then
    useradd --system --home-dir /var/lib/freqtrade-equities --shell /usr/sbin/nologin bolsa
fi
install -d -m 0755 /opt/freqtrade-equities
python3 -m venv /opt/freqtrade-equities/venv
/opt/freqtrade-equities/venv/bin/python -m pip install --no-cache-dir --upgrade --force-reinstall "$wheel_path"
install -d -m 0700 -o bolsa -g bolsa /var/lib/freqtrade-equities
install -d -m 0700 -o bolsa -g bolsa /var/lib/freqtrade-equities/.secrets
install -m 0644 "$script_dir/freqtrade-equities.service" /etc/systemd/system/freqtrade-equities.service
systemd-analyze verify /etc/systemd/system/freqtrade-equities.service
systemctl daemon-reload
systemctl enable --now freqtrade-equities.service
echo "Servicio instalado. Arranca con las entradas en pausa."
echo "Guarda tus claves Paper con permiso 0600 y propietario bolsa en /var/lib/freqtrade-equities/.secrets/alpaca.json."
echo "Después: sudo systemctl restart freqtrade-equities.service"
