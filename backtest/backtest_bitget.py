#!/usr/bin/env python3
"""
DS-SpreadScalper Bitget Backtest — 1 Jahr Bitget-Daten, 10+ Strategie-Varianten
==============================================================================
Simuliert exakt die Bot-Logik auf 1H Kerzen:
- Funding-Signal basierte Richtungsentscheidung (Contrarian)
- Spread-Penetration (30/70)
- Chart-basierter SL (20 Kerzen H/L + 0.1%), Fallback 1.5×ATR
- Single TP 4×ATR (100%)
- ROE-Trailing ab 3% Peak, 2% unter Peak
- Funding-Rate-Filter (MAX_FUNDING_RATE=0.0005)
- 5× Hebel, Maker Fee 0.02%
"""
import sys, json, math, os, time
from datetime import datetime, timezone
from collections import defaultdict

sys.path.insert(0, '/Users/andreas/bitget_bot_v1')
import bitget_client

# ── Basis-Konstanten ──
SYMBOLS = ["SOLUSDT", "BTCUSDT", "ETHUSDT", "XRPUSDT"]
MAKER_FEE = 0.0002
TAKER_FEE = 0.0006
BREAKEVEN_PNL_PCT = 0.03

MIN_SIZES = {"BTCUSDT": 0.001, "ETHUSDT": 0.05, "SOLUSDT": 0.2, "XRPUSDT": 5}
PRICE_PLACES = {"SOLUSDT": 3, "BTCUSDT": 1, "ETHUSDT": 1, "XRPUSDT": 4}

# Ergebnis-Speicher
RESULT_FILE = os.path.join(os.path.dirname(__file__), "backtest_bitget_results.json")

# ── Helper ──
def calc_atr(candles, idx, period=14):
    if idx < period:
        return None
    trs = []
    for i in range(idx - period + 1, idx + 1):
        h = float(candles[i][2])
        l = float(candles[i][3])
        pc = float(candles[i - 1][4])
        tr = max(h - l, abs(h - pc), abs(l - pc))
        trs.append(tr)
    return sum(trs) / len(trs)


def calc_chart_sl(candles, idx, entry_price, side):
    """Chart-basierter SL: letzte 20 Kerzen H/L + 0.1% Buffer"""
    if idx < 20:
        return None
    price_places = 2
    # Use candles before current entry candle for SL calculation
    window_start = max(0, idx - 20)
    highs = [float(candles[k][2]) for k in range(window_start, idx)]
    lows = [float(candles[k][3]) for k in range(window_start, idx)]
    if not highs or not lows:
        return None
    if side == "short":
        highest = max(highs)
        sl = round(highest * 1.001, price_places)  # 0.1% über Hoch
        atr = calc_atr(candles, idx)
        if atr and sl < entry_price + atr * 0.5:
            sl = round(entry_price + atr * 0.5, price_places)
        return sl
    else:  # long
        lowest = min(lows)
        sl = round(lowest * 0.999, price_places)  # 0.1% unter Tief
        atr = calc_atr(candles, idx)
        if atr and sl > entry_price - atr * 0.5:
            sl = round(entry_price - atr * 0.5, price_places)
        return sl


def decide_direction_funding(candles, idx, funding_rates_ts):
    """Funding-basierte Richtungsentscheidung mit Funding-Rate-Filter"""
    if not funding_rates_ts:
        return None  # Keine Daten -> skip
    candle_ts = int(candles[idx][0])
    # Finde die nächstgelegene Funding Rate vor diesem Candle
    fr = None
    for ts, rate in reversed(funding_rates_ts):
        if ts <= candle_ts:
            fr = rate
            break
    if fr is None:
        return None
    
    FUNDING_SIGNAL_THRESHOLD = 0.0001  # 0.01%
    MAX_FUNDING_RATE = 0.0005          # 0.05%
    
    # Funding-Filter: extrem hohes Funding -> nicht shorten
    if fr > MAX_FUNDING_RATE:
        return None  # Zu bullish, kein Short
    if fr < -MAX_FUNDING_RATE:
        return None  # Zu bearish, kein Long
    
    # Contrarian Signal
    if fr > FUNDING_SIGNAL_THRESHOLD:
        return "short"  # Crowd long -> SHORT
    elif fr < -FUNDING_SIGNAL_THRESHOLD:
        return "long"   # Crowd short -> LONG
    return None  # Neutral -> skip


