"""Worker lifecycle shared with the dashboard, including terminal failures."""

import threading
from datetime import UTC, datetime


class Automation:
    def __init__(self, enabled=False):
        self.enabled = enabled
        self.state = "starting" if enabled else "manual"
        self.error = None
        self.last_cycle = None
        self.lock = threading.Lock()

    def snapshot(self):
        with self.lock:
            return {
                "enabled": self.enabled,
                "running": self.state in ("starting", "running"),
                "state": self.state,
                "error": self.error,
                "last_cycle": self.last_cycle,
            }

    def run(self, engine, stop, interval=60):
        with self.lock:
            self.state = "running"
        try:
            while not stop.is_set():
                engine.cycle()
                with self.lock:
                    self.last_cycle = datetime.now(UTC).isoformat()
                stop.wait(interval)
        except Exception:
            with self.lock:
                self.state = "error"
                self.error = (
                    "Automatización detenida por error. Revisa órdenes en Alpaca "
                    "antes de reiniciar."
                )
            return
        with self.lock:
            self.state = "stopped"
