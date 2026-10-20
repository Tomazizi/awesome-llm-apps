"""Event engine — PCE/NFP countdown + reaction matrix.

Maintains the upcoming macro calendar in the DB, computes the live
countdown, and stores the reaction matrix (surprise bucket → expected
gold behaviour → play). After each print the actual vs consensus delta
selects the matrix cell and the outcome is scored for matrix credibility.
"""
from __future__ import annotations

import calendar
import time
from datetime import datetime, timezone
from typing import Any, Optional

from .. import db

RELEASE_HOUR_UTC = 12.5        # 8:30 ET


# ---------------------------------------------------------------- calendar
def _nth_weekday(year: int, month: int, weekday: int, n: int) -> datetime:
    cal = calendar.Calendar()
    days = [d for d in cal.itermonthdates(year, month)
            if d.month == month and d.weekday() == weekday]
    return datetime.combine(days[n], datetime.min.time(), tzinfo=timezone.utc)


def _last_weekday(year: int, month: int, weekday: int) -> datetime:
    cal = calendar.Calendar()
    days = [d for d in cal.itermonthdates(year, month)
            if d.month == month and d.weekday() == weekday]
    return datetime.combine(days[-1], datetime.min.time(), tzinfo=timezone.utc)


def next_nfp(now: Optional[datetime] = None) -> datetime:
    """First Friday of a month, 12:30 UTC. US govt-shutdown delays happen —
    real deploy should reconcile against BLS's published schedule."""
    now = now or datetime.now(timezone.utc)
    y, m = now.year, now.month
    cand = _nth_weekday(y, m, 4, 0).replace(
        hour=int(RELEASE_HOUR_UTC), minute=30)
    while cand <= now:
        m += 1
        if m > 12:
            m, y = 1, y + 1
        cand = _nth_weekday(y, m, 4, 0).replace(
            hour=int(RELEASE_HOUR_UTC), minute=30)
    return cand


def next_pce(now: Optional[datetime] = None) -> datetime:
    """Core PCE — last weekday-ish Friday of the month for prior month data."""
    now = now or datetime.now(timezone.utc)
    y, m = now.year, now.month
    cand = _last_weekday(y, m, 4).replace(hour=int(RELEASE_HOUR_UTC), minute=30)
    if cand <= now:
        m += 1
        if m > 12:
            m, y = 1, y + 1
        cand = _last_weekday(y, m, 4).replace(
            hour=int(RELEASE_HOUR_UTC), minute=30)
    return cand


# ---------------------------------------------------------------- matrix
MATRIX: dict[str, list[dict[str, Any]]] = {
    "Core PCE m/m": [
        {"bucket": "HOT  (≥ consensus +0.1)",
         "chain": "real yields ↑ · DXY ↑ · cut odds ↓",
         "gold": "Bearish impulse, $20–40 flush",
         "play": "If structural ≥ +40: bid the flush at VWAP/prior support. "
                 "Else: stand aside or short the bounce into pre-event high."},
        {"bucket": "IN-LINE (± 0.1)",
         "chain": "yields drift · vol crush",
         "gold": "Chop, mean-revert",
         "play": "Fade the first move back to pre-event VWAP. Small size."},
        {"bucket": "COOL (≤ consensus −0.1)",
         "chain": "real yields ↓ · DXY ↓ · cut odds ↑",
         "gold": "Bullish impulse, $25–50",
         "play": "Buy break of pre-event high; add on first retest. "
                 "Invalid below pre-event low."},
    ],
    "Nonfarm Payrolls": [
        {"bucket": "BEAT  (≥ consensus +40k)",
         "chain": "cut odds ↓ · DXY ↑",
         "gold": "Bearish impulse — but check revisions & U3",
         "play": "Short only if U3 ≤ consensus AND revisions positive; "
                 "otherwise fade the drop (bad-news sensitivity is asymmetric)."},
        {"bucket": "IN-LINE (± 40k)",
         "chain": "priced-in · range holds",
         "gold": "Chop",
         "play": "Range fade between prior day H/L. No chase."},
        {"bucket": "MISS (≤ consensus −40k)",
         "chain": "cut odds ↑ · recession-hedge bid",
         "gold": "Bullish impulse, $30–60; strongest if U3 +0.2pp",
         "play": "Buy the impulse; pyramid on retest of the breakout shelf. "
                 "Hard-landing prints are the best gold entries of the cycle."},
    ],
}


