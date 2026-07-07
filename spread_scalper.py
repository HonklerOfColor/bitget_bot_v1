"""
DS-SpreadScalper — High-Frequency Spread Scalping Bot
======================================================
Strategie: Spread-Penetration on Bitget Futures
- Liest Orderbook alle 2s
- Platziert Limit-Orders knapp über Bid / unter Ask
- Wenn eine Seite gefüllt wird, wird die andere zum TP
- Viele kleine Trades, Spread-Capture
"""
import sys, os, time, json, threading
from datetime import datetime
from loguru import logger

sys.path.insert(0, "/Users/andreas/bitget_bot")
import bitget_client as client

# ── Config ───────────────────────────────────────────────────────────────────
SYMBOLS = ["SOLUSDT", "BTCUSDT", "ETHUSDT", "XRPUSDT"]
LOOP_INTERVAL = 2          # Alle 2 Sekunden
LEVERAGE = 5  # muss mit exchange übereinstimmen (API zeigt leverage=5)
OFFSET_PCT = 0.0001        # 0.01% Offset (hauchduenn, um im Orderbook zu bleiben)
MAX_SPREAD_PCT = 0.005     # Max 0.5% Spread (sonst zu volatil)
TELEGRAM_ON = True

# Symbol-spezifische Mindestmengen (updatet fuer 5 USDT Minimum UND Bitget Min-Qty Limits)
MIN_SIZES = {
    "BTCUSDT": 0.001,   # min_qty=0.001 → ~$63 (groesser als 5 USDT min)
    "ETHUSDT": 0.05,    # min_qty erhöht auf 0.05 (war 0.01, zu klein) → ~$150
    "SOLUSDT": 0.2,     # min_qty erhöht auf 0.2 (war 0.1) → ~$16
    "XRPUSDT": 5.0,     # min_qty erhöht auf 5.0 (war 2.0) → ~$3.50
}

# 📊 Multi-Level TP/SL Strategy
TP_LEVELS = [
    {"pct": 0.15, "atr_mult": 3.0, "label": "TP1"},   # 15% @ 3.0× ATR
    {"pct": 0.35, "atr_mult": 6.0, "label": "TP2"},   # 35% @ 6.0× ATR
    {"pct": 0.50, "atr_mult": 9.0, "label": "TP3"},   # 50% @ 9.0× ATR
]
SL_BASE_MULT = 0.30     # 0.30× ATR baseline
SL_MAX_MULT = 0.60      # 0.60× ATR bei Verlustserie (proportional zu SL_BASE)
BREAKEVEN_PNL_PCT = 0.02  # +2% unrealized → move SL to entry
LOSS_STREAK_THRESHOLD = 3  # Nach 3 Verlusten: scale SL bis 0.60×

# Preis-Rundung (Stellen nach Komma)
PRICE_PLACES = {
    "SOLUSDT": 3,
    "BTCUSDT": 1,
    "ETHUSDT": 1,
    "XRPUSDT": 4,
}

# Logger
logger.remove()
logger.add(sys.stderr, level="INFO", format="<green>{time:HH:mm:ss}</green> | {message}")
logger.add("spread_scalper.log", level="INFO", format="{time:YYYY-MM-DD HH:mm:ss} | {message}")


