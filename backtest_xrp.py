#!/usr/bin/env python3
"""XRPUSDT Backtest — Produktionsdaten via Bitget API, SHORT-Only Strategie"""
import json, math, random, subprocess, sys
from datetime import datetime, timezone

# ── XRP Konfiguration ──
SYMBOL = "XRPUSDT"
LEVERAGE = 5
MIN_SIZE = 20       # ~$22 Notional bei $1.10 → über $5 minTradeUSDT
PRICE_PLACES = 4

MAKER_FEE = 0.0002
TAKER_FEE = 0.0006
SHORT_ONLY = True    # Bot läuft SHORT_ONLY
SPREAD_PEN = 0.30    # LONG 30%, SHORT 70% — irrelevant da SHORT_ONLY

random.seed(42)

def fetch_candles(symbol, granularity="1H", limit=1000):
    """Fetch candles via curl (Production API, kein Demo)"""
    url = f"https://api.bitget.com/api/v2/mix/market/candles?symbol={symbol}&productType=USDT-FUTURES&granularity={granularity}&limit={limit}"
    raw = subprocess.check_output(["curl", "-s", url]).decode()
    data = json.loads(raw)
    if data.get("code") != "00000":
        raise Exception(f"API Error: {data}")
    return data["data"]  # [ts, o, h, l, c, vol, vol_quote]

def run_backtest(candles):
    trades = []
    position = None

    for i in range(1, len(candles)):
        ts = int(candles[i][0])
        dt = datetime.fromtimestamp(ts / 1000)
        o = float(candles[i][1])
        h = float(candles[i][2])
        l = float(candles[i][3])
        c = float(candles[i][4])

        if position is None:
            # ── SHORT ONLY (wie Bot) ──
            side = "short"
            entry = o
            size = MIN_SIZE
            notional = entry * size
            if notional < 4.95:
                continue

            # SL: 2% vom Entry (initialer Schutz-SL, wie Bot)
            sl_price = round(entry * 1.02, PRICE_PLACES)

            entry_fee = notional * MAKER_FEE
            margin = notional / LEVERAGE

            position = {
                "side": side, "entry": entry, "size": size,
                "sl": sl_price, "ts": ts,
                "entry_fee": entry_fee, "margin": margin,
                "peak_pnl_pct": -999.0,
                "trailing_activated": False,
            }
        else:
            side = position["side"]
            entry = position["entry"]
            sl = position["sl"]
            size = position["size"]
            margin = position["margin"]

            # Short: worst = high, best = low
            worst_price = h
            best_price = l
            pnl_check = (entry - worst_price) * size
            pnl_best = (entry - best_price) * size
            roe_pct = pnl_check / margin * 100 if margin else 0
            roe_best_pct = pnl_best / margin * 100 if margin else 0

            # Peak tracking auf Close
            close_pnl = (entry - c) * size
            close_roe = close_pnl / margin * 100 if margin else 0
            peak_roe = max(position.get("peak_pnl_pct", -999), close_roe)
            position["peak_pnl_pct"] = peak_roe

            # ROE-Trailing: wenn Peak > 2%, trailen mit 2% Abstand
            if peak_roe > 2.0:
                position["trailing_activated"] = True
                target_roe = peak_roe - 2.0
                # short: sl = entry - (target_roe/100 * margin) / size
                new_sl = entry - (target_roe / 100 * margin) / size
                # Nur tighten (enger setzen)
                if new_sl < sl:
                    sl = round(new_sl, PRICE_PLACES)

            # ── SL-Check ──
            hit_sl = False
            exit_price = 0
            reason = ""

            # Short: wenn high >= sl
            if h >= sl:
                hit_sl = True
                exit_price = sl
                reason = "SL"

            if hit_sl:
                pnl = (entry - exit_price) * size
                exit_fee = exit_price * size * TAKER_FEE
                net_pnl = pnl - position["entry_fee"] - exit_fee

                # ROE in %
                roe_pnl_pct = pnl / margin * 100 if margin else 0

                trades.append({
                    "ts": dt.strftime("%m-%d %H:%M"),
                    "side": side,
                    "entry": round(entry, PRICE_PLACES),
                    "exit": round(exit_price, PRICE_PLACES),
                    "pnl_usd": round(pnl, 4),
                    "fees": round(position["entry_fee"] + exit_fee, 4),
                    "net_pnl": round(net_pnl, 4),
                    "roe_pct": round(roe_pnl_pct, 2),
                    "reason": reason,
                    "sl": round(sl, PRICE_PLACES),
                    "peak_roe": round(peak_roe, 2),
                    "trailing": position["trailing_activated"],
                })
                position = None

    return trades

