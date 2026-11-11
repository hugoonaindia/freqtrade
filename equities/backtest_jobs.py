"""One isolated read-only historical job; no access to the live Engine."""

import re
import secrets
import threading
import json
import multiprocessing
import sys
import tempfile
import shutil
from pathlib import Path
from queue import Empty
from time import monotonic
from dataclasses import asdict
from contextlib import ExitStack

from .backtest import Config, compare
from .backtest_data import load_histories
from .broker import Alpaca
from .paths import credentials_path, runtime_root
from .temporal_policy import enforce_development, temporal_policy
from .out_of_sample import evaluate_holdout, validate_holdout


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
    def __init__(
        self,
        broker_factory=historical_client,
        analytics=None,
        *,
        isolated=None,
        process_target=None,
        timeout_seconds=1800,
    ):
        self.factory = broker_factory
        self.isolated = (
            (broker_factory is historical_client and not getattr(sys, "frozen", False))
            if isolated is None
            else isolated
        )
        self.process_target = process_target
        self.timeout_seconds = timeout_seconds
        self.cancelled = threading.Event()
        self.process = None
        self.analytics = analytics
        self.lock = threading.Lock()
        self.thread = None
        self.current = {"id": None, "state": "idle", "message": "Sin backtest ejecutado."}
        self.report = None

    def status(self):
        with self.lock:
            return dict(self.current)

    def start(self, payload):
        if not isinstance(payload, dict):
            raise ValueError("Parámetros de backtest no válidos.")
        payload = dict(payload)
        holdout = payload.pop("out_of_sample", None)
        symbols, config = parameters(payload)
        if holdout is not None:
            holdout = validate_holdout(config, holdout)
        else:
            enforce_development(config)
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
            self.cancelled.clear()
            self.thread = threading.Thread(
                target=self._run_isolated if self.isolated else self._run,
                args=(symbols, config, holdout),
                daemon=True,
            )
            self.thread.start()
            return dict(self.current)

    def _progress(self, message):
        with self.lock:
            self.current["message"] = message

    def _run_isolated(self, symbols, config, holdout=None):
        from .backtest_process import worker_entry, MAX_REPORT_BYTES, stop_process

        process = messages = None
        try:
            directory = runtime_root() / ".equities-state" / "backtest-tmp"
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            if shutil.disk_usage(directory).free < 512 * 1024 * 1024:
                raise ValueError(
                    "Espacio insuficiente para iniciar investigación: se requieren 512 MiB libres para preservar margen operativo."
                )
            research = self.analytics.path.with_name("research.sqlite") if self.analytics else None
            context = multiprocessing.get_context("spawn")
            messages = context.Queue(maxsize=32)
            with ExitStack() as cleanup:
                temporary = cleanup.enter_context(
                    tempfile.TemporaryDirectory(prefix="job-", dir=directory)
                )
                result_path = Path(temporary) / "report.json"
                process = context.Process(
                    target=self.process_target or worker_entry,
                    args=(
                        symbols,
                        asdict(config),
                        holdout,
                        str(research) if research else None,
                        str(result_path),
                        messages,
                    ),
                    daemon=True,
                )
                cleanup.callback(stop_process, process)
                self.process = process
                if self.cancelled.is_set():
                    raise ValueError("Backtest cancelado al cerrar la aplicación.")
                process.start()
                deadline = monotonic() + self.timeout_seconds
                complete = False
                while not complete:
                    if self.cancelled.is_set():
                        raise ValueError("Backtest cancelado al cerrar la aplicación.")
                    if monotonic() >= deadline:
                        raise ValueError(
                            "Backtest detenido al superar el tiempo máximo. Reduce símbolos o período."
                        )
                    try:
                        message = messages.get(timeout=0.25)
                    except Empty:
                        if not process.is_alive():
                            raise ValueError(
                                "El proceso de backtest terminó sin informe; puede haber alcanzado sus límites de memoria o CPU. La supervisión de órdenes permanece separada."
                            ) from None
                        continue
                    if message["state"] == "error":
                        raise ValueError(message["message"])
                    complete = message["state"] == "complete"
                    self._progress(message["message"])
                if result_path.stat().st_size > MAX_REPORT_BYTES:
                    raise ValueError("Informe demasiado grande para el panel.")
                with result_path.open(encoding="utf-8") as stream:
                    report = json.load(stream)
            with self.lock:
                self.report = report
                self.current.update(
                    state="complete", message="Backtest completado en proceso independiente."
                )
        except Exception as exc:
            with self.lock:
                self.current.update(
                    state="error",
                    message=str(exc)
                    if isinstance(exc, ValueError)
                    else "No se pudo iniciar el proceso de backtest.",
                )
        finally:
            self.process = None
            if messages is not None:
                messages.close()
                messages.cancel_join_thread()

    def close(self):
        self.cancelled.set()
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=7)

    def _run(self, symbols, config, holdout=None):
        run_id = None
        try:
            run_id = (
                self.analytics.start("backtest", {**asdict(config), "out_of_sample": holdout})
                if self.analytics
                else None
            )
            policy = temporal_policy()
            if holdout is None:
                enforce_development(config, policy)
            else:
                holdout = validate_holdout(config, holdout)
            histories = load_histories(self.factory(), symbols, config, self._progress)
            self._progress("Simulando seis estrategias y dos referencias…")
            execution_data = None
            if config.execution == "intraday":
                from .execution_replay import ExecutionData

                execution_data = ExecutionData(self.factory(), histories, config, self._progress)
            report = (
                evaluate_holdout(
                    histories, config, holdout, self._progress, execution_data=execution_data
                )
                if holdout is not None
                else (
                    compare(histories, config, execution_data)
                    if execution_data
                    else compare(histories, config)
                )
            )
            report["temporal_policy"] = policy
            report["assumptions"].append(
                f"Fuente histórica: {config.feed.upper()}. Paper utiliza IEX; una fuente distinta puede cambiar señales, liquidez y resultados."
            )
            if self.analytics:
                self.analytics.finish_backtest(
                    run_id,
                    {
                        "histories": histories,
                        "config": asdict(config),
                        "out_of_sample": holdout,
                        "execution_data": execution_data.archive if execution_data else None,
                    },
                    report,
                )
            with self.lock:
                self.report = report
                self.current.update(state="complete", message="Backtest completado.")
        except Exception as exc:
            if self.analytics and run_id:
                try:
                    self.analytics.record(
                        run_id, "backtest_failed", {"exception_type": type(exc).__name__}
                    )
                except Exception:
                    pass  # Preserve the original failure and always release the running job.
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
