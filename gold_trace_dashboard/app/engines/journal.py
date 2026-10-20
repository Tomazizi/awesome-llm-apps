"""Journal module — logs each setup and reports weekly win-rate by type."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Optional

from .. import db

SETUP_TYPES = [
    "Whale Follow", "ETF Flow Fade", "Vault Drawdown", "SGE Premium",
    "COT Squeeze", "Event Reaction", "Breakout Retest",
]
RESULTS = ["Win", "Loss", "BE"]


def add_setup(trade_date: str, setup_type: str, direction: str,
              level: Optional[float], trigger: str, result: str,
              r_multiple: Optional[float], notes: str = "",
              screenshot: str = "") -> int:
    with db.connect() as conn:
        cur = conn.execute(
            """INSERT INTO journal(created_ts, trade_date, setup_type, direction,
                       level, trigger, result, r_multiple, notes, screenshot)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (db.now(), trade_date, setup_type, direction, level, trigger,
             result, r_multiple, notes, screenshot))
        return int(cur.lastrowid)


def entries(limit: int = 50) -> list[dict[str, Any]]:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM journal ORDER BY trade_date DESC, id DESC LIMIT ?",
            (limit,)).fetchall()
    return [dict(r) for r in rows]


def _iso_week(date_str: str) -> str:
    d = datetime.strptime(date_str, "%Y-%m-%d").date()
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def weekly_report() -> dict[str, Any]:
    with db.connect() as conn:
        rows = conn.execute("SELECT * FROM journal").fetchall()
    entries_all = [dict(r) for r in rows]

    # by setup type -------------------------------------------------------
    by_type: dict[str, dict[str, Any]] = {}
    for e in entries_all:
        t = by_type.setdefault(e["setup_type"], {"setup_type": e["setup_type"],
                                                 "trades": 0, "wins": 0,
                                                 "losses": 0, "be": 0, "net_r": 0.0})
        t["trades"] += 1
        if e["result"] == "Win":  t["wins"] += 1
        elif e["result"] == "Loss": t["losses"] += 1
        else: t["be"] += 1
        t["net_r"] += e["r_multiple"] or 0.0
    for t in by_type.values():
        decided = t["wins"] + t["losses"]
        t["win_rate"] = round(t["wins"] / decided * 100) if decided else None
        t["net_r"] = round(t["net_r"], 1)
        t["avg_r"] = round(t["net_r"] / t["trades"], 2) if t["trades"] else 0.0
    by_type_list = sorted(by_type.values(),
                          key=lambda t: (t["win_rate"] or 0, t["net_r"]),
                          reverse=True)

    # by week ----------------------------------------------------------------
    weeks: dict[str, dict[str, Any]] = {}
    for e in entries_all:
        wk = _iso_week(e["trade_date"])
        w = weeks.setdefault(wk, {"week": wk, "trades": 0, "wins": 0,
                                  "losses": 0, "net_r": 0.0})
        w["trades"] += 1
        if e["result"] == "Win": w["wins"] += 1
        elif e["result"] == "Loss": w["losses"] += 1
        w["net_r"] += e["r_multiple"] or 0.0
    for w in weeks.values():
        decided = w["wins"] + w["losses"]
        w["win_rate"] = round(w["wins"] / decided * 100) if decided else None
        w["net_r"] = round(w["net_r"], 1)
    weeks_list = sorted(weeks.values(), key=lambda w: w["week"], reverse=True)

    total = len(entries_all)
    wins = sum(1 for e in entries_all if e["result"] == "Win")
    losses = sum(1 for e in entries_all if e["result"] == "Loss")
    net_r = round(sum(e["r_multiple"] or 0.0 for e in entries_all), 1)
    decided = wins + losses
    return {
        "total": total, "wins": wins, "losses": losses,
        "overall_win_rate": round(wins / decided * 100) if decided else None,
        "net_r": net_r,
        "by_setup_type": by_type_list,
        "by_week": weeks_list[:8],
    }
