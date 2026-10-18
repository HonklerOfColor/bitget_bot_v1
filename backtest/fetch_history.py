"""Lädt öffentliche Bitget-1h-Kerzen und Funding. Keine API-Keys.

Funding-Historie liefert die öffentliche API nur etwa 90 Tage. Ältere
Fenster im Backtest enthalten deshalb keine Funding-Kosten. Im vorhandenen
Fenster sind das rund −1.4 USDT auf das OOS-Buch, klein gegen Fees und SL.
"""
import json
import time
import urllib.request
from pathlib import Path

BASE = "https://api.bitget.com"
CACHE = Path(__file__).resolve().parent / "cache"
SYMBOLS = ("BTCUSDT", "ETHUSDT", "BNBUSDT")


def _get(url, attempts=5):
    last = None
    for i in range(attempts):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "v1-backtest"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = json.load(resp)
            if payload.get("code") != "00000":
                raise RuntimeError(payload.get("msg") or payload.get("code"))
            return payload.get("data") or []
        except Exception as exc:
            last = exc
            time.sleep(0.4 * (i + 1))
    raise RuntimeError(f"{url} -> {last}")


def fetch_candles(symbol, start_ms, end_ms, pause=0.12):
    found = {}
    end = end_ms
    guard = 0
    while guard < 400:
        guard += 1
        url = (
            f"{BASE}/api/v2/mix/market/history-candles?symbol={symbol}"
            f"&productType=USDT-FUTURES&granularity=1H&limit=200&endTime={end}"
        )
        batch = _get(url)
        if not batch:
            break
        oldest = None
        for row in batch:
            ts = int(row[0])
            oldest = ts if oldest is None else min(oldest, ts)
            if start_ms <= ts <= end_ms:
                found[ts] = (ts, float(row[1]), float(row[2]), float(row[3]), float(row[4]))
        if oldest is None or oldest <= start_ms or oldest >= end:
            break
        end = oldest - 1
        time.sleep(pause)
    return [found[k] for k in sorted(found)]


def fetch_funding(symbol, start_ms, pause=0.12):
    events = {}
    page = 1
    while page <= 80:
        url = (
            f"{BASE}/api/v2/mix/market/history-fund-rate?symbol={symbol}"
            f"&productType=USDT-FUTURES&pageSize=100&pageNo={page}"
        )
        batch = _get(url)
        if not batch:
            break
        oldest = None
        for row in batch:
            ts = int(row["fundingTime"])
            oldest = ts if oldest is None else min(oldest, ts)
            if ts >= start_ms:
                events[ts] = (ts, float(row["fundingRate"]))
        if oldest is not None and oldest < start_ms:
            break
        if len(batch) < 100:
            break
        page += 1
        time.sleep(pause)
    return [events[k] for k in sorted(events)]


def load_or_fetch(symbol, start_ms, end_ms, refresh=False):
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{symbol}.json"
    if path.exists() and not refresh:
        payload = json.loads(path.read_text())
        candles = [tuple(row) for row in payload["candles"]]
        funding = [tuple(row) for row in payload["funding"]]
        if candles and candles[0][0] <= start_ms + 3_600_000 and candles[-1][0] >= end_ms - 3 * 3_600_000:
            return candles, funding
    candles = fetch_candles(symbol, start_ms, end_ms)
    funding = fetch_funding(symbol, start_ms)
    path.write_text(json.dumps({
        "symbol": symbol,
        "candles": candles,
        "funding": funding,
    }))
    return candles, funding


if __name__ == "__main__":
    start = 1_727_740_800_000  # 2024-10-01 UTC
    end = int(time.time() * 1000)
    for sym in SYMBOLS:
        candles, funding = load_or_fetch(sym, start, end, refresh=True)
        print(f"{sym} candles={len(candles)} funding={len(funding)} "
              f"from={candles[0][0] if candles else '-'} to={candles[-1][0] if candles else '-'}")
