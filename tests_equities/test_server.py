# ruff: noqa: S310
# HTTP requests below target only the ephemeral local test server.
import json
import re
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from test_engine import Broker

from equities import server
from equities.engine import Engine
from equities.server import make_server


class FakeBroker:
    configured = False


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.server = make_server(FakeBroker(), None, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = "http://127.0.0.1:" + str(self.server.server_port)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def test_dashboard(self):
        with urlopen(self.url) as r:
            self.assertIn("ALPACA PAPER", r.read().decode())

    def test_scripts_are_served_as_utf8(self):
        for route, filename in [
            ("/dashboard.js", "dashboard_ui.js"),
            ("/backtest.js", "backtest_ui.js"),
        ]:
            with self.subTest(route=route), urlopen(self.url + route) as response:
                expected = (
                    Path(server.__file__).with_name(filename).read_text(encoding="utf-8").encode()
                )
                self.assertEqual(response.read(), expected)
                self.assertIn("charset=utf-8", response.headers["Content-Type"])

    def test_setup_status(self):
        with urlopen(self.url + "/api/status") as r:
            self.assertFalse(json.load(r)["configured"])

    def test_writes_need_token(self):
        req = Request(self.url + "/api/cycle", data=b"{}", method="POST")
        with self.assertRaises(HTTPError) as ctx:
            urlopen(req)
        self.assertEqual(ctx.exception.code, 403)

    def test_foreign_host_rejected(self):
        req = Request(self.url, headers={"Host": "evil.example"})
        with self.assertRaises(HTTPError) as ctx:
            urlopen(req)
        self.assertEqual(ctx.exception.code, 403)


class StrategyServerTests(ServerTests):
    def setUp(self):
        self.broker = Broker()
        self.broker.configured = False
        self.engine = Engine(self.broker, ["AAPL"], 500)
        self.server = make_server(self.broker, self.engine, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = "http://127.0.0.1:" + str(self.server.server_port)
        with urlopen(self.url) as response:
            html = response.read().decode()
        self.token = re.search("token='([^']+)'", html)[1]

    def select(self, payload, origin=None):
        req = Request(
            self.url + "/api/strategy",
            data=json.dumps(payload).encode(),
            headers={
                "X-Session-Token": self.token,
                "Origin": origin or self.url,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        return urlopen(req)

    def test_catalog_available_without_credentials(self):
        with urlopen(self.url + "/api/status") as response:
            data = json.load(response)
        self.assertEqual(len(data["strategies"]), 6)
        self.assertEqual(data["strategy"], "sma")

    def test_strategy_selection_never_sends_orders(self):
        with self.select({"strategy": "macd"}) as response:
            self.assertEqual(json.load(response)["strategy"], "macd")
        self.assertEqual(self.engine.strategy, "macd")
        self.assertEqual(self.broker.sent, [])

    def test_invalid_strategy_is_bad_request(self):
        with self.assertRaises(HTTPError) as ctx:
            self.select({"strategy": "unknown"})
        self.assertEqual(ctx.exception.code, 400)
        self.assertEqual(self.engine.strategy, "sma")

    def test_strategy_change_rejects_foreign_origin(self):
        with self.assertRaises(HTTPError) as ctx:
            self.select({"strategy": "macd"}, "https://evil.example")
        self.assertEqual(ctx.exception.code, 403)

    def test_strategy_payload_must_be_object(self):
        with self.assertRaises(HTTPError) as ctx:
            self.select(["macd"])
        self.assertEqual(ctx.exception.code, 400)

    def cycle_request(self, payload):
        req = Request(
            self.url + "/api/cycle",
            data=json.dumps(payload).encode(),
            headers={
                "X-Session-Token": self.token,
                "Origin": self.url,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        return urlopen(req)

    def test_stale_tab_order_rejected_with_conflict(self):
        with self.select({"strategy": "mean_reversion"}):
            pass
        with self.assertRaises(HTTPError) as ctx:
            self.cycle_request({"strategy": "sma", "revision": 0})
        self.assertEqual(ctx.exception.code, 409)
        self.assertEqual(self.broker.sent, [])

    def test_order_without_selection_rejected(self):
        with self.assertRaises(HTTPError) as ctx:
            self.cycle_request({})
        self.assertEqual(ctx.exception.code, 400)
        self.assertEqual(self.broker.sent, [])

    def test_current_selection_can_execute(self):
        from test_regressions import RegressionBroker

        self.engine.broker = RegressionBroker()
        with self.cycle_request({"strategy": "sma", "revision": 0}) as response:
            data = json.load(response)
        self.assertEqual(data["results"][0]["strategy"], "sma")
        self.assertEqual(len(self.engine.broker.sent), 1)

    def test_automation_failure_visible_even_with_broker_offline(self):
        from equities.automation import Automation

        automation = Automation(enabled=True)

        class FailingEngine:
            def cycle(self):
                raise ValueError("offline")

        automation.run(FailingEngine(), threading.Event())
        other = make_server(self.broker, self.engine, 0, automation=automation)
        thread = threading.Thread(target=other.serve_forever, daemon=True)
        thread.start()
        try:
            with urlopen(f"http://127.0.0.1:{other.server_port}/api/status") as response:
                data = json.load(response)
            self.assertFalse(data["auto"])
            self.assertEqual(data["automation"]["state"], "error")
            self.assertTrue(data["automation"]["error"])
        finally:
            other.shutdown()
            other.server_close()
            thread.join()

    def test_bad_account_read_still_publishes_failed_worker(self):
        from equities.automation import Automation

        lifecycle = Automation(enabled=True)

        class Offline:
            configured = True

            def get(self, *args, **kwargs):
                raise ValueError("Alpaca no responde.")

        class FailedEngine:
            def cycle(self):
                raise ValueError("timeout")

        lifecycle.run(FailedEngine(), threading.Event())
        other = make_server(Offline(), self.engine, 0, automation=lifecycle)
        thread = threading.Thread(target=other.serve_forever, daemon=True)
        thread.start()
        try:
            with self.assertRaises(HTTPError) as ctx:
                urlopen(f"http://127.0.0.1:{other.server_port}/api/status")
            data = json.load(ctx.exception)
            self.assertEqual(ctx.exception.code, 502)
            self.assertFalse(data["auto"])
            self.assertEqual(data["automation"]["state"], "error")
        finally:
            other.shutdown()
            other.server_close()
            thread.join()

    def test_unexpected_broker_error_preserves_lifecycle(self):
        from equities.automation import Automation

        lifecycle = Automation(enabled=True)

        class Offline:
            configured = True

            def get(self, *args, **kwargs):
                raise RuntimeError("malformed upstream response")

        class FailedEngine:
            def cycle(self):
                raise ValueError("timeout")

        lifecycle.run(FailedEngine(), threading.Event())
        other = make_server(Offline(), self.engine, 0, automation=lifecycle)
        thread = threading.Thread(target=other.serve_forever, daemon=True)
        thread.start()
        try:
            with self.assertRaises(HTTPError) as ctx:
                urlopen(f"http://127.0.0.1:{other.server_port}/api/status")
            data = json.load(ctx.exception)
            self.assertEqual(ctx.exception.code, 500)
            self.assertEqual(data["automation"]["state"], "error")
        finally:
            other.shutdown()
            other.server_close()
            thread.join()

    def test_health_endpoint_does_not_read_broker_account(self):
        with urlopen(self.url + "/api/health") as response:
            value = json.load(response)
        self.assertEqual(value["application"], "freqtrade-equities")
        self.assertEqual(value["protocol"], 1)
        self.assertGreater(value["pid"], 0)

    def operator_request(self, path, payload, token=None):
        return urlopen(
            Request(
                self.url + path,
                data=json.dumps(payload).encode(),
                headers={
                    "Origin": self.url,
                    "X-Session-Token": token or self.token,
                    "Content-Type": "application/json",
                },
                method="POST",
            )
        )

    def test_operator_configuration_updates_all_fields_without_orders(self):
        with self.operator_request(
            "/api/settings", {"strategy": "macd", "symbols": ["MSFT"], "budget": 250}
        ) as response:
            self.assertEqual(json.load(response)["strategy"], "macd")
        self.assertEqual(self.engine.symbols, ["MSFT"])
        self.assertEqual(self.engine.budget, 250)
        self.assertEqual(self.broker.sent, [])

    def test_operator_routes_require_session_token(self):
        for path in [
            "/api/settings",
            "/api/paper/start",
            "/api/paper/stop",
            "/api/paper/reset",
            "/api/history/clear",
            "/api/positions/close",
        ]:
            with self.assertRaises(HTTPError) as ctx:
                self.operator_request(path, {}, token="invalid")  # noqa: S106 - Invalid test token.
            self.assertEqual(ctx.exception.code, 403)

    def test_start_without_credentials_cannot_send_orders(self):
        with self.assertRaises(HTTPError):
            self.operator_request("/api/paper/start", self.engine.selection())
        self.assertEqual(self.broker.sent, [])

    def test_history_reset_returns_cutoff_without_orders(self):
        with self.operator_request("/api/history/clear", {}) as response:
            self.assertTrue(json.load(response)["history_since"])
        self.assertEqual(self.broker.sent, [])
