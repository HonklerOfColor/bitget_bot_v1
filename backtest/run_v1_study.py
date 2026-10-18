"""IS/OOS-Studie der V1-Logik. Lädt Kerzen bei Bedarf, druckt Kennzahlen.

Auswahl nur auf dem In-Sample. Out-of-Sample wird danach einmal berichtet.
"""
import json
import sys
import time
from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backtest.fetch_history import SYMBOLS, load_or_fetch
from backtest.v1_engine import V1Config, clear_caches, merge_symbol_trades, metrics, simulate

SIZES = {"BTCUSDT": 0.001, "ETHUSDT": 0.01, "BNBUSDT": 0.05}
# In-Sample endet vor dem Live-Fenster. OOS ab 2026-07-01 enthält die echten Trades.
IS_END = int(datetime(2026, 7, 1, tzinfo=timezone.utc).timestamp() * 1000)
LIVE_START = int(datetime(2026, 9, 14, 5, 14, tzinfo=timezone.utc).timestamp() * 1000)


def run_book(data, cfg_for, start_ms=None, end_ms=None):
    """Simuliert auf der vollen Historie und filtert nach Entry-Zeit.

    Der Exit darf über die Fenstergrenze laufen, sonst wird jeder Trade am
    Stichtag künstlich geschlossen.
    """
    clear_caches()
    by = {}
    for sym, (candles, funding) in data.items():
        by[sym] = simulate(candles, funding, cfg_for(sym), sym)
    trades = merge_symbol_trades(by)
    if start_ms is not None:
        trades = [t for t in trades if t["entry_ts"] >= start_ms]
    if end_ms is not None:
        trades = [t for t in trades if t["entry_ts"] < end_ms]
    return trades


def base_cfg(symbol, **overrides):
    cfg = V1Config(size=SIZES[symbol], entry_mode="confirmed")
    return replace(cfg, **overrides) if overrides else cfg


def fmt(m):
    return (
        f"{m['n']:4d}  {m['net']:+8.2f}  {m['expect']:+7.4f}  {m['pf']:5.2f}  "
        f"{m['wr']:5.1f}  {m['max_dd']:7.2f}  {m['gross']:+8.2f}  {m['fees']:+7.2f}"
    )


HEADER = "   n      Netto   E[trade]     PF     WR    MaxDD     Brutto     Fees"


def report(label, trades):
    print(f"{label:<28} {fmt(metrics(trades))}")


def loser_table(trades):
    if not trades:
        print("keine Trades")
        return
    losers = [t for t in trades if t["net"] <= 0]
    print(f"Verlierer {len(losers)} / {len(trades)}")
    by = defaultdict(list)
    for t in trades:
        by[t["symbol"]].append(t)
    print("Symbol")
    for sym, rows in sorted(by.items()):
        print(f"  {sym:<10} {fmt(metrics(rows))}")
    print("Wochentag")
    for wd in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"):
        rows = [t for t in trades if t["weekday"] == wd]
        if rows:
            print(f"  {wd:<10} {fmt(metrics(rows))}")
    print("Haltedauer")
    buckets = [(0, 2, "<2h"), (2, 6, "2-6h"), (6, 12, "6-12h"), (12, 24, "12-24h"), (24, 1000, ">24h")]
    for lo, hi, name in buckets:
        rows = [t for t in trades if lo <= t["hold_h"] < hi]
        if rows:
            print(f"  {name:<10} {fmt(metrics(rows))}")
    print("ATR%")
    for lo, hi, name in ((0, 0.005, "<0.5%"), (0.005, 0.01, "0.5-1%"), (0.01, 0.02, "1-2%"), (0.02, 1, ">2%")):
        rows = [t for t in trades if lo <= t["atr_pct"] < hi]
        if rows:
            print(f"  {name:<10} {fmt(metrics(rows))}")
    print("Exit-Grund")
    reasons = defaultdict(list)
    for t in trades:
        reasons[t["reason"]].append(t)
    for reason, rows in sorted(reasons.items()):
        print(f"  {reason:<10} {fmt(metrics(rows))}")
    # Re-Entry: selber Symbol innerhalb von 6h nach einem Verlierer
    re_n = re_net = 0
    last = {}
    for t in sorted(trades, key=lambda x: x["entry_ts"]):
        prev = last.get(t["symbol"])
        if prev and prev["net"] <= 0 and t["entry_ts"] - prev["exit_ts"] <= 6 * 3_600_000:
            re_n += 1
            re_net += t["net"]
        last[t["symbol"]] = t
    print(f"Re-Entry <6h nach Verlust: n={re_n} net={re_net:+.2f}")


