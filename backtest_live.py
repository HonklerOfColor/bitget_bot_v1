"""
DS-SpreadScalper Live Backtest — frische 1H-Daten von Bitget API
Simuliert die exakte Bot-Strategie: random LONG/SHORT, 30%/70% Spread-Penetration
"""
import sys, json, math, random
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, '.')
import bitget_client

# ── Config (exakt wie spread_scalper.py) ──
SYMBOLS = ["SOLUSDT", "BTCUSDT", "ETHUSDT"]
LEVERAGE = 5

MIN_SIZES = {"BTCUSDT": 0.001, "ETHUSDT": 0.05, "SOLUSDT": 0.2}
PRICE_PLACES = {"SOLUSDT": 3, "BTCUSDT": 1, "ETHUSDT": 1}

# Fees: 0.02% Maker Entry + 0.06% Taker Exit = 0.08% Round Trip
MAKER_FEE = 0.0002
TAKER_FEE = 0.0006

random.seed(42)

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

def run_backtest(symbol, candles):
    """Simuliere Bot-Strategie: random LONG/SHORT, SL bei 2% vom Entry, kein TP (bot nutzt Trailing)"""
    trades = []
    position = None  # None or dict
    
    for i in range(1, len(candles)):
        ts = int(candles[i][0])
        dt = datetime.fromtimestamp(ts/1000)
        o = float(candles[i][1])
        h = float(candles[i][2])
        l = float(candles[i][3])
        c = float(candles[i][4])
        
        # ATR (rolling, ab voller Periode)
        atr = calc_atr_from_candles(candles, i)
        if atr is None or atr <= 0:
            continue
        
        if position is None:
            # ── Keine Position → neuen Trade eröffnen (random LONG/SHORT) ──
            side = random.choice(["long", "short"])
            entry = o  # Einstieg ≈ Open (Spread-Capture vernachlässigbar)
            size = MIN_SIZES.get(symbol, 0.1)
            
            # Notional-Check
            notional = entry * size
            if notional < 4.95:
                continue
            
            # SL: wie Bot — 2% vom Entry (initialer Schutz-SL)
            if side == "long":
                sl_price = round(entry * 0.98, PRICE_PLACES.get(symbol, 2))
            else:  # short
                sl_price = round(entry * 1.02, PRICE_PLACES.get(symbol, 2))
            
            # Kein TP — Bot nutzt ROE-Trailing, simulieren wir vereinfacht
            # TP wird als "wenn Preis >1% ROE erreicht, dann trailen wir mit 2%"
            
            # Maker Fee für Entry
            entry_fee = notional * MAKER_FEE
            
            # Margin = size * entry / LEVERAGE
            margin = (size * entry) / LEVERAGE
            
            position = {
                "side": side, "entry": entry, "size": size,
                "sl": sl_price, "ts": ts,
                "entry_fee": entry_fee, "margin": margin,
                "peak_pnl_pct": -999.0,  # Peak ROE%
            }
        else:
            # ── Position existiert → prüfe SL und simuliere ROE-Trailing ──
            side = position["side"]
            entry = position["entry"]
            sl = position["sl"]
            size = position["size"]
            margin = position["margin"]
            
            # Aktuelles PnL% (ROE) basierend auf High/Low dieser Candle
            # Für SL-Check verwenden wir den ungünstigsten Preis
            if side == "long":
                # Long: ungünstigster = Low dieser Candle
                worst_price = l
                best_price = h
                pnl_check = (worst_price - entry) * size  # USD
                pnl_best = (best_price - entry) * size  # USD
            else:
                # Short: ungünstigster = High dieser Candle
                worst_price = h
                best_price = l
                pnl_check = (entry - worst_price) * size  # USD
                pnl_best = (entry - best_price) * size  # USD
            
            # ROE%
            roe_pct = pnl_check / margin * 100 if margin else 0
            roe_best_pct = pnl_best / margin * 100 if margin else 0
            
            # Peak-ROE tracken (vereinfacht: nur auf Close-Preis)
            close_pnl = (c - entry) * size if side == "long" else (entry - c) * size
            close_roe = close_pnl / margin * 100 if margin else 0
            peak_roe = max(position.get("peak_pnl_pct", -999), close_roe)
            position["peak_pnl_pct"] = peak_roe
            
            # ROE-Trailing: wenn Peak > 1%, trailen wir mit 2% Abstand
            if peak_roe > 1.0:
                target_roe = peak_roe - 2.0
                if side == "short":
                    # short: sl_price = entry - (target_roe/100 * margin) / size
                    new_sl = entry - (target_roe / 100 * margin) / size
                    # Short-SL muss > mark sein — für den Check nehmen wir den High
                    # Nur tighten: neuer SL muss < current sein (enger)
                    if new_sl < sl and new_sl > worst_price:  # Sicherheitscheck
                        sl = round(new_sl, PRICE_PLACES.get(symbol, 2))
                else:
                    new_sl = entry + (target_roe / 100 * margin) / size
                    if new_sl > sl and new_sl < worst_price:
                        sl = round(new_sl, PRICE_PLACES.get(symbol, 2))
            
            # ── SL-Check (markPreis simulation): wenn worst_price den SL berührt → Exit ──
            hit_sl = False
            exit_price = 0
            reason = ""
            
            if side == "long":
                if l <= sl:
                    hit_sl = True
                    exit_price = sl
                    reason = "SL"
            else:  # short
                if h >= sl:
                    hit_sl = True
                    exit_price = sl
                    reason = "SL"
            
            if hit_sl:
                # Berechne PnL
                if side == "long":
                    pnl = (exit_price - entry) * size
                else:
                    pnl = (entry - exit_price) * size
                
                # Taker Fee für Exit
                exit_fee = exit_price * size * TAKER_FEE
                net_pnl = pnl - position["entry_fee"] - exit_fee
                
                trades.append({
                    "ts": dt.strftime("%m-%d %H:%M"),
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
                    "roe_peak": round(peak_roe, 2),
                })
                position = None
    
    return trades

