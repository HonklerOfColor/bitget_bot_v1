"""
DS-SpreadScalper Backtest — SHORT-Only mit ECHTEN Marktdaten
===========================================================
Datenquelle: Bitget Public API (kein Demo/paptrading)
Strategie: SHORT-Only, SL=2% Entry, ROE-Trailing, 5× Hebel
"""
import requests, sys, random
from datetime import datetime

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
PRICE_PLACES = {"BTCUSDT": 1, "ETHUSDT": 1, "SOLUSDT": 3}
MIN_SIZES = {"BTCUSDT": 0.001, "ETHUSDT": 0.05, "SOLUSDT": 0.2}
LEVERAGE = 5
MAKER_FEE = 0.0002
TAKER_FEE = 0.0006

def fetch_candles(symbol, limit=1000):
    """Echte Marktdaten via öffentliche API (kein Demo!)"""
    url = f"https://api.bitget.com/api/v2/mix/market/candles?symbol={symbol}&productType=USDT-FUTURES&granularity=1H&limit={limit}"
    r = requests.get(url, timeout=15)
    data = r.json()
    if data.get("code") != "00000":
        print(f"  FEHLER {symbol}: {data.get('msg')}")
        return []
    return data["data"]

def calc_atr(candles, idx, period=14):
    if idx < period:
        return None
    trs = []
    for i in range(idx - period + 1, idx + 1):
        h = float(candles[i][2])
        l = float(candles[i][3])
        pc = float(candles[i-1][4])
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs) / len(trs)

