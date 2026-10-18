import unittest
from datetime import UTC, datetime, timedelta

from equities.strategies import STRATEGIES, decide, rotation


def bars(closes):
    return [
        {
            "c": c,
            "h": c + 0.5,
            "l": c - 0.5,
            "t": (datetime(2026, 1, 1, 12, tzinfo=UTC) + timedelta(days=i)).isoformat(),
        }
        for i, c in enumerate(closes)
    ]


class StrategyTests(unittest.TestCase):
    def action(self, name, closes):
        return decide(name, bars(closes))["signal"]

    def test_catalog_has_six(self):
        self.assertEqual(
            set(STRATEGIES), {"sma", "mean_reversion", "bollinger", "breakout", "momentum", "macd"}
        )

    def test_sma_requires_cross(self):
        self.assertEqual(self.action("sma", [100] * 20 + [110]), "buy")
        self.assertEqual(self.action("sma", [100] * 20 + [90]), "sell")
        self.assertEqual(self.action("sma", list(range(100, 125))), "hold")

    def test_mean_reversion(self):
        self.assertEqual(self.action("mean_reversion", [100] * 19 + [90]), "buy")
        self.assertEqual(self.action("mean_reversion", [100] * 19 + [110]), "sell")
        self.assertEqual(self.action("mean_reversion", [100] * 20), "hold")

    def test_bollinger_requires_recovery(self):
        self.assertEqual(self.action("bollinger", [100] * 20 + [80, 90]), "buy")
        self.assertEqual(self.action("bollinger", [100] * 20 + [80]), "hold")
        self.assertEqual(self.action("bollinger", [100] * 20 + [110]), "sell")

    def test_breakout_uses_previous_highs(self):
        self.assertEqual(self.action("breakout", [100] * 20 + [101]), "buy")
        self.assertEqual(self.action("breakout", [100] * 20 + [99]), "sell")
        data = bars([100] * 20 + [101])
        for b in data[:-1]:
            b["h"] = 105
        self.assertEqual(decide("breakout", data)["signal"], "hold")

    def test_macd_cross(self):
        self.assertEqual(self.action("macd", [100] * 34 + [101]), "buy")
        self.assertEqual(self.action("macd", [100] * 34 + [99]), "sell")
        self.assertEqual(self.action("macd", [100] * 35), "hold")

    def test_warmup_and_nonfinite_hold(self):
        for name in STRATEGIES:
            self.assertEqual(self.action(name, [100] * 5), "hold")
            for bad in [float("nan"), float("inf"), -1, 0]:
                self.assertEqual(self.action(name, [100] * 70 + [bad]), "hold")

    def test_breakout_rejects_invalid_ohlc(self):
        data = bars([100] * 20 + [110])
        data[0]["h"] = float("nan")
        self.assertEqual(decide("breakout", data)["signal"], "hold")

    def test_momentum_selects_one_positive_winner(self):
        result = rotation({"AAPL": bars([100] * 63 + [110]), "MSFT": bars([100] * 63 + [120])})
        self.assertEqual(result["AAPL"]["signal"], "sell")
        self.assertEqual(result["MSFT"]["signal"], "buy")

    def test_momentum_no_positive_returns_exits(self):
        result = rotation({"AAPL": bars([100] * 63 + [90])})
        self.assertEqual(result["AAPL"]["signal"], "sell")

    def test_momentum_incomplete_universe_holds_everyone(self):
        result = rotation({"AAPL": bars([100] * 63 + [110]), "MSFT": bars([100] * 4)})
        self.assertTrue(all(v["signal"] == "hold" for v in result.values()))

    def test_momentum_tie_is_deterministic(self):
        result = rotation({"MSFT": bars([100] * 63 + [110]), "AAPL": bars([100] * 63 + [110])})
        self.assertEqual(result["AAPL"]["signal"], "buy")
        self.assertEqual(result["MSFT"]["signal"], "sell")

    def test_unknown_strategy_rejected(self):
        with self.assertRaises(ValueError):
            decide("unknown", bars([100] * 70))


class MomentumAlignmentTests(unittest.TestCase):
    def test_missing_latest_session_holds_universe(self):
        aapl = bars([100] * 64 + [110])[1:]
        msft = bars([100] * 64 + [120])[:-1]
        results = rotation({"AAPL": aapl, "MSFT": msft})
        self.assertTrue(all(r["signal"] == "hold" for r in results.values()))

    def test_different_middle_session_holds_universe(self):
        aapl = bars([100] * 64 + [110])
        msft = bars([100] * 64 + [120])
        del aapl[30]
        del msft[31]
        results = rotation({"AAPL": aapl, "MSFT": msft})
        self.assertTrue(all(r["signal"] == "hold" for r in results.values()))
