"""
10 Backtests — Parameter-Sweep SHORT-Only (echte Marktdaten)
============================================================
Variation: SL, Trailing-Schwelle, Trailing-Distanz, Richtung
"""
import requests, sys
from datetime import datetime

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
PRICE_PLACES = {"BTCUSDT": 1, "ETHUSDT": 1, "SOLUSDT": 3}
MIN_SIZES = {"BTCUSDT": 0.001, "ETHUSDT": 0.05, "SOLUSDT": 0.2}
LEVERAGE = 5
MAKER_FEE = 0.0002
TAKER_FEE = 0.0006

def fetch_candles(symbol, limit=1000):
    url = f"https://api.bitget.com/api/v2/mix/market/candles?symbol={symbol}&productType=USDT-FUTURES&granularity=1H&limit={limit}"
    r = requests.get(url, timeout=15)
    data = r.json()
    if data.get("code") != "00000": return []
    return data["data"]

def calc_atr(candles, idx, period=14):
    if idx < period: return None
    trs = []
    for i in range(idx - period + 1, idx + 1):
        h = float(candles[i][2]); l = float(candles[i][3]); pc = float(candles[i-1][4])
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs) / len(trs)

def run_backtest(symbol, candles, sl_mode="pct2", trail_trigger=1.0, trail_dist=2.0, short_only=True):
    """sl_mode: 'none','pct1','pct2','pct3','pct5','atr15'"""
    import random; random.seed(42)
    trades = []
    position = None

    for i in range(1, len(candles)):
        o = float(candles[i][1]); h = float(candles[i][2])
        l = float(candles[i][3]); c = float(candles[i][4])
        atr = calc_atr(candles, i)
        if atr is None: continue

        if position is None:
            if short_only:
                side = "short"
            else:
                side = random.choice(["long", "short"])

            entry = o
            size = MIN_SIZES.get(symbol, 0.1)
            notional = entry * size
            if notional < 4.95: continue

            if sl_mode == "none": sl_price = None
            elif sl_mode == "pct1": sl_price = round(entry * 1.01, PRICE_PLACES.get(symbol, 2)) if side=="short" else round(entry*0.99, PRICE_PLACES.get(symbol,2))
            elif sl_mode == "pct2": sl_price = round(entry * 1.02, PRICE_PLACES.get(symbol, 2)) if side=="short" else round(entry*0.98, PRICE_PLACES.get(symbol,2))
            elif sl_mode == "pct3": sl_price = round(entry * 1.03, PRICE_PLACES.get(symbol, 2)) if side=="short" else round(entry*0.97, PRICE_PLACES.get(symbol,2))
            elif sl_mode == "pct5": sl_price = round(entry * 1.05, PRICE_PLACES.get(symbol, 2)) if side=="short" else round(entry*0.95, PRICE_PLACES.get(symbol,2))
            elif sl_mode == "atr15": sl_price = round(entry + atr*1.5, PRICE_PLACES.get(symbol,2)) if side=="short" else round(entry-atr*1.5, PRICE_PLACES.get(symbol,2))
            else: sl_price = round(entry * 1.02, PRICE_PLACES.get(symbol, 2))

            entry_fee = notional * MAKER_FEE
            margin = (size * entry) / LEVERAGE
            position = {"entry": entry, "size": size, "sl": sl_price, "entry_fee": entry_fee,
                        "margin": margin, "peak_roe": -999.0, "start_idx": i, "side": side}
        else:
            entry = position["entry"]; sl = position["sl"]; size = position["size"]
            margin = position["margin"]; side = position["side"]

            if side == "short":
                worst = h; best = l
                pnl_check = (entry - worst) * size
            else:
                worst = l; best = h
                pnl_check = (entry - worst) * size

            roe_pct = pnl_check / margin * 100 if margin else 0
            close_pnl = (entry - c) * size if side == "short" else (c - entry) * size
            close_roe = close_pnl / margin * 100 if margin else 0
            peak_roe = max(position["peak_roe"], close_roe)
            position["peak_roe"] = peak_roe

            # ROE-Trailing
            if peak_roe > trail_trigger:
                target_roe = peak_roe - trail_dist
                if side == "short":
                    new_sl = entry - (target_roe / 100 * margin) / size
                    if sl is None:
                        if new_sl > worst:
                            sl = round(new_sl, PRICE_PLACES.get(symbol, 2))
                            position["sl"] = sl
                    elif new_sl < sl and new_sl > worst:
                        sl = round(new_sl, PRICE_PLACES.get(symbol, 2))
                        position["sl"] = sl
                else:
                    new_sl = entry + (target_roe / 100 * margin) / size
                    if sl is None:
                        if new_sl < worst:
                            sl = round(new_sl, PRICE_PLACES.get(symbol, 2))
                            position["sl"] = sl
                    elif new_sl > sl and new_sl < worst:
                        sl = round(new_sl, PRICE_PLACES.get(symbol, 2))
                        position["sl"] = sl

            # Exit-Check
            exit_price = 0; reason = ""
            timeout = 24
            if sl is not None:
                if (side == "short" and h >= sl) or (side == "long" and l <= sl):
                    exit_price = sl; reason = "SL"
                elif peak_roe > trail_trigger:
                    if (side == "short" and l <= sl) or (side == "long" and h >= sl):
                        exit_price = sl; reason = "TP"
            if not exit_price and (i - position["start_idx"]) > timeout:
                exit_price = c; reason = "TIMEOUT"
            if not exit_price: continue

            pnl = (entry - exit_price) * size if side == "short" else (exit_price - entry) * size
            exit_fee = exit_price * size * TAKER_FEE
            net_pnl = pnl - position["entry_fee"] - exit_fee
            trades.append({"net_pnl": round(net_pnl, 4), "reason": reason, "roe_peak": round(peak_roe, 2)})
            position = None

    return trades

