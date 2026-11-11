"""Bounded HTTPS requests to fixed Alpaca paper and stock-data endpoints."""

import json
import math
import os
import ssl
import logging
import time
import threading
from collections import deque
from time import sleep
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from .paths import credentials_path

logger = logging.getLogger("freqtrade_equities")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class RateLimiter:
    """Token bucket rate limiter para evitar rate limits de Alpaca."""

    def __init__(self, max_tokens=200, refill_rate=200/60, burst_tokens=50):
        """
        Configurar rate limiter.
        - max_tokens: límite máximo de tokens (200 req/min para Alpaca)
        - refill_rate: tokens por segundo (200/60 ≈ 3.33)
        - burst_tokens: permite ráfagas iniciales
        """
        self.max_tokens = max_tokens
        self.refill_rate = refill_rate
        self.burst_tokens = burst_tokens
        self.tokens = burst_tokens
        self.last_refill = time.monotonic()
        self.lock = threading.Lock()
        # Separate tracking for GET (data endpoint) and POST (trading endpoint)
        self.get_request_times = deque(maxlen=200)
        self.post_request_times = deque(maxlen=200)

    def _refill(self):
        """Refill tokens based on elapsed time."""
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.max_tokens, self.tokens + elapsed * self.refill_rate)
        self.last_refill = now

    def acquire(self, tokens=1, block=True, timeout=None):
        """
        Intentar adquirir tokens.
        - block: si True, esperar hasta obtener tokens
        - timeout: tiempo máximo de espera (None = forever)
        """
        start = time.monotonic()
        while True:
            with self.lock:
                self._refill()
                if self.tokens >= tokens:
                    self.tokens -= tokens
                    return True

            if not block:
                return False

            if timeout is not None and time.monotonic() - start >= timeout:
                return False

            # Esperar antes de reintentar
            sleep(0.1)

    def wait_for_token(self, tokens=1, timeout=5):
        """Esperar hasta obtener tokens con timeout."""
        return self.acquire(tokens, block=True, timeout=timeout)

    def get_wait_time(self, tokens=1):
        """Estimar tiempo de espera para obtener tokens."""
        with self.lock:
            self._refill()
            if self.tokens >= tokens:
                return 0
            needed = tokens - self.tokens
            return needed / self.refill_rate

    def record_request(self, method="GET"):
        """Registrar una solicitud para tracking."""
        now = time.monotonic()
        if method == "GET":
            self.get_request_times.append(now)
        else:
            self.post_request_times.append(now)

    def get_recent_requests(self, window=60, method="GET"):
        """Contar solicitudes recientes en ventana temporal."""
        now = time.monotonic()
        cutoff = now - window
        times = self.get_request_times if method == "GET" else self.post_request_times
        return sum(1 for t in times if t > cutoff)


class Alpaca:
    def __init__(self, credentials=None, validate=False):
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

        # Rate limiter para Alpaca (200 req/min, con ráfagas)
        self.rate_limiter = RateLimiter(max_tokens=200, refill_rate=200/60, burst_tokens=50)

        # Circuit breaker: disable broker after consecutive failures
        self._consecutive_failures = 0
        self._circuit_breaker_threshold = 5
        self._circuit_open = False

        if validate:
            self._validate_credentials()

    @property
    def configured(self):
        return bool(self.key and self.secret)

    def _record_success(self):
        self._consecutive_failures = 0
        self._circuit_open = False

    def _record_failure(self):
        self._consecutive_failures += 1
        if self._consecutive_failures >= self._circuit_breaker_threshold:
            self._circuit_open = True
            logger.error("Circuit breaker abierto tras %d fallos consecutivos", self._consecutive_failures)

    def _check_circuit(self):
        if self._circuit_open:
            raise ValueError("Circuit breaker abierto: demasiados fallos consecutivos. Requiere reinicio manual.")

    def _validate_credentials(self):
        """Validar credentials al arranque con llamada rápida a Alpaca."""
        if not self.configured:
            raise ValueError(
                "Faltan claves Alpaca Paper. Configura .secrets/alpaca.json o variables de entorno."
            )
        try:
            # Llamada rápida a account endpoint con timeout reducido
            account = self.request("GET", "/v2/account", timeout=5)
            if not isinstance(account, dict):
                raise ValueError("Respuesta de cuenta inválida")
            logger.info(
                "Credentials Alpaca validadas correctamente. Account: %s, Status: %s",
                account.get("id", "unknown")[:8] + "...",
                account.get("status", "unknown")
            )
        except ValueError as e:
            if "Alpaca no responde" in str(e) or "HTTP 401:" in str(e) or "HTTP 403:" in str(e):
                raise ValueError(
                    f"Validación de credentials fallida: {e}. "
                    "Revisa tu clave y secreto en Alpaca Paper."
                ) from None
            raise

    def request(self, method, path, payload=None, data=False, **params):
        self._check_circuit()
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

        # Añadir logging para debugging
        logger.debug("%s %s params=%s", method, path, params)

        # Rate limiting - separate buckets for GET (data) and POST (trading)
        if method == "GET" or (method == "POST" and data):
            # Esperar si estamos cerca del límite
            rate_method = "GET" if method == "GET" else "POST"
            if self.rate_limiter.get_recent_requests(60, rate_method) > 180:
                wait_time = self.rate_limiter.get_wait_time(1)
                if wait_time > 0.5:
                    logger.info("Rate limiter: esperando %.2fs antes de %s %s", wait_time, method, path)
                    self.rate_limiter.wait_for_token(timeout=10)

            self.rate_limiter.record_request(method)
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
                result = json.loads(raw) if raw else {}
            self._record_success()
            return result
        except HTTPError as exc:
            self._record_failure()
            # Never include headers, credentials or raw broker responses in logs/UI.
            logger.warning("HTTP %d en %s %s", exc.code, method, path)
            if exc.code == 429:
                # Rate limit exceeded - esperar y reintentar
                retry_after = int(exc.headers.get("Retry-After", 5)) if exc.headers else 5
                logger.warning("Rate limit alcanzado. Esperando %ds", retry_after)
                sleep(retry_after)
                raise ValueError(
                    f"Alpaca HTTP {exc.code}: rate limit. Espera {retry_after}s y reintentar."
                ) from None
            raise ValueError(
                f"Alpaca HTTP {exc.code}: comprueba claves Paper, permisos y datos."
            ) from None
        except (URLError, TimeoutError, OSError):
            self._record_failure()
            raise ValueError(
                "Alpaca no responde. No se reintentan órdenes automáticamente."
            ) from None

    def get(self, path, **params):
        for attempt in range(3):
            try:
                return self.request("GET", path, **params)
            except ValueError as exc:
                transient = "Alpaca no responde" in str(exc) or any(
                    f"HTTP {code}:" in str(exc) for code in (429, 500, 502, 503, 504)
                )
                if not transient or attempt == 2:
                    raise
                sleep(2**attempt)
        raise RuntimeError("Consulta agotada.")

    def post(self, path, payload):
        return self.request("POST", path, payload)

    def delete(self, path):
        return self.request("DELETE", path)