def decide_direction_price(candles, idx):
    """Einfacher Preis-Action Heuristic (für Perioden ohne Funding-Daten)"""
    if idx < 5:
        return "short"  # Default
    # Trend über letzte 5 Kerzen
    c5 = float(candles[idx-5][4])
    c0 = float(candles[idx][4])
    if c0 > c5:
        return "short"  # Bullisch -> Contrarian SHORT
    else:
        return "long"   # Bärisch -> Contrarian LONG


def decide_direction_alternating(candles, idx):
    """Einfach abwechselnd, basierend auf vorheriger Kerze"""
    if idx < 1:
        return "short"
    prev_o = float(candles[idx-1][1])
    prev_c = float(candles[idx-1][4])
    if prev_c <= prev_o:
        return "long"   # fallend -> LONG erwartet Erholung
    else:
        return "short"  # steigend -> SHORT erwartet Fall


# ── Funding-Daten laden ──
def fetch_funding_rates(symbol):
    """Lade verfügbare Funding Rates (~3 Monate)"""
    all_rates = []
    for page in range(1, 5):
        params = {
            'symbol': symbol,
            'productType': 'USDT-FUTURES',
            'pageNo': str(page),
            'pageSize': '100',
        }
        data = bitget_client._get('/api/v2/mix/market/history-fund-rate', params)
        batch = data.get('data', [])
        if not batch:
            break
        for item in batch:
            ts = int(item['fundingTime'])
            rate = float(item['fundingRate'])
            all_rates.append((ts, rate))
        if len(batch) < 100:
            break
    # Sortiere nach Zeit (älteste zuerst)
    all_rates.sort(key=lambda x: x[0])
    return all_rates


# ── Kerzen laden ──
def fetch_year_candles(symbol):
    """1 Jahr 1H-Kerzen von Bitget in Batches von 200."""
    all_candles = []
    now_ms = int(time.time() * 1000)
    year_ago_ms = now_ms - 365 * 24 * 3600 * 1000
    
    for i in range(50):
        start = year_ago_ms + i * 200 * 3600 * 1000
        end = start + 200 * 3600 * 1000 - 1
        params = {
            'symbol': symbol,
            'productType': 'USDT-FUTURES',
            'granularity': '1H',
            'limit': '200',
            'startTime': str(start),
            'endTime': str(end),
        }
        data = bitget_client._get('/api/v2/mix/market/history-candles', params)
        batch = data.get('data', [])
        all_candles.extend(batch)
        if len(batch) < 200:
            break
    return all_candles


