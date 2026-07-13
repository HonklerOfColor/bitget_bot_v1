"""
Bitget API Client — Futures (USDT-Perpetual)
"""
import time
import hmac
import hashlib
import base64
import json
import socket
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from loguru import logger
import config


BASE_URL = "https://api.bitget.com"

# DNS-Cache + Retry Session
_session = None

def _get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        retries = Retry(
            total=3,
            backoff_factor=3,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "POST"],
        )
        adapter = HTTPAdapter(max_retries=retries, pool_connections=10, pool_maxsize=10)
        _session.mount("https://", adapter)
        _session.mount("http://", adapter)
        # DNS-Cache umgeht temporäre Auflösungsfehler
        _session.trust_env = False
    return _session


def _sign(timestamp: str, method: str, path: str, body: str = "") -> str:
    msg = f"{timestamp}{method.upper()}{path}{body}"
    mac = hmac.new(
        config.SECRET_KEY.encode("utf-8"),
        msg.encode("utf-8"),
        hashlib.sha256,
    )
    return base64.b64encode(mac.digest()).decode()


def _headers(method: str, path: str, body: str = "") -> dict:
    ts = str(int(time.time() * 1000))
    headers = {
        "ACCESS-KEY":        config.API_KEY,
        "ACCESS-SIGN":       _sign(ts, method, path, body),
        "ACCESS-TIMESTAMP":  ts,
        "ACCESS-PASSPHRASE": config.PASSPHRASE,
        "Content-Type":      "application/json",
        "locale":            "en-US",
    }
    if config.DEMO_MODE:
        headers["paptrading"] = "1"
    return headers


def _get(path: str, params: dict = None) -> dict:
    full_path = path
    if params:
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        full_path = f"{path}?{qs}"
    headers = _headers("GET", full_path)
    data = None
    for attempt in range(3):
        try:
            sess = _get_session()
            resp = sess.get(BASE_URL + full_path, headers=headers, timeout=15)
            data = resp.json()
            break
        except (requests.exceptions.ConnectionError,
                requests.exceptions.Timeout) as e:
            if attempt < 2:
                wait = 3 ** attempt
                logger.warning(f"GET {path} → {e.__class__.__name__}, retry {attempt+1}/3 in {wait}s")
                time.sleep(wait)
                continue
            logger.error(f"GET {path} → {e}")
            return {"code": "error", "data": []}
    if data is None:
        return {"code": "error", "data": []}
    if not isinstance(data, dict):
        logger.error(f"GET {full_path} → unerwarteter Typ: {type(data)}: {str(data)[:200]}")
        return {"code": "error", "data": []}
    if data.get("code") != "00000":
        logger.error(f"GET {full_path} → {data}")
    return data


def _post(path: str, payload: dict) -> dict:
    body = json.dumps(payload)
    headers = _headers("POST", path, body)
    data = None
    for attempt in range(3):
        try:
            sess = _get_session()
            resp = sess.post(BASE_URL + path, headers=headers, data=body, timeout=15)
            data = resp.json()
            break
        except (requests.exceptions.ConnectionError,
                requests.exceptions.Timeout) as e:
            if attempt < 2:
                wait = 3 ** attempt
                logger.warning(f"POST {path} → {e.__class__.__name__}, retry {attempt+1}/3 in {wait}s")
                time.sleep(wait)
                continue
            logger.error(f"POST {path} → {e}")
            return {"code": "error"}
    if data is None:
        return {"code": "error"}
    if not isinstance(data, dict):
        logger.error(f"POST {path} → unerwarteter Typ: {type(data)}: {str(data)[:200]}")
        return {"code": "error"}
    if data.get("code") != "00000":
        # 43001 = order nicht mehr vorhanden (bereits gefüllt/storniert) — kein Fehler
        if data.get("code") != "43001":
            logger.error(f"POST {path} → {data}")
        else:
            logger.debug(f"POST {path} → {data}")
    return data


# ── Market Data ───────────────────────────────────────────────────────────────

