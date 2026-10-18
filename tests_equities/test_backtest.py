import unittest
from datetime import date, timedelta

from equities.backtest import Config, compare, simulate
from equities.backtest_metrics import capture, metrics, protection


def history(closes, opens=None, begin=date(2025, 1, 1)):
    return [
        {
            "t": (begin + timedelta(days=i)).isoformat() + "T12:00:00Z",
            "o": opens[i] if opens else c,
            "c": c,
            "h": max(c, opens[i] if opens else c) + 1,
            "l": min(c, opens[i] if opens else c) - 1,
        }
        for i, c in enumerate(closes)
    ]


def config(data, **kw):
    return Config(
        start=data[64]["t"][:10],
        end=data[-1]["t"][:10],
        capital=kw.pop("capital", 1000),
        budget=kw.pop("budget", 500),
        fee_bps=kw.pop("fee_bps", 0),
        slippage_bps=kw.pop("slippage_bps", 0),
        **kw,
    )


class BacktestTests(unittest.TestCase):
    def test_trade_fills_next_open_not_signal_close(self):
        values = [100] * 64 + [110, 120, 125]
        data = history(values, opens=[100] * 65 + [115, 123])
        result = simulate({"AAPL": data}, config(data), "sma")
        trade = result["trades"][0]
        self.assertEqual(trade["signal_date"], data[64]["t"][:10])
        self.assertEqual(trade["date"], data[65]["t"][:10])
        self.assertEqual(trade["price"], 115)
        self.assertEqual(trade["qty"], 4)

    def test_future_prices_do_not_change_past_trades(self):
        original = history([100] * 64 + [110, 120, 125, 130])
        changed = history([100] * 64 + [110, 120, 125, 20])
        first = simulate({"AAPL": original}, config(original), "sma")
        second = simulate({"AAPL": changed}, config(changed), "sma")
        self.assertEqual(first["trades"], second["trades"])
        self.assertEqual(first["curve"][:-1], second["curve"][:-1])

    def test_costs_and_slippage_reduce_cash(self):
        data = history([100] * 64 + [110, 120, 125])
        raw = simulate({"AAPL": data}, config(data), "sma")
        costly = simulate({"AAPL": data}, config(data, fee_bps=10, slippage_bps=10), "sma")
        self.assertLess(costly["metrics"]["final_equity"], raw["metrics"]["final_equity"])
        self.assertGreater(costly["trades"][0]["fee"], 0)
        self.assertGreater(costly["trades"][0]["price"], 120)

    def test_gap_caps_by_cash_without_negative_balance(self):
        data = history([100] * 64 + [110, 120, 125], opens=[100] * 65 + [900, 123])
        result = simulate({"AAPL": data}, config(data), "sma")
        self.assertEqual(result["trades"][0]["qty"], 1)
        self.assertTrue(all(p["cash"] >= 0 for p in result["curve"]))

    def test_bearish_signal_never_opens_short(self):
        data = history([100] * 64 + [90, 80, 75])
        result = simulate({"AAPL": data}, config(data), "sma")
        self.assertEqual(result["trades"], [])
        self.assertEqual(result["metrics"]["final_equity"], 1000)

    def test_last_signal_cannot_fill_without_next_session(self):
        data = history([100] * 64 + [100, 110])
        self.assertEqual(simulate({"AAPL": data}, config(data), "sma")["trades"], [])

    def test_compare_has_six_strategies_and_two_references(self):
        data = history([100] * 64 + [110, 120, 125])
        result = compare({"AAPL": data}, config(data))
        self.assertEqual(len(result["strategies"]), 6)
        self.assertEqual(set(result["benchmarks"]), {"allocated", "fully_invested"})
        self.assertIn("drawdown_reduction_pp", result["strategies"]["sma"]["protection"])

    def test_missing_evaluation_date_rejected(self):
        one = history([100] * 70)
        two = one[:65] + one[66:]
        with self.assertRaises(ValueError):
            compare({"AAPL": one, "MSFT": two}, config(one))

    def test_invalid_ohlc_rejected(self):
        data = history([100] * 70)
        data[66]["o"] = float("nan")
        with self.assertRaises(ValueError):
            simulate({"AAPL": data}, config(data), "sma")

    def test_warmup_required(self):
        data = history([100] * 70)
        cfg = Config(start=data[2]["t"][:10], end=data[-1]["t"][:10])
        with self.assertRaises(ValueError):
            compare({"AAPL": data}, cfg)

    def test_invalid_config(self):
        for kwargs in [
            {"capital": -1},
            {"budget": float("nan")},
            {"fee_bps": -1},
            {"start": "2026-02-01", "end": "2025-01-01"},
        ]:
            with self.assertRaises(ValueError):
                Config(**kwargs)

    def test_momentum_buys_only_positive_leader(self):
        a = history([100] * 64 + [110, 111, 112])
        b = history([100] * 64 + [120, 121, 122])
        result = simulate({"AAPL": a, "MSFT": b}, config(a), "momentum")
        self.assertEqual([t["symbol"] for t in result["trades"] if t["side"] == "buy"], ["MSFT"])

    def test_budget_reference_does_not_invest_all_capital(self):
        data = history([100] * 64 + [100, 100, 100])
        result = compare({"AAPL": data}, config(data, capital=10000, budget=500))
        primary = result["benchmarks"]["allocated"]
        self.assertEqual(primary["trades"][0]["qty"], 5)
        self.assertAlmostEqual(primary["metrics"]["average_exposure_pct"], 5)
        self.assertGreater(
            result["benchmarks"]["fully_invested"]["metrics"]["average_exposure_pct"], 90
        )


