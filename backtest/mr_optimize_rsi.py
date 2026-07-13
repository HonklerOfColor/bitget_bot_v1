#!/usr/bin/env python3
"""
RSI Mean Reversion — Parameter Optimization
===========================================
Sweept RSI-Parameter:
  - RSI Periode: 7, 10, 14, 21
  - Entry Long: RSI < rsi_low (20-35)
  - Exit Long: RSI > rsi_exit (50-70)
  - SL: 1.0/1.5/2.0/2.5/3.0 × ATR
  - Take-Profit: SMA oder ATR-basiert
  - Mit/ohne 3-Bar-Filter
"""
import json, os, math
from collections import defaultdict

SYMBOLS = ["SOLUSDT", "BTCUSDT", "ETHUSDT", "XRPUSDT"]
DATA_DIR = os.path.dirname(__file__)
MAKER_FEE = 0.0002
TAKER_FEE = 0.0006
CAPITAL = 10000
LEVERAGE = 1

RESULT_FILE = os.path.join(DATA_DIR, "mr_rsi_optimized.json")

def load_data(symbol):
    path = os.path.join(DATA_DIR, f"{symbol}_1H.json")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        raw = json.load(f)
    candles = []
    for r in raw:
        candles.append({
            "ts": int(r[0]),
            "open": float(r[1]),
            "high": float(r[2]),
            "low": float(r[3]),
            "close": float(r[4]),
            "volume": float(r[5]),
        })
    return candles

def calc_atr(candles, idx, period=14):
    if idx < period:
        return None
    trs = []
    for i in range(idx - period + 1, idx + 1):
        h = candles[i]["high"]
        l = candles[i]["low"]
        pc = candles[i-1]["close"]
        tr = max(h - l, abs(h - pc), abs(l - pc))
        trs.append(tr)
    return sum(trs) / len(trs)

def calc_rsi(closes, period=14):
    if len(closes) < period + 1:
        return None
    gains, losses = 0.0, 0.0
    for i in range(-period, 0):
        diff = closes[i] - closes[i-1]
        if diff > 0:
            gains += diff
        else:
            losses -= diff
    avg_gain = gains / period
    avg_loss = losses / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))

def calc_ema(closes, period):
    if len(closes) < period:
        return None
    k = 2 / (period + 1)
    ema = sum(closes[-period:]) / period
    # Rollierend ab letzten period
    for i in range(len(closes) - period, len(closes)):
        ema = closes[i] * k + ema * (1 - k)
    return ema

