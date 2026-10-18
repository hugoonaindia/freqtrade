"""Run with python3 -m equities. No Freqtrade dependencies required."""

import argparse
import fcntl
import signal
import threading

from .broker import Alpaca
from .controls import Controls
from .engine import Engine
from .paths import credentials_path, runtime_root
from .server import make_server
from .strategies import STRATEGIES


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Alpaca Paper: acciones, panel local y seis estrategias de ejemplo."
    )
    parser.add_argument("--symbols", default="AAPL,MSFT", help="Símbolos separados por comas")
    parser.add_argument(
        "--budget", type=float, default=500, help="USD estimados por nueva posición"
    )
    parser.add_argument("--port", type=int, default=8081)
    parser.add_argument(
        "--strategy", choices=STRATEGIES, default="sma", help="Estrategia diaria seleccionada"
    )
    parser.add_argument(
        "--auto", action="store_true", help="Ejecuta estrategia Paper cada 60 segundos"
    )
    args = parser.parse_args(argv)
    state_dir = runtime_root() / ".equities-state"
    state_dir.mkdir(mode=0o700, exist_ok=True)
    lock = (state_dir / "process.lock").open("w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        parser.error("Ya hay un proceso de bolsa activo para este proyecto.")
    broker = Alpaca(credentials_path())
    try:
        engine = Engine(
            broker,
            [s.strip().upper() for s in args.symbols.split(",")],
            args.budget,
            state_dir / "submissions.json",
            strategy=args.strategy,
        )
    except ValueError as exc:
        parser.error(str(exc))
    engine.entries_enabled = False
    stop = threading.Event()

    controls = Controls(engine, settings_path=runtime_root() / "settings.json", port=args.port)
    server = make_server(broker, engine, args.port, controls=controls)

    def terminate(signum, frame):
        stop.set()
        controls.event.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, terminate)
    controls.stop()  # User requested entry-free startup; never resume automated orders.
    print(
        f"Alpaca Paper: http://127.0.0.1:{server.server_port} | auto=False | entradas bloqueadas",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()
        controls.close()
        lock.close()


if __name__ == "__main__":
    main()
