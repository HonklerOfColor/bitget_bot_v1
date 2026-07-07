# DS-SpreadScalper

High-frequency spread-scalping bot for **Bitget USDT-M Futures** (demo/live).

Places post-only limit orders inside the bid-ask spread, sets ATR-based TP/SL on the exchange, and monitors positions in a 2-second loop across BTC, ETH, SOL, and XRP.

## Features

- Post-only maker entries inside the spread
- ATR-based TP/SL via Bitget `place-pos-tpsl`
- PnL-protected SL move at +1% ROE
- Trade logging and per-symbol stats (`spread_learnings.json`)
- Telegram notifications (TP only)
- Terminal dashboard for live position overview

## Setup

```bash
git clone https://github.com/HonklerOfColor/bitget_bot.git
cd bitget_bot
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp config.example.py config.py          # add Bitget API keys
cp scalper_config.example.py scalper_config.py  # optional: Telegram
```

Edit `config.py`:
- Set `API_KEY`, `SECRET_KEY`, `PASSPHRASE`
- Keep `DEMO_MODE = True` for Bitget demo account

## Usage

```bash
# Start the bot
python3 spread_scalper.py

# Live dashboard (separate terminal)
python3 dashboard.py
```

## Project Structure

| File | Description |
|------|-------------|
| `spread_scalper.py` | Main trading bot |
| `bitget_client.py` | Bitget Futures API wrapper |
| `dashboard.py` | Terminal UI for open positions |
| `telegram_notify.py` | Telegram alerts |
| `STRATEGY.md` | Strategy documentation |
| `TRADING_RULES.md` | Trading rules |
| `backtest/` | Historical 1H candle data |

## Strategy

See [STRATEGY.md](STRATEGY.md) for entry logic, TP/SL rules, and risk management.

## Security

- **Never commit** `config.py` or `scalper_config.py` — they contain API keys
- Use Bitget demo mode for testing (`DEMO_MODE = True`)
- Rotate keys if they were ever exposed

## Disclaimer

For educational purposes. Trading futures involves substantial risk. Use at your own risk.
