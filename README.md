# DS-SpreadScalper

Automatisierter Spread-Scalping-Bot für **Bitget USDT-M Futures** (Demo oder Live).

Der Bot platziert Post-Only-Limit-Orders innerhalb des Bid/Ask-Spreads, setzt ATR-basierte Take-Profit- und Stop-Loss-Orders auf der Exchange und überwacht alle 2 Sekunden die Positionen in BTC, ETH, SOL und XRP.

## Schnellstart

```bash
git clone https://github.com/HonklerOfColor/bitget_bot.git
cd bitget_bot
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp config.example.py config.py
cp scalper_config.example.py scalper_config.py   # optional, für Telegram
```

In `config.py` die Bitget-API-Keys eintragen. Für Demo-Trading `DEMO_MODE = True` lassen.

```bash
python3 spread_scalper.py      # Bot starten
python3 dashboard.py           # Live-Übersicht (separates Terminal)
```

## Dateien

| Datei | Beschreibung |
|-------|--------------|
| `spread_scalper.py` | Hauptbot |
| `bitget_client.py` | Bitget-API-Wrapper |
| `dashboard.py` | Terminal-Dashboard |
| `telegram_notify.py` | Telegram-Benachrichtigungen |
| `STRATEGY.md` | Strategie im Detail |

## Hinweise

- `config.py` und `scalper_config.py` enthalten Secrets und werden **nicht** ins Repo committed.
- Ausführliche Strategie- und Risikoregeln: [STRATEGY.md](STRATEGY.md)

**Disclaimer:** Nur zu Bildungszwecken. Futures-Trading ist mit erheblichem Risiko verbunden.