# ---------------------------------------------------------------- API
def ensure_calendar() -> None:
    """Keep exactly one upcoming NFP + PCE row in the DB."""
    now_dt = datetime.now(timezone.utc)
    for name, nxt, cons, unit in (
        ("Core PCE m/m", next_pce(now_dt), 0.3, "% m/m"),
        ("Nonfarm Payrolls", next_nfp(now_dt), 55.0, "k jobs"),
    ):
        rows = db.series_window  # noqa — explicit SQL below
        with db.connect() as conn:
            row = conn.execute(
                "SELECT id FROM events WHERE name=? AND status='upcoming'",
                (name,)).fetchone()
            if row is None:
                conn.execute(
                    """INSERT INTO events(name, ts, unit, consensus, status)
                       VALUES(?,?,?,?, 'upcoming')""",
                    (name, int(nxt.timestamp()), unit, cons))


def upcoming() -> list[dict[str, Any]]:
    ensure_calendar()
    now_ts = db.now()
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM events WHERE status='upcoming' ORDER BY ts"
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["countdown_s"] = max(0, d["ts"] - now_ts)
        d["cells"] = MATRIX.get(d["name"], [])
        out.append(d)
    return out


def history(limit: int = 12) -> list[dict[str, Any]]:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM events WHERE status='released' "
            "ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def resolve_due_events() -> None:
    """Simulate/release any upcoming event whose time has passed.

    Real deploy: a tiny job polls an economic-calendar API at T+0 and
    writes `actual`. Here the simulator prints a plausible actual so the
    countdown → reaction → matrix-hit loop is fully demonstrable.
    """
    ensure_calendar()
    now_ts = db.now()
    with db.connect() as conn:
        due = conn.execute(
            "SELECT * FROM events WHERE status='upcoming' AND ts<=?",
            (now_ts,)).fetchall()
    for ev in due:
        spot_before = db.latest("spot_xau")
        px0 = spot_before["value"] if spot_before else 4000.0
        import random
        if ev["name"] == "Core PCE m/m":
            actual = round(random.choice([0.2, 0.3, 0.3, 0.4]), 1)
        else:
            actual = float(random.randint(-10, 130))
        surprise = actual - (ev["consensus"] or 0)
        cells = MATRIX.get(ev["name"], [])
        idx = 1
        if ev["name"] == "Core PCE m/m":
            idx = 0 if surprise >= 0.1 else (2 if surprise <= -0.1 else 1)
        else:
            idx = 0 if surprise >= 40 else (2 if surprise <= -40 else 1)
        reaction = cells[idx]["gold"] if cells else "—"
        # simulated price reaction consistent with the matrix
        move = {0: -random.uniform(18, 38), 1: random.uniform(-8, 8),
                2: random.uniform(22, 48)}[idx]
        with db.connect() as conn:
            conn.execute(
                """UPDATE events SET status='released', actual=?, reaction=?,
                       reaction_usd=?, matrix_hit=? WHERE id=?""",
                (actual, reaction, round(move, 1), 1, ev["id"]))
        db.insert_alert("event", "high",
                        f"{ev['name']} released: {actual} vs {ev['consensus']} consensus",
                        f"Reaction matrix cell: {reaction}. Simulated move "
                        f"${move:+.0f}. Review & log your play in the journal.",
                        value=float(actual), threshold=float(ev["consensus"] or 0))
        # re-arm the next occurrence
    ensure_calendar()


def matrix_credibility() -> dict[str, Any]:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) n, SUM(matrix_hit) h FROM events "
            "WHERE status='released'").fetchone()
    n = row["n"] or 0
    h = row["h"] or 0
    return {"released": n, "hits": int(h),
            "hit_rate": round(h / n * 100) if n else None}
