# ruff: noqa: S310
# All HTTP calls below target the ephemeral local test server.
import json
import re
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from test_backtest_data import HistoryBroker

from equities.backtest_jobs import BacktestJobs
from equities.server import make_server


class BacktestApiTests(unittest.TestCase):
    def setUp(self):
        self.jobs = BacktestJobs(lambda: HistoryBroker())

        class NoTrading:
            configured = False

        self.server = make_server(NoTrading(), None, 0, backtests=self.jobs, auto=True)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        with urlopen(self.url) as response:
            html = response.read().decode()
        self.token = re.search("token='([^']+)'", html)[1]

    def tearDown(self):
        self.jobs.wait(10)
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def post(self, payload, origin=None):
        return urlopen(
            Request(
                self.url + "/api/backtest",
                data=json.dumps(payload).encode(),
                headers={
                    "Origin": origin or self.url,
                    "X-Session-Token": self.token,
                    "Content-Type": "application/json",
                },
                method="POST",
            )
        )

    def test_job_can_run_while_auto_enabled(self):
        with self.post(
            {"symbols": ["AAPL"], "start": "2025-01-02", "end": "2025-02-28"}
        ) as response:
            self.assertEqual(response.status, 202)
            job = json.load(response)
        self.jobs.wait(10)
        with urlopen(self.url + "/api/backtest") as response:
            self.assertEqual(json.load(response)["state"], "complete")
        with urlopen(self.url + "/api/backtest/result/" + job["id"]) as response:
            self.assertEqual(len(json.load(response)["strategies"]), 6)

    def test_unsafe_symbol_rejected(self):
        with self.assertRaises(HTTPError) as ctx:
            self.post({"symbols": ["BTC/USD"]})
        self.assertEqual(ctx.exception.code, 400)

    def test_foreign_origin_rejected(self):
        with self.assertRaises(HTTPError) as ctx:
            self.post({}, "https://evil.example")
        self.assertEqual(ctx.exception.code, 403)

    def test_unknown_report_not_returned(self):
        with self.assertRaises(HTTPError) as ctx:
            urlopen(self.url + "/api/backtest/result/missing")
        self.assertEqual(ctx.exception.code, 404)
