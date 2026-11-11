"""Exploratory daily-bar simulation with explicit event chronology."""

import math

from .strategies import decide, rotation


def simulate_swing(histories, config, strategy, *, _prepared=None):
    from .backtest import Portfolio, past_bars, prepare, session_date
    from .strategies import STRATEGIES

    universe, days = _prepared or prepare(histories, config, STRATEGIES[strategy]["bars"])
    portfolio = Portfolio(config)
    stops = {}
    tripped = None
    floor = config.capital * 0.98
    ambiguous = 0

    def exit_position(symbol, day, price, reason, phase, signal_day=None):
        before = len(portfolio.trades)
        portfolio.fill(symbol, "sell", portfolio.positions[symbol], {"o": price}, day, signal_day)
        if len(portfolio.trades) > before:
            portfolio.trades[-1].update(reason=reason, phase=phase)
        stops.pop(symbol, None)

    for day in days:
        stopped = set()
        spendable = portfolio.cash  # Never spend proceeds from later events in this session.
        opening = portfolio.cash + sum(
            qty * universe[symbol][day]["o"] for symbol, qty in portfolio.positions.items()
        )
        if opening <= floor:
            tripped = tripped or day
        if tripped:
            for symbol in list(portfolio.positions):
                exit_position(symbol, day, universe[symbol][day]["o"], "account_stop", "open")
            portfolio.mark(day, {symbol: bars[day] for symbol, bars in universe.items()})
            continue
        window = None if strategy == "macd" else STRATEGIES[strategy]["bars"]
        past = {symbol: past_bars(bars, day, window) for symbol, bars in universe.items()}
        decisions = (
            rotation(past, _validated=True)
            if strategy == "momentum"
            else {symbol: decide(strategy, bars, _validated=True) for symbol, bars in past.items()}
        )
        # Phase 1: opening gaps and strategy exits. A future daily low cannot override them.
        for symbol in list(portfolio.positions):
            bar = universe[symbol][day]
            if bar["o"] <= stops[symbol]:
                exit_position(symbol, day, bar["o"], "position_stop", "open")
                stopped.add(symbol)
            elif decisions[symbol]["signal"] == "sell":
                exit_position(
                    symbol, day, bar["o"], "strategy", "open", session_date(past[symbol][-1])
                )
        allocation = (portfolio.curve[-1]["equity"] if portfolio.curve else config.capital) / (
            1 if strategy == "momentum" else len(universe)
        )
        # Phase 2: entry approximation. Daily bars cannot prove a fill within 120 seconds.
        for symbol, decision in decisions.items():
            if symbol in stopped or decision["signal"] != "buy" or symbol in portfolio.positions:
                continue
            bar = universe[symbol][day]
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
                stops[symbol] = fill * 0.98
                phase = "open" if bar["o"] <= limit else "intraday_unknown"
                portfolio.trades[-1].update(stop_price=stops[symbol], phase=phase)
                ambiguous += phase == "intraday_unknown"
        # Phase 3: remaining intraday stops, after opening exits have already completed.
        for symbol in list(portfolio.positions):
            bar = universe[symbol][day]
            if bar["l"] <= stops[symbol]:
                exit_position(symbol, day, stops[symbol], "position_stop", "intraday_unknown")
                ambiguous += 1
        portfolio.mark(day, {symbol: bars[day] for symbol, bars in universe.items()})
        if portfolio.curve[-1]["equity"] <= floor:
            tripped = day
    report = portfolio.report()
    report["risk"] = {
        "profile": "swing",
        "account_stop_date": tripped,
        "liquidation_pending": bool(tripped and portfolio.positions),
    }
    report["execution"] = {
        "resolution": "1Day",
        "quality": "exploratory",
        "ambiguous_events": ambiguous,
        "entry_timeout_modeled": False,
    }
    return report
