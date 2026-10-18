"""Selectable long-only daily strategies with shared paper-order safeguards."""

import hashlib
import json
import math
import re
import threading
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .guards import available_cash, completed_session
from .strategies import STRATEGIES, decide, rotation


def signal(closes):
    """Compatibility entry point for the default SMA crossover strategy."""
    return decide("sma", [{"c": close} for close in closes])["signal"]


class SelectionChanged(ValueError):
    """A request references a selection no longer active."""


class Engine:
    def __init__(self, broker, symbols, budget, state=None, strategy="sma"):
        if (
            not symbols
            or len(symbols) != len(set(symbols))
            or any(not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", s) for s in symbols)
        ):
            raise ValueError("Usa símbolos de acciones únicos, por ejemplo AAPL,MSFT.")
        if not math.isfinite(budget) or budget <= 0:
            raise ValueError("Presupuesto debe ser positivo y finito.")
        if strategy not in STRATEGIES:
            raise ValueError("Estrategia desconocida.")
        self.entries_enabled = True
        self.strategy = strategy
        self.revision = 0
        self.broker, self.symbols, self.budget = broker, symbols, budget
        self.state = Path(state) if state else None
        self.seen = (
            set(json.loads(self.state.read_text())) if self.state and self.state.exists() else set()
        )
        self.lock = threading.Lock()
        self.last = []

    def _reserve(self, order_id):
        self.seen.add(order_id)
        if self.state:
            self.state.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            tmp = self.state.with_suffix(".tmp")
            tmp.write_text(json.dumps(sorted(self.seen)))
            tmp.replace(self.state)

    def selection(self):
        with self.lock:
            return {"strategy": self.strategy, "revision": self.revision}

    def cycle(self, execute=True, expected_strategy=None, expected_revision=None):
        with self.lock:
            if (expected_strategy is not None and expected_strategy != self.strategy) or (
                expected_revision is not None and expected_revision != self.revision
            ):
                raise SelectionChanged(
                    "Estrategia cambió en otra pestaña. Actualiza panel y revisa señales."
                )
            self.last = self._cycle(execute)
            return self.last

    def select_strategy(self, strategy):
        if strategy not in STRATEGIES:
            raise ValueError("Estrategia desconocida.")
        with self.lock:
            if self.strategy != strategy:
                self.revision += 1
            self.strategy = strategy
            self.last = []
            return {"strategy": self.strategy, "revision": self.revision}

    def close_positions(self):
        """Close existing long equity positions through Alpaca liquidation endpoint."""
        with self.lock:
            b = self.broker
            if not b.get("/v2/clock")["is_open"]:
                raise ValueError("Mercado cerrado. No se envían cierres fuera de sesión.")
            account = b.get("/v2/account")
            if account.get("trading_blocked") or account.get("account_blocked"):
                raise ValueError("Cuenta bloqueada para operar.")
            pending = b.get("/v2/orders", status="open", limit=500)
            if len(pending) >= 500:
                raise ValueError("Demasiadas órdenes abiertas para cerrar con seguridad.")
            pending_symbols = {order["symbol"] for order in pending}
            positions = b.get("/v2/positions")
            rows = []
            for position in positions:
                symbol = position["symbol"]
                row = {"symbol": symbol, "signal": "sell", "status": "Sin cierre"}
                rows.append(row)
                if (
                    position.get("asset_class") != "us_equity"
                    or not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", symbol)
                    or float(position["qty"]) <= 0
                ):
                    row["status"] = "Omitida: solo posiciones largas de acciones."
                elif symbol in pending_symbols:
                    row["status"] = "Orden pendiente; espera antes de cerrar."
                elif not b.get("/v2/clock")["is_open"]:
                    row["status"] = "Mercado cerrado antes del envío; sin cierre."
                else:
                    try:
                        # Broker computes remaining position: never submit a raw sell quantity.
                        b.delete("/v2/positions/" + symbol)
                        row["status"] = "Cierre solicitado en Paper; ejecución pendiente."
                    except ValueError as exc:
                        row["status"] = str(exc) + " Revisa Alpaca antes de reintentar."
            self.last = rows
            return rows

    def _history(self, symbol, today, expected_session):
        response = self.broker.get(
            "/v2/stocks/" + symbol + "/bars",
            data=True,
            timeframe="1Day",
            start=(today - timedelta(days=400)).isoformat(),
            end=(expected_session + timedelta(days=1)).isoformat(),
            adjustment="all",
            feed="iex",
            sort="asc",
            limit=1000,
        )
        if response.get("next_page_token"):
            raise ValueError("Datos incompletos; no se opera.")
        bars = sorted(
            (
                v
                for v in response.get("bars", [])
                if datetime.fromisoformat(v["t"].replace("Z", "+00:00"))
                .astimezone(ZoneInfo("America/New_York"))
                .date()
                <= expected_session
            ),
            key=lambda v: v["t"],
        )
        if bars:
            last_date = (
                datetime.fromisoformat(bars[-1]["t"].replace("Z", "+00:00"))
                .astimezone(ZoneInfo("America/New_York"))
                .date()
            )
            if last_date != expected_session:
                return []
        return bars

    def _cycle(self, execute):  # noqa: C901 - One locked cycle keeps order guards sequential.
        b = self.broker
        clock = b.get("/v2/clock")
        market_open = clock["is_open"]
        if execute and not market_open:
            return [{"status": "Mercado cerrado; sin órdenes."}]
        account = b.get("/v2/account")
        if account.get("trading_blocked") or account.get("account_blocked"):
            raise ValueError("Cuenta bloqueada para operar.")
        positions = {p["symbol"]: p for p in b.get("/v2/positions")}
        pending = b.get("/v2/orders", status="open", limit=500)
        if len(pending) >= 500:
            raise ValueError("Demasiadas órdenes abiertas para reconciliar con seguridad.")
        pending_symbols = {p["symbol"] for p in pending}
        cash = available_cash(account, pending)
        expected_session = completed_session(b, clock)
        today = (
            datetime.fromisoformat(clock["timestamp"].replace("Z", "+00:00"))
            .astimezone(ZoneInfo("America/New_York"))
            .date()
        )
        universe, unavailable = {}, {}
        # Fetch the full universe before making any rotation decisions or writes.
        for symbol in self.symbols:
            asset = b.get("/v2/assets/" + symbol)
            if (
                asset.get("class") != "us_equity"
                or not asset.get("tradable")
                or asset.get("status") != "active"
            ):
                unavailable[symbol] = "Activo no apto: solo acciones estadounidenses activas"
                universe[symbol] = []
            else:
                universe[symbol] = self._history(symbol, today, expected_session)
        decisions = (
            rotation(universe)
            if self.strategy == "momentum"
            else {s: decide(self.strategy, data) for s, data in universe.items()}
        )
        rows = []
        for symbol, bars in universe.items():
            decision = decisions[symbol]
            row = {"symbol": symbol, "strategy": self.strategy, "status": "Sin orden", **decision}
            rows.append(row)
            if symbol in unavailable:
                row["status"] = unavailable[symbol]
                continue
            if not bars or decision["signal"] == "hold":
                if not bars:
                    row["reason"] = (
                        f"Sin datos válidos de última sesión completa ({expected_session})"
                    )
                row["status"] = row["reason"]
                continue
            row.update(close=float(bars[-1]["c"]), bar=bars[-1]["t"])
            if symbol in pending_symbols:
                row["status"] = "Orden pendiente; esperando"
                continue
            held = float(positions.get(symbol, {}).get("qty", 0))
            if held < 0:
                row["status"] = "Posición corta existente; requiere revisión"
                continue
            action, price = decision["signal"], row["close"]
            qty = 0
            if action == "buy" and not self.entries_enabled:
                row["status"] = "Entradas bloqueadas; solo cierre manual."
                continue
            if action == "buy" and held == 0:
                qty = math.floor(min(self.budget, cash) / (price * 1.05))
            elif action == "sell" and held > 0:
                qty = math.floor(held)
            if qty <= 0:
                row["status"] = "Sin cambio de posición o efectivo insuficiente"
                continue
            # Keep original namespace: switching strategies cannot duplicate same-day orders.
            order_id = (
                "eq-"
                + hashlib.sha256(f"sma5-20:{symbol}:{bars[-1]['t']}:{action}".encode()).hexdigest()[
                    :40
                ]
            )
            row.update(qty=qty, side=action)
            if order_id in self.seen:
                row["status"] = "Señal ya tramitada; sin duplicar"
                continue
            if not execute:
                row["status"] = "Vista previa; no enviada" + (
                    "; mercado cerrado" if not market_open else ""
                )
                if action == "buy":
                    cash -= qty * price * 1.05
                continue
            if not b.get("/v2/clock")["is_open"]:
                row["status"] = "Mercado cerrado antes del envío; sin orden"
                continue
            self._reserve(order_id)
            b.post(
                "/v2/orders",
                {
                    "symbol": symbol,
                    "qty": str(qty),
                    "side": action,
                    "type": "market",
                    "time_in_force": "day",
                    "client_order_id": order_id,
                },
            )
            if action == "buy":
                cash -= qty * price * 1.05
            row["status"] = "Orden enviada a Alpaca Paper; ejecución pendiente de bróker"
        return rows
