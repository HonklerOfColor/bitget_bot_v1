#!/usr/bin/env python3
"""Test: Fetch 1 year of 1H candles from Bitget history-candles API."""
import sys, time, json
sys.path.insert(0, '/Users/andreas/bitget_bot_v1')
import bitget_client

now_ms = int(time.time() * 1000)
year_ago_ms = now_ms - 365 * 24 * 3600 * 1000

all_candles = []
for i in range(45):
    start = year_ago_ms + i * 200 * 3600 * 1000
    end = start + 200 * 3600 * 1000 - 1
    params = {
        'symbol': 'BTCUSDT',
        'productType': 'USDT-FUTURES',
        'granularity': '1H',
        'limit': '200',
        'startTime': str(start),
        'endTime': str(end),
    }
    data = bitget_client._get('/api/v2/mix/market/history-candles', params)
    batch = data.get('data', [])
    all_candles.extend(batch)
    if len(batch) < 200:
        print(f'Batch {i}: {len(batch)} (last), total: {len(all_candles)}')
        break
    if i % 5 == 0:
        print(f'Batch {i}: {len(batch)}, total: {len(all_candles)}')
else:
    print(f'45 batches done: {len(all_candles)} total')

if all_candles:
    print(f'Date range: {time.strftime("%Y-%m-%d", time.gmtime(int(all_candles[0][0])/1000))} to {time.strftime("%Y-%m-%d", time.gmtime(int(all_candles[-1][0])/1000))}')
    print(f'Candles count: {len(all_candles)} (~{len(all_candles)/24:.0f} days)')
