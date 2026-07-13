#!/usr/bin/env python3
"""📊 DS-SpreadScalper Dashboard — Terminal-UI."""
import sys, os, time, json
from datetime import datetime

sys.path.insert(0, "/Users/andreas/bitget_bot_v1")
import bitget_client as client

REFRESH = 3
LEVERAGE = 5
STATE_PATH = "/Users/andreas/bitget_bot_v1/bot_state.json"

def read_bot_state():
    try:
        with open(STATE_PATH) as f:
            return json.load(f) or {}
    except:
        return {}

def clr():
    os.system('clear 2>/dev/null || printf "\\033c"')

def pnl_c(pnl):
    if pnl > 0: return f"\033[32m{pnl:+.4f}\033[0m"
    if pnl < 0: return f"\033[31m{pnl:+.4f}\033[0m"
    return f"{pnl:+.4f}"

def roe_c(pct):
    if pct > 0: return f"\033[32m{pct:+.1f}%\033[0m"
    if pct < 0: return f"\033[31m{pct:+.1f}%\033[0m"
    return f"{pct:+.1f}%"

def main():
    cycle = 0
    while True:
        cycle += 1
        now = datetime.now().strftime("%H:%M:%S")
        
        # Daten holen
        try:
            raw = client._get('/api/v2/mix/position/all-position', {'productType': 'USDT-FUTURES'})
            positions = raw.get('data') or []
        except:
            positions = []
        
        try:
            bal = client._get('/api/v2/mix/account/accounts', {'productType': 'USDT-FUTURES'})
            bal_data = bal.get('data') or []
            usdt = bal_data[0] if bal_data else {}
            equity = float(usdt.get('accountEquity', 0))
            upnl = float(usdt.get('unrealizedPL', 0))
        except:
            equity, upnl = 0, 0
        
        clr()
        print(f"\033[1m  📊 DS-SpreadScalper Dashboard\033[0m        {now}")
        print(f"  {'─'*70}")
        print(f"  Wallet: \033[36m{equity:>8.2f}\033[0m USDT  |  Unreal. PnL: {pnl_c(upnl)}")
        print(f"  {'─'*70}")
        
        if not positions:
            print(f"\n  \033[90mKeine offenen Positionen\033[0m\n")
        else:
            bot_state = read_bot_state()
            for p in positions:
                sym   = p.get('symbol', '?')
                side  = p.get('holdSide', '?')
                entry = float(p.get('openPriceAvg', 0))
                mark  = float(p.get('markPrice', 0))
                size  = float(p.get('total', 0))
                pnl   = float(p.get('unrealizedPL', 0))
                # SL/TP vom Bot-State (trackt place-pos-tpsl), sonst Exchange-Fallback
                bdata = bot_state.get(sym, {})
                sl    = bdata.get('sl') or p.get('stopLoss')
                tp    = bdata.get('tp') or p.get('takeProfit')
                liq   = float(p.get('liquidationPrice', 0))
                margin = size * mark / LEVERAGE
                roe   = pnl / margin * 100 if margin > 0 else 0
                
                prot = (side=='long' and sl and float(sl) < mark) or \
                       (side=='short' and sl and float(sl) > mark)
                icon = "\033[32m🔒\033[0m" if prot else "\033[33m🔓\033[0m"
                
                safe_text = " \033[32m✅ Trade SL abgesichert!\033[0m" if prot and roe > 1 else ""
                print(f"\n  \033[1m{sym:9s}\033[0m {side.upper():6s} {icon}{safe_text}")
                print(f"  Entry: \033[36m{entry:>10.2f}\033[0m  Mark: \033[36m{mark:>10.2f}\033[0m  ROE: {roe_c(roe)}")
                print(f"  Size:  {size:>8.4f}    Margin: {margin:>8.2f}   Liq: {liq:>10.2f}")
                print(f"  PnL:   {pnl_c(pnl)} USDT")
                sl_str = f"{float(sl):>10.1f} {icon if prot else ''}" if sl else "---"
                tp_str = f"{float(tp):>10.1f}" if tp else "---"
                print(f"  SL:    {sl_str:>20}   TP: {tp_str:>10}")
        
        print(f"\n  {'─'*70}")
        print(f"  \033[90m[refresh: {REFRESH}s | Cyc {cycle}]  Ctrl+C zum Beenden\033[0m")
        
        try:
            time.sleep(REFRESH)
        except KeyboardInterrupt:
            print("\n  👋 Dashboard beendet")
            break

if __name__ == "__main__":
    main()
