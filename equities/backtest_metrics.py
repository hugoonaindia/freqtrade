"""Close-to-close portfolio diagnostics; undefined ratios are None, never infinity."""

import math
from datetime import date
from statistics import fmean, stdev


def daily_returns(curve, capital):
    previous = capital
    result = []
    for point in curve:
        result.append(point["equity"] / previous - 1)
        previous = point["equity"]
    return result


def compounded(values):
    return math.prod(1 + value for value in values) - 1


def metrics(curve, capital, trades):
    returns = daily_returns(curve, capital)
    peak = capital
    max_drawdown = 0
    underwater = longest = 0
    peak_date = date.fromisoformat(curve[0]["date"])
    longest_days = 0
    months = {}
    for point, change in zip(curve, returns, strict=True):
        current_date = date.fromisoformat(point["date"])
        max_drawdown = max(max_drawdown, 1 - point["equity"] / peak)
        if point["equity"] >= peak:
            if underwater:
                longest_days = max(longest_days, (current_date - peak_date).days)
            peak, peak_date, underwater = point["equity"], current_date, 0
        else:
            underwater += 1
            longest = max(longest, underwater)
            longest_days = max(longest_days, (current_date - peak_date).days)
        months.setdefault(point["date"][:7], []).append(change)
    closed = [trade["realized_pnl"] for trade in trades if trade.get("realized_pnl") is not None]
    return {
        "final_equity": curve[-1]["equity"],
        "return_pct": (curve[-1]["equity"] / capital - 1) * 100,
        "max_drawdown_pct": max_drawdown * 100,
        "underwater_sessions": longest,
        "underwater_calendar_days": longest_days,
        "recovered": underwater == 0,
        "current_underwater_sessions": underwater,
        "worst_day_pct": min(returns) * 100,
        "worst_month_pct": min(compounded(values) for values in months.values()) * 100,
        "volatility_annual_pct": stdev(returns) * math.sqrt(252) * 100
        if len(returns) > 1
        else None,
        "average_exposure_pct": fmean(point["exposure"] for point in curve) * 100,
        "invested_sessions_pct": sum(point["exposure"] > 0 for point in curve) / len(curve) * 100,
        "orders": len(trades),
        "closed_trades": len(closed),
        "win_rate_pct": sum(pnl > 0 for pnl in closed) / len(closed) * 100 if closed else None,
        "fees": sum(trade["fee"] for trade in trades),
    }


def protection(strategy, benchmark):
    baseline = benchmark["max_drawdown_pct"]
    improvement = baseline - strategy["max_drawdown_pct"]
    return {
        "drawdown_reduction_pp": improvement,
        "drawdown_reduction_pct": improvement / baseline * 100 if baseline > 0 else None,
    }


def capture(curve, reference, capital):
    own = daily_returns(curve, capital)
    market = daily_returns(reference, capital)
    output = {}
    for label, predicate in [("downside", lambda v: v < 0), ("upside", lambda v: v > 0)]:
        indices = [i for i, value in enumerate(market) if predicate(value)]
        numerator = fmean(own[i] for i in indices) if indices else None
        denominator = fmean(market[i] for i in indices) if indices else None
        output[label + "_capture_daily_pct"] = (
            numerator / denominator * 100 if denominator else None
        )
        output[label + "_sessions"] = len(indices)
    return output


def grouped_returns(curve, capital, groups):
    changes = daily_returns(curve, capital)
    result = {}
    for label in dict.fromkeys(groups):
        values = [change for change, group in zip(changes, groups, strict=True) if group == label]
        result[label] = {"sessions": len(values), "return_pct": compounded(values) * 100}
    return result
