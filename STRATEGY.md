# DS-SpreadScaler — Trading Strategy

## Overview

High-frequency spread-scalping bot on **Bitget USDT-M Futures (Demo, UTA Mode)**.
Places post-only limit orders inside the bid-ask spread, captures small
profits from spread contraction, and exits via ATR-based TP/SL.

## Core Parameters

| Parameter | Value | Description |
|-----------|-------|-------------|
| **Symbols** | BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT | 4 pairs simultaneously |
| **Leverage** | 5× Isolated | UTA-compatible |
| **Account Mode** | Hedge Mode | Can hold LONG + SHORT simultaneously (random direction per cycle) |
| **Loop interval** | 2 seconds | Full cycle across all symbols |
| **Order type** | Post-only limit | Always maker (fee discount) |
| **Max spread** | 0.5% | Skip if wider |
| **Offset** | 0.01% | Inside the spread |

## Entry Logic

1. **Fetch orderbook depth** (bid, ask, spread)
2. **Check for existing pending order** — skip if one is active (< 60s old)
3. **Calculate entry prices** within the spread:
   - LONG entry = bid + (spread × 0.3)
   - SHORT entry = bid + (spread × 0.7)
   - Ensures LONG < SHORT
4. **Random direction**: randomly pick LONG **or** SHORT each cycle (Hedge Mode)
5. **Post-only limit order** at calculated price
6. **0.5s pause**, check if order filled:
   - Filled → set TP/SL via `place-pos-tpsl`
   - Not filled → store as pending, retry next cycle
7. **Stale order cleanup**: cancel after 60 seconds, place fresh

### Position Sizing

| Symbol | Min Qty | Approx Notional |
|--------|---------|-----------------|
| BTCUSDT | 0.001 | ~$63 |
| ETHUSDT | 0.05 | ~$150 |
| SOLUSDT | 0.2 | ~$16 |
| XRPUSDT | 5.0 | ~$5 |

Minimum notional enforced at **$5 USD**. Size is dynamically adjusted
upwards if min_qty × mid_price < $5.

## TP/SL (Exchange-Managed via `place-pos-tpsl`)

Take-profit and stop-loss are set via Bitget's `place-pos-tpsl` API
immediately after a position opens. Sets TP + SL on the **entire position**
(no partial sizes, UTA-compatible).

| Level | ATR Multiplier | Direction |
|-------|----------------|-----------|
| **TP1** | 3.0× ATR | LONG: entry + (ATR × 3), SHORT: entry - (ATR × 3) |
| **SL** | 0.4× – 0.8× ATR (dynamic) | LONG: entry - (ATR × mult), SHORT: entry + (ATR × mult) |

### SL Dynamic Adjustment

| Condition | SL Multiplier |
|-----------|--------------|
| Base (0 losses) | 0.4× ATR |
| After 3 consecutive losses | 0.6× ATR |
| After 5+ consecutive losses | 0.8× ATR (capped) |

### PnL-Protected SL (alle 10s, einmalig pro Position)

When unrealized PnL exceeds **+1% ROE**, the SL is moved **once** to lock in profit:

| Direction | Target SL | Fallback if target beyond market |
|-----------|-----------|----------------------------------|
| **LONG** | Entry × 1.01 (1% profit) | Markt × 0.998 (0.2% under market) |
| **SHORT** | Entry × 0.99 (1% profit) | Entry (breakeven) |

After the SL is moved, `sl_protected = True` prevents further adjustments.

SL protection is tracked per position via `self.positions[symbol]["sl_protected"]`.
The flag is preserved across all code paths (plan orders, fallback, TPSL retries).

## ATR Calculation

- **Timeframe**: 1-hour candles
- **Period**: 14 candles
- **Formula**: Simple moving average of True Range (high - low, |high - prev close|, |low - prev close|)
- **Source**: Bitget market candles API (`get_candles` with `"1H"` granularity)
- **Fallback**: None → skip position entry

## Position Monitoring

Once a position is open, the bot cycles every 2 seconds:

1. **Fetch position** from API (`get_position`)
2. **Check TP/SL status** — retry if not set
3. **Check TP/SL hit** via `markPrice` (not bid/ask), using position's `stopLoss`/`takeProfit` fields
4. **Close via market order** when TP or SL is hit (with flash-close detection for already-closed positions)
5. **Record trade** to learnings file + Telegram notification (TP only, no SL spam)

### Closed-Position Detection

Runs every ~30s. Detects positions closed by the exchange (TP/SL hit).
Calculates PnL from ticker last-price (not entry price — avoids 0.0000 USDT bug).

## Trade Recording & Learning

Each trade is saved to `spread_learnings.json` with:
- Symbol, side, entry/exit price, PnL, exit reason
- Stats per symbol: win rate, total PnL, consecutive losses

### Adaptive Behavior

| Condition | Action |
|-----------|--------|
| 3 consecutive losses | Log warning (SL multiplier increased to 0.6×) |
| 5 consecutive losses | Log warning (SL multiplier capped at 0.8×) |
| Consecutive wins | Loss streak resets to 0 |

## Risk Management

| Rule | Detail |
|------|--------|
| **Position limit** | 1 order/pair at a time (pending order check) |
| **SL (exchange)** | Dynamic 0.4–0.8× ATR via `place-pos-tpsl` |
| **PnL protection** | SL moves to profit zone at +1% ROE (once per position) |
| **Stale order** | Auto-cancel after 60 seconds |
| **Min notional** | $5 USD (exchange minimum) |
| **Spread filter** | Skip if > 0.5% |
| **Order type** | Post-Only (Maker) |
| **SL/TP check** | Uses `markPrice` (not bid/ask) to avoid false triggers from spread spikes |

## Dashboard

A live terminal dashboard runs as a separate process:
```bash
cd ~/bitget_bot && python3 dashboard.py
```

Displays:
- Wallet balance + unrealized PnL
- Open positions: Entry, Mark, Size, Margin, Liquidation
- TP/SL levels with 🔒/🔓 protection status
- ROE % (PnL / Margin)
- Auto-refresh every 3 seconds

## Telegram Notifications

- **TP trades**: ✅ Sent with PnL details
- **SL trades**: 🔇 Suppressed (no spam)
- **SL protection move**: 🔒 Sent when SL is moved to profit zone
- **Errors**: Silent for routine API issues (43001, 40890)

## File Layout

```
~/bitget_bot/
├── spread_scalper.py       # Main bot
├── bitget_client.py        # API wrapper
├── telegram_notify.py      # Telegram sender
├── dashboard.py            # Live dashboard
├── spread_learnings.json   # Saved trades + stats
├── spread_scalper.log      # Runtime log (rotated per start)
├── STRATEGY.md             # This file
└── backtest/               # Historical 1H candle data
    ├── BTCUSDT_1H.json
    ├── ETHUSDT_1H.json
    ├── SOLUSDT_1H.json
    └── XRPUSDT_1H.json
```
