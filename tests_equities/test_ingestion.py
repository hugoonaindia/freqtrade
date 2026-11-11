import unittest
from copy import deepcopy
from datetime import timedelta

from test_engine import Broker
from equities.engine import Engine
from equities.history_validation import validate_daily


class IngestionTests(unittest.TestCase):
    def setUp(self):
        self.broker = Broker()
        self.engine = Engine(self.broker, ["AAPL"], 500)
        self.end = self.broker.now.date() - timedelta(days=1)
        self.data = self.broker.get("/v2/stocks/AAPL/bars")["bars"]
        self.calendar = self.broker.get(
            "/v2/calendar", start=self.data[0]["t"][:10], end=self.end.isoformat()
        )

    def assert_rejected_in_trading_and_shared_validator(self, bars):
        with self.assertRaises(ValueError):
            validate_daily("AAPL", bars, calendar=self.calendar, end=self.end.isoformat())
        original = self.broker.get

        def corrupt(path, **params):
            if path.endswith("/bars"):
                return {"bars": bars}
            return original(path, **params)

        self.broker.get = corrupt
        with self.assertRaises(ValueError):
            self.engine.cycle()
        self.assertEqual(self.broker.sent, [])

    def test_internal_gap_blocks_trading(self):
        del self.data[10]
        self.assert_rejected_in_trading_and_shared_validator(self.data)

    def test_duplicate_blocks_trading(self):
        self.data.append(deepcopy(self.data[10]))
        self.assert_rejected_in_trading_and_shared_validator(self.data)

    def test_missing_timezone_blocks_trading(self):
        self.data[10]["t"] = self.data[10]["t"][:19]
        self.assert_rejected_in_trading_and_shared_validator(self.data)

    def test_inconsistent_open_blocks_trading(self):
        self.data[10]["o"] = self.data[10]["h"] + 10
        self.assert_rejected_in_trading_and_shared_validator(self.data)

    def test_nonfinite_price_blocks_trading(self):
        self.data[10]["c"] = float("nan")
        self.assert_rejected_in_trading_and_shared_validator(self.data)

    def test_missing_ohlc_field_blocks_trading(self):
        del self.data[10]["o"]
        self.assert_rejected_in_trading_and_shared_validator(self.data)

    def test_empty_calendar_blocks_trading(self):
        with self.assertRaises(ValueError):
            validate_daily("AAPL", self.data, calendar=[], end=self.end.isoformat())

    def test_valid_history_is_sorted_without_filling_gaps(self):
        original = self.broker.get

        def reversed_history(path, **params):
            if path.endswith("/bars"):
                return {"bars": list(reversed(self.data))}
            return original(path, **params)

        self.broker.get = reversed_history
        result = self.engine._history("AAPL", self.broker.now.date(), self.end)
        self.assertEqual(result, self.data)

    def test_gap_explanation_identifies_symbol_date_and_action(self):
        del self.data[10]
        with self.assertRaises(ValueError) as ctx:
            validate_daily("AAPL", self.data, calendar=self.calendar, end=self.end.isoformat())
        message = str(ctx.exception)
        self.assertIn("AAPL", message)
        self.assertIn("faltan 1 sesiones", message)
        self.assertIn("primera:", message)
        self.assertIn("Vuelve a descargar", message)

    def test_worker_retains_safe_data_error_explanation(self):
        import threading
        from unittest.mock import Mock
        from equities.automation import Automation
        from equities.history_validation import HistoryError

        engine = Mock()
        engine.cycle.side_effect = HistoryError("AAPL: falta la sesión 2026-10-14.")
        worker = Automation()
        worker.run(engine, threading.Event())
        self.assertIn("AAPL: falta la sesión 2026-10-14", worker.snapshot()["error"])

    def test_worker_does_not_expose_unclassified_exception(self):
        import threading
        from unittest.mock import Mock
        from equities.automation import Automation

        engine = Mock()
        engine.cycle.side_effect = ValueError("private payload")
        worker = Automation()
        worker.run(engine, threading.Event())
        self.assertNotIn("private payload", worker.snapshot()["error"])
