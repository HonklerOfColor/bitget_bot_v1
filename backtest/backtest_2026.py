#!/usr/bin/env python3
"""
DS-SpreadScalper V1 Backtest — 2026er Daten von Bitget
======================================================
Exakte Bot-Logik aus spread_scalper.py (Stand Juli 2026):
- SHORT-Only, Hebel 3×
- Single TP @ 3.0× ATR
- SL chart-basiert (20er High × 1.001) + min 0.5× ATR, Fallback 1.5× ATR
- ROE-Trailing: ab 3% Peak-ROE, SL 2% unter Peak
- Spread-Penetration SHORT=70%
- Fees: 0.02% Maker / 0.06% Taker
- 4 Symbole: SOLUSDT, BTCUSDT, ETHUSDT, XRPUSDT
"""
import sys, json, time, math
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, "/Users/andreas/bitget_bot_v1")
import bitget_client as client

# ── Exakte Bot-Parameter ──
SYMBOLS = ["SOLUSDT", "BTCUSDT", "ETHUSDT", "XRPUSDT"]
LEVERAGE = 3
MAKER_FEE = 0.0002
TAKER_FEE = 0.0006
BREAKEVEN_PNL_PCT = 0.03
SPREAD_SHORT = 0.7

PRICE_PLACES = {"SOLUSDT": 3, "BTCUSDT": 1, "ETHUSDT": 1, "XRPUSDT": 4}
MIN_SIZES = {"BTCUSDT": 0.001, "ETHUSDT": 0.05, "SOLUSDT": 0.2, "XRPUSDT": 5}

# Single TP: 100% bei 3.0× ATR
TP_LEVELS = [{"pct": 1.0, "atr_mult": 3.0, "label": "TP1"}]

DATA_DIR = Path("/Users/andreas/bitget_bot_v1/backtest")


def fetch_bitget_2026(symbol):
    """Hole alle 1H Kerzen von Bitget für 2026 (via history-candles, 200er Batches)."""
    now = int(datetime.now().timestamp() * 1000)
    start_2026 = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)

    all_candles = []
    batch_start = start_2026
    batch_num = 0
    limit = 200

    while batch_start < now:
        params = {
            "symbol": symbol,
            "productType": "USDT-FUTURES",
            "granularity": "1H",
            "limit": str(limit),
            "startTime": str(batch_start),
        }
        data = client._get("/api/v2/mix/market/history-candles", params)
        batch = data.get("data", [])

        if not batch:
            break

        batch = list(reversed(batch))
        all_candles.extend(batch)
        batch_num += 1

        last_ts = int(batch[-1][0])

        if batch_num == 1:
            first_dt = datetime.fromtimestamp(int(batch[0][0])/1000, tz=timezone.utc)
            last_dt = datetime.fromtimestamp(last_ts/1000, tz=timezone.utc)
            print(f"  📦 Batch 1: {len(batch)} Kerzen ({first_dt.strftime('%d.%m')} → {last_dt.strftime('%d.%m')})")

        if last_ts >= now:
            break

        batch_start = last_ts + 3600000
        time.sleep(0.05)

        if batch_num % 20 == 0:
            print(f"     Batch {batch_num}: {len(all_candles)} Kerzen insgesamt", end="\r")

    seen = set()
    unique = []
    for c in all_candles:
        ts = int(c[0])
        if ts not in seen:
            seen.add(ts)
            unique.append(c)

    unique.sort(key=lambda x: int(x[0]))
    print(f"  📊 {len(unique)} Kerzen ({len(all_candles)} roh, {batch_num} Batches)")
    return unique


def load_or_fetch(symbol):
    """Lade existierende Daten oder fetch von Bitget."""
    f = DATA_DIR / f"{symbol}_1H.json"
    if f.exists():
        data = json.loads(f.read_text())
        first = datetime.fromtimestamp(int(data[0][0])/1000, tz=timezone.utc)
        last = datetime.fromtimestamp(int(data[-1][0])/1000, tz=timezone.utc)
        days = (last - first).days
        if days > 120 and int(data[-1][0]) > int(datetime.now().timestamp()*1000) - 7*24*3600000:
            print(f"  ✅ {symbol}: {len(data)} Kerzen ({first.strftime('%d.%m.%y')} → {last.strftime('%d.%m.%y')}, {days}d)")
            return data
        print(f"  📡 {symbol}: Daten veraltet ({days}d, bis {last.strftime('%d.%m')}), fetche neu...")

    print(f"  📡 {symbol}: Lade 2026er Daten von Bitget...")
    data = fetch_bitget_2026(symbol)
    f.write_text(json.dumps(data))
    return data


