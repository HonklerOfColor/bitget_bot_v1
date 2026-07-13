#!/usr/bin/env python3
"""Fetch positions from one bot's API. Usage: python3 fetch_bot.py <bot_dir>"""
import sys, json, importlib.util
from pathlib import Path

bot_dir = Path(sys.argv[1])
sys.path.insert(0, str(bot_dir))

config = importlib.util.module_from_spec(importlib.util.spec_from_file_location("config", bot_dir / "config.py"))
spec = importlib.util.spec_from_file_location("config", bot_dir / "config.py")
spec.loader.exec_module(config)

client = importlib.util.module_from_spec(importlib.util.spec_from_file_location("client", bot_dir / "bitget_client.py"))
spec2 = importlib.util.spec_from_file_location("client", bot_dir / "bitget_client.py")
spec2.loader.exec_module(client)

positions = []
# V1 Bot hat XRP hartkodiert (fehlt in config.py)
all_symbols = list(set(
    (sym_entry["symbol"] if isinstance(sym_entry, dict) else sym_entry)
    for sym_entry in config.SYMBOLS
) | {"SOLUSDT", "BTCUSDT", "ETHUSDT", "XRPUSDT"})

for sym in all_symbols:
    pos = client.get_position(sym)
    if pos and float(pos.get("total", 0)) > 0:
        sl_raw = pos.get("stopLoss", "")
        tp_raw = pos.get("takeProfit", "")
        positions.append({
            "symbol": sym,
            "side": pos.get("holdSide", "?"),
            "size": float(pos.get("total", 0)),
            "entry": float(pos.get("openPriceAvg", 0)),
            "mark": float(pos.get("markPrice", 0)),
            "pnl": float(pos.get("unrealizedPL", 0)),
            "margin": float(pos.get("marginSize", 0)),
            "sl": float(sl_raw) if sl_raw and sl_raw != "" else None,
            "tp": float(tp_raw) if tp_raw and tp_raw != "" else None,
        })

print(json.dumps(positions, default=str))
