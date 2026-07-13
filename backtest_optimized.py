"""
DS-SpreadScalper Backtest 2.0 — Optimiert
Vergleicht Originalstrategie vs Trendfilter (20-EMA) + größere Positionen
"""
import json, math, random
from collections import defaultdict
from datetime import datetime

# ── Config (exakt wie spread_scalper.py) ──
SYMBOLS = ["SOLUSDT", "BTCUSDT", "ETHUSDT"]
LEVERAGE = 5
OFFSET_PCT = 0.0001
MAX_SPREAD_PCT = 0.005

TP_LEVELS = [
    {"pct": 0.15, "atr_mult": 3.0, "label": "TP1"},
    {"pct": 0.35, "atr_mult": 6.0, "label": "TP2"},
    {"pct": 0.50, "atr_mult": 9.0, "label": "TP3"},
]
SL_BASE_MULT = 0.30
SL_MAX_MULT = 0.60
LOSS_STREAK_THRESHOLD = 3

# ORIGINAL sizes
MIN_SIZES_ORIG = {"BTCUSDT": 0.001, "ETHUSDT": 0.05, "SOLUSDT": 0.2}
# OPTIMIZED sizes (2× BTC, 1× ETH, stays same SOL from 0.1→0.2)
MIN_SIZES_OPT = {"BTCUSDT": 0.002, "ETHUSDT": 0.05, "SOLUSDT": 0.2}

PRICE_PLACES = {"SOLUSDT": 3, "BTCUSDT": 1, "ETHUSDT": 1}

MAKER_FEE = 0.0002
TAKER_FEE = 0.0006

random.seed(42)

def calc_ema(values, period=20):
    """Berechnet EMA über values (Liste von floats). Gibt Liste der Länge len(values)."""
    if not values or len(values) < period:
        return None
    k = 2.0 / (period + 1)
    ema = sum(values[:period]) / period  # SMA-Start
    result = [None] * (period - 1) + [ema]
    for v in values[period:]:
        ema = v * k + ema * (1 - k)
        result.append(ema)
    return result

def calc_atr_from_candles(candles, idx, period=14):
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

def run_backtest(symbol, candles, use_trendfilter=False, sizes=None):
    if sizes is None:
        sizes = MIN_SIZES_ORIG
    
    trades = []
    position = None
    
    # EMA vorab berechnen
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
        
        # EMA für diesen Candle
        ema = emas[i] if emas and i < len(emas) else None
        
        if position is None:
            # Wähle Side (random oder trendgefiltert)
            side = random.choice(["long", "short"])
            
            if use_trendfilter and ema is not None:
                # Nur LONG wenn close > EMA, nur SHORT wenn close < EMA
                if side == "long" and c <= ema:
                    continue  # skip, trade gegen Trend
                if side == "short" and c >= ema:
                    continue  # skip, trade gegen Trend
            
            entry = o
            size = sizes.get(symbol, 0.1)
            
            notional = entry * size
            if notional < 4.95:
                continue
            
            sl_mult = SL_BASE_MULT
            sl = entry - atr * sl_mult if side == "long" else entry + atr * sl_mult
            sl = round(sl, PRICE_PLACES.get(symbol, 2))
            
            tp1 = entry + atr * 3.0 if side == "long" else entry - atr * 3.0
            tp1 = round(tp1, PRICE_PLACES.get(symbol, 2))
            
            entry_fee = notional * MAKER_FEE
            
            position = {
                "side": side, "entry": entry, "size": size,
                "sl": sl, "tp1": tp1,
                "loss_streak": 0, "tp_level": 0,
                "entry_fee": entry_fee, "entry_ts": ts,
            }
        else:
            side = position["side"]
            entry = position["entry"]
            sl = position["sl"]
            tp1 = position["tp1"]
            size = position["size"]
            
            hit_tp = hit_sl = False
            exit_price = 0
            reason = ""
            
            if side == "long":
                if l <= sl:
                    hit_sl = True
                    if h >= tp1:
                        hit_sl = True
                        hit_tp = False
                    exit_price = sl
                    reason = "SL"
                elif h >= tp1:
                    hit_tp = True
                    exit_price = tp1
                    reason = "TP1"
            else:
                if h >= sl:
                    hit_sl = True
                    if l <= tp1:
                        hit_sl = True
                        hit_tp = False
                    exit_price = sl
                    reason = "SL"
                elif l <= tp1:
                    hit_tp = True
                    exit_price = tp1
                    reason = "TP1"
            
            if hit_sl or hit_tp:
                if side == "long":
                    pnl = (exit_price - entry) * size
                else:
                    pnl = (entry - exit_price) * size
                
                exit_fee = exit_price * size * TAKER_FEE
                net_pnl = pnl - position["entry_fee"] - exit_fee
                
                trades.append({
                    "ts": dt,
                    "symbol": symbol,
                    "side": side,
                    "entry": entry,
                    "exit": exit_price,
                    "pnl": round(pnl, 4),
                    "fees": round(position["entry_fee"] + exit_fee, 4),
                    "net_pnl": round(net_pnl, 4),
                    "reason": reason,
                    "atr": round(atr, 4),
                    "sl": sl,
                    "tp1": tp1,
                })
                position = None
                continue
    
    return trades

