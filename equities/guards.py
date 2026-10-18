"""Fail-closed checks for completed exchange sessions and uncommitted cash."""

import math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


NY = ZoneInfo("America/New_York")


def completed_session(broker, clock):
    now = datetime.fromisoformat(clock["timestamp"].replace("Z", "+00:00"))
    if now.tzinfo is None:
        raise ValueError("Reloj de Alpaca sin zona horaria; no se opera.")
    now = now.astimezone(NY)
    calendar = broker.get(
        "/v2/calendar",
        start=(now.date() - timedelta(days=21)).isoformat(),
        end=now.date().isoformat(),
    )
    completed = []
    for session in calendar:
        close = datetime.fromisoformat(session["date"] + "T" + session["close"]).replace(tzinfo=NY)
        if close <= now:
            completed.append(close.date())
    if not completed:
        raise ValueError("No se pudo verificar última sesión completa; no se opera.")
    return max(completed)


def nonnegative(value):
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError("Valor monetario inválido.")
    return number


def available_cash(account, pending):
    """BP already includes broker reservations; separately cap cash to avoid margin."""
    try:
        cash = max(0, float(account["cash"]))
        if not math.isfinite(cash):
            return 0
        buying_power = nonnegative(account["buying_power"])
        non_margin = nonnegative(account.get("non_marginable_buying_power", buying_power))
        reserved = 0
        for order in pending:
            if order.get("side") == "sell":
                continue
            if order.get("side") != "buy":
                return 0
            if order.get("notional") is not None:
                # Full notional is conservative if part of the order has filled.
                reserved += nonnegative(order["notional"])
            elif order.get("limit_price") is not None:
                remaining = max(
                    0, nonnegative(order["qty"]) - nonnegative(order.get("filled_qty", 0))
                )
                reserved += remaining * nonnegative(order["limit_price"])
            else:
                # Pending market/stop buys have no bounded execution cost.
                return 0
        return max(0, min(cash - reserved, buying_power, non_margin))
    except (KeyError, TypeError, ValueError):
        return 0
