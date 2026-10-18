"""Regeln aus TRADING_RULES.md, ohne Exchange."""
import ast
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from trading_rules import (
    BLOCKED_ENTRY_HOURS,
    VIENNA,
    entries_are_paused,
    entry_hour_blocked,
    should_arm_breakeven,
    should_time_stop,
    time_stop_due,
    vienna_hour,
)

def epoch(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=VIENNA).timestamp()


class HourWindowTests(unittest.TestCase):
    def test_blocked_set_matches_rule(self):
        self.assertEqual(BLOCKED_ENTRY_HOURS, frozenset({0, 1, 2, 3, 4, 14, 17, 20, 22}))

    def test_summer_and_winter_use_vienna_not_utc(self):
        # CEST (UTC+2): Wien 00:30 = UTC 22:30 am Vortag. UTC-Stunde 22 ist erlaubt.
        self.assertEqual(vienna_hour(epoch(2026, 8, 26, 0, 30)), 0)
        self.assertTrue(entry_hour_blocked(epoch(2026, 8, 26, 0, 30)))
        # CET (UTC+1): Wien 03:30 = UTC 02:30.
        self.assertEqual(vienna_hour(epoch(2026, 1, 15, 3, 30)), 3)
        self.assertTrue(entry_hour_blocked(epoch(2026, 1, 15, 3, 30)))
        # UTC 12:00 im Juli ist Wien 14:00 (gesperrt), nicht Stunde 12.
        utc_noon = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc).timestamp()
        self.assertEqual(vienna_hour(utc_noon), 14)
        self.assertTrue(entry_hour_blocked(utc_noon))

    def test_allowed_neighbors(self):
        for hour in (5, 13, 15, 16, 18, 19, 21, 23):
            self.assertFalse(entry_hour_blocked(epoch(2026, 10, 16, hour)), hour)
        for hour in (0, 1, 2, 3, 4, 14, 17, 20, 22):
            self.assertTrue(entry_hour_blocked(epoch(2026, 10, 16, hour, 59)), hour)


class TimeStopTests(unittest.TestCase):
    def test_only_losers_at_or_after_24h(self):
        self.assertFalse(should_time_stop(23.99, -5))
        self.assertTrue(should_time_stop(24.0, 0))
        self.assertTrue(should_time_stop(30, -0.1))
        self.assertFalse(should_time_stop(51, 2.1))

    def test_missing_mark_does_not_stop_a_winner(self):
        self.assertFalse(time_stop_due(30, pnl=1.5, size=0.01, mark=0, leverage=3))
        self.assertTrue(time_stop_due(24, pnl=-0.2, size=0.01, mark=0, leverage=3))
        self.assertTrue(time_stop_due(24, pnl=-0.2, size=0.05, mark=729, leverage=3))
        self.assertFalse(time_stop_due(24, pnl=0.08, size=0.01, mark=2441, leverage=3))


class BreakevenTests(unittest.TestCase):
    def test_arms_only_when_in_profit_before_trailing(self):
        self.assertFalse(should_arm_breakeven(11.9, 1.0, 1.0))
        self.assertFalse(should_arm_breakeven(13.0, 0.29, 0.29))
        self.assertFalse(should_arm_breakeven(13.2, -0.70, -0.70))
        self.assertTrue(should_arm_breakeven(13.2, 0.97, 0.97))
        # Peak bereits im Trailing (≥3%): Break-Even-Arm nicht noch einmal setzen.
        self.assertFalse(should_arm_breakeven(20, 1.0, 3.0))


class EntriesPausedTests(unittest.TestCase):
    def test_only_explicit_false_resumes(self):
        self.assertTrue(entries_are_paused(None))
        self.assertTrue(entries_are_paused(object()))

        class Missing:
            pass

        class Paused:
            ENTRIES_PAUSED = True

        class Other:
            ENTRIES_PAUSED = None

        class Live:
            ENTRIES_PAUSED = False

        self.assertTrue(entries_are_paused(Missing()))
        self.assertTrue(entries_are_paused(Paused()))
        self.assertTrue(entries_are_paused(Other()))
        self.assertFalse(entries_are_paused(Live()))

    def test_pause_blocks_new_entries_but_not_open_positions(self):
        src = (ROOT / "spread_scalper.py").read_text()
        start = src.index("def run_cycle")
        end = src.index("def _entries_paused")
        cycle = src[start:end]
        pos = cycle.index("if pos:")
        flat = cycle.index("else:", pos)
        self.assertLess(cycle.index("_enforce_time_stop"), flat)
        self.assertLess(flat, cycle.index("self._entries_paused()"))
        self.assertLess(cycle.index("self._entries_paused()"), cycle.index("place_limit_order"))
        order = src[src.index("def place_limit_order"):src.index("def place_market_close")]
        self.assertIn("SKIP_ENTRIES_PAUSED", order)
        self.assertLess(order.index("_entries_paused"), order.index("place-order"))
        run = src[src.index("def run(self)"):]
        self.assertIn("_notify_entries_paused", run)
        self.assertIn("3600", run)
        self.assertIn("Hebel-Setup übersprungen", run)
    def test_cycle_enforces_stop_before_orderbook_and_uses_vienna(self):
        src = (ROOT / "spread_scalper.py").read_text()
        start = src.index("def run_cycle")
        end = src.index("def run(self)", start)
        cycle = src[start:end]
        self.assertLess(cycle.index("_enforce_time_stop"), cycle.index("get_depth"))
        self.assertIn("entry_hour_blocked()", cycle)
        self.assertNotIn("time.localtime", cycle)
        self.assertIn("LONG_ONLY", cycle)
        tree = ast.parse(src)
        methods = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
        self.assertIn("_enforce_time_stop", methods)


if __name__ == "__main__":
    unittest.main()
