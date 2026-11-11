#!/usr/bin/env python
from tests_equities.test_strategies import bars
from equities.strategies import rotation

# Test data
aapl = bars([100] * 63 + [110])
msft = bars([100] * 63 + [120])
print(f'AAPL length: {len(aapl)}')
print(f'MSFT length: {len(msft)}')

# Run rotation
result = rotation({'AAPL': aapl, 'MSFT': msft})
print(f'AAPL signal: {result["AAPL"]["signal"]}')
print(f'MSFT signal: {result["MSFT"]["signal"]}')
print(f'AAPL reason: {result["AAPL"]["reason"]}')
print(f'MSFT reason: {result["MSFT"]["reason"]}')
