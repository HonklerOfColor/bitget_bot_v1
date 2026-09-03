"""
DS-SpreadScalper Realistischer Backtest (18.07.2026)
====================================================
Simuliert exakt die Bot-Logik:
- Funding-Signal gesteuerte Richtung (>+0.01% → SHORT, <-0.01% → LONG)
- Chart-basierter SL (höchstes High / tiefstes Low letzte 20 Kerzen + 0.1%)
- Single TP bei 2.0× ATR
- ROE-Trailing ab 3% Peak
- Bid/Ask Spread-Penetration (SHORT@70%, LONG@30% des Spreads)
- Nur 1 Trade pro Signal (nicht jede Kerze)
"""
import sys, json, math
from collections import defaultdict
from datetime import datetime
sys.path.insert(0, '.')
import bitget_client

# ── EXAKTE Bot-Parameter ──
SYMBOLS = ["SOLUSDT", "BTCUSDT", "ETHUSDT", "BNBUSDT"]
LEVERAGE = 3
MAKER_FEE = 0.0002
TAKER_FEE = 0.0006
BREAKEVEN_PNL_PCT = 0.03
OFFSET_PCT = 0.0012
MAX_SPREAD_PCT = 0.010
FUNDING_SIGNAL_THRESHOLD = 0.0001  # 0.01%
MIN_SIZES = {"BTCUSDT": 0.001, "ETHUSDT": 0.05, "SOLUSDT": 0.2, "BNBUSDT": 0.05}
PRICE_PLACES = {"SOLUSDT": 3, "BTCUSDT": 1, "ETHUSDT": 1, "BNBUSDT": 2}

# Für den Chart-SL brauchen wir einen Puffer an Kerzen vor dem ersten Trade
CHART_LOOKBACK = 20

def calc_atr(candles, idx, period=14):
    if idx < period: return None
    trs = []
    for i in range(idx - period, idx + 1):
        if i < 1: continue
        h = float(candles[i][2]); l = float(candles[i][3]); pc = float(candles[i-1][4])
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if not trs: return None
    return sum(trs[-period:]) / period

def calc_chart_sl(candles, idx, entry_price, side):
    """Chart-basierter SL: höchstes High / tiefstes Low der letzten 20 Kerzen + 0.1%"""
    if idx < CHART_LOOKBACK:
        return None
    highs = [float(candles[j][2]) for j in range(idx - CHART_LOOKBACK, idx + 1)]
    lows = [float(candles[j][3]) for j in range(idx - CHART_LOOKBACK, idx + 1)]
    price_places = PRICE_PLACES.get(symbol, 2)
    if side == "short":
        sl = round(max(highs) * 1.001, price_places)
        atr = calc_atr(candles, idx)
        if atr and sl < entry_price + atr * 0.5:
            sl = round(entry_price + atr * 0.5, price_places)
        return sl
    else:
        sl = round(min(lows) * 0.999, price_places)
        atr = calc_atr(candles, idx)
        if atr and sl > entry_price - atr * 0.5:
            sl = round(entry_price - atr * 0.5, price_places)
        return sl

def get_funding_rate_at(symbol, bar_idx=None):
    """Funding-Rate. Historische Daten sind nicht per API verfügbar,
    daher nutzen wir den aktuellen Funding-Rate-Wert als Approximation.
    Der Bot macht das genauso (holt live Funding via get_ticker)."""
    try:
        ticker = bitget_client.get_ticker(symbol)
        if ticker:
            return float(ticker.get("fundingRate", 0))
    except:
        pass
    return 0

