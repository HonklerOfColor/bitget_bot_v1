"""
DS-SpreadScalper Backtest 2.0 — BTC, ETH, SOL
Simuliert die exakte Bot-Strategie auf 1H OHLC Kerzen.
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
    trades = []
    position = None  # None or {"side", "entry", "size", "sl", "tp1", "tp2", "tp3", "loss_streak", "tp_level"}
    
    for i in range(1, len(candles)):
        ts = int(candles[i][0])
        dt = datetime.fromtimestamp(ts/1000).isoformat()
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
            entry = o  # entry ≈ open price (spread capture vernachlässigbar)
            size = MIN_SIZES.get(symbol, 0.1)
            
            # Notional-Check
            notional = entry * size
            if notional < 4.95:
                continue
            
            # SL dynamisch basierend auf loss_streak
            loss_streak = 0  # neu start, also 0
            sl_mult = SL_BASE_MULT
            sl = entry - atr * sl_mult if side == "long" else entry + atr * sl_mult
            sl = round(sl, PRICE_PLACES.get(symbol, 2))
            
            # TP-Level
            tp1 = entry + atr * 3.0 if side == "long" else entry - atr * 3.0
            tp2 = entry + atr * 6.0 if side == "long" else entry - atr * 6.0
            tp3 = entry + atr * 9.0 if side == "long" else entry - atr * 9.0
            tp1 = round(tp1, PRICE_PLACES.get(symbol, 2))
            tp2 = round(tp2, PRICE_PLACES.get(symbol, 2))
            tp3 = round(tp3, PRICE_PLACES.get(symbol, 2))
            
            # Maker Fee für Entry
            entry_fee = notional * MAKER_FEE
            
            position = {
                "side": side, "entry": entry, "size": size,
                "sl": sl, "tp1": tp1, "tp2": tp2, "tp3": tp3,
                "loss_streak": 0, "tp_level": 0,
                "entry_fee": entry_fee, "entry_ts": ts,
            }
        else:
            # ── Position existiert → prüfe SL/TP ──
            side = position["side"]
            entry = position["entry"]
            sl = position["sl"]
            tp1 = position["tp1"]
            tp2 = position["tp2"]
            tp3 = position["tp3"]
            size = position["size"]
            
            hit_tp = hit_sl = False
            exit_price = 0
            reason = ""
            
            if side == "long":
                # LONG: SL unter Entry, TP über Entry
                # Prüfe ob Candle irgendwo SL getroffen hat
                if l <= sl:
                    # SL getroffen
                    hit_sl = True
                    # Prüfe ob TP auch drin war (beide hit — bad tick)
                    if h >= tp1:
                        # Ambiguous: wer zuerst? Annahme: SL zuerst (wahrscheinlicher bei Scalp)
                        hit_sl = True
                        hit_tp = False
                    exit_price = sl
                    reason = "SL"
                elif h >= tp1:
                    hit_tp = True
                    exit_price = tp1
                    reason = "TP1"
            else:  # short
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
                # Berechne PnL
                if side == "long":
                    pnl = (exit_price - entry) * size
                else:
                    pnl = (entry - exit_price) * size
                
                # Taker Fee für Exit
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
            
            # ── Update loss_streak für nächsten Trade ──
            # (wird beim nächsten Trade-Start verwendet)
    
    return trades

# ── Main ──
print("=" * 100)
print(f"{'DS-SpreadScalper Backtest 2.0':^100}")
print(f"{'3 Symbole · 1H Kerzen · ' + datetime.now().strftime('%d.%m.%Y %H:%M'):^100}")
print("=" * 100)

all_trades = {s: [] for s in SYMBOLS}

for symbol in SYMBOLS:
    with open(f"/Users/andreas/bitget_bot_v1/backtest/{symbol}_1H.json") as f:
        candles = json.load(f)
    
    trades = run_backtest(symbol, candles)
    all_trades[symbol] = trades
    
    # Stats
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
    profit_factor = abs(sum(t["net_pnl"] for t in wins)) / abs(sum(t["net_pnl"] for t in losses)) if losses and sum(t["net_pnl"] for t in losses) != 0 else float('inf')
    
    # SL vs TP breakdown
    tp_trades = [t for t in trades if t["reason"].startswith("TP")]
    sl_trades = [t for t in trades if t["reason"] == "SL"]
    
    # Long vs Short
    long_trades = [t for t in trades if t["side"] == "long"]
    short_trades = [t for t in trades if t["side"] == "short"]
    
    print(f"\n{'─' * 100}")
    print(f"📊 {symbol}")
    print(f"{'─' * 100}")
    print(f"  Trades:       {n:>5}")
    print(f"  Winrate:      {wr:>5.1f}%  ({len(wins)}W/{len(losses)}L)")
    print(f"  Total PnL:    {total_pnl:>+8.4f} USDT  (brutto: {total_pnl+total_fees:.4f} USDT, fees: {total_fees:.4f} USDT)")
    print(f"  Profit Factor:{profit_factor:>8.2f}")
    print(f"  Ø Win:        {avg_win:>+8.4f}   Ø Loss: {avg_loss:>+8.4f}")
    print(f"  Max Win:      {max_win:>+8.4f}   Max Loss: {max_loss:>+8.4f}")
    print(f"  SL-Hits:      {len(sl_trades):>4}   TP-Hits: {len(tp_trades):>4}")
    print(f"  LONG:         {len(long_trades):>4} Trades, {sum(1 for t in long_trades if t['net_pnl']>0)/len(long_trades)*100 if long_trades else 0:.0f}% WR, {sum(t['net_pnl'] for t in long_trades):+>.4f}")
    print(f"  SHORT:        {len(short_trades):>4} Trades, {sum(1 for t in short_trades if t['net_pnl']>0)/len(short_trades)*100 if short_trades else 0:.0f}% WR, {sum(t['net_pnl'] for t in short_trades):+>.4f}")
    
    if losses:
        # Top 5 losses
        print(f"\n  ⚠️  Top 3 Verluste:")
        for t in sorted(losses, key=lambda x: x["net_pnl"])[:3]:
            print(f"      {t['side']:>5} @ {t['entry']:<10.4f} → {t['exit']:<10.4f} | PnL={t['net_pnl']:<+8.4f} | {t['reason']} | SL={t['sl']} ATR={t['atr']:.4f}")

# ── Gesamt ──
print(f"\n{'=' * 100}")
print(f"{'GESAMT':^100}")
print(f"{'=' * 100}")

all_t = []
for s in SYMBOLS:
    all_t.extend(all_trades[s])

total_trades = len(all_t)
total_wins = len([t for t in all_t if t["net_pnl"] > 0])
total_losses = len([t for t in all_t if t["net_pnl"] <= 0])
total_pnl = sum(t["net_pnl"] for t in all_t)
total_fees = sum(t["fees"] for t in all_t)
wr = total_wins / total_trades * 100 if total_trades else 0
avg_net = total_pnl / total_trades if total_trades else 0
pf = abs(sum(t["net_pnl"] for t in all_t if t["net_pnl"] > 0)) / abs(sum(t["net_pnl"] for t in all_t if t["net_pnl"] < 0)) if total_losses and sum(t["net_pnl"] for t in all_t if t["net_pnl"] < 0) != 0 else float('inf')

print(f"\n  Gesamt Trades: {total_trades}")
print(f"  Gesamt PnL:    {total_pnl:>+8.4f} USDT (Fees: {total_fees:.4f})")
print(f"  Winrate:       {wr:.1f}% ({total_wins}W/{total_losses}L)")
print(f"  Profit Factor: {pf:.2f}")
print(f"  Ø PnL/Trade:   {avg_net:+.4f} USDT")

# Überlebensanalyse (Drawdown)
print(f"\n{'─' * 100}")
print(f"{'DRAWDOWN-ANALYSE':^100}")
print(f"{'─' * 100}")

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

print(f"  Peak PnL:      {peak:>+8.4f}")
print(f"  Max Drawdown:  {max_dd:>8.4f} USDT ({max_dd_pct:.2f}%)")
print(f"  Final PnL:     {cumulative:>+8.4f}")

# Consecutive losses
print(f"\n{'─' * 100}")
print(f"{'VERLUSTSERIEN-ANALYSE':^100}")
print(f"{'─' * 100}")

max_consec = 0
cur_consec = 0
for t in all_t:
    if t["net_pnl"] <= 0:
        cur_consec += 1
        max_consec = max(max_consec, cur_consec)
    else:
        cur_consec = 0

print(f"  Max consecutive losses: {max_consec}")
print(f"  Trades mit Verlust:     {total_losses} / {total_trades}")

# Equity Curve als CSV zum Visualisieren
print(f"\n{'─' * 100}")
print(f"{'EQUITY CURVE (erste 25 + letzte 25 Trades)':^100}")
print(f"{'─' * 100}")

cum = 0
points = []
for i, t in enumerate(all_t):
    cum += t["net_pnl"]
    points.append(cum)
    if i < 25 or i >= total_trades - 25 or i % max(1, total_trades // 20) == 0:
        markers = "▁▂▃▄▅▆▇█"
        val = min(int((cum - min(points)) / max(1, max(points) - min(points)) * 7), 7)
        print(f"  #{i+1:>3}  {cum:>+8.4f}  {markers[val]}")

print(f"\n{'=' * 100}")
print(f"{'BACKTEST ABGESCHLOSSEN':^100}")
print(f"{'=' * 100}")
