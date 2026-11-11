"""Read-only research worker, isolated from the process supervising Paper orders."""

import json
import os
import sys
from pathlib import Path
from queue import Full

MAX_REPORT_BYTES = 16 * 1024 * 1024


def apply_limits():
    if sys.platform.startswith("linux"):
        import resource

        try:
            memory = int(os.environ.get("EQUITIES_BACKTEST_MEMORY_MB", "384"))
        except ValueError:
            raise ValueError(
                "EQUITIES_BACKTEST_MEMORY_MB debe ser un entero entre 128 y 2048."
            ) from None
        if not 128 <= memory <= 2048:
            raise ValueError("Memoria del backtest inválida: usa entre 128 y 2048 MiB.")
        resource.setrlimit(resource.RLIMIT_AS, (memory * 1024 * 1024, memory * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_CPU, (600, 600))
        os.nice(10)


def stop_process(process):
    if process.pid is not None:
        process.join(timeout=1)
        if process.is_alive():
            process.terminate()
            process.join(timeout=2)
        if process.is_alive():
            process.kill()
            process.join(timeout=2)
    process.close()


def worker_entry(symbols, config_dict, holdout, analytics_path, result_path, messages):
    try:
        apply_limits()
        from .analytics import Analytics
        from .backtest import Config
        from .backtest_jobs import BacktestJobs

        jobs = BacktestJobs(
            analytics=Analytics(analytics_path, reserve_bytes=256 * 1024 * 1024)
            if analytics_path
            else None,
            isolated=False,
        )

        def progress(message):
            try:
                messages.put_nowait({"state": "running", "message": message})
            except Full:
                pass

        jobs._progress = progress
        jobs._run(symbols, Config(**config_dict), holdout)
        if jobs.current["state"] != "complete":
            messages.put({"state": "error", "message": jobs.current["message"]}, timeout=5)
            return
        target = Path(result_path)
        # Iterative encoding avoids a second full JSON copy in worker memory.
        bytes_written = 0
        with target.open("w", encoding="utf-8") as stream:
            for chunk in json.JSONEncoder(
                ensure_ascii=False, allow_nan=False, separators=(",", ":")
            ).iterencode(jobs.report):
                bytes_written += len(chunk.encode("utf-8"))
                if bytes_written > MAX_REPORT_BYTES:
                    raise ValueError("Informe demasiado grande; reduce el período o los símbolos.")
                stream.write(chunk)
        messages.put({"state": "complete", "message": "Backtest completado."}, timeout=5)
    except MemoryError:
        messages.put(
            {
                "state": "error",
                "message": "El backtest superó su memoria asignada; reduce símbolos o período. La supervisión de órdenes sigue en otro proceso.",
            },
            timeout=5,
        )
    except Exception:
        messages.put(
            {
                "state": "error",
                "message": "El proceso de backtest no pudo completar el trabajo. Revisa recursos y datos.",
            },
            timeout=5,
        )
