"""
DS-SpreadScalper Backtest 2.0 — SHORT-ONLY
Simuliert den Bot mit ausschließlich SHORT-Positionen.
"""
import json, random
from datetime import datetime

from backtest_optimized import calc_atr_from_candles, calc_ema
from backtest_optimized import MIN_SIZES_OPT, PRICE_PLACES
from backtest_optimized import MAKER_FEE, TAKER_FEE, SL_BASE_MULT

SYMBOLS = ["SOLUSDT", "BTCUSDT", "ETHUSDT"]
random.seed(42)

def run_backtest_shortonly(symbol, candles, use_trendfilter=False):
    trades = []
    position = None
    closes = [float(c[4]) for c in candles]
    emas = calc_ema(closes, 20)

    for i in range(1, len(candles)):
        ts = int(candles[i][0])
        dt = datetime.fromtimestamp(ts/1000).isoformat()
        o = float(candles[i][1])
        h = float(candles[i][2])
        l = float(candles[i][3])
        c = float(candles[i][4])

        atr = calc_atr_from_candles(candles, i)
        if atr is None or atr <= 0:
            continue

        ema = emas[i] if emas and i < len(emas) else None

        if position is None:
            # SHORT-ONLY
            if use_trendfilter and ema is not None and c >= ema:
                continue  # über EMA → kein Short

            entry = o
            size = MIN_SIZES_OPT.get(symbol, 0.1)
            notional = entry * size
            if notional < 4.95:
                continue

            sl = entry + atr * SL_BASE_MULT  # SHORT SL über Entry
            sl = round(sl, PRICE_PLACES.get(symbol, 2))
            tp1 = entry - atr * 3.0  # SHORT TP unter Entry
            tp1 = round(tp1, PRICE_PLACES.get(symbol, 2))
            entry_fee = notional * MAKER_FEE

            position = {
                "side": "short", "entry": entry, "size": size,
                "sl": sl, "tp1": tp1, "entry_fee": entry_fee,
            }
        else:
            entry = position["entry"]
            sl = position["sl"]
            tp1 = position["tp1"]
            size = position["size"]

            exit_price = 0
            reason = ""

            if h >= sl:
                exit_price = sl
                reason = "SL"
            elif l <= tp1:
                exit_price = tp1
                reason = "TP1"
            else:
                continue

            pnl = (entry - exit_price) * size
            exit_fee = exit_price * size * TAKER_FEE
            net_pnl = pnl - position["entry_fee"] - exit_fee

            trades.append({
                "ts": dt, "symbol": symbol, "side": "short",
                "entry": entry, "exit": exit_price,
                "pnl": round(pnl, 4),
                "fees": round(position["entry_fee"] + exit_fee, 4),
                "net_pnl": round(net_pnl, 4),
                "reason": reason, "atr": round(atr, 4),
                "sl": sl, "tp1": tp1,
            })
            position = None

    return trades


# ══════ MAIN ══════
now = datetime.now().strftime('%d.%m.%Y %H:%M')
print("=" * 110)
print(f"{'DS-SpreadScalper Backtest 2.0 — SHORT-ONLY':^110}")
print(f"{'BTC/ETH/SOL · 1H Kerzen · größere Positionen (BTC 0.002, SOL 0.2)':^110}")
print(f"{now:^110}")
print("=" * 110)

for mode_label, use_tf in [("SHORT-ONLY (random)", False), ("SHORT-ONLY + 20-EMA Filter", True)]:
    print(f"\n{'─' * 110}")
    print(f"  📊 {mode_label}")
    print(f"{'─' * 110}")

    all_trades = []
    for symbol in SYMBOLS:
        with open(f"/Users/andreas/bitget_bot_v1/backtest/{symbol}_1H.json") as f:
            candles = json.load(f)
        trades = run_backtest_shortonly(symbol, candles, use_trendfilter=use_tf)
        all_trades.extend(trades)

        n = len(trades)
        wins = [t for t in trades if t["net_pnl"] > 0]
        losses = [t for t in trades if t["net_pnl"] <= 0]
        wr = len(wins) / n * 100 if n else 0
        total_pnl = sum(t["net_pnl"] for t in trades)
        total_fees = sum(t["fees"] for t in trades)
        avg_win = sum(t["net_pnl"] for t in wins) / len(wins) if wins else 0
        avg_loss = sum(t["net_pnl"] for t in losses) / len(losses) if losses else 0
        max_win = max((t["net_pnl"] for t in trades), default=0)
        max_loss = min((t["net_pnl"] for t in trades), default=0)
        pf = abs(sum(t["net_pnl"] for t in wins)) / abs(sum(t["net_pnl"] for t in losses)) if losses and sum(t["net_pnl"] for t in losses) != 0 else float('inf')
        sl_hits = len([t for t in trades if t["reason"] == "SL"])
        tp_hits = len([t for t in trades if t["reason"].startswith("TP")])

        print(f"\n    {symbol}")
        print(f"      Trades:       {n:>5}")
        print(f"      Winrate:      {wr:>5.1f}%  ({len(wins)}W/{len(losses)}L)")
        print(f"      Total PnL:    {total_pnl:>+8.4f} USDT  (fees: {total_fees:.4f})")
        print(f"      Profit Factor:{pf:>8.2f}")
        print(f"      Ø Win:        {avg_win:>+8.4f}   Ø Loss: {avg_loss:>+8.4f}")
        print(f"      Max Win:      {max_win:>+8.4f}   Max Loss: {max_loss:>+8.4f}")
        print(f"      SL-Hits:      {sl_hits:>4}   TP-Hits: {tp_hits:>4}")

    # Gesamt
    print(f"\n    {'─'*50}")
    n = len(all_trades)
    wins = [t for t in all_trades if t["net_pnl"] > 0]
    losses = [t for t in all_trades if t["net_pnl"] <= 0]
    wr = len(wins) / n * 100 if n else 0
    total_pnl = sum(t["net_pnl"] for t in all_trades)
    total_fees = sum(t["fees"] for t in all_trades)
    avg_win = sum(t["net_pnl"] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t["net_pnl"] for t in losses) / len(losses) if losses else 0
    max_win = max((t["net_pnl"] for t in all_trades), default=0)
    max_loss = min((t["net_pnl"] for t in all_trades), default=0)
    pf = abs(sum(t["net_pnl"] for t in wins)) / abs(sum(t["net_pnl"] for t in losses)) if losses and sum(t["net_pnl"] for t in losses) != 0 else float('inf')
    print(f"    GESAMT")
    print(f"      Trades:       {n:>5}")
    print(f"      Winrate:      {wr:>5.1f}%  ({len(wins)}W/{len(losses)}L)")
    print(f"      Total PnL:    {total_pnl:>+8.4f} USDT  (fees: {total_fees:.4f})")
    print(f"      Profit Factor:{pf:>8.2f}")
    print(f"      Ø Win:        {avg_win:>+8.4f}   Ø Loss: {avg_loss:>+8.4f}")

    # Drawdown
    cum = peak = 0
    max_dd = 0
    consec = cur = 0
    for t in all_trades:
        cum += t["net_pnl"]
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)
        if t["net_pnl"] <= 0:
            cur += 1
            consec = max(consec, cur)
        else:
            cur = 0
    print(f"      Max DD:       {max_dd:>8.4f} USDT")
    print(f"      Max Serie:    {consec}")
    print()

print("=" * 110)
print(f"{'BACKTEST ABGESCHLOSSEN':^110}")
print("=" * 110)
