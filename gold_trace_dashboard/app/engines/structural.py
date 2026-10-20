"""Structural score — the long-term bias.

Central-bank flow trend + ETF flows + COMEX stock changes + Asia physical
premium, combined into -100..+100 (positive = accumulation / bullish
structure). This is the layer stack answering: who is actually taking
delivery of the metal?
"""
from __future__ import annotations

from typing import Any, Optional

from .. import db
from .base import DAY

def _clamp(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def compute() -> dict[str, Any]:
    factors: list[dict[str, Any]] = []

    # 1) Central bank 3-month trend (lagged monthly prints) -------------
    cb = db.series_window("cb_net_purchases", points=6)
    if len(cb) >= 3:
        recent = [r["value"] for r in cb[-3:]]
        trend_t = sum(recent) / len(recent)          # avg tonnes/month
        comp = _clamp(trend_t / 55.0)                # +55t/mo = full deflection
        factors.append({
            "name": "Central bank trend",
            "value": f"{trend_t:+.0f} t/mo (3m avg)",
            "component": round(comp, 3), "weight": 0.35,
            "note": "lagged 1–2 months — slowest, strongest layer",
        })
    else:
        comp, trend_t = 0.0, None
        factors.append({"name": "Central bank trend", "value": "warming up…",
                        "component": 0.0, "weight": 0.35, "note": ""})

    # 2) ETF flows — GLD+IAU 5-day delta --------------------------------
    gld5 = db.delta("gld_tonnes", 5 * DAY)
    iau5 = db.delta("iau_tonnes", 5 * DAY)
    etf5 = (gld5 or 0.0) + (iau5 or 0.0)
    comp_etf = _clamp(etf5 / 12.0)                   # ±12t/5d = full
    factors.append({
        "name": "ETF flows (5d)",
        "value": f"{etf5:+.1f} t",
        "component": round(comp_etf, 3), "weight": 0.25,
        "note": "GLD+IAU creations/redemptions",
    })

    # 3) COMEX registered stock 20-day % change (drawdowns = bullish) ----
    reg_pct = db.pct_change("comex_registered", 20 * DAY)
    comp_reg = _clamp(-(reg_pct or 0.0) / 6.0)       # -6%/20d = full bullish
    factors.append({
        "name": "COMEX registered (20d)",
        "value": f"{reg_pct:+.1f}%" if reg_pct is not None else "warming up…",
        "component": round(comp_reg, 3), "weight": 0.25,
        "note": "registered stock leaving = physical demand",
    })

    # 4) Asia physical premium -------------------------------------------
    sge = db.latest("sge_premium")
    comp_sge = _clamp((sge["value"] if sge else 0.0) / 55.0)
    factors.append({
        "name": "SGE premium",
        "value": f"${sge['value']:.0f}/oz" if sge else "warming up…",
        "component": round(comp_sge, 3), "weight": 0.15,
        "note": "Shanghai over LBMA — retail/physical pull",
    })

    score = sum(f["component"] * f["weight"] for f in factors) * 100.0
    score = round(_clamp(score, -100, 100), 1)

    if score >= 55:   bias, tag = "Strong accumulation", "strong-bull"
    elif score >= 25: bias, tag = "Accumulation", "bull"
    elif score >= -25: bias, tag = "Neutral structure", "neutral"
    elif score >= -55: bias, tag = "Distribution", "bear"
    else:             bias, tag = "Heavy distribution", "strong-bear"

    return {
        "score": score, "bias": bias, "tag": tag, "factors": factors,
        "cb_trend_t": trend_t,
        "playbook": ("Structural bid intact — buy dips, event shocks are"
                     " opportunities" if score >= 25 else
                     "Structure deteriorating — respect rallies, tighten stops"
                     if score <= -25 else
                     "Mixed structure — trade ranges, wait for a layer to break"),
    }
