# V1 Edge-Studie (2024-10-01 bis 2026-10-18)

Öffentliche Bitget-1h-Kerzen, BTCUSDT / ETHUSDT / BNBUSDT, je 17 846 Kerzen.
Kontraktgrößen wie live: BTC 0.001, ETH 0.01, BNB 0.05. Hebel 3× nur für die ROE-Rechnung.
Kosten: Maker-Entry 0,02 %, Taker-Exit 0,06 %, Entry-Slippage 1 bp, Exit-Slippage 2 bp.
Funding aus der öffentlichen API nur ab 2026-07-20 (die API gibt nicht mehr her). Im OOS-Fenster sind das −1.4 USDT, klein gegen Fees und Stops. Drawdown in USDT auf einer Start-Equity von 128.

Zwei Entry-Modelle in `backtest/v1_engine.py`:

- **intra:** wie der laufende Bot. Vorherige Stunde rot, laufende Stunde handelt über ihren Open, Fill am Open. 131 von 169 Live-Entries ab 14.09.2026 liegen in derselben Stunde. Der Live-Entry sitzt im Median 0,026 % vom Stunden-Open (0,20 % vom Close).
- **confirmed:** die Umkehrstunde muss geschlossen grün sein, Fill am nächsten Open. Vollständig kausal. Das ist die Basis für IS/OOS, damit kein Intra-Bar-Pfad die Auswahl schönt.

IS = Entries vor 2026-07-01. OOS = Entries ab 2026-07-01, nicht für die Vorauswahl benutzt. Ein Trade darf über die Grenze zu Ende laufen.

## Live-Abgleich ab 2026-09-14

Long only, BTC/ETH/BNB, Subaccount `at-HNKHNKHNK`.

| Quelle | Trades | Netto USDT | Brutto | Fees |
| --- | ---: | ---: | ---: | ---: |
| Live | 169 | −6.56 | −1.64 | −4.60 |
| Backtest intra | 228 | −9.63 | −0.11 | −8.79 |
| Backtest confirmed | 165 | −2.42 | +4.57 | −6.42 |

Intra handelt öfter, weil jeder Signal-Fill angenommen wird. Live verpasst Post-Only-Orders. Richtung und Größenordnung stimmen. Der Brutto der echten Trades ist schon leicht negativ, die Fee macht den Rest.

## Vorher: Baseline confirmed

| Fenster | Trades | Netto | E[Trade] | PF | Winrate | Max-DD |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| IS vor 2026-07 | 3460 | −302.28 | −0.0874 | 0.74 | 52.7 % | 312.18 |
| IS Hälfte A | 1730 | −106.21 | −0.0614 | 0.81 | 55.5 % | 121.06 |
| IS Hälfte B | 1730 | −196.07 | −0.1133 | 0.67 | 49.8 % | 206.58 |
| OOS ab 2026-07 | 545 | +0.64 | +0.0012 | 1.01 | 51.9 % | 17.16 |
| Gesamt | 4005 | −301.64 | −0.0753 | 0.76 | 52.6 % | 312.53 |

Walk-Forward, Baseline confirmed, Netto USDT:

| Fenster | Trades | Netto | PF | Max-DD |
| --- | ---: | ---: | ---: | ---: |
| 2024-10 .. 2025-03 | 1076 | −75.66 | 0.79 | 87.68 |
| 2025-04 .. 2025-09 | 936 | −52.08 | 0.82 | 71.04 |
| 2025-10 .. 2026-03 | 989 | −137.34 | 0.64 | 155.43 |
| 2026-04 .. 2026-06 | 459 | −37.20 | 0.67 | 41.38 |
| 2026-07 .. 2026-10 | 545 | +0.64 | 1.01 | 17.16 |

Vier von fünf Fenstern sind negativ. Das letzte ist ein halber USDT bei PF 1.01.

Intra (live-nah) ist in jedem Fenster schlechter, gesamt −575 USDT, OOS −11.03.

## Wo das IS-Geld verloren wird

- Exit: SL −624 USDT (2567 Trades), Time-Stop −165 (232, Winrate 0 %), TP +487 (661).
- Haltedauer: 2–6h ist das einzige positive IS-Bucket (+77). Unter 2h −79, 12–24h −140, über 24h −149.
- Symbol, gleiche Kontraktgröße wie live: BTC −183, ETH −62, BNB −57. Auf gleiche 20-USDT-Nominale über die ganze Historie bleibt der Erwartungswert negativ (−0.027 pro Trade, PF 0.79). BTC ist nicht nur wegen der größeren Nominale schlecht.
- Donnerstag ist im IS der schwächste Wochentag (−142), aber das sind viele einzelne BTC-Stops über Monate, kein einzelner Crash. Kein Wochentagsfilter.

## Geprüft und nicht übernommen

Auswahlregel, vor dem OOS-Blick: IS-Erwartungswert besser als die Baseline, Drawdown nicht größer, und beide IS-Hälften besser als die Baseline-Hälften. Die OOS-Spalte ist danach einmal gerechnet. Keine Variante hat im IS einen positiven Erwartungswert. Die OOS-Pluszeichen sitzen auf 21 Monaten Verlust.

| Variante | IS Netto | IS PF | IS E | OOS Netto | OOS PF | OOS E | OOS Max-DD |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Baseline confirmed | −302.28 | 0.74 | −0.087 | +0.64 | 1.01 | +0.001 | 17.16 |
| EMA20, nur über EMA | −170.06 | 0.75 | −0.087 | +6.68 | 1.11 | +0.020 | 10.91 |
| EMA50 | −108.09 | 0.81 | −0.062 | +5.34 | 1.09 | +0.017 | 11.75 |
| Maker-TP statt Taker-TP | −280.33 | 0.76 | −0.081 | +4.74 | 1.05 | +0.009 | 15.88 |
| 6h keine Re-Entry nach Verlust | −237.78 | 0.74 | −0.086 | +9.06 | 1.11 | +0.020 | 11.20 |
| EMA20 + Maker-TP | −154.69 | 0.78 | −0.079 | +9.67 | 1.15 | +0.029 | 10.27 |
| EMA20 + 12h-Time-Stop | −150.31 | 0.78 | −0.070 | +8.91 | 1.14 | +0.024 | 8.74 |

Dieselbe Nachbarschaft im Walk-Forward (Netto): EMA20, EMA50, Maker-TP, Re-Entry 6h und EMA20+Maker sind in allen vier Fenstern bis Juni 2026 negativ und nur im letzten Fenster leicht positiv. TP 2.0/3.0 und SL 0.6/1.2 ändern das IS-Ergebnis praktisch nicht (−295 bis −302). Ein ATR-Deckel auch nicht.

Auf gleiche 20-USDT-Nominale, ganze Historie: EMA50 ist mit −32 USDT und PF 0.88 der kleinste Verlust, nicht ein Vorteil.

## Schluss

Die Reversal-Long-Logik hat nach Kosten keinen stabilen positiven Erwartungswert. Der Brutto über 24 Monate ist selbst ohne Fee negativ (−143 USDT confirmed, intra noch schwächer). Eine halbierte Fee durch Maker-Exits reicht nicht. Der Bot soll nicht mit einer größeren Size oder einem nachfitten Filter weiterlaufen. Eine andere These bräuchte ein anderes Signal, nicht eine neue Schwelle um 2.5×ATR und die Wien-Stunden.

Nicht geändert im Live-Code: Entry, SL, TP, Time-Stop, Break-Even, Hebel. `EMAFILTER` bleibt unverdrahtet, weil das Zuschalten die historischen Fenster nicht ins Plus dreht.
