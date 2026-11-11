import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from test_engine import Broker

from equities.broker import Alpaca
from equities.swing import SwingEngine


class PaperBroker(Broker):
    configured = True
    allow_protected_entries = False

    def __init__(self):
        super().__init__()
        self.equity = 1000
        self.deleted = []
        self.account_id = "paper-test"

    def get(self, path, **kwargs):
        if path == "/v2/account":
            return {
                **super().get(path, **kwargs),
                "id": self.account_id,
                "equity": str(self.equity),
                "non_marginable_buying_power": self.cash,
            }
        if path.endswith("/quotes/latest"):
            return {"quote": {"t": self.now.isoformat(), "ap": 110, "bp": 109.9}}
        return super().get(path, **kwargs)

    def delete(self, path):
        self.deleted.append(path)
        return {"id": "close-test"}


def position(qty="4", price="100"):
    return {
        "symbol": "AAPL",
        "asset_class": "us_equity",
        "qty": qty,
        "avg_entry_price": price,
        "current_price": price,
    }


def stop(qty="4", price="98"):
    return {
        "id": "stop-1",
        "symbol": "AAPL",
        "side": "sell",
        "type": "stop",
        "qty": qty,
        "filled_qty": "0",
        "stop_price": price,
        "time_in_force": "gtc",
        "status": "new",
    }


class SwingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.broker = PaperBroker()
        self.engine = self.make_engine()

    def make_engine(self):
        return SwingEngine(
            self.broker,
            ["AAPL"],
            500,
            self.root / "orders.json",
            campaign_path=self.root / "campaign.json",
        )

    def test_start_records_baseline_without_orders(self):
        self.engine.arm()
        self.assertEqual(self.engine.risk_snapshot()["baseline"], 1000)
        self.assertTrue(self.engine.entries_enabled)
        self.assertTrue(self.broker.allow_protected_entries)
        self.assertEqual(self.broker.sent, [])

    def test_first_campaign_rejects_existing_positions(self):
        self.broker.positions = [position()]
        with self.assertRaises(ValueError):
            self.engine.arm()
        self.assertIsNone(self.engine.campaign)
        self.assertFalse(self.engine.entries_enabled)

    def test_first_campaign_rejects_existing_orders(self):
        self.broker.pending = [stop()]
        with self.assertRaises(ValueError):
            self.engine.arm()
        self.assertFalse((self.root / "campaign.json").exists())

    def test_baseline_survives_pause_and_restart(self):
        self.engine.arm()
        self.engine.disarm()
        self.broker.equity = 990
        restarted = self.make_engine()
        restarted.arm()
        self.assertEqual(restarted.campaign["baseline"], 1000)

    def test_global_threshold_latches_and_cannot_be_reset_by_recovery(self):
        self.engine.arm()
        self.broker.equity = 980
        self.engine.monitor()
        self.assertTrue(self.engine.campaign["tripped"])
        self.assertFalse(self.engine.entries_enabled)
        self.broker.equity = 1100
        restarted = self.make_engine()
        with self.assertRaises(ValueError):
            restarted.arm()
        self.assertTrue(json.loads((self.root / "campaign.json").read_text())["tripped"])

    def test_global_stop_cancels_protection_before_close(self):
        self.engine.arm()
        self.broker.positions = [position()]
        self.broker.pending = [stop()]
        self.broker.equity = 979
        self.engine.monitor()
        self.assertEqual(self.broker.deleted, ["/v2/orders/stop-1"])
        self.broker.pending = []
        self.engine.monitor()
        self.assertIn("/v2/positions/AAPL", self.broker.deleted)
        with self.assertRaises(ValueError):
            self.engine.monitor()
        self.assertEqual(self.broker.deleted.count("/v2/positions/AAPL"), 1)

    def test_closed_market_preserves_protection_after_global_trip(self):
        self.engine.arm()
        self.broker.positions = [position()]
        self.broker.pending = [stop()]
        self.broker.open = False
        self.broker.equity = 970
        self.engine.monitor()
        self.assertEqual(self.broker.deleted, [])
        self.assertTrue(self.engine.campaign["tripped"])

    def test_individual_protection_persists_while_entries_paused(self):
        self.engine.arm()
        self.engine.disarm()
        self.broker.positions = [position()]
        with self.assertRaises(ValueError):
            self.engine.monitor()
        self.assertEqual(self.broker.sent[0]["type"], "stop")
        self.assertEqual(self.broker.sent[0]["stop_price"], "98.00")
        with self.assertRaises(ValueError):
            self.engine.monitor()
        self.assertEqual(len(self.broker.sent), 1)

    def test_existing_sufficient_stop_does_not_duplicate(self):
        self.engine.arm()
        self.broker.positions = [position()]
        self.broker.pending = [stop()]
        self.engine.monitor()
        self.assertEqual(self.broker.sent, [])
        self.assertEqual(self.broker.deleted, [])

    def test_insufficient_stop_blocks_entries(self):
        self.engine.arm()
        self.broker.positions = [position()]
        self.broker.pending = [stop(qty="1")]
        with self.assertRaises(ValueError):
            self.engine.monitor()
        self.assertFalse(self.engine.entries_enabled)
        self.assertIsNotNone(self.engine.risk_snapshot()["monitor_error"])

    def test_account_change_blocks_entries(self):
        self.engine.arm()
        self.broker.account_id = "other"
        with self.assertRaises(ValueError):
            self.engine.monitor()
        self.assertFalse(self.engine.entries_enabled)

    def test_entry_uses_full_available_capital_with_attached_stop(self):
        self.engine.arm()
        self.engine.cycle()
        self.assertEqual(len(self.broker.sent), 1)
        order = self.broker.sent[0]
        self.assertEqual(order["qty"], "9")
        self.assertEqual(order["order_class"], "oto")
        self.assertEqual(order["stop_loss"]["stop_price"], "107.80")
        self.engine.cycle()
        self.assertEqual(len(self.broker.sent), 1)

    def test_sell_intent_survives_cancel_and_does_not_recreate_stop(self):
        self.engine.arm()
        self.broker.positions = [position()]
        self.broker.pending = [stop()]
        self.broker.closes = [100] * 24 + [90]
        self.engine.cycle()
        self.assertIn("AAPL", self.engine.campaign["closing"])
        self.broker.pending = []
        self.engine.monitor()
        self.assertIn("/v2/positions/AAPL", self.broker.deleted)
        self.assertEqual(self.broker.sent, [])

    def test_preview_never_arms_or_submits(self):
        self.engine.cycle(execute=False)
        self.assertIsNone(self.engine.campaign)
        self.assertFalse(self.engine.entries_enabled)
        self.assertEqual(self.broker.sent, [])

    def test_ambiguous_entry_is_not_retried(self):
        self.engine.arm()
        self.broker.post = Mock(side_effect=ValueError("timeout"))
        with self.assertRaises(ValueError):
            self.engine.cycle()
        self.engine.cycle()
        self.assertEqual(self.broker.post.call_count, 1)

    def test_broker_rejects_unprotected_buy_even_when_armed(self):
        credentials = self.root / "keys.json"
        credentials.write_text(json.dumps({"key": "test", "secret": "test"}))
        broker = Alpaca(credentials)
        broker.allow_protected_entries = True
        broker.opener = Mock()
        with self.assertRaises(ValueError):
            broker.post("/v2/orders", {"side": "buy", "qty": "2"})
        broker.opener.open.assert_not_called()

    def test_reset_requires_flat_account_and_keeps_previous_campaign_archived(self):
        self.engine.arm()
        self.broker.positions = [position()]
        with self.assertRaises(ValueError):
            self.engine.reset_campaign()
        self.assertIsNotNone(self.engine.campaign)
        self.broker.positions = []
        self.engine.reset_campaign()
        self.assertIsNone(self.engine.campaign)
        self.assertEqual(len(list(self.root.glob("campaign-*.json"))), 1)
        self.assertIsNone(self.make_engine().campaign)

    def test_pausing_cancels_pending_entries_on_monitor(self):
        self.engine.arm()
        self.broker.pending = [{"id": "buy-1", "side": "buy", "symbol": "AAPL"}]
        self.engine.disarm()
        with self.assertRaises(ValueError):
            self.engine.monitor()
        self.assertEqual(self.broker.deleted, ["/v2/orders/buy-1"])

    def test_stale_quote_blocks_new_order(self):
        from datetime import timedelta
        from unittest.mock import patch

        self.engine.arm()
        original = self.broker.get

        def stale(path, **kwargs):
            data = original(path, **kwargs)
            if path.endswith("/quotes/latest"):
                data["quote"]["t"] = (self.broker.now - timedelta(minutes=5)).isoformat()
            return data

        with patch.object(self.broker, "get", side_effect=stale):
            with self.assertRaises(ValueError):
                self.engine.cycle()
        self.assertEqual(self.broker.sent, [])

    def test_risk_guard_keeps_running_after_pause(self):
        self.engine.arm()
        self.engine.disarm()
        self.broker.equity = 970
        self.engine.monitor()
        self.assertTrue(self.engine.campaign["tripped"])

    def test_arm_does_not_resume_unfinished_manual_liquidation(self):
        self.engine.arm()
        self.engine.campaign["closing_all"] = True
        with self.assertRaises(ValueError):
            self.engine.arm()

    def test_real_broker_allows_only_protected_entry_after_arming(self):
        from test_broker import Opener

        credentials = self.root / "keys.json"
        credentials.write_text(json.dumps({"key": "test", "secret": "test"}))
        broker = Alpaca(credentials)
        broker.allow_protected_entries = True
        broker.opener = Opener()
        broker.post(
            "/v2/orders",
            {
                "side": "buy",
                "qty": "2",
                "type": "limit",
                "limit_price": "100",
                "stop_loss": {"stop_price": "98"},
                "time_in_force": "gtc",
                "order_class": "oto",
            },
        )
        self.assertEqual(len(broker.opener.calls), 1)

    def test_ledger_failure_blocks_entries_but_not_global_exit(self):
        self.engine.arm()
        self.engine.analytics.record = Mock(side_effect=OSError("disk unavailable"))
        self.engine.analytics.order = Mock(side_effect=OSError("disk unavailable"))
        self.broker.positions = [position()]
        self.broker.equity = 979
        self.engine.monitor()
        self.assertTrue(self.engine.campaign["tripped"])
        self.assertFalse(self.engine.entries_enabled)
        self.assertIn("/v2/positions/AAPL", self.broker.deleted)
        self.assertIsNotNone(self.engine.risk_snapshot()["monitor_error"])

    def test_ledger_failure_does_not_block_missing_protective_stop(self):
        self.engine.arm()
        self.engine.analytics.record = Mock(side_effect=OSError("disk unavailable"))
        self.engine.analytics.order = Mock(side_effect=OSError("disk unavailable"))
        self.broker.positions = [position()]
        with self.assertRaises(ValueError):
            self.engine.monitor()
        self.assertFalse(self.engine.entries_enabled)
        self.assertEqual(self.broker.sent[0]["type"], "stop")

    def test_ledger_failure_prevents_new_buy_submission(self):
        self.engine.arm()
        self.engine.analytics.record = Mock(side_effect=OSError("disk unavailable"))
        with self.assertRaises(ValueError):
            self.engine._submit({"side": "buy", "symbol": "AAPL", "client_order_id": "test"})
        self.assertEqual(self.broker.sent, [])

    def test_fill_activities_are_deduplicated_and_keep_each_partial(self):
        self.engine.arm()
        original = self.broker.get
        fills = [
            {
                "id": "fill-1",
                "order_id": "entry-1",
                "symbol": "AAPL",
                "side": "buy",
                "qty": "1",
                "price": "100",
                "transaction_time": "2026-10-18T14:30:01Z",
                "type": "partial_fill",
            },
            {
                "id": "fill-2",
                "order_id": "entry-1",
                "symbol": "AAPL",
                "side": "buy",
                "qty": "2",
                "price": "100.1",
                "transaction_time": "2026-10-18T14:30:02Z",
                "type": "fill",
            },
        ]

        def read(path, **params):
            return fills if path == "/v2/account/activities/FILL" else original(path, **params)

        self.broker.get = read
        self.engine.capture_order_updates()
        self.engine.capture_order_updates()
        self.assertEqual(len(self.engine.risk_snapshot()["fills"]), 2)
        run = self.engine.campaign["analytics_run_id"]
        self.assertEqual(
            sum(e["kind"] == "fill" for e in self.engine.analytics.summary(run)["events"]), 2
        )

    def test_expired_quote_before_submission_never_reaches_broker(self):
        from time import monotonic

        self.engine.arm()
        self.engine.quote_checked["AAPL"] = (monotonic() - 61, 0)
        with self.assertRaisesRegex(ValueError, "envejecida"):
            self.engine._submit({"symbol": "AAPL", "side": "buy", "client_order_id": "late"})
        self.assertEqual(self.broker.sent, [])

    def test_cancel_request_is_not_reported_as_confirmed(self):
        self.engine.arm()
        self.engine._cancel_entry(
            {"id": "entry-1", "client_order_id": "client-1", "symbol": "AAPL"}, "timeout_120s"
        )
        row = self.engine.analytics.executions(self.engine.campaign["analytics_run_id"])[0]
        self.assertIsNotNone(row["cancel_requested_at"])
        self.assertIsNone(row["canceled_at"])

    def test_unchanged_risk_snapshots_skip_redundant_writes_but_changes_are_immediate(self):
        self.engine.arm()
        original = self.engine.analytics.record
        self.engine.analytics.record = Mock(wraps=original)
        self.engine._audit("positions", [])
        self.engine._audit("positions", [])
        self.assertEqual(self.engine.analytics.record.call_count, 0)  # Already observed during arm.
        self.engine._audit("positions", [{"symbol": "AAPL", "qty": "1"}])
        self.assertEqual(self.engine.analytics.record.call_count, 1)
