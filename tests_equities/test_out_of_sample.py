import unittest
from copy import deepcopy
from datetime import date, timedelta
from unittest.mock import patch

from test_backtest_data import HistoryBroker
from equities.backtest import Config
from equities.backtest_data import load_histories
from equities.backtest_jobs import BacktestJobs
from equities.out_of_sample import evaluate_holdout, validate_holdout


class HoldoutTests(unittest.TestCase):
    def setUp(self):
        self.broker = HistoryBroker()
        self.broker.days = []
        day = date(2023, 9, 1)
        while day <= date(2025, 2, 28):
            if day.weekday() < 5:
                self.broker.days.append(day.isoformat())
            day += timedelta(days=1)
        self.config = Config(start="2024-01-02", end="2025-02-28", profile="swing")
        self.spec = {"start": "2024-02-01", "strategy": "sma"}
        self.data = load_histories(self.broker, ["AAPL"], self.config)

    def test_disjoint_periods_and_prespecified_strategy(self):
        report = evaluate_holdout(self.data, self.config, self.spec)
        development = report["out_of_sample"]["development"]
        self.assertLess(development["actual_end"], report["actual_start"])
        self.assertEqual(list(report["strategies"]), ["sma"])
        self.assertTrue(report["validation"]["out_of_sample"])
        self.assertEqual(report["config"]["capital"], development["config"]["capital"])
        for trade in report["strategies"]["sma"]["trades"]:
            self.assertGreaterEqual(trade["date"], self.spec["start"])

    def test_future_changes_cannot_change_development(self):
        first = evaluate_holdout(self.data, self.config, self.spec)
        changed = deepcopy(self.data)
        for bar in changed["AAPL"]:
            if bar["t"][:10] >= self.spec["start"]:
                bar.update(o=150, h=151, l=149, c=150)
        second = evaluate_holdout(changed, self.config, self.spec)
        self.assertEqual(
            first["out_of_sample"]["development"], second["out_of_sample"]["development"]
        )

    def test_development_does_not_receive_reserved_bars(self):
        from equities.backtest import compare

        with patch("equities.out_of_sample.compare", wraps=compare) as simulator:
            evaluate_holdout(self.data, self.config, self.spec)
        received = simulator.call_args_list[0].args[0]["AAPL"]
        self.assertTrue(all(bar["t"][:10] < self.spec["start"] for bar in received))

    def test_invalid_reserve_rejected_before_starting_job(self):
        for spec in (
            {"start": "2024-01-02", "strategy": "sma"},
            {"start": "2026-01-01", "strategy": "sma"},
            {"start": "2024-02-01", "strategy": "unknown"},
            {"start": "2024-02-01"},
            False,
        ):
            with self.subTest(spec=spec), self.assertRaises(ValueError):
                validate_holdout(self.config, spec)

    def test_too_short_development_rejected(self):
        with self.assertRaises(ValueError):
            evaluate_holdout(self.data, self.config, {"start": "2024-01-03", "strategy": "sma"})

    def test_async_job_records_holdout_without_orders(self):
        jobs = BacktestJobs(lambda: self.broker)
        job = jobs.start(
            {
                "symbols": ["AAPL"],
                "start": self.config.start,
                "end": self.config.end,
                "out_of_sample": self.spec,
            }
        )
        jobs.wait(10)
        self.assertEqual(jobs.status()["state"], "complete")
        self.assertEqual(jobs.result(job["id"])["out_of_sample"]["selected_strategy"], "sma")
