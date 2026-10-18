# TRADING RULES — DS-SpreadScalper (V1)

Stand 18.10.2026. Durchgesetzt in `spread_scalper.py` (`trading_rules.py` für die reinen Entscheidungen).
Geprüft gegen die geschlossenen Trades von Subaccount `at-HNKHNKHNK`.

**Neue Entries sind pausiert** (18.10.2026). Default, solange `config.py` nicht ausdrücklich `ENTRIES_PAUSED = False` setzt. Der Loop bleibt an, loggt die Pause stündlich und schickt einmal Telegram. Offene Positionen werden weiter verwaltet.

## 0. Pause

24-Monats-Backtest der Long-Regeln: Netto −302 USDT, kein OOS-robuster Edge (vier von fünf Sechsmonatsfenstern negativ). Monte-Carlo auf den Live-Trades: P(Gewinn) 1,8 %, seit 14.09. 9,7 %, Ruin-Risiko 29–37 % in 500 Trades. Deshalb keine neuen Orders. Details: [backtest/V1_EDGE_STUDY.md](backtest/V1_EDGE_STUDY.md).

## 1. Nur Long — seit 2026-08-26

`LONG_ONLY = True`. Shorts laufen zusätzlich in eine Order-Grenze (`place_limit_order` gibt `SKIP_LONG_ONLY` zurück, der Short-Zweig im Loop bricht ab).

Im Code seit Commit 2026-09-03. Davor, am 27.08., ein Short (BNBUSDT, Netto −1.00). Danach kein Short mehr.

## 2. Keine neuen Entries in Verluststunden — Europe/Vienna

Stunden **0, 1, 2, 3, 4, 14, 17, 20, 22**.

- 2–4, 14, 17, 20, 22 seit 2026-09-03
- 0 und 1 seit 2026-09-14

Die Stunde kommt aus `zoneinfo` / `Europe/Vienna`, nicht aus `time.localtime()`. Der Live-Host lief schon auf Wien (nach dem 14.09. null Entries in diesen Stunden bei 152 Trades). Ein UTC-Prozess hätte die Sperre um eine bzw. zwei Stunden verschoben.

## 3. 24h-Time-Stop für Verlierer — seit 2026-09-14

Alter ≥ 24h und unrealisierte ROE ≤ 0 → Market-Close. Gewinner (ROE > 0) laufen weiter.

Nach dem Stichtag: 4 Verlierer bei exakt 24h geschlossen (Überzug 0,01–0,04 Minuten, Netto −1.94). Drei Gewinner blieben länger offen (24,5h / 46h / 51h, Netto +0.90) — das ist die Regel, kein Leck.

Der Stop läuft vor dem Orderbuch- und TP-Check. Vorher hat ein fehlendes Orderbuch oder ein leeres `tp_prices` den Stop per `continue` ausgelassen. Ein fehlgeschlagener Close wird im nächsten Zyklus erneut versucht und erst nach Erfolg verbucht.

Fehlender Mark-Preis schließt keine Position mit positivem PnL.

## 4. 12h-Break-Even-SL — Learning 14.09.

Alter ≥ 12h, ROE ≥ 0,3%, Peak-ROE noch unter 3% (Trailing nicht aktiv) → SL auf den Entry, nur wenn der neue SL enger ist und mindestens ~0,15% vom Mark entfernt liegt.

Live zum 16.10. 08:12 Wien: ETHUSDT long, 13,2h, ROE +0,97%, SL = Entry. BNBUSDT long, 13,2h, ROE −0,70%, SL bleibt der Chart-SL — die Position ist nicht im Plus.

`get_position` reicht `stopLoss` durch, damit der Vergleich den Exchange-SL sieht und einen bereits engeren SL nicht auf den Entry weitet.

## 5. Hebel 3× — seit 2026-07-13

`LEVERAGE = 3`. Nicht 5×. Der 5×-Wert aus dem Initial-Commit und aus `dashboard.py` ist historisch; der Backtest-Kommentar und `STRATEGY.md` setzen 3×, die offenen Positionen dieses Subaccounts laufen mit 3×. Das ist regelkonform.

Das Dashboard hat ROE mit hartem 5× gerechnet und damit die Margin zu klein angesetzt. Es nutzt jetzt `marginSize` der Position, sonst den Hebel der Position, sonst 3×.

## 6. Fees und Post-Only — Vorschlag, nicht umgesetzt

Entries sind bereits Post-Only (`force: post_only`). Ungefüllte Orders werden nach 60s gecancelt. Das ist derselbe Gedanke wie `MAKER_ENTRY` plus Fill-Timeout in `bitget_bot_v3` (dort nur der Entry, der TP bleibt ein Trigger).

Gemessene Fee auf diesem Subaccount: **0,080% des Entry-Notionals**, Median über alle Monate. Das ist Maker 0,02% plus Taker 0,06%. Der Exit (place-pos-tpsl und Market-Close für SL, TP-Touch und Time-Stop) ist der Taker-Teil.

| Zeitraum | Trades | Brutto | Fees | Netto |
| --- | ---: | ---: | ---: | ---: |
| Gesamt | 587 | −2.93 | −14.75 | −18.16 |
| ab 26.08. | 210 | −1.70 | −6.02 | −8.24 |
| September | 120 | +4.01 | −3.51 | +0.17 |

Im September hat die Fee den Brutto-Edge fast vollständig aufgefressen. Ein Maker-Exit (0,02% + 0,02% statt 0,08%) hätte die September-Fees grob halbiert und das Netto auf etwa +1.9 USDT gehoben. Über alle Monate bleibt der Brutto negativ — Maker-Exits allein drehen die Strategie nicht ins Plus.

Nicht umgesetzt, weil ein Post-Only-Limit anstelle des Trigger-TP einen Gap durch das Ziel verpassen kann. Sicherer Folge-Schritt, separat zu reviewen: Reduce-Only-Limit auf den TP legen, Trigger-SL behalten, Trigger-TP ein Stück dahinter als Backup stehen lassen. Das ist eine Order-/Exit-Änderung.

## 7. Edge-Studie 2024-10 bis 2026-10 — kein robuster Vorteil

Details: [backtest/V1_EDGE_STUDY.md](backtest/V1_EDGE_STUDY.md). Kurzfassung: die echte Long-Reversal-Logik (Stundensperre, 24h-Stop, 12h-Break-Even, 3×, Maker-Entry 0,02 % + Taker-Exit 0,06 %) hat über 24 Monate nach Kosten einen negativen Erwartungswert. Vier Sechsmonatsfenster bis Juni 2026 sind negativ, das Fenster ab Juli 2026 ist bei der Baseline +0.64 USDT (PF 1.01). EMA, Maker-TP, engerer Time-Stop und Re-Entry-Sperre bleiben in den historischen Fenstern negativ. Deshalb ändert dieser Stand keine Entry-, SL- oder TP-Regel. Empfehlung: V1 nicht weiter mit Größe fahren, bis eine andere These außerhalb dieser Parameter-Nachbarschaft vorliegt.

## Was bewusst nicht geändert wurde

- Hebel bleibt 3×. Die offenen 3×-Positionen sind kein Verstoß.
- Shorts vor dem 03.09. und die eine Short-Position vom 27.08. sind Historie, kein offener Code-Pfad.
- Entries in Stunde 1 zwischen dem 03.09. und dem 14.09. (3 Trades) liegen vor der Nachtsperre.
- 40 Trades über 24h (Netto −12.55) sind überwiegend vor dem Time-Stop. Danach schließen Verlierer bei 24h.
