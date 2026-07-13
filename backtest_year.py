""""
DS-SpreadScalper Backtest — Letztes Jahr (SHORT-Only, aktuelle Strategie)
=======================================================================
Simuliert exakt die Bot-Logik aus spread_scalper.py (Stand Juli 2026):
- SHORT-Only (wie live config seit 2026-07-09)
- Chart-basierter initialer SL (20er High × 1.001) + min 0.5× ATR, Fallback 1.5× ATR
- Multi-Level TP: 15%@3×ATR, 35%@6×ATR, 50%@9×ATR
- ROE-Trailing: ab 3% Peak-ROE, SL 2% unter Peak (alle 10s → jede Kerze)
- Break-Even bei +3% unrealized
- Spread-Penetration SHORT=70%
- 5× Hebel, Fees: 0.02% Maker / 0.06% Taker
- 4 Symbole: SOLUSDT, BTCUSDT, ETHUSDT, XRPUSDT
- Daten: 1H Kerzen via Bitget API (letztes Jahr ~8760h)

Hinweis: Funding-Rate-Filter (MAX_FUNDING_RATE=0.0005) wird im Backtest
nicht simuliert, da historische Funding-Rates nicht via API abrufbar sind.
"""
import sys, json, math, time, hmac, hashlib, base64
from datetime import datetime, timezone, timedelta
from collections import defaultdict

sys.path.insert(0, "/Users/andreas/bitget_bot_v1")
import config

# ── Exakte Bot-Parameter ──
SYMBOLS = ["SOLUSDT", "BTCUSDT", "ETHUSDT", "XRPUSDT"]
LEVERAGE = 5
MAKER_FEE = 0.0002
TAKER_FEE = 0.0006
BREAKEVEN_PNL_PCT = 0.03  # 3%

MIN_SIZES = {"BTCUSDT": 0.001, "ETHUSDT": 0.05, "SOLUSDT": 0.2, "XRPUSDT": 5}
PRICE_PLACES = {"SOLUSDT": 3, "BTCUSDT": 1, "ETHUSDT": 1, "XRPUSDT": 4}

SPREAD_LONG = 0.3   # 30% LONG
SPREAD_SHORT = 0.7  # 70% SHORT

TP_LEVELS = [
    {"pct": 0.15, "atr_mult": 3.0, "label": "TP1"},
    {"pct": 0.35, "atr_mult": 6.0, "label": "TP2"},
    {"pct": 0.50, "atr_mult": 9.0, "label": "TP3"},
]

# ── Direct Bitget API Calls (kein Bitget-Client nötig, mehr Kontrolle) ──
API_BASE = "https://api.binance.com"

def fetch_binance_klines(symbol, interval, limit=1000, start_time=None, end_time=None):
    """Fetch klines from Binance API (no auth needed)."""
    import requests
    params = {
        "symbol": symbol.replace("USDT", "USDT"),
        "interval": interval,
        "limit": limit,
    }
    if start_time:
        params["startTime"] = start_time
    if end_time:
        params["endTime"] = end_time
    
    try:
        r = requests.get(API_BASE + "/api/v3/klines", params=params, timeout=30)
        data = r.json()
        if isinstance(data, dict) and "code" in data:
            print(f"  ⚠️  Binance Error: {data}")
            return None
        return data
    except Exception as e:
        print(f"  ⚠️  Request Error: {e}")
        return None

