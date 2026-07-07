# 📋 TRADING REGELN — DS-TradeBot
# =================================
# Diese Datei definiert die aktiven Trading-Regeln.
# Bearbeite sie manuell und starte den Bot neu um Änderungen zu übernehmen.
#
# Bot:    ds_tradebot.py
# Config: scalper_config.py
# Stand:  2026-07-05

---

## 📡 SIGNAL-ERKENNUNG (alle 15 Minuten)

- **Zeitrahmen:** 1-Minuten-Kerzen, Analyse alle 15 Minuten (900s)
- **Analyse:** 6-Punkt AI Analyse via DeepSeek V4 Flash
- **Indikatoren:**
  - SMA(3/7/15) — Trend-Alignment
  - RSI(14) — Momentum + Extreme Guard
  - MACD(12,26,9) — Momentum-Bestätigung
  - Bollinger Bands(20,2) — Volatilität + BB-Position
  - Volume Ratio (20) — Volumen-Bestätigung
  - Support/Resistance (20 Kerzen) — S/R Levels
- **News-Integration:** Fear & Greed, BTC Dominance, Volume, Schlagzeilen
- **DeepSeek AI:** Bewertet LONG/SHORT/HOLD mit 6-Punkt Analyse
- **Symbole:** BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT (10× Leverage, Isolated)

## 🧠 DEEPSEEK AI 6-PUNKT ANALYSE

1. **Trend Assessment** — SMA-Alignment, Price Action
2. **Momentum Analysis** — RSI, MACD, Histogram
3. **Volatility & Levels** — BB Position, S/R Proximity
4. **Volume Confirmation** — Volume Ratio, Conviction
5. **Risk Factors** — Reversal Points, Adverse Scenarios
6. **Trade Justification** — Why NOW, Confirmation, Invalidation

### Confidence & Sizing
| Confidence | Aktion | Größe |
|-----------|--------|-------|
| **HIGH** | ✅ Trade | 150% (×1.5) |
| **MEDIUM** | ✅ Trade | 100% (×1.0) |
| **LOW** | ❌ Übersprungen | 0% |
| **HOLD** | ❌ Kein Trade | — |

- **Trend Boost:** +20% bei STRONG Trend, -20% bei WEAK
- **DeepSeek V4 Pro:** Nur für strategische Entscheidungen (Risiko-Check, Strategie-Optimierung)

## 🛡️ RSI EXTREME GUARD

- **RSI > 75 oder RSI < 25** → Positionsgröße -30% (Faktor 0.70)
- Verhindert Trades in überkauften/überverkauften Zonen

## 📤 EXIT — GESTAFFELT

| Level | Anteil | Trigger | Überwachung |
|-------|--------|---------|-------------|
| **TP1** 🥇 | **15%** | 3.0× ATR | Exchange (`place-pos-tpsl`) + Client (10s) |
| **TP2** 🥈 | **35%** | 6.0× ATR | Client-seitig (alle 10s) |
| **TP3** 🥉 | **50%** | 9.0× ATR | Client-seitig (alle 10s) |
| **SL** 🛑 | **100%** | 0.4× ATR (dyn. bis 0.8×) | Exchange (`place-pos-tpsl`) + Client (10s) |
| **Breakeven** 🔒 | SL→Entry | bei **+0.3% Kurs** (= 3% ROI bei 10×) | Client-seitig |

## ⏱ MONITORING-ZYKLEN

- Positionen prüfen: alle 10 Sekunden (TP/SL/Breakeven)
- DeepSeek AI Analyse: alle 15 Minuten (900s) mit 1-Min Kerzen
- Trade Learner Cooldown: nach echten Verlusten (120s/300s/600s)
- **Kein Max Consecutive Guard** (entfernt — nur Cooldown)

## ⚙️ RISIKO-MANAGEMENT

- Risk pro Trade: 25 USDT (Basis)
- Max offene Positionen: 3
- Leverage: **10×** (Isolated Margin)
- **MIN_CONFIDENCE_TO_TRADE:** MEDIUM (LOW wird übersprungen)
- **MAX_POSITION_RATIO:** 0.10 (max 10% des Kapitals pro Trade)
- **Positions-Management:** Add/Reduce/Reversal (Allow Reversal: ✅)
- **Account:** ~179 USDT Equity (Demo: Spielgeld)

## 🧠 TRADE LEARNER

- **Bad-Entry-Skip:** Entry-Preis der 3× verloren hat → blockiert
- **Cooldown:** 1 Loss = 120s / 3 Losses = 300s / 5 Losses = 600s Pause
- **Dynamischer SL:** 0.40 → 0.50 → 0.60 → 0.80× ATR nach Verlustserie
- **DeepSeek Analyse:** Post-Trade Analyse jedes geschlossenen Trades
- **Persistenz:** `trade_learnings.json` (überlebt Neustarts)
- **Telegram:** Trade-Learning bei jedem Close 📱

## 📱 TELEGRAM

- **Bot:** @HonkHonkHonkBot
- **Chat-ID:** 507397874
- **Benachrichtigungen:** Trade-Eröffnung, Trade-Close mit DeepSeek Analyse
- **Stündlicher Health Check:** Bot-Status via lokales Mistral 7B

## 🔄 EXCHANGE ORDERS

- **`place-pos-tpsl`:** 1× TP + 1× SL auf Positionsebene (funktioniert zuverlässig)
- **Bitget Cancel-API:** Defekt im UTA-Modus → keine manuellen Löschungen möglich
- **Client-Backup:** Alle 3 TPs + Breakeven werden client-seitig überwacht

## 🧹 HINWEISE

- Stale Exchange-Orders können nicht gelöscht werden (API-Bug) — der Bot ignoriert sie
- Der Bot schließt Positionen immer selbst via Market Order
- Breakeven wird NUR client-seitig aktiviert (kein Exchange-Update)