# ── Main ──
print("=" * 110)
print(f"{'DS-SpreadScalper Live Backtest':^110}")
print(f"{'Strategie: Random LONG/SHORT · SL=2% Entry · Spread 30%/70% · ' + datetime.now().strftime('%d.%m.%Y %H:%M'):^110}")
print("=" * 110)

all_trades = {s: [] for s in SYMBOLS}

for symbol in SYMBOLS:
    print(f"\n📡 Lade {symbol} 1H-Daten von Bitget API...", end=" ")
    sys.stdout.flush()
    candles = bitget_client.get_candles(symbol, "1H", 1000)
    print(f"{len(candles)} Kerzen geladen")
    
    trades = run_backtest(symbol, candles)
    all_trades[symbol] = trades
    
    # Stats
    n = len(trades)
    wins = [t for t in trades if t["net_pnl"] > 0]
    losses = [t for t in trades if t["net_pnl"] <= 0]
    wr = len(wins) / n * 100 if n else 0
    total_pnl = sum(t["net_pnl"] for t in trades)
    total_fees = sum(t["fees"] for t in trades)
    gross_pnl = total_pnl + total_fees
    avg_win = sum(t["net_pnl"] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t["net_pnl"] for t in losses) / len(losses) if losses else 0
    max_win = max((t["net_pnl"] for t in trades), default=0)
    max_loss = min((t["net_pnl"] for t in trades), default=0)
    pf = abs(sum(t["net_pnl"] for t in wins)) / abs(sum(t["net_pnl"] for t in losses)) if losses and sum(t["net_pnl"] for t in losses) != 0 else float('inf')
    
    # SL breakdown
    sl_trades = [t for t in trades if t["reason"] == "SL"]
    
    # Long vs Short
    long_trades = [t for t in trades if t["side"] == "long"]
    short_trades = [t for t in trades if t["side"] == "short"]
    
    print(f"📊 {symbol}")
    print(f"    Trades:          {n:>5}")
    print(f"    Winrate:         {wr:>5.1f}%  ({len(wins)}W/{len(losses)}L)")
    print(f"    Total PnL:       {total_pnl:>+8.4f} USDT  (brutto: {gross_pnl:.4f} Fees: {total_fees:.4f})")
    print(f"    Profit Factor:   {pf:>8.2f}")
    print(f"    Ø Win:           {avg_win:>+8.4f}   Ø Loss: {avg_loss:>+8.4f}")
    print(f"    Max Win:         {max_win:>+8.4f}   Max Loss: {max_loss:>+8.4f}")
    print(f"    SL-Hits:         {len(sl_trades):>4}")
    if long_trades:
        long_wr = sum(1 for t in long_trades if t['net_pnl']>0)/len(long_trades)*100
        long_pnl = sum(t['net_pnl'] for t in long_trades)
        print(f"    LONG:            {len(long_trades):>4} Trades, {long_wr:.0f}% WR, {long_pnl:+.4f}")
    if short_trades:
        short_wr = sum(1 for t in short_trades if t['net_pnl']>0)/len(short_trades)*100
        short_pnl = sum(t['net_pnl'] for t in short_trades)
        print(f"    SHORT:           {len(short_trades):>4} Trades, {short_wr:.0f}% WR, {short_pnl:+.4f}")
    
    if losses:
        print(f"    ⚠️  Top 3 Verluste:")
        for t in sorted(losses, key=lambda x: x["net_pnl"])[:3]:
            print(f"        {t['side']:>5} {t['ts']} @ {t['entry']:<10.4f} → {t['exit']:<10.4f} | PnL={t['net_pnl']:<+8.4f} | {t['reason']}")

