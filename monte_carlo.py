"""
Monte Carlo Simulation — Aktuelle V1 Reversal-Strategie
Simuliert 500+ Durchläufe mit zufälligen Parameter-Variationen
"""
import sys, json, math, random
from datetime import datetime
sys.path.insert(0, '/opt/data/bitget_bot_v1')
import bitget_client as client

# ── Basis-Parameter (exakte Bot-Werte) ──
SYMBOLS = ["SOLUSDT", "BTCUSDT", "ETHUSDT", "BNBUSDT"]
LEVERAGE = 3
MAKER_FEE = 0.0002
TAKER_FEE = 0.0006
BREAKEVEN_PNL_PCT = 0.02
MIN_SIZES = {"BTCUSDT": 0.001, "ETHUSDT": 0.05, "SOLUSDT": 0.2, "BNBUSDT": 0.05}
PRICE_PLACES = {"SOLUSDT": 3, "BTCUSDT": 1, "ETHUSDT": 1, "BNBUSDT": 2}

NUM_RUNS = 500  # Monte Carlo Iterationen

def calc_atr(candles, idx, period=14):
    if idx < period: return None
    trs = []
    for i in range(idx - period + 1, idx + 1):
        h = float(candles[i][2]); l = float(candles[i][3])
        pc = float(candles[i-1][4])
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs) / len(trs)

def run_simulation(candles, sl_mult, tp_mult, offset_pct):
    """Ein Durchlauf mit gegebenen Parametern."""
    trades = []
    position = None
    
    for i in range(22, len(candles)):  # Ab 22 damit ATR + Chart-SL verfügbar
        c1 = candles[i-2]; c2 = candles[i-1]  # Vorletzte und letzte Kerze
        o1 = float(c1[1]); cc1 = float(c1[4])
        o2 = float(c2[1]); cc2 = float(c2[4])
        
        # Reversal-Signal
        short_signal = cc1 > o1 and cc2 < o2
        long_signal = cc1 < o1 and cc2 > o2
        
        signal = None
        if short_signal: signal = "short"
        elif long_signal: signal = "long"
        
        if signal is None:
            continue
        
        atr = calc_atr(candles, i)
        if atr is None or atr <= 0: continue
        
        entry = float(candles[i][1])  # Open-Preis
        size = MIN_SIZES.get(symbol, 0.1)
        notional = entry * size
        if notional < 4.95: continue
        
        # SL: chart-basiert (höchstes High / tiefstes Low + 0.1%) + ATR-Variation
        highs = [float(candles[j][2]) for j in range(i-20, i+1)]
        lows = [float(candles[j][3]) for j in range(i-20, i+1)]
        price_places = PRICE_PLACES.get(symbol, 2)
        
        if signal == "short":
            sl = round(max(highs) * 1.001, price_places)
            # Safety: min 0.5x ATR
            if sl < entry + atr * 0.5:
                sl = round(entry + atr * 0.5, price_places)
            tp = round(entry - atr * tp_mult, price_places)
        else:
            sl = round(min(lows) * 0.999, price_places)
            if sl > entry - atr * 0.5:
                sl = round(entry - atr * 0.5, price_places)
            tp = round(entry + atr * tp_mult, price_places)
        
        # Position wird eröffnet
        entry_fee = notional * MAKER_FEE
        margin = (size * entry) / LEVERAGE
        
        # Prüfe auf SL/TP im Verlauf der nächsten 24 Kerzen
        hit = False
        for j in range(i, min(i + 24, len(candles))):
            h = float(candles[j][2])
            l = float(candles[j][3])
            
            if signal == "short":
                if h >= sl:
                    exit_price = sl
                    reason = "SL"
                    hit = True
                    break
                elif l <= tp:
                    exit_price = tp
                    reason = "TP"
                    hit = True
                    break
            else:
                if l <= sl:
                    exit_price = sl
                    reason = "SL"
                    hit = True
                    break
                elif h >= tp:
                    exit_price = tp
                    reason = "TP"
                    hit = True
                    break
        
        if hit:
            if signal == "short":
                pnl = (entry - exit_price) * size
            else:
                pnl = (exit_price - entry) * size
            exit_fee = exit_price * size * TAKER_FEE
            net_pnl = pnl - entry_fee - exit_fee
            trades.append(net_pnl)
    
    return trades

# ── Daten laden und Simulation ──
print(f"Monte Carlo Simulation — Reversal-Strategie")
print(f"==========================================")
print(f"Lade Daten und führe {NUM_RUNS} Durchläufe aus...\n")

all_results = []

