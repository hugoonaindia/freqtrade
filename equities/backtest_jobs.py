"""One isolated read-only historical job; no access to the live Engine."""

import re
import secrets
import threading
from dataclasses import asdict

from .backtest import Config, compare
from .backtest_data import load_histories
from .broker import Alpaca
from .paths import credentials_path


def historical_client():
    return Alpaca(credentials_path())


def parameters(payload):
    if not isinstance(payload, dict) or set(payload) - {"symbols", *Config.__dataclass_fields__}:
        raise ValueError("Parámetros de backtest no válidos.")
    symbols = payload.get("symbols", ["AAPL", "MSFT"])
    if (
        not isinstance(symbols, list)
        or not 1 <= len(symbols) <= 8
        or any(
            not isinstance(s, str) or not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", s) for s in symbols
        )
        or len(set(symbols)) != len(symbols)
    ):
        raise ValueError("Indica de 1 a 8 símbolos únicos en mayúsculas.")
    config = Config(**{k: v for k, v in payload.items() if k != "symbols"})
    return symbols, config


class BacktestJobs:
    def __init__(self, broker_factory=historical_client):
        self.factory = broker_factory
        self.lock = threading.Lock()
        self.thread = None
        self.current = {"id": None, "state": "idle", "message": "Sin backtest ejecutado."}
        self.report = None

    def status(self):
        with self.lock:
            return dict(self.current)

    def start(self, payload):
        symbols, config = parameters(payload)
        with self.lock:
            if self.current["state"] == "running":
                raise RuntimeError("Ya hay un backtest en curso.")
            self.report = None
            self.current = {
                "id": secrets.token_hex(12),
                "state": "running",
                "message": "Preparando históricos…",
                "symbols": symbols,
                "config": asdict(config),
            }
            self.thread = threading.Thread(target=self._run, args=(symbols, config), daemon=True)
            self.thread.start()
            return dict(self.current)

    def _progress(self, message):
        with self.lock:
            self.current["message"] = message

    def _run(self, symbols, config):
        try:
            histories = load_histories(self.factory(), symbols, config, self._progress)
            self._progress("Simulando seis estrategias y dos referencias…")
            report = compare(histories, config)
            with self.lock:
                self.report = report
                self.current.update(state="complete", message="Backtest completado.")
        except Exception as exc:
            message = (
                str(exc)
                if isinstance(exc, ValueError)
                else "Datos o cálculo no válidos; backtest detenido."
            )
            with self.lock:
                self.current.update(state="error", message=message)

    def result(self, job_id):
        with self.lock:
            if job_id != self.current["id"] or self.current["state"] != "complete":
                raise ValueError("Informe no disponible para este backtest.")
            return self.report

    def wait(self, timeout):
        if self.thread:
            self.thread.join(timeout)
