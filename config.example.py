"""
Bitget API configuration — copy to config.py and fill in your credentials.

  cp config.example.py config.py

Get API keys from Bitget → API Management.
For demo trading, set DEMO_MODE = True (adds paptrading header).
"""

# ── API ───────────────────────────────────────────────────────────────────────
API_KEY    = "your_bitget_api_key"
SECRET_KEY = "your_bitget_secret_key"
PASSPHRASE = "your_bitget_passphrase"

# ── Symbole (used by legacy strategies; spread_scalper has its own list) ─────
SYMBOLS = [
    {"symbol": "SOLUSDT", "leverage": 5, "strict": False, "min_prob": 0.57, "max_risk_pct": 0.012},
    {"symbol": "BTCUSDT", "leverage": 5, "strict": True,  "min_prob": 0.60, "max_risk_pct": 0.008},
    {"symbol": "ETHUSDT", "leverage": 5, "strict": True,  "min_prob": 0.58, "max_risk_pct": 0.010, "requires_btc_bias": True},
]

# ── Markt ─────────────────────────────────────────────────────────────────────
PRODUCT_TYPE = "USDT-FUTURES"
MARGIN_MODE  = "isolated"
MARGIN_COIN  = "USDT"
TIMEFRAME    = "5m"
TIMEFRAME_4H = "4H"

# ── Bot ───────────────────────────────────────────────────────────────────────
LOOP_INTERVAL = 30
DRY_RUN       = False
LOG_LEVEL     = "INFO"
DEMO_MODE     = True   # paptrading: 1 header for Bitget demo account

# Remaining constants from config.py — only needed for other strategies
HURST_WINDOW = 300
ADX_PERIOD = 14
ADX_THRESHOLD = 22
SWING_LOOKBACK = 20
EQ_LEVEL_TOLERANCE = 0.0015
SWEEP_LOOKBACK = 3
BOS_LOOKBACK = 50
ATR_PERIOD = 14
ATR_LOOKBACK_BARS = 72
FAT_TAIL_TRENDING = 1.45
FAT_TAIL_RANGING = 1.25
EWMA_FAST = 12
EWMA_SLOW = 36
HORIZON_MIN = 9
HORIZON_MAX = 18
SL_ATR_MULT = 1.8
SL_ATR_MAX = 2.0
TP1_RR = 1.6
TP2_RR = 3.2
TP1_FRAC = 0.50
TP2_FRAC = 0.30
TP3_FRAC = 0.20
TRAIL_ATR_MULT = 2.0
MIN_PROB_DEFAULT = 0.58
MIN_RRR = 1.6
KELLY_FRACTION_NORMAL = 0.20
KELLY_FRACTION_DD = 0.10
MAX_DRAWDOWN_KELLY = 0.04
BASE_BALANCE = 200.0
MAX_RISK_PCT_DEFAULT = 0.010
HIGH_VOL_THRESHOLD = 1.4
POS_SIZE_HIGH_VOL = 0.006
MAX_FUNDING_RATE = 0.00020
NEWS_BLACKOUT_MIN = 30
RECURRING_NEWS_UTC = ["13:30", "14:00", "15:00", "18:00", "20:00"]
MAX_DAILY_EXPOSURE = 0.125
BM_LOOKBACK = ATR_LOOKBACK_BARS
MIN_EDGE_DEFAULT = MIN_PROB_DEFAULT
SAFETY_MARGIN = MIN_PROB_DEFAULT