def fetch_year_candles_binance(symbol):
    """Fetch ~1 year of 1H candles from Binance (startTime pagination, ~9 batches)."""
    now = int(time.time() * 1000)
    one_year_ago = now - 366 * 24 * 3600 * 1000
    
    all_candles = []
    batch_start = one_year_ago
    batch_num = 0
    
    while True:
        batch = fetch_binance_klines(symbol, "1h", limit=1000, start_time=batch_start)
        if not batch or len(batch) == 0:
            break
        
        all_candles.extend(batch)
        batch_num += 1
        
        last_ts = int(batch[-1][0])  # newest in this batch
        
        if batch_num == 1:
            first_dt = datetime.fromtimestamp(int(batch[0][0]) / 1000, tz=timezone.utc)
            last_dt = datetime.fromtimestamp(int(batch[-1][0]) / 1000, tz=timezone.utc)
            print(f"     📦 Batch 1: {len(batch)} Kerzen ({first_dt.strftime('%d.%m')} → {last_dt.strftime('%d.%m')})")
        
        if last_ts >= now:
            break
        
        # Next batch starts after the last candle
        batch_start = last_ts + 3600000  # next hour
        time.sleep(0.15)  # Rate limit
    
    # Sort ascending (Binance returns ascending by default)
    unique = sorted(all_candles, key=lambda x: int(x[0]))
    
    # Deduplicate by timestamp
    seen = set()
    trimmed = []
    for c in unique:
        ts = int(c[0])
        if ts not in seen:
            seen.add(ts)
            trimmed.append(c)
    
    # Trim to ~1 year
    cutoff = one_year_ago
    trimmed = [c for c in trimmed if int(c[0]) >= cutoff]
    
    print(f"     📊 {len(trimmed)} Kerzen ({len(unique)} unique, {len(all_candles)} roh, {batch_num} Batches)")
    return trimmed


# ── Backtest Logic ──
def calc_atr(candles, idx, period=14):
    if idx < period:
        return None
    trs = []
    for i in range(idx - period + 1, idx + 1):
        h = float(candles[i][2])
        l = float(candles[i][3])
        pc = float(candles[i - 1][4])
        tr = max(h - l, abs(h - pc), abs(l - pc))
        trs.append(tr)
    return sum(trs) / len(trs)

def calc_chart_sl(candles, idx, entry_price, side, price_places):
    """Chart-based SL wie Bot: 20 candles high*1.001, min 0.5× ATR, fallback 1.5× ATR."""
    lookback = min(idx, 20)
    if lookback < 5:
        # Fallback
        atr = calc_atr(candles, idx)
        if not atr or atr <= 0:
            return None
        if side == "short":
            return round(entry_price + atr * 1.5, price_places)
        else:
            return round(entry_price - atr * 1.5, price_places)
    
    sub = candles[idx - lookback:idx]
    highs = [float(k[2]) for k in sub]
    lows = [float(k[3]) for k in sub]
    atr = calc_atr(candles, idx)
    
    if side == "short":
        highest = max(highs)
        sl = round(highest * 1.001, price_places)
        if atr and sl < entry_price + atr * 0.5:
            sl = round(entry_price + atr * 0.5, price_places)
        return sl
    else:
        lowest = min(lows)
        sl = round(lowest * 0.999, price_places)
        if atr and sl > entry_price - atr * 0.5:
            sl = round(entry_price - atr * 0.5, price_places)
        return sl