# ── Backtest-Logik ──
def run_backtest(symbol, candles, funding_rates, config):
    """
    Simuliere Bot-Strategie auf 1H Kerzen.
    
    config = {
        'direction_mode': 'funding' | 'price_action' | 'alternating' | 'short_only' | 'long_only',
        'tp_atr_mult': 4.0,      # TP ATR Multiplikator
        'sl_type': 'chart' | 'fixed',  # SL-Berechnung
        'sl_atr_mult': 1.5,       # Nur bei sl_type='fixed'
        'leverage': 5,
        'roe_trailing': True,
        'name': 'Variantenname'
    }
    """
    trades = []
    position = None
    direction_mode = config.get('direction_mode', 'funding')
    tp_atr_mult = config.get('tp_atr_mult', 4.0)
    sl_type = config.get('sl_type', 'chart')
    sl_atr_mult = config.get('sl_atr_mult', 1.5)
    leverage = config.get('leverage', 5)
    roe_trailing = config.get('roe_trailing', True)
    
    # Für ROE-Trailing
    peak_pnl_pct = 0
    entry_side = None
    entry_price = 0
    entry_idx = 0
    current_sl = None
    current_tp = None
    pos_size = 0
    
    for i in range(1, len(candles)):
        ts = int(candles[i][0])
        dt = datetime.fromtimestamp(ts / 1000)
        o = float(candles[i][1])
        h = float(candles[i][2])
        l = float(candles[i][3])
        c = float(candles[i][4])
        
        atr = calc_atr(candles, i)
        if atr is None or atr <= 0:
            continue
        
        if position is None:
            # ── Entry entscheiden ──
            if direction_mode == 'short_only':
                side = "short"
            elif direction_mode == 'long_only':
                side = "long"
            elif direction_mode == 'funding':
                if funding_rates:
                    side = decide_direction_funding(candles, i, funding_rates)
                    if side is None:
                        # Fallback auf Price-Action wenn kein Funding-Signal
                        direction_mode_fb = config.get('funding_fallback', 'alternating')
                        if direction_mode_fb == 'price_action':
                            side = decide_direction_price(candles, i)
                        else:
                            side = decide_direction_alternating(candles, i)
                else:
                    side = decide_direction_price(candles, i)
            elif direction_mode == 'price_action':
                side = decide_direction_price(candles, i)
            elif direction_mode == 'alternating':
                side = decide_direction_alternating(candles, i)
            else:
                side = "short"  # Fallback
            
            if side is None:
                continue
            
            # Entry-Preis: Open der aktuellen Kerze
            entry_price = o
            price_places = PRICE_PLACES.get(symbol, 2)
            
            # Größe basierend auf MIN_SIZES (Minimum ~5 USDT)
            size = MIN_SIZES.get(symbol, 0.1)
            notional = entry_price * size
            if notional < 4.95:
                size = 5.0 / entry_price
                size = max(size, MIN_SIZES.get(symbol, 0.1))
            pos_size = size
            
            # SL berechnen
            if sl_type == 'chart':
                sl = calc_chart_sl(candles, i, entry_price, side)
                if sl is None:
                    # Fallback: ATR-basiert
                    if side == "long":
                        sl = round(entry_price - atr * sl_atr_mult, price_places)
                    else:
                        sl = round(entry_price + atr * sl_atr_mult, price_places)
            else:
                # Fixed ATR-basierter SL
                if side == "long":
                    sl = round(entry_price - atr * sl_atr_mult, price_places)
                else:
                    sl = round(entry_price + atr * sl_atr_mult, price_places)
            
            # TP berechnen
            if side == "long":
                tp = round(entry_price + atr * tp_atr_mult, price_places)
            else:
                tp = round(entry_price - atr * tp_atr_mult, price_places)
            
            # Position eröffnen
            position = {
                'side': side,
                'entry': entry_price,
                'size': pos_size,
                'entry_ts': ts,
                'entry_dt': str(dt),
                'entry_idx': i,
            }
            current_sl = sl
            current_tp = tp
            peak_pnl_pct = 0
            entry_side = side
            entry_idx = i
            
        else:
            # ── Offene Position prüfen ──
            side = position['side']
            entry_price = position['entry']
            pos_size = position['size']
            
            # Mark-Preis (Durchschnitt der Kerze)
            mark_price = (h + l + c) / 3
            
            # ROE berechnen für Trailing
            margin = pos_size * entry_price / leverage
            if side == "long":
                unrealized_pnl = (mark_price - entry_price) * pos_size
            else:
                unrealized_pnl = (entry_price - mark_price) * pos_size
            roe_pct = (unrealized_pnl / margin) * 100 if margin > 0 else 0
            
            # Peak-ROE aktualisieren
            if roe_pct > peak_pnl_pct:
                peak_pnl_pct = roe_pct
            
            # ROE-Trailing (nur bei Positionsfortführung, alle ~10 Kerzen = 10h)
            if roe_trailing and peak_pnl_pct >= (BREAKEVEN_PNL_PCT * 100):
                target_roe = peak_pnl_pct - 2.0
                pnl_target = target_roe / 100 * margin
                sl_places = PRICE_PLACES.get(symbol, 2)
                if side == "short":
                    new_sl = round(entry_price - pnl_target / pos_size, sl_places)
                    if new_sl > mark_price and (current_sl is None or new_sl < current_sl):
                        current_sl = new_sl
                else:
                    new_sl = round(entry_price + pnl_target / pos_size, sl_places)
                    if new_sl < mark_price and (current_sl is None or new_sl > current_sl):
                        current_sl = new_sl
            
            # Prüfe TP-Hit (während dieser Kerze)
            tp_hit = False
            sl_hit = False
            exit_price = None
            reason = ""
            
            if side == "long":
                # LONG: TP bei High >= TP, SL bei Low <= SL
                if h >= current_tp:
                    tp_hit = True
                    exit_price = current_tp
                    reason = "TP"
                elif current_sl is not None and l <= current_sl:
                    sl_hit = True
                    exit_price = current_sl
                    reason = "SL"
            else:
                # SHORT: TP bei Low <= TP, SL bei High >= SL
                if l <= current_tp:
                    tp_hit = True
                    exit_price = current_tp
                    reason = "TP"
                elif current_sl is not None and h >= current_sl:
                    sl_hit = True
                    exit_price = current_sl
                    reason = "SL"
            
            if tp_hit or sl_hit:
                # Trade schließen
                if side == "long":
                    gross_pnl = (exit_price - entry_price) * pos_size
                else:
                    gross_pnl = (entry_price - exit_price) * pos_size
                
                # Fees (Taker bei Exit via TP/SL)
                fee_long = entry_price * pos_size * MAKER_FEE  # Entry Post-Only = Maker
                fee_exit = exit_price * pos_size * TAKER_FEE    # Exit = Taker
                total_fee = fee_long + fee_exit
                net_pnl = gross_pnl - total_fee
                
                trades.append({
                    'ts': ts,
                    'date': str(dt),
                    'symbol': symbol,
                    'side': side,
                    'entry': entry_price,
                    'exit': exit_price,
                    'size': pos_size,
                    'margin': margin,
                    'gross_pnl': round(gross_pnl, 4),
                    'fee': round(total_fee, 4),
                    'net_pnl': round(net_pnl, 4),
                    'roe_pct': round((gross_pnl / margin) * 100, 2) if margin > 0 else 0,
                    'reason': reason,
                    'atr_entry': round(atr, 4),
                    'tp_atr_mult': tp_atr_mult,
                    'sl_atr': round(abs(entry_price - (current_sl if current_sl else 0)) / atr, 2) if atr > 0 and current_sl else 0,
                    'duration_candles': i - entry_idx,
                })
                
                position = None
                current_sl = None
                current_tp = None
                peak_pnl_pct = 0
            else:
                # Position fortsetzen
                position['mark_price'] = mark_price
                position['roe_pct'] = roe_pct
                position['peak_roe'] = peak_pnl_pct
    
    return trades


