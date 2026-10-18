import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from unittest.mock import patch

from test_backtest import history
from test_swing import PaperBroker

from equities.backtest import SessionHistory, past_bars
from equities.dashboard_reader import DashboardReader
from equities.strategies import decide
from equities.swing import SwingEngine


class IndexedHistoryTests(unittest.TestCase):
    def test_indexed_windows_match_original_and_exclude_future(self):
        data = {bar["t"][:10]: bar for bar in history([100] * 500)}
        indexed = SessionHistory(data)
        for offset in [0, 20, 400, 450, 499, 550]:
            day = (date(2025, 1, 1) + timedelta(days=offset)).isoformat()
            expected = past_bars(data, day)
            self.assertEqual(past_bars(indexed, day), expected)
            self.assertEqual(past_bars(indexed, day, 21), expected[-21:])
        with self.assertRaises(TypeError):
            indexed["2030-01-01"] = {}

    def test_runtime_still_rejects_invalid_early_bar(self):
        data = history([100] * 100)
        data[0]["c"] = float("nan")
        self.assertEqual(decide("sma", data)["signal"], "hold")


class DashboardReadTests(unittest.TestCase):
    def test_four_reads_run_concurrently(self):
        barrier = threading.Barrier(4, timeout=3)

        class Broker:
            def get(self, path, **kwargs):
                barrier.wait()
                return path

        reader = DashboardReader(Broker())
        self.addCleanup(reader.close)
        result = reader.load()
        self.assertEqual(result["positions"], "/v2/positions")
        self.assertIn("received_at", result)

    def test_next_poll_fetches_fresh_data_and_recovers_after_failure(self):
        class Broker:
            calls = 0
            fail = True
            lock = threading.Lock()

            def get(self, path, **kwargs):
                with self.lock:
                    self.calls += 1
                if self.fail:
                    raise ValueError("offline")
                return self.calls

        broker = Broker()
        reader = DashboardReader(broker)
        self.addCleanup(reader.close)
        with self.assertRaises(ValueError):
            reader.load()
        broker.fail = False
        reader.load()
        reader.load()
        self.assertEqual(broker.calls, 12)

    def test_overlapping_polls_share_inflight_reads(self):
        ready = threading.Event()

        class Broker:
            calls = 0
            lock = threading.Lock()

            def get(self, path, **kwargs):
                with self.lock:
                    self.calls += 1
                if not ready.wait(3):
                    raise RuntimeError("Overlapping pollers did not arrive")
                return path

        broker = Broker()
        reader = DashboardReader(broker)
        self.addCleanup(reader.close)
        original_wait = reader.condition.wait
        waiting = 0

        def wait_for_batch(*args, **kwargs):
            nonlocal waiting
            waiting += 1
            if waiting == 7:
                ready.set()
            return original_wait(*args, **kwargs)

        gate = threading.Barrier(8)

        def poll():
            gate.wait(timeout=3)
            return reader.load()

        with (
            patch.object(reader.condition, "wait", side_effect=wait_for_batch),
            ThreadPoolExecutor(max_workers=8) as callers,
        ):
            results = list(callers.map(lambda _: poll(), range(8)))
        self.assertEqual(broker.calls, 4)
        self.assertTrue(all(r["received_at"] == results[0]["received_at"] for r in results))

    def test_healthy_trading_cycle_reads_risk_once_without_reusing_previous_cycle(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as folder:
            broker = PaperBroker()
            engine = SwingEngine(
                broker, ["AAPL"], 500, campaign_path=Path(folder) / "campaign.json"
            )
            engine.arm()
            with patch.object(broker, "get", wraps=broker.get) as get:
                engine.cycle()
                paths = [call.args[0] for call in get.call_args_list]
                for path in ["/v2/account", "/v2/orders", "/v2/positions", "/v2/clock"]:
                    self.assertEqual(paths.count(path), 1)
                broker.equity = 970
                engine.cycle()
                self.assertTrue(engine.campaign["tripped"])
