# Six Alpaca paper strategies

**Goal:** Make all six discussed daily equities strategies selectable from CLI and dashboard.
**Architecture:** Pure signal functions over completed daily OHLC bars, one selected strategy per engine. Momentum uses a complete fresh universe snapshot and selects one positive 63-session winner. Keep broker, paper endpoint and submission guards shared.
**Tech Stack:** Python standard library, existing HTML dashboard and unittest.

Rules:
- SMA: 5/20 true cross, 21 completed bars.
- Mean reversion: 20-close population z-score <= -2 entry, close >= mean exit.
- Bollinger: recover inside lower 20/2 band after prior outside close; exit at middle band.
- Breakout: close above previous 20 highs; exit below previous 10 lows.
- Momentum: highest positive return over 63 sessions; deterministic symbol tie-break; exit non-winners; incomplete universe means no rotation.
- MACD: SMA-seeded EMA 12/26, EMA9 signal, true cross, minimum 35 bars.

1. Add failing tests for each signal, warmups, invalid data, rotation and engine selection.
2. Implement pure strategies and integrate engine fetching/decisions. Deduplicate by symbol/bar/side across strategy switches; retain old submission namespace.
3. Add CLI option, protected strategy selector, server tests, table of signal explanations.
4. Document exact defaults, restart behavior and limits. Run all isolated tests; verify broker read-only previews and browser UI. Do not send paper orders during verification.

No parallel execution of strategies; no profitability claims or backtester added. Strategy selector is session-local; CLI sets restart default. Auto mode locks dashboard selection to prevent unnoticed changes to an active worker.
