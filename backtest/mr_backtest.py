#!/usr/bin/env python3
"""
Mean Reversion Backtest — Bitget 1H Daten
=========================================
Strategie: Preis weicht vom Mittelwert ab und revertiert zurück.
- Entry Long: close < SMA - k * StdDev (Bollinger-artig) ODER RSI < threshold
- Entry Short: close > SMA + k * StdDev ODER RSI > threshold
- Exit: Preis kreuzt zurück zur SMA (reversion)
- SL: 2× ATR vom Entry
"""
import json, os, math
from datetime import datetime, timezone
from collections import defaultdict

# ── Config ──
SYMBOLS = ["SOLUSDT", "BTCUSDT", "ETHUSDT", "XRPUSDT"]
DATA_DIR = os.path.dirname(__file__)
MAKER_FEE = 0.0002
TAKER_FEE = 0.0006
CAPITAL = 10000  # Startkapital USDT
LEVERAGE = 1     # Kein Hebel — reine Strategie-Bewertung

RESULT_FILE = os.path.join(DATA_DIR, "mr_backtest_results.json")

# ── Varianten ──
VARIANTS = [
    # (name, entry_type, period, k, rsi_low, rsi_high, trend_filter, extra_desc)
    ("BB20x2",     "bb",    20, 2.0, 30, 70, False, "BB(20,2) klassisch"),
    ("BB20x25",    "bb",    20, 2.5, 30, 70, False, "BB(20,2.5) engere Bänder"),
    ("BB50x2",     "bb",    50, 2.0, 30, 70, False, "BB(50,2) längerer Lookback"),
    ("BB20x2+EMA","bb",    20, 2.0, 30, 70, True,  "BB(20,2) + EMA200 Trendfilter"),
    ("ZScore20x2", "zscore",20, 2.0, 30, 70, False, "Z-Score(20, 2)"),
    ("ZScore50x2", "zscore",50, 2.0, 30, 70, False, "Z-Score(50, 2)"),
    ("RSI14",      "rsi",   14, 2.0, 30, 70, False, "RSI(14) <30/>70"),
    ("RSI14-EXT",  "rsi",   14, 2.0, 20, 80, False, "RSI(14) <20/>80 extrem"),
]

# ── Daten laden ──
def load_data(symbol):
    path = os.path.join(DATA_DIR, f"{symbol}_1H.json")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        raw = json.load(f)
    # Format: [ts_ms, open, high, low, close, volume, turnover]
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

# ── Indikatoren ──
def calc_sma(values, period):
    if len(values) < period:
        return None
    return sum(values[-period:]) / period

def calc_std(values, period, mean):
    if len(values) < period:
        return None
    var = sum((v - mean) ** 2 for v in values[-period:]) / period
    return math.sqrt(var)

def calc_ema(values, period):
    if len(values) < period:
        return None
    k = 2 / (period + 1)
    ema = sum(values[:period]) / period  # SMA start
    for v in values[period:]:
        ema = v * k + ema * (1 - k)
    return ema

def calc_rsi(values, period=14):
    if len(values) < period + 1:
        return None
    gains, losses = 0, 0
    for i in range(-period, 0):
        diff = values[i] - values[i - 1]
        if diff > 0:
            gains += diff
        else:
            losses -= diff
    avg_gain = gains / period
    avg_loss = losses / period
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

