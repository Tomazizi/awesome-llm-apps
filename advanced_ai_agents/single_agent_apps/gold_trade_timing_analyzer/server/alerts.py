"""
Alert engine.

A signal only becomes an alert when it clears the configured conviction bar, and
only once per setup — the cooldown exists because the same structural trigger
stays live for several bars and a desk that fires on every one of them is a
desk that gets muted.

Every alert is written to the journal first, so the message you receive and the
track record you can audit later are the same object.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

from .models import AlertChannel, AlertConfig
from .store import Journal, json_safe

log = logging.getLogger("gold_timing.alerts")

TIER_ORDER = {"A": 3, "B": 2, "C": 1, "-": 0}

try:
    import requests
except Exception:  # pragma: no cover
    requests = None


class AlertManager:
    def __init__(self, journal: Journal) -> None:
        self.journal = journal
        self.config = self._load()
        self._last: Dict[str, dict] = {}          # direction -> {"ts", "time", "score"}

    # -- config ------------------------------------------------------------
    def _load(self) -> AlertConfig:
        raw = self.journal.get_setting("alerts")
        if not raw:
            return AlertConfig()
        try:
            return AlertConfig(**raw)
        except Exception:
            return AlertConfig()

    def update(self, cfg: AlertConfig) -> AlertConfig:
        self.config = cfg
        self.journal.set_setting("alerts", json_safe(cfg.model_dump()))
        return self.config

    def config_dict(self) -> dict:
        return self.config.model_dump()

    # -- evaluation --------------------------------------------------------
    def should_fire(self, analysis: dict, interval: str = "1h") -> Optional[dict]:
        if not self.config.enabled:
            return None
        sig = (analysis or {}).get("signal") or {}
        trade = sig.get("trade")
        if not trade:
            return None
        tier = (trade.get("tier") or "-").upper()
        if TIER_ORDER.get(tier, 0) < TIER_ORDER.get(self.config.min_tier.upper(), 3):
            return None
        if float(trade.get("score") or 0) < float(self.config.min_score):
            return None
        if trade.get("direction") not in (self.config.directions or ["LONG", "SHORT"]):
            return None

        bar = sig.get("bar")
        key = trade["direction"]
        prev = self._last.get(key)
        if prev and prev.get("bar") == bar:
            return None                                  # same bar, already announced
        mins = {"5m": 5, "15m": 15, "30m": 30, "1h": 60, "2h": 120, "4h": 240, "1d": 1440}
        cool = float(self.config.cooldown_bars) * mins.get(interval, 60) * 60.0
        if prev and (time.time() - float(prev.get("time", 0))) < cool:
            return None
        return trade

    def mark(self, trade: dict, analysis: dict, signal_id: Optional[int]) -> None:
        self._last[trade["direction"]] = {
            "bar": (analysis.get("signal") or {}).get("bar"),
            "time": time.time(),
            "score": trade.get("score"),
            "signal_id": signal_id,
        }

    # -- dispatch ----------------------------------------------------------
    @staticmethod
    def format_message(trade: dict, analysis: dict) -> str:
        meta = analysis.get("meta") or {}
        sig = analysis.get("signal") or {}
        c = trade.get("components") or {}
        lines = [
            f"🥇 XAU/USD {trade['direction']} — TIER {trade.get('tier')} ({trade.get('score', 0):.0f}/100)",
            f"Entry {trade['entry']:,.2f}  ·  Stop {trade['stop']:,.2f}  ·  TP1 {trade['tp1']:,.2f}",
            f"R:R {trade['rr']:.2f}  ·  {trade['size']:.2f} lots  ·  risk ${trade['risk_usd']:,.0f}  ·  {trade['risk_atr']:.2f} ATR",
            "",
            "Model:  " + "  ".join(
                f"{k} {v:.0f}" for k, v in list(c.items())[:6] if isinstance(v, (int, float))),
            f"Bar {str(sig.get('bar', ''))[:16]} UTC  ·  {meta.get('interval', '')}  ·  "
            f"spot {meta.get('spot') or '—'}",
        ]
        if trade.get("notes"):
            lines.append("Notes: " + "; ".join(trade["notes"]))
        return "\n".join(lines)

    @staticmethod
    def _payload(trade: dict, analysis: dict) -> dict:
        return json_safe({
            "event": "signal",
            "symbol": "XAUUSD",
            "emitted_at": datetime.now(timezone.utc).isoformat(),
            "message": AlertManager.format_message(trade, analysis),
            "signal": trade,
            "score": (analysis.get("signal") or {}).get("score"),
            "bar": (analysis.get("signal") or {}).get("bar"),
            "models": (analysis or {}).get("models"),
        })

    async def dispatch(self, signal_id: Optional[int], trade: dict, analysis: dict) -> List[dict]:
        payload = self._payload(trade, analysis)
        results = []
        channels = [c for c in (self.config.channels or []) if c.enabled] or [
            AlertChannel(type="log", enabled=True)]
        for ch in channels:
            ok, detail = await asyncio.to_thread(self._send, ch, payload)
            self.journal.log_alert(signal_id, ch.type, "sent" if ok else "failed", detail)
            results.append({"channel": ch.type, "ok": ok, "detail": detail})
            log.info("alert %s -> %s: %s", ch.type, "ok" if ok else "FAILED", detail[:160])
        return results

    @staticmethod
    def _send(ch: AlertChannel, payload: dict) -> tuple:
        text = payload.get("message", "")
        if requests is None:
            return False, "`requests` not installed"
        try:
            if ch.type == "log" or not ch.enabled:
                return True, "logged locally"
            if ch.type == "webhook":
                if not ch.url:
                    return False, "no webhook URL configured"
                r = requests.post(ch.url, json=payload, timeout=15)
                return r.status_code < 300, f"HTTP {r.status_code}"
            if ch.type == "telegram":
                if not (ch.token and ch.chat_id):
                    return False, "telegram token/chat id missing"
                r = requests.post(
                    f"https://api.telegram.org/bot{ch.token}/sendMessage",
                    json={"chat_id": ch.chat_id, "text": text,
                          "disable_web_page_preview": True},
                    timeout=15)
                ok = r.status_code < 300
                return ok, (f"HTTP {r.status_code}" if ok else f"HTTP {r.status_code} {r.text[:160]}")
            if ch.type == "discord":
                if not ch.url:
                    return False, "no discord webhook URL configured"
                colour = 0x26A69A if "LONG" in text else 0xEF5350
                r = requests.post(ch.url, timeout=15, json={
                    "content": None,
                    "embeds": [{
                        "title": text.splitlines()[0] if text else "XAU/USD signal",
                        "description": "\n".join(text.splitlines()[1:]),
                        "color": colour,
                        "footer": {"text": "Gold Entry Timing Analyzer — research, not advice"},
                    }],
                })
                return r.status_code < 300, f"HTTP {r.status_code}"
            return False, f"unknown channel {ch.type}"
        except Exception as e:
            return False, f"{type(e).__name__}: {e}"

    async def test(self, ch: AlertChannel) -> dict:
        payload = {
            "event": "test",
            "emitted_at": datetime.now(timezone.utc).isoformat(),
            "message": ("🥇 Gold Timing Desk — test alert\n"
                        "If you can read this, the channel is wired correctly."),
        }
        ok, detail = await asyncio.to_thread(self._send, ch, payload)
        self.journal.log_alert(None, ch.type, "test-sent" if ok else "test-failed", detail)
        return {"channel": ch.type, "ok": ok, "detail": detail}
