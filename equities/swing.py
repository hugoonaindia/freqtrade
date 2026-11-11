"""Protected multi-day Paper execution and persistent account drawdown guard."""

import json
import math
import threading
import logging
from time import monotonic
from datetime import UTC, datetime, timedelta
from decimal import ROUND_CEILING, Decimal, ROUND_FLOOR
from statistics import stdev

from .alerts import AlertManager
from .analytics import Analytics
from .engine import Engine
from .durable_state import write_json
from .guards import available_cash, completed_session
from .strategies import decide, rotation

logger = logging.getLogger("freqtrade_equities")


def positive(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError("Valor de cuenta o precio inválido; no se opera.")
    return number


def price_text(value, rounding=ROUND_CEILING):
    number = Decimal(str(value))
    tick = Decimal("0.01") if number >= 1 else Decimal("0.0001")
    return str(number.quantize(tick, rounding=rounding))


def stop_price_text(value):
    """Stop price rounded DOWN (ROUND_FLOOR) to guarantee ≤2% loss on sell stops."""
    return price_text(value, rounding=ROUND_FLOOR)


class SwingEngine(Engine):
    """A campaign baseline survives pauses and restarts; only explicit start arms entries."""

    def __init__(self, *args, campaign_path, **kwargs):
            super().__init__(*args, **kwargs)
            self.lock = threading.RLock()
            self.entries_enabled = False
            self.history_adjustment = "split"
            self.campaign_path = campaign_path
            self.analytics = Analytics(campaign_path.parent / "analytics.sqlite")
            self.alerts = AlertManager(campaign_path.parent, campaign_path.parent / "alert_config.json")
            self.analytics_error = None
            self.quote_context = {}
            self.quote_checked = {}
            self.audit_snapshots = {}
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
        
            # Enhanced risk parameters (disabled by default for backward compatibility)
            self.trailing_stop_pct = 2.0  # Trailing stop percentage
            self.max_daily_loss_pct = 1.0  # Kill switch: max daily loss %
            self.vol_target_pct = 15.0  # Annualized volatility target for position sizing
            self.regime_filter_enabled = False  # Enable regime filter (SPY > MA200)
            self.vol_sizing_enabled = False  # Enable volatility-based position sizing
            self.correlation_check_enabled = False  # Enable correlation check
            self.correlation_window = 60  # Correlation lookback window
            self.regime_adx_enabled = False  # Enable ADX filter
            self.regime_adx_threshold = 20.0  # ADX threshold for trend strength
            self.regime_vix_enabled = False  # Enable VIX filter
            self.regime_vix_threshold = 30.0  # VIX threshold for high volatility
            self.max_position_pct = 20.0  # Max single position as % of equity
            self.max_sector_pct = 40.0  # Max sector concentration
        
            # Iniciar worker de alerts
            self.alerts.start()

    def _persist(self):
        self.campaign_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        write_json(self.campaign_path, self.campaign)

    def _ledger_failed(self):
        self.disarm()
        self.analytics_error = (
            "Registro analítico incompleto; entradas bloqueadas. Revisa almacenamiento."
        )

    def _audit(self, kind, payload, symbol=None):
        try:
            if not self.campaign:
                return
            if not self.campaign.get("analytics_run_id"):
                self.campaign["analytics_run_id"] = self.analytics.start(
                    "paper",
                    {
                        "activated_at": self.campaign["activated_at"],
                        "baseline": self.campaign["baseline"],
                        "strategy": self.strategy,
                        "symbols": self.symbols,
                        "position_stop_pct": 2,
                        "account_stop_pct": 2,
                    },
                )
                self._persist()
            snapshot_key = (self.campaign["analytics_run_id"], kind, symbol)
            snapshot = json.dumps(payload, sort_keys=True, allow_nan=False)
            previous = self.audit_snapshots.get(snapshot_key)
            if (
                kind in ("account", "positions", "stop_reconciliation")
                and previous
                and previous[0] == snapshot
                and monotonic() - previous[1] < 300
            ):
                return
            self.analytics.record(self.campaign["analytics_run_id"], kind, payload, symbol)
            if kind in ("account", "positions", "stop_reconciliation"):
                self.audit_snapshots[snapshot_key] = (snapshot, monotonic())

        except Exception:
            self._ledger_failed()

    def _track_order(self, order):
        try:
            run_id = (self.campaign or {}).get("analytics_run_id")
            if run_id:
                changed = self.analytics.order(run_id, order)
                if changed and order.get("client_order_id"):
                    self.analytics.execution_context(
                        run_id,
                        order["client_order_id"],
                        {"observed_at": datetime.now(UTC).isoformat()},
                    )
        except Exception:
            self._ledger_failed()

    def capture_order_updates(self):
        with self.lock:
            if not self.campaign or not self.campaign.get("analytics_run_id"):
                return
            run_id = self.campaign["analytics_run_id"]
            try:
                for order in self.analytics.pending(run_id):
                    if order.get("id"):
                        updated = self.broker.get("/v2/orders/" + order["id"], nested="true")
                    else:
                        updated = self.broker.get(
                            "/v2/orders:by_client_order_id",
                            client_order_id=order["client_order_id"],
                        )
                    self._track_order(updated)
                token, seen = None, set()
                for _ in range(20):
                    params = {
                        "after": self.campaign["activated_at"],
                        "direction": "asc",
                        "page_size": 100,
                    }
                    if token:
                        params["page_token"] = token
                    fills = self.broker.get("/v2/account/activities/FILL", **params)
                    if not isinstance(fills, list):
                        raise ValueError("Respuesta de fills inválida.")
                    for fill in fills:
                        self.analytics.fill(run_id, fill)
                    if len(fills) < 100:
                        break
                    token = fills[-1]["id"]
                    if token in seen:
                        raise ValueError("Paginación de fills repetida.")
                    seen.add(token)
                else:
                    raise ValueError("Demasiados fills para reconciliar; requiere revisión.")
            except Exception:
                self.disarm()
                self.analytics_error = (
                    "No se pudo reconciliar el registro de órdenes; revisa Alpaca."
                )
                raise

    def _submit(self, payload):
        if payload.get("side") == "buy":
            checked = self.quote_checked.get(payload.get("symbol"))
            if checked is None:
                raise ValueError("Cotización envejecida antes de enviar; compra bloqueada.")
            checked_time, age = checked
            # Support both old format (monotonic float) and new format (datetime)
            if isinstance(checked_time, float):
                # Old format: checked_time is monotonic() timestamp
                elapsed = monotonic() - checked_time
            else:
                # New format: checked_time is datetime.now(UTC)
                elapsed = (datetime.now(UTC) - checked_time).total_seconds()
            if age + elapsed > 60:
                raise ValueError("Cotización envejecida antes de enviar; compra bloqueada.")
        self._audit("order_intent", payload, payload.get("symbol"))
        if payload.get("side") == "buy" and self.analytics_error:
            raise ValueError("Registro analítico incompleto; no se envían nuevas entradas.")
        order_key = payload.get("client_order_id")
        if order_key:
            try:
                sent = datetime.now(UTC)
                self.analytics.execution_context(
                    self.campaign["analytics_run_id"],
                    order_key,
                    {
                        "symbol": payload.get("symbol"),
                        "side": payload.get("side"),
                        "initial_stop_price": (payload.get("stop_loss") or {}).get("stop_price"),
                        "sent_at": sent.isoformat(),
                        "deadline_at": (sent + timedelta(seconds=120)).isoformat()
                        if payload.get("side") == "buy"
                        else None,
                        **(
                            self.quote_context.get(payload.get("symbol"), {})
                            if payload.get("side") == "buy"
                            else {}
                        ),
                    },
                )
            except Exception:
                self._ledger_failed()
                if payload.get("side") == "buy":
                    raise ValueError("No se pudo registrar el envío; compra bloqueada.") from None
        self._track_order({**payload, "status": "submitting"})
        if payload.get("side") == "buy" and self.analytics_error:
            raise ValueError("Registro incompleto; compra bloqueada antes del envío.")
        try:
            response = self.broker.post("/v2/orders", payload)
        except ValueError as exc:
            if any(f"HTTP {code}:" in str(exc) for code in (400, 401, 403, 404, 422)):
                self._track_order({**payload, "status": "rejected"})
            self._audit(
                "submission_failed",
                {
                    "client_order_id": order_key,
                    "response_uncertain": not any(
                        f"HTTP {code}:" in str(exc) for code in (400, 401, 403, 404, 422)
                    ),
                },
                payload.get("symbol"),
            )
            raise
        self._track_order({**payload, **response})
        return response

    def _cancel_entry(self, order, reason):
        now = datetime.now(UTC).isoformat()
        self._audit(
            "cancel_requested",
            {"order_id": order["id"], "reason": reason, "requested_at": now},
            order.get("symbol"),
        )
        try:
            key = order.get("client_order_id") or order["id"]
            self.analytics.execution_context(
                self.campaign["analytics_run_id"],
                key,
                {"cancel_requested_at": now, "cancel_reason": reason},
            )
        except Exception:
            self._ledger_failed()
        self.broker.delete("/v2/orders/" + order["id"])

    def _event(self, message):
        self._audit("campaign_event", {"message": message})
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
                "monitor_error": self.monitor_error or self.analytics_error,
                "analytics_run_id": (self.campaign or {}).get("analytics_run_id"),
                "fills": self.analytics.fills((self.campaign or {}).get("analytics_run_id")),
                "executions": self.analytics.executions(
                    (self.campaign or {}).get("analytics_run_id")
                ),
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
        self._audit(
            "account",
            {
                key: account.get(key)
                for key in ("equity", "cash", "buying_power", "currency", "status")
            },
        )
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
            if self.analytics_error:
                raise ValueError(self.analytics_error)
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
                write_json(archive, self.campaign)
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
    
    def close(self):
        """Detener worker de alerts al cerrar engine."""
        self.alerts.stop()

    def monitor(self):
        with self.lock:
            if not self.campaign:
                return
            try:
                self._monitor_locked()
                self.monitor_error = None
                self.last_check = datetime.now(UTC).isoformat()
            except Exception as e:
                self.disarm()
                self.monitor_error = (
                    "Supervisión incompleta; entradas bloqueadas. Revisa Alpaca y conexión."
                )
                # Enviar alerta de error
                self.alerts.monitor_error(str(e)[:200])
                raise

    def _check_account_stop(self, account):
        """Check global account stop (2% below baseline). Latches on trigger."""
        current_equity = positive(account["equity"])
        if (
            current_equity <= self.campaign["baseline"] * 0.98
            and not self.campaign["tripped"]
        ):
            loss_pct = (1 - current_equity / self.campaign["baseline"]) * 100
            self.campaign["tripped"] = True
            self._persist()  # Latch before any remote write, even if the network fails.
            self._event("Límite global alcanzado. Cancelando entradas y cerrando posiciones.")
            self.alerts.account_stop_triggered(
                current_equity, self.campaign["baseline"], loss_pct
            )
        if self.campaign["tripped"] or self.campaign.get("closing_all"):
            self.disarm()
            self._liquidate()
            return True
        return False

    def _cleanup_closing_symbols(self, positions, orders):
        """Remove symbols from closing list if position/order no longer exists."""
        for symbol in list(self.campaign.get("closing", {})):
            if not any(p["symbol"] == symbol for p in positions) and not any(
                o["symbol"] == symbol for o in orders
            ):
                del self.campaign["closing"][symbol]
                self._persist()

    def _audit_positions(self, positions):
        """Audit current positions for analytics."""
        self._audit(
            "positions",
            [
                {
                    key: p.get(key)
                    for key in (
                        "symbol",
                        "qty",
                        "avg_entry_price",
                        "current_price",
                        "market_value",
                        "unrealized_pl",
                    )
                }
                for p in positions
            ],
        )

    def _validate_orders(self, orders, market_open):
        """Validate open orders: pause, timeout, partial fill."""
        for order in orders:
            if order["side"] == "buy" and not self.entries_enabled:
                self._cancel_entry(order, "pause")
                raise ValueError("Entradas en pausa: cancelación pendiente de confirmación.")
            if order["side"] == "buy" and (order.get("submitted_at") or order.get("created_at")):
                created = datetime.fromisoformat(order.get("submitted_at") or order["created_at"])
                if created.tzinfo is None:
                    raise ValueError("Orden sin zona horaria: revisa Alpaca antes de operar.")
                age = (datetime.now(UTC) - created).total_seconds()
                if age >= 120:
                    self._cancel_entry(order, "timeout_120s")
                    raise ValueError("Entrada antigua cancelándose; esperando reconciliación.")
            if order["side"] == "buy" and order.get("status") == "partially_filled":
                self._cancel_entry(order, "partial_fill")
                raise ValueError(
                    "Entrada parcial: cancelación solicitada; esperando reconciliación."
                )

    def _reconcile_position(self, position, orders, market_open, clock):
        """Reconcile a single position: validate, check stops, request protection if needed."""
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
            return False  # Position is being closed, skip further checks
        exits = [o for o in same if o["side"] == "sell"]
        if any(o.get("type") == "market" for o in exits):
            return True  # Market exit order exists, position will be closed
        entry_price = positive(position["avg_entry_price"])
        floor = float(stop_price_text(entry_price * 0.98))
        self._audit(
            "stop_reconciliation",
            {
                "entry_price": entry_price,
                "target_stop": floor,
                "requested_loss_pct": 2,
                "observed_stops": [
                    {
                        "stop_price": o.get("stop_price"),
                        "effective_loss_pct": (1 - float(o["stop_price"]) / entry_price) * 100,
                    }
                    for o in exits
                    if o.get("stop_price")
                ],
            },
            symbol,
        )
        covered = sum(
            float(o["qty"]) - float(o.get("filled_qty", 0))
            for o in exits
            if o.get("type") == "stop"
            and o.get("time_in_force") == "gtc"
            and o.get("status") in ("new", "accepted")
            and float(o.get("stop_price", 0)) >= floor
        )
        if covered >= positive(position["qty"]):
            return True  # Position fully protected
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
            self._submit(
                {
                    "symbol": symbol,
                    "qty": str(position["qty"]),
                    "side": "sell",
                    "type": "stop",
                    "stop_price": stop_price_text(floor),
                    "time_in_force": "gtc",
                    "client_order_id": order_id,
                },
            )
            self._event(f"{symbol}: protección 2% solicitada en Alpaca.")
        raise ValueError("Protección pendiente de confirmación; entradas bloqueadas.")

    def _monitor_locked(self):  # Broker reconciliation stays sequential under one lock.
        account = self._account()
        if self._check_account_stop(account):
            return None
        positions = self.broker.get("/v2/positions")
        orders = self._orders()
        self._cleanup_closing_symbols(positions, orders)
        clock = self.broker.get("/v2/clock")
        self._audit_positions(positions)
        for order in orders:
            self._track_order(order)
        market_open = clock["is_open"]
        self._validate_orders(orders, market_open)
        for position in positions:
            self._reconcile_position(position, orders, market_open, clock)
        if not self.campaign.get("closing"):
            # Reuse this cycle's fresh reads only when reconciliation made no broker writes.
            return {"account": account, "positions": positions, "orders": orders, "clock": clock}
        return None

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
        self._audit("close_intent", {"symbol": symbol}, symbol)
        sent_at = datetime.now(UTC).isoformat()
        response = self.broker.delete("/v2/positions/" + symbol)
        if (response or {}).get("id"):
            try:
                self.analytics.execution_context(
                    self.campaign["analytics_run_id"],
                    response.get("client_order_id") or response["id"],
                    {"symbol": symbol, "side": "sell", "sent_at": sent_at},
                )
            except Exception:
                self._ledger_failed()
        self._track_order(
            {"symbol": symbol, "side": "sell", **(response or {})},
        )
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
            fresh_clock = self.broker.get("/v2/clock")
            if not fresh_clock["is_open"]:
                raise ValueError("Mercado cerrado antes del envío; sin entrada.")
            now = datetime.fromisoformat(fresh_clock["timestamp"])
            if stamp.tzinfo is None or not 0 <= (now - stamp).total_seconds() <= 60:
                raise ValueError("Cotización antigua; sin entrada.")
            ask, bid = positive(quote["ap"]), positive(quote["bp"])
            if bid > ask or (ask - bid) / ask > 0.01:
                raise ValueError("Cotización inválida o spread superior al 1%; sin entrada.")
            self.quote_checked[symbol] = (datetime.now(UTC), (now - stamp).total_seconds())
            self.quote_context[symbol] = {
                "quote_at": quote["t"],
                "bid": bid,
                "ask": ask,
                "quote_checked_at": now.isoformat(),
            }
            self._audit("quote", {"feed": "iex", **quote}, symbol)
            return float(price_text(ask))

    def _calculate_volatility(self, bars, window=20):
        """Calculate annualized volatility from daily bars."""
        if len(bars) < window + 1:
            return None
        closes = [float(b["c"]) for b in bars[-window-1:]]
        returns = [(closes[i] / closes[i-1] - 1) for i in range(1, len(closes))]
        if len(returns) < 2:
            return None
        daily_vol = stdev(returns)
        return daily_vol * math.sqrt(252)

    def _calculate_volatility_size(self, equity, price, vol_annual):
        """Calculate position size based on volatility targeting."""
        if not vol_annual or vol_annual <= 0:
            logger.warning("Vol sizing: insufficient volatility data, falling back to fixed sizing")
            return 0
        # Target daily vol = vol_target_pct / sqrt(252)
        target_daily_vol = self.vol_target_pct / 100 / math.sqrt(252)
        # Position vol = position_value * daily_vol / equity
        # position_value = target_daily_vol * equity / daily_vol
        position_value = target_daily_vol * equity / vol_annual
        # Apply max position pct limit
        max_position_value = equity * self.max_position_pct / 100
        position_value = min(position_value, max_position_value)
        qty = math.floor(position_value / price)
        return max(0, qty)

    def _check_regime_filter(self):
        """Check market regime using SPY trend. Returns True if favorable."""
        if not self.regime_filter_enabled:
            return True
        try:
            # Get SPY data
            spy_bars = self._history("SPY", datetime.now(UTC).date(), 
                completed_session(self.broker, self.broker.get("/v2/clock")))
            if len(spy_bars) < 200:
                logger.warning("Regime filter: SPY data insufficient (%d bars), allowing", len(spy_bars))
                return True  # Insufficient data, allow
            spy_closes = [float(b["c"]) for b in spy_bars]
            ma200 = sum(spy_closes[-200:]) / 200
            current = spy_closes[-1]
            # Allow longs only if SPY > MA200
            if current <= ma200:
                logger.info("Regime filter: SPY %.2f <= MA200 %.2f, blocking entries", current, ma200)
                return False
            
            # ADX filter for trend strength
            if self.regime_adx_enabled and len(spy_bars) >= 14:
                adx = self._calculate_adx(spy_bars[-14:])
                if adx is not None and adx < self.regime_adx_threshold:
                    logger.info("Regime filter: ADX %.1f < %.1f, blocking entries", adx, self.regime_adx_threshold)
                    return False
            
            # VIX filter for volatility regime
            if self.regime_vix_enabled:
                vix = self._get_vix()
                if vix is not None and vix > self.regime_vix_threshold:
                    logger.info("Regime filter: VIX %.1f > %.1f, blocking entries", vix, self.regime_vix_threshold)
                    return False
            
            return True
        except Exception as e:
            logger.warning("Regime filter error: %s, allowing (fail open)", e)
            return True  # On error, allow (fail open)

    def _calculate_adx(self, bars):
        """Calculate ADX from bars. Returns None if insufficient data."""
        if len(bars) < 14:
            return None
        try:
            highs = [float(b["h"]) for b in bars]
            lows = [float(b["l"]) for b in bars]
            closes = [float(b["c"]) for b in bars]
            
            # True Range
            tr = []
            for i in range(1, len(bars)):
                tr.append(max(
                    highs[i] - lows[i],
                    abs(highs[i] - closes[i-1]),
                    abs(lows[i] - closes[i-1])
                ))
            
            # Directional Movement
            plus_dm = []
            minus_dm = []
            for i in range(1, len(bars)):
                up = highs[i] - highs[i-1]
                down = lows[i-1] - lows[i]
                plus_dm.append(up if up > down and up > 0 else 0)
                minus_dm.append(down if down > up and down > 0 else 0)
            
            # Smooth with Wilder's smoothing (14-period)
            atr = sum(tr[:14]) / 14
            plus_di = sum(plus_dm[:14]) / 14 / atr * 100 if atr > 0 else 0
            minus_di = sum(minus_dm[:14]) / 14 / atr * 100 if atr > 0 else 0
            
            for i in range(14, len(tr)):
                atr = (atr * 13 + tr[i]) / 14
                plus_di = (plus_di * 13 + plus_dm[i] / atr * 100) / 14 if atr > 0 else 0
                minus_di = (minus_di * 13 + minus_dm[i] / atr * 100) / 14 if atr > 0 else 0
            
            dx = abs(plus_di - minus_di) / (plus_di + minus_di) * 100 if (plus_di + minus_di) > 0 else 0
            return dx
        except Exception:
            return None

    def _get_vix(self):
        """Get VIX from Alpaca. Returns None if unavailable."""
        try:
            # Try to get VIX data - Alpaca may not have VIX directly
            # This is a placeholder for when VIX data becomes available
            return None
        except Exception:
            return None

    def _check_daily_drawdown(self, account):
            """Check daily drawdown kill switch. Returns True if should stop."""
            if not self.campaign:
                return False
            current_equity = positive(account["equity"])
            day_start_equity = self.campaign.get("day_start_equity")
            if day_start_equity is None:
                # First check of the day, record starting equity
                self.campaign["day_start_equity"] = current_equity
                # Also initialize intraday high-water mark
                self.campaign["intraday_high_equity"] = current_equity
                self._persist()
                return False
        
            # Update intraday high-water mark
            intraday_high = self.campaign.get("intraday_high_equity", day_start_equity)
            if current_equity > intraday_high:
                self.campaign["intraday_high_equity"] = current_equity
                self._persist()
                intraday_high = current_equity
        
            # Check drawdown from intraday high (more protective than day start)
            daily_loss_pct = (1 - current_equity / intraday_high) * 100
            if daily_loss_pct >= self.max_daily_loss_pct:
                self.campaign["tripped"] = True
                self._persist()
                self._event(f"Límite diario alcanzado: pérdida {daily_loss_pct:.2f}% > {self.max_daily_loss_pct}% (desde máximo intradía {intraday_high:.2f})")
                self.alerts.account_stop_triggered(current_equity, intraday_high, daily_loss_pct)
                return True
            return False

    def _calculate_trailing_stop(self, position, current_price):
            """Calculate trailing stop price for a position."""
            entry_price = positive(position["avg_entry_price"])
            # Initial stop
            initial_stop = entry_price * (1 - self.trailing_stop_pct / 100)
            # Trailing stop: track highest price since entry
            symbol = position["symbol"]
            high_key = f"high_since_entry_{symbol}"
            high_since_entry = self.campaign.get(high_key, entry_price)
            if current_price > high_since_entry:
                high_since_entry = current_price
                self.campaign[high_key] = high_since_entry
                self._persist()
            # Trailing stop: highest price since entry * (1 - trailing_pct)
            trailing_stop = high_since_entry * (1 - self.trailing_stop_pct / 100)
            # Return the higher of initial and trailing (more protective)
            return max(initial_stop, trailing_stop)

    def _check_correlation(self, universe, new_symbol, positions):
        """Check correlation of new position with existing positions."""
        if not self.correlation_check_enabled:
            return True
        if len(positions) == 0:
            return True
        try:
            window = self.correlation_window
            new_bars = universe.get(new_symbol, [])
            if len(new_bars) < window:
                return True
            new_closes = [float(b["c"]) for b in new_bars[-window:]]
            new_returns = [(new_closes[i] / new_closes[i-1] - 1) for i in range(1, len(new_closes))]
            
            for symbol in positions:
                pos_bars = universe.get(symbol, [])
                if len(pos_bars) < window:
                    continue
                pos_closes = [float(b["c"]) for b in pos_bars[-window:]]
                pos_returns = [(pos_closes[i] / pos_closes[i-1] - 1) for i in range(1, len(pos_closes))]
                
                # Calculate correlation
                if len(new_returns) != len(pos_returns):
                    continue
                n = len(new_returns)
                mean_new = sum(new_returns) / n
                mean_pos = sum(pos_returns) / n
                cov = sum((new_returns[i] - mean_new) * (pos_returns[i] - mean_pos) for i in range(n)) / n
                std_new = math.sqrt(sum((r - mean_new)**2 for r in new_returns) / n)
                std_pos = math.sqrt(sum((r - mean_pos)**2 for r in pos_returns) / n)
                if std_new > 0 and std_pos > 0:
                    corr = cov / (std_new * std_pos)
                    if corr > 0.7:  # High correlation threshold
                        return False
            return True
        except Exception:
            return True


    def _cycle(self, execute):  # noqa: C901 - Reserve, validate and submit under one lock.
        if not execute:
            return super()._cycle(False)
        if not self.entries_enabled or not self.campaign:
            return [{"status": "Autonomía en pausa; activa GO PAPER para iniciar."}]
        snapshot = self._monitor_locked()
        if self.campaign["tripped"]:
            return [{"status": "Límite global alcanzado; campaña bloqueada."}]
        clock = snapshot["clock"] if snapshot else self.broker.get("/v2/clock")
        if not clock["is_open"]:
            return [{"status": "Mercado cerrado; conserva posiciones y stops GTC."}]
        account = snapshot["account"] if snapshot else self._account()
        
        # Check daily drawdown kill switch
        if self._check_daily_drawdown(account):
            return [{"status": "Límite diario alcanzado; campaña bloqueada."}]
        
        # Check regime filter
        if not self._check_regime_filter():
            return [{"status": "Régimen desfavorable (SPY < MA200); entradas bloqueadas."}]
        
        orders = snapshot["orders"] if snapshot else self._orders()
        position_rows = snapshot["positions"] if snapshot else self.broker.get("/v2/positions")
        positions = {p["symbol"]: p for p in position_rows}
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
        decision_at = datetime.now(UTC).isoformat()
        data_hash = self.analytics.dataset(universe)
        self._audit(
            "decision",
            {
                "strategy": self.strategy,
                "revision": self.revision,
                "session": session.isoformat(),
                "input_sha256": data_hash,
                "feed": "iex",
                "adjustment": self.history_adjustment,
                "decisions": decisions,
            },
        )
        cash = available_cash(account, orders)
        equity = positive(account["equity"])
        allocation = equity / (
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
            # Check correlation with existing positions
            if not self._check_correlation(universe, symbol, positions):
                row["status"] = "Correlación alta con posiciones existentes; entrada bloqueada."
                continue
            order_id = self._id(symbol, "buy", session.isoformat())
            if order_id in self.seen:
                row["status"] = "Señal de esta sesión ya tramitada."
                continue
            limit = self._quote(symbol, clock)
            self.quote_context[symbol].update(
                signal_session=session.isoformat(), decision_at=decision_at
            )
            # Volatility-based position sizing (if enabled)
            if self.vol_sizing_enabled:
                vol = self._calculate_volatility(universe[symbol])
                qty = self._calculate_volatility_size(equity, limit, vol)
            else:
                qty = math.floor(min(allocation, cash) / (limit * 1.01))
            if qty <= 0:
                row["status"] = "Tamaño calculado por volatilidad es cero."
                continue
            # Also respect cash limit
            max_qty_cash = math.floor(min(allocation, cash) / (limit * 1.01))
            qty = min(qty, max_qty_cash)
            if qty <= 0:
                row["status"] = "Efectivo insuficiente para una acción."
                continue
            # A limit entry bounds purchase cost. Its attached stop remains broker-side.
            self._reserve(order_id)
            self._submit(
                {
                    "symbol": symbol,
                    "qty": str(qty),
                    "side": "buy",
                    "type": "limit",
                    "limit_price": price_text(limit),
                    "time_in_force": "gtc",
                    "order_class": "oto",
                    "stop_loss": {"stop_price": stop_price_text(limit * 0.98)},
                    "client_order_id": order_id,
                },
            )
            cash -= qty * limit * 1.01
            row.update(qty=qty, status="Entrada limitada con stop 2% enviada a Paper.")
            self._event(f"{symbol}: {qty} acciones solicitadas con stop adjunto.")
        return rows