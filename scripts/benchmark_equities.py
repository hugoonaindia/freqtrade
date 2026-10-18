"""Repeatable CPU benchmark using synthetic data; no broker connection or orders."""

import argparse
import hashlib
import json
import math
import statistics
import sys
import time
from datetime import date, timedelta
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from equities.backtest import Config, compare


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sessions", type=int, default=1000)
    parser.add_argument("--symbols", type=int, default=4)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    if not 66 <= args.sessions <= 3660 or not 1 <= args.symbols <= 8 or args.repeats < 1:
        parser.error("Use 66-3660 sessions, 1-8 symbols and at least one repetition.")
    histories = {}
    symbols = ["AAPL", "MSFT", "NVDA", "AMD", "GOOG", "META", "AMZN", "TSLA"]
    for index, symbol in enumerate(symbols[: args.symbols]):
        bars = []
        for session in range(args.sessions):
            close = 100 + index * 20 + session * 0.03 + 8 * math.sin(session / 17 + index)
            opening = close + math.sin(session / 5)
            bars.append(
                {
                    "t": (date(2023, 1, 1) + timedelta(days=session)).isoformat() + "T12:00:00Z",
                    "o": opening,
                    "h": max(opening, close) + 2,
                    "l": min(opening, close) - 2,
                    "c": close,
                }
            )
        histories[symbol] = bars
    results = {
        "python": sys.version.split()[0],
        "sessions": args.sessions,
        "symbols": args.symbols,
        "repeats": args.repeats,
        "profiles": {},
    }
    for profile in ("budget", "swing"):
        config = Config(start=bars[64]["t"][:10], end=bars[-1]["t"][:10], profile=profile)
        durations, fingerprints = [], set()
        for _ in range(args.repeats):
            start = time.perf_counter()
            report = compare(histories, config)
            durations.append(time.perf_counter() - start)
            fingerprints.add(
                hashlib.sha256(
                    json.dumps(report, sort_keys=True, allow_nan=False).encode()
                ).hexdigest()
            )
        if len(fingerprints) != 1:
            raise RuntimeError("Reports changed between repetitions.")
        results["profiles"][profile] = {
            "median_seconds": round(statistics.median(durations), 4),
            "seconds": [round(value, 4) for value in durations],
            "report_sha256": fingerprints.pop(),
        }
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
