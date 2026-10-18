"""Bounded HTTPS requests to fixed Alpaca paper and stock-data endpoints."""

import json
import math
import os
import ssl
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from .paths import credentials_path


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Alpaca:
    def __init__(self, credentials=None):
        credentials = credentials or credentials_path()
        saved = json.loads(credentials.read_text()) if credentials.exists() else {}
        self.allow_protected_entries = False
        self.key = os.environ.get("APCA_API_KEY_ID") or saved.get("key")
        self.secret = os.environ.get("APCA_API_SECRET_KEY") or saved.get("secret")
        try:
            import certifi

            context = ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            context = ssl.create_default_context()
        self.opener = build_opener(NoRedirect, HTTPSHandler(context=context))

    @property
    def configured(self):
        return bool(self.key and self.secret)

    def request(self, method, path, payload=None, data=False, **params):
        if method == "POST" and path == "/v2/orders" and (payload or {}).get("side") == "buy":
            try:
                qty = float(payload["qty"])
                limit = float(payload["limit_price"])
                stop = float(payload["stop_loss"]["stop_price"])
                allowed = (
                    self.allow_protected_entries
                    and payload.get("order_class") == "oto"
                    and payload.get("type") == "limit"
                    and payload.get("time_in_force") == "gtc"
                    and all(math.isfinite(v) and v > 0 for v in (qty, limit, stop))
                    and qty.is_integer()
                    and 0.98 * limit <= stop < limit
                )
            except (KeyError, TypeError, ValueError):
                allowed = False
            if not allowed:
                raise ValueError(
                    "Entradas bloqueadas: requiere campaña activa y stop adjunto del 2%."
                )
        if not self.configured:
            raise ValueError("Faltan claves Alpaca Paper. Consulta docs/alpaca.md.")
        base = "https://data.alpaca.markets" if data else "https://paper-api.alpaca.markets"
        url = base + path + ("?" + urlencode(params) if params else "")
        req = Request(  # noqa: S310 - Fixed HTTPS endpoints; redirects are disabled.
            url,
            data=json.dumps(payload).encode() if payload is not None else None,
            method=method,
            headers={
                "APCA-API-KEY-ID": self.key,
                "APCA-API-SECRET-KEY": self.secret,
                "Content-Type": "application/json",
            },
        )
        try:
            with self.opener.open(req, timeout=15) as response:
                raw = response.read()
                return json.loads(raw) if raw else {}
        except HTTPError as exc:
            # Never include headers, credentials or raw broker responses in logs/UI.
            raise ValueError(
                f"Alpaca HTTP {exc.code}: comprueba claves Paper, permisos y datos."
            ) from None
        except (URLError, TimeoutError, OSError):
            raise ValueError(
                "Alpaca no responde. No se reintentan órdenes automáticamente."
            ) from None

    def get(self, path, **params):
        return self.request("GET", path, **params)

    def post(self, path, payload):
        return self.request("POST", path, payload)

    def delete(self, path):
        return self.request("DELETE", path)
