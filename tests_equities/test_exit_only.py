import unittest
from unittest.mock import Mock

from equities.broker import Alpaca
from equities.controls import Controls
from equities.engine import Engine


class ExitOnlyTests(unittest.TestCase):
    def broker(self, positions=None, pending=None, opened=True):
        b = Mock()
        data = {
            "/v2/clock": {"is_open": opened},
            "/v2/positions": positions or [],
            "/v2/orders": pending or [],
            "/v2/account": {},
        }
        b.get.side_effect = lambda path, **kw: data[path]
        return b

    def test_close_uses_position_liquidation_never_new_order(self):
        b = self.broker([{"symbol": "AAPL", "asset_class": "us_equity", "qty": "2.5"}])
        e = Engine(b, ["AAPL"], 500)
        rows = e.close_positions()
        b.delete.assert_called_once_with("/v2/positions/AAPL")
        b.post.assert_not_called()
        self.assertEqual(rows[0]["symbol"], "AAPL")

    def test_pending_order_prevents_duplicate_close(self):
        b = self.broker(
            [{"symbol": "AAPL", "asset_class": "us_equity", "qty": "2"}], [{"symbol": "AAPL"}]
        )
        e = Engine(b, ["AAPL"], 500)
        e.close_positions()
        b.delete.assert_not_called()

    def test_no_positions_no_orders(self):
        b = self.broker()
        Engine(b, ["AAPL"], 500).close_positions()
        b.delete.assert_not_called()
        b.post.assert_not_called()

    def test_closed_market_cannot_submit_close(self):
        b = self.broker(opened=False)
        with self.assertRaises(ValueError):
            Engine(b, ["AAPL"], 500).close_positions()
        b.delete.assert_not_called()

    def test_short_and_crypto_positions_are_skipped(self):
        b = self.broker(
            [
                {"symbol": "AAPL", "asset_class": "us_equity", "qty": "-2"},
                {"symbol": "BTCUSD", "asset_class": "crypto", "qty": "1"},
            ]
        )
        Engine(b, ["AAPL"], 500).close_positions()
        b.delete.assert_not_called()

    def test_exit_only_blocks_start_and_manual_strategy(self):
        e = Engine(self.broker(), ["AAPL"], 500)
        e.entries_enabled = False
        c = Controls(e)
        with self.assertRaises(ValueError):
            c.start(e.selection())
        with self.assertRaises(ValueError):
            c.cycle(True, e.selection())

    def test_real_broker_rejects_entry_before_network(self):
        b = Alpaca()
        b.opener = Mock()
        with self.assertRaises(ValueError):
            b.post("/v2/orders", {"side": "buy", "symbol": "AAPL", "qty": "1"})
        b.opener.open.assert_not_called()
