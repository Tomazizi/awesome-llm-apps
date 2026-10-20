"""Layer 10 — Macro: TIPS 10y (FRED DFII10), DXY, Brent, FedWatch cut odds.

Live: FRED for DFII10 / DTWEXBGS / DCOILBRENTEU (daily prints); DXY ticks
simulated around the FRED anchor between prints. FedWatch odds have no
free API — modelled from real-yield drift until you wire a CME feed.
"""
from __future__ import annotations

import random
from typing import Optional

from .. import config, db
from .base import SimSeries, fred_series, record, try_live

SOURCE = "macro"

_tips = SimSeries("tips_10y", base=1.86, sigma=0.004, reversion=0.04)
_dxy = SimSeries("dxy", base=98.4, sigma=0.0006, reversion=0.03)
_brent = SimSeries("brent", base=68.5, sigma=0.004, reversion=0.02,
                   shock_prob=0.008, shock_size=0.02)
_cut = SimSeries("fedwatch_cut_prob", base=64.0, sigma=0.35, reversion=0.02,
                 floor=2.0)


def _live() -> Optional[dict]:
    if not config.FRED_API_KEY:
        return None
    tips = fred_series("DFII10")
    dxy = fred_series("DTWEXBGS")
    brent = fred_series("DCOILBRENTEU")
    if tips is None and dxy is None:
        return None
    return {"tips": tips, "dxy": dxy, "brent": brent}


def run() -> None:
    ts = db.now()
    live = try_live(_live)
    if live:
        if live.get("tips") is not None:
            _tips.value = float(live["tips"])
        if live.get("dxy") is not None:
            _dxy.value = float(live["dxy"])
        if live.get("brent") is not None:
            _brent.value = float(live["brent"])
        mode, latency, status = "live", live.get("_latency_ms", 0), "ok"
    else:
        mode, latency, status = "sim", 0, "ok(fallback)"

    # intraday jitter around the (live or drifting) anchor
    tips = _tips.step(0.6)
    dxy = _dxy.step()
    brent = _brent.step()
    cut = _cut.step()
    cut = min(97.0, max(3.0, cut))

    db.upsert_obs("tips_10y", ts, round(tips, 3), mode)
    db.upsert_obs("dxy", ts, round(dxy, 3), mode)
    db.upsert_obs("brent", ts, round(brent, 2), mode)
    db.upsert_obs("fedwatch_cut_prob", ts, round(cut, 1), mode,
                  {"meeting": "Dec 2026 FOMC"})
    record(SOURCE, mode, status, latency,
           f"tips={tips:.2f}% dxy={dxy:.2f} brent={brent:.2f} cut={cut:.0f}%")