def get_candles(symbol: str, granularity: str, limit: int = 200) -> list:
    """Futures OHLCV-Kerzen."""
    gran_map = {
        "1m":  "1m",  "3m":  "3m",  "5m":  "5m",
        "15m": "15m", "30m": "30m",
        "1H":  "1H",  "4H":  "4H",  "6H":  "6H",
        "12H": "12H", "1D":  "1D",  "1W":  "1W",
    }
    gran = gran_map.get(granularity, granularity)
    params = {
        "symbol":      symbol,
        "productType": config.PRODUCT_TYPE,
        "granularity": gran,
        "limit":       str(limit),
    }
    logger.debug(f"get_candles: {symbol} gran={gran!r} params={params}")
    data = _get("/api/v2/mix/market/candles", params)
    return data.get("data", [])


def get_ticker(symbol: str) -> dict:
    data = _get(
        "/api/v2/mix/market/ticker",
        {"symbol": symbol, "productType": config.PRODUCT_TYPE},
    )
    result = data.get("data", {})
    # API gibt manchmal eine Liste zurück
    if isinstance(result, list):
        return result[0] if result else {}
    return result


def get_symbol_info(symbol: str) -> dict:
    """Holt Kontraktdetails: minTradeNum, sizeMultiplier, volumePlace etc."""
    data = _get(
        "/api/v2/mix/market/contracts",
        {"symbol": symbol, "productType": config.PRODUCT_TYPE},
    )
    items = data.get("data", [])
    return items[0] if items else {}


# ── Account ───────────────────────────────────────────────────────────────────

def get_balance(margin_coin: str = "USDT") -> float:
    # Ersten Symbol aus der Liste nehmen für den Account-Endpoint
    first_symbol = config.SYMBOLS[0]["symbol"]
    data = _get(
        "/api/v2/mix/account/account",
        {"symbol": first_symbol, "productType": config.PRODUCT_TYPE, "marginCoin": margin_coin},
    )
    d = data.get("data", {})
    return float(d.get("available", 0))


def set_leverage(symbol: str, leverage: int, hold_side: str = "long") -> dict:
    return _post(
        "/api/v2/mix/account/set-leverage",
        {
            "symbol":      symbol,
            "productType": config.PRODUCT_TYPE,
            "marginCoin":  config.MARGIN_COIN,
            "leverage":    str(leverage),
            "holdSide":    hold_side,
        },
    )


def set_margin_mode(symbol: str, mode: str = "crossed") -> dict:
    return _post(
        "/api/v2/mix/account/set-margin-mode",
        {
            "symbol":      symbol,
            "productType": config.PRODUCT_TYPE,
            "marginCoin":  config.MARGIN_COIN,
            "marginMode":  mode,
        },
    )


def get_position(symbol: str) -> dict | None:
    """Gibt die aktuelle offene Position zurück (oder None). Nutzt all-position ohne marginCoin für UTA."""
    data = _get(
        "/api/v2/mix/position/all-position",
        {
            "productType": config.PRODUCT_TYPE,
        },
    )
    positions = data.get("data") or []
    if not isinstance(positions, list):
        return None
    for p in positions:
        if p.get("symbol") == symbol and float(p.get("total", 0)) > 0:
            return p
    return None


# ── Orders ────────────────────────────────────────────────────────────────────

def place_futures_order(
    symbol: str,
    side: str,          # buy | sell
    trade_side: str,    # open | close
    size: float,        # Kontraktmenge (Base Coin, z.B. SOL)
) -> dict:
    """
    Platziert eine Market-Order im Futures-Markt.
    side=buy  + tradeSide=open  → Long eröffnen
    side=sell + tradeSide=close → Long schließen
    side=sell + tradeSide=open  → Short eröffnen
    side=buy  + tradeSide=close → Short schließen
    """
    if config.DRY_RUN:
        logger.info(f"[DRY-RUN] FUTURES {side.upper()}/{trade_side.upper()} {size} {symbol}")
        return {"code": "00000", "data": {"orderId": "DRY_RUN"}}

    payload = {
        "symbol":      symbol,
        "productType": config.PRODUCT_TYPE,
        "marginMode":  config.MARGIN_MODE,
        "marginCoin":  config.MARGIN_COIN,
        "size":        str(size),
        "side":        side,
        "tradeSide":   trade_side,
        "orderType":   "market",
        "force":       "ioc",
    }
    return _post("/api/v2/mix/order/place-order", payload)


