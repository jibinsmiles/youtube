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


# ---------------------------------------------------------------------------
# Problem alerts (email + Telegram). Only for things that need a human: crashes, voice fallbacks, uploads that
# were not made public, audio dropouts, quota limits. Never raises, and each distinct alert is sent once per run.
# Secrets: ALERT_EMAIL_TO (where to send), SMTP_USER + SMTP_APP_PASSWORD (a Gmail address and an app password).
# ---------------------------------------------------------------------------
import os
import smtplib
from email.message import EmailMessage

_sent = set()


def _run_link():
    repo, run = os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GITHUB_RUN_ID")
    return f"https://github.com/{repo}/actions/runs/{run}" if repo and run else ""


def send_email(subject, body):
    to, user, pw = env("ALERT_EMAIL_TO"), env("SMTP_USER"), env("SMTP_APP_PASSWORD")
    if not (to and user and pw):
        return False
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = user, to, subject
    msg.set_content(body)
    try:
        with smtplib.SMTP_SSL(env("SMTP_HOST", "smtp.gmail.com"), int(env("SMTP_PORT", "465")), timeout=30) as s:
            s.login(user, pw)
            s.send_message(msg)
        return True
    except Exception as e:
        log(f"⚠️ Alert email failed: {type(e).__name__}: {e}")
        return False


def alert(channel, problem, details=""):
    """Tell the owner something went wrong. `channel` is the channel name; `problem` a short headline."""
    key = (channel, problem)
    if key in _sent:
        return
    _sent.add(key)
    link = _run_link()
    body = f"Channel: {channel}\nProblem: {problem}\n\n{details}\n\n{('Run log: ' + link) if link else ''}".strip()
    send_email(f"[Video robot] {channel}: {problem}", body)
    telegram(f"⚠️ {channel}: {problem}\n{details[:300]}\n{link}")