def analyze_trades(trades, name):
    """Berechne Metriken aus Trade-Liste"""
    if not trades:
        return {
            'name': name,
            'total_trades': 0, 'wins': 0, 'losses': 0,
            'win_rate': 0, 'total_pnl': 0, 'profit_factor': 0,
            'max_dd': 0, 'max_dd_pct': 0, 'avg_pnl': 0, 'avg_roe': 0,
            'largest_win': 0, 'largest_loss': 0,
        }
    
    wins = [t for t in trades if t['net_pnl'] > 0]
    losses = [t for t in trades if t['net_pnl'] <= 0]
    total_pnl = sum(t['net_pnl'] for t in trades)
    
    # Equity curve für Max Drawdown
    equity = 0
    peak = 0
    max_dd = 0
    for t in trades:
        equity += t['net_pnl']
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd
    
    profit_factor = sum(t['net_pnl'] for t in wins) / max(abs(sum(t['net_pnl'] for t in losses)), 0.001) if losses else 999
    
    return {
        'name': name,
        'total_trades': len(trades),
        'wins': len(wins),
        'losses': len(losses),
        'win_rate': round(len(wins) / len(trades) * 100, 1) if trades else 0,
        'total_pnl': round(total_pnl, 4),
        'profit_factor': round(profit_factor, 4),
        'max_dd': round(max_dd, 4),
        'max_dd_pct': round(max_dd / max(peak, 1) * 100, 2) if peak > 0 else 0,
        'avg_pnl': round(total_pnl / len(trades), 4) if trades else 0,
        'avg_roe': round(sum(t['roe_pct'] for t in trades) / len(trades), 2) if trades else 0,
        'largest_win': round(max(t['net_pnl'] for t in trades), 4) if trades else 0,
        'largest_loss': round(min(t['net_pnl'] for t in trades), 4) if trades else 0,
    }