def run_backtest(symbol, candles):
    """Simuliere Bot-Strategie LONG+SHORT auf 1H Kerzen über 1 Jahr.
    Spread-Penetration: 30% LONG, 70% SHORT.
    """
    trades = []
    position = None
    entry_count = 0  # für 30/70 ratio
    
    for i in range(1, len(candles)):
        ts = int(candles[i][0])
        dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
        o = float(candles[i][1])
        h = float(candles[i][2])
        l = float(candles[i][3])
        c = float(candles[i][4])
        
        price_places = PRICE_PLACES.get(symbol, 2)
        
        atr = calc_atr(candles, i)
        if atr is None or atr <= 0:
            continue
        
        if position is None:
            # ── Alternating Entry: 30% LONG / 70% SHORT ──
            entry_count += 1
            is_long = (entry_count % 10) < 3  # 3 von 10 = 30%
            side = "long" if is_long else "short"
            entry = o
            size = MIN_SIZES.get(symbol, 0.1)
            
            notional = entry * size
            if notional < 4.95:
                continue
            
            # Initialer SL: Chart-basiert
            sl_price = calc_chart_sl(candles, i, entry, side, price_places)
            if sl_price is None:
                if side == "short":
                    sl_price = round(entry + atr * 1.5, price_places)
                else:
                    sl_price = round(entry - atr * 1.5, price_places)
            
            # TP Levels (entgegengesetzte Richtung)
            tp_prices = []
            for level in TP_LEVELS:
                if side == "short":
                    tp = round(entry - atr * level["atr_mult"], price_places)
                else:
                    tp = round(entry + atr * level["atr_mult"], price_places)
                tp_prices.append(tp)
            
            entry_fee = notional * MAKER_FEE
            margin = (size * entry) / LEVERAGE
            
            position = {
                "side": side,
                "entry": entry,
                "size": size,
                "sl": sl_price,
                "tp_prices": tp_prices,
                "tp_level": 0,
                "ts": ts,
                "entry_fee": entry_fee,
                "margin": margin,
                "peak_roe": -999.0,
                "breakeven_activated": False,
            }
        else:
            side = position["side"]
            entry = position["entry"]
            sl = position["sl"]
            size = position["size"]
            margin = position["margin"]
            tp_prices = position["tp_prices"]
            tp_level = position["tp_level"]
            
            # PnL auf 1H High/Low
            if side == "short":
                worst_price = h  # Short: worst=High
                best_price = l   # Short: best=Low
                pnl_check = (entry - worst_price) * size
                pnl_best = (entry - best_price) * size
            else:
                worst_price = l  # Long: worst=Low
                best_price = h   # Long: best=High
                pnl_check = (worst_price - entry) * size
                pnl_best = (best_price - entry) * size
            
            roe_pct = pnl_check / margin * 100 if margin else 0
            roe_best_pct = pnl_best / margin * 100 if margin else 0
            
            # Peak-ROE auf Close-Basis (wie Bot)
            if side == "short":
                close_pnl = (entry - c) * size
            else:
                close_pnl = (c - entry) * size
            close_roe = close_pnl / margin * 100 if margin else 0
            peak_roe = max(position.get("peak_roe", -999), close_roe)
            position["peak_roe"] = peak_roe
            
            # ── ROE-Trailing (ab 3% Peak) ──
            if peak_roe >= (BREAKEVEN_PNL_PCT * 100):  # 3%
                target_roe = peak_roe - 2.0  # 2% unter Peak
                pnl_target = target_roe / 100 * margin
                if side == "short":
                    new_sl = round(entry - pnl_target / size, price_places)
                    # Short: SL tighten (new_sl < sl) && SL > mark
                    if new_sl < sl and new_sl > worst_price:
                        sl = new_sl
                        position["sl"] = sl
                else:
                    new_sl = round(entry + pnl_target / size, price_places)
                    # Long: SL tighten (new_sl > sl) && SL < mark
                    if new_sl > sl and new_sl < worst_price:
                        sl = new_sl
                        position["sl"] = sl
            
            # ── TP Check ──
            hit_tp = False
            exit_tp_price = 0
            tp_pnl = 0.0
            tp_reason = ""
            tp_portion = 0.0
            
            for lvl_idx in range(tp_level, len(TP_LEVELS)):
                tp = tp_prices[lvl_idx]
                if (side == "short" and l <= tp) or (side == "long" and h >= tp):
                    portion = TP_LEVELS[lvl_idx]["pct"]
                    if side == "short":
                        portion_pnl = (entry - tp) * size * portion
                    else:
                        portion_pnl = (tp - entry) * size * portion
                    tp_pnl += portion_pnl
                    exit_tp_price = tp
                    hit_tp = True
                    tp_reason = f"TP{lvl_idx+1}"
                    tp_portion += portion
                    if lvl_idx == 0:
                        position["tp_level"] = lvl_idx + 1
                        if not position.get("breakeven_activated"):
                            position["sl"] = entry
                            position["breakeven_activated"] = True
                    break
            
            # ── SL Check ──
            hit_sl = False
            exit_sl_price = 0
            sl_loss = 0.0
            
            if (side == "short" and h >= sl) or (side == "long" and l <= sl):
                remaining = 1.0 - tp_portion
                if remaining > 0:
                    hit_sl = True
                    exit_sl_price = sl
                    if side == "short":
                        sl_pnl = (entry - sl) * size * remaining
                    else:
                        sl_pnl = (sl - entry) * size * remaining
                    sl_loss = sl_pnl
                elif tp_portion >= 1.0:
                    hit_sl = False
            
            # ── Trade abschliessen ──
            if hit_sl or hit_tp:
                net_pnl = tp_pnl + sl_loss
                exit_fee = exit_sl_price * size * TAKER_FEE if hit_sl else (exit_tp_price * size * tp_portion * TAKER_FEE if tp_portion > 0 else 0)
                net_pnl -= position["entry_fee"] + exit_fee
                
                reason = tp_reason if hit_tp else "SL"
                
                trades.append({
                    "ts": dt.strftime("%Y-%m-%d %H:%M"),
                    "symbol": symbol,
                    "side": side,
                    "entry": entry,
                    "exit": exit_sl_price if hit_sl else exit_tp_price,
                    "pnl": round(net_pnl, 4),
                    "fees": round(position["entry_fee"] + exit_fee, 4),
                    "net_pnl": round(net_pnl, 4),
                    "reason": reason,
                    "atr": round(atr, 4),
                    "sl": sl,
                    "roe_peak": round(peak_roe, 2),
                    "tp_reached": tp_portion,
                })
                position = None
    
    return trades


