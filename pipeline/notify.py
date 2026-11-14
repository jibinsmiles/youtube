"""Optional Telegram ping so you can review a video in seconds."""
import requests

from .config import env, log


def telegram(text):
    token, chat = env("TELEGRAM_BOT_TOKEN"), env("TELEGRAM_CHAT_ID")
    if not (token and chat):
        return False
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json={"chat_id": chat, "text": text, "disable_web_page_preview": True}, timeout=20)
        return r.ok
    except requests.RequestException as e:
        log(f"⚠️ Telegram notify failed: {e}")
        return False