def run_backtest(candles, period, rsi_low, rsi_high, rsi_exit_low, rsi_exit_high, sl_atr, bar_filter=True, tp_type="rsi"):
    """
    period: RSI Periode
    rsi_low: Einstieg LONG wenn RSI < rsi_low
    rsi_high: Einstieg SHORT wenn RSI > rsi_high
    rsi_exit_low: Exit LONG wenn RSI > rsi_exit_low
    rsi_exit_high: Exit SHORT wenn RSI < rsi_exit_high
    sl_atr: SL als Multiplikator von ATR
    bar_filter: 3-Bar-Filter aktiv
    tp_type: "rsi" = Exit bei RSI-Schwelle, "sma" = Exit bei SMA-Cross
    """
    n = len(candles)
    closes = [c["close"] for c in candles]
    
    active_pos = None
    stats = {
        "trades": 0, "wins": 0, "losses": 0,
        "total_pnl": 0.0, "total_fees": 0.0,
        "longs": 0, "shorts": 0,
        "max_drawdown": 0.0,
        "equity": CAPITAL, "equity_peak": CAPITAL,
        "avg_hold_bars": 0, "hold_bars_sum": 0,
    }
    hold_bars = 0
    
    for i in range(period + 20, n):
        window = closes[:i+1]
        c = candles[i]
        c_close = c["close"]
        
        rsi_val = calc_rsi(window, period)
        
        # SMA für Exit
        sma = sum(window[-20:]) / 20 if len(window) >= 20 else None
        atr = calc_atr(candles, i)
        
        # Entry Signal
        signal = 0
        if rsi_val is not None and atr is not None:
            if rsi_val < rsi_low:
                if not bar_filter or (len(window) >= 4 and window[-4] >= window[-1]):
                    signal = 1  # LONG
            elif rsi_val > rsi_high:
                if not bar_filter or (len(window) >= 4 and window[-4] <= window[-1]):
                    signal = -1  # SHORT
        
        # Positions-Management
        if active_pos is None and signal != 0:
            size = CAPITAL / len(SYMBOLS) / c_close
            size *= LEVERAGE
            active_pos = {
                "side": "long" if signal == 1 else "short",
                "entry": c_close,
                "size": size,
                "entry_bar": i,
                "entry_rsi": rsi_val,
                "sl": c_close - sl_atr * atr if signal == 1 else c_close + sl_atr * atr,
                "entry_sma": sma,
            }
            stats["total_fees"] += size * c_close * TAKER_FEE
            hold_bars = 0
        
        if active_pos is not None:
            hold_bars += 1
            side = active_pos["side"]
            entry = active_pos["entry"]
            sl = active_pos["sl"]
            
            # Fortlaufendes RSI
            current_rsi = calc_rsi(window, period) if len(window) > period else None
            
            exited = False
            exit_price = None
            exit_reason = ""
            
            # SL-Check
            if sl and ((side == "long" and c["low"] <= sl) or (side == "short" and c["high"] >= sl)):
                exit_price = sl
                exit_reason = "SL"
                exited = True
            
            # Exit-Check (RSI oder SMA)
            if not exited and current_rsi is not None:
                if tp_type == "rsi":
                    if side == "long" and current_rsi >= rsi_exit_low:
                        exit_price = c_close
                        exit_reason = "RSI-Exit"
                        exited = True
                    elif side == "short" and current_rsi <= rsi_exit_high:
                        exit_price = c_close
                        exit_reason = "RSI-Exit"
                        exited = True
                else:  # sma
                    if sma and side == "long" and c_close >= sma:
                        exit_price = c_close
                        exit_reason = "SMA-Exit"
                        exited = True
                    elif sma and side == "short" and c_close <= sma:
                        exit_price = c_close
                        exit_reason = "SMA-Exit"
                        exited = True
            
            if exited:
                size = active_pos["size"]
                if side == "long":
                    pnl = size * (exit_price - entry)
                else:
                    pnl = size * (entry - exit_price)
                
                fee = size * exit_price * TAKER_FEE
                net_pnl = pnl - fee
                
                stats["total_pnl"] += net_pnl
                stats["total_fees"] += fee
                stats["equity"] += net_pnl
                stats["trades"] += 1
                stats["hold_bars_sum"] += hold_bars
                
                if net_pnl > 0:
                    stats["wins"] += 1
                else:
                    stats["losses"] += 1
                if side == "long":
                    stats["longs"] += 1
                else:
                    stats["shorts"] += 1
                
                if stats["equity"] > stats["equity_peak"]:
                    stats["equity_peak"] = stats["equity"]
                dd = (stats["equity_peak"] - stats["equity"]) / stats["equity_peak"] * 100
                if dd > stats["max_drawdown"]:
                    stats["max_drawdown"] = dd
                
                active_pos = None
    
    stats["final_equity"] = stats["equity"]
    stats["total_return_pct"] = (stats["equity"] / CAPITAL - 1) * 100
    stats["win_rate"] = (stats["wins"] / stats["trades"] * 100) if stats["trades"] > 0 else 0
    stats["avg_hold_bars"] = (stats["hold_bars_sum"] / stats["trades"]) if stats["trades"] > 0 else 0
    stats["avg_pnl"] = (stats["total_pnl"] / stats["trades"]) if stats["trades"] > 0 else 0
    stats["profit_factor"] = 0  # wird kombiniert berechnet
    return stats

