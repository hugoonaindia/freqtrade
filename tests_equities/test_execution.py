import unittest
from datetime import datetime, timedelta
from unittest.mock import patch
from test_backtest import history, config
from equities.backtest import prepare, compare
from equities.execution_replay import simulate_execution, ExecutionData
from equities.execution_quality import execution_quality


class ScenarioData:
    def __init__(self, histories, cfg):
        universe, self.days = prepare(histories, cfg, 64)
        self.archive = {"daily": {s: dict(bars) for s, bars in universe.items()}}
        self.asks = [100, 100, 100, 100]
        self.seconds = [0, 1, 60, 121]
        self.low = 99.5
        self.volume = 100000
        self.size = 100
        self.second_open = 100

    def bounds(self, day):
        opening = datetime.fromisoformat(day + "T14:30:00+00:00")
        return opening, opening + timedelta(minutes=4)

    def minutes(self, symbol, day):
        start, _ = self.bounds(day)
        opening = self.second_open if day == self.days[-1] else 100
        return [
            {
                "t": (start + timedelta(minutes=i)).isoformat(),
                "o": opening,
                "h": max(101, opening),
                "l": min(self.low, opening),
                "c": opening,
                "v": self.volume,
            }
            for i in range(4)
        ]

    def quotes(self, symbol, day):
        start, _ = self.bounds(day)
        return [
            {
                "t": (start + timedelta(seconds=second)).isoformat(),
                "ap": ask,
                "bp": ask - 0.1,
                "as": self.size,
            }
            for second, ask in zip(self.seconds, self.asks)
        ]


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.history = {"AAPL": history([100] * 66)}
        self.cfg = config(self.history["AAPL"], profile="swing", execution="intraday", spread_bps=0)
        self.universe, self.days = prepare(self.history, self.cfg, 64)
        self.data = ScenarioData(self.history, self.cfg)

    def run_scenario(self):
        with patch(
            "equities.execution_replay.decide", side_effect=[{"signal": "buy"}, {"signal": "hold"}]
        ):
            return simulate_execution(self.universe, self.days, self.cfg, "sma", self.data)

    def test_fill_has_causal_times_and_never_exceeds_limit(self):
        result = self.run_scenario()
        buy = result["trades"][0]
        self.assertLessEqual(buy["price"], buy["limit_price"])
        self.assertGreaterEqual(buy["timestamp"], buy["sent_at"])
        self.assertLess(buy["timestamp"], buy["deadline_at"])

    def test_touch_after_timeout_does_not_fill(self):
        self.data.asks = [100, 101, 101, 100]
        result = self.run_scenario()
        self.assertEqual(result["trades"], [])
        self.assertEqual(result["execution"]["orders"][0]["status"], "expired")

    def test_liquidity_cap_produces_partial_and_cancels_remainder(self):
        self.data.size = 1
        result = self.run_scenario()
        self.assertEqual(result["trades"][0]["qty"], 1)
        self.assertTrue(result["trades"][0]["partial"])
        self.assertEqual(result["execution"]["orders"][0]["status"], "partial_then_cancel_assumed")

    def test_wide_spread_rejects_entry(self):
        original = self.data.quotes

        def wide(s, d):
            return [{**q, "bp": 90} for q in original(s, d)]

        self.data.quotes = wide
        self.assertEqual(self.run_scenario()["trades"], [])

    def test_gap_stop_can_exceed_two_percent(self):
        self.data.second_open = 90
        result = self.run_scenario()
        self.assertEqual(result["trades"][-1]["price"], 90)
        self.assertIn(result["trades"][-1]["reason"], ("account_stop", "gap_stop"))

    def test_intraday_never_silently_falls_back(self):
        with self.assertRaisesRegex(ValueError, "Faltan datos intradía"):
            compare(self.history, self.cfg)

    def test_quality_uses_broker_fill_time_and_signed_slippage(self):
        context = {"ask": 100, "bid": 99.9, "deadline_at": "2024-01-02T14:32:00+00:00"}
        order = {
            "side": "buy",
            "submitted_at": "2024-01-02T14:30:00+00:00",
            "filled_at": "2024-01-02T14:32:01+00:00",
            "filled_avg_price": "100.1",
        }
        result = execution_quality(context, order)
        self.assertEqual(result["fill_delay_seconds"], 121)
        self.assertTrue(result["filled_after_deadline"])
        self.assertAlmostEqual(result["slippage_bps"], 10)

    def test_unknown_fill_time_is_not_replaced_with_observation_time(self):
        result = execution_quality(
            {"observed_at": "2024-01-02T15:00:00+00:00"},
            {"status": "partially_filled", "filled_qty": "2"},
        )
        self.assertIsNone(result["filled_at"])
        self.assertIsNone(result["fill_delay_seconds"])

    def test_missing_minute_is_rejected(self):
        provider = object.__new__(ExecutionData)
        provider.archive = {"minutes": {}}
        provider.config = self.cfg
        provider.calendar = {self.days[0]: {"open": "09:30", "close": "09:34"}}
        provider.progress = lambda m: None
        provider.pages = lambda *a, **k: []
        with self.assertRaisesRegex(ValueError, "faltan minutos"):
            provider.minutes("AAPL", self.days[0])

    def test_missing_initial_quote_is_rejected(self):
        provider = object.__new__(ExecutionData)
        provider.archive = {"quotes": {}}
        provider.calendar = {self.days[0]: {"open": "09:30", "close": "09:34"}}
        provider.progress = lambda m: None
        provider.pages = lambda *a, **k: []
        with self.assertRaisesRegex(ValueError, "cotización inicial fresca"):
            provider.quotes("AAPL", self.days[0])

    def test_historical_bounds_exclude_next_day_and_follow_dst(self):
        from equities.history_validation import historical_bounds

        winter = historical_bounds("2024-01-02", "2024-01-03")
        summer = historical_bounds("2024-07-01", "2024-07-01")
        self.assertEqual(winter["start"], "2024-01-02T05:00:00+00:00")
        self.assertEqual(winter["end"], "2024-01-04T04:59:59.999999+00:00")
        self.assertEqual(summer["start"], "2024-07-01T04:00:00+00:00")

    def test_zero_ask_is_not_executable_and_exclusion_is_recorded(self):
        provider = object.__new__(ExecutionData)
        provider.archive = {"quotes": {}}
        day = self.days[0]
        provider.calendar = {day: {"open": "09:30", "close": "09:34"}}
        provider.progress = lambda m: None
        opening, _ = provider.bounds(day)
        provider.pages = lambda *a, **k: [
            {"t": opening.isoformat(), "ap": 0, "bp": 100, "as": 0},
            {"t": (opening + timedelta(seconds=1)).isoformat(), "ap": 100, "bp": 99.9, "as": 1},
        ]
        quotes = provider.quotes("AAPL", day)
        self.assertEqual(len(quotes), 1)
        self.assertEqual(provider.archive["quality"]["AAPL:" + day]["non_executable_quotes"], 1)

    def test_comparison_preserves_nominal_benchmark_prices(self):
        report = compare(self.history, self.cfg, self.data, strategy="sma")
        self.assertEqual(list(report["strategies"]), ["sma"])
        self.assertEqual(report["validation"]["quality"], "intraday_scenario")

    def test_paginated_quotes_are_sampled_before_accumulating_raw_session(self):
        provider = object.__new__(ExecutionData)
        provider.config = self.cfg
        provider.archive = {}
        provider.rows = 0
        opening = datetime.fromisoformat(self.days[0] + "T14:30:00+00:00")
        pages = []
        for page in range(3):
            pages.append(
                {
                    "quotes": [
                        {
                            "t": (
                                opening + timedelta(microseconds=(page * 1000 + i) * 100)
                            ).isoformat(),
                            "ap": 100,
                            "bp": 99.9,
                            "as": 1,
                        }
                        for i in range(1000)
                    ],
                    "next_page_token": str(page + 1) if page < 2 else None,
                }
            )
        provider.read = lambda *a, **k: pages.pop(0)
        result = provider.pages(
            "AAPL",
            "quotes",
            start=opening.isoformat(),
            end=(opening + timedelta(minutes=4)).isoformat(),
        )
        self.assertEqual(provider.rows, 3000)
        self.assertLessEqual(len(result), 4)
        quality = provider.archive["quality"]["AAPL:" + self.days[0]]
        self.assertEqual(quality["received_quotes"], 3000)
        self.assertEqual(quality["sampled_out_quotes"], 3000 - len(result))
