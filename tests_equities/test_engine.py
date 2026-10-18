import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from equities.engine import Engine, signal


class Broker:
    def __init__(self):
        self.now = datetime(2026, 10, 15, 12, tzinfo=ZoneInfo("America/New_York"))
        self.open = True
        self.pending = []
        self.positions = []
        self.sent = []
        self.asset = {"class": "us_equity", "tradable": True, "status": "active"}
        self.cash = "1000"
        self.closes = [100] * 24 + [110]

    def get(self, path, **kwargs):
        if path == "/v2/clock":
            return {"is_open": self.open, "timestamp": self.now.isoformat()}
        if path == "/v2/account":
            return {
                "cash": self.cash,
                "buying_power": self.cash,
                "trading_blocked": False,
                "account_blocked": False,
            }
        if path == "/v2/calendar":
            start = datetime.fromisoformat(kwargs["start"]).date()
            end = datetime.fromisoformat(kwargs["end"]).date()
            return [
                {"date": (start + timedelta(days=i)).isoformat(), "open": "09:30", "close": "16:00"}
                for i in range((end - start).days + 1)
                if (start + timedelta(days=i)).weekday() < 5
            ]
        if path == "/v2/positions":
            return self.positions
        if path == "/v2/orders":
            return self.pending
        if path.startswith("/v2/assets/"):
            return self.asset
        if path.endswith("/bars"):
            return {
                "bars": [
                    {
                        "t": (self.now - timedelta(days=len(self.closes) - i)).isoformat(),
                        "c": c,
                        "h": c + 0.5,
                        "l": c - 0.5,
                    }
                    for i, c in enumerate(self.closes)
                ],
                "next_page_token": None,
            }
        raise AssertionError(path)

    def post(self, path, payload):
        self.sent.append(payload)
        return {"id": "test", **payload}


class Tests(unittest.TestCase):
    def setUp(self):
        self.broker = Broker()
        self.engine = Engine(self.broker, ["AAPL"], 550)

    def test_signal(self):
        self.assertEqual(signal([100] * 20 + [110]), "buy")
        self.assertEqual(signal([100] * 20 + [90]), "sell")
        self.assertEqual(signal([1] * 25), "hold")
        self.assertEqual(signal([1]), "hold")

    def test_closed_market(self):
        self.broker.open = False
        self.engine.cycle()
        self.assertEqual(self.broker.sent, [])

    def test_pending_order_blocks(self):
        self.broker.pending = [{"symbol": "AAPL"}]
        self.engine.cycle()
        self.assertEqual(self.broker.sent, [])

    def test_buy_limited_and_duplicate_blocked(self):
        self.engine.cycle()
        self.engine.cycle()
        self.assertEqual(len(self.broker.sent), 1)
        self.assertEqual(self.broker.sent[0]["qty"], "4")
        self.assertEqual(self.broker.sent[0]["side"], "buy")

    def test_crypto_rejected(self):
        self.broker.asset["class"] = "crypto"
        self.engine.cycle()
        self.assertEqual(self.broker.sent, [])

    def test_no_short(self):
        self.broker.closes = [100] * 24 + [90]
        self.engine.cycle()
        self.assertEqual(self.broker.sent, [])

    def test_existing_long_not_increased(self):
        self.broker.positions = [{"symbol": "AAPL", "qty": "2"}]
        self.engine.cycle()
        self.assertEqual(self.broker.sent, [])

    def test_cash_limit(self):
        self.broker.cash = "10"
        self.engine.cycle()
        self.assertEqual(self.broker.sent, [])

    def test_blocked_account(self):
        original = self.broker.get
        self.broker.get = lambda path, **kw: (
            {"trading_blocked": True} if path == "/v2/account" else original(path, **kw)
        )
        with self.assertRaises(ValueError):
            self.engine.cycle()
        self.assertEqual(self.broker.sent, [])

    def test_invalid_config(self):
        for symbols, budget in [(["BTC/USD"], 500), (["AAPL"], -1), (["AAPL"], float("nan"))]:
            with self.assertRaises(ValueError):
                Engine(self.broker, symbols, budget)

    def test_preview_does_not_submit_or_consume_signal(self):
        result = self.engine.cycle(execute=False)
        self.assertEqual(result[0]["status"], "Vista previa; no enviada")
        self.assertEqual(self.broker.sent, [])
        self.engine.cycle()
        self.assertEqual(len(self.broker.sent), 1)

    def test_submission_survives_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder) / "state.json"
            Engine(self.broker, ["AAPL"], 550, state).cycle()
            Engine(self.broker, ["AAPL"], 550, state).cycle()
            self.assertEqual(len(self.broker.sent), 1)

    def test_timeout_reserves_signal(self):
        def fail(path, payload):
            raise ValueError("Timeout")

        self.broker.post = fail
        with self.assertRaises(ValueError):
            self.engine.cycle()
        self.engine.cycle()
        self.assertEqual(self.broker.sent, [])

    def test_sell_does_not_exceed_long_position(self):
        self.broker.closes = [100] * 24 + [90]
        self.broker.positions = [{"symbol": "AAPL", "qty": "2.5"}]
        self.engine.cycle()
        self.assertEqual(self.broker.sent[0]["qty"], "2")
        self.assertEqual(self.broker.sent[0]["side"], "sell")

    def test_invalid_prices_hold(self):
        self.assertEqual(signal([float("nan")] * 25), "hold")
        self.assertEqual(signal([-1] * 25), "hold")


if __name__ == "__main__":
    unittest.main()
