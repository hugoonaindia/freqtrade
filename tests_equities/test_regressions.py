"""Regression coverage for the five reviewed trading defects. No network calls."""

import threading
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from test_engine import Broker
from test_strategies import bars

from equities.engine import Engine
from equities.strategies import decide


NY = ZoneInfo("America/New_York")


class RegressionBroker(Broker):
    def __init__(self):
        super().__init__()
        self.now = datetime(2026, 10, 15, 12, tzinfo=NY)
        self.history_end = self.now.date() - timedelta(days=1)
        self.holidays = set()
        self.buying_power = "1000"
        self.marginable = "1000"

    def get(self, path, **params):
        if path == "/v2/clock":
            return {"is_open": self.open, "timestamp": self.now.isoformat()}
        if path == "/v2/calendar":
            start = datetime.fromisoformat(params["start"]).date()
            end = datetime.fromisoformat(params["end"]).date()
            return [
                {"date": (start + timedelta(days=i)).isoformat(), "open": "09:30", "close": "16:00"}
                for i in range((end - start).days + 1)
                if (start + timedelta(days=i)).weekday() < 5
                and start + timedelta(days=i) not in self.holidays
            ]
        if path.endswith("/bars"):
            dates = []
            day = self.history_end
            while len(dates) < len(self.closes):
                if day.weekday() < 5 and day not in self.holidays:
                    dates.append(day)
                day -= timedelta(days=1)
            return {
                "bars": [
                    {
                        "t": datetime.combine(day, datetime.min.time(), NY).isoformat(),
                        "c": value,
                        "h": value + 0.5,
                        "l": value - 0.5,
                    }
                    for day, value in zip(reversed(dates), self.closes, strict=True)
                ]
            }
        value = super().get(path, **params)
        if path == "/v2/account":
            value.update(
                buying_power=self.buying_power, non_marginable_buying_power=self.marginable
            )
        return value


