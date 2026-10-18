# Equities Backtester Implementation Plan

**Goal:** Compare six daily equities strategies and loss protection, independently of Alpaca Paper execution.
**Architecture:** A pure portfolio simulator consumes validated daily OHLC histories; a read-only Alpaca loader supplies split-adjusted bars and exchange calendar. A separate asynchronous job runner exposes progress/results in dashboard and CLI exports JSON. Backtests never invoke broker order methods or alter active strategy.
**Tech Stack:** Python standard library, unittest, existing HTML/JavaScript dashboard.

Decisions from user discussion:
- Same dates, universe, initial capital, cost assumptions and whole-share execution across strategies.
- Signal uses only completed prior sessions; fill at following session open plus adverse slippage and commissions. Final positions marked to close, never force-liquidated invisibly.
- Keep fixed entry budget, long-only, no margin, no pyramiding; conservative 5% prior-close sizing buffer consistent with Paper; next-open cash cap. Sell proceeds available next cycle, like Paper snapshots.
- Buy-and-hold with same per-symbol entry budget is primary reference; fully allocated equal-weight buy-and-hold is secondary. Show exposure explicitly.
- Return, daily-close max drawdown, reduction in percentage points and relative percent, longest underwater period, recovery state, downside/upside capture, worst day/month and annualized volatility. Return by causal prior-20-session basket trend regimes (bull >2%, bear <-2%, otherwise sideways), plus unclassified warmup.
- No optimization or claims of out-of-sample validation: report first/second chronological halves separately and year breakdown. User-selected universe has survivorship bias.
- Historical prices split-adjusted; dividends, taxes, cash interest and actual corporate-action share accounting omitted and prominently disclosed. Metrics are historical price simulations, not guaranteed executable fills.
- Loader paginates, validates finite OHLC, distinct NY dates, exact calendar coverage and warmup. No silent dropping missing sessions. Limit 8 symbols, 10 years and one concurrent job.

Tasks:
1. Failing tests: future-data invariance, next-open fills, fees/slippage, cash constraints, short prohibition, whole-universe momentum, deterministic results, drawdown/capture denominators and warmup/missing dates.
2. Implement simulator and metrics with trade ledger and equity curves.
3. Failing tests for read-only paginated loader and asynchronous jobs; implement using separate Alpaca client in production to preserve Paper isolation.
4. Add protected start/status/report endpoints, dashboard form/comparison/chart/download and standalone CLI.
5. Verify full suite, lint, JS syntax, real historical read-only run and browser flow. Review with independent reviewer before completion. Restart dashboard preserving existing Paper settings if necessary; report any interruption.
