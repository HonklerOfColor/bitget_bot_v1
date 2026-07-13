"""
Aktualisiere Backtest-Daten mit frischen 1H Kerzen von Bitget API
"""
import sys, json
sys.path.insert(0, '.')
import bitget_client

SYMBOLS = ["SOLUSDT", "BTCUSDT", "ETHUSDT"]

for symbol in SYMBOLS:
    print(f"📡 {symbol}: Lade 1000 frische 1H Kerzen...")
    candles = bitget_client.get_candles(symbol, "1H", 1000)
    print(f"   → {len(candles)} Kerzen erhalten")
    
    # Speichern
    outpath = f"/Users/andreas/bitget_bot_v1/backtest/{symbol}_1H.json"
    with open(outpath, "w") as f:
        json.dump(candles, f)
    
    from datetime import datetime
    first_dt = datetime.fromtimestamp(int(candles[0][0])/1000)
    last_dt = datetime.fromtimestamp(int(candles[-1][0])/1000)
    print(f"   → {first_dt.strftime('%Y-%m-%d %H:%M')} bis {last_dt.strftime('%Y-%m-%d %H:%M')}")
    
print("\n✅ Alle Daten aktualisiert!")