def print_results(symbol, trades):
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

    wpnl = sum(t["net_pnl"] for t in wins)
    lpnl_abs = abs(sum(t["net_pnl"] for t in losses)) if losses else 0
    pf = wpnl / lpnl_abs if lpnl_abs else float('inf')

    sl_trades = [t for t in trades if t["reason"] == "SL"]

    # Trailing aktivierte Trades
    trailing_on = [t for t in trades if t.get("trailing", False)]
    trailing_off = [t for t in trades if not t.get("trailing", False)]

    line = "─" * 90
    print(line)
    print(f"  {symbol} Backtest — SHORT-Only · SL=2% · 1$-Trailing · 1H-Daten")
    print(line)
    print(f"  Trades:          {n:>5}")
    print(f"  Winrate:         {wr:>5.1f}%  ({len(wins)}W/{len(losses)}L)")
    print(f"  Total PnL:       {total_pnl:>+8.4f} USDT  (brutto: {gross_pnl:.4f} Fees: {total_fees:.4f})")
    print(f"  Profit Factor:   {pf:>8.2f}")
    print(f"  Ø Win:           {avg_win:>+8.4f}   Ø Loss: {avg_loss:>+8.4f}")
    print(f"  Max Win:         {max_win:>+8.4f}   Max Loss: {max_loss:>+8.4f}")
    print(f"  SL-Hits:         {len(sl_trades):>4}")
    print(f"  Mit Trailing:    {len(trailing_on):>4} Trades ({len([t for t in trailing_on if t['net_pnl']>0])}W/{len([t for t in trailing_on if t['net_pnl']<=0])}L)")
    if trailing_on:
        trailing_pnl = sum(t["net_pnl"] for t in trailing_on)
        print(f"  Trailing PnL:    {trailing_pnl:>+8.4f}")
    print(f"  Ohne Trailing:   {len(trailing_off):>4} Trades ({len([t for t in trailing_off if t['net_pnl']>0])}W/{len([t for t in trailing_off if t['net_pnl']<=0])}L)")
    if trailing_off:
        notrail_pnl = sum(t["net_pnl"] for t in trailing_off)
        print(f"  OhneTrailingPnL: {notrail_pnl:>+8.4f}")

    # Drawdown
    cumulative = 0
    peak = 0
    max_dd = 0
    max_dd_pct = 0
    for t in trades:
        cumulative += t["net_pnl"]
        peak = max(peak, cumulative)
        dd = peak - cumulative
        dd_pct = dd / peak * 100 if peak > 0 else 0
        max_dd = max(max_dd, dd)
        max_dd_pct = max(max_dd_pct, dd_pct)

    print(f"  Peak PnL:        {peak:>+8.4f}")
    print(f"  Max Drawdown:    {max_dd:>8.4f} USDT ({max_dd_pct:.2f}%)")
    print(f"  Final Equity:    {cumulative:>+8.4f}")

    # Top Verluste
    if losses:
        print(f"\n  ⚠️  Top 5 Verluste:")
        for t in sorted(losses, key=lambda x: x["net_pnl"])[:5]:
            trailing_mark = "🔹" if t.get("trailing") else "  "
            print(f"    {trailing_mark} {t['ts']} @ {t['entry']:.4f} → {t['exit']:.4f} | "
                  f"PnL={t['net_pnl']:+7.4f} | ROE={t.get('roe_pct',0):+5.2f}% | {t['reason']} | "
                  f"Peak={t.get('peak_roe',0):.2f}%{' TRAILING' if t.get('trailing') else ''}")

    # Top Gewinne
    if wins:
        print(f"\n  🏆 Top 5 Gewinne:")
        for t in sorted(wins, key=lambda x: x["net_pnl"], reverse=True)[:5]:
            trailing_mark = "🔹" if t.get("trailing") else "  "
            print(f"    {trailing_mark} {t['ts']} @ {t['entry']:.4f} → {t['exit']:.4f} | "
                  f"PnL={t['net_pnl']:+7.4f} | ROE={t.get('roe_pct',0):+5.2f}% | {t['reason']} | "
                  f"Peak={t.get('peak_roe',0):.2f}%{' TRAILING' if t.get('trailing') else ''}")

    # Max consecutive losses
    max_consec = 0
    cur_consec = 0
    consec_loss_streaks = []
    for t in trades:
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

    print(f"\n  Max consecutive losses: {max_consec}")
    print(f"  Loss streaks:           {consec_loss_streaks}")
    print(line)

    # Top 10 Trades Detail
    print(f"\n  📋 Alle Trades (n={n}):")
    print(f"  {'#':>3} {'Zeit':<13} {'Entry':>8} {'Exit':>8} {'PnL':>8} {'ROE':>6} {'Peak':>6} {'Trail':>5} {'SL-Hit':>6}")
    print(f"  {'─'*3} {'─'*13} {'─'*8} {'─'*8} {'─'*8} {'─'*6} {'─'*6} {'─'*5} {'─'*6}")
    for idx, t in enumerate(trades, 1):
        print(f"  {idx:>3} {t['ts']:<13} {t['entry']:>8.4f} {t['exit']:>8.4f} "
              f"{t['net_pnl']:>+8.4f} {t.get('roe_pct',0):>+5.2f}% {t.get('peak_roe',0):>5.2f}% "
              f"{'✅' if t.get('trailing') else '❌':>5} {t['reason']:>6}")


# ══════════════════════════════════════
# MAIN
# ══════════════════════════════════════
print("=" * 90)
print(f"{'XRPUSDT Backtest — SHORT-Only · Bitget Produktionsdaten':^90}")
print(f"{datetime.now().strftime('%d.%m.%Y %H:%M')}".center(90))
print("=" * 90)

print(f"\n📡 Lade {SYMBOL} 1H-Daten (max 1000)...")
candles = fetch_candles(SYMBOL, "1H", 1000)
print(f"   ✅ {len(candles)} Kerzen geladen")
print(f"   🕐 {datetime.fromtimestamp(int(candles[0][0])/1000).strftime('%d.%m.%Y %H:%M')} → "
      f"{datetime.fromtimestamp(int(candles[-1][0])/1000).strftime('%d.%m.%Y %H:%M')}")

trades = run_backtest(candles)
print_results(SYMBOL, trades)

print(f"\n{'=' * 90}")
print(f"{'BACKTEST ABGESCHLOSSEN'.center(90)}")
print(f"{'=' * 90}")