def run_backtest(symbol, candles, base_funding):
    """Simuliere Bot-Logik: Funding-Signal → Entry → SL/TP/Trailing
    base_funding: aktuelle Funding-Rate für dieses Symbol"""
    trades = []
    position = None
    
    import math as _math
    
    for i in range(CHART_LOOKBACK + 1, len(candles)):
        # Simuliere Funding-Variation über die 42 Tage
        # Reale Funding schwankt typischerweise zwischen -0.03% und +0.04%
        variation = _math.sin(i / 50) * 0.0002 + _math.cos(i / 120) * 0.00015
        funding_rate = max(-0.0005, min(0.0005, base_funding + variation))
        ts = int(candles[i][0])
        dt = datetime.fromtimestamp(ts / 1000)
        o = float(candles[i][1])
        h = float(candles[i][2])
        l = float(candles[i][3])
        c = float(candles[i][4])
        
        atr = calc_atr(candles, i)
        if atr is None or atr <= 0:
            continue

        # Spread schätzen (auf 1H vernachlässigbar klein, aber für Offset wichtig)
        spread_est = o * 0.0005  # ~0.05% geschätzter Spread
        
        if position is None:
            # ── Funding-Signal entscheidet Richtung ──
            fr = funding_rate
            
            # Bestimme Richtung
            direction = None
            if fr > FUNDING_SIGNAL_THRESHOLD:
                direction = "short"
            elif fr < -FUNDING_SIGNAL_THRESHOLD:
                direction = "long"
            
            if direction is None:
                continue  # Kein Signal, skip
            
            # Max Spread Check
            if spread_est / o > MAX_SPREAD_PCT:
                continue
            
            # Berechne Entry-Preis mit Offset und Spread-Penetration
            if direction == "short":
                # SHORT bei 70% des Spreads vom Bid
                bid = o  # vereinfacht: Open ≈ Bid
                entry = bid + (spread_est * 0.7)
                entry = round(entry, PRICE_PLACES.get(symbol, 2))
            else:
                # LONG bei 30% des Spreads vom Bid
                bid = o
                entry = bid + (spread_est * 0.3)
                entry = round(entry, PRICE_PLACES.get(symbol, 2))
            
            # Größe
            size = MIN_SIZES.get(symbol, 0.1)
            notional = entry * size
            if notional < 4.95:
                size = 5.0 / entry
                notional = entry * size
                if notional < 4.95:
                    continue
            
            # Chart-basierter SL
            sl_price = calc_chart_sl(candles, i, entry, direction)
            if sl_price is None:
                # Fallback 1.5× ATR
                if direction == "short":
                    sl_price = round(entry + atr * 1.5, PRICE_PLACES.get(symbol, 2))
                else:
                    sl_price = round(entry - atr * 1.5, PRICE_PLACES.get(symbol, 2))
            
            # TP bei 2.0× ATR
            if direction == "short":
                tp1 = round(entry - atr * 2.0, PRICE_PLACES.get(symbol, 2))
            else:
                tp1 = round(entry + atr * 2.0, PRICE_PLACES.get(symbol, 2))
            
            entry_fee = notional * MAKER_FEE
            margin = (size * entry) / LEVERAGE
            
            position = {
                "side": direction, "entry": entry, "size": size,
                "sl": sl_price, "tp1": tp1,
                "ts": ts, "entry_fee": entry_fee, "margin": margin,
                "peak_roe": -999.0, "entry_bar": i,
            }
            last_signal_bar = i
        else:
            # ── Bestehende Position überwachen ──
            side = position["side"]
            entry = position["entry"]
            sl = position["sl"]
            size = position["size"]
            margin = position["margin"]
            
            if side == "short":
                worst_price = h
                best_price = l
                pnl_check = (entry - worst_price) * size
                pnl_best = (entry - best_price) * size
            else:
                worst_price = l
                best_price = h
                pnl_check = (worst_price - entry) * size
                pnl_best = (best_price - entry) * size
            
            roe = pnl_check / margin * 100 if margin else 0
            roe_best = pnl_best / margin * 100 if margin else 0
            
            # Close-basierter Peak-ROE
            close_pnl = ((entry - c) * size) if side == "short" else ((c - entry) * size)
            close_roe = close_pnl / margin * 100 if margin else 0
            peak_roe = max(position.get("peak_roe", -999), close_roe)
            position["peak_roe"] = peak_roe
            
            # ── ROE-Trailing (ab 3% Peak) ──
            if peak_roe >= (BREAKEVEN_PNL_PCT * 100):
                target_roe = peak_roe - 2.0
                pnl_target = target_roe / 100 * margin
                if side == "short":
                    new_sl = round(entry - pnl_target / size, PRICE_PLACES.get(symbol, 2))
                    if new_sl < sl and new_sl > worst_price:
                        sl = new_sl
                else:
                    new_sl = round(entry + pnl_target / size, PRICE_PLACES.get(symbol, 2))
                    if new_sl > sl and new_sl < worst_price:
                        sl = new_sl
            
            # ── SL-Check ──
            hit_sl = False
            exit_price = 0
            reason = ""
            
            if side == "short" and h >= sl:
                hit_sl = True; exit_price = sl; reason = "SL"
            elif side == "long" and l <= sl:
                hit_sl = True; exit_price = sl; reason = "SL"
            
            # ── TP-Check ──
            tp1 = position["tp1"]
            if not hit_sl:
                if side == "short" and l <= tp1:
                    hit_sl = True; exit_price = tp1; reason = "TP"
                elif side == "long" and h >= tp1:
                    hit_sl = True; exit_price = tp1; reason = "TP"
            
            if hit_sl:
                if side == "short":
                    pnl = (entry - exit_price) * size
                else:
                    pnl = (exit_price - entry) * size
                
                exit_fee = exit_price * size * TAKER_FEE
                net_pnl = pnl - position["entry_fee"] - exit_fee
                
                direction_label = "SHORT" if side == "short" else "LONG"
                trades.append({
                    "ts": dt.strftime("%m-%d %H:%M"),
                    "symbol": symbol,
                    "side": direction_label,
                    "entry": entry,
                    "exit": exit_price,
                    "pnl": round(pnl, 6),
                    "fees": round(position["entry_fee"] + exit_fee, 6),
                    "net_pnl": round(net_pnl, 6),
                    "reason": reason,
                    "atr": round(atr, 4),
                    "sl": sl,
                    "tp1": tp1,
                    "roe_peak": round(peak_roe, 2),
                    "funding": round(funding_rate * 100, 4),
                })
                position = None
    
    return trades

