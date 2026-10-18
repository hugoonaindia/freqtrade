# Five reviewed equities defects

Implemented with regression tests failing before production changes:

1. Guard HTTP cycles with required strategy/revision; compare atomically under engine lock, reject stale selection with 409. UI clears stale signals and serializes background refresh with interaction.
2. Publish Automation lifecycle and sanitized failure message independently of broker account availability. Poll dashboard every 15 seconds. Stop after ambiguous failures; do not retry automatically.
3. Cap buys by cash minus pending buy reservations, buying power and non-margin buying power. Unknown pending cost blocks buys, never long exits.
4. Prioritize Bollinger middle-band exit over recovery entry.
5. Require exact most recent completed Alpaca calendar session using broker clock; account for weekends, holidays and early closes, fail closed on missing calendar/data.

Verification: isolated unittest regressions, full equities suite, Ruff, JavaScript syntax, read-only Alpaca preview, browser verification. No broker orders are sent during verification.
