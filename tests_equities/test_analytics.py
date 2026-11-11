import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

from equities.analytics import Analytics
from equities.backtest import Config
from equities.backtest_jobs import BacktestJobs


class AnalyticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "analytics.sqlite"
        self.store = Analytics(self.path)
        self.run = self.store.start("paper", {"capital": 1000, "api_secret": "hidden"})

    def test_persists_complete_history_beyond_preview(self):
        for i in range(110):
            self.store.record(self.run, "decision", {"index": i})
        restored = Analytics(self.path).summary(self.run)
        self.assertEqual(restored["counts"]["events"], 110)
        self.assertEqual(len(restored["events"]), 100)
        self.assertEqual(restored["events"][0]["payload"]["index"], 109)

    def test_redacts_nested_credentials_and_roundtrips_datasets(self):
        inputs = {
            "bars": [{"c": 100}],
            "headers": {"Authorization": "hidden"},
            "nested": {"API_SECRET": "hidden", "value": 2},
        }
        fingerprint = self.store.dataset(inputs)
        self.assertEqual(
            self.store.restore_dataset(fingerprint), {"bars": [{"c": 100}], "nested": {"value": 2}}
        )
        self.assertEqual(self.store.dataset(inputs), fingerprint)
        with self.store.connection() as db:
            self.assertNotIn("hidden", db.execute("SELECT config_json FROM runs").fetchone()[0])

    def test_unchanged_parent_still_reconciles_changed_child(self):
        parent = {
            "id": "parent",
            "status": "filled",
            "legs": [{"id": "child", "status": "new", "symbol": "AAPL", "stop_price": "98"}],
        }
        self.store.order(self.run, parent)
        self.store.order(self.run, parent)
        self.assertEqual(self.store.summary()["counts"]["events"], 2)
        parent["legs"][0]["status"] = "filled"
        parent["legs"][0]["filled_avg_price"] = "97.5"
        self.store.order(self.run, parent)
        self.assertEqual(self.store.summary()["counts"]["events"], 3)
        self.assertEqual(self.store.pending(self.run), [])

    def test_concurrent_writes_are_not_lost(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda i: self.store.record(self.run, "equity", {"value": i}), range(40)))
        self.assertEqual(self.store.summary()["counts"]["events"], 40)

    def test_backtest_inputs_and_report_can_be_restored(self):
        report = {"strategies": {}}
        provenance = self.store.finish_backtest(self.run, {"histories": {"AAPL": [100]}}, report)
        self.assertEqual(
            self.store.restore_dataset(provenance["input_sha256"]), {"histories": {"AAPL": [100]}}
        )
        event = self.store.summary(self.run)["events"][0]
        self.assertEqual(self.store.restore_dataset(event["payload"]["report_sha256"]), report)

    def test_ledger_failure_does_not_leave_backtest_running(self):
        analytics = Mock()
        analytics.start.side_effect = OSError("unavailable")
        jobs = BacktestJobs(analytics=analytics)
        jobs.current = {"state": "running"}
        jobs._run(["AAPL"], Config(end="2025-02-28"))
        self.assertEqual(jobs.status()["state"], "error")

    def test_failure_reporting_cannot_hide_calculation_error(self):
        analytics = Mock()
        analytics.record.side_effect = OSError("unavailable")
        jobs = BacktestJobs(analytics=analytics)
        with patch("equities.backtest_jobs.load_histories", side_effect=ValueError("bad data")):
            jobs._run(["AAPL"], Config(end="2025-02-28"))
        self.assertEqual(jobs.status()["state"], "error")
        self.assertEqual(jobs.status()["message"], "bad data")

    def test_fill_quality_links_to_original_quote_and_close_context_alias(self):
        self.store.execution_context(
            self.run, "broker-1", {"sent_at": "2024-01-02T14:30:00Z", "ask": 100, "bid": 99.9}
        )
        self.store.order(
            self.run,
            {
                "id": "broker-1",
                "client_order_id": "client-1",
                "side": "buy",
                "submitted_at": "2024-01-02T14:30:00Z",
                "status": "filled",
            },
        )
        self.store.fill(
            self.run,
            {
                "id": "fill-1",
                "order_id": "broker-1",
                "side": "buy",
                "qty": "2",
                "price": "100.1",
                "transaction_time": "2024-01-02T14:30:02Z",
            },
        )
        result = self.store.fills(self.run)[0]
        self.assertEqual(result["fill_delay_seconds"], 2)
        self.assertAlmostEqual(result["slippage_bps"], 10)
        self.assertEqual(len(self.store.executions(self.run)), 1)

    def test_research_archive_preserves_disk_margin(self):
        from types import SimpleNamespace

        research = Analytics(Path(self.temp.name) / "research.sqlite", reserve_bytes=1024)
        with patch("equities.analytics.shutil.disk_usage", return_value=SimpleNamespace(free=1)):
            with self.assertRaisesRegex(ValueError, "Espacio insuficiente"):
                research.dataset({"bars": [100]})
        self.assertEqual(research.summary()["counts"]["datasets"], 0)
