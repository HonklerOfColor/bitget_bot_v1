"""Kausaler 1h-Backtest der V1-Long-Logik.

Bildet die Live-Regeln nach, ohne API:

- Reversal: vorherige 1h rot, aktuelle grün (dieselbe Funktion wie der Bot)
- eine Position pro Symbol, fixe Kontraktgröße
- SL = tiefstes Tief der letzten 20 abgeschlossenen Stunden, mindestens 0.8×ATR weg
- TP = 2.5×ATR
- keine Entries in den Wien-Verluststunden
- 12h-Break-Even, Trailing ab 3% Peak-ROE, 24h-Time-Stop bei ROE ≤ 0
- Fees: Maker-Entry 0.02%, Taker-Exit 0.06%, Slippage, Funding

Zwei Entry-Modi:

- ``intra``: wie live — sobald die laufende Stunde über ihren Open steigt,
  Fill am Open. SL/TP gelten erst ab der nächsten Stunde, damit der Tiefpunkt
  vor dem Signal nicht als Stop zählt.
- ``confirmed``: die Umkehr-Stunde muss geschlossen grün sein, Fill am Open
  der Folge-Stunde. Vollständig kausal, keine Intra-Bar-Annahme.

SL und TP derselben Stunde: Stop zuerst (pessimistisch).
"""
from dataclasses import dataclass, field

from datetime import datetime

from trading_rules import (
    BLOCKED_ENTRY_HOURS,
    VIENNA,
    is_bearish,
    is_bullish,
    is_reversal_long,
    vienna_hour,
)


@dataclass(frozen=True)
class V1Config:
    size: float
    leverage: float = 3.0
    tp_atr: float = 2.5
    min_sl_atr: float = 0.8
    sl_lookback: int = 20
    atr_period: int = 14
    maker_fee: float = 0.0002
    taker_fee: float = 0.0006
    entry_slippage: float = 0.0001
    exit_slippage: float = 0.0002
    trail_start_roe: float = 3.0
    trail_giveback: float = 2.0
    be_min_roe: float = 0.3
    be_price_buffer: float = 0.0015
    time_stop_h: float = 24.0
    be_age_h: float = 12.0
    entry_mode: str = "confirmed"  # intra | confirmed
    ema_period: int = 0
    reentry_block_h: float = 0.0
    atr_pct_max: float = 0.0
    maker_tp: bool = False
    funding_skip_below: float = -0.0005
    blocked_hours: frozenset = field(default_factory=lambda: BLOCKED_ENTRY_HOURS)


def roe_pct(entry, mark, leverage):
    if mark <= 0 or entry <= 0 or leverage <= 0:
        return 0.0
    return (mark - entry) / mark * leverage * 100.0


def _atr_at(trs, i, period):
    """ATR aus den `period` abgeschlossenen Kerzen vor Index i."""
    if i < period + 1:
        return None
    window = trs[i - period:i]
    if any(v is None for v in window):
        return None
    return sum(window) / period


def _ema_known_before(closes, period):
    """ema[i] nutzt nur Closes bis einschließlich Kerze i-1."""
    out = [None] * len(closes)
    if period <= 0 or len(closes) <= period:
        return out
    k = 2.0 / (period + 1)
    ema = sum(closes[:period]) / period
    out[period] = ema
    for i in range(period, len(closes) - 1):
        ema = (closes[i] - ema) * k + ema
        out[i + 1] = ema
    return out


def _true_ranges(candles):
    trs = [None]
    for i in range(1, len(candles)):
        _, _, h, l, _ = candles[i]
        prev_c = candles[i - 1][4]
        trs.append(max(h - l, abs(h - prev_c), abs(l - prev_c)))
    return trs


def _funding_index(events, ts):
    """Letzter Funding-Satz mit Zeitpunkt <= ts. events aufsteigend."""
    lo, hi = 0, len(events)
    while lo < hi:
        mid = (lo + hi) // 2
        if events[mid][0] <= ts:
            lo = mid + 1
        else:
            hi = mid
    if lo == 0:
        return None
    return events[lo - 1][1]


def _accrue_funding(events, cursor, start_ts, end_ts, entry, size):
    """Funding für (start_ts, end_ts]. Long zahlt positiven Satz."""
    pnl = 0.0
    i = cursor
    notional = entry * size
    while i < len(events) and events[i][0] <= end_ts:
        if events[i][0] > start_ts:
            pnl -= events[i][1] * notional
        i += 1
    return pnl, i


