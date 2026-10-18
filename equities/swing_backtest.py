"""Conservative daily-bar approximation of the protected swing profile."""

import math

from .strategies import decide, rotation


def simulate_swing(histories, config, strategy):  # noqa: C901 - Ordered daily fills share portfolio state.
    from .backtest import Portfolio, past_bars, prepare, session_date
    from .strategies import STRATEGIES

    universe, days = prepare(histories, config, STRATEGIES[strategy]["bars"])
    portfolio = Portfolio(config)
    stops = {}
    tripped = None
    floor = config.capital * 0.98

    def exit_position(symbol, day, price, reason):
        before = len(portfolio.trades)
        portfolio.fill(symbol, "sell", portfolio.positions[symbol], {"o": price}, day, None)
        if len(portfolio.trades) > before:
            portfolio.trades[-1]["reason"] = reason
        stops.pop(symbol, None)

    for day in days:
        stopped = set()
        opening = portfolio.cash + sum(
            qty * universe[symbol][day]["o"] for symbol, qty in portfolio.positions.items()
        )
        if opening <= floor:
            tripped = tripped or day
        if tripped:
            for symbol in list(portfolio.positions):
                exit_position(symbol, day, universe[symbol][day]["o"], "account_stop")
            portfolio.mark(day, {symbol: bars[day] for symbol, bars in universe.items()})
            continue
        # Existing stops execute at the worse opening price if the market gaps below them.
        for symbol in list(portfolio.positions):
            bar = universe[symbol][day]
            if bar["l"] <= stops[symbol]:
                exit_position(symbol, day, min(bar["o"], stops[symbol]), "position_stop")
                stopped.add(symbol)
        past = {symbol: past_bars(bars, day) for symbol, bars in universe.items()}
        decisions = (
            rotation(past)
            if strategy == "momentum"
            else {symbol: decide(strategy, bars) for symbol, bars in past.items()}
        )
        spendable = portfolio.cash
        allocation = (portfolio.curve[-1]["equity"] if portfolio.curve else config.capital) / (
            1 if strategy == "momentum" else len(universe)
        )
        for symbol, decision in decisions.items():
            if symbol in stopped:
                continue
            bar = universe[symbol][day]
            if decision["signal"] == "sell" and symbol in portfolio.positions:
                # Stop takes precedence on ambiguous intraday paths.
                exit_position(symbol, day, bar["o"], "strategy")
            elif decision["signal"] == "buy" and symbol not in portfolio.positions:
                # No historical quotes: approximate limit using the previous session close.
                limit = past[symbol][-1]["c"]
                if bar["l"] > limit:
                    continue
                qty = math.floor(min(allocation, spendable) / (limit * 1.01))
                fill = min(min(bar["o"], limit) * (1 + config.slippage_bps / 10000), limit)
                before = portfolio.cash
                portfolio.fill(
                    symbol,
                    "buy",
                    qty,
                    {"o": fill / (1 + config.slippage_bps / 10000)},
                    day,
                    session_date(past[symbol][-1]),
                )
                spendable -= before - portfolio.cash
                if symbol in portfolio.positions:
                    stops[symbol] = limit * 0.98
                    portfolio.trades[-1]["stop_price"] = stops[symbol]
                    if bar["l"] <= stops[symbol]:
                        exit_position(symbol, day, min(bar["o"], stops[symbol]), "position_stop")
                        stopped.add(symbol)
        portfolio.mark(day, {symbol: bars[day] for symbol, bars in universe.items()})
        if portfolio.curve[-1]["equity"] <= floor:
            tripped = day
    report = portfolio.report()
    report["risk"] = {
        "profile": "swing",
        "account_stop_date": tripped,
        "liquidation_pending": bool(tripped and portfolio.positions),
    }
    return report