def main():
    all_symbols = {}
    for sym in SYMBOLS:
        candles = load_data(sym)
        if candles:
            all_symbols[sym] = candles
            print(f"  📊 {sym}: {len(candles)} Kerzen")
    
    # Parameter-Sweep
    params = []
    for period in [7, 10, 14, 21]:
        for rsi_low in [20, 25, 30, 35]:
            rsi_high = 100 - rsi_low  # symmetrisch
            rsi_exit_low = 50
            rsi_exit_high = 50
            for sl_atr in [1.0, 1.5, 2.0, 2.5, 3.0]:
                for bar_filter in [True, False]:
                    for tp_type in ["rsi", "sma"]:
                        params.append((period, rsi_low, rsi_high, rsi_exit_low, rsi_exit_high, sl_atr, bar_filter, tp_type))
    
    print(f"\n  Sweepe {len(params)} Parameter-Kombinationen...")
    results = []
    
    for idx, (period, rsi_low, rsi_high, rsi_exit_low, rsi_exit_high, sl_atr, bar_filter, tp_type) in enumerate(params):
        combined = {"trades": 0, "wins": 0, "losses": 0, "total_pnl": 0.0, "max_dd": 0.0, "longs": 0, "shorts": 0}
        
        for sym in SYMBOLS:
            if sym not in all_symbols:
                continue
            st = run_backtest(all_symbols[sym], period, rsi_low, rsi_high, rsi_exit_low, rsi_exit_high, sl_atr, bar_filter, tp_type)
            combined["trades"] += st["trades"]
            combined["wins"] += st["wins"]
            combined["losses"] += st["losses"]
            combined["total_pnl"] += st["total_pnl"]
            combined["longs"] += st["longs"]
            combined["shorts"] += st["shorts"]
            if st["max_drawdown"] > combined["max_dd"]:
                combined["max_dd"] = st["max_drawdown"]
        
        if combined["trades"] == 0:
            continue
        
        wr = combined["wins"] / combined["trades"] * 100
        pf = combined["wins"] / max(combined["losses"], 1) if combined["losses"] > 0 else 0
        avg_pnl = combined["total_pnl"] / combined["trades"]
        
        results.append({
            "period": period, "rsi_low": rsi_low, "rsi_high": rsi_high,
            "sl_atr": sl_atr, "bar_filter": bar_filter, "tp_type": tp_type,
            "trades": combined["trades"], "pnl": round(combined["total_pnl"], 2),
            "wr": round(wr, 1), "pf": round(pf, 2),
            "max_dd": round(combined["max_dd"], 1),
            "avg_pnl": round(avg_pnl, 2),
        })
    
    # Ranking nach PnL
    results.sort(key=lambda r: r["pnl"], reverse=True)
    
    print(f"\n  {'='*70}")
    print(f"  RSI OPTIMIERUNG — TOP 20")
    print(f"  {'='*70}")
    print(f"  {'#':>3} {'RSI':>7} {'Entry':>7} {'Exit':>7} {'SLxATR':>7} {'Filter':>6} {'Trades':>7} {'PnL':>10} {'WR':>6} {'PF':>5} {'MaxDD':>7}")
    print(f"  {'-'*70}")
    for i, r in enumerate(results[:20], 1):
        filt = "3Bar" if r["bar_filter"] else "kein"
        tp = "RSI" if r["tp_type"] == "rsi" else "SMA"
        print(f"  {i:>3}  {r['period']:>2}/{r['rsi_low']:>2}-{r['rsi_high']:>2}  {tp:>4}/{r['sl_atr']:.1f}x  {filt:>5}  "
              f"{r['trades']:>5}  {r['pnl']:>+8.2f}  {r['wr']:>4.1f}%  {r['pf']:>4.2f}  {r['max_dd']:>5.1f}%")
    
    # Top-PnL und Top-PF separat
    best_pnl = results[0]
    best_pf = max(results, key=lambda r: r["pf"])
    best_wr = max(results, key=lambda r: r["wr"])
    best_sharpe = max(results, key=lambda r: r["pf"] * r["wr"] / max(r["max_dd"], 1))
    
    print(f"\n  {'='*70}")
    print(f"  BESTE VARIANTEN")
    print(f"  {'='*70}")
    print(f"  🥇 Höchster PnL:   RSI({best_pnl['period']}) Entry<{best_pnl['rsi_low']}/>{best_pnl['rsi_high']} "
          f"| SL={best_pnl['sl_atr']}×ATR | {best_pnl['tp_type']}-Exit | Filter={'3Bar' if best_pnl['bar_filter'] else 'kein'} "
          f"| {best_pnl['pnl']:+} USDT | WR {best_pnl['wr']}% | PF {best_pnl['pf']}")
    print(f"  🥇 Bester PF:     RSI({best_pf['period']}) Entry<{best_pf['rsi_low']}/>{best_pf['rsi_high']} "
          f"| SL={best_pf['sl_atr']}×ATR | {best_pf['tp_type']}-Exit | Filter={'3Bar' if best_pf['bar_filter'] else 'kein'} "
          f"| {best_pf['pnl']:+} USDT | WR {best_pf['wr']}% | PF {best_pf['pf']}")
    print(f"  🥇 Beste WR:      RSI({best_wr['period']}) Entry<{best_wr['rsi_low']}/>{best_wr['rsi_high']} "
          f"| SL={best_wr['sl_atr']}×ATR | {best_wr['tp_type']}-Exit | Filter={'3Bar' if best_wr['bar_filter'] else 'kein'} "
          f"| {best_wr['pnl']:+} USDT | WR {best_wr['wr']}% | PF {best_wr['pf']}")
    
    # Per-Symbol-Analyse für die beste Variante
    print(f"\n  {'='*70}")
    print(f"  PER-SYMBOL-ANALYSE — Beste Variante")
    print(f"  {'='*70}")
    best = best_pnl
    for sym in SYMBOLS:
        if sym not in all_symbols:
            continue
        st = run_backtest(all_symbols[sym], best["period"], best["rsi_low"], best["rsi_high"], 
                         50, 50, best["sl_atr"], best["bar_filter"], best["tp_type"])
        print(f"  {sym:8s}: {st['trades']:>3} Trades | PnL {st['total_pnl']:>+8.2f} USDT | "
              f"WR {st['win_rate']:>4.1f}% | Ø {st['avg_pnl']:>+6.2f} | LONG {st['longs']}/{st['shorts']} | "
              f"MaxDD {st['max_drawdown']:.1f}% | Ø Hold {st['avg_hold_bars']:.0f} Bars")
    
    # Speichern
    with open(RESULT_FILE, "w") as f:
        json.dump({
            "best_pnl": best_pnl,
            "best_pf": best_pf,
            "best_wr": best_wr,
            "top20": results[:20],
        }, f, indent=2)
    
    print(f"\n  💾 Ergebnisse: {RESULT_FILE}")

if __name__ == "__main__":
    main()