for symbol in SYMBOLS:
    candles = client.get_candles(symbol, "1H", 1000)
    if not candles or len(candles) < 100:
        print(f"  ⏭️ {symbol}: Nicht genug Daten")
        continue
    
    print(f"  {symbol}: {len(candles)} Kerzen")
    symbol_results = []
    
    for run in range(NUM_RUNS):
        # Zufällige Parameter-Variation (±20% um Basis-Werte)
        sl_mult = 1.5 + random.uniform(-0.3, 0.3)     # 1.2 - 1.8
        tp_mult = 2.0 + random.uniform(-0.4, 0.4)     # 1.6 - 2.4
        offset = 0.0012 + random.uniform(-0.0003, 0.0003)  # 0.09 - 0.15%
        
        trades = run_simulation(candles, sl_mult, tp_mult, offset)
        if trades:
            total_pnl = sum(trades)
            wins = len([t for t in trades if t > 0])
            wr = wins / len(trades) * 100 if trades else 0
            symbol_results.append({
                "pnl": total_pnl, "trades": len(trades),
                "wr": wr, "sl": sl_mult, "tp": tp_mult
            })
    
    if symbol_results:
        pnls = [r["pnl"] for r in symbol_results]
        trades_n = [r["trades"] for r in symbol_results]
        wrs = [r["wr"] for r in symbol_results]
        
        pnls_sorted = sorted(pnls)
        median_idx = len(pnls_sorted) // 2
        
        print(f"    Trades/Sim:  {sum(trades_n)//len(trades_n):.0f} (Ø)")
        print(f"    Ø PnL:       {sum(pnls)/len(pnls):+.4f} USDT")
        print(f"    Median PnL:  {pnls_sorted[median_idx]:+.4f} USDT")
        print(f"    Best:        {max(pnls):+.4f} USDT")
        print(f"    Worst:       {min(pnls):+.4f} USDT")
        print(f"    PnL > 0:     {len([p for p in pnls if p > 0])}/{len(pnls)} ({len([p for p in pnls if p > 0])/len(pnls)*100:.0f}%)")
        print(f"    Ø Winrate:   {sum(wrs)/len(wrs):.1f}%")
        print(f"    95%-Konfidenz: {pnls_sorted[int(len(pnls_sorted)*0.05)]:+.4f} bis {pnls_sorted[int(len(pnls_sorted)*0.95)]:+.4f}")
        print()
        
        all_results.extend([(symbol, r) for r in symbol_results])

# ── Gesamtergebnis (alle Symbole aggregiert) ──
if all_results:
    total_pnls = [r["pnl"] for _, r in all_results]
    total_trades = [r["trades"] for _, r in all_results]
    total_wrs = [r["wr"] for _, r in all_results]
    
    total_pnls_sorted = sorted(total_pnls)
    med = len(total_pnls_sorted) // 2
    
    print(f"==========================================")
    print(f"📊 GESAMTERGEBNIS ({len(all_results)} Simulationen)")
    print(f"==========================================")
    print(f"  Ø PnL:        {sum(total_pnls)/len(total_pnls):+.4f} USDT")
    print(f"  Median PnL:   {total_pnls_sorted[med]:+.4f} USDT")
    print(f"  Best:         {max(total_pnls):+.4f} USDT")
    print(f"  Worst:        {min(total_pnls):+.4f} USDT")
    print(f"  Positive Sims:{len([p for p in total_pnls if p > 0])}/{len(total_pnls)} ({len([p for p in total_pnls if p > 0])/len(total_pnls)*100:.0f}%)")
    print(f"  Ø Winrate:    {sum(total_wrs)/len(total_wrs):.1f}%")
    print(f"  Ø Trades/Sim: {sum(total_trades)/len(total_trades):.0f}")
    print(f"  95%-KI:       {total_pnls_sorted[int(len(total_pnls_sorted)*0.05)]:+.4f} bis {total_pnls_sorted[int(len(total_pnls_sorted)*0.95)]:+.4f}")
    
    # Per Symbol
    print(f"\n⟐ Per Symbol:")
    for sym in SYMBOLS:
        sym_results = [r for s, r in all_results if s == sym]
        if sym_results:
            pnls = [r["pnl"] for r in sym_results]
            pnls_s = sorted(pnls)
            print(f"  {sym}: Ø {sum(pnls)/len(pnls):+.4f} | Median {pnls_s[len(pnls_s)//2]:+.4f} | "
                  f"{len([p for p in pnls if p > 0])}/{len(pnls)} positiv | "
                  f"KI [{pnls_s[int(len(pnls_s)*0.05)]:+.4f} .. {pnls_s[int(len(pnls_s)*0.95)]:+.4f}]")