def print_results(trades_dict, title):
    """Drucke formatierte Ergebnisse"""
    print(f"\n{'=' * 100}")
    print(f"{title:^100}")
    print(f"{'=' * 100}")
    print(f"  {'Symbol':<10} {'Trades':>7} {'WR':>6} {'PnL':>12} {'PF':>7} {'MaxDD':>10} {'AvgPnL':>10} {'AvgROE':>7}")
    print(f"  {'-'*10} {'-'*7} {'-'*6} {'-'*12} {'-'*7} {'-'*10} {'-'*10} {'-'*7}")
    
    total_trades = 0
    total_pnl = 0
    
    for symbol, trades in trades_dict.items():
        r = analyze_trades(trades, symbol)
        total_trades += r['total_trades']
        total_pnl += r['total_pnl']
        if r['total_trades'] > 0:
            print(f"  {symbol:<10} {r['total_trades']:>7} {r['win_rate']:>5.1f}% {r['total_pnl']:>+11.4f} {r['profit_factor']:>6.2f} {r['max_dd']:>9.2f} {r['avg_pnl']:>+9.4f} {r['avg_roe']:>+6.1f}%")
    
    combined = analyze_trades(
        [t for sym_trades in trades_dict.values() for t in sym_trades], title
    )
    print(f"  {'-'*10} {'-'*7} {'-'*6} {'-'*12} {'-'*7} {'-'*10} {'-'*10} {'-'*7}")
    print(f"  {'Σ GESAMT':<10} {combined['total_trades']:>7} {combined['win_rate']:>5.1f}% {combined['total_pnl']:>+11.4f} {combined['profit_factor']:>6.2f} {combined['max_dd']:>9.2f} {combined['avg_pnl']:>+9.4f} {combined['avg_roe']:>+6.1f}%")
    
    return combined


