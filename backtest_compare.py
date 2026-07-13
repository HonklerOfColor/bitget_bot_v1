#!/usr/bin/env python3
"""Multi-Variant Backtest — Spread-Scalper: 10 Strategie-Varianten auf gleichen Daten."""

import sys, json, math
from datetime import datetime, timezone
from collections import defaultdict

# ── Konstanten (Basis) ──
SYMBOLS = ["SOLUSDT", "BTCUSDT", "ETHUSDT", "XRPUSDT"]
LEVERAGE = 5
MAKER_FEE = 0.0002
TAKER_FEE = 0.0006
BREAKEVEN_PNL_PCT = 0.03

MIN_SIZES = {"BTCUSDT": 0.001, "ETHUSDT": 0.05, "SOLUSDT": 0.2, "XRPUSDT": 5}
PRICE_PLACES = {"SOLUSDT": 3, "BTCUSDT": 1, "ETHUSDT": 1, "XRPUSDT": 4}


def fetch_binance_klines(symbol, interval="1h", limit=1000, start_time=None):
    """Fetch klines from Binance public API."""
    import requests, time
    url = "https://api.binance.com/api/v3/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    if start_time:
        params["startTime"] = start_time
    for attempt in range(3):
        try:
            r = requests.get(url, params=params, timeout=15)
            if r.status_code == 429:
                time.sleep(3)
                continue
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt == 2:
                raise
            time.sleep(2)
    return []


def fetch_year_candles_binance(symbol):
    """1 Jahr 1H-Kerzen von Binance in Batches."""
    all_candles = []
    seen = set()
    end_ts = None
    now = datetime.now(timezone.utc)
    end_ts = int(now.timestamp() * 1000)
    start_ts = end_ts - 365 * 24 * 3600 * 1000  # 1 Jahr zurück
    print(f"    Start: {datetime.fromtimestamp(start_ts/1000).strftime('%Y-%m-%d')}", end="")

    while len(all_candles) < 8784:
        batch = fetch_binance_klines(symbol, "1h", limit=1000, start_time=start_ts + len(all_candles) * 3600000)
        if not batch:
            break
        new_count = 0
        for c in batch:
            t = c[0]
            if t not in seen:
                seen.add(t)
                all_candles.append(c)
                new_count += 1
        if new_count == 0 or len(batch) < 1000:
            break
    # sort by timestamp
    all_candles.sort(key=lambda x: x[0])
    print(f"  → {len(all_candles)} Kerzen (bis {datetime.fromtimestamp(all_candles[-1][0]/1000).strftime('%Y-%m-%d')})")
    return all_candles


def calc_atr(candles, idx, period=14):
    if idx < period:
        return None
    tr_sum = 0.0
    for i in range(idx - period + 1, idx + 1):
        h = float(candles[i][2])
        l = float(candles[i][3])
        pc = float(candles[i - 1][4])
        tr = max(h - l, abs(h - pc), abs(l - pc))
        tr_sum += tr
    return tr_sum / period


def calc_chart_sl(candles, idx, entry, side, price_places, lookback=20, atr_min_mult=0.5, atr_fallback_mult=1.5):
    atr = calc_atr(candles, idx)
    sub = candles[max(0, idx - lookback):idx]
    if side == "short":
        highs = [float(k[2]) for k in sub]
        if not highs:
            return round(entry + atr * atr_fallback_mult, price_places) if atr else None
        highest = max(highs)
        sl = round(highest * 1.001, price_places)
        if atr and sl < entry + atr * atr_min_mult:
            sl = round(entry + atr * atr_min_mult, price_places)
        return sl
    else:
        lows = [float(k[3]) for k in sub]
        if not lows:
            return round(entry - atr * atr_fallback_mult, price_places) if atr else None
        lowest = min(lows)
        sl = round(lowest * 0.999, price_places)
        if atr and sl > entry - atr * atr_min_mult:
            sl = round(entry - atr * atr_min_mult, price_places)
        return sl