class SpreadScalper:
    def __init__(self):
        self.running = True
        self.positions = {}   # symbol -> {
                              #   "side": "long"/"short"
                              #   "entry": price, "size": qty, "mark_price": current
                              #   "tp_level": 0-3 (welcher TP gerade aktiv)
                              #   "tp_prices": [tp1, tp2, tp3]
                              #   "sl_price": current SL
                              #   "loss_streak": consecutive losses
                              # }
        self.pending_orders = {}  # symbol -> {"buy_id": "...", "sell_id": "...", "ts": timestamp}
        self.last_pnl_check = {}  # symbol -> timestamp (10s-TP1-Cooldown)
        
        # 🧠 Trade Learner
        self.trade_log = []  # Abgeschlossene Trades
        self.stats = {}      # Statistik pro Symbol
        self.load_learnings()
        
        logger.info("=" * 50)
        logger.info("🚀 DS-SpreadScalper gestartet")
        logger.info(f"   Symbole: {SYMBOLS}")
        logger.info(f"   Intervall: {LOOP_INTERVAL}s | Hebel: {LEVERAGE}x")
        logger.info(f"   Offset: {OFFSET_PCT*100:.3f}%")
        self.print_stats()
        logger.info("=" * 50)
        
        # Startup: Prüfe bestehende Positionen auf fehlende TP/SL
        self._check_existing_tpsl()
    
    def _check_existing_tpsl(self):
        """Setze TP/SL fuer bestehende Positionen ohne — nutze ATR-basierte Multi-Level TP"""
        for symbol in SYMBOLS:
            try:
                pos_raw = client.get_position(symbol)  # Volle raw API-Response
                if not pos_raw or float(pos_raw.get("total", 0)) == 0:
                    continue
                has_tp = bool(pos_raw.get("takeProfit", ""))
                has_sl = bool(pos_raw.get("stopLoss", ""))
                if not has_tp or not has_sl:
                    logger.info(f"🔧 Startup-Check für {symbol}: TP={has_tp}, SL={has_sl}")
                    
                    # Berechne ATR für TP/SL Levels
                    atr = self.calc_atr(symbol)
                    if not atr:
                        logger.warning(f"  ⏭️  ATR nicht verfügbar für {symbol}, skip")
                        continue
                    
                    entry_price = float(pos_raw.get("openPriceAvg", pos_raw.get("markPrice", 0)))
                    side = pos_raw["holdSide"]
                    
                    # Berechne Multi-Level TP/SL
                    levels = self.calc_tp_sl_levels(symbol, entry_price, side, atr)
                    if not levels:
                        continue
                    
                    logger.info(f"  📍 {side.upper()}: Entry={entry_price}, ATR={atr:.4f}")
                    logger.info(f"     TP1={levels['tp_prices'][0]}, TP2={levels['tp_prices'][1]}, TP3={levels['tp_prices'][2]}, SL={levels['sl']}")
                    
                    self.set_tpsl_for_position(symbol, side, levels["tp_prices"], levels["sl"], float(pos_raw["total"]))
            except Exception as e:
                logger.error(f"  ❌ {symbol}: {type(e).__name__}: {e}")
    
    def load_learnings(self):
        """Lade gespeicherte Learnings"""
        try:
            import json
            with open("spread_learnings.json") as f:
                data = json.load(f)
                self.trade_log = data.get("trades", [])
                self.stats = data.get("stats", {})
            logger.info(f"🧠 {len(self.trade_log)} Trades geladen")
        except:
            self.trade_log = []
            self.stats = {}
    
    def save_learnings(self):
        """Speichere Learnings"""
        try:
            import json
            with open("spread_learnings.json", "w") as f:
                json.dump({"trades": self.trade_log[-100:], "stats": self.stats}, f, indent=2)
        except:
            pass
    
    def calc_atr(self, symbol, period=14):
        """Berechne ATR (Average True Range) aus letzten Candles"""
        try:
            # Fetch 14 candles (1h timeframe für bessere Stabilität)
            klines = client.get_candles(symbol, "1H", limit=period + 1)
            if not klines or len(klines) < period:
                return None
            
            trs = []
            for i in range(1, len(klines)):
                h = float(klines[i][2])      # high
                l = float(klines[i][3])      # low
                c = float(klines[i-1][4])    # prev close
                tr = max(h - l, abs(h - c), abs(l - c))
                trs.append(tr)
            
            atr = sum(trs[-period:]) / period
            return atr
        except Exception as e:
            logger.debug(f"  ⏭️  ATR calc ({symbol}): {e}")
            return None
    
    def calc_tp_sl_levels(self, symbol, entry_price, side, atr):
        """Berechne Multi-Level TP/SL Preise basierend auf ATR"""
        if not atr or atr <= 0:
            return None
        
        tp_prices = []
        price_places = PRICE_PLACES.get(symbol, 2)
        
        for level in TP_LEVELS:
            if side == "long":
                tp = round(entry_price + atr * level["atr_mult"], price_places)
            else:  # short
                tp = round(entry_price - atr * level["atr_mult"], price_places)
            tp_prices.append(tp)
        
        # SL: dynamisch basierend auf loss_streak
        loss_streak = self.stats.get(symbol, {}).get("consecutive_losses", 0)
        sl_mult = min(SL_BASE_MULT + (loss_streak / LOSS_STREAK_THRESHOLD) * (SL_MAX_MULT - SL_BASE_MULT), SL_MAX_MULT)
        
        if side == "long":
            sl = round(entry_price - atr * sl_mult, price_places)
        else:  # short
            sl = round(entry_price + atr * sl_mult, price_places)
        
        return {"tp_prices": tp_prices, "sl": sl, "atr": atr}
    
    def record_trade(self, symbol, side, entry, exit_price, pnl, reason):
        """Zeichne Trade auf und lerne daraus"""
        trade = {
            "ts": datetime.now().isoformat(),
            "symbol": symbol, "side": side,
            "entry": entry, "exit": exit_price,
            "pnl": round(pnl, 4), "reason": reason,
        }
        self.trade_log.append(trade)
        
        if symbol not in self.stats:
            self.stats[symbol] = {"trades": 0, "wins": 0, "losses": 0,
                                  "total_pnl": 0.0, "consecutive_losses": 0}
        s = self.stats[symbol]
        s["trades"] += 1
        s["total_pnl"] += pnl
        if pnl > 0:
            s["wins"] += 1
            s["consecutive_losses"] = 0
        else:
            s["losses"] += 1
            s["consecutive_losses"] += 1
        
        # Adaptive Anpassungen
        # Adaptive Anpassungen
        if s["consecutive_losses"] >= 3:
            logger.warning(f"🧠 {symbol}: {s['consecutive_losses']}x Verlust — Spread vergroessern")
        if s["consecutive_losses"] >= 5:
            logger.warning(f"🧠 {symbol}: 5x Verlust — Pausiere fuer 5 Min")
        
        # 📱 Telegram (nur bei TP, kein SL-Spam)
        try:
            if reason != "SL":  # SL-Nachrichten unterdrücken
                import telegram_notify as tg
                icon = "🟢" if pnl > 0 else "🔴"
                tg_msg = f"{icon} {symbol} {side.upper()}\\nEntry={entry} → Exit={exit_price}\\n{pnl:+.4f} USDT | {reason}"
                tg.send(tg_msg)
        except Exception as tg_err:
            logger.warning(f"  ⚠️  Telegram error (record_trade): {tg_err}")
        
        self.save_learnings()
        
        icon = "✅" if pnl > 0 else "❌"
        logger.info(f"{icon} TRADE: {symbol} {side} | {pnl:+.4f} USDT | {reason}")
    
    def print_stats(self):
        """Zeige gespeicherte Statistik"""
        if not self.stats:
            return
        logger.info("📊 Lernstatus:")
        for sym, s in sorted(self.stats.items()):
            wr = s["wins"] / max(s["trades"], 1) * 100
            logger.info(f"   {sym}: {s['trades']} Trades | {wr:.0f}% WR | {s['total_pnl']:+.2f} USDT")
    
    def analyze_trade_with_mistral(self, symbol, side, entry, exit_price, pnl, reason):
        """Rufe lokales Mistral fuer Trade-Analyse"""
        try:
            import requests
            prompt = f"Spread-Trade {symbol} {side}: Entry {entry:.4f} -> Exit {exit_price:.4f}, {pnl:+.4f} USDT, {reason}. Warum? (1 Satz)"
            resp = requests.post("http://localhost:11434/api/generate",
                json={"model": "mistral:7b", "prompt": prompt,
                      "stream": False, "temperature": 0.1},
                timeout=15)
            if resp.status_code == 200:
                lesson = resp.json().get("response", "").strip()
                logger.info(f"📖 {lesson}")
                trade = self.trade_log[-1] if self.trade_log else {}
                trade["lesson"] = lesson
                self.save_learnings()
        except:
            pass
    
    def get_depth(self, symbol):
        """Hole Orderbook Depth"""
        try:
            data = client._get("/api/v2/mix/market/merge-depth", {
                "symbol": symbol, "productType": "USDT-FUTURES", "limit": "5"
            })
            if data.get("code") != "00000":
                return None
            d = data.get("data", {})
            bids = d.get("bids", [])
            asks = d.get("asks", [])
            if not bids or not asks:
                return None
            return {
                "bid": float(bids[0][0]),
                "bid_size": float(bids[0][1]),
                "ask": float(asks[0][0]),
                "ask_size": float(asks[0][1]),
                "spread": float(asks[0][0]) - float(bids[0][0]),
                "mid": (float(asks[0][0]) + float(bids[0][0])) / 2,
            }
        except Exception as e:
            logger.debug(f"Depth Error {symbol}: {e}")
            return None
    
    def get_position(self, symbol):
        """Aktuelle Position abrufen"""
        try:
            pos = client.get_position(symbol)
            if pos and float(pos.get("total", 0)) > 0:
                return {
                    "side": pos.get("holdSide", "long"),
                    "size": float(pos["total"]),
                    "entry": float(pos.get("openPriceAvg", 0)),
                    "pnl": float(pos.get("unrealizedPL", 0)),
                    "markPrice": float(pos.get("markPrice", 0)),
                }
        except:
            pass
        return None
    
    def cancel_all_orders(self, symbol):
        """Alle offenen Orders für Symbol löschen"""
        try:
            client.cancel_tpsl_orders(symbol)
        except:
            pass
    
    def place_limit_order(self, symbol, side, price, size):
        """Platziere Limit-Order (Post-Only = Maker)"""
        notional = float(price) * float(size)
        if notional < 4.95:
            logger.warning(f"  ⚠️  {symbol} {side}: size={size} @ ${price} = ${notional:.2f} < $5 — SKIPPE Order")
            return None
        order_side = "buy" if side == "long" else "sell"
        client_oid = f"{symbol}_{side}_{int(time.time()*1000)}_{os.urandom(2).hex()}"
        try:
            # Konvertiere zu Strings — Strings sind SICHER und vermeiden Scientific Notation!
            qty_str = str(float(size))  # "0.001", "0.05", etc
            price_str = str(float(price))
            
            # Log für Debugging
            logger.info(f"  📤 place_limit_order: {symbol} {side} | qty={qty_str} | price={price_str}")
            
            # Sanity-Check
            if not qty_str or qty_str == "0" or qty_str == "0.0":
                logger.warning(f"  ⚠️  {symbol} {side}: qty_str={qty_str} (raw size={size}) — SKIPPE Order (Größe ist 0!)")
                return None
            
            result = client._post("/api/v2/mix/order/place-order", {
                "symbol": symbol,
                "productType": "USDT-FUTURES",
                "marginCoin": "USDT",
                "marginMode": "isolated",
                "side": order_side,
                "tradeSide": "open",           # Pflichtfeld: Positionseröffnung!
                "orderType": "limit",
                "force": "post_only",          # Richtig: "force", nicht "timeInForce"!
                "price": price_str,
                "size": qty_str,
                "clientOid": client_oid,
            })
            code = result.get("code")
            if code == "00000":
                oid = result.get("data", {}).get("orderId", "?")
                logger.info(f"  📄 {side.upper()} Limit @ {price} (ID: {oid[:8]})")
                return oid
            elif code == "40004":  # Post-only would be taker
                logger.debug(f"  ⏭️  {side.upper()} Post-Only abgelehnt (waere Taker)")
            else:
                logger.debug(f"  ❌ {side.upper()} Order: {result.get('msg','?')[:50]}")
        except Exception as e:
            logger.debug(f"  ❌ {side.upper()} Error: {e}")
        return None
    
    def place_market_close(self, symbol, side, size):
        """Schliesse Position per Market-Order"""
        close_side = "sell" if side == "long" else "buy"
        try:
            result = client._post("/api/v2/mix/order/place-order", {
                "symbol": symbol,
                "productType": "USDT-FUTURES",
                "marginCoin": "USDT",
                "marginMode": "isolated",
                "side": close_side,
                "orderType": "market",
                "size": str(size),
                "tradeSide": "close",
            })
            if result.get("code") == "00000":
                logger.success(f"  ✅ Position CLOSED (Market)")
                return True
            # Flash-Close Fallback
            flash = client.close_all_positions(symbol)
            if flash.get("code") == "00000":
                logger.success(f"  ✅ Flash-Close")
                return True
        except:
            pass
        return False
    
    def _place_stop_order(self, symbol, side, trigger_price, size, label):
        """Platziere eine Stop-Market Close-Order (TP oder SL)."""
        close_side = "sell" if side == "long" else "buy"
        try:
            qty_str = str(float(size))
            trigger_str = str(float(trigger_price))
            client_oid = f"tpsl_{symbol}_{label}_{int(time.time()*1000)}_{os.urandom(2).hex()}"
            
            result = client._post("/api/v2/mix/order/place-order", {
                "symbol": symbol,
                "productType": "USDT-FUTURES",
                "marginCoin": "USDT",
                "marginMode": "isolated",
                "side": close_side,
                "tradeSide": "close",
                "orderType": "market",
                "size": qty_str,
                "triggerPrice": trigger_str,
                "triggerType": "mark_price",
                "clientOid": client_oid,
            })
            
            code = result.get("code")
            if code == "00000":
                oid = result.get("data", {}).get("orderId", "?")
                logger.info(f"  📄 {label} @ {trigger_price} (ID: {str(oid)[:8]})")
                return oid
            else:
                logger.debug(f"  ⚠️  {label}: {result.get('msg','?')[:60]}")
                return None
        except Exception as e:
            logger.debug(f"  ❌ {label}: {e}")
            return None
    
    def set_tpsl_for_position(self, symbol, side, tp_prices, sl_price, size):
        """Setze TP/SL via place-pos-tpsl (UTA-kompatibel, kein 40890-Limit).
        tp_prices: [tp1, tp2, tp3] — nutzt tp1 als einzigen TP.
        """
        try:
            self._cancel_plan_orders(symbol)
        except:
            pass
        self._fallback_tpsl(symbol, side, tp_prices, sl_price, size)
    
    def _cancel_plan_orders(self, symbol):
        """Cancel ALL TPSL-Orders für ein Symbol (ohne ID-Liste)."""
        try:
            client.cancel_tpsl_orders(symbol)
        except:
            pass
    
    def _fallback_tpsl(self, symbol, side, tp_prices, sl_price, size):
        """Fallback: place-pos-tpsl (nur 1 TP)."""
        try:
            tp_price = tp_prices[0]
            result = client._post("/api/v2/mix/order/place-pos-tpsl", {
                "symbol": symbol,
                "productType": "USDT-FUTURES",
                "marginCoin": "USDT",
                "holdSide": side,
                "stopSurplusTriggerPrice": f"{tp_price}",
                "stopSurplusTriggerType": "mark_price",
                "stopSurplusExecutePrice": f"{tp_price}",
                "stopLossTriggerPrice": f"{sl_price}",
                "stopLossTriggerType": "mark_price",
                "stopLossExecutePrice": f"{sl_price}",
            })
            if result.get("code") == "00000":
                logger.info(f"  ⚠️  Fallback TP1={tp_price} SL={sl_price}")
                # Update statt Überschreiben — sl_protected Flag erhalten
                if symbol not in self.positions:
                    self.positions[symbol] = {}
                self.positions[symbol].update({
                    "tp_level": 0,
                    "tp_prices": tp_prices,
                    "sl": sl_price,
                    "original_size": size,
                })
                # sl_protected initialisieren falls nicht gesetzt
                self.positions[symbol].setdefault("sl_protected_level", 0)
            else:
                logger.warning(f"  ⚠️  Fallback Fehler: {result.get('msg','?')}")
        except Exception as e:
            logger.warning(f"  ⚠️  Fallback Exception: {e}")
    
    def _notify_sl_move(self, symbol, side, pnl_pct, new_sl):
        """Sende Telegram bei SL-Verschiebung."""
        try:
            import telegram_notify as tg
            msg = f"🔒 {symbol} {side}\nPnL={pnl_pct:.1f}% → SL auf {new_sl}"
            tg.send(msg)
        except:
            pass
    
    def get_order_status(self, order_id, symbol):
        """Prüfe ob Order gefüllt wurde"""
        try:
            result = client._get("/api/v2/mix/order/detail", {
                "symbol": symbol,
                "productType": "USDT-FUTURES",
                "orderId": order_id,
            })
            if result.get("code") == "00000":
                data = result.get("data", {})
                state = data.get("state", "")
                if state == "filled":
                    return "filled", float(data.get("priceAvg", data.get("price", 0)))
                elif state in ("partial_fill", "partial_cancel"):
                    return "filled", float(data.get("priceAvg", 0))
                return "open", 0
        except:
            pass
        return "unknown", 0
    
    def run_cycle(self):
        """Haupt-Loop alle 2 Sekunden"""
        for symbol in SYMBOLS:
            try:
                pos = self.get_position(symbol)
                
                if pos:
                    # ── Position existiert → warte auf TP oder schliesse ──
                    depth = self.get_depth(symbol)
                    if not depth:
                        continue
                    
                    tp_prices = self.positions.get(symbol, {}).get("tp_prices")
                    
                    # Wenn TP noch nicht gesetzt, versuche erneut
                    if tp_prices is None:
                        # TP/SL nochmal versuchen
                        logger.debug(f"🔄 {symbol}: TP noch nicht gesetzt, versuche erneut")
                        atr = self.calc_atr(symbol)
                        if atr:
                            levels = self.calc_tp_sl_levels(symbol, float(pos["entry"]), pos["side"], atr)
                            if levels:
                                logger.info(f"  🎯 Multi-Level TP retry: {levels['tp_prices']}, SL: {levels['sl']}")
                                self.set_tpsl_for_position(symbol, pos["side"], levels["tp_prices"], levels["sl"], float(pos["size"]))
                                # Bei Erfolg in positions speichern
                                if symbol in self.positions:
                                    self.positions[symbol].update(levels)
                        continue
                    
                    # Ersten TP-Preis für Client-seitiges Monitoring nutzen
                    tp_price = tp_prices[0]
                    # Marktpreis (markPrice) statt Bid/Ask für SL/TP-Prüfung,
                    # da Bid/Ask durch Spread-Spikes zu Fehlauslösungen führen.
                    # Exchange verwendet Last-Price für Stop Loss.
                    mark_price = float(pos.get("markPrice", (depth["bid"] + depth["ask"]) / 2))
                    
                    if pos["side"] == "long":
                        # LONG: TP wenn Marktpreis >= TP-Preis
                        # SL wenn Marktpreis <= SL-Preis
                        sl_price = self.positions.get(symbol, {}).get("sl", depth["bid"])
                        if mark_price >= tp_price:
                            logger.info(f"🎯 {symbol} LONG TP erreicht @ {mark_price:.2f} >= {tp_price:.2f}")
                            exit_price = mark_price
                            actual_pnl = (exit_price - pos["entry"]) * pos["size"]
                            self.record_trade(symbol, "long", pos["entry"], exit_price, actual_pnl, "TP")
                            self.place_market_close(symbol, pos["side"], pos["size"])
                            self.positions.pop(symbol, None)
                        elif mark_price <= sl_price:
                            logger.warning(f"🛑 {symbol} LONG SL hit @ {mark_price:.2f} <= {sl_price:.2f}")
                            actual_pnl = (sl_price - pos["entry"]) * pos["size"]
                            self.record_trade(symbol, "long", pos["entry"], sl_price, actual_pnl, "SL")
                            self.place_market_close(symbol, pos["side"], pos["size"])
                            self.positions.pop(symbol, None)
                    else:
                        # SHORT: TP wenn Marktpreis <= TP-Preis
                        # SL wenn Marktpreis >= SL-Preis
                        sl_price = self.positions.get(symbol, {}).get("sl", depth["ask"])
                        if mark_price <= tp_price:
                            logger.info(f"🎯 {symbol} SHORT TP erreicht @ {mark_price:.2f} <= {tp_price:.2f}")
                            exit_price = mark_price
                            actual_pnl = (pos["entry"] - exit_price) * pos["size"]
                            self.record_trade(symbol, "short", pos["entry"], exit_price, actual_pnl, "TP")
                            self.place_market_close(symbol, pos["side"], pos["size"])
                            self.positions.pop(symbol, None)
                        elif mark_price >= sl_price:
                            logger.warning(f"🛑 {symbol} SHORT SL hit @ {mark_price:.2f} >= {sl_price:.2f}")
                            actual_pnl = (pos["entry"] - sl_price) * pos["size"]
                            self.record_trade(symbol, "short", pos["entry"], sl_price, actual_pnl, "SL")
                            self.place_market_close(symbol, pos["side"], pos["size"])
                            self.positions.pop(symbol, None)
                    
                    # ── PnL-basierte SL-Protection (alle 10s prüfen) ──
                    # Bei PnL > 1% wird der SL auf Entry (Breakeven) gezogen,
                    # damit der Trade risikofrei weiterlaufen kann.
                    # TPs bleiben unverändert (Gewinne laufen lassen).
                    if pos and tp_prices:
                        now = time.time()
                        last = self.last_pnl_check.get(symbol, 0)
                        if now - last >= 10:
                            self.last_pnl_check[symbol] = now
                            try:
                                unrealized = float(pos.get("pnl", 0))
                                entry = float(pos["entry"])
                                notional = float(pos["size"]) * float(pos.get("markPrice", depth["mid"]))
                                if notional <= 0:
                                    notional = float(pos["size"]) * entry * LEVERAGE
                                # ROE = PnL / Margin (wie Bitget UI)
                                margin = notional / LEVERAGE if notional > 0 else 0
                                pnl_pct = unrealized / margin * 100 if margin > 0 else 0
                                
                                if pnl_pct > 1.0:
                                    prot_level = self.positions.get(symbol, {}).get("sl_protected_level", 0)
                                    
                                    current_sl = self.positions.get(symbol, {}).get("sl")
                                    mark = float(pos.get("markPrice", depth["mid"]))
                                    
                                    if current_sl is not None:
                                        # sl_protected_level: 0=kein, 1=Entry±1%, 2=Entry±2%
                                        prot_level = self.positions.get(symbol, {}).get("sl_protected_level", 0)
                                        
                                        if pnl_pct > 4.0 and prot_level < 2:
                                            # Ab 4% → SL auf Entry±2% verschieben
                                            target_mult = 1.02 if pos["side"] == "long" else 0.98
                                            fallback_mult = 0.996 if pos["side"] == "long" else 1.004
                                            fallback_mark = mark * fallback_mult
                                            new_sl = round(entry * target_mult, PRICE_PLACES.get(symbol, 2))
                                            
                                            if pos["side"] == "long":
                                                if new_sl >= mark:
                                                    new_sl = round(fallback_mark, PRICE_PLACES.get(symbol, 2))
                                                if new_sl > current_sl and new_sl < mark:
                                                    self.positions[symbol]["sl"] = new_sl
                                                    self.positions[symbol]["sl_protected_level"] = 2
                                                    self.set_tpsl_for_position(symbol, "long", tp_prices, new_sl, float(pos["size"]))
                                                    logger.info(f"🔒 {symbol} LONG: PnL={pnl_pct:.1f}% → SL auf {new_sl} (Entry+2%)")
                                                    self._notify_sl_move(symbol, "LONG", pnl_pct, new_sl)
                                            else:  # short
                                                if new_sl <= mark:
                                                    new_sl = round(fallback_mark, PRICE_PLACES.get(symbol, 2))
                                                if new_sl < current_sl and new_sl > mark:
                                                    self.positions[symbol]["sl"] = new_sl
                                                    self.positions[symbol]["sl_protected_level"] = 2
                                                    self.set_tpsl_for_position(symbol, "short", tp_prices, new_sl, float(pos["size"]))
                                                    logger.info(f"🔒 {symbol} SHORT: PnL={pnl_pct:.1f}% → SL auf {new_sl} (Entry-2%)")
                                                    self._notify_sl_move(symbol, "SHORT", pnl_pct, new_sl)
                                                    
                                        elif pnl_pct > 1.0 and prot_level == 0:
                                            if pos["side"] == "long":
                                                new_sl = round(entry * 1.01, PRICE_PLACES.get(symbol, 2))
                                                if new_sl >= mark:
                                                    new_sl = round(mark * 0.998, PRICE_PLACES.get(symbol, 2))
                                                if new_sl > current_sl and new_sl < mark:
                                                    self.positions[symbol]["sl"] = new_sl
                                                    self.positions[symbol]["sl_protected_level"] = 1
                                                    self.set_tpsl_for_position(symbol, "long", tp_prices, new_sl, float(pos["size"]))
                                                    logger.info(f"🔒 {symbol} LONG: PnL={pnl_pct:.1f}% → SL auf {new_sl} (Entry+1% od. Markt-0.2%)")
                                                    self._notify_sl_move(symbol, "LONG", pnl_pct, new_sl)
                                                elif new_sl <= current_sl:
                                                    logger.debug(f"🔒 {symbol}: SL bereits auf/über {current_sl}")
                                            else:  # short
                                                new_sl = round(entry * 0.99, PRICE_PLACES.get(symbol, 2))
                                                if new_sl <= mark:
                                                    new_sl = round(mark * 1.002, PRICE_PLACES.get(symbol, 2))
                                                if new_sl < current_sl and new_sl > mark:
                                                    self.positions[symbol]["sl"] = new_sl
                                                    self.positions[symbol]["sl_protected_level"] = 1
                                                    self.set_tpsl_for_position(symbol, "short", tp_prices, new_sl, float(pos["size"]))
                                                    logger.info(f"🔒 {symbol} SHORT: PnL={pnl_pct:.1f}% → SL auf {new_sl} (Entry-1% od. Markt+0.2%)")
                                                    self._notify_sl_move(symbol, "SHORT", pnl_pct, new_sl)
                                                elif new_sl >= current_sl:
                                                    logger.debug(f"🔒 {symbol}: SL bereits auf/unter {current_sl}")
                                        elif prot_level > 0:
                                            logger.debug(f"🔒 {symbol}: SL bereits geschützt (Level {prot_level})")
                            except Exception as pnl_e:
                                logger.debug(f"  ⚠️ PnL-Check {symbol}: {pnl_e}")
                    
                else:
                    depth = self.get_depth(symbol)
                    if not depth:
                        continue
                    
                    spread_pct = depth["spread"] / depth["mid"]
                    if spread_pct > MAX_SPREAD_PCT:
                        logger.debug(f"⏭️  {symbol}: Spread zu gross ({spread_pct*100:.3f}%)")
                        continue
                    
                    # ❗ Prüfe ob schon eine Order schwebt — keine neue platzieren!
                    pending = self.pending_orders.get(symbol)
                    if pending:
                        # Alte Orders (>60s) cenceln und neu versuchen
                        if time.time() - pending.get("ts", 0) > 60:
                            for otype in ("buy_id", "sell_id"):
                                oid = pending.get(otype)
                                if oid:
                                    try:
                                        client._post("/api/v2/mix/order/cancel-order", {
                                            "symbol": symbol, "productType": "USDT-FUTURES",
                                            "marginCoin": "USDT", "orderId": oid
                                        })
                                    except:
                                        pass
                            self.pending_orders.pop(symbol, None)
                            logger.info(f"🔄 {symbol}: Stale Order gecancelt, versuche neu")
                        else:
                            logger.debug(f"⏳ {symbol}: Order noch offen — warte ({int(time.time()-pending.get('ts',0))}s)")
                            continue
                    
                    # Berechne Einstiegspreise (innerhalb des Spreads)
                    price_place = PRICE_PLACES.get(symbol, 2)
                    spread_size = depth["spread"]
                    # BUY bei 30% vom Bid, SELL bei 70% vom Bid (innerhalb Spread)
                    bid_price = depth["bid"] + (spread_size * 0.3)
                    ask_price = depth["bid"] + (spread_size * 0.7)
                    # Stelle sicher dass BUY < SELL
                    bid_price = min(bid_price, ask_price - (spread_size * 0.1))
                    bid_offset = round(bid_price, price_place)
                    ask_offset = round(ask_price, price_place)
                    order_size = MIN_SIZES.get(symbol, 0.1)
                    # Size dynamisch an Minimum 5 USDT anpassen
                    min_notional = 5 / depth["mid"]
                    if min_notional > order_size:
                        order_size = min_notional
                    order_size = round(order_size, 4)  # 4 Dezimalstellen
                    notional_usd = order_size * depth["mid"]
                    if notional_usd < 4.95:  # Floating-point-Safety: 4.95 statt 5.0
                        logger.warning(f"⚠️  {symbol}: size={order_size} Qty={order_size:.4f} @ ${depth['mid']:.4f} = ${notional_usd:.2f} < $5 — skippe")
                        continue
                    
                    # TEMPORARY FIX: Bitget Demo Account ist auf HEDGE mode
                    # Platziere NUR LONG oder SHORT (zufällig), nicht beides
                    import random
                    direction = random.choice(["long", "short"])  # Zufällig wählen
                    
                    if direction == "long":
                        buy_id = self.place_limit_order(symbol, "long", round(bid_offset, price_place), order_size)
                        sell_id = None
                        logger.info(f"  🎲 {symbol}: LONG Order (Hedge Mode)")
                    else:
                        buy_id = None
                        sell_id = self.place_limit_order(symbol, "short", round(ask_offset, price_place), order_size)
                        logger.info(f"  🎲 {symbol}: SHORT Order (Hedge Mode)")
                    
                    if buy_id or sell_id:
                        self.pending_orders[symbol] = {"buy_id": buy_id, "sell_id": sell_id, "ts": time.time()}
                    
                    # Warte kurz und prüfe welche gefüllt wurde
                    time.sleep(0.5)
                    
                    if buy_id:
                        status, price = self.get_order_status(buy_id, symbol)
                        if status == "filled":
                            # BUY gefuellt -> LONG Position
                            logger.success(f"📈 {symbol} LONG gefuellt @ {price}")
                            
                            # Berechne ATR für Multi-Level TP/SL
                            atr = self.calc_atr(symbol)
                            if atr:
                                levels = self.calc_tp_sl_levels(symbol, price, "long", atr)
                                if levels:
                                    logger.info(f"  🎯 Multi-Level TP: {levels['tp_prices']}, SL: {levels['sl']}")
                                    self.set_tpsl_for_position(symbol, "long", levels["tp_prices"], levels["sl"], order_size)
                                else:
                                    # Fallback auf spread-basiert
                                    tp = round(price + depth["spread"], PRICE_PLACES.get(symbol, 2))
                                    sl = round(price - depth["spread"] * 0.5, PRICE_PLACES.get(symbol, 2))
                                    self.set_tpsl_for_position(symbol, "long", [tp, tp, tp], sl, order_size)
                            else:
                                tp = round(price + depth["spread"], PRICE_PLACES.get(symbol, 2))
                                sl = round(price - depth["spread"] * 0.5, PRICE_PLACES.get(symbol, 2))
                                self.set_tpsl_for_position(symbol, "long", [tp, tp, tp], sl, order_size)
                            
                            if symbol in self.positions:
                                self.positions[symbol].update({
                                    "side": "long", "entry": price, "size": order_size,
                                    "mark_price": depth["mid"],
                                })
                            else:
                                self.positions[symbol] = {
                                    "side": "long", "entry": price, "size": order_size,
                                    "mark_price": depth["mid"],
                                }
                            if sell_id:
                                client._post("/api/v2/mix/order/cancel-order", {
                                    "symbol": symbol, "productType": "USDT-FUTURES",
                                    "marginCoin": "USDT", "orderId": sell_id
                                })
                            continue

                    if sell_id:
                        status, price = self.get_order_status(sell_id, symbol)
                        if status == "filled":
                            # SELL gefuellt -> SHORT Position
                            logger.success(f"📉 {symbol} SHORT gefuellt @ {price}")
                            
                            # Berechne ATR für Multi-Level TP/SL
                            atr = self.calc_atr(symbol)
                            if atr:
                                levels = self.calc_tp_sl_levels(symbol, price, "short", atr)
                                if levels:
                                    logger.info(f"  🎯 Multi-Level TP: {levels['tp_prices']}, SL: {levels['sl']}")
                                    self.set_tpsl_for_position(symbol, "short", levels["tp_prices"], levels["sl"], order_size)
                                else:
                                    # Fallback auf spread-basiert
                                    tp = round(price - depth["spread"], PRICE_PLACES.get(symbol, 2))
                                    sl = round(price + depth["spread"] * 0.5, PRICE_PLACES.get(symbol, 2))
                                    self.set_tpsl_for_position(symbol, "short", [tp, tp, tp], sl, order_size)
                            else:
                                tp = round(price - depth["spread"], PRICE_PLACES.get(symbol, 2))
                                sl = round(price + depth["spread"] * 0.5, PRICE_PLACES.get(symbol, 2))
                                self.set_tpsl_for_position(symbol, "short", [tp, tp, tp], sl, order_size)
                            
                            # MERGE statt OVERWRITE — TP/SL-Tracking erhalten
                            if symbol in self.positions:
                                self.positions[symbol].update({
                                    "side": "short", "entry": price, "size": order_size,
                                    "mark_price": depth["mid"],
                                })
                            else:
                                self.positions[symbol] = {
                                    "side": "short", "entry": price, "size": order_size,
                                    "mark_price": depth["mid"],
                                }
                            if buy_id:
                                client._post("/api/v2/mix/order/cancel-order", {
                                    "symbol": symbol, "productType": "USDT-FUTURES",
                                    "marginCoin": "USDT", "orderId": buy_id
                                })
                            continue
                    
                    # Kein Fill → cancel beide
                    if buy_id:
                        client._post("/api/v2/mix/order/cancel-order", {
                            "symbol": symbol, "productType": "USDT-FUTURES",
                            "marginCoin": "USDT", "orderId": buy_id
                        })
                    if sell_id:
                        client._post("/api/v2/mix/order/cancel-order", {
                            "symbol": symbol, "productType": "USDT-FUTURES",
                            "marginCoin": "USDT", "orderId": sell_id
                        })
                
            except Exception as e:
                logger.error(f"❌ {symbol} Error: {e}")
    
    def run(self):
        """Main Loop"""
        cycle = 0
        while self.running:
            try:
                cycle += 1
                start = time.time()
                
                self.run_cycle()
                
                # Alle 15 Zyklen (~30s) Prüfe TP/SL auf offenen Positionen und closed positions
                if cycle % 15 == 0:
                    self._verify_tpsl_on_positions()
                    self._check_closed_positions()
                
                elapsed = time.time() - start
                if elapsed < LOOP_INTERVAL:
                    time.sleep(LOOP_INTERVAL - elapsed)
                    
            except KeyboardInterrupt:
                logger.info("⏹️  Bot gestoppt")
                break
            except Exception as e:
                logger.error(f"❌ Main Loop Error: {e}")
                time.sleep(LOOP_INTERVAL)
    
    def _verify_tpsl_on_positions(self):
        """Prüfe alle offenen Positionen auf korrekte TP/SL (Spread-Scalping Logik)"""
        for symbol in SYMBOLS:
            try:
                pos = self.get_position(symbol)
                if not pos:
                    continue
                
                entry_price = float(pos.get("openPriceAvg", 0))
                depth = self.get_depth(symbol)
                if not depth or not entry_price:
                    continue
                
                spread = depth["spread"]
                price_place = PRICE_PLACES.get(symbol, 2)
                
                # Berechne korrektes TP/SL fuer Spread-Scalping
                mark_price = float(pos.get("markPrice", entry_price))
                
                if pos["side"] == "long":
                    # LONG: TP muss > MarkPrice (nicht > Entry!)
                    # SL muss < Entry
                    correct_tp = round(mark_price + spread, price_place)
                    correct_sl = round(min(entry_price - spread * 0.5, mark_price * 0.985), price_place)
                else:
                    # SHORT: TP muss < MarkPrice
                    # SL muss > Entry
                    correct_tp = round(mark_price - spread, price_place)
                    correct_sl = round(max(entry_price + spread * 0.5, mark_price * 1.015), price_place)
                
                # Prüfe ob TPSL bereits gesetzt wurde — sonst neu setzen
                stored_tps = self.positions.get(symbol, {}).get("tp_prices")
                if stored_tps is None:
                    logger.info(f"🔄 {symbol}: TP/SL fehlt, setze neu (Entry={entry_price:.2f}, Spread={spread:.4f})")
                    self.set_tpsl_for_position(symbol, pos["side"], [correct_tp, correct_tp, correct_tp], correct_sl, pos["size"])
                    
            except Exception as e:
                logger.debug(f"  ⏭️  {symbol} verify: {e}")
    
    def _check_closed_positions(self):
        """Überprüfe ob Positionen geschlossen wurden (TP/SL Hit) und sende Telegram mit P&L"""
        for symbol in SYMBOLS:
            try:
                pos_raw = client.get_position(symbol)
                
                # Wenn Position = 0, war sie offen und ist jetzt closed
                if not pos_raw or float(pos_raw.get("total", 0)) == 0:
                    # Prüfe ob wir diese Position in self.positions tracked haben
                    if symbol in self.positions:
                        old_pos = self.positions[symbol]
                        
                        # Berechne P&L
                        entry_price = old_pos.get("entry", 0)
                        if pos_raw:
                            mark_price = float(pos_raw.get("markPrice", entry_price))
                        else:
                            # Position wurde vom Exchange geschlossen (TP/SL)
                            # Verwende aktuellen Ticker-Preis als Schätzung
                            try:
                                ticker = client.get_ticker(symbol)
                                mark_price = float(ticker.get("last", entry_price))
                            except:
                                mark_price = entry_price
                        side = old_pos.get("side", "?")
                        size = old_pos.get("size", 0)
                        
                        if side == "long":
                            pnl = (mark_price - entry_price) * size
                            exit_reason = "TP hit" if mark_price > entry_price else "SL hit"
                        else:  # short
                            pnl = (entry_price - mark_price) * size
                            exit_reason = "TP hit" if mark_price < entry_price else "SL hit"
                        
                        # Telegram Nachricht mit P&L (nur TP, kein SL-Spam)
                        try:
                            if "TP" in exit_reason:  # Nur TP-Benachrichtigungen
                                import telegram_notify as tg
                                icon = "🟢" if pnl > 0 else "🔴" if pnl < 0 else "⚪"
                                reason_emoji = "✅" if "TP" in exit_reason else "🛑"
                                tg_msg = f"{icon} {symbol} {side.upper()} CLOSED\n{pnl:+.4f} USDT | {reason_emoji} {exit_reason}"
                                tg.send(tg_msg)
                            else:
                                icon = "⚪"
                            logger.info(f"{icon} Position closed: {symbol} {side.upper()} | {pnl:+.4f} USDT | {exit_reason}")
                        except Exception as tg_err:
                            logger.warning(f"  ⚠️  Telegram send failed: {tg_err}")
                        
                        # Remove position tracking
                        del self.positions[symbol]
                        
            except Exception as e:
                logger.debug(f"  ⏭️  {symbol} closed-check: {e}")


if __name__ == "__main__":
    bot = SpreadScalper()
    bot.run()
