"""Measure broker execution without confusing observation time with fill time."""

from datetime import datetime, timedelta
import math


def seconds_between(first, last):
    if not first or not last:
        return None
    try:
        a, b = datetime.fromisoformat(first), datetime.fromisoformat(last)
        if a.tzinfo is None or b.tzinfo is None:
            return None
        value = (b - a).total_seconds()
        return value if value >= 0 else None
    except (TypeError, ValueError):
        return None


def execution_quality(context, order):
    result = {
        **context,
        "symbol": order.get("symbol", context.get("symbol")),
        "status": order.get("status"),
        "order_id": order.get("id"),
        "client_order_id": order.get("client_order_id"),
        "quote_to_fill_seconds": seconds_between(context.get("quote_at"), order.get("filled_at")),
        "decision_to_send_seconds": seconds_between(
            context.get("decision_at"), context.get("sent_at")
        ),
        "filled_qty": order.get("filled_qty"),
        "filled_at": order.get("filled_at"),
        "observed_at": context.get("observed_at"),
        "broker_submitted_at": order.get("submitted_at"),
        "broker_created_at": order.get("created_at"),
        "canceled_at": order.get("canceled_at"),
        "fill_delay_seconds": seconds_between(
            order.get("submitted_at") or order.get("created_at"), order.get("filled_at")
        ),
        "cancel_delay_seconds": seconds_between(
            context.get("cancel_requested_at"), order.get("canceled_at")
        ),
    }
    deadline = context.get("deadline_at")
    submitted = order.get("submitted_at") or order.get("created_at")
    if order.get("side") == "buy" and submitted:
        try:
            origin = datetime.fromisoformat(submitted)
            if origin.tzinfo is not None:
                deadline = (origin + timedelta(seconds=120)).isoformat()
                result["deadline_at"] = deadline
                result["deadline_origin"] = (
                    "broker_submitted_at" if order.get("submitted_at") else "broker_created_at"
                )
        except (TypeError, ValueError):
            pass
    late = seconds_between(deadline, order.get("filled_at"))
    result["filled_after_deadline"] = late is not None and late > 0
    result["seconds_after_deadline"] = late
    try:
        price = float(order.get("filled_avg_price") or 0)
        bid, ask = float(context.get("bid") or 0), float(context.get("ask") or 0)
        if (
            price > 0
            and bid > 0
            and ask >= bid
            and all(math.isfinite(v) for v in (price, bid, ask))
        ):
            if context.get("initial_stop_price") and order.get("side") == "buy":
                result["initial_stop_distance_pct"] = (
                    1 - float(context["initial_stop_price"]) / price
                ) * 100
            direction = 1 if order.get("side") == "buy" else -1
            reference = ask if direction == 1 else bid
            mid = (ask + bid) / 2
            result.update(
                fill_price=price,
                spread_bps=(ask - bid) / mid * 10000,
                slippage_bps=direction * (price / reference - 1) * 10000,
                cost_vs_mid_bps=direction * (price / mid - 1) * 10000,
            )
    except (TypeError, ValueError):
        pass
    return result