# ══════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════
if __name__ == '__main__':
    print("=" * 100)
    print(f"{'DS-SpreadScalper Bitget Backtest — 1 Jahr Daten 🚀':^100}")
    print(f"={':'*98}=")
    print(f"{'Alle 4 Symbole · 10+ Varianten · Bitget history-candles API':^100}")
    print(f"{'Start: ' + datetime.now().strftime('%d.%m.%Y %H:%M'):^100}")
    print("=" * 100)
    
    # ── Definierte Varianten ──
    VARIANTS = [
        {
            'direction_mode': 'funding',
            'funding_fallback': 'price_action',
            'tp_atr_mult': 4.0,
            'sl_type': 'chart',
            'leverage': 5,
            'roe_trailing': True,
            'name': 'V1: Funding + Chart-SL + TP 4×ATR (AKTUELL)'
        },
        {
            'direction_mode': 'short_only',
            'tp_atr_mult': 4.0,
            'sl_type': 'chart',
            'leverage': 5,
            'roe_trailing': True,
            'name': 'V2: SHORT-Only + Chart-SL + TP 4×ATR'
        },
        {
            'direction_mode': 'long_only',
            'tp_atr_mult': 4.0,
            'sl_type': 'chart',
            'leverage': 5,
            'roe_trailing': True,
            'name': 'V3: LONG-Only + Chart-SL + TP 4×ATR'
        },
        {
            'direction_mode': 'funding',
            'funding_fallback': 'alternating',
            'tp_atr_mult': 3.0,
            'sl_type': 'chart',
            'leverage': 5,
            'roe_trailing': True,
            'name': 'V4: Funding + Chart-SL + TP 3×ATR'
        },
        {
            'direction_mode': 'funding',
            'funding_fallback': 'alternating',
            'tp_atr_mult': 5.0,
            'sl_type': 'chart',
            'leverage': 5,
            'roe_trailing': True,
            'name': 'V5: Funding + Chart-SL + TP 5×ATR'
        },
        {
            'direction_mode': 'funding',
            'funding_fallback': 'alternating',
            'tp_atr_mult': 6.0,
            'sl_type': 'chart',
            'leverage': 5,
            'roe_trailing': True,
            'name': 'V6: Funding + Chart-SL + TP 6×ATR'
        },
        {
            'direction_mode': 'funding',
            'funding_fallback': 'alternating',
            'tp_atr_mult': 4.0,
            'sl_type': 'fixed',
            'sl_atr_mult': 1.5,
            'leverage': 5,
            'roe_trailing': True,
            'name': 'V7: Funding + Fix-SL 1.5×ATR + TP 4×ATR'
        },
        {
            'direction_mode': 'funding',
            'funding_fallback': 'alternating',
            'tp_atr_mult': 4.0,
            'sl_type': 'fixed',
            'sl_atr_mult': 2.0,
            'leverage': 5,
            'roe_trailing': True,
            'name': 'V8: Funding + Fix-SL 2.0×ATR + TP 4×ATR'
        },
        {
            'direction_mode': 'price_action',
            'tp_atr_mult': 4.0,
            'sl_type': 'chart',
            'leverage': 5,
            'roe_trailing': True,
            'name': 'V9: Price-Action + Chart-SL + TP 4×ATR'
        },
        {
            'direction_mode': 'funding',
            'funding_fallback': 'alternating',
            'tp_atr_mult': 4.0,
            'sl_type': 'chart',
            'leverage': 1,
            'roe_trailing': True,
            'name': 'V10: Funding + Chart-SL + TP 4×ATR (1× Hebel)'
        },
        {
            'direction_mode': 'funding',
            'funding_fallback': 'alternating',
            'tp_atr_mult': 4.0,
            'sl_type': 'chart',
            'leverage': 5,
            'roe_trailing': False,
            'name': 'V11: Funding + Chart-SL + TP 4×ATR (KEIN Trailing)'
        },
        {
            'direction_mode': 'alternating',
            'tp_atr_mult': 4.0,
            'sl_type': 'chart',
            'leverage': 5,
            'roe_trailing': True,
            'name': 'V12: Alternating + Chart-SL + TP 4×ATR'
        },
    ]
    
    # ── Daten laden ──
    all_data = {}
    for symbol in SYMBOLS:
        print(f"\n📡 Lade {symbol} 1H Kerzen (1 Jahr)...", end=" ", flush=True)
        candles = fetch_year_candles(symbol)
        print(f"{len(candles)} Kerzen", end=" ")
        
        print(f"| Lade Funding-Rates...", end=" ", flush=True)
        funding = fetch_funding_rates(symbol)
        print(f"{len(funding)} Einträge", flush=True)
        
        all_data[symbol] = {'candles': candles, 'funding': funding}
        sys.stdout.flush()
    
    # ── Alle Varianten testen ──
    all_variant_results = []
    
    for v_idx, config in enumerate(VARIANTS):
        name = config['name']
        print(f"\n{'─' * 100}")
        print(f"🧪 Teste Variante {v_idx+1}/{len(VARIANTS)}: {name}")
        print(f"{'─' * 100}")
        
        variant_trades = {}
        total_trades = 0
        
        for symbol in SYMBOLS:
            candles = all_data[symbol]['candles']
            funding = all_data[symbol]['funding']
            
            print(f"  {symbol}...", end=" ", flush=True)
            trades = run_backtest(symbol, candles, funding, config)
            variant_trades[symbol] = trades
            total_trades += len(trades)
            print(f"{len(trades)} Trades")
            sys.stdout.flush()
        
        # Ergebnisse drucken
        result = print_results(variant_trades, f"📊 {name}")
        result['config'] = config
        result['trades_by_symbol'] = {s: len(t) for s, t in variant_trades.items()}
        result['all_trades'] = [t for sym_trades in variant_trades.values() for t in sym_trades]
        all_variant_results.append(result)
        
        # Nach jeder 2. Variante kurz warten (Rate-Limiting)
        if (v_idx + 1) % 2 == 0 and v_idx < len(VARIANTS) - 1:
            time.sleep(1)
    
    # ── Ranking ──
    print(f"\n{'=' * 100}")
    print(f"{'🏆 RANKING — Sortiert nach Gesamt-PnL':^100}")
    print(f"{'=' * 100}")
    
    sorted_results = sorted(all_variant_results, key=lambda r: r['total_pnl'], reverse=True)
    
    print(f"  {'Rang':<5} {'Variante':<55} {'Trades':>7} {'WR':>6} {'PnL':>12} {'PF':>7} {'MaxDD':>10} {'AvgROE':>7}")
    print(f"  {'-'*5} {'-'*55} {'-'*7} {'-'*6} {'-'*12} {'-'*7} {'-'*10} {'-'*7}")
    
    for rank, r in enumerate(sorted_results, 1):
        pnl_str = f"{r['total_pnl']:+.4f}"
        if r['total_pnl'] > 0:
            pnl_str = f"+{r['total_pnl']:.4f} USDT"
        else:
            pnl_str = f"{r['total_pnl']:.4f} USDT"
        print(f"  #{rank:<3} {r['name']:<55} {r['total_trades']:>7} {r['win_rate']:>5.1f}% {r['total_pnl']:>+11.4f} {r['profit_factor']:>6.2f} {r['max_dd']:>9.2f} {r['avg_roe']:>+6.1f}%")
    
    # ── Ergebnisse speichern ──
    output = []
    for r in sorted_results:
        clean = {k: v for k, v in r.items() if k != 'all_trades'}
        output.append(clean)
    
    # Trades separat speichern (nur Top-3 Varianten)
    for r in sorted_results[:3]:
        trades_file = os.path.join(os.path.dirname(__file__), 
                                   f'trades_{r["name"].split(":")[0].strip().replace(" ","_").lower()}.json')
        with open(trades_file, 'w') as f:
            json.dump(r['all_trades'], f, indent=2)
    
    with open(RESULT_FILE, 'w') as f:
        json.dump({
            'generated': datetime.now().isoformat(),
            'variants_count': len(VARIANTS),
            'symbols': SYMBOLS,
            'ranking': output,
        }, f, indent=2)
    
    print(f"\n💾 Ergebnisse gespeichert: {RESULT_FILE}")
    print(f"   Trades der Top-3 Varianten einzeln gespeichert.")
    print(f"\n{'=' * 100}")
    print(f"{'✅ Backtest abgeschlossen':^100}")
    print(f"{'=' * 100}")