# ══════ Konfigurationen ══════
configs = [
    ("01) Nur Trailing",       "none", 1.0, 2.0, True),
    ("02) SL=2% (Baseline)",   "pct2", 1.0, 2.0, True),
    ("03) Random + Nur Trail", "none", 1.0, 2.0, False),
    ("04) SL=5% (weit)",       "pct5", 1.0, 2.0, True),
    ("05) SL=1.5×ATR",         "atr15",1.0, 2.0, True),
    ("06) Trail früh 0.5/1%",  "none", 0.5, 1.0, True),
    ("07) Trail spät 3/3%",    "none", 3.0, 3.0, True),
    ("08) Trail eng 1/0.5%",   "none", 1.0, 0.5, True),
    ("09) Trail weit 1/4%",    "none", 1.0, 4.0, True),
    ("10) SL=3% + Trail 1/2%", "pct3", 1.0, 2.0, True),
]

# Daten laden
print("📡 Lade Daten...")
candles_cache = {}
for sym in SYMBOLS:
    candles_cache[sym] = fetch_candles(sym, 1000)
    print(f"  {sym}: {len(candles_cache[sym])} Kerzen")

# Alle Backtests laufen lassen
results = []
for label, sl_mode, trail_trig, trail_dist, short_only in configs:
    all_t = []
    for sym in SYMBOLS:
        t = run_backtest(sym, candles_cache[sym], sl_mode, trail_trig, trail_dist, short_only)
        all_t.extend(t)

    n = len(all_t)
    wins = [x for x in all_t if x["net_pnl"] > 0]
    losses = [x for x in all_t if x["net_pnl"] <= 0]
    wr = len(wins)/n*100 if n else 0
    total = sum(x["net_pnl"] for x in all_t)
    fees_est = abs(total) * 0.08 / 100  # Schätzung
    win_t = sum(x["net_pnl"] for x in wins)
    loss_t = abs(sum(x["net_pnl"] for x in losses))
    pf = win_t/loss_t if loss_t else 0
    max_win = max((x["net_pnl"] for x in all_t), default=0)
    max_loss = min((x["net_pnl"] for x in all_t), default=0)
    sl_hits = len([x for x in all_t if x["reason"] == "SL"])
    tp_hits = len([x for x in all_t if x["reason"] == "TP"])
    to_hits = len([x for x in all_t if x["reason"] == "TIMEOUT"])
    cum = peak = dd = consec = cur = 0
    for x in all_t:
        cum += x["net_pnl"]; peak = max(peak, cum)
        dd = max(dd, peak - cum)
        if x["net_pnl"] <= 0: cur += 1; consec = max(consec, cur)
        else: cur = 0

    avg_win = win_t/len(wins) if wins else 0
    avg_loss = loss_t/len(losses) if losses else 0

    results.append({"label": label, "n": n, "wr": wr, "pnl": total, "pf": pf,
                    "max_win": max_win, "max_loss": max_loss, "max_dd": dd,
                    "sl": sl_hits, "tp": tp_hits, "to": to_hits, "consec": consec,
                    "avg_win": avg_win, "avg_loss": avg_loss, "dir": "SHORT" if short_only else "RANDOM"})