def simulate(candles, funding, cfg, symbol=""):
    """candles: (ts_ms, open, high, low, close) aufsteigend. funding: (ts_ms, rate)."""
    if len(candles) < cfg.atr_period + 3:
        return []
    funding = sorted(funding or [])
    closes = [c[4] for c in candles]
    trs = _true_ranges(candles)
    emas = _ema_known_before(closes, cfg.ema_period) if cfg.ema_period else None

    trades = []
    pos = None
    fund_cursor = 0
    last_exit_ts = None
    last_exit_net = 0.0

    for i in range(len(candles)):
        ts, o, h, l, c = candles[i]
        if pos is not None and i > pos["i"]:
            fund_pnl, fund_cursor = _accrue_funding(
                funding, fund_cursor, candles[i - 1][0], ts, pos["entry"], cfg.size,
            )
            pos["funding"] += fund_pnl
            reason, raw_exit, maker = _exit_on_bar(pos, ts, o, h, l, cfg)
            if reason:
                trades.append(_close(pos, ts, raw_exit, reason, maker, cfg, symbol))
                last_exit_ts = ts
                last_exit_net = trades[-1]["net"]
                pos = None
            else:
                _update_protection(pos, ts, h, c, cfg)

        if pos is None:
            opened = _try_open(candles, i, emas, funding, cfg, last_exit_ts, last_exit_net)
            if opened:
                pos = opened
                pos["i"] = i

    if pos is not None:
        ts, _, _, _, c = candles[-1]
        trades.append(_close(pos, ts, c, "EOD", False, cfg, symbol))
    return trades


def _exit_on_bar(pos, ts, o, h, l, cfg):
    sl = pos["sl"]
    tp = pos["tp"]
    entry = pos["entry"]
    age_h = (ts - pos["ts"]) / 3_600_000

    def stop_price(level):
        return o if o < level else level

    # Schützender SL über dem Entry (Trailing / Break-Even) zuerst.
    if sl >= entry and l <= sl:
        return "SL", stop_price(sl), False
    if age_h >= cfg.time_stop_h and l <= entry:
        return "TIME", (o if o < entry else entry), False
    if l <= sl:
        return "SL", stop_price(sl), False
    if h >= tp:
        return "TP", tp, bool(cfg.maker_tp)
    return None, None, False


def _update_protection(pos, ts, high, close, cfg):
    entry = pos["entry"]
    age_h = (ts - pos["ts"]) / 3_600_000
    peak = max(pos["peak"], roe_pct(entry, high, cfg.leverage))
    pos["peak"] = peak
    sl = pos["sl"]
    if peak >= cfg.trail_start_roe:
        target = peak - cfg.trail_giveback
        new_sl = entry + (target / 100.0) * high / cfg.leverage
        if new_sl < high and new_sl > sl:
            pos["sl"] = new_sl
        return
    if (
        age_h >= cfg.be_age_h
        and roe_pct(entry, close, cfg.leverage) >= cfg.be_min_roe
        and close > entry * (1.0 + cfg.be_price_buffer)
        and entry > sl
    ):
        pos["sl"] = entry