class RegressionTests(unittest.TestCase):
    def setUp(self):
        self.broker = RegressionBroker()

    def test_stale_tab_cannot_execute_another_strategy(self):
        engine = Engine(self.broker, ["AAPL"], 550)
        engine.select_strategy("mean_reversion")
        with self.assertRaises(ValueError):
            engine.cycle(expected_strategy="sma", expected_revision=0)
        self.assertEqual(self.broker.sent, [])

    def test_switch_away_and_back_still_rejects_stale_revision(self):
        engine = Engine(self.broker, ["AAPL"], 550)
        engine.select_strategy("macd")
        engine.select_strategy("sma")
        with self.assertRaises(ValueError):
            engine.cycle(expected_strategy="sma", expected_revision=0)
        self.assertEqual(self.broker.sent, [])

    def test_available_buying_power_caps_new_order(self):
        self.broker.buying_power = "100"
        Engine(self.broker, ["AAPL"], 550).cycle()
        self.assertEqual(self.broker.sent, [])

    def test_other_symbol_limit_order_reserves_cash(self):
        self.broker.buying_power = "4000"
        self.broker.pending = [
            {
                "symbol": "MSFT",
                "side": "buy",
                "qty": "10",
                "filled_qty": "1",
                "limit_price": "100",
                "type": "limit",
            }
        ]
        Engine(self.broker, ["AAPL"], 550).cycle()
        self.assertEqual(self.broker.sent, [])

    def test_unknown_pending_market_cost_blocks_new_buys(self):
        self.broker.pending = [
            {
                "symbol": "MSFT",
                "side": "buy",
                "qty": "2",
                "filled_qty": "0",
                "type": "market",
                "limit_price": None,
            }
        ]
        Engine(self.broker, ["AAPL"], 550).cycle()
        self.assertEqual(self.broker.sent, [])

    def test_bollinger_recovery_above_middle_exits_existing_long(self):
        self.broker.closes = [100] * 20 + [80, 120]
        self.broker.positions = [{"symbol": "AAPL", "qty": "2"}]
        Engine(self.broker, ["AAPL"], 550, strategy="bollinger").cycle()
        self.assertEqual([o["side"] for o in self.broker.sent], ["sell"])

    def test_bollinger_does_not_buy_above_middle(self):
        self.assertEqual(decide("bollinger", bars([100] * 20 + [80, 120]))["signal"], "sell")

    def test_missing_latest_session_blocks_orders(self):
        self.broker.history_end = datetime(2026, 10, 12).date()
        Engine(self.broker, ["AAPL"], 550).cycle()
        self.assertEqual(self.broker.sent, [])

    def test_monday_accepts_friday_close(self):
        self.broker.now = datetime(2026, 10, 19, 12, tzinfo=NY)
        self.broker.history_end = datetime(2026, 10, 16).date()
        Engine(self.broker, ["AAPL"], 550).cycle()
        self.assertEqual(len(self.broker.sent), 1)

    def test_holiday_does_not_require_nonexistent_session(self):
        self.broker.now = datetime(2026, 12, 28, 12, tzinfo=NY)
        self.broker.history_end = datetime(2026, 12, 24).date()
        self.broker.holidays = {datetime(2026, 12, 25).date()}
        Engine(self.broker, ["AAPL"], 550).cycle()
        self.assertEqual(len(self.broker.sent), 1)

    def test_calendar_failure_does_not_fall_back_to_stale_data(self):
        original = self.broker.get

        def fail(path, **params):
            if path == "/v2/calendar":
                raise ValueError("calendar unavailable")
            return original(path, **params)

        self.broker.get = fail
        with self.assertRaises(ValueError):
            Engine(self.broker, ["AAPL"], 550).cycle()
        self.assertEqual(self.broker.sent, [])

    def test_worker_failure_updates_published_state(self):
        from equities.automation import Automation

        state = Automation(enabled=True)

        class FailingEngine:
            def cycle(self):
                raise ValueError("simulated broker error")

        state.run(FailingEngine(), threading.Event())
        status = state.snapshot()
        self.assertFalse(status["running"])
        self.assertEqual(status["state"], "error")
        self.assertTrue(status["error"])

    def test_non_marginable_buying_power_caps_order(self):
        self.broker.marginable = "100"
        Engine(self.broker, ["AAPL"], 550).cycle()
        self.assertEqual(self.broker.sent, [])

    def test_missing_buying_power_blocks_buys_but_allows_exit(self):
        original = self.broker.get

        def account_without_bp(path, **params):
            value = original(path, **params)
            if path == "/v2/account":
                value.pop("buying_power")
            return value

        self.broker.get = account_without_bp
        self.broker.closes = [100] * 24 + [90]
        self.broker.positions = [{"symbol": "AAPL", "qty": "2"}]
        Engine(self.broker, ["AAPL"], 550).cycle()
        self.assertEqual([o["side"] for o in self.broker.sent], ["sell"])

    def test_pending_notional_reduces_cash(self):
        self.broker.buying_power = "4000"
        self.broker.pending = [{"symbol": "MSFT", "side": "buy", "notional": "950"}]
        Engine(self.broker, ["AAPL"], 550).cycle()
        self.assertEqual(self.broker.sent, [])

    def test_after_close_requires_today_bar(self):
        self.broker.open = False
        self.broker.now = self.broker.now.replace(hour=17)
        row = Engine(self.broker, ["AAPL"], 550).cycle(execute=False)[0]
        self.assertEqual(row["signal"], "hold")
        self.assertEqual(self.broker.sent, [])

    def test_early_close_accepts_completed_today_bar_in_preview(self):
        self.broker.now = self.broker.now.replace(hour=14)
        self.broker.open = False
        self.broker.history_end = self.broker.now.date()
        original = self.broker.get

        def early_close(path, **params):
            value = original(path, **params)
            if path == "/v2/calendar":
                for day in value:
                    if day["date"] == self.broker.now.date().isoformat():
                        day["close"] = "13:00"
            return value

        self.broker.get = early_close
        row = Engine(self.broker, ["AAPL"], 550).cycle(execute=False)[0]
        self.assertEqual(row["signal"], "buy")
        self.assertEqual(self.broker.sent, [])

    def test_worker_normal_stop_is_published(self):
        from equities.automation import Automation

        state = Automation(enabled=True)
        stop = threading.Event()

        class EngineStopping:
            def cycle(self):
                stop.set()

        state.run(EngineStopping(), stop)
        self.assertEqual(state.snapshot()["state"], "stopped")
        self.assertFalse(state.snapshot()["running"])
        self.assertIsNotNone(state.snapshot()["last_cycle"])