def run_backtest_variant(candles, symbol, params):
    """
    params dict:
      - spread_long: 0.0-1.0 (Anteil LONG)
      - sl_lookback: int (Chart-SL Lookback, default 20)
      - sl_atr_min: float (min ATR multiple für SL, default 0.5)
      - sl_atr_fallback: float (fallback ATR mult, default 1.5)
      - tp_mults: list of ATR multipliers for TP levels
      - tp_pcts: list of portion percentages
      - roe_trail_trigger: float (Peak ROE % zum Trailing start, default 3.0)
      - roe_trail_guard: float (Guard unter Peak %, default 2.0)
      - use_trailing: bool
    """
    tp_mults = params.get("tp_mults", [3.0, 6.0, 9.0])
    tp_pcts = params.get("tp_pcts", [0.15, 0.35, 0.50])
    spread_long = params.get("spread_long", 0.3)
    sl_lookback = params.get("sl_lookback", 20)
    sl_atr_min = params.get("sl_atr_min", 0.5)
    sl_atr_fallback = params.get("sl_atr_fallback", 1.5)
    roe_trail_trigger = params.get("roe_trail_trigger", 3.0)
    roe_trail_guard = params.get("roe_trail_guard", 2.0)
    use_trailing = params.get("use_trailing", True)

    trades = []
    position = None
    entry_count = 0
    price_places = PRICE_PLACES.get(symbol, 2)

    for i in range(1, len(candles)):
        ts = int(candles[i][0])
        dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
        o = float(candles[i][1])
        h = float(candles[i][2])
        l = float(candles[i][3])
        c = float(candles[i][4])

        atr = calc_atr(candles, i)
        if atr is None or atr <= 0:
            continue

        if position is None:
            entry_count += 1
            is_long = (entry_count % 100) / 100.0 < spread_long
            side = "long" if is_long else "short"
            entry = o
            size = MIN_SIZES.get(symbol, 0.1)
            notional = entry * size
            if notional < 4.95:
                continue

            sl_price = calc_chart_sl(candles, i, entry, side, price_places,
                                     lookback=sl_lookback, atr_min_mult=sl_atr_min,
                                     atr_fallback_mult=sl_atr_fallback)
            if sl_price is None:
                if side == "short":
                    sl_price = round(entry + atr * sl_atr_fallback, price_places)
                else:
                    sl_price = round(entry - atr * sl_atr_fallback, price_places)

            tp_prices = []
            for mult in tp_mults:
                if side == "short":
                    tp = round(entry - atr * mult, price_places)
                else:
                    tp = round(entry + atr * mult, price_places)
                tp_prices.append(tp)

            entry_fee = notional * MAKER_FEE
            margin = (size * entry) / LEVERAGE

            position = {
                "side": side, "entry": entry, "size": size, "sl": sl_price,
                "tp_prices": tp_prices, "tp_level": 0, "ts": ts,
                "entry_fee": entry_fee, "margin": margin,
                "peak_roe": -999.0, "breakeven_activated": False,
            }
        else:
            side, entry, sl = position["side"], position["entry"], position["sl"]
            size, margin = position["size"], position["margin"]
            tp_prices, tp_level = position["tp_prices"], position["tp_level"]

            if side == "short":
                worst_price = h
                best_price = l
                pnl_check = (entry - worst_price) * size
                close_pnl = (entry - c) * size
            else:
                worst_price = l
                best_price = h
                pnl_check = (worst_price - entry) * size
                close_pnl = (c - entry) * size

            roe_pct = pnl_check / margin * 100 if margin else 0
            close_roe = close_pnl / margin * 100 if margin else 0
            peak_roe = max(position.get("peak_roe", -999), close_roe)
            position["peak_roe"] = peak_roe

            # ROE Trailing
            if use_trailing and peak_roe >= (roe_trail_trigger * 100):
                target_roe = peak_roe - roe_trail_guard * 100
                pnl_target = target_roe / 100 * margin
                if side == "short":
                    new_sl = round(entry - pnl_target / size, price_places)
                    if new_sl < sl and new_sl > worst_price:
                        sl = new_sl
                        position["sl"] = sl
                else:
                    new_sl = round(entry + pnl_target / size, price_places)
                    if new_sl > sl and new_sl < worst_price:
                        sl = new_sl
                        position["sl"] = sl

            # TP Check
            hit_tp = False
            exit_tp_price = 0
            tp_pnl = 0.0
            tp_reason = ""
            tp_portion = 0.0

            for lvl_idx in range(tp_level, len(tp_mults)):
                tp = tp_prices[lvl_idx]
                if (side == "short" and l <= tp) or (side == "long" and h >= tp):
                    portion = tp_pcts[lvl_idx]
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

            # SL Check
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

            # Trade close
            if hit_sl or hit_tp:
                net_pnl = tp_pnl + sl_loss
                exit_fee = (exit_sl_price * size * TAKER_FEE if hit_sl
                            else exit_tp_price * size * tp_portion * TAKER_FEE if tp_portion > 0
                            else 0)
                net_pnl -= position["entry_fee"] + exit_fee
                reason = tp_reason if hit_tp else "SL"

                trades.append({
                    "ts": dt.strftime("%Y-%m-%d %H:%M"),
                    "symbol": symbol, "side": side,
                    "entry": entry, "exit": exit_sl_price if hit_sl else exit_tp_price,
                    "pnl": round(net_pnl, 4),
                    "net_pnl": round(net_pnl, 4),
                    "reason": reason, "atr": round(atr, 4),
                    "sl": sl, "roe_peak": round(peak_roe, 2),
                    "tp_reached": tp_portion,
                })
                position = None

    return trades


