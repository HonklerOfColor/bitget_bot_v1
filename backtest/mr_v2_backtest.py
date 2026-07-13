#!/usr/bin/env python3
"""
MRv2 Backtest — BAR_FILTER=True vs False
========================================
Exakte Kopie der mr_v2_bot.py Logik auf Bitget 1H-Daten (2026).
Vergleicht BAR_FILTER aktiv vs deaktiviert.
"""
import json, os, math, sys
from datetime import datetime, timezone
from collections import defaultdict

# ── Config (identisch zu mr_v2_bot.py) ──
SYMBOLS = ["SOLUSDT", "BTCUSDT", "ETHUSDT", "XRPUSDT"]
DATA_DIR = os.path.dirname(__file__)
MAKER_FEE = 0.0002
TAKER_FEE = 0.0006
CAPITAL = 10000.0
LEVERAGE = 3

RSI_PERIOD = 7
RSI_ENTRY_LOW = 35
RSI_ENTRY_HIGH = 65
RSI_EXIT = 50
SL_ATR_MULT = 2.0
ATR_PERIOD = 14
RISK_PER_TRADE = 0.02
MAX_POSITIONS = 2

# ── Indikatoren (identisch zu mr_v2_bot.py) ──
def calc_rsi(closes, period=7):
    if len(closes) < period + 1:
        return None
    gains, losses = 0.0, 0.0
    for i in range(-period, 0):
        diff = closes[i] - closes[i-1]
        if diff >= 0: gains += diff
        else: losses -= diff
    avg_gain = gains / period
    avg_loss = losses / period
    if avg_loss == 0: return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))

def calc_sma(values, period):
    if len(values) < period: return None
    return sum(values[-period:]) / period

def calc_atr(candles, idx, period=14):
    if idx < period: return None
    trs = []
    for i in range(idx - period + 1, idx + 1):
        h = float(candles[i][2])
        l = float(candles[i][3])
        pc = float(candles[i-1][4])
        tr = max(h - l, abs(h - pc), abs(l - pc))
        trs.append(tr)
    return sum(trs) / len(trs)

# ── Daten laden ──
def load_data(symbol):
    path = os.path.join(DATA_DIR, f"{symbol}_1H.json")
    with open(path) as f:
        return json.load(f)  # Rohformat: [ts, open, high, low, close, volume, turnover]

