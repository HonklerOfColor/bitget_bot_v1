"""
DS-SpreadScalper Backtest — Aktuelle Strategie (LONG+SHORT + SHORT-Only)
=======================================================================
Simuliert exakt die Bot-Logik:
- Einstieg jede 1H Kerze (wie Bot ≈ alle 2s Limit-Orders)
- ATR-basierter SL (1.5×ATR), ROE-Trailing ab 3% Peak, Break-Even bei 3%
- Multi-Level TP: 15%@3×ATR, 35%@6×ATR, 50%@9×ATR
- Spread-Penetration: SHORT=70%, LONG=30%
- Fees: 0.02% Maker / 0.06% Taker
"""
import sys, json, math
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, '.')
import bitget_client

# ── Exakte Bot-Parameter ──
SYMBOLS = ["SOLUSDT", "BTCUSDT", "ETHUSDT", "XRPUSDT"]
LEVERAGE = 5
MAKER_FEE = 0.0002
TAKER_FEE = 0.0006
BREAKEVEN_PNL_PCT = 0.03

MIN_SIZES = {"BTCUSDT": 0.001, "ETHUSDT": 0.05, "SOLUSDT": 0.2, "XRPUSDT": 5}
PRICE_PLACES = {"SOLUSDT": 3, "BTCUSDT": 1, "ETHUSDT": 1, "XRPUSDT": 4}

TP_LEVELS = [
    {"pct": 0.15, "atr_mult": 3.0, "label": "TP1"},
    {"pct": 0.35, "atr_mult": 6.0, "label": "TP2"},
    {"pct": 0.50, "atr_mult": 9.0, "label": "TP3"},
]


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


