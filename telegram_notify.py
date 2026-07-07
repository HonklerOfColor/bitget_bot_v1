"""
Telegram Benachrichtigungen für den Bitget Bot
"""
import requests
from loguru import logger
import scalper_config as config


def send(text: str) -> bool:
    """Sendet eine Nachricht via Telegram Bot API."""
    if not config.TG_TOKEN or not config.TG_CHAT_ID:
        return False
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{config.TG_TOKEN}/sendMessage",
            data={"chat_id": config.TG_CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
        if not resp.json().get("ok"):
            logger.warning(f"Telegram Fehler: {resp.text}")
            return False
        return True
    except Exception as e:
        logger.warning(f"Telegram nicht erreichbar: {e}")
        return False