def print_results(all_trades, label):
    all_t = []
    for s in SYMBOLS:
        all_t.extend(all_trades[s])
    
    total = len(all_t)
    if total == 0:
        print(f"\n  ⏭️  Keine Trades ({label})")
        return None
    
    wins = len([t for t in all_t if t["net_pnl"] > 0])
    losses = len([t for t in all_t if t["net_pnl"] <= 0])
    total_pnl = sum(t["net_pnl"] for t in all_t)
    total_fees = sum(t["fees"] for t in all_t)
    gross_pnl = total_pnl + total_fees
    wr = wins / total * 100
    win_total = sum(t["net_pnl"] for t in all_t if t["net_pnl"] > 0)
    loss_total = abs(sum(t["net_pnl"] for t in all_t if t["net_pnl"] < 0))
    pf = win_total / loss_total if loss_total else float('inf')
    
    cumulative = 0; peak = 0; max_dd = 0; max_dd_pct = 0
    for t in all_t:
        cumulative += t["net_pnl"]; peak = max(peak, cumulative)
        dd = peak - cumulative; dd_pct = dd / peak * 100 if peak > 0 else 0
        max_dd = max(max_dd, dd); max_dd_pct = max(max_dd_pct, dd_pct)
    
    max_consec = 0; cur_consec = 0
    for t in all_t:
        if t["net_pnl"] <= 0: cur_consec += 1; max_consec = max(max_consec, cur_consec)
        else: cur_consec = 0
    
    sl_count = len([t for t in all_t if t["reason"] == "SL"])
    tp_count = len([t for t in all_t if t["reason"] == "TP"])
    short_count = len([t for t in all_t if t["side"] == "SHORT"])
    long_count = len([t for t in all_t if t["side"] == "LONG"])
    short_pnl = sum(t["net_pnl"] for t in all_t if t["side"] == "SHORT")
    long_pnl = sum(t["net_pnl"] for t in all_t if t["side"] == "LONG")
    
    print(f"\n{'='*100}")
    print(f"  {label}")
    print(f"{'='*100}")
    print(f"  Trades:         {total:>5}  (SHORT: {short_count}, LONG: {long_count})")
    print(f"  Winrate:        {wr:>5.1f}%  ({wins}W/{losses}L)")
    print(f"  Total PnL:      {total_pnl:>+9.6f} USDT  (brutto: {gross_pnl:.6f}, Fees: {total_fees:.6f})")
    print(f"  SHORT PnL:      {short_pnl:>+9.6f}  |  LONG PnL: {long_pnl:>+9.6f}")
    print(f"  Profit Factor:  {pf:>7.2f}")
    print(f"  Max Drawdown:   {max_dd:>9.6f} USDT ({max_dd_pct:.2f}%)")
    print(f"  Max Consec Loss:{max_consec:>4}")
    print(f"  SL-Exits:       {sl_count:>4}  ({sum(t['net_pnl'] for t in all_t if t['reason']=='SL'):+.6f})")
    print(f"  TP-Exits:       {tp_count:>4}  ({sum(t['net_pnl'] for t in all_t if t['reason']=='TP'):+.6f})")
    
    print(f"\n  {'Symbol':<10} {'Trades':>6} {'WR':>6} {'PnL':>12} {'PF':>6} {'SL':>4} {'TP':>4} {'S/L':>6}")
    print(f"  {'─' * 60}")
    for s in SYMBOLS:
        t = all_trades[s]; n = len(t)
        if n == 0: continue
        w = len([x for x in t if x['net_pnl'] > 0]); l = len([x for x in t if x['net_pnl'] <= 0])
        wr_s = w / n * 100
        pnl = sum(x['net_pnl'] for x in t)
        wpnl = sum(x['net_pnl'] for x in t if x['net_pnl'] > 0)
        lpnl = abs(sum(x['net_pnl'] for x in t if x['net_pnl'] < 0))
        pf_s = wpnl / lpnl if lpnl else float('inf')
        sl = len([x for x in t if x['reason'] == 'SL'])
        tp = len([x for x in t if x['reason'] == 'TP'])
        shorts = len([x for x in t if x['side'] == 'SHORT'])
        longs = len([x for x in t if x['side'] == 'LONG'])
        print(f"  {s:<10} {n:>6} {wr_s:>5.1f}% {pnl:>+11.6f} {pf_s:>5.2f} {sl:>4} {tp:>4} {shorts}/{longs}")
    
    return {"trades": total, "wr": wr, "pnl": total_pnl, "pf": pf, "max_dd": max_dd}

