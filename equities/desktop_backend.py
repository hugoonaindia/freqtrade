"""Bundled backend entry point. Native launcher owns lifecycle and window."""

import json
import sys
from pathlib import Path

from .__main__ import main
from .paths import runtime_root
from .strategies import STRATEGIES


def launch_arguments(settings_path):
    settings = json.loads(settings_path.read_text()) if settings_path.exists() else {}
    if not isinstance(settings, dict) or type(settings.get("auto", False)) is not bool:
        raise ValueError("Configuración de arranque no válida.")
    strategy = settings.get("strategy", "sma")
    symbols = settings.get("symbols", ["AAPL", "MSFT"])
    port = settings.get("port", 8081)
    if (
        strategy not in STRATEGIES
        or not isinstance(symbols, list)
        or not all(isinstance(symbol, str) for symbol in symbols)
        or type(port) is not int
        or not 1 <= port <= 65535
    ):
        raise ValueError("Configuración de bolsa no válida.")
    args = [
        "--strategy",
        strategy,
        "--symbols",
        ",".join(symbols),
        "--budget",
        str(settings.get("budget", 500)),
        "--port",
        str(port),
    ]
    if settings.get("auto", False):
        args.append("--auto")
    return args


def backend_main():
    root = runtime_root()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    logs = Path.home() / "Library/Logs/Freqtrade Bolsa"
    logs.mkdir(mode=0o700, parents=True, exist_ok=True)
    output = (logs / "backend.log").open("a", buffering=1)
    sys.stdout = sys.stderr = output
    main(sys.argv[1:] or launch_arguments(root / "settings.json"))


if __name__ == "__main__":
    backend_main()