# ══════ AUSGABE ══════
print("\n" + "="*140)
print(f"{'10 BACKTESTS — PARAMETER-SWEEP (ECHTE 1H-DATEN)':^140}")
print(f"{datetime.now().strftime('%d.%m.%Y %H:%M'):^140}")
print("="*140)

# Kopfzeile
print(f"  {'#':<2} {'Konfiguration':<28} {'Richtung':<8} {'Tr':>5} {'WR':>5} {'PnL':>10} {'PF':>5} {'ØWin':>7} {'ØLoss':>7} {'MaxW':>7} {'MaxL':>7} {'MaxDD':>8} {'SL':>3} {'TP':>3} {'TO':>3} {'Serie':>4}")
print(f"  {'─'*2} {'─'*28} {'─'*8} {'─'*5} {'─'*5} {'─'*10} {'─'*5} {'─'*7} {'─'*7} {'─'*7} {'─'*7} {'─'*8} {'─'*3} {'─'*3} {'─'*3} {'─'*4}")

for i, r in enumerate(results):
    marker = "✅" if r["pnl"] > 0 else "❌" if r["pnl"] < 0 else "⚪"
    print(f"  {i+1:<2} {r['label']:<28} {r['dir']:<8} {r['n']:>5} {r['wr']:>4.1f}% {r['pnl']:>+9.2f} {marker} {r['pf']:>4.2f} {r['avg_win']:>+7.4f} {r['avg_loss']:>+7.4f} {r['max_win']:>+7.4f} {r['max_loss']:>+7.4f} {r['max_dd']:>7.2f} {r['sl']:>3} {r['tp']:>3} {r['to']:>3} {r['consec']:>4}")

print("\n" + "─"*140)
print("LEGENDE:")
print("  Tr=Trades WR=Winrate PF=ProfitFactor ØWin/ØLoss=Ø PnL pro Gewinn/Verlust")
print("  MaxW/MaxL=Max Gewinn/Verlust MaxDD=Max Drawdown SL/TP/TO=Exit-Grund Serie=Max Verlustserie")
print("─"*140)

# Ranking
profitables = [r for r in results if r["pnl"] > 0]
negatives = [r for r in results if r["pnl"] <= 0]
print(f"\n🏆 PROFITABLE KONFIGURATIONEN ({len(profitables)}/{len(results)}):")
if profitables:
    for r in sorted(profitables, key=lambda x: x["pnl"], reverse=True):
        print(f"  {r['label']:<28} → PnL {r['pnl']:>+8.2f} | WR {r['wr']:>4.1f}% | PF {r['pf']:>4.2f} | DD {r['max_dd']:>7.2f}")
else:
    print("  Keine 😢")

print(f"\n❌ NICHT PROFITABEL ({len(negatives)}):")
for r in sorted(negatives, key=lambda x: x["pnl"], reverse=True):
    print(f"  {r['label']:<28} → PnL {r['pnl']:>+8.2f} | WR {r['wr']:>4.1f}% | PF {r['pf']:>4.2f}")

# Beste Einzelwerte
best_pnl = max(results, key=lambda r: r["pnl"])
best_wr = max(results, key=lambda r: r["wr"])
best_pf = max(results, key=lambda r: r["pf"])
lowest_dd = min(results, key=lambda r: r["max_dd"])

print(f"\n{'─'*140}")
print("KENNZAHLEN:")
print(f"  Best PnL: {best_pnl['label']} ({best_pnl['pnl']:+.2f} USDT)")
print(f"  Best WR:  {best_wr['label']} ({best_wr['wr']:.1f}%)")
print(f"  Best PF:  {best_pf['label']} ({best_pf['pf']:.2f})")
print(f"  Min DD:   {lowest_dd['label']} ({lowest_dd['max_dd']:.2f} USDT)")
print(f"{'='*140}")