def validate_live(data, trades_path):
    raw = json.loads(Path(trades_path).read_text())
    rows = raw["trades"] if isinstance(raw, dict) else raw
    live = [
        t for t in rows
        if t.get("subaccount") == "at-HNKHNKHNK"
        and t.get("side") == "long"
        and t.get("symbol") in SIZES
        and t["opened_at"] >= LIVE_START
    ]
    book = run_book(data, lambda s: base_cfg(s, entry_mode="intra"), start_ms=LIVE_START)
    confirmed = run_book(data, lambda s: base_cfg(s, entry_mode="confirmed"), start_ms=LIVE_START)
    print("\n=== Live vs Backtest ab 2026-09-14 (Long, BTC/ETH/BNB) ===")
    print(f"Live n={len(live)} net={sum(t['net_pnl'] for t in live):+.2f} "
          f"gross={sum(t['pnl'] for t in live):+.2f} fees={sum(t['fees'] for t in live):+.2f}")
    print(f"{'intra':<28} {fmt(metrics(book))}")
    print(f"{'confirmed':<28} {fmt(metrics(confirmed))}")
    # Preisnähe: Live-Entry gegen Open und Close der Stunde
    open_err = []
    close_err = []
    matched = 0
    for t in live:
        cs = data[t["symbol"]][0]
        ts = t["opened_at"]
        candle = None
        for c in cs:
            if c[0] <= ts < c[0] + 3_600_000:
                candle = c
                break
        if not candle or t["entry"] == 0:
            continue
        open_err.append(abs(t["entry"] - candle[1]) / t["entry"])
        close_err.append(abs(t["entry"] - candle[4]) / t["entry"])
        near = [
            b for b in book
            if b["symbol"] == t["symbol"] and abs(b["entry_ts"] - candle[0]) <= 3_600_000
        ]
        if near:
            matched += 1
    if open_err:
        open_err.sort()
        close_err.sort()
        print(f"Live-Entry vs Stunden-Open, Median {open_err[len(open_err)//2]*100:.3f}%  "
              f"vs Close {close_err[len(close_err)//2]*100:.3f}%")
        print(f"Live-Trades mit Intra-Backtest-Entry in derselben Stunde: {matched}/{len(live)}")