# ── Backtest Engine ──
def run_backtest(candles, variant):
    name, entry_type, period, k, rsi_low, rsi_high, trend_filter, desc = variant
    n = len(candles)
    
    # Precompute close prices
    closes = [c["close"] for c in candles]
    
    positions = []  # list of dicts
    active_pos = None  # current open position
    
    stats = {
        "trades": 0, "wins": 0, "losses": 0,
        "gross_pnl": 0.0,
        "total_fees": 0.0,
        "max_drawdown": 0.0,
        "equity_peak": CAPITAL,
        "equity": CAPITAL,
        "longs": 0, "shorts": 0,
    }
    equity_curve = [CAPITAL]
    
    entry_bar = 0
    
    for i in range(period + 20, n):
        # Blinde-Zone vermeiden
        window = closes[:i+1]
        c = candles[i]
        c_close = c["close"]
        
        # Indikatoren berechnen
        sma = calc_sma(window, period)
        std = calc_std(window, period, sma) if sma else None
        
        # Trendfilter
        ema200 = calc_ema(window, 200) if trend_filter else None
        
        # Entry Signal
        signal = 0  # 0=kein, 1=long, -1=short
        
        if sma and std:
            if entry_type == "bb":
                lower = sma - k * std
                upper = sma + k * std
                if c_close < lower:
                    signal = 1  # Long: Preis unter unterem Band → Reversion nach oben
                elif c_close > upper:
                    signal = -1  # Short: Preis über oberem Band → Reversion nach unten
                    
            elif entry_type == "zscore":
                z = (c_close - sma) / std if std > 0 else 0
                if z < -k:
                    signal = 1
                elif z > k:
                    signal = -1
                    
            elif entry_type == "rsi":
                if i >= 14:
                    rsi_val = calc_rsi(window, period)
                    if rsi_val is not None:
                        if rsi_val < rsi_low:
                            # Prüfe ob fallend — zusätzlicher Filter
                            if window[-3] >= window[-1]:  # 3 Kerzen fallend
                                signal = 1
                        elif rsi_val > rsi_high:
                            if window[-3] <= window[-1]:  # 3 Kerzen steigend
                                signal = -1
        
        # Trendfilter: nur in Hauptrichtung handeln
        if trend_filter and ema200 is not None:
            if signal == 1 and c_close < ema200:
                signal = 0  # Long unter EMA200 = Abwärtstrend → keine Long-MR
            elif signal == -1 and c_close > ema200:
                signal = 0  # Short über EMA200 = Aufwärtstrend → keine Short-MR
        
        # --- Positions-Management ---
        if active_pos is None and signal != 0:
            assert sma is not None and std is not None  # guaranteed by signal logic
            size = CAPITAL / len(SYMBOLS) / c_close
            size = size * LEVERAGE
            active_pos = {
                "side": "long" if signal == 1 else "short",
                "entry": c_close,
                "size": size,
                "entry_bar": i,
                "entry_ts": candles[i]["ts"],
                "sl": None,
                "tp_reached": False,
            }
            if active_pos["side"] == "long":
                sl_val = 2 * (std if std is not None else sma * 0.01)
                active_pos["sl"] = c_close - sl_val
            else:
                sl_val = 2 * (std if std is not None else sma * 0.01)
                active_pos["sl"] = c_close + sl_val
            entry_bar = i
            stats["total_fees"] += size * c_close * TAKER_FEE
        
        # Offene Position managen
        if active_pos is not None:
            side = active_pos["side"]
            entry = active_pos["entry"]
            sl = active_pos["sl"]
            
            # Exit-Check: Preis kreuzt zurück zur SMA (reversion) ODER SL
            exited = False
            exit_price = None
            exit_reason = ""
            
            if sl and ((side == "long" and c["low"] <= sl) or (side == "short" and c["high"] >= sl)):
                # SL getroffen — Exit zum SL-Preis (bzw. schlechter)
                exit_price = sl
                exit_reason = "SL"
                exited = True
            elif sma:
                if side == "long" and c_close >= sma:
                    exit_price = c_close
                    exit_reason = "Reversion"
                    exited = True
                elif side == "short" and c_close <= sma:
                    exit_price = c_close
                    exit_reason = "Reversion"
                    exited = True
            
            if exited:
                size = active_pos["size"]
                if side == "long":
                    pnl = size * (exit_price - entry)
                else:
                    pnl = size * (entry - exit_price)
                
                fee = size * exit_price * TAKER_FEE
                net_pnl = pnl - fee
                
                stats["gross_pnl"] += pnl
                stats["total_fees"] += fee
                stats["equity"] += net_pnl
                
                stats["trades"] += 1
                if net_pnl > 0:
                    stats["wins"] += 1
                else:
                    stats["losses"] += 1
                if side == "long":
                    stats["longs"] += 1
                else:
                    stats["shorts"] += 1
                
                # Drawdown
                if stats["equity"] > stats["equity_peak"]:
                    stats["equity_peak"] = stats["equity"]
                dd = (stats["equity_peak"] - stats["equity"]) / stats["equity_peak"] * 100
                if dd > stats["max_drawdown"]:
                    stats["max_drawdown"] = dd
                
                equity_curve.append(stats["equity"])
                active_pos = None
        
        equity_curve.append(stats["equity"])
    
    # Finale Berechnungen
    stats["final_equity"] = stats["equity"]
    stats["total_pnl"] = stats["equity"] - CAPITAL
    stats["total_return_pct"] = (stats["equity"] / CAPITAL - 1) * 100
    stats["win_rate"] = (stats["wins"] / stats["trades"] * 100) if stats["trades"] > 0 else 0
    stats["avg_win"] = 0
    stats["avg_loss"] = 0
    stats["profit_factor"] = 0
    
    return stats


