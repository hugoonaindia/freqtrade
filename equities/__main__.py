"""Run with python3 -m equities. No Freqtrade dependencies required."""

import argparse
import json
import signal
import threading

from .broker import Alpaca
from .controls import Controls
from .paths import credentials_path, runtime_root
from .process_lock import acquire_process_lock
from .server import make_server
from .strategies import STRATEGIES
from .swing import SwingEngine


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
        "--auto",
        action="store_true",
        help="Compatibilidad: las entradas siempre se activan desde GO PAPER",
    )
    settings_path = runtime_root() / "settings.json"
    if settings_path.exists():
        saved = json.loads(settings_path.read_text(encoding="utf-8"))
        parser.set_defaults(
            symbols=",".join(saved.get("symbols", ["AAPL", "MSFT"])),
            strategy=saved.get("strategy", "sma"),
            budget=saved.get("budget", 500),
            port=saved.get("port", 8081),
        )
    args = parser.parse_args(argv)
    state_dir = runtime_root() / ".equities-state"
    state_dir.mkdir(mode=0o700, exist_ok=True)
    try:
        lock = acquire_process_lock(state_dir / "process.lock")
    except BlockingIOError:
        parser.error("Ya hay un proceso de bolsa activo para este proyecto.")
    broker = Alpaca(credentials_path())
    try:
        engine = SwingEngine(
            broker,
            [s.strip().upper() for s in args.symbols.split(",")],
            args.budget,
            state_dir / "submissions.json",
            strategy=args.strategy,
            campaign_path=state_dir / "campaign.json",
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
    controls.stop()  # Restart paused; the campaign baseline and protective orders survive.

    def supervise():
        while not stop.is_set():
            try:
                engine.monitor()
            except Exception:  # noqa: S110 - Error is exposed by risk_snapshot, no secrets logged.
                pass
            stop.wait(30)

    supervisor = threading.Thread(target=supervise, daemon=True)
    supervisor.start()
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
        supervisor.join(timeout=20)
        lock.close()


if __name__ == "__main__":
    main()
