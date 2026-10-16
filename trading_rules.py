"""Reine Entscheidungsfunktionen der Live-Regeln.

Keine API, kein State. `spread_scalper.py` ruft sie auf, die Tests prüfen sie
ohne Bitget-Credentials.
"""
import time
from datetime import datetime
from zoneinfo import ZoneInfo

# Verluststunden, lokal Europe/Vienna (nicht die Prozess-Zeitzone).
# 2–4 / 14 / 17 / 20 / 22 seit 2026-09-03, 0 und 1 seit 2026-09-14.
VIENNA = ZoneInfo("Europe/Vienna")
BLOCKED_ENTRY_HOURS = frozenset({0, 1, 2, 3, 4, 14, 17, 20, 22})

TIME_STOP_HOURS = 24.0
BREAKEVEN_MIN_AGE_H = 12.0
BREAKEVEN_MIN_ROE_PCT = 0.3


def vienna_hour(epoch_s=None):
    """Stunde 0–23 in Europe/Vienna. DST (CET/CEST) macht zoneinfo."""
    if epoch_s is None:
        epoch_s = time.time()
    return datetime.fromtimestamp(epoch_s, VIENNA).hour


def entry_hour_blocked(epoch_s=None):
    """Keine neuen Entries in den Verluststunden."""
    return vienna_hour(epoch_s) in BLOCKED_ENTRY_HOURS


def unrealized_roe_pct(pnl, size, mark, leverage):
    """ROE in Prozent. None, wenn sich die Position nicht bewerten lässt."""
    if size <= 0 or mark <= 0 or leverage <= 0:
        return None
    margin = size * mark / leverage
    if margin <= 0:
        return None
    return pnl / margin * 100.0


def should_time_stop(age_h, roe_pct):
    """24h-Time-Stop nur für Positionen mit ROE ≤ 0. Gewinner laufen weiter."""
    return age_h >= TIME_STOP_HOURS and roe_pct <= 0


def time_stop_due(age_h, pnl, size, mark, leverage):
    """Wie der Bot entscheidet.

    Fehlender Mark-Preis darf einen Gewinner (pnl > 0) nicht schließen.
    pnl ≤ 0 ohne bewertbare Margin zählt als Verlust und wird gestoppt.
    """
    roe = unrealized_roe_pct(pnl, size, mark, leverage)
    if roe is None:
        roe = 0.0 if pnl <= 0 else 1.0
    return should_time_stop(age_h, roe)


def should_arm_breakeven(age_h, roe_pct, peak_roe, breakeven_pnl_pct=0.03):
    """12h-Break-Even: im Plus, Trailing (ab 3% Peak) noch nicht zuständig."""
    return (
        age_h >= BREAKEVEN_MIN_AGE_H
        and roe_pct >= BREAKEVEN_MIN_ROE_PCT
        and peak_roe < (breakeven_pnl_pct * 100.0)
    )