# ── Gesamt ──
print(f"\n{'=' * 110}")
print(f"{'GESAMT (alle Symbole)':^110}")
print(f"{'=' * 110}")

all_t = []
for s in SYMBOLS:
    all_t.extend(all_trades[s])

total_trades = len(all_t)
total_wins = len([t for t in all_t if t["net_pnl"] > 0])
total_losses = len([t for t in all_t if t["net_pnl"] <= 0])
total_pnl = sum(t["net_pnl"] for t in all_t)
total_fees = sum(t["fees"] for t in all_t)
gross_pnl = total_pnl + total_fees
wr = total_wins / total_trades * 100 if total_trades else 0
avg_net = total_pnl / total_trades if total_trades else 0
pf = abs(sum(t["net_pnl"] for t in all_t if t["net_pnl"] > 0)) / abs(sum(t["net_pnl"] for t in all_t if t["net_pnl"] < 0)) if total_losses and sum(t["net_pnl"] for t in all_t if t["net_pnl"] < 0) != 0 else float('inf')

print(f"\n  Gesamt Trades:  {total_trades}")
print(f"  Gesamt PnL:     {total_pnl:>+8.4f} USDT (brutto: {gross_pnl:.4f} Fees: {total_fees:.4f})")
print(f"  Winrate:        {wr:.1f}% ({total_wins}W/{total_losses}L)")
print(f"  Profit Factor:  {pf:.2f}")
print(f"  Ø PnL/Trade:    {avg_net:+.4f} USDT")

# ── Drawdown ──
print(f"\n{'─' * 110}")
print(f"{'DRAWDOWN':^110}")
print(f"{'─' * 110}")

cumulative = 0
peak = 0
max_dd = 0
max_dd_pct = 0
for t in all_t:
    cumulative += t["net_pnl"]
    peak = max(peak, cumulative)
    dd = peak - cumulative
    dd_pct = dd / peak * 100 if peak > 0 else 0
    max_dd = max(max_dd, dd)
    max_dd_pct = max(max_dd_pct, dd_pct)

print(f"  Peak PnL:       {peak:>+8.4f}")
print(f"  Max Drawdown:   {max_dd:>8.4f} USDT ({max_dd_pct:.2f}%)")
print(f"  Final PnL:      {cumulative:>+8.4f}")

# Max consecutive losses
max_consec = 0
cur_consec = 0
consec_loss_streaks = []
for t in all_t:
    if t["net_pnl"] <= 0:
        cur_consec += 1
        if cur_consec > max_consec:
            max_consec = cur_consec
    else:
        if cur_consec > 0:
            consec_loss_streaks.append(cur_consec)
        cur_consec = 0
if cur_consec > 0:
    consec_loss_streaks.append(cur_consec)

print(f"\n  Max consecutive losses: {max_consec}")
print(f"  Loss streaks:           {consec_loss_streaks}")

# ── Symbol-Vergleich ──
print(f"\n{'─' * 110}")
print(f"{'SYMBOL-VERGLEICH':^110}")
print(f"{'─' * 110}")
print(f"  {'Symbol':<10} {'Trades':>6} {'WR':>6} {'PnL':>10} {'PF':>6}")
print(f"  {'─'*10} {'─'*6} {'─'*6} {'─'*10} {'─'*6}")
for s in SYMBOLS:
    t = all_trades[s]
    n = len(t)
    if n == 0:
        print(f"  {s:<10} {0:>6} {'N/A':>6} {'N/A':>10} {'N/A':>6}")
        continue
    w = len([x for x in t if x['net_pnl'] > 0])
    l = len([x for x in t if x['net_pnl'] <= 0])
    wr_pct = w / n * 100
    pnl = sum(x['net_pnl'] for x in t)
    wpnl = sum(x['net_pnl'] for x in t if x['net_pnl'] > 0)
    lpnl = abs(sum(x['net_pnl'] for x in t if x['net_pnl'] < 0))
    pf_s = wpnl / lpnl if lpnl else float('inf')
    print(f"  {s:<10} {n:>6} {wr_pct:>5.1f}% {pnl:>+9.4f} {pf_s:>5.2f}")

print(f"\n{'=' * 110}")
print(f"{'BACKTEST ABGESCHLOSSEN — ' + datetime.now().strftime('%d.%m.%Y %H:%M'):^110}")
print(f"{'=' * 110}")