# ── Backtest (ein Symbol, eine Konfiguration) ──
def run_backtest(candles, bar_filter):
    n = len(candles)
    closes = [float(c[4]) for c in candles]
    
    equity = CAPITAL
    equity_peak = CAPITAL
    max_dd = 0.0
    
    trades = []
    active_pos = None  # {side, entry, sl, entry_bar}
    
    for i in range(RSI_PERIOD + ATR_PERIOD + 20, n):
        c = candles[i]
        c_close = closes[i]
        
        # --- Exit für aktive Position ---
        if active_pos is not None:
            side = active_pos["side"]
            entry = active_pos["entry"]
            sl = active_pos["sl"]
            entry_bar = active_pos["entry_bar"]
            
            exited = False
            exit_price = None
            exit_reason = ""
            
            # SL-Check (Kerzenhigh/-low)
            if side == "long" and float(c[3]) <= sl:
                exit_price = sl
                exit_reason = "SL"
                exited = True
            elif side == "short" and float(c[2]) >= sl:
                exit_price = sl
                exit_reason = "SL"
                exited = True
            
            # RSI-Exit (zurück zur Mitte)
            if not exited:
                rsi_now = calc_rsi(closes[:i+1], RSI_PERIOD)
                if rsi_now is not None:
                    if side == "long" and rsi_now >= RSI_EXIT:
                        exit_price = c_close
                        exit_reason = "RSI-Exit"
                        exited = True
                    elif side == "short" and rsi_now <= RSI_EXIT:
                        exit_price = c_close
                        exit_reason = "RSI-Exit"
                        exited = True
            
            if exited:
                if side == "long":
                    pnl = active_pos["size"] * (exit_price - entry)
                else:
                    pnl = active_pos["size"] * (entry - exit_price)
                fee = active_pos["size"] * exit_price * TAKER_FEE
                net_pnl = pnl - fee
                
                equity += net_pnl
                if equity > equity_peak: equity_peak = equity
                dd = (equity_peak - equity) / equity_peak * 100 if equity_peak > 0 else 0
                if dd > max_dd: max_dd = dd
                
                trades.append({
                    "side": side, "entry": entry, "exit": exit_price,
                    "entry_bar": entry_bar, "exit_bar": i,
                    "pnl": net_pnl, "reason": exit_reason,
                })
                active_pos = None
                continue  # Kein Entry im gleichen Bar
        
        # --- Entry-Check (nur wenn keine aktive Position) ---
        if active_pos is not None:
            continue
        if len(trades) >= MAX_POSITIONS:
            continue
        
        # Indikatoren
        rsi = calc_rsi(closes[:i+1], RSI_PERIOD)
        sma20 = calc_sma(closes[:i+1], 20)
        atr = calc_atr(candles, i, ATR_PERIOD)
        if rsi is None or sma20 is None or atr is None:
            continue
        
        side = None
        if rsi < RSI_ENTRY_LOW and c_close < sma20 * 0.99:
            side = "long"
        elif rsi > RSI_ENTRY_HIGH and c_close > sma20 * 1.01:
            side = "short"
        
        if side is None:
            continue
        
        # 3-Bar-Filter
        if bar_filter and len(closes[:i+1]) >= 4:
            last3 = closes[i-3:i+1]
            trend_up = last3[-1] > last3[-2] > last3[-3]
            trend_down = last3[-1] < last3[-2] < last3[-3]
            if side == "long" and not trend_up:
                continue
            if side == "short" and not trend_down:
                continue
        
        # Position eröffnen
        sl_price = c_close - SL_ATR_MULT * atr if side == "long" else c_close + SL_ATR_MULT * atr
        sl_dist = abs(c_close - sl_price)
        
        balance = equity
        risk_amount = balance * RISK_PER_TRADE
        size = (risk_amount / sl_dist) * LEVERAGE if sl_dist > 0 else 0
        
        if size <= 0:
            continue
        
        active_pos = {
            "side": side, "entry": c_close, "sl": sl_price,
            "size": size, "entry_bar": i,
        }
    
    # Statistiken
    total_pnl = sum(t["pnl"] for t in trades)
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    n_trades = len(trades)
    win_rate = len(wins) / n_trades * 100 if n_trades > 0 else 0
    avg_win = sum(t["pnl"] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t["pnl"] for t in losses) / len(losses) if losses else 0
    profit_factor = abs(sum(t["pnl"] for t in wins) / sum(t["pnl"] for t in losses)) if losses and sum(t["pnl"] for t in losses) != 0 else float('inf') if wins else 0
    
    return {
        "trades": n_trades,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(win_rate, 1),
        "total_pnl": round(total_pnl, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "profit_factor": round(profit_factor, 2),
        "max_drawdown_pct": round(max_dd, 1),
        "final_equity": round(equity, 2),
        "return_pct": round((equity / CAPITAL - 1) * 100, 1),
        "longs": len([t for t in trades if t["side"] == "long"]),
        "shorts": len([t for t in trades if t["side"] == "short"]),
    }


# ── Main ──
def main():
    print("=" * 65)
    print("  MRv2 Backtest — BAR_FILTER=True vs False")
    print("  Strategie: RSI(7) Entry<35/>65 | Exit RSI=50 | SL 2xATR")
    print(f"  Kapital: {CAPITAL:.0f} USDT | Hebel: {LEVERAGE}x")
    print("=" * 65)
    
    configs = [
        ("BAR_FILTER=True  (3-Bar-Filter aktiv)", True),
        ("BAR_FILTER=False (3-Bar-Filter aus)",   False),
    ]
    
    all_results = {}
    
    for label, bf in configs:
        print(f"\n{'─' * 65}")
        print(f"  📊 {label}")
        print(f"{'─' * 65}")
        
        combined = {
            "trades": 0, "wins": 0, "losses": 0,
            "total_pnl": 0.0, "longs": 0, "shorts": 0,
            "max_dd": 0.0,
        }
        
        for sym in SYMBOLS:
            candles = load_data(sym)
            st = run_backtest(candles, bf)
            
            combined["trades"] += st["trades"]
            combined["wins"] += st["wins"]
            combined["losses"] += st["losses"]
            combined["total_pnl"] += st["total_pnl"]
            combined["longs"] += st["longs"]
            combined["shorts"] += st["shorts"]
            if st["max_drawdown_pct"] > combined["max_dd"]:
                combined["max_dd"] = st["max_drawdown_pct"]
            
            date_from = datetime.fromtimestamp(int(candles[0][0])/1000, tz=timezone.utc).strftime("%d.%m.")
            date_to   = datetime.fromtimestamp(int(candles[-1][0])/1000, tz=timezone.utc).strftime("%d.%m.%Y")
            
            print(f"  {sym:10s} ({date_from}–{date_to}): "
                  f"{st['trades']:>3} Trades | "
                  f"PnL {st['total_pnl']:>+8.2f} USDT | "
                  f"WR {st['win_rate']:>5.1f}% | "
                  f"PF {st['profit_factor']:>5.2f} | "
                  f"MaxDD {st['max_drawdown_pct']:>5.1f}%")
        
        wr = round(combined["wins"] / combined["trades"] * 100, 1) if combined["trades"] > 0 else 0
        pf = round(combined["wins"] / max(combined["losses"], 1), 2)
        print(f"  {'─' * 60}")
        print(f"  GESAMT: {combined['trades']} Trades | "
              f"PnL {combined['total_pnl']:>+8.2f} USDT | "
              f"WR {wr}% | "
              f"PF {pf} | "
              f"MaxDD {combined['max_dd']:.1f}% | "
              f"L={combined['longs']} S={combined['shorts']}")
        
        all_results[label] = combined
    
    print(f"\n{'=' * 65}")
    print("  VERGLEICH")
    print(f"{'=' * 65}")
    for label, data in all_results.items():
        pf = round(data["wins"] / max(data["losses"], 1), 2)
        wr = round(data["wins"] / data["trades"] * 100, 1) if data["trades"] > 0 else 0
        print(f"  {label:40s}: "
              f"PnL {data['total_pnl']:>+8.2f} USDT | "
              f"WR {wr:>5.1f}% | "
              f"PF {pf:>5.2f} | "
              f"DD {data['max_dd']:>5.1f}% | "
              f"{data['trades']:>3} Trades")
    
    # Delta
    if all_results:
        r1 = list(all_results.values())[0]
        r2 = list(all_results.values())[1]
        delta_pnl = r2["total_pnl"] - r1["total_pnl"]
        delta_trades = r2["trades"] - r1["trades"]
        print(f"\n  📌 Delta (FILTER=False − FILTER=True):")
        print(f"     PnL: {delta_pnl:>+8.2f} USDT | Trades: {delta_trades:+d}")

if __name__ == "__main__":
    main()