def set_position_sl_tp(
    symbol: str,
    hold_side: str,     # long | short
    sl_price: float,
    tp_price: float,
) -> dict:
    """
    Setzt Stop-Loss und Take-Profit direkt auf der Position (Exchange-seitig).
    Damit wird SL/TP auch ausgeführt wenn der Bot offline ist.
    """
    if config.DRY_RUN:
        logger.info(f"[DRY-RUN] SET SL={sl_price:.4f} TP={tp_price:.4f} für {symbol} {hold_side}")
        return {"code": "00000"}

    payload = {
        "symbol":      symbol,
        "productType": config.PRODUCT_TYPE,
        "marginCoin":  config.MARGIN_COIN,
        "holdSide":    hold_side,
        "stopLoss":    str(round(sl_price, 4)),
        "takeProfit":  str(round(tp_price, 4)),
    }
    result = _post("/api/v2/mix/order/set-position-auto-margin", payload)
    # Bitget nutzt anderen Endpoint für SL/TP auf Position
    if result.get("code") != "00000":
        # Fallback: adjust-margin endpoint
        result = _post("/api/v2/mix/position/modify-margin", payload)
    return result


def cancel_tpsl_orders(symbol: str, extra_types: list | None = None) -> dict:
    """Loescht ALLE offenen TPSL-Orders fuer ein Symbol (ohne ID-Liste).

    Cancelt standardmaessig profit_plan + loss_plan (planType='profit_loss').
    extra_types: zusaetzliche planType-Werte wie ['pos_loss', 'pos_profit']
    (nötig nach place-pos-tpsl, das eigene Order-Typen erzeugt).
    """
    if config.DRY_RUN:
        logger.info(f"[DRY-RUN] Cancel TPSL {symbol}")
        return {"code": "00000"}
    result = {"code": "00000"}
    types_to_cancel = ["profit_loss", "profit_plan", "loss_plan"]
    if extra_types:
        types_to_cancel.extend(extra_types)
    try:
        for pt in types_to_cancel:
            r = _post(
                "/api/v2/mix/order/cancel-plan-order",
                {
                    "symbol": symbol,
                    "productType": config.PRODUCT_TYPE,
                    "marginCoin": config.MARGIN_COIN,
                    "planType": pt,
                },
            )
            if r.get("code") == "00000":
                logger.debug(f"  [{symbol}] Cancel planType={pt} ✅")
            else:
                logger.debug(f"  [{symbol}] Cancel planType={pt}: {r.get('msg')}")
        logger.info(f"[{symbol}] Alle TPSL-Orders geloescht ✅")
        return result
    except Exception as e:
        logger.warning(f"[{symbol}] Cancel TPSL Fehler (nicht kritisch): {e}")
        return {"code": "00000"}


