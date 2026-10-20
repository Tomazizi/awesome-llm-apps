"""Telegram alert sender with a visible outbox fallback.

Set TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID and alerts are delivered for
real. Without keys the status is recorded as queued so the dashboard
still shows exactly what *would* have been pushed.
"""
from __future__ import annotations

import httpx

from . import config

_SEV_EMOJI = {"high": "🚨", "medium": "⚠️", "low": "ℹ️"}


def send(title: str, body: str, severity: str = "medium") -> str:
    """Returns the delivery status string stored on the alert row."""
    if not config.telegram_configured():
        return "queued (no bot token)"
    text = f"{_SEV_EMOJI.get(severity, '⚠️')} <b>{title}</b>\n\n{body}\n\n— Gold Trace"
    try:
        with httpx.Client(timeout=6.0) as client:
            r = client.post(
                f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": config.TELEGRAM_CHAT_ID, "text": text,
                      "parse_mode": "HTML", "disable_web_page_preview": True},
            )
        if r.status_code == 200 and r.json().get("ok"):
            return "sent"
        return f"failed ({r.status_code})"
    except Exception as exc:                       # network blocked etc.
        return f"failed ({type(exc).__name__})"