# ── Main ──
def main():
    all_symbols = {}
    for sym in SYMBOLS:
        candles = load_data(sym)
        if not candles:
            print(f"  ⚠️  Keine Daten für {sym}")
            continue
        all_symbols[sym] = candles
        print(f"  📊 {sym}: {len(candles)} Kerzen geladen")
    
    results = []
    for v in VARIANTS:
        name = v[0]
        total_pnl = 0.0
        combined = {
            "trades": 0, "wins": 0, "losses": 0,
            "total_pnl": 0.0, "total_fees": 0.0,
            "longs": 0, "shorts": 0,
            "max_drawdown": 0.0,
        }
        print(f"\n  {'='*50}")
        print(f"  📈 {name}: {v[-1]}")
        
        for sym in SYMBOLS:
            if sym not in all_symbols:
                continue
            st = run_backtest(all_symbols[sym], v)
            combined["trades"] += st["trades"]
            combined["wins"] += st["wins"]
            combined["losses"] += st["losses"]
            combined["total_pnl"] += st["total_pnl"]
            combined["total_fees"] += st["total_fees"]
            combined["longs"] += st["longs"]
            combined["shorts"] += st["shorts"]
            if st["max_drawdown"] > combined["max_drawdown"]:
                combined["max_drawdown"] = st["max_drawdown"]
            if st["trades"] > 0:
                print(f"    {sym}: {st['trades']:>3} Trades | "
                      f"PnL {st['total_pnl']:>+8.2f} USDT | "
                      f"WR {st['win_rate']:.0f}% | "
                      f"MaxDD {st['max_drawdown']:.1f}%")
        
        wr = (combined["wins"] / combined["trades"] * 100) if combined["trades"] > 0 else 0
        avg_pnl = combined["total_pnl"] / len(SYMBOLS) if combined["total_pnl"] else 0
        pf = (combined["wins"] / max(combined["losses"], 1)) if combined["losses"] > 0 else 0
        
        print(f"  ──> {name}: {combined['trades']} Trades | "
              f"PnL {combined['total_pnl']:>+8.2f} USDT | "
              f"WR {wr:.1f}% | "
              f"PF {pf:.2f} | "
              f"MaxDD {combined['max_drawdown']:.1f}%")
        
        results.append({
            "name": name,
            "desc": v[-1],
            "trades": combined["trades"],
            "wins": combined["wins"],
            "losses": combined["losses"],
            "win_rate": round(wr, 1),
            "total_pnl": round(combined["total_pnl"], 2),
            "total_fees": round(combined["total_fees"], 2),
            "longs": combined["longs"],
            "shorts": combined["shorts"],
            "profit_factor": round(pf, 2),
            "max_drawdown_pct": round(combined["max_drawdown"], 1),
            "avg_pnl_per_trade": round(combined["total_pnl"] / combined["trades"], 2) if combined["trades"] > 0 else 0,
        })
    
    # Ranking
    results.sort(key=lambda r: r["total_pnl"], reverse=True)
    print(f"\n{'='*60}")
    print(f"  RANKING (nach PnL)")
    print(f"{'='*60}")
    for i, r in enumerate(results, 1):
        medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"  {i}."
        print(f"  {medal} {r['name']:14s} | PnL {r['total_pnl']:>+8.2f} USDT | "
              f"WR {r['win_rate']:5.1f}% | PF {r['profit_factor']:5.2f} | "
              f"MaxDD {r['max_drawdown_pct']:5.1f}% | {r['trades']:>3} Trades")
    
    # Save
    with open(RESULT_FILE, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\n  💾 Ergebnisse gespeichert: {RESULT_FILE}")


if __name__ == "__main__":
    main()