def analyze_trades(trades):
    total = len(trades)
    if total == 0:
        return {"trades": 0}

    wins = [t for t in trades if t["net_pnl"] > 0]
    losses = [t for t in trades if t["net_pnl"] <= 0]
    sls = [t for t in trades if t["reason"] == "SL"]
    tps = [t for t in trades if t["reason"] != "SL"]

    total_pnl = sum(t["net_pnl"] for t in trades)
    wr = len(wins) / total * 100 if total else 0
    win_pnl = sum(t["net_pnl"] for t in wins)
    loss_pnl = abs(sum(t["net_pnl"] for t in losses))
    pf = win_pnl / loss_pnl if loss_pnl else float('inf')

    # Max DD
    cumulative, peak, max_dd = 0, 0, 0
    for t in trades:
        cumulative += t["net_pnl"]
        peak = max(peak, cumulative)
        max_dd = max(max_dd, peak - cumulative)

    # Consec losses
    max_consec = cur = 0
    for t in trades:
        if t["net_pnl"] <= 0:
            cur += 1
            max_consec = max(max_consec, cur)
        else:
            cur = 0

    avg_win = win_pnl / len(wins) if wins else 0
    avg_loss = loss_pnl / len(losses) if losses else 0

    sl_profitable = len([t for t in sls if t["net_pnl"] > 0])
    sl_profitable_pnl = sum(t["net_pnl"] for t in sls if t["net_pnl"] > 0)
    sl_loss_pnl = sum(t["net_pnl"] for t in sls if t["net_pnl"] <= 0)

    return {
        "trades": total, "wr": round(wr, 1),
        "pnl": round(total_pnl, 2),
        "pf": round(pf, 2),
        "max_dd": round(max_dd, 2),
        "max_consec": max_consec,
        "avg_win": round(avg_win, 4),
        "avg_loss": round(avg_loss, 4),
        "sl_pct": round(len(sls) / total * 100, 1) if total else 0,
        "tp_pct": round(len(tps) / total * 100, 1) if total else 0,
        "sl_profitable": sl_profitable,
        "sl_profitable_pnl": round(sl_profitable_pnl, 2),
        "sl_loss_pnl": round(sl_loss_pnl, 2),
        "sl_pnl_total": round(sum(t["net_pnl"] for t in sls), 2),
        "tp_pnl_total": round(sum(t["net_pnl"] for t in tps), 2),
    }