def print_stats(trades, label, prefix=""):
    n = len(trades)
    if n == 0:
        print(f"  {prefix}{label}:  0 Trades (keine Daten)")
        return {"n": 0, "pnl": 0}
    wins = [t for t in trades if t["net_pnl"] > 0]
    losses = [t for t in trades if t["net_pnl"] <= 0]
    wr = len(wins) / n * 100
    total_pnl = sum(t["net_pnl"] for t in trades)
    total_fees = sum(t["fees"] for t in trades)
    avg_win = sum(t["net_pnl"] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t["net_pnl"] for t in losses) / len(losses) if losses else 0
    max_win = max((t["net_pnl"] for t in trades), default=0)
    max_loss = min((t["net_pnl"] for t in trades), default=0)
    pf = abs(sum(t["net_pnl"] for t in wins)) / abs(sum(t["net_pnl"] for t in losses)) if losses and sum(t["net_pnl"] for t in losses) != 0 else float('inf')
    sl_hits = len([t for t in trades if t["reason"] == "SL"])
    tp_hits = len([t for t in trades if t["reason"].startswith("TP")])
    long_trades = [t for t in trades if t["side"] == "long"]
    short_trades = [t for t in trades if t["side"] == "short"]
    
    print(f"  {prefix}{label}")
    print(f"  {prefix}  Trades:       {n:>5}")
    print(f"  {prefix}  Winrate:      {wr:>5.1f}%  ({len(wins)}W/{len(losses)}L)")
    print(f"  {prefix}  Total PnL:    {total_pnl:>+8.4f} USDT  (brutto: {total_pnl+total_fees:.4f}, fees: {total_fees:.4f})")
    print(f"  {prefix}  Profit Factor:{pf:>8.2f}")
    print(f"  {prefix}  Ø Win:        {avg_win:>+8.4f}   Ø Loss: {avg_loss:>+8.4f}")
    print(f"  {prefix}  Max Win:      {max_win:>+8.4f}   Max Loss: {max_loss:>+8.4f}")
    print(f"  {prefix}  SL-Hits:      {sl_hits:>4}   TP-Hits: {tp_hits:>4}")
    print(f"  {prefix}  LONG:         {len(long_trades):>4} Trades, {sum(1 for t in long_trades if t['net_pnl']>0)/len(long_trades)*100 if long_trades else 0:.0f}% WR, {sum(t['net_pnl'] for t in long_trades):+>.4f}")
    print(f"  {prefix}  SHORT:        {len(short_trades):>4} Trades, {sum(1 for t in short_trades if t['net_pnl']>0)/len(short_trades)*100 if short_trades else 0:.0f}% WR, {sum(t['net_pnl'] for t in short_trades):+>.4f}")
    if losses:
        print(f"  {prefix}  ⚠️  Top 3 Verluste:")
        for t in sorted(losses, key=lambda x: x["net_pnl"])[:3]:
            print(f"  {prefix}      {t['side']:>5} @ {t['entry']:<10.4f} → {t['exit']:<10.4f} | PnL={t['net_pnl']:<+8.4f} | {t['reason']}")
    return {"n": n, "pnl": total_pnl, "wr": wr, "pf": pf, "fees": total_fees}


