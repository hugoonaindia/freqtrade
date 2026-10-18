import io
import json
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError, URLError

from equities.broker import Alpaca, NoRedirect


class Opener:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def open(self, req, timeout):
        self.calls.append((req, timeout))
        if self.error:
            raise self.error
        return io.BytesIO(b'{"status":"ACTIVE"}')


class BrokerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        path = Path(self.temp.name) / "keys.json"
        path.write_text(json.dumps({"key": "test-key", "secret": "test-secret"}))
        self.broker = Alpaca(path)
        self.broker.opener = Opener()

    def tearDown(self):
        self.temp.cleanup()

    def test_fixed_paper_endpoint(self):
        self.broker.post("/v2/orders", {"symbol": "AAPL"})
        req, timeout = self.broker.opener.calls[0]
        self.assertEqual(req.full_url, "https://paper-api.alpaca.markets/v2/orders")
        self.assertEqual(timeout, 15)
        self.assertEqual(json.loads(req.data), {"symbol": "AAPL"})

    def test_data_endpoint(self):
        self.broker.get("/v2/stocks/AAPL/bars", data=True, feed="iex")
        self.assertEqual(
            self.broker.opener.calls[0][0].full_url,
            "https://data.alpaca.markets/v2/stocks/AAPL/bars?feed=iex",
        )

    def test_ambiguous_write_not_retried(self):
        self.broker.opener = Opener(URLError("timeout"))
        with self.assertRaises(ValueError):
            self.broker.post("/v2/orders", {})
        self.assertEqual(len(self.broker.opener.calls), 1)

    def test_error_does_not_expose_response(self):
        self.broker.opener = Opener(HTTPError("url", 401, "test-secret", {}, None))
        with self.assertRaises(ValueError) as ctx:
            self.broker.get("/v2/account")
        self.assertNotIn("test-secret", str(ctx.exception))

    def test_redirect_not_followed(self):
        self.assertIsNone(
            NoRedirect().redirect_request(None, None, 302, "", {}, "https://elsewhere.invalid")
        )