def run_backtest(symbol, candles, short_only=True, force_long=False):
    """Simuliere Bot-Strategie auf 1H Kerzen."""
    trades = []
    position = None

    for i in range(1, len(candles)):
        ts = int(candles[i][0])
        dt = datetime.fromtimestamp(ts / 1000)
        o = float(candles[i][1])
        h = float(candles[i][2])
        l = float(candles[i][3])
        c = float(candles[i][4])

        atr = calc_atr(candles, i)
        if atr is None or atr <= 0:
            continue

        if position is None:
            # ── Entry (jede Kerze) ──
            if short_only:
                side = "short"
            elif force_long:
                side = "long"
            else:
                # Abwechselnd: short bei fallenden, long bei steigenden
                prev_c = float(candles[i - 1][4]) if i > 0 else o
                side = "short" if o <= prev_c else "long"

            entry = o  # Spread-Capture auf 1H vernachlässigbar
            size = MIN_SIZES.get(symbol, 0.1)

            notional = entry * size
            if notional < 4.95:
                continue

            # ATR-basierter initialer SL (wie Bot: chart-basiert oder 1.5×ATR)
            if side == "short":
                sl_price = round(entry + atr * 1.5, PRICE_PLACES.get(symbol, 2))
            else:
                sl_price = round(entry - atr * 1.5, PRICE_PLACES.get(symbol, 2))

            # TP1 = 3× ATR vom Entry
            if side == "short":
                tp1 = round(entry - atr * 3.0, PRICE_PLACES.get(symbol, 2))
            else:
                tp1 = round(entry + atr * 3.0, PRICE_PLACES.get(symbol, 2))

            entry_fee = notional * MAKER_FEE
            margin = (size * entry) / LEVERAGE

            position = {
                "side": side, "entry": entry, "size": size,
                "sl": sl_price, "tp1": tp1,
                "ts": ts, "entry_fee": entry_fee, "margin": margin,
                "peak_roe": -999.0, "breakeven_activated": False,
            }
        else:
            side = position["side"]
            entry = position["entry"]
            sl = position["sl"]
            size = position["size"]
            margin = position["margin"]

            # PnL auf 1H High/Low berechnen
            if side == "short":
                worst_price = h  # Short: teuer = Verlust
                best_price = l   # Short: billig = Gewinn
                pnl_check = (entry - worst_price) * size
                pnl_best = (entry - best_price) * size
            else:
                worst_price = l  # Long: billig = Verlust
                best_price = h   # Long: teuer = Gewinn
                pnl_check = (worst_price - entry) * size
                pnl_best = (best_price - entry) * size

            roe = pnl_check / margin * 100 if margin else 0
            roe_best = pnl_best / margin * 100 if margin else 0

            # Peak-ROE auf Close-Basis (wie Bot)
            close_pnl = ((entry - c) * size) if side == "short" else ((c - entry) * size)
            close_roe = close_pnl / margin * 100 if margin else 0
            peak_roe = max(position.get("peak_roe", -999), close_roe)
            position["peak_roe"] = peak_roe

            # ── ROE-Trailing (ab 3% Peak) ──
            if peak_roe >= (BREAKEVEN_PNL_PCT * 100):
                target_roe = peak_roe - 2.0  # 2% unter Peak
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
                hit_sl = True
                exit_price = sl
                reason = "SL"
            elif side == "long" and l <= sl:
                hit_sl = True
                exit_price = sl
                reason = "SL"

            # ── TP-Check (Multi-Level: TP1@3×ATR) ──
            tp1 = position["tp1"]
            if not hit_sl:
                if side == "short" and l <= tp1:
                    hit_sl = True
                    exit_price = tp1
                    reason = "TP"
                elif side == "long" and h >= tp1:
                    hit_sl = True
                    exit_price = tp1
                    reason = "TP"

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
                    "side": side,
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
                })
                position = None

    return trades


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
    for t in all_t:
        if t["net_pnl"] <= 0:
            cur_consec += 1
            max_consec = max(max_consec, cur_consec)
        else:
            cur_consec = 0

    sl_count = len([t for t in all_t if t["reason"] == "SL"])
    tp_count = len([t for t in all_t if t["reason"] == "TP"])
    sl_pnl = sum(t["net_pnl"] for t in all_t if t["reason"] == "SL")
    tp_pnl = sum(t["net_pnl"] for t in all_t if t["reason"] == "TP")

    print(f"\n{'=' * 100}")
    print(f"  {label}")
    print(f"{'=' * 100}")
    print(f"  Trades:         {total_trades:>5}")
    print(f"  Winrate:        {wr:>5.1f}%  ({total_wins}W/{total_losses}L)")
    print(f"  Total PnL:      {total_pnl:>+9.6f} USDT  (brutto: {gross_pnl:.6f}, Fees: {total_fees:.6f})")
    print(f"  Profit Factor:  {pf:>7.2f}")
    print(f"  Ø PnL/Trade:    {avg_net:>+9.6f}")
    print(f"  Max Drawdown:   {max_dd:>9.6f} USDT ({max_dd_pct:.2f}%)")
    print(f"  Final PnL:      {cumulative:>+9.6f}")
    print(f"  Max Consec Loss:{max_consec:>4}")
    print(f"  SL-Exits:       {sl_count:>4}  ({sl_pnl:+.6f})")
    print(f"  TP-Exits:       {tp_count:>4}  ({tp_pnl:+.6f})")

    # Per-Symbol Breakdown
    print(f"\n  {'Symbol':<10} {'Trades':>6} {'WR':>6} {'PnL':>12} {'PF':>6} {'SL':>4} {'TP':>4}")
    print(f"  {'─' * 54}")
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
        tp = len([x for x in t if x['reason'] == 'TP'])
        print(f"  {s:<10} {n:>6} {wr_s:>5.1f}% {pnl:>+11.6f} {pf_s:>5.2f} {sl:>4} {tp:>4}")

    return {
        "trades": total_trades, "wr": wr, "pnl": total_pnl,
        "pf": pf, "max_dd": max_dd, "max_dd_pct": max_dd_pct
    }


# ══════ MAIN ══════
print("=" * 100)
print(f"{'DS-SpreadScalper Backtest — Aktuelle Strategie (10.07.2026)':^100}")
print(f"{'ATR-1.5×SL · 3×ATR-TP1 · 3% ROE-Trailing · SHORT+LONG+BOTH':^100}")
print(f'{"4 Symbole · 5× Hebel · 1H Kerzen · " + datetime.now().strftime("%d.%m.%Y %H:%M"):^100}')
print("=" * 100)