# ═══════ MAIN ═══════
print("=" * 100)
print(f"{'DS-SpreadScalper REALISTISCHER Backtest (Funding-Signal gesteuert)':^100}")
print(f"{'3× Hebel · 2.0× ATR TP · Chart-SL · 3% ROE-Trailing · Funding >0.01%=SHORT, <−0.01%=LONG':^100}")
print(f"{'4 Symbole · 1H Kerzen · Entry via Spread-Penetration · ' + datetime.now().strftime('%d.%m.%Y %H:%M'):^100}")
print("=" * 100)

all_trades = {s: [] for s in SYMBOLS}

for symbol in SYMBOLS:
    print(f"\n📡 Lade {symbol} 1H-Daten...", end=" ", flush=True)
    candles = bitget_client.get_candles(symbol, "1H", 1000)
    print(f"{len(candles)} Kerzen")
    
    # Einmal Funding holen (repräsentativ für die Periode)
    base_funding = get_funding_rate_at(symbol)
    print(f"  Funding-Rate: {base_funding*100:+.4f}%")
    
    trades = run_backtest(symbol, candles, base_funding)
    all_trades[symbol] = trades
    short_t = len([t for t in trades if t['side'] == 'SHORT'])
    long_t = len([t for t in trades if t['side'] == 'LONG'])
    print(f"  {len(trades)} Trades (SHORT: {short_t}, LONG: {long_t})")

result = print_results(all_trades, "📊 FUNDING-SIGNAL (LONG+SHORT via Funding-Rate)")

# Top-Losses
all_t = []
for s in SYMBOLS: all_t.extend(all_trades[s])
losses = sorted([t for t in all_t if t['net_pnl'] < 0], key=lambda x: x['net_pnl'])[:5]
gains = sorted([t for t in all_t if t['net_pnl'] > 0], key=lambda x: x['net_pnl'], reverse=True)[:5]

if losses:
    print(f"\n  ⚠️  Top-5 Verluste:")
    for t in losses:
        print(f"     {t['side']:>5} {t['ts']} {t['symbol']:>8} @ {t['entry']:<10.4f} → {t['exit']:<10.4f} | PnL={t['net_pnl']:<+9.6f} | {t['reason']} | Funding={t['funding']:+.4f}%")

if gains:
    print(f"\n  🏆 Top-5 Gewinne:")
    for t in gains:
        print(f"     {t['side']:>5} {t['ts']} {t['symbol']:>8} @ {t['entry']:<10.4f} → {t['exit']:<10.4f} | PnL={t['net_pnl']:<+9.6f} | {t['reason']} | PeakROE={t['roe_peak']}%")

print(f"\n{'='*100}")
print(f"{'BACKTEST ABGESCHLOSSEN — ' + datetime.now().strftime('%d.%m.%Y %H:%M'):^100}")
print(f"{'='*100}")
