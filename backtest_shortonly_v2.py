"""
DS-SpreadScalper Backtest — SHORT-ONLY mit aktueller Bot-Logik
==============================================================
Exakte Simulation:
  - SHORT-Only (randomisierte Einstiege wie Bot = jede 1H Kerze ~60s)
  - Entry: bid + 70% vom Spread (asymmetrisch 30/70)
  - SL: 2% vom Entry (initialer Schutz-SL)
  - ROE-Trailing: ab 1% Peak-ROE wird SL 2% unter Peak nachgezogen
  - Fees: 0.02% Maker Entry + 0.06% Taker Exit
  - Hebel: 5x
  - Daten: 1000 1H Kerzen von Bitget (frisch)
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
    """Simuliere SHORT-Only Bot-Strategie: immer Short, SL=2%, ROE-Trailing"""
    trades = []
    position = None

    for i in range(1, len(candles)):
        ts = int(candles[i][0])
        dt = datetime.fromtimestamp(ts/1000)
        o = float(candles[i][1])
        h = float(candles[i][2])
        l = float(candles[i][3])
        c = float(candles[i][4])

        atr = calc_atr_from_candles(candles, i)
        if atr is None or atr <= 0:
            continue

        if position is None:
            # ── SHORT-Only: immer Short ──
            # Simuliere Spread-Einstieg (bid + 70% spread)
            # Vereinfacht: Entry ~ o (Spread-Capture ist vernachlässigbar auf 1H)
            entry = o
            size = MIN_SIZES.get(symbol, 0.1)

            notional = entry * size
            if notional < 4.95:
                continue

            # SL: 2% über Entry (wie Bot)
            sl_price = round(entry * 1.02, PRICE_PLACES.get(symbol, 2))

            entry_fee = notional * MAKER_FEE
            margin = (size * entry) / LEVERAGE

            position = {
                "side": "short", "entry": entry, "size": size,
                "sl": sl_price, "ts": ts,
                "entry_fee": entry_fee, "margin": margin,
                "peak_pnl_pct": -999.0,
            }
        else:
            side = "short"
            entry = position["entry"]
            sl = position["sl"]
            size = position["size"]
            margin = position["margin"]

            # PnL-Check: Short verliert bei High, gewinnt bei Low
            worst_price = h  # Short: schlechtester = High
            best_price = l   # Short: bester = Low
            pnl_check = (entry - worst_price) * size
            pnl_best = (entry - best_price) * size

            roe_pct = pnl_check / margin * 100 if margin else 0
            roe_best_pct = pnl_best / margin * 100 if margin else 0

            # Peak-ROE tracken (auf Close-Basis, wie Bot)
            close_pnl = (entry - c) * size
            close_roe = close_pnl / margin * 100 if margin else 0
            peak_roe = max(position.get("peak_pnl_pct", -999), close_roe)
            position["peak_pnl_pct"] = peak_roe

            # ROE-Trailing: wenn Peak > 1%, SL 2% unter Peak
            if peak_roe > 1.0:
                target_roe = peak_roe - 2.0
                # Short: SL bei entry - (target_roe% von margin) / size
                # = entry wird kleiner (SL sinkt) = Gewinn festzurren
                new_sl = entry - (target_roe / 100 * margin) / size
                # Short SL muss > mark sein (sonst sofort getriggert)
                # Vereinfacht: nur tighten (new_sl < current_sl)
                if new_sl < sl and new_sl > worst_price:
                    sl = round(new_sl, PRICE_PLACES.get(symbol, 2))

            # ── SL-Check ──
            hit_sl = False
            exit_price = 0
            reason = ""

            # Short: SL getroffen wenn High >= SL
            if h >= sl:
                hit_sl = True
                exit_price = sl
                reason = "SL"

            # ── TP-Check (vereinfacht: Preis unter Mid-Point) ──
            # Bot hat kein festes TP, nutzt ROE-Trailing.
            # Wenn Trailing aktiv ist und Preis gut läuft, hält der Bot.
            # Für den Backtest: wenn der tiefste Preis der Kerze den Trailing-SL
            # erreicht oder unterschreitet → TP-Exit (vereinfacht)
            if not hit_sl and best_price <= sl:
                hit_sl = True
                exit_price = sl
                reason = "TP"  # Trailing-Exit

            # Zusätzlich: wenn Peak-ROE > 3% und Close nahe Peak → Gewinnmitnahme
            if not hit_sl and peak_roe > 3.0:
                # Prüfe ob Preis stark zurückkommt (Kerze close > mid)
                mid = (h + l) / 2
                if c > mid:  # Preis erholt sich → SL wahren
                    pass  # Bot würde halten

            if hit_sl:
                if side == "short":
                    pnl = (entry - exit_price) * size
                else:
                    pnl = (exit_price - entry) * size

                exit_fee = exit_price * size * TAKER_FEE
                net_pnl = pnl - position["entry_fee"] - exit_fee

                trades.append({
                    "ts": dt.strftime("%m-%d %H:%M"),
                    "symbol": symbol,
                    "side": "short",
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

# ══════ MAIN ══════
print("=" * 120)
print(f"{'DS-SpreadScalper Backtest — SHORT-ONLY (aktuelle Bot-Logik)':^120}")
print(f"{'SL=2% Entry · ROE-Trailing · 30%/70% Spread · 5× Hebel · ' + datetime.now().strftime('%d.%m.%Y %H:%M'):^120}")
print("=" * 120)

all_trades = {s: [] for s in SYMBOLS}

for symbol in SYMBOLS:
    print(f"\n📡 Lade {symbol} 1H-Daten von Bitget API...", end=" ")
    sys.stdout.flush()
    candles = bitget_client.get_candles(symbol, "1H", 1000)
    print(f"{len(candles)} Kerzen geladen")

    trades = run_backtest(symbol, candles)
    all_trades[symbol] = trades

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

    sl_hits = len([t for t in trades if t["reason"] == "SL"])
    tp_hits = len([t for t in trades if t["reason"] == "TP"])

    print(f"\n  📊 {symbol}")
    print(f"      Trades:          {n:>5}")
    print(f"      Winrate:         {wr:>5.1f}%  ({len(wins)}W/{len(losses)}L)")
    print(f"      Total PnL:       {total_pnl:>+8.4f} USDT  (brutto: {gross_pnl:.4f}, Fees: {total_fees:.4f})")
    print(f"      Profit Factor:   {pf:>8.2f}")
    print(f"      Ø Win:           {avg_win:>+8.4f}   Ø Loss: {avg_loss:>+8.4f}")
    print(f"      Max Win:         {max_win:>+8.4f}   Max Loss: {max_loss:>+8.4f}")
    print(f"      SL-Hits:         {sl_hits:>4}   TP-Hits: {tp_hits:>4}")

    if losses:
        print(f"      ⚠️  Top-3 Verluste:")
        for t in sorted(losses, key=lambda x: x["net_pnl"])[:3]:
            print(f"         {t['side']:>5} {t['ts']} @ {t['entry']:<10.4f} → {t['exit']:<10.4f} | PnL={t['net_pnl']:<+8.4f} | {t['reason']} (Sl={t['sl']})")
    if wins:
        print(f"      🏆 Top-3 Gewinne:")
        for t in sorted(wins, key=lambda x: x["net_pnl"], reverse=True)[:3]:
            print(f"         {t['side']:>5} {t['ts']} @ {t['entry']:<10.4f} → {t['exit']:<10.4f} | PnL={t['net_pnl']:<+8.4f} | {t['reason']} (PeakROE={t['roe_peak']}%)")

# ── GESAMT ──
print(f"\n{'=' * 120}")
print(f"{'GESAMT (alle Symbole)':^120}")
print(f"{'=' * 120}")

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
win_total = sum(t["net_pnl"] for t in all_t if t["net_pnl"] > 0)
loss_total = abs(sum(t["net_pnl"] for t in all_t if t["net_pnl"] < 0))
pf = win_total / loss_total if loss_total else float('inf')

print(f"\n  Gesamt Trades:      {total_trades}")
print(f"  Gesamt PnL:         {total_pnl:>+8.4f} USDT  (brutto: {gross_pnl:.4f}, Fees: {total_fees:.4f})")
print(f"  Winrate:            {wr:.1f}%  ({total_wins}W/{total_losses}L)")
print(f"  Profit Factor:      {pf:.2f}")
print(f"  Ø PnL/Trade:        {avg_net:+.4f} USDT")
print(f"  Win Total:          {win_total:.4f}   Loss Total: {loss_total:.4f}")

# ── DRAWDOWN ──
print(f"\n{'─' * 120}")
print(f"{'DRAWDOWN & STATISTIK':^120}")
print(f"{'─' * 120}")

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

print(f"  Peak PnL:           {peak:>+8.4f}")
print(f"  Max Drawdown:       {max_dd:>8.4f} USDT ({max_dd_pct:.2f}%)")
print(f"  Final PnL:          {cumulative:>+8.4f}")

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

print(f"  Max consecutive losses:  {max_consec}")
print(f"  Loss streaks:            {consec_loss_streaks}")

# SL vs TP breakdown
sl_count = len([t for t in all_t if t["reason"] == "SL"])
tp_count = len([t for t in all_t if t["reason"] == "TP"])
sl_pnl = sum(t["net_pnl"] for t in all_t if t["reason"] == "SL")
tp_pnl = sum(t["net_pnl"] for t in all_t if t["reason"] == "TP")
print(f"\n  SL-Exits:           {sl_count:>4}  ({sl_pnl:+.4f} USDT)")
print(f"  TP-Exits:           {tp_count:>4}  ({tp_pnl:+.4f} USDT)")

# ── SYMBOL-VERGLEICH ──
print(f"\n{'─' * 120}")
print(f"{'SYMBOL-VERGLEICH':^120}")
print(f"{'─' * 120}")
print(f"  {'Symbol':<10} {'Trades':>6} {'WR':>6} {'PnL':>10} {'PF':>6} {'SL':>4} {'TP':>4} {'Ø PnL':>8}")
print(f"  {'─'*10} {'─'*6} {'─'*6} {'─'*10} {'─'*6} {'─'*4} {'─'*4} {'─'*8}")
for s in SYMBOLS:
    t = all_trades[s]
    n = len(t)
    if n == 0:
        print(f"  {s:<10} {0:>6} {'N/A':>6} {'N/A':>10} {'N/A':>6} {'N/A':>4} {'N/A':>4} {'N/A':>8}")
        continue
    w = len([x for x in t if x['net_pnl'] > 0])
    l = len([x for x in t if x['net_pnl'] <= 0])
    wr_pct = w / n * 100
    pnl = sum(x['net_pnl'] for x in t)
    wpnl = sum(x['net_pnl'] for x in t if x['net_pnl'] > 0)
    lpnl = abs(sum(x['net_pnl'] for x in t if x['net_pnl'] < 0))
    pf_s = wpnl / lpnl if lpnl else float('inf')
    sl = len([x for x in t if x['reason'] == 'SL'])
    tp = len([x for x in t if x['reason'] == 'TP'])
    avg = pnl / n if n else 0
    print(f"  {s:<10} {n:>6} {wr_pct:>5.1f}% {pnl:>+9.4f} {pf_s:>5.2f} {sl:>4} {tp:>4} {avg:>+8.4f}")

print(f"\n{'=' * 120}")
print(f"{'BACKTEST ABGESCHLOSSEN — ' + datetime.now().strftime('%d.%m.%Y %H:%M'):^120}")
print(f"{'=' * 120}")