def run_backtest(symbol, candles):
    trades = []
    position = None

    for i in range(1, len(candles)):
        ts = int(candles[i][0])
        dt = datetime.fromtimestamp(ts/1000)
        o = float(candles[i][1])
        h = float(candles[i][2])
        l = float(candles[i][3])
        c = float(candles[i][4])

        atr = calc_atr(candles, i)
        if atr is None:
            continue

        if position is None:
            entry = o
            size = MIN_SIZES.get(symbol, 0.1)
            notional = entry * size
            if notional < 4.95:
                continue

            # SL: 2% über Entry (Short)
            sl_price = round(entry * 1.02, PRICE_PLACES.get(symbol, 2))
            entry_fee = notional * MAKER_FEE
            margin = (size * entry) / LEVERAGE

            position = {
                "entry": entry, "size": size, "sl": sl_price,
                "entry_fee": entry_fee, "margin": margin,
                "peak_roe": -999.0, "ts": ts
            }
        else:
            entry = position["entry"]
            sl = position["sl"]
            size = position["size"]
            margin = position["margin"]

            # Short: schlechtester Preis = High
            pnl_check = (entry - h) * size
            roe_pct = pnl_check / margin * 100 if margin else 0

            # Peak-ROE (Close-basiert)
            close_pnl = (entry - c) * size
            close_roe = close_pnl / margin * 100 if margin else 0
            peak_roe = max(position["peak_roe"], close_roe)
            position["peak_roe"] = peak_roe

            # ROE-Trailing: ab 1% Peak, SL 2% unter Peak
            if peak_roe > 1.0:
                target_roe = peak_roe - 2.0
                new_sl = entry - (target_roe / 100 * margin) / size
                if new_sl < sl and new_sl > h:  # tighten + Sicherheit
                    sl = round(new_sl, PRICE_PLACES.get(symbol, 2))
                    position["sl"] = sl

            # SL-Check: Short SL getroffen wenn High >= SL
            exit_price = 0
            reason = ""
            if h >= sl:
                exit_price = sl
                reason = "SL"
            elif peak_roe > 1.0 and l <= sl:
                # Trailing-Exit: Preis fällt unter Trailing-SL
                exit_price = sl
                reason = "TP"

            if not exit_price:
                continue

            pnl = (entry - exit_price) * size
            exit_fee = exit_price * size * TAKER_FEE
            net_pnl = pnl - position["entry_fee"] - exit_fee

            trades.append({
                "ts": dt.strftime("%m-%d %H:%M"),
                "symbol": symbol,
                "entry": entry, "exit": exit_price,
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

def print_stats(label, trades, symbol=""):
    n = len(trades)
    if n == 0:
        print(f"  {label:<30} {'—':>6}")
        return
    wins = [t for t in trades if t["net_pnl"] > 0]
    losses = [t for t in trades if t["net_pnl"] <= 0]
    wr = len(wins) / n * 100
    total_pnl = sum(t["net_pnl"] for t in trades)
    total_fees = sum(t["fees"] for t in trades)
    gross = total_pnl + total_fees
    avg_win = sum(t["net_pnl"] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t["net_pnl"] for t in losses) / len(losses) if losses else 0
    max_win = max((t["net_pnl"] for t in trades), default=0)
    max_loss = min((t["net_pnl"] for t in trades), default=0)
    win_total = sum(t["net_pnl"] for t in wins)
    loss_total = abs(sum(t["net_pnl"] for t in losses))
    pf = win_total / loss_total if loss_total else float('inf')
    sl_hits = len([t for t in trades if t["reason"] == "SL"])
    tp_hits = len([t for t in trades if t["reason"] == "TP"])

    # Drawdown
    cum = peak = max_dd = consec = cur = 0
    for t in trades:
        cum += t["net_pnl"]
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)
        if t["net_pnl"] <= 0:
            cur += 1
            consec = max(consec, cur)
        else:
            cur = 0

    print(f"  {'─'*65}")
    print(f"  {label}")
    print(f"  {'─'*65}")
    print(f"  Trades:       {n:>5}    WR:       {wr:>5.1f}%  ({len(wins)}W/{len(losses)}L)")
    print(f"  Total PnL:    {total_pnl:>+10.4f} USDT  (brutto: {gross:.4f}, Fees: {total_fees:.4f})")
    print(f"  Profit F.:    {pf:>8.2f}    Ø PnL:   {total_pnl/n:>+8.4f}")
    print(f"  Ø Win:        {avg_win:>+8.4f}    Ø Loss: {avg_loss:>+8.4f}")
    print(f"  Max Win:      {max_win:>+8.4f}    Max L:  {max_loss:>+8.4f}")
    print(f"  SL-Hits:      {sl_hits:>4}    TP-Hits:{tp_hits:>4}")
    print(f"  Max DD:       {max_dd:>8.4f}    Max Serie: {consec}")

    if losses:
        print(f"  ⚠️  Top-3 Verluste:")
        for t in sorted(losses, key=lambda x: x["net_pnl"])[:3]:
            print(f"      {t['ts']} @ {t['entry']:<10.4f} → {t['exit']:<10.4f} | {t['net_pnl']:<+8.4f} | {t['reason']} (Sl={t['sl']})")
    if wins:
        print(f"  🏆 Top-3 Gewinne:")
        for t in sorted(wins, key=lambda x: x["net_pnl"], reverse=True)[:3]:
            print(f"      {t['ts']} @ {t['entry']:<10.4f} → {t['exit']:<10.4f} | {t['net_pnl']:<+8.4f} | {t['reason']} (PeakROE={t['roe_peak']}%)")


# ══════ MAIN ══════
print("=" * 120)
print(f"{'📊 SHORT-Only Backtest — ECHTE Marktdaten (kein Demo)':^120}")
print(f"{'SL=2% · ROE-Trailing · 5× Hebel · 1H Kerzen · ' + datetime.now().strftime('%d.%m.%Y %H:%M'):^120}")
print("=" * 120)

all_trades = []

for symbol in SYMBOLS:
    print(f"\n📡 Lade {symbol} echte Marktdaten...", end=" ")
    sys.stdout.flush()
    candles = fetch_candles(symbol, 1000)
    if not candles:
        continue
    print(f"{len(candles)} Kerzen geladen")
    print(f"    Von: {datetime.fromtimestamp(int(candles[-1][0])/1000).strftime('%d.%m.%Y %H:%M')}")
    print(f"    Bis: {datetime.fromtimestamp(int(candles[0][0])/1000).strftime('%d.%m.%Y %H:%M')}")
    print(f"    Range: {float(candles[-1][2]):.2f} – {float(candles[0][2]):.2f}")

    trades = run_backtest(symbol, candles)
    all_trades.extend(trades)

print(f"\n{'='*120}")
print_stats("GESAMT — SHORT-Only (echte Daten)", all_trades)

# Symbol-Vergleich
print(f"\n{'─'*120}")
print(f"{'SYMBOL-VERGLEICH':^120}")
print(f"{'─'*120}")
print(f"  {'Symbol':<10} {'Trades':>6} {'WR':>6} {'PnL':>12} {'PF':>6} {'SL':>4} {'TP':>4} {'Ø PnL':>9} {'MaxDD':>9}")
print(f"  {'─'*10} {'─'*6} {'─'*6} {'─'*12} {'─'*6} {'─'*4} {'─'*4} {'─'*9} {'─'*9}")
for symbol in SYMBOLS:
    t = [x for x in all_trades if x["symbol"] == symbol]
    n = len(t)
    if n == 0:
        continue
    w = len([x for x in t if x["net_pnl"] > 0])
    l = len([x for x in t if x["net_pnl"] <= 0])
    wr = w / n * 100
    pnl = sum(x["net_pnl"] for x in t)
    wpnl = sum(x["net_pnl"] for x in t if x["net_pnl"] > 0)
    lpnl = abs(sum(x["net_pnl"] for x in t if x["net_pnl"] < 0))
    pf = wpnl / lpnl if lpnl else float('inf')
    sl = len([x for x in t if x["reason"] == "SL"])
    tp = len([x for x in t if x["reason"] == "TP"])
    avg = pnl / n
    cum = peak = dd = 0
    for x in t:
        cum += x["net_pnl"]
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    print(f"  {symbol:<10} {n:>6} {wr:>5.1f}% {pnl:>+11.4f} {pf:>5.2f} {sl:>4} {tp:>4} {avg:>+8.4f} {dd:>8.2f}")

print(f"\n{'='*120}")
print(f"{'BACKTEST FERTIG — ' + datetime.now().strftime('%d.%m.%Y %H:%M'):^120}")
print(f"{'='*120}")
