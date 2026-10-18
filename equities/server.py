"""Local dashboard. Credentials remain server-side; writes require same-origin token."""

import json
import os
import secrets
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .automation import Automation
from .backtest_jobs import BacktestJobs
from .controls import Controls
from .engine import SelectionChanged
from .strategies import STRATEGIES


def make_server(broker, engine, port, auto=False, automation=None, backtests=None, controls=None):  # noqa: C901
    automation = automation or Automation(enabled=auto)
    backtests = backtests or BacktestJobs()
    controls = controls or Controls(engine, automation)
    token = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, status, value, content_type="application/json"):
            raw = value.encode() if isinstance(value, str) else json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type + "; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                "style-src 'self' 'unsafe-inline'; frame-ancestors 'none'",
            )
            self.end_headers()
            self.wfile.write(raw)

        def allowed_host(self):
            return self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"

        def do_GET(self):  # noqa: C901 - Local read routes and broker status.
            if not self.allowed_host():
                return self.send(403, {"error": "Host no permitido"})
            settings = {}
            try:
                if self.path == "/api/health":
                    return self.send(
                        200,
                        {
                            "application": "freqtrade-equities",
                            "protocol": 1,
                            "pid": os.getpid(),
                            "desktop": bool(getattr(sys, "frozen", False)),
                        },
                    )
                if self.path == "/":
                    html = Path(__file__).with_name("dashboard.html").read_text()
                    return self.send(200, html.replace("__TOKEN__", token), "text/html")
                if self.path == "/dashboard.js":
                    return self.send(
                        200,
                        Path(__file__).with_name("dashboard_ui.js").read_text(),
                        "text/javascript",
                    )
                if self.path == "/backtest.js":
                    return self.send(
                        200,
                        Path(__file__).with_name("backtest_ui.js").read_text(),
                        "text/javascript",
                    )
                if self.path == "/api/backtest":
                    return self.send(200, backtests.status())
                if self.path.startswith("/api/backtest/result/"):
                    try:
                        report = backtests.result(self.path.removeprefix("/api/backtest/result/"))
                        return self.send(200, report)
                    except ValueError as exc:
                        return self.send(404, {"error": str(exc)})
                if self.path != "/api/status":
                    return self.send(404, {"error": "No encontrado"})
                lifecycle = controls.snapshot()
                settings = {
                    "strategies": STRATEGIES,
                    **(engine.selection() if engine else {"strategy": "sma", "revision": 0}),
                    "symbols": engine.symbols if engine else [],
                    "budget": engine.budget if engine else 0,
                    "auto": lifecycle["running"],
                    "entries_enabled": engine.entries_enabled if engine else False,
                    "automation": lifecycle,
                }
                if not broker.configured:
                    return self.send(
                        200,
                        {
                            **settings,
                            "configured": False,
                            "setup_message": "Faltan claves Alpaca Paper. Consulta docs/alpaca.md.",
                        },
                    )
                account = broker.get("/v2/account")
                return self.send(
                    200,
                    {
                        "configured": True,
                        "paper": True,
                        "account": {
                            k: account.get(k)
                            for k in (
                                "equity",
                                "cash",
                                "buying_power",
                                "last_equity",
                                "currency",
                                "status",
                                "trading_blocked",
                            )
                        },
                        "clock": broker.get("/v2/clock"),
                        "positions": broker.get("/v2/positions"),
                        "orders": controls.visible_orders(
                            broker.get("/v2/orders", status="all", limit=100, direction="desc")
                        ),
                        "history_since": controls.history_since,
                        **settings,
                        "last": engine.last,
                    },
                )
            except ValueError as exc:
                self.send(502, {**settings, "error": str(exc)})
            except Exception:
                self.send(
                    500, {**settings, "error": "Error interno. Revisa configuración y reinicia."}
                )

        def read_payload(self):
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 4096:
                raise ValueError("Tamaño no válido.")
            payload = json.loads(self.rfile.read(size))
            if not isinstance(payload, dict):
                raise ValueError("Formato no válido.")
            return payload

        def start_backtest(self, payload):
            try:
                return self.send(202, backtests.start(payload))
            except ValueError as exc:
                return self.send(400, {"error": str(exc)})
            except RuntimeError as exc:
                return self.send(409, {"error": str(exc)})

        def do_POST(self):  # noqa: C901 - Explicit authenticated operator routes.
            origin = f"http://127.0.0.1:{self.server.server_port}"
            if (
                not self.allowed_host()
                or self.headers.get("Origin") != origin
                or not secrets.compare_digest(self.headers.get("X-Session-Token", ""), token)
            ):
                return self.send(403, {"error": "Sesión no válida; recarga panel."})
            if self.path not in (
                "/api/cycle",
                "/api/preview",
                "/api/strategy",
                "/api/backtest",
                "/api/settings",
                "/api/paper/start",
                "/api/paper/stop",
                "/api/positions/close",
                "/api/history/clear",
            ):
                return self.send(404, {"error": "No encontrado"})
            try:
                payload = self.read_payload()
            except (ValueError, UnicodeDecodeError):
                return self.send(400, {"error": "Formato de petición no válido."})
            try:
                if self.path == "/api/backtest":
                    return self.start_backtest(payload)
                if self.path == "/api/settings":
                    return self.send(200, controls.configure(payload))
                if self.path == "/api/paper/start":
                    return self.send(200, controls.start(payload))
                if self.path == "/api/positions/close":
                    return self.send(200, {"results": controls.close_positions()})
                if self.path == "/api/paper/stop":
                    return self.send(200, controls.stop())
                if self.path == "/api/history/clear":
                    return self.send(200, controls.clear_history())
                if self.path == "/api/strategy":
                    if controls.snapshot()["running"] or automation.enabled:
                        return self.send(
                            409, {"error": "Reinicia sin --auto para cambiar estrategia."}
                        )
                    strategy = payload.get("strategy")
                    if not isinstance(strategy, str) or strategy not in STRATEGIES:
                        return self.send(400, {"error": "Estrategia no válida."})
                    return self.send(
                        200,
                        controls.configure(
                            {
                                "strategy": strategy,
                                "symbols": engine.symbols,
                                "budget": engine.budget,
                            }
                        ),
                    )
                strategy, revision = payload.get("strategy"), payload.get("revision")
                if not isinstance(strategy, str) or type(revision) is not int or revision < 0:
                    return self.send(
                        400, {"error": "Actualiza panel antes de operar; falta selección válida."}
                    )
                self.send(
                    200,
                    {
                        "results": controls.cycle(
                            self.path == "/api/cycle", {"strategy": strategy, "revision": revision}
                        )
                    },
                )
            except RuntimeError as exc:
                self.send(409, {"error": str(exc)})
            except SelectionChanged as exc:
                self.send(409, {"error": str(exc)})
            except ValueError as exc:
                self.send(502, {"error": str(exc)})
            except Exception:
                self.send(
                    500,
                    {"error": "Ciclo detenido. Comprueba órdenes en Alpaca antes de reintentar."},
                )

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)