def set_sl_tp(
    symbol: str,
    hold_side: str,
    sl_price: float,
    tp_price: float,
    size: float = 0.0,
) -> dict:
    """Löscht alte TPSL-Orders, dann setzt neue SL/TP."""
    cancel_tpsl_orders(symbol)

    if config.DRY_RUN:
        logger.info(f"[DRY-RUN] TPSL SL={sl_price} TP={tp_price} {symbol} {hold_side}")
        return {"code": "00000"}

    # Dezimalstellen für Preis aus Symbol-Info holen
    from indicators import candles_to_df  # vermeidet circular import
    sym_info = _get(
        "/api/v2/mix/market/contracts",
        {"symbol": symbol, "productType": config.PRODUCT_TYPE},
    )
    items = sym_info.get("data", [])
    price_place = int(items[0].get("pricePlace", 2)) if items else 2

    sl_rounded = round(sl_price, price_place)
    tp_rounded = round(tp_price, price_place)

    results = []
    sl_side = "buy" if hold_side == "short" else "sell"
    tp_side = "sell" if hold_side == "long" else "buy"

    for plan_type, trigger_price, order_side in [
        ("loss_plan",   sl_rounded, sl_side),
        ("profit_plan", tp_rounded, tp_side),
    ]:
        payload = {
            "symbol":        symbol,
            "productType":   config.PRODUCT_TYPE,
            "marginCoin":    config.MARGIN_COIN,
            "planType":      plan_type,
            "holdSide":      hold_side,
            "triggerPrice":  str(trigger_price),
            "triggerType":   "mark_price",
            "size":          str(size),
            "side":          order_side,
            "tradeSide":     "close",
            "orderType":     "market",
        }
        r = _post("/api/v2/mix/order/place-tpsl-order", payload)
        label = "SL" if plan_type == "loss_plan" else "TP"
        if r.get("code") == "00000":
            logger.info(f"  {label} Order gesetzt ✅ ({trigger_price})")
        else:
            logger.warning(f"  {label} Order fehlgeschlagen: {r.get('msg')}")
        results.append((label, r))

    return results[0][1]


