"""Synthetische Pfade für den V1-Engine. Keine Kerzen-Downloads."""
import unittest
from datetime import datetime, timedelta, timezone

from backtest.v1_engine import V1Config, clear_caches, metrics, simulate
from trading_rules import VIENNA


def bar(ts, o, h, l, c):
    return (int(ts.timestamp() * 1000), float(o), float(h), float(l), float(c))


def hour_series(start, n, price=100.0, step=0.0):
    """Flache Stunden, jede Kerze leicht grün, damit kein Reversal entsteht."""
    out = []
    px = price
    for i in range(n):
        o = px
        c = px + step
        h = max(o, c) + 0.05
        l = min(o, c) - 0.05
        out.append(bar(start + timedelta(hours=i), o, h, l, c))
        px = c
    return out


class EngineTests(unittest.TestCase):
    def setUp(self):
        clear_caches()

    def test_blocked_vienna_hour_skips_entry(self):
        # 2026-01-15 03:00 Wien = 02:00 UTC, Stunde 3 ist gesperrt.
        start = datetime(2026, 1, 15, 2, 0, tzinfo=timezone.utc) - timedelta(hours=30)
        candles = hour_series(start, 40, price=100, step=0.0)
        # Kerze mit Wien-Stunde 3: Index so wählen, dass die Vor-Kerze rot ist
        # und die Signal-Kerze ein höheres High als Open hat.
        target = datetime(2026, 1, 15, 2, 0, tzinfo=timezone.utc)
        target_ms = int(target.timestamp() * 1000)
        idx = next(i for i, c in enumerate(candles) if c[0] == target_ms)
        prev = list(candles[idx - 1])
        prev[4] = prev[1] - 1.0  # rot
        prev[3] = prev[4] - 0.1
        candles[idx - 1] = tuple(prev)
        cur = list(candles[idx])
        cur[2] = cur[1] + 1.0  # High über Open
        candles[idx] = tuple(cur)
        cfg = V1Config(size=1.0, entry_mode="intra", entry_slippage=0, exit_slippage=0)
        trades = simulate(candles, [], cfg, "TEST")
        self.assertTrue(all(t["hour"] != 3 for t in trades))

    def test_confirmed_reversal_enters_next_open_and_tp(self):
        start = datetime(2026, 6, 2, 8, 0, tzinfo=timezone.utc)  # 10:00 Wien, erlaubt
        candles = hour_series(start, 50, price=100, step=0.01)
        # Bar 20 rot, Bar 21 grün → Entry am Open von Bar 22.
        red = list(candles[20])
        red[4] = red[1] - 2
        red[3] = red[4]
        red[2] = red[1]
        candles[20] = tuple(red)
        green = list(candles[21])
        green[4] = green[1] + 2
        green[2] = green[4]
        candles[21] = tuple(green)
        # Danach stark steigend, TP muss kommen. ATR ist klein (~0.1), TP nah.
        for j in range(22, 40):
            prev_c = candles[j - 1][4]
            candles[j] = bar(start + timedelta(hours=j), prev_c, prev_c + 5, prev_c - 0.01, prev_c + 4)
        cfg = V1Config(
            size=1.0, entry_mode="confirmed", entry_slippage=0, exit_slippage=0,
            maker_fee=0, taker_fee=0, tp_atr=2.5, min_sl_atr=0.8,
        )
        trades = simulate(candles, [], cfg, "TEST")
        self.assertGreaterEqual(len(trades), 1)
        self.assertEqual(trades[0]["reason"], "TP")
        self.assertGreater(trades[0]["net"], 0)

    def test_time_stop_closes_loser_and_spares_winner(self):
        start = datetime(2026, 6, 2, 8, 0, tzinfo=timezone.utc)
        candles = hour_series(start, 60, price=100, step=0.0)
        red = list(candles[20])
        red[4] = red[1] - 1
        red[3] = red[4]
        candles[20] = tuple(red)
        green = list(candles[21])
        green[4] = green[1] + 0.2
        green[2] = green[4]
        candles[21] = tuple(green)
        # Ab Entry (Bar 22) seitwärts unter dem Entry, aber über dem weiten SL.
        entry_open = candles[22][1]
        for j in range(22, 55):
            px = entry_open - 0.05
            candles[j] = bar(start + timedelta(hours=j), px, px + 0.02, px - 0.02, px)
        cfg = V1Config(
            size=1.0, entry_mode="confirmed", entry_slippage=0, exit_slippage=0,
            maker_fee=0, taker_fee=0, time_stop_h=24, min_sl_atr=5,
        )
        trades = simulate(candles, [], cfg, "TEST")
        self.assertTrue(any(t["reason"] == "TIME" for t in trades), trades)
        held = [t for t in trades if t["reason"] == "TIME"][0]
        self.assertGreaterEqual(held["hold_h"], 24)

    def test_fees_reduce_a_flat_roundtrip(self):
        start = datetime(2026, 6, 2, 8, 0, tzinfo=timezone.utc)
        candles = hour_series(start, 30, price=100, step=0.0)
        red = list(candles[20])
        red[4] = red[1] - 1
        red[3] = red[4]
        candles[20] = tuple(red)
        green = list(candles[21])
        green[4] = green[1] + 0.2
        green[2] = green[4]
        candles[21] = tuple(green)
        # Sofortiger Stop in der nächsten Stunde.
        entry = candles[22][1]
        candles[23] = bar(start + timedelta(hours=23), entry, entry + 0.01, entry - 20, entry - 10)
        cfg = V1Config(size=2.0, entry_mode="confirmed", entry_slippage=0, exit_slippage=0)
        trades = simulate(candles, [], cfg, "TEST")
        self.assertEqual(len(trades), 1)
        self.assertLess(trades[0]["fees"], 0)
        self.assertLess(trades[0]["net"], trades[0]["gross"])

    def test_metrics_drawdown(self):
        rows = [
            {"net": 2, "gross": 2, "fees": 0, "funding": 0, "exit_ts": 2},
            {"net": -5, "gross": -5, "fees": 0, "funding": 0, "exit_ts": 3},
            {"net": 1, "gross": 1, "fees": 0, "funding": 0, "exit_ts": 4},
        ]
        m = metrics(rows, start_equity=100)
        self.assertEqual(m["n"], 3)
        self.assertAlmostEqual(m["net"], -2)
        self.assertAlmostEqual(m["max_dd"], 5)
        self.assertAlmostEqual(m["wr"], 100 * 2 / 3)


if __name__ == "__main__":
    unittest.main()