# ═══════════════════════════════════════════
# VARIANTS
# ═══════════════════════════════════════════
VARIANTS = [
    {
        "name": "1) Baseline (30/70)",
        "desc": "Aktuelle Strategie: 30%L/70%S, Chart-SL(20), TP 3/6/9×ATR, ROE Trail 3%/2%",
        "params": {"spread_long": 0.3, "sl_lookback": 20, "sl_atr_min": 0.5, "sl_atr_fallback": 1.5,
                    "tp_mults": [3.0, 6.0, 9.0], "tp_pcts": [0.15, 0.35, 0.50],
                    "roe_trail_trigger": 0.03, "roe_trail_guard": 0.02, "use_trailing": True},
    },
    {
        "name": "2) SHORT-Only",
        "desc": "Nur SHORT, sonst identisch zu Baseline",
        "params": {"spread_long": 0.0, "sl_lookback": 20, "sl_atr_min": 0.5, "sl_atr_fallback": 1.5,
                    "tp_mults": [3.0, 6.0, 9.0], "tp_pcts": [0.15, 0.35, 0.50],
                    "roe_trail_trigger": 0.03, "roe_trail_guard": 0.02, "use_trailing": True},
    },
    {
        "name": "3) Enger SL (Lookback 10)",
        "desc": "Chart-SL nur 10 Kerzen, sonst Baseline",
        "params": {"spread_long": 0.3, "sl_lookback": 10, "sl_atr_min": 0.3, "sl_atr_fallback": 1.0,
                    "tp_mults": [3.0, 6.0, 9.0], "tp_pcts": [0.15, 0.35, 0.50],
                    "roe_trail_trigger": 0.03, "roe_trail_guard": 0.02, "use_trailing": True},
    },
    {
        "name": "4) Weiter SL (Lookback 30, 2.5×)",
        "desc": "Chart-SL 30 Kerzen, fallback 2.5×ATR, sonst Baseline",
        "params": {"spread_long": 0.3, "sl_lookback": 30, "sl_atr_min": 0.5, "sl_atr_fallback": 2.5,
                    "tp_mults": [3.0, 6.0, 9.0], "tp_pcts": [0.15, 0.35, 0.50],
                    "roe_trail_trigger": 0.03, "roe_trail_guard": 0.02, "use_trailing": True},
    },
    {
        "name": "5) Höhere TP (5/10/15×ATR)",
        "desc": "TP bei 5/10/15×ATR statt 3/6/9×, sonst Baseline",
        "params": {"spread_long": 0.3, "sl_lookback": 20, "sl_atr_min": 0.5, "sl_atr_fallback": 1.5,
                    "tp_mults": [5.0, 10.0, 15.0], "tp_pcts": [0.15, 0.35, 0.50],
                    "roe_trail_trigger": 0.03, "roe_trail_guard": 0.02, "use_trailing": True},
    },
    {
        "name": "6) Tiefere TP (2/4/6×ATR)",
        "desc": "TP bei 2/4/6×ATR, sonst Baseline",
        "params": {"spread_long": 0.3, "sl_lookback": 20, "sl_atr_min": 0.5, "sl_atr_fallback": 1.5,
                    "tp_mults": [2.0, 4.0, 6.0], "tp_pcts": [0.15, 0.35, 0.50],
                    "roe_trail_trigger": 0.03, "roe_trail_guard": 0.02, "use_trailing": True},
    },
    {
        "name": "7) Kein Trailing",
        "desc": "Fester SL, kein ROE Trailing, sonst Baseline",
        "params": {"spread_long": 0.3, "sl_lookback": 20, "sl_atr_min": 0.5, "sl_atr_fallback": 1.5,
                    "tp_mults": [3.0, 6.0, 9.0], "tp_pcts": [0.15, 0.35, 0.50],
                    "roe_trail_trigger": 0.03, "roe_trail_guard": 0.02, "use_trailing": False},
    },
    {
        "name": "8) Aggressives Trailing",
        "desc": "Trail ab 1% Peak mit 1% Guard, sonst Baseline",
        "params": {"spread_long": 0.3, "sl_lookback": 20, "sl_atr_min": 0.5, "sl_atr_fallback": 1.5,
                    "tp_mults": [3.0, 6.0, 9.0], "tp_pcts": [0.15, 0.35, 0.50],
                    "roe_trail_trigger": 0.01, "roe_trail_guard": 0.01, "use_trailing": True},
    },
    {
        "name": "9) 50/50 LONG/SHORT",
        "desc": "50% LONG / 50% SHORT, sonst Baseline",
        "params": {"spread_long": 0.5, "sl_lookback": 20, "sl_atr_min": 0.5, "sl_atr_fallback": 1.5,
                    "tp_mults": [3.0, 6.0, 9.0], "tp_pcts": [0.15, 0.35, 0.50],
                    "roe_trail_trigger": 0.03, "roe_trail_guard": 0.02, "use_trailing": True},
    },
    {
        "name": "10) 1× Hebel + 100% TP",
        "desc": "1× Hebel (kein Margin-Effekt), TP nur ein Level 100% bei 4×ATR",
        "params": {"spread_long": 0.3, "sl_lookback": 20, "sl_atr_min": 0.5, "sl_atr_fallback": 1.5,
                    "tp_mults": [4.0], "tp_pcts": [1.0],
                    "roe_trail_trigger": 0.03, "roe_trail_guard": 0.02, "use_trailing": True},
    },
]


# ═══════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════
print("=" * 130)
print(f"{'Multi-Varianten Backtest — 10 Strategie-Varianten auf gleichen Daten':^130}")
print(f"{'Spread-Scalper · Letztes Jahr (Jul 2025 — Jul 2026)':^130}")
print("=" * 130)