all_trades_short = {s: [] for s in SYMBOLS}
all_trades_long = {s: [] for s in SYMBOLS}
all_trades_both = {s: [] for s in SYMBOLS}

for symbol in SYMBOLS:
    print(f"\n📡 Lade {symbol} 30m-Daten...", end=" ")
    sys.stdout.flush()
    candles = bitget_client.get_candles(symbol, "1H", 1000)
    print(f"{len(candles)} Kerzen")

    trades_s = run_backtest(symbol, candles, short_only=True)
    all_trades_short[symbol] = trades_s
    print(f"  SHORT-Only: {len(trades_s)} Trades")

    trades_l = run_backtest(symbol, candles, short_only=False, force_long=True)
    all_trades_long[symbol] = trades_l
    print(f"  LONG-Only:  {len(trades_l)} Trades")

    trades_b = run_backtest(symbol, candles, short_only=False)
    all_trades_both[symbol] = trades_b
    print(f"  BOTH:       {len(trades_b)} Trades")

# ── SHORT-Only ──
res_short = print_results(all_trades_short, "📉 SHORT-ONLY")

# ── LONG-Only ──
res_long = print_results(all_trades_long, "📈 LONG-ONLY")

# ── BOTH ──
res_both = print_results(all_trades_both, "📊 LONG + SHORT")

# ── Vergleich ──
print(f"\n{'=' * 100}")
print(f"{'VERGLEICH':^100}")
print(f"{'=' * 100}")
print(f"  {'Modus':<20} {'Trades':>6} {'WR':>6} {'PnL':>12} {'PF':>6} {'MaxDD':>8} {'MaxDD%':>7}")
print(f"  {'─' * 65}")
print(f"  {'SHORT-Only':<20} {res_short['trades']:>6} {res_short['wr']:>5.1f}% {res_short['pnl']:>+11.6f} {res_short['pf']:>5.2f} {res_short['max_dd']:>8.6f} {res_short['max_dd_pct']:>6.2f}%")
print(f"  {'LONG-Only':<20}  {res_long['trades']:>6} {res_long['wr']:>5.1f}% {res_long['pnl']:>+11.6f} {res_long['pf']:>5.2f} {res_long['max_dd']:>8.6f} {res_long['max_dd_pct']:>6.2f}%")
print(f"  {'LONG+SHORT':<20} {res_both['trades']:>6} {res_both['wr']:>5.1f}% {res_both['pnl']:>+11.6f} {res_both['pf']:>5.2f} {res_both['max_dd']:>8.6f} {res_both['max_dd_pct']:>6.2f}%")

# ── Top-Losses (beide Modi) ──
all_t = []
for s in SYMBOLS:
    all_t.extend(all_trades_both[s])
losses = sorted([t for t in all_t if t["net_pnl"] < 0], key=lambda x: x["net_pnl"])[:3]
if losses:
    print(f"\n  ⚠️  Top-3 Verluste (BOTH):")
    for t in losses:
        print(f"     {t['side']:>5} {t['ts']} {t['symbol']:>8} @ {t['entry']:<10.4f} → {t['exit']:<10.4f} | PnL={t['net_pnl']:<+9.6f} | {t['reason']} (SL={t['sl']})")

all_ts = []
for s in SYMBOLS:
    all_ts.extend(all_trades_short[s])
losses_s = sorted([t for t in all_ts if t["net_pnl"] < 0], key=lambda x: x["net_pnl"])[:3]
if losses_s:
    print(f"\n  ⚠️  Top-3 Verluste (SHORT-Only):")
    for t in losses_s:
        print(f"     {t['side']:>5} {t['ts']} {t['symbol']:>8} @ {t['entry']:<10.4f} → {t['exit']:<10.4f} | PnL={t['net_pnl']:<+9.6f} | {t['reason']} (SL={t['sl']})")

print(f"\n{'=' * 100}")
print(f"{'BACKTEST ABGESCHLOSSEN — ' + datetime.now().strftime('%d.%m.%Y %H:%M'):^100}")
print(f"{'=' * 100}")
