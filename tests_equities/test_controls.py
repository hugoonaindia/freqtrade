import json
import tempfile
import threading
import unittest
from pathlib import Path

from test_engine import Broker

from equities.controls import Controls
from equities.engine import Engine


class ControlsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "settings.json"
        self.engine = Engine(Broker(), ["AAPL"], 500)
        self.engine.broker.configured = True
        self.controls = Controls(self.engine, settings_path=self.path)
        self.addCleanup(self.controls.close)

    def test_configuration_persists_without_orders(self):
        self.controls.configure({"strategy": "macd", "symbols": ["MSFT"], "budget": 300})
        self.assertEqual(self.engine.symbols, ["MSFT"])
        self.assertEqual(self.engine.budget, 300)
        self.assertEqual(json.loads(self.path.read_text())["strategy"], "macd")
        self.assertFalse(json.loads(self.path.read_text())["auto"])
        self.assertEqual(self.engine.broker.sent, [])

    def test_invalid_configuration_leaves_selection_unchanged(self):
        for budget in [0, float("nan"), True, "500"]:
            with self.assertRaises(ValueError):
                self.controls.configure({"strategy": "macd", "symbols": ["MSFT"], "budget": budget})
        self.assertEqual(self.engine.strategy, "sma")

    def test_start_stop_prevents_overlap_and_edits_during_cycle(self):
        entered, release = threading.Event(), threading.Event()

        def cycle(**kwargs):
            entered.set()
            release.wait(3)
            return []

        self.engine.cycle = cycle
        self.controls.start(self.engine.selection())
        self.assertTrue(entered.wait(1))
        with self.assertRaises(RuntimeError):
            self.controls.start(self.engine.selection())
        with self.assertRaises(RuntimeError):
            self.controls.configure({"strategy": "macd", "symbols": ["MSFT"], "budget": 300})
        with self.assertRaises(RuntimeError):
            self.controls.cycle(True, self.engine.selection())
        with self.assertRaises(RuntimeError):
            self.controls.cycle(False, self.engine.selection())
        self.controls.stop()
        self.assertFalse(json.loads(self.path.read_text())["auto"])
        self.assertTrue(self.controls.snapshot()["running"])
        release.set()
        self.controls.close()
        self.assertFalse(self.controls.snapshot()["running"])
        self.controls.configure({"strategy": "macd", "symbols": ["MSFT"], "budget": 300})

    def test_stale_start_cannot_trade(self):
        from equities.engine import SelectionChanged

        self.controls.configure({"strategy": "macd", "symbols": ["MSFT"], "budget": 300})
        with self.assertRaises(SelectionChanged):
            self.controls.start({"strategy": "sma", "revision": 0})
        self.assertEqual(self.engine.broker.sent, [])

    def test_history_cutoff_filters_only_display_and_keeps_guard(self):
        self.engine.seen.add("keep")
        self.controls.clear_history()
        self.assertEqual(self.controls.visible_orders([{"created_at": "2020-01-01T00:00:00Z"}]), [])
        self.assertEqual(self.engine.seen, {"keep"})
        self.assertIn("history_since", json.loads(self.path.read_text()))

    def test_worker_failure_disables_autostart(self):
        def fail():
            raise ValueError("broker unavailable")

        self.engine.cycle = fail
        self.controls.start(self.engine.selection())
        self.controls.close()
        self.assertEqual(self.controls.snapshot()["state"], "error")
        self.assertFalse(json.loads(self.path.read_text())["auto"])

    def test_stop_signals_worker_even_if_settings_write_fails(self):
        from unittest.mock import patch

        entered, release = threading.Event(), threading.Event()

        def cycle():
            entered.set()
            release.wait(2)

        self.engine.cycle = cycle
        self.controls.start(self.engine.selection())
        self.assertTrue(entered.wait(1))
        with patch.object(self.controls, "_save", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.controls.stop()
            self.assertTrue(self.controls.event.is_set())
        release.set()
