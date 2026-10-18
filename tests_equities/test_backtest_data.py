import threading
import unittest
from datetime import datetime, timedelta

from equities.backtest import Config
from equities.backtest_data import load_histories
from equities.backtest_jobs import BacktestJobs


class HistoryBroker:
    def __init__(self):
        self.calls = []
        self.days = []
        day = datetime(2024, 9, 1).date()
        while day <= datetime(2025, 2, 28).date():
            if day.weekday() < 5:
                self.days.append(day.isoformat())
            day += timedelta(days=1)
        self.missing = None

    def get(self, path, **params):
        self.calls.append((path, params))
        if path == "/v2/clock":
            return {"timestamp": "2025-03-03T12:00:00-05:00", "is_open": True}
        if path == "/v2/calendar":
            return [
                {"date": d, "open": "09:30", "close": "16:00"}
                for d in self.days
                if params["start"] <= d <= params["end"]
            ]
        if path.startswith("/v2/assets/"):
            return {"class": "us_equity"}
        if path.endswith("/bars"):
            dates = self.days[:70] if not params.get("page_token") else self.days[70:]
            return {
                "bars": [
                    {"t": d + "T12:00:00Z", "o": 100, "h": 101, "l": 99, "c": 100}
                    for d in dates
                    if d != self.missing
                ],
                "next_page_token": "second" if not params.get("page_token") else None,
            }
        raise AssertionError(path)

    def post(self, *args):
        raise AssertionError("Backtester must never place orders")


class DataTests(unittest.TestCase):
    def setUp(self):
        self.broker = HistoryBroker()
        self.config = Config(start="2025-01-02", end="2025-02-28")

    def test_loads_all_pages_and_split_adjustment(self):
        data = load_histories(self.broker, ["AAPL"], self.config)
        self.assertEqual(len(data["AAPL"]), len(self.broker.days))
        calls = [params for path, params in self.broker.calls if path.endswith("/bars")]
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["adjustment"], "split")
        self.assertEqual(calls[1]["page_token"], "second")

    def test_missing_market_session_rejected(self):
        self.broker.missing = "2025-02-03"
        with self.assertRaises(ValueError):
            load_histories(self.broker, ["AAPL"], self.config)

    def test_future_end_rejected(self):
        with self.assertRaises(ValueError):
            load_histories(self.broker, ["AAPL"], Config(start="2025-01-02", end="2026-01-01"))

    def test_repeated_page_token_rejected(self):
        original = self.broker.get

        def stuck(path, **params):
            data = original(path, **params)
            if path.endswith("/bars"):
                data["next_page_token"] = "second"  # noqa: S105 - Fake pagination cursor.
            return data

        self.broker.get = stuck
        with self.assertRaises(ValueError):
            load_histories(self.broker, ["AAPL"], self.config)

    def test_async_job_finishes_without_order_calls(self):
        jobs = BacktestJobs(lambda: self.broker)
        job = jobs.start({"symbols": ["AAPL"], "start": "2025-01-02", "end": "2025-02-28"})
        jobs.wait(10)
        self.assertEqual(jobs.status()["id"], job["id"])
        self.assertEqual(jobs.status()["state"], "complete")
        self.assertEqual(len(jobs.result(job["id"])["strategies"]), 6)

    def test_rejects_unknown_parameters(self):
        jobs = BacktestJobs(lambda: self.broker)
        with self.assertRaises(ValueError):
            jobs.start({"symbols": ["AAPL"], "made_up": 1})

    def test_only_one_job_at_a_time(self):
        gate = threading.Event()

        class Waiting(HistoryBroker):
            def get(self, *args, **params):
                gate.wait(5)
                return super().get(*args, **params)

        jobs = BacktestJobs(lambda: Waiting())
        jobs.start({"symbols": ["AAPL"], "start": "2025-01-02", "end": "2025-02-28"})
        try:
            with self.assertRaises(RuntimeError):
                jobs.start({"symbols": ["MSFT"]})
        finally:
            gate.set()
            jobs.wait(10)

    def test_missing_last_warmup_session_rejected(self):
        self.broker.missing = "2024-12-31"
        with self.assertRaises(ValueError):
            load_histories(self.broker, ["AAPL"], self.config)