def half_split(trades):
    if not trades:
        return [], []
    ordered = sorted(trades, key=lambda t: t["entry_ts"])
    mid = ordered[len(ordered) // 2]["entry_ts"]
    return [t for t in ordered if t["entry_ts"] < mid], [t for t in ordered if t["entry_ts"] >= mid]


def main():
    start = 1_727_740_800_000
    end = int(time.time() * 1000)
    data = {}
    for sym in SYMBOLS:
        candles, funding = load_or_fetch(sym, start, end)
        data[sym] = (candles, funding)
        print(f"{sym} {len(candles)} Kerzen  {len(funding)} Funding  "
              f"{datetime.fromtimestamp(candles[0][0]/1000, timezone.utc).date()} → "
              f"{datetime.fromtimestamp(candles[-1][0]/1000, timezone.utc).date()}")

    trades_path = sys.argv[1] if len(sys.argv) > 1 else ""
    if trades_path:
        validate_live(data, trades_path)

    print("\n=== Baseline confirmed, ganz / IS / OOS ===")
    print(HEADER)
    all_tr = run_book(data, base_cfg)
    is_tr = run_book(data, base_cfg, end_ms=IS_END)
    oos_tr = run_book(data, base_cfg, start_ms=IS_END)
    report("alle", all_tr)
    report("IS <2026-07", is_tr)
    report("OOS >=2026-07", oos_tr)
    a, b = half_split(is_tr)
    report("IS Hälfte A", a)
    report("IS Hälfte B", b)

    print("\n=== Verlierer-Struktur, nur IS ===")
    loser_table(is_tr)

    variants = {
        "intra": dict(entry_mode="intra"),
        "ema20": dict(ema_period=20),
        "ema50": dict(ema_period=50),
        "maker_tp": dict(maker_tp=True),
        "reentry_6h": dict(reentry_block_h=6),
        "reentry_12h": dict(reentry_block_h=12),
        "stop_12h": dict(time_stop_h=12),
        "stop_36h": dict(time_stop_h=36),
        "tp_2.0": dict(tp_atr=2.0),
        "tp_3.0": dict(tp_atr=3.0),
        "sl_0.6": dict(min_sl_atr=0.6),
        "sl_1.2": dict(min_sl_atr=1.2),
        "atr_cap_1.5pct": dict(atr_pct_max=0.015),
        "atr_cap_2.5pct": dict(atr_pct_max=0.025),
        "ema20_maker": dict(ema_period=20, maker_tp=True),
        "ema20_stop12": dict(ema_period=20, time_stop_h=12),
    }
    print("\n=== Varianten nur IS (Auswahl) ===")
    print(HEADER)
    is_scores = {}
    for name, kw in variants.items():
        trades = run_book(data, lambda s, kw=kw: base_cfg(s, **kw), end_ms=IS_END)
        is_scores[name] = metrics(trades)
        report(name, trades)
        ha, hb = half_split(trades)
        # Stabilität der beiden IS-Hälften: beide Netto nicht klar schlechter als ihre Baseline-Hälften
        is_scores[name]["_a"] = metrics(ha)["net"]
        is_scores[name]["_b"] = metrics(hb)["net"]

    base_is = metrics(is_tr)
    print("\n=== OOS der IS-Kandidaten (einmalig) ===")
    print("Kandidat, wenn IS-Erwartungswert > Baseline und beide IS-Hälften netto ≥ 0 "
          "oder beide besser als die Baseline-Hälfte.")
    base_a, base_b = half_split(is_tr)
    ba, bb = metrics(base_a)["net"], metrics(base_b)["net"]
    print(HEADER)
    report("OOS baseline", oos_tr)
    adopted = []
    for name, kw in variants.items():
        sc = is_scores[name]
        better_expect = sc["expect"] > base_is["expect"]
        halves_ok = (sc["_a"] >= 0 and sc["_b"] >= 0) or (sc["_a"] >= ba and sc["_b"] >= bb)
        dd_ok = sc["max_dd"] <= base_is["max_dd"] * 1.05
        if not (better_expect and halves_ok and dd_ok and sc["n"] >= 30):
            continue
        trades = run_book(data, lambda s, kw=kw: base_cfg(s, **kw), start_ms=IS_END)
        report(f"OOS {name}", trades)
        adopted.append((name, metrics(trades), sc))

    out = {
        "baseline_is": base_is,
        "baseline_oos": metrics(oos_tr),
        "is_scores": {k: {kk: vv for kk, vv in sc.items() if not kk.startswith("_")} for k, sc in is_scores.items()},
        "adopted_oos": [{"name": n, "oos": o, "is": {kk: vv for kk, vv in s.items() if not str(kk).startswith("_")}} for n, o, s in adopted],
    }
    dest = ROOT / "backtest" / "cache" / "study.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, indent=2))
    print(f"\ngeschrieben {dest}")


if __name__ == "__main__":
    main()