def set_multi_tp_sl(
    symbol: str,
    hold_side: str,
    sl_price: float,
    tp_prices: list,  # [tp1, tp2, tp3]
    tp_sizes: list,    # [size1, size2, size3]
    size: float = 0.0,
) -> dict:
    """Setzt 3 separate TP-Orders + 1 SL-Order."""
    cancel_tpsl_orders(symbol)

    if config.DRY_RUN:
        logger.info(f"[DRY-RUN] Multi-TP: SL={sl_price}, TPs={tp_prices}, sizes={tp_sizes}")
        return {"code": "00000"}

    # Dezimalstellen für Preis und Größe
    from indicators import candles_to_df
    sym_info = _get(
        "/api/v2/mix/market/contracts",
        {"symbol": symbol, "productType": config.PRODUCT_TYPE},
    )
    items = sym_info.get("data", [])
    price_place = int(items[0].get("pricePlace", 2)) if items else 2
    
    # Größe-Dezimalstellen: normalerweise 4 für die meisten Pairs
    # Bitget nutzt "sizeMultiplier" (z.B. "0.01" = meaning min size 0.01, decimal places = 2)
    size_place = 4  # Default: 4 Dezimalstellen
    if items and "sizeMultiplier" in items[0]:
        try:
            mult = items[0].get("sizeMultiplier", "0.0001")
            # sizeMultiplier "0.0001" → 4 decimals, "0.01" → 2 decimals
            size_place = len(mult.split(".")[-1]) if "." in str(mult) else 0
        except:
            size_place = 4

    sl_rounded = round(sl_price, price_place)
    tp_rounded = [round(tp, price_place) for tp in tp_prices]
    # Runde Größen mit Format-String statt round() um Floating-Point Fehler zu vermeiden
    size_rounded = []
    for s in tp_sizes:
        # Format mit genau size_place Dezimalstellen, dann zurück zu float
        formatted = float(f"{s:.{size_place}f}")
        size_rounded.append(formatted)

    results = []
    
    # 1. SL Order (loss_plan)
    sl_side = "buy" if hold_side == "short" else "sell"
    # Runde size ebenfalls nach sizePlace
    size_formatted = round(size if size > 0 else 0, size_place)
    sl_payload = {
        "symbol": symbol,
        "productType": config.PRODUCT_TYPE,
        "marginCoin": config.MARGIN_COIN,
        "planType": "loss_plan",
        "holdSide": hold_side,
        "triggerPrice": str(sl_rounded),
        "triggerType": "mark_price",
        "size": str(size_formatted),
        "side": sl_side,
        "tradeSide": "close",
        "orderType": "market",
    }
    r_sl = _post("/api/v2/mix/order/place-tpsl-order", sl_payload)
    if r_sl.get("code") == "00000":
        logger.info(f"  🛑 SL Order gesetzt ✅ ({sl_rounded})")
    else:
        logger.warning(f"  🛑 SL Order fehlgeschlagen: {r_sl.get('msg')}")
    results.append(("SL", r_sl))

    # 2-4. TP Orders (profit_plan)
    tp_side = "sell" if hold_side == "long" else "buy"
    # Sammle alle gueltigen TP-Orders (size > 0 nach Rundung)
    remaining_size = size_formatted
    valid_tps = []
    for i, (tp_price, tp_size) in enumerate(zip(tp_rounded, size_rounded), 1):
        if tp_size <= 0:
            logger.debug(f"  ⏭️  TP{i} übersprungen (size={tp_size} <= 0)")
            continue
        valid_tps.append((i, tp_price))
    
    # Wenn nicht alle 3 TPs gesetzt werden können, verteile die Groesse neu
    if len(valid_tps) < 3 and size_formatted > 0:
        # Verteile auf alle 3 TPs proportional
        fractions = [0.15, 0.35, 0.50]
        redistributed = []
        for i, (idx, price) in enumerate(valid_tps):
            frac = fractions[idx - 1]  # Ursprünglicher Anteil
            tp_sz = max(round(size_formatted * frac, int(size_place)), size_formatted * 0.01)
            tp_sz = float(f"{tp_sz:.{size_place}f}")
            if tp_sz > 0:
                redistributed.append((idx, price, tp_sz))
        
        # Fallback: nur 1 TP mit voller Groesse wenn immer noch nichts
        if not redistributed:
            logger.warning(f"  ⚠️  Keine TP-Groessen verteilbar — setze 1 TP mit voller Groesse")
            tp_payload = {
                "symbol": symbol,
                "productType": config.PRODUCT_TYPE,
                "marginCoin": config.MARGIN_COIN,
                "planType": "profit_plan",
                "holdSide": hold_side,
                "triggerPrice": str(tp_rounded[0]),
                "triggerType": "mark_price",
                "size": str(size_formatted),
                "side": tp_side,
                "tradeSide": "close",
                "orderType": "market",
            }
            r_tp = _post("/api/v2/mix/order/place-tpsl-order", tp_payload)
            if r_tp.get("code") == "00000":
                logger.info(f"  📈 TP Fallback ✅ ({tp_rounded[0]} × {size_formatted})")
            else:
                logger.warning(f"  📈 TP Fallback fehlgeschlagen: {r_tp.get('msg')}")
            results.append(("TP_fallback", r_tp))
        
        valid_tps = redistributed
    
    for i, tp_price, tp_size in valid_tps:
        tp_payload = {
            "symbol": symbol,
            "productType": config.PRODUCT_TYPE,
            "marginCoin": config.MARGIN_COIN,
            "planType": "profit_plan",
            "holdSide": hold_side,
            "triggerPrice": str(tp_price),
            "triggerType": "mark_price",
            "size": str(tp_size),
            "side": tp_side,
            "tradeSide": "close",
            "orderType": "market",
        }
        r_tp = _post("/api/v2/mix/order/place-tpsl-order", tp_payload)
        label = f"TP{i}"
        if r_tp.get("code") == "00000":
            logger.info(f"  📈 {label} Order gesetzt ✅ ({tp_price} × {tp_size})")
        else:
            logger.warning(f"  📈 {label} Order fehlgeschlagen: {r_tp.get('msg')}")
        results.append((label, r_tp))

    return results[0][1]  # Return SL result


def close_all_positions(symbol: str) -> dict:
    """Schließt alle offenen Positionen via Flash-Close."""
    if config.DRY_RUN:
        logger.info(f"[DRY-RUN] Close all positions {symbol}")
        return {"code": "00000"}
    return _post(
        "/api/v2/mix/order/close-positions",
        {"symbol": symbol, "productType": config.PRODUCT_TYPE},
    )