# ── Backtest Logic ──

def calc_atr(candles, idx, period=14):
    if idx < period:
        return None
    trs = []
    for i in range(idx - period + 1, idx + 1):
        h = float(candles[i][2])
        l = float(candles[i][3])
        pc = float(candles[i-1][4])
        tr = max(h - l, abs(h - pc), abs(l - pc))
        trs.append(tr)
    return sum(trs) / len(trs)


def calc_sl(candles, idx, entry, side, pp):
    """Chart-SL wie Bot: 20 Kerzen + 0.1%, min 0.5×ATR, Fallback 1.5×ATR."""
    lookback = min(idx, 20)
    if lookback < 5:
        atr = calc_atr(candles, idx)
        if not atr:
            return None
        return round(entry + atr * 1.5, pp) if side == "short" else round(entry - atr * 1.5, pp)

    sub = candles[idx - lookback:idx]
    highs = [float(k[2]) for k in sub]
    lows = [float(k[3]) for k in sub]
    atr = calc_atr(candles, idx)

    if side == "short":
        sl = round(max(highs) * 1.001, pp)
        if atr and sl < entry + atr * 0.5:
            sl = round(entry + atr * 0.5, pp)
        return sl
    else:
        sl = round(min(lows) * 0.999, pp)
        if atr and sl > entry - atr * 0.5:
            sl = round(entry - atr * 0.5, pp)
        return sl


def run_backtest(symbol, candles):
    """Führe Backtest mit exakter V1 Bot-Logik."""
    n = len(candles)
    trades = []
    position = None

    pp = PRICE_PLACES.get(symbol, 2)
    min_size = MIN_SIZES.get(symbol, 0.1)

    total_pnl = 0.0
    wins = 0
    losses = 0

    for i in range(50, n - 1):
        if i % 5000 == 0 and i > 0:
            pct = i / n * 100
            print(f"    {symbol}: {pct:.0f}% ({i}/{n}) — {len(trades)} Trades", end="\r")

        o, h, l, c = float(candles[i][1]), float(candles[i][2]), float(candles[i][3]), float(candles[i][4])

        if position:
            entry, sl, tp, e_idx, peak_roe = position["entry"], position["sl"], position["tp"], position["entry_idx"], position["peak_roe"]

            # TP: Marktpreis <= TP (Short)
            if c <= tp:
                pnl = (entry - tp) * position["size"]
                pnl -= abs(pnl) * TAKER_FEE
                total_pnl += pnl
                wins += 1
                trades.append({"entry": entry, "exit": tp, "pnl": pnl, "reason": "TP",
                              "entry_ts": int(candles[e_idx][0]), "exit_ts": int(candles[i][0]),
                              "bars": i - e_idx})
                position = None
                continue

            # SL: Marktpreis >= SL (Short)
            if h >= sl:
                pnl = (entry - sl) * position["size"]
                pnl -= abs(pnl) * TAKER_FEE
                total_pnl += pnl
                losses += 1
                trades.append({"entry": entry, "exit": sl, "pnl": pnl, "reason": "SL",
                              "entry_ts": int(candles[e_idx][0]), "exit_ts": int(candles[i][0]),
                              "bars": i - e_idx})
                position = None
                continue

            # ROE-Trailing (wie Bot: 2% unter Peak, ab 3%)
            margin = entry * position["size"] / LEVERAGE
            u_pnl = (entry - c) * position["size"]
            roe = (u_pnl / margin) * 100 if margin > 0 else 0

            if roe > peak_roe:
                position["peak_roe"] = roe

            if position["peak_roe"] >= BREAKEVEN_PNL_PCT * 100:
                target_roe = position["peak_roe"] - 2.0
                pnl_target = target_roe / 100 * margin
                new_sl = round(entry - pnl_target / position["size"], pp)

                if new_sl > c and new_sl < position["sl"]:
                    position["sl"] = new_sl

        else:
            # Keine Position → SHORT Entry prüfen
            atr = calc_atr(candles, i)
            if not atr or atr <= 0:
                continue

            spread = h - l
            if spread <= 0:
                continue
            spread_pct = spread / c
            if spread_pct > 0.005:  # MAX_SPREAD_PCT = 0.5%
                continue

            entry_price = round(l + spread * SPREAD_SHORT, pp)
            sl_price = calc_sl(candles, i, entry_price, "short", pp)
            if sl_price is None:
                continue

            tp_price = round(entry_price - atr * TP_LEVELS[0]["atr_mult"], pp)

            # Min Notional Check
            notional = entry_price * min_size
            if notional < 5.0:
                continue

            position = {
                "entry": entry_price,
                "sl": sl_price,
                "tp": tp_price,
                "size": min_size,
                "entry_idx": i,
                "peak_roe": 0.0,
            }

    print(f"    {symbol}: Fertig — {len(trades)} Trades         ")
    return trades, total_pnl, wins, losses