# ═══════════════════════════════════════════
#  MAIN – Vergleich Original vs Optimiert
# ═══════════════════════════════════════════
if __name__ == "__main__":
    now = datetime.now().strftime('%d.%m.%Y %H:%M')
    print("=" * 110)
    print(f"{'DS-SpreadScalper Backtest 2.0 — OPTIMIERT':^110}")
    print(f"{'Vergleich: Original vs. 20-EMA Trendfilter + größere Positionen':^110}")
    print(f"{now:^110}")
    print("=" * 110)

    labels = {"orig": "📊 ORIGINAL (random, kleine Sizes)", "trend": "📊 TRENDFILTER (20-EMA, gr. Sizes)"}

    all_results = {}
    for mode_label, use_tf, sizes in [("orig", False, MIN_SIZES_ORIG), ("trend", True, MIN_SIZES_OPT)]:
        all_trades = {s: [] for s in SYMBOLS}

        for symbol in SYMBOLS:
            with open(f"/Users/andreas/bitget_bot_v1/backtest/{symbol}_1H.json") as f:
                candles = json.load(f)
            trades = run_backtest(symbol, candles, use_trendfilter=use_tf, sizes=sizes)
            all_trades[symbol] = trades

        print(f"\n{'─' * 110}")
        print(f"  {labels[mode_label]}")
        print(f"{'─' * 110}")

        sym_stats = {}
        for symbol in SYMBOLS:
            stats = print_stats(all_trades[symbol], symbol, prefix="  ")
            sym_stats[symbol] = stats
            print()

        all_t = []
        for s in SYMBOLS:
            all_t.extend(all_trades[s])

        total_stats = print_stats(all_t, "GESAMT", prefix="  ")
        total_stats["sym"] = sym_stats
        all_results[mode_label] = total_stats

        cumulative = 0
        peak = 0
        max_dd = 0
        for t in all_t:
            cumulative += t["net_pnl"]
            peak = max(peak, cumulative)
            dd = peak - cumulative
            max_dd = max(max_dd, dd)
        print(f"\n  Max Drawdown:  {max_dd:>8.4f} USDT  →  Final: {cumulative:>+8.4f}")

        max_consec = cur_consec = 0
        for t in all_t:
            if t["net_pnl"] <= 0:
                cur_consec += 1
                max_consec = max(max_consec, cur_consec)
            else:
                cur_consec = 0
        print(f"  Max Verlustserie: {max_consec}")
        print()

    print(f"\n{'=' * 110}")
    print(f"{'VERGLEICH — Original vs. Trendfilter + größere Positionen':^110}")
    print(f"{'=' * 110}")
    print(f"  {'Symbol':<10} {'Modus':<20} {'Trades':>6} {'WR':>5} {'PnL':>10} {'Fees':>10} {'PF':>6}")
    print(f"  {'─'*9} {'─'*19} {'─'*5} {'─'*4} {'─'*9} {'─'*9} {'─'*5}")

    for mode_label, display_name in [("orig", "Original"), ("trend", "Trendfilter")]:
        for s in SYMBOLS:
            st = all_results[mode_label]["sym"][s]
            label = display_name if s == SYMBOLS[0] else ""
            print(f"  {s:<10} {label:<20} {st['n']:>6} {st['wr']:>4.0f}% {st['pnl']:>+9.4f} {st['fees']:>9.4f} {st['pf']:>5.2f}")
        gt = all_results[mode_label]
        print(f"  {'GESAMT':<10} {'':<20} {gt['n']:>6} {gt['wr']:>4.0f}% {gt['pnl']:>+9.4f} {gt['fees']:>9.4f} {gt['pf']:>5.2f}")
        print()

    orig_pnl = all_results["orig"]["pnl"]
    trend_pnl = all_results["trend"]["pnl"]
    delta = trend_pnl - orig_pnl
    print(f"  Verbesserung durch Trendfilter:  {delta:+.4f} USDT ({delta/abs(orig_pnl)*100 if orig_pnl else 0:.0f}%)")
    print(f"  Trade-Reduktion:  {all_results['orig']['n']} → {all_results['trend']['n']} (-{all_results['orig']['n']-all_results['trend']['n']})")

    print(f"\n{'=' * 110}")
    print(f"{'BACKTEST ABGESCHLOSSEN':^110}")
    print(f"{'=' * 110}")