def _try_open(candles, i, emas, funding, cfg, last_exit_ts, last_exit_net):
    mode = cfg.entry_mode
    if mode == "intra":
        if i < 1:
            return None
        prev, cur = candles[i - 1], candles[i]
        if not (is_bearish(prev[1], prev[4]) and is_bullish(cur[1], cur[2])):
            return None
        signal_close = candles[i - 1][4]
    elif mode == "confirmed":
        if i < 2:
            return None
        older, prev = candles[i - 2], candles[i - 1]
        if not is_reversal_long(older[1], older[4], prev[1], prev[4]):
            return None
        signal_close = prev[4]
    else:
        raise ValueError(mode)

    ts, o, h, l, c = candles[i]
    hour = vienna_hour(ts / 1000.0)
    if hour in cfg.blocked_hours:
        return None
    if cfg.reentry_block_h > 0 and last_exit_ts is not None and last_exit_net <= 0:
        if ts - last_exit_ts < cfg.reentry_block_h * 3_600_000:
            return None
    if cfg.ema_period:
        ema = emas[i] if emas else None
        if ema is None or signal_close <= ema:
            return None
    atr = _atr_at(_true_ranges_cached(candles), i, cfg.atr_period)
    if atr is None or atr <= 0 or o <= 0:
        return None
    if cfg.atr_pct_max > 0 and (atr / o) > cfg.atr_pct_max:
        return None
    if cfg.funding_skip_below is not None and funding:
        rate = _funding_index(funding, ts)
        if rate is not None and rate < cfg.funding_skip_below:
            return None

    fill = o * (1.0 + cfg.entry_slippage)
    start = max(0, i - cfg.sl_lookback)
    lowest = min(bar[3] for bar in candles[start:i])
    sl = lowest * 0.999
    min_dist = fill - atr * cfg.min_sl_atr
    if sl > min_dist:
        sl = min_dist
    if sl >= fill:
        return None
    tp = fill + atr * cfg.tp_atr
    return {
        "ts": ts,
        "entry": fill,
        "sl": sl,
        "tp": tp,
        "peak": 0.0,
        "funding": 0.0,
        "atr_pct": atr / o,
        "sl_dist_pct": (fill - sl) / fill,
        "tp_dist_pct": (tp - fill) / fill,
    }


_TR_CACHE = {}


def _true_ranges_cached(candles):
    key = id(candles)
    hit = _TR_CACHE.get(key)
    if hit is None or hit[0] != len(candles):
        hit = (len(candles), _true_ranges(candles))
        _TR_CACHE[key] = hit
    return hit[1]


def clear_caches():
    _TR_CACHE.clear()


def _close(pos, ts, raw_exit, reason, maker, cfg, symbol):
    if maker:
        exit_px = raw_exit
        exit_fee_rate = cfg.maker_fee
    else:
        exit_px = raw_exit * (1.0 - cfg.exit_slippage)
        exit_fee_rate = cfg.taker_fee
    size = cfg.size
    gross = (exit_px - pos["entry"]) * size
    fees = -(pos["entry"] * size * cfg.maker_fee + exit_px * size * exit_fee_rate)
    net = gross + fees + pos["funding"]
    hold_h = (ts - pos["ts"]) / 3_600_000
    wd = datetime.fromtimestamp(pos["ts"] / 1000, VIENNA)
    return {
        "symbol": symbol,
        "entry_ts": pos["ts"],
        "exit_ts": ts,
        "entry": pos["entry"],
        "exit": exit_px,
        "size": size,
        "gross": gross,
        "fees": fees,
        "funding": pos["funding"],
        "net": net,
        "reason": reason,
        "hold_h": hold_h,
        "atr_pct": pos["atr_pct"],
        "sl_dist_pct": pos["sl_dist_pct"],
        "tp_dist_pct": pos["tp_dist_pct"],
        "hour": vienna_hour(pos["ts"] / 1000),
        "weekday": wd.strftime("%a"),
    }


def metrics(trades, start_equity=128.0):
    if not trades:
        return {
            "n": 0, "net": 0.0, "gross": 0.0, "fees": 0.0, "funding": 0.0,
            "pf": 0.0, "wr": 0.0, "max_dd": 0.0, "max_dd_pct": 0.0,
            "expect": 0.0,
        }
    nets = [t["net"] for t in trades]
    wins = [x for x in nets if x > 0]
    losses = [x for x in nets if x <= 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    pf = (gross_win / gross_loss) if gross_loss > 0 else (999.0 if gross_win > 0 else 0.0)
    ordered = sorted(trades, key=lambda t: t["exit_ts"])
    eq = start_equity
    peak = eq
    max_dd = 0.0
    for t in ordered:
        eq += t["net"]
        peak = max(peak, eq)
        max_dd = max(max_dd, peak - eq)
    net = sum(nets)
    return {
        "n": len(trades),
        "net": net,
        "gross": sum(t["gross"] for t in trades),
        "fees": sum(t["fees"] for t in trades),
        "funding": sum(t["funding"] for t in trades),
        "pf": pf,
        "wr": 100.0 * len(wins) / len(trades),
        "max_dd": max_dd,
        "max_dd_pct": 100.0 * max_dd / start_equity,
        "expect": net / len(trades),
    }


def merge_symbol_trades(by_symbol):
    out = []
    for rows in by_symbol.values():
        out.extend(rows)
    out.sort(key=lambda t: t["exit_ts"])
    return out