# ── Main ──

def main():
    print("=" * 65)
    print("  📊 DS-SpreadScalper V1 Backtest — 2026er Daten")
    print("  SHORT-Only | 3× Hebel | Single TP @ 3.0× ATR")
    print("=" * 65)

    all_trades = {}
    all_pnl = {}

    for sym in SYMBOLS:
        print(f"\n📡 {sym}: Daten laden...")
        candles = load_or_fetch(sym)
        if not candles or len(candles) < 100:
            print(f"  ❌ Nicht genug Daten für {sym}")
            continue

        print(f"  🧪 Backtest läuft...")
        trades, total_pnl, wins, losses = run_backtest(sym, candles)
        all_trades[sym] = trades
        all_pnl[sym] = {"total_pnl": total_pnl, "wins": wins, "losses": losses, "trades": len(trades)}

    # Ergebnisse
    print("\n\n" + "=" * 65)
    print("  📊 BACKTEST-ERGEBNISSE 2026")
    print("=" * 65)

    grand_total = 0.0
    grand_trades = 0

    for sym in SYMBOLS:
        info = all_pnl.get(sym)
        if not info or info["trades"] == 0:
            print(f"\n  {sym}: Keine Trades")
            continue

        t = info["trades"]
        w = info["wins"]
        l = info["losses"]
        wr = w / t * 100 if t > 0 else 0
        pnl = info["total_pnl"]
        avg = pnl / t if t > 0 else 0
        pf = abs(sum(t["pnl"] for t in all_trades[sym] if t["pnl"] > 0) / max(abs(sum(t["pnl"] for t in all_trades[sym] if t["pnl"] < 0)), 0.01))

        print(f"\n  {'─'*60}")
        print(f"  🔴 {sym}")
        print(f"  {'─'*60}")
        print(f"    Trades:     {t:>5d}")
        print(f"    Wins:       {w:>5d}  ({wr:.1f}%)")
        print(f"    Losses:     {l:>5d}")
        print(f"    Gesamt PnL: {pnl:>+8.2f} USDT")
        print(f"    ⌀ PnL/Trade:{avg:>+8.2f} USDT")
        print(f"    Profit F.:  {pf:.2f}")

        if t > 0:
            best = max(all_trades[sym], key=lambda x: x["pnl"])
            worst = min(all_trades[sym], key=lambda x: x["pnl"])
            print(f"    🏆 Bester:    {best['pnl']:>+8.2f} USDT ({best['reason']})")
            print(f"    💀 Schlecht:  {worst['pnl']:>+8.2f} USDT ({worst['reason']})")

        grand_total += pnl
        grand_trades += t

    print(f"\n  {'='*60}")
    print(f"  📊 GESAMT")
    print(f"  {'='*60}")
    print(f"    Trades:     {grand_trades:>5d}")
    print(f"    Gesamt PnL: {grand_total:>+8.2f} USDT")
    print(f"  {'='*60}")
    print()


if __name__ == "__main__":
    main()