# ══════ MAIN ══════
print("=" * 120)
print(f"{'DS-SpreadScalper Backtest — LETZTES JAHR (LONG+SHORT, 30/70)':^120}")
print(f"{'ATR-Chart-SL · Multi-Level TP · ROE-Trailing 3% · 30% LONG / 70% SHORT · 5× Hebel':^120}")
print(f"{'4 Symbole — ' + datetime.now().strftime('%d.%m.%Y %H:%M'):^120}")
print("=" * 120)

all_trades = {s: [] for s in SYMBOLS}
data_stats = {}

for symbol in SYMBOLS:
    print(f"\n📡 {symbol}: Lade 1 Jahr 1H-Daten von Binance API...")
    sys.stdout.flush()
    candles = fetch_year_candles_binance(symbol)
    
    if len(candles) < 100:
        print(f"  ❌ Nicht genug Daten: {len(candles)} Kerzen")
        continue
    
    first_dt = datetime.fromtimestamp(int(candles[0][0])/1000, tz=timezone.utc)
    last_dt = datetime.fromtimestamp(int(candles[-1][0])/1000, tz=timezone.utc)
    data_stats[symbol] = {
        "candles": len(candles),
        "from": first_dt.strftime("%Y-%m-%d"),
        "to": last_dt.strftime("%Y-%m-%d"),
    }
    print(f"  🕐 {first_dt.strftime('%d.%m.%Y')} → {last_dt.strftime('%d.%m.%Y')} ({len(candles)} Kerzen)")
    
    trades = run_backtest(symbol, candles)
    all_trades[symbol] = trades
    print(f"  📊 {len(trades)} Trades simuliert")