# Daten einmal laden
all_candle_data = {}
for symbol in SYMBOLS:
    print(f"\n📡 {symbol}:", end="")
    sys.stdout.flush()
    candles = fetch_year_candles_binance(symbol)
    all_candle_data[symbol] = candles

# Alle Varianten durchlaufen
results = []
for variant in VARIANTS:
    print(f"\n{'─' * 130}")
    print(f"  {variant['name']}: {variant['desc']}")
    print(f"{'─' * 130}")

    all_trades = {s: [] for s in SYMBOLS}
    total_trades = 0

    for symbol in SYMBOLS:
        candles = all_candle_data[symbol]
        if len(candles) < 100:
            print(f"  {symbol}: ⚠️  Nicht genug Daten")
            continue
        trades = run_backtest_variant(candles, symbol, variant["params"])
        all_trades[symbol] = trades
        total_trades += len(trades)

    combined = []
    for s in SYMBOLS:
        combined.extend(all_trades[s])

    stats = analyze_trades(combined)
    stats["name"] = variant["name"]
    stats["total_trades"] = total_trades
    results.append(stats)

    print(f"  Trades: {stats['trades']}  |  WR: {stats['wr']}%  |  PnL: {stats['pnl']:+8.2f} USDT  |  PF: {stats['pf']}  |  MaxDD: {stats['max_dd']:8.2f}")
    print(f"  AvgWin: {stats['avg_win']:+6.4f}  |  AvgLoss: {stats['avg_loss']:6.4f}  |  SL: {stats['sl_pct']}% ({stats['sl_pnl_total']:+8.2f})  |  TP: {stats['tp_pct']}% ({stats['tp_pnl_total']:+8.2f})")
    print(f"  SL profitabel: {stats['sl_profitable']} ({stats['sl_profitable_pnl']:+8.2f})  |  SL Verlust: {stats['sl_loss_pnl']:+8.2f}  |  Max♠: {stats['max_consec']}")

# ── Ranking-Tabelle ──
print("\n\n" + "=" * 130)
print(f"{'🏆 RANKING — Alle 10 Varianten (sortiert nach PnL)':^130}")
print("=" * 130)
header = f"{'#':>2} | {'Variante':<30} | {'Trades':>6} | {'WR':>5} | {'PnL':>10} | {'PF':>5} | {'MaxDD':>9} | {'ØWin':>8} | {'ØLoss':>8} | {'SL%':>4} | {'TP%':>4}"
print(header)
print("─" * 112)

ranked = sorted(results, key=lambda x: x["pnl"], reverse=True)
for i, r in enumerate(ranked, 1):
    marker = " 🏆" if i == 1 else ""
    print(f"{i:>2} | {r['name']:<30} | {r['trades']:>6} | {r['wr']:>4.1f}% | {r['pnl']:>+9.2f} | {r['pf']:>4.2f} | {r['max_dd']:>8.2f} | {r['avg_win']:>+7.4f} | {r['avg_loss']:>7.4f} | {r['sl_pct']:>3.0f}% | {r['tp_pct']:>3.0f}%{marker}")

# Winner details
best = ranked[0]
print(f"\n{'★' * 130}")
print(f"  BESTE VARIANTE: {best['name']}")
print(f"  PnL: {best['pnl']:+8.2f} USDT | WR: {best['wr']}% | PF: {best['pf']} | MaxDD: {best['max_dd']:8.2f}")
print(f"  SL profitabel: {best['sl_profitable']} ({best['sl_profitable_pnl']:+8.2f}) vs SL Verlust: {best['sl_loss_pnl']:+8.2f}")
print(f"  AvgWin: {best['avg_win']:+6.4f} | AvgLoss: {best['avg_loss']:6.4f} | Ratio: {abs(best['avg_win']/best['avg_loss']):.2f}x")
print(f"{'★' * 130}")

# Save all
try:
    out = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "ranking": [{"rank": i+1, "name": r["name"], **{k: v for k, v in r.items() if k != "name"}} for i, r in enumerate(ranked)],
    }
    with open("/Users/andreas/bitget_bot/backtest/compare_10_variants.json", "w") as f:
        json.dump(out, f, indent=2)
    print(f"\n  💾 Ergebnisse gespeichert: backtest/compare_10_variants.json")
except Exception as e:
    print(f"\n  ⚠️  Speichern fehlgeschlagen: {e}")

print(f"\n{'=' * 130}")
print(f"{'BACKTEST ABGESCHLOSSEN':^130}")
print("=" * 130)