class MetricTests(unittest.TestCase):
    def test_drawdown_includes_starting_capital(self):
        out = metrics(
            [
                {"date": "2025-01-02", "equity": 90, "cash": 90, "exposure": 0},
                {"date": "2025-01-03", "equity": 80, "cash": 80, "exposure": 0},
            ],
            100,
            [],
        )
        self.assertAlmostEqual(out["max_drawdown_pct"], 20)
        self.assertFalse(out["recovered"])
        self.assertEqual(out["underwater_sessions"], 2)

    def test_drawdown_reduction_relative_and_points(self):
        out = protection({"max_drawdown_pct": 12}, {"max_drawdown_pct": 30})
        self.assertAlmostEqual(out["drawdown_reduction_pp"], 18)
        self.assertAlmostEqual(out["drawdown_reduction_pct"], 60)

    def test_zero_baseline_drawdown_is_undefined_relative_reduction(self):
        out = protection({"max_drawdown_pct": 0}, {"max_drawdown_pct": 0})
        self.assertIsNone(out["drawdown_reduction_pct"])

    def test_recovery_and_worst_month(self):
        curve = [
            {"date": day, "equity": v, "cash": v, "exposure": 0}
            for day, v in [("2025-01-02", 100), ("2025-01-03", 80), ("2025-02-03", 100)]
        ]
        out = metrics(curve, 100, [])
        self.assertTrue(out["recovered"])
        self.assertAlmostEqual(out["worst_month_pct"], -20)


class CaptureTests(unittest.TestCase):
    def test_half_of_reference_loss_has_fifty_percent_capture(self):
        own = [{"date": "2025-01-01", "equity": 90}]
        reference = [{"date": "2025-01-01", "equity": 80}]
        result = capture(own, reference, 100)
        self.assertAlmostEqual(result["downside_capture_daily_pct"], 50)
        self.assertIsNone(result["upside_capture_daily_pct"])

    def test_positive_strategy_on_negative_reference_has_negative_capture(self):
        own = [{"date": "2025-01-01", "equity": 110}]
        reference = [{"date": "2025-01-01", "equity": 80}]
        self.assertAlmostEqual(capture(own, reference, 100)["downside_capture_daily_pct"], -50)