# ── RESULTS ──
def print_results(all_trades, label):
    all_t = []
    for s in SYMBOLS:
        all_t.extend(all_trades[s])
    
    total_trades = len(all_t)
    if total_trades == 0:
        print(f"\n  ⏭️  Keine Trades ({label})")
        return
    
    total_wins = len([t for t in all_t if t["net_pnl"] > 0])
    total_losses = len([t for t in all_t if t["net_pnl"] <= 0])
    total_pnl = sum(t["net_pnl"] for t in all_t)
    total_fees = sum(t["fees"] for t in all_t)
    gross_pnl = total_pnl + total_fees
    wr = total_wins / total_trades * 100 if total_trades else 0
    avg_net = total_pnl / total_trades if total_trades else 0
    win_total = sum(t["net_pnl"] for t in all_t if t["net_pnl"] > 0)
    loss_total = abs(sum(t["net_pnl"] for t in all_t if t["net_pnl"] < 0))
    pf = win_total / loss_total if loss_total else float('inf')
    
    # Drawdown
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
    
    # Consecutive losses
    max_consec = 0
    cur_consec = 0
    consec_streaks = []
    for t in all_t:
        if t["net_pnl"] <= 0:
            cur_consec += 1
            max_consec = max(max_consec, cur_consec)
        else:
            if cur_consec > 0:
                consec_streaks.append(cur_consec)
            cur_consec = 0
    if cur_consec > 0:
        consec_streaks.append(cur_consec)
    
    sl_count = len([t for t in all_t if t["reason"] == "SL"])
    tp_count = len([t for t in all_t if t["reason"] not in ("SL",)])
    sl_pnl = sum(t["net_pnl"] for t in all_t if t["reason"] == "SL")
    tp_pnl = sum(t["net_pnl"] for t in all_t if t["reason"] != "SL")
    
    print(f"\n{'=' * 100}")
    print(f"  {label}")
    print(f"{'=' * 100}")
    print(f"  Trades:             {total_trades:>6}")
    print(f"  Winrate:            {wr:>5.1f}%  ({total_wins}W/{total_losses}L)")
    print(f"  Total PnL:          {total_pnl:>+10.4f} USDT")
    print(f"  Gross PnL:          {gross_pnl:>+10.4f} USDT  (Fees: {total_fees:.4f})")
    print(f"  Profit Factor:      {pf:>7.2f}")
    print(f"  Ø PnL/Trade:        {avg_net:>+10.4f} USDT")
    print(f"  Max Drawdown:       {max_dd:>10.4f} USDT ({max_dd_pct:.2f}%)")
    print(f"  Final PnL:          {cumulative:>+10.4f} USDT")
    print(f"  Max Consec Loss:    {max_consec:>4}")
    print(f"  Loss Streaks:       {consec_streaks}")
    print(f"  SL-Exits:           {sl_count:>4}  ({sl_pnl:+.4f} USDT)")
    print(f"  TP-Exits:           {tp_count:>4}  ({tp_pnl:+.4f} USDT)")
    
    # Per-Side Breakdown
    for check_side in ["long", "short"]:
        side_t = [t for t in all_t if t["side"] == check_side]
        sn = len(side_t)
        if sn == 0:
            continue
        sw = len([t for t in side_t if t["net_pnl"] > 0])
        spnl = sum(t["net_pnl"] for t in side_t)
        swpnl = sum(t["net_pnl"] for t in side_t if t["net_pnl"] > 0)
        slpnl = abs(sum(t["net_pnl"] for t in side_t if t["net_pnl"] < 0))
        spf = swpnl / slpnl if slpnl else float('inf')
        ssl = len([t for t in side_t if t["reason"] == "SL"])
        stp = len([t for t in side_t if t["reason"] != "SL"])
        swr = sw / sn * 100 if sn else 0
        sag = spnl / sn if sn else 0
        print(f"  {check_side.upper():<10} {sn:>4}T  WR={swr:>5.1f}%  PnL={spnl:>+10.4f}  PF={spf:>5.2f}  SL={ssl:>3}  TP={stp:>3}  \u00d8={sag:>+10.4f}")
    
    # Per-Symbol
    print(f"\n  {'Symbol':<10} {'Trades':>6} {'WR':>6} {'PnL':>12} {'PF':>6} {'SL':>4} {'TP':>4} {'Ø PnL':>10} {'MaxDD₿':>8}")
    print(f"  {'─' * 66}")
    for s in SYMBOLS:
        t = all_trades[s]
        n = len(t)
        if n == 0:
            continue
        w = len([x for x in t if x['net_pnl'] > 0])
        l = len([x for x in t if x['net_pnl'] <= 0])
        wr_s = w / n * 100
        pnl = sum(x['net_pnl'] for x in t)
        wpnl = sum(x['net_pnl'] for x in t if x['net_pnl'] > 0)
        lpnl = abs(sum(x['net_pnl'] for x in t if x['net_pnl'] < 0))
        pf_s = wpnl / lpnl if lpnl else float('inf')
        sl = len([x for x in t if x['reason'] == 'SL'])
        tp = len([x for x in t if x['reason'] != 'SL'])
        avg = pnl / n if n else 0
        
        # DD per symbol
        cum = 0
        peak_s = 0
        max_dd_s = 0
        for x in t:
            cum += x["net_pnl"]
            peak_s = max(peak_s, cum)
            max_dd_s = max(max_dd_s, peak_s - cum)
        
        print(f"  {s:<10} {n:>6} {wr_s:>5.1f}% {pnl:>+11.4f} {pf_s:>5.2f} {sl:>4} {tp:>4} {avg:>+10.4f} {max_dd_s:>8.4f}")
    
    # Top wins/losses
    if total_trades > 0:
        losses = sorted([t for t in all_t if t["net_pnl"] < 0], key=lambda x: x["net_pnl"])[:3]
        wins = sorted([t for t in all_t if t["net_pnl"] > 0], key=lambda x: x["net_pnl"], reverse=True)[:3]
        if losses:
            print(f"\n  ⚠️  Top-3 Verluste:")
            for t in losses:
                print(f"     {t['ts']} {t['symbol']:<8} @ {t['entry']:<10.4f} → {t['exit']:<10.4f} | PnL={t['net_pnl']:<+10.4f} | {t['reason']} (SL={t['sl']})")
        if wins:
            print(f"  🏆 Top-3 Gewinne:")
            for t in wins:
                print(f"     {t['ts']} {t['symbol']:<8} @ {t['entry']:<10.4f} → {t['exit']:<10.4f} | PnL={t['net_pnl']:<+10.4f} | {t['reason']} (PeakROE={t['roe_peak']}%)")
    
    # Monthly breakdown
    months = defaultdict(lambda: {"trades": 0, "pnl": 0.0, "wins": 0, "losses": 0})
    for t in all_t:
        month = t["ts"][:7]  # YYYY-MM
        months[month]["trades"] += 1
        months[month]["pnl"] += t["net_pnl"]
        if t["net_pnl"] > 0:
            months[month]["wins"] += 1
        else:
            months[month]["losses"] += 1
    
    if months:
        print(f"\n  📅 Monatliche Performance:")
        print(f"  {'Monat':<10} {'Trades':>6} {'WR':>6} {'PnL':>12}")
        print(f"  {'─' * 34}")
        for month in sorted(months.keys()):
            m = months[month]
            wr_m = m["wins"] / m["trades"] * 100 if m["trades"] else 0
            print(f"  {month:<10} {m['trades']:>6} {wr_m:>5.1f}% {m['pnl']:>+11.4f}")
    
    # Save to JSON
    try:
        with open(f"/Users/andreas/bitget_bot_v1/backtest/backtest_year_results.json", "w") as f:
            json.dump({
                "params": {
                    "symbols": SYMBOLS,
                    "leverage": LEVERAGE,
                    "mode": "long_short",
                    "spread": "LONG 30% / SHORT 70%",
                    "sl": "chart-based (20h high*1.001/low*0.999, min 0.5×ATR, fallback 1.5×ATR)",
                    "tp": "Multi-Level (3×/6×/9× ATR)",
                    "roe_trailing": "ab 3% Peak, 2% Guard",
                    "fees": f"Maker {MAKER_FEE}, Taker {TAKER_FEE}",
                },
                "summary": {
                    "trades": total_trades,
                    "winrate": round(wr, 2),
                    "total_pnl": round(total_pnl, 4),
                    "profit_factor": round(pf, 2),
                    "max_drawdown": round(max_dd, 4),
                    "max_dd_pct": round(max_dd_pct, 2),
                    "max_consec_losses": max_consec,
                    "final_pnl": round(cumulative, 4),
                },
                "trades": all_t,
                "data_stats": data_stats,
            }, f, indent=2)
        print(f"\n  💾 Ergebnisse gespeichert: backtest/backtest_year_results.json")
    except Exception as e:
        print(f"\n  ⚠️  Speichern fehlgeschlagen: {e}")
    
    return {
        "trades": total_trades, "wr": wr, "pnl": total_pnl,
        "pf": pf, "max_dd": max_dd, "max_dd_pct": max_dd_pct,
        "max_consec": max_consec, "final_pnl": cumulative,
        "monthly": dict(sorted(months.items())) if months else {},
    }


# ── Ausgabe ──
res = print_results(all_trades, "📊 LONG+SHORT (Letztes Jahr, 30/70)")

print(f"\n{'=' * 100}")
print(f"{'BACKTEST ABGESCHLOSSEN — ' + datetime.now().strftime('%d.%m.%Y %H:%M'):^100}")
print(f"{'=' * 100}")

print(f"\n⚠️  HINWEIS: Funding-Rate-Filter (MAX_FUNDING_RATE=0.0005) nicht simuliert,")
print(f"   da historische Funding-Rates nicht via API abrufbar sind.")
print(f"   Live-Bot blockiert SHORT wenn Funding > 0.05% (z.B. ETH bei +0.375%).")
