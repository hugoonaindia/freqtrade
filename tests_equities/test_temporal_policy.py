import unittest
from datetime import date
from equities.backtest import Config
from equities.out_of_sample import validate_holdout
from equities.temporal_policy import temporal_policy, enforce_development, year_before


class TemporalPolicyTests(unittest.TestCase):
    def test_development_preserves_recent_year_plus_session_buffer(self):
        policy = temporal_policy(date(2026, 10, 18))
        self.assertEqual(policy["reserve_start"], "2025-10-11")
        self.assertEqual(policy["development_end_max"], "2025-10-10")
        enforce_development(Config(end="2025-10-10"), policy)
        with self.assertRaises(ValueError):
            enforce_development(Config(end="2025-10-11"), policy)

    def test_leap_day_anniversary(self):
        self.assertEqual(year_before(date(2024, 2, 29)), date(2023, 2, 28))

    def test_short_holdout_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "año natural completo"):
            validate_holdout(
                Config(start="2022-01-01", end="2025-12-31"),
                {"start": "2025-01-01", "strategy": "sma"},
            )

    def test_full_anniversary_is_accepted(self):
        self.assertEqual(
            validate_holdout(
                Config(start="2022-01-01", end="2025-12-31"),
                {"start": "2024-12-31", "strategy": "sma"},
            )["start"],
            "2024-12-31",
        )

    def test_holdout_does_not_allow_recent_development(self):
        with self.assertRaisesRegex(ValueError, "último año"):
            validate_holdout(
                Config(start="2022-01-01", end="2027-12-31"),
                {"start": "2026-12-31", "strategy": "sma"},
            )
