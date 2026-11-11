#!/usr/bin/env python
from tests_equities.test_strategies import bars
from equities.strategies import rotation, valid
from datetime import datetime
from zoneinfo import ZoneInfo
from itertools import pairwise

# Test data
aapl = bars([100] * 63 + [110])
print(f'AAPL length: {len(aapl)}')
print(f'First date: {aapl[0]["t"]}')
print(f'Last date: {aapl[-1]["t"]}')

# Check alignment logic manually
try:
    windows = [
        tuple(
            datetime.fromisoformat(bar["t"]).astimezone(ZoneInfo("America/New_York")).date()
            for bar in data[-64:]
        )
        for data in [aapl]
    ]
    print(f'Windows: {windows}')
    aligned = len(set(windows)) == 1 and all(
        all(first < second for first, second in pairwise(window)) for window in windows
    )
    print(f'Aligned: {aligned}')
except Exception as e:
    print(f'Error: {e}')
