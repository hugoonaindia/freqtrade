"""Protected multi-day Paper execution and persistent account drawdown guard."""

import json
import math
import threading
from datetime import UTC, datetime
from decimal import ROUND_CEILING, Decimal

from .engine import Engine
from .guards import available_cash, completed_session
from .strategies import decide, rotation


def positive(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError("Valor de cuenta o precio inválido; no se opera.")
    return number


def price_text(value):
    number = Decimal(str(value))
    tick = Decimal("0.01") if number >= 1 else Decimal("0.0001")
    return str(number.quantize(tick, rounding=ROUND_CEILING))


class SwingEngine(Engine):
    """A campaign baseline survives pauses and restarts; only explicit start arms entries."""

    def __init__(self, *args, campaign_path, **kwargs):
        super().__init__(*args, **kwargs)
        self.lock = threading.RLock()
        self.entries_enabled = False
        self.history_adjustment = "split"
        self.campaign_path = campaign_path
        self.campaign = (
            json.loads(campaign_path.read_text(encoding="utf-8"))
            if campaign_path.exists()
            else None
        )
        self.monitor_error = None
        self.last_check = None
        self.last_equity = None
        self.events = (self.campaign or {}).get("events", [])
        if self.campaign:
            positive(self.campaign["baseline"])
            if (
                not self.campaign.get("account_id")
                or type(self.campaign.get("tripped")) is not bool
            ):
                raise ValueError("Estado de riesgo inválido; requiere revisión manual.")

    def _persist(self):
        self.campaign_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = self.campaign_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.campaign, indent=2), encoding="utf-8")
        temporary.replace(self.campaign_path)

    def _event(self, message):
        event = {"time": datetime.now(UTC).isoformat(), "message": message}
        self.events = [*self.events[-99:], event]
        if self.campaign:
            self.campaign["events"] = self.events
            self._persist()

    def risk_snapshot(self):
        with self.lock:
            baseline = self.campaign["baseline"] if self.campaign else None
            return {
                "allocation_pct": 100,
                "position_stop_pct": 2,
                "account_stop_pct": 2,
                "baseline": baseline,
                "account_floor": baseline * 0.98 if baseline else None,
                "equity": self.last_equity,
                "loss_pct": (1 - self.last_equity / baseline) * 100
                if baseline and self.last_equity is not None
                else None,
                "activated_at": self.campaign["activated_at"] if self.campaign else None,
                "tripped": bool(self.campaign and self.campaign["tripped"]),
                "monitor_error": self.monitor_error,
                "last_check": self.last_check,
                "events": self.events or (self.campaign or {}).get("events", []),
                "mode": "swing-paper",
            }

    def _orders(self):
        orders = self.broker.get("/v2/orders", status="open", limit=500)
        if len(orders) >= 500:
            raise ValueError("Órdenes incompletas; entradas bloqueadas.")
        return orders

    def _account(self):
        account = self.broker.get("/v2/account")
        equity = positive(account["equity"])
        if account.get("trading_blocked") or account.get("account_blocked"):
            raise ValueError("Cuenta bloqueada; requiere revisión en Alpaca.")
        if self.campaign and account["id"] != self.campaign["account_id"]:
            raise ValueError("Cuenta distinta a la campaña; entradas bloqueadas.")
        self.last_equity = equity
        return account

    def arm(self):
        with self.lock:
            account = self._account()
            if self.campaign and (self.campaign["tripped"] or self.campaign.get("closing_all")):
                raise ValueError("Límite global alcanzado. Campaña bloqueada; revisa resultados.")
            if not self.campaign:
                if self.broker.get("/v2/positions") or self._orders():
                    raise ValueError(
                        "Primera activación requiere cuenta Paper sin posiciones ni órdenes."
                    )
                self.campaign = {
                    "account_id": account["id"],
                    "baseline": positive(account["equity"]),
                    "activated_at": datetime.now(UTC).isoformat(),
                    "tripped": False,
                }
                self._persist()
                self._event(
                    "Campaña iniciada: stop individual 2%, límite global 2% del capital inicial."
                )
            self._monitor_locked()
            if self.campaign["tripped"] or self.campaign.get("closing_all"):
                raise ValueError("Liquidación pendiente o límite alcanzado; entradas bloqueadas.")
            self.entries_enabled = True
            self.broker.allow_protected_entries = True

    def reset_campaign(self):
        with self.lock:
            self.disarm()
            self._account()
            if self.broker.get("/v2/positions") or self._orders():
                raise ValueError("Para cerrar campaña, confirma antes posiciones y órdenes vacías.")
            if self.campaign:
                stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
                archive = self.campaign_path.with_name(f"campaign-{stamp}.json")
                archive.write_text(json.dumps(self.campaign, indent=2), encoding="utf-8")
            self.campaign = None
            self.events = []
            self.last_equity = None
            self.monitor_error = None
            self.last_check = None
            self._persist()
            return self.risk_snapshot()

    def disarm(self):
        with self.lock:
            self.entries_enabled = False
            self.broker.allow_protected_entries = False

    def monitor(self):
        with self.lock:
            if not self.campaign:
                return
            try:
                self._monitor_locked()
                self.monitor_error = None
                self.last_check = datetime.now(UTC).isoformat()
            except Exception:
                self.disarm()
                self.monitor_error = (
                    "Supervisión incompleta; entradas bloqueadas. Revisa Alpaca y conexión."
                )
                raise

    def _monitor_locked(self):  # noqa: C901 - Broker reconciliation stays sequential under one lock.
        account = self._account()
        if (
            positive(account["equity"]) <= self.campaign["baseline"] * 0.98
            and not self.campaign["tripped"]
        ):
            self.campaign["tripped"] = True
            self._persist()  # Latch before any remote write, even if the network fails.
            self._event("Límite global alcanzado. Cancelando entradas y cerrando posiciones.")
        if self.campaign["tripped"] or self.campaign.get("closing_all"):
            self.disarm()
            self._liquidate()
            return
        positions = self.broker.get("/v2/positions")
        orders = self._orders()
        for symbol in list(self.campaign.get("closing", {})):
            if not any(p["symbol"] == symbol for p in positions) and not any(
                o["symbol"] == symbol for o in orders
            ):
                del self.campaign["closing"][symbol]
                self._persist()
        market_open = self.broker.get("/v2/clock")["is_open"]
        for order in orders:
            if order["side"] == "buy" and not self.entries_enabled:
                self.broker.delete("/v2/orders/" + order["id"])
                raise ValueError("Entradas en pausa: cancelación pendiente de confirmación.")
            if order["side"] == "buy" and order.get("created_at"):
                created = datetime.fromisoformat(order["created_at"])
                age = (datetime.now(UTC) - created).total_seconds()
                if age > 120:
                    self.broker.delete("/v2/orders/" + order["id"])
                    raise ValueError("Entrada antigua cancelándose; esperando reconciliación.")
            if order["side"] == "buy" and order.get("status") == "partially_filled":
                self.broker.delete("/v2/orders/" + order["id"])
                raise ValueError(
                    "Entrada parcial: cancelación solicitada; esperando reconciliación."
                )
        for position in positions:
            symbol = position["symbol"]
            if position.get("asset_class") != "us_equity" or positive(position["qty"]) % 1:
                raise ValueError("Posición incompatible; requiere revisión manual.")
            same = [order for order in orders if order["symbol"] == symbol]
            if any(o["side"] == "buy" for o in same):
                raise ValueError(
                    "Entrada pendiente con posición abierta; esperando reconciliación."
                )
            if symbol in self.campaign.get("closing", {}):
                self._close_symbol(symbol, same)
                continue
            exits = [o for o in same if o["side"] == "sell"]
            if any(o.get("type") == "market" for o in exits):
                continue
            floor = float(price_text(positive(position["avg_entry_price"]) * 0.98))
            covered = sum(
                float(o["qty"]) - float(o.get("filled_qty", 0))
                for o in exits
                if o.get("type") == "stop"
                and o.get("time_in_force") == "gtc"
                and o.get("status") in ("new", "accepted")
                and float(o.get("stop_price", 0)) >= floor
            )
            if covered >= positive(position["qty"]):
                continue
            if exits:
                raise ValueError("Protección insuficiente; revisa las órdenes de salida en Alpaca.")
            if not market_open:
                raise ValueError(
                    "Posición sin protección confirmada fuera de sesión; requiere revisión."
                )
            if positive(position["current_price"]) <= floor:
                self._request_close(symbol, same)
            else:
                stamp = position["avg_entry_price"] + ":" + str(position["qty"])
                order_id = self._id(symbol, "protect", stamp)
                if order_id in self.seen:
                    raise ValueError("Protección ya solicitada sin confirmar; revisa Alpaca.")
                self._reserve(order_id)
                self.broker.post(
                    "/v2/orders",
                    {
                        "symbol": symbol,
                        "qty": str(position["qty"]),
                        "side": "sell",
                        "type": "stop",
                        "stop_price": price_text(floor),
                        "time_in_force": "gtc",
                        "client_order_id": order_id,
                    },
                )
                self._event(f"{symbol}: protección 2% solicitada en Alpaca.")
            raise ValueError("Protección pendiente de confirmación; entradas bloqueadas.")

    def _liquidate(self):
        orders = self._orders()
        for order in orders:
            if order["side"] == "buy":
                self.broker.delete("/v2/orders/" + order["id"])
        if any(o["side"] == "buy" for o in orders):
            return  # Confirm cancellations next poll before selling.
        if not self.broker.get("/v2/clock")["is_open"]:
            return  # Leave broker stops intact while the market is closed.
        positions = self.broker.get("/v2/positions")
        if not positions and not orders and self.campaign.get("closing_all"):
            self.campaign["closing_all"] = False
            self._persist()
        for position in positions:
            if position.get("asset_class") != "us_equity" or float(position["qty"]) <= 0:
                raise ValueError("Cuenta contiene posiciones incompatibles; revisión manual.")
            symbol = position["symbol"]
            self._request_close(symbol, [o for o in orders if o["symbol"] == symbol])

    def _request_close(self, symbol, orders):
        if symbol not in self.campaign.setdefault("closing", {}):
            self.campaign["closing"][symbol] = "canceling"
            self._persist()
        self._close_symbol(symbol, orders)

    def _close_symbol(self, symbol, orders):
        if orders:
            for order in orders:
                if order.get("type") != "market" or order["side"] != "sell":
                    self.broker.delete("/v2/orders/" + order["id"])
            return
        if self.campaign["closing"][symbol] != "canceling":
            raise ValueError("Cierre enviado sin confirmación; revisa Alpaca antes de reintentar.")
        # Reserve before submitting: ambiguous responses never produce duplicate exits.
        self.campaign["closing"][symbol] = "submitted"
        self._persist()
        self.broker.delete("/v2/positions/" + symbol)
        self._event(f"{symbol}: cierre solicitado; pendiente de ejecución.")

    def close_positions(self):
        with self.lock:
            if not self.campaign:
                return super().close_positions()
            if not self.broker.get("/v2/clock")["is_open"]:
                raise ValueError("Mercado cerrado; se conservan stops de protección.")
            self.disarm()
            self._account()
            self.campaign["closing_all"] = True
            self._persist()
            self._liquidate()
            return [
                {"status": "Cierre solicitado. Supervisor confirma cancelaciones y ejecuciones."}
            ]

    @staticmethod
    def _id(symbol, action, stamp):
        import hashlib

        return "swing-" + hashlib.sha256(f"{symbol}:{action}:{stamp}".encode()).hexdigest()[:40]

    def _quote(self, symbol, clock):
        quote = self.broker.get("/v2/stocks/" + symbol + "/quotes/latest", data=True, feed="iex")[
            "quote"
        ]
        stamp = datetime.fromisoformat(quote["t"])
        now = datetime.fromisoformat(clock["timestamp"])
        if stamp.tzinfo is None or not 0 <= (now - stamp).total_seconds() <= 60:
            raise ValueError("Cotización antigua; sin entrada.")
        ask, bid = positive(quote["ap"]), positive(quote["bp"])
        if bid > ask or (ask - bid) / ask > 0.01:
            raise ValueError("Cotización inválida o spread superior al 1%; sin entrada.")
        return float(price_text(ask))

    def _cycle(self, execute):  # noqa: C901 - Reserve, validate and submit under one lock.
        if not execute:
            return super()._cycle(False)
        if not self.entries_enabled or not self.campaign:
            return [{"status": "Autonomía en pausa; activa GO PAPER para iniciar."}]
        self._monitor_locked()
        if self.campaign["tripped"]:
            return [{"status": "Límite global alcanzado; campaña bloqueada."}]
        clock = self.broker.get("/v2/clock")
        if not clock["is_open"]:
            return [{"status": "Mercado cerrado; conserva posiciones y stops GTC."}]
        account = self._account()
        orders = self._orders()
        positions = {p["symbol"]: p for p in self.broker.get("/v2/positions")}
        session = completed_session(self.broker, clock)
        today = datetime.fromisoformat(clock["timestamp"]).date()
        universe = {}
        for symbol in sorted(self.symbols):
            asset = self.broker.get("/v2/assets/" + symbol)
            if (
                asset.get("class") != "us_equity"
                or not asset.get("tradable")
                or asset.get("status") != "active"
            ):
                raise ValueError("Activo no apto; no se opera.")
            universe[symbol] = self._history(symbol, today, session)
        decisions = (
            rotation(universe)
            if self.strategy == "momentum"
            else {symbol: decide(self.strategy, bars) for symbol, bars in universe.items()}
        )
        cash = available_cash(account, orders)
        allocation = positive(account["equity"]) / (
            1 if self.strategy == "momentum" else len(self.symbols)
        )
        rows = []
        for symbol, decision in decisions.items():
            row = {"symbol": symbol, **decision, "status": "Sin orden"}
            rows.append(row)
            pending = [o for o in orders if o["symbol"] == symbol]
            if decision["signal"] == "sell" and symbol in positions:
                self._request_close(symbol, pending)
                row["status"] = "Salida solicitada; esperando cancelaciones y ejecución."
                continue
            if symbol in self.campaign.get("closing", {}):
                row["status"] = "Símbolo cerrado por campaña; no reentra automáticamente."
                continue
            if decision["signal"] != "buy" or symbol in positions or pending:
                row["status"] = (
                    "Mantiene posición / esperando orden"
                    if symbol in positions or pending
                    else decision["reason"]
                )
                continue
            order_id = self._id(symbol, "buy", session.isoformat())
            if order_id in self.seen:
                row["status"] = "Señal de esta sesión ya tramitada."
                continue
            limit = self._quote(symbol, clock)
            qty = math.floor(min(allocation, cash) / (limit * 1.01))
            if qty <= 0:
                row["status"] = "Efectivo insuficiente para una acción."
                continue
            # A limit entry bounds purchase cost. Its attached stop remains broker-side.
            self._reserve(order_id)
            self.broker.post(
                "/v2/orders",
                {
                    "symbol": symbol,
                    "qty": str(qty),
                    "side": "buy",
                    "type": "limit",
                    "limit_price": price_text(limit),
                    "time_in_force": "gtc",
                    "order_class": "oto",
                    "stop_loss": {"stop_price": price_text(limit * 0.98)},
                    "client_order_id": order_id,
                },
            )
            cash -= qty * limit * 1.01
            row.update(qty=qty, status="Entrada limitada con stop 2% enviada a Paper.")
            self._event(f"{symbol}: {qty} acciones solicitadas con stop adjunto.")
        return rows
