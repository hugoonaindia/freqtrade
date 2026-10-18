import unittest

from test_engine import Broker

from equities.engine import Engine


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.broker = Broker()
        self.broker.closes = [100] * 24 + [90]

    def test_engine_uses_selected_strategy(self):
        engine = Engine(self.broker, ["AAPL"], 550, strategy="mean_reversion")
        rows = engine.cycle(execute=False)
        self.assertEqual(rows[0]["signal"], "buy")
        self.assertIn("z-score", rows[0]["reason"])
        self.assertEqual(self.broker.sent, [])

    def test_unknown_selection_does_not_mutate(self):
        engine = Engine(self.broker, ["AAPL"], 550)
        with self.assertRaises(ValueError):
            engine.select_strategy("unknown")
        self.assertEqual(engine.strategy, "sma")

    def test_switch_does_not_allow_duplicate_orders(self):
        self.broker.closes = [100] * 24 + [110]
        engine = Engine(self.broker, ["AAPL"], 550, strategy="sma")
        engine.cycle()
        engine.select_strategy("breakout")
        self.broker.asset = {"class": "us_equity", "tradable": True, "status": "active"}
        original = self.broker.get

        def with_high_low(path, **params):
            result = original(path, **params)
            if path.endswith("/bars"):
                for b in result["bars"]:
                    b.update(h=b["c"] + 0.5, l=b["c"] - 0.5)
            return result

        self.broker.get = with_high_low
        engine.cycle()
        self.assertEqual(len(self.broker.sent), 1)

    def test_momentum_requires_longer_history(self):
        engine = Engine(self.broker, ["AAPL"], 550, strategy="momentum")
        rows = engine.cycle()
        self.assertEqual(self.broker.sent, [])
        self.assertEqual(rows[0]["signal"], "hold")

    def test_momentum_ranks_universe_before_orders(self):
        original = self.broker.get

        def history(path, **params):
            result = original(path, **params)
            if path.endswith("/bars"):
                from datetime import timedelta

                end = 120 if "MSFT" in path else 110
                values = [100] * 63 + [end]
                result["bars"] = [
                    {
                        "t": (self.broker.now - timedelta(days=64 - i)).isoformat(),
                        "c": c,
                    }
                    for i, c in enumerate(values)
                ]
            return result

        self.broker.get = history
        engine = Engine(self.broker, ["AAPL", "MSFT"], 550, strategy="momentum")
        rows = engine.cycle()
        self.assertEqual([r["signal"] for r in rows], ["sell", "buy"])
        self.assertEqual([r["symbol"] for r in self.broker.sent], ["MSFT"])

    def test_preview_available_when_market_closed(self):
        self.broker.open = False
        engine = Engine(self.broker, ["AAPL"], 550, strategy="mean_reversion")
        self.assertEqual(engine.cycle(execute=False)[0]["signal"], "buy")
        self.assertEqual(self.broker.sent, [])
