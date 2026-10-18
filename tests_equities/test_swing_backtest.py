import unittest
from dataclasses import replace

from test_backtest import config, history

from equities.backtest import Config, compare, simulate


class SwingBacktestTests(unittest.TestCase):
    def run_profile(self, data):
        return simulate({"AAPL": data}, replace(config(data), profile="swing"), "momentum")

    def test_swing_uses_capital_instead_of_budget(self):
        data = history([100] * 63 + [110] * 5)
        report = self.run_profile(data)
        buy = next(t for t in report["trades"] if t["side"] == "buy")
        self.assertEqual(buy["qty"], 9)
        self.assertGreater(buy["qty"] * buy["price"], 500)
        self.assertAlmostEqual(buy["stop_price"], 107.8)

    def test_gap_executes_stop_below_threshold_and_latches_global(self):
        data = history([100] * 63 + [110, 110, 90, 110, 110, 110])
        report = self.run_profile(data)
        sells = [t for t in report["trades"] if t["side"] == "sell"]
        self.assertEqual(sells[0]["price"], 90)
        self.assertIsNotNone(report["risk"]["account_stop_date"])
        self.assertEqual(sum(t["side"] == "buy" for t in report["trades"]), 1)
        self.assertEqual(report["open_positions"], {})

    def test_compare_reports_swing_assumptions(self):
        data = history([100] * 63 + [110] * 5)
        report = compare({"AAPL": data}, replace(config(data), profile="swing"))
        self.assertEqual(report["config"]["profile"], "swing")
        self.assertTrue(any("120 segundos" in item for item in report["assumptions"]))

    def test_unknown_profile_rejected(self):
        with self.assertRaises(ValueError):
            Config(profile="unknown")

    def test_cli_accepts_swing_profile(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        from equities.backtest_cli import main

        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "report.json"
            with (
                patch(
                    "sys.argv",
                    [
                        "backtest",
                        "--start",
                        "2025-01-01",
                        "--end",
                        "2025-02-01",
                        "--profile",
                        "swing",
                        "--output",
                        str(target),
                    ],
                ),
                patch("equities.backtest_cli.historical_client"),
                patch("equities.backtest_cli.load_histories", return_value={}) as loader,
                patch("equities.backtest_cli.compare", return_value={"strategies": {}}),
                patch("builtins.print"),
            ):
                main()
            self.assertEqual(loader.call_args.args[2].profile, "swing")
            self.assertTrue(target.exists())
