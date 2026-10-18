"""Serialize operator actions and persist desktop settings without clearing order guards."""

import json
import threading
from datetime import UTC, datetime

from .automation import Automation
from .engine import Engine, SelectionChanged


class Controls:
    def __init__(self, engine, automation=None, settings_path=None, port=8081):
        self.engine = engine
        self.automation = automation or Automation()
        self.path = settings_path
        self.port = port
        self.lock = threading.RLock()
        self.worker = None
        self.event = threading.Event()
        self.history_since = None
        if self.path and self.path.exists():
            self.history_since = json.loads(self.path.read_text()).get("history_since")

    def snapshot(self):
        with self.lock:
            state = self.automation.snapshot()
            if self.worker and self.worker.is_alive():
                state["running"] = True
                if self.event.is_set():
                    state["state"] = "stopping"
            return state

    def _idle(self):
        if self.snapshot()["running"]:
            raise RuntimeError(
                "Para Paper y espera fin del ciclo antes de cambiar ajustes u operar manualmente."
            )

    def _save(self, auto=False):
        if not self.path:
            return
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        settings = {
            "auto": auto,
            "strategy": self.engine.strategy,
            "symbols": self.engine.symbols,
            "budget": self.engine.budget,
            "port": self.port,
            "history_since": self.history_since,
            "entries_enabled": self.engine.entries_enabled,
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(settings, indent=2) + "\n")
        tmp.chmod(0o600)
        tmp.replace(self.path)

    def configure(self, payload):
        with self.lock:
            self._idle()
            if set(payload) != {"strategy", "symbols", "budget"}:
                raise ValueError("Indica estrategia, símbolos y presupuesto.")
            symbols, budget = payload["symbols"], payload["budget"]
            if (
                not isinstance(symbols, list)
                or not 1 <= len(symbols) <= 8
                or any(not isinstance(s, str) for s in symbols)
                or type(budget) not in (int, float)
            ):
                raise ValueError("Usa de 1 a 8 símbolos y presupuesto positivo.")
            if getattr(self.engine, "campaign", None) and (
                self.engine.broker.get("/v2/positions") or self.engine._orders()
            ):
                raise ValueError(
                    "No cambies el universo con posiciones u órdenes de campaña abiertas."
                )
            Engine(self.engine.broker, symbols, budget, strategy=payload["strategy"])
            with self.engine.lock:
                previous = (
                    self.engine.strategy,
                    self.engine.symbols,
                    self.engine.budget,
                    self.engine.revision,
                    self.engine.last,
                )
                self.engine.strategy = payload["strategy"]
                self.engine.symbols = list(symbols)
                self.engine.budget = budget
                self.engine.revision += 1
                self.engine.last = []
                try:
                    self._save()
                except Exception:
                    (
                        self.engine.strategy,
                        self.engine.symbols,
                        self.engine.budget,
                        self.engine.revision,
                        self.engine.last,
                    ) = previous
                    raise
            return self.engine.selection()

    def _selection(self, payload):
        if (
            not isinstance(payload.get("strategy"), str)
            or type(payload.get("revision")) is not int
            or payload["revision"] < 0
        ):
            raise ValueError("Actualiza panel antes de operar; falta selección válida.")
        if self.engine.selection() != payload:
            raise SelectionChanged("Ajustes cambiaron. Actualiza panel y revisa señales.")

    def start(self, selection):
        with self.lock:
            self._idle()
            self._selection(selection)
            if hasattr(self.engine, "arm"):
                self.engine.arm()
            if not self.engine.entries_enabled:
                raise ValueError("Entradas bloqueadas. Usa Cerrar posiciones Paper.")
            if not self.engine.broker.configured:
                raise ValueError("Faltan claves Alpaca Paper.")
            try:
                self._save(auto=True)
            except Exception:
                if hasattr(self.engine, "disarm"):
                    self.engine.disarm()
                raise
            self.event = threading.Event()
            self.automation = Automation(enabled=True)
            self.worker = threading.Thread(target=self._run, daemon=True)
            self.worker.start()
            return self.snapshot()

    def _run(self):
        self.automation.run(self.engine, self.event)
        if self.automation.snapshot()["state"] == "error":
            if hasattr(self.engine, "disarm"):
                self.engine.disarm()
            with self.lock:
                with self.automation.lock:
                    self.automation.enabled = False
                try:
                    self._save(auto=False)
                except OSError:
                    pass  # Failure remains visible; no further cycles run.

    def stop(self):
        with self.lock:
            self.event.set()
            if hasattr(self.engine, "disarm"):
                self.engine.disarm()
            with self.automation.lock:
                self.automation.enabled = False
            self._save(auto=False)
            return self.snapshot()

    def cycle(self, execute, selection):
        with self.lock:
            if execute and not self.engine.entries_enabled:
                raise ValueError("Entradas bloqueadas. Usa Cerrar posiciones Paper.")
            self._idle()
            self._selection(selection)
            return self.engine.cycle(
                execute=execute,
                expected_strategy=selection["strategy"],
                expected_revision=selection["revision"],
            )

    def close_positions(self):
        with self.lock:
            self._idle()
            return self.engine.close_positions()

    def reset_campaign(self):
        with self.lock:
            self._idle()
            if not hasattr(self.engine, "reset_campaign"):
                raise ValueError("Este motor no usa campañas de riesgo.")
            return self.engine.reset_campaign()

    def clear_history(self):
        with self.lock:
            previous = self.history_since
            self.history_since = datetime.now(UTC).isoformat()
            try:
                self._save(auto=self.snapshot()["enabled"])
            except Exception:
                self.history_since = previous
                raise
            return {"history_since": self.history_since}

    def visible_orders(self, orders):
        with self.lock:
            if not self.history_since:
                return orders
            cutoff = datetime.fromisoformat(self.history_since)
            return [
                o
                for o in orders
                if o.get("created_at") and datetime.fromisoformat(o["created_at"]) >= cutoff
            ]

    def close(self):
        self.event.set()
        if self.worker:
            self.worker.join()
