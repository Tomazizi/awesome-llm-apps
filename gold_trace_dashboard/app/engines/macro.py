"""Macro score — the directional lean.

Real yields (TIPS 10y) and DXY direction are gold's two dominant macro
drivers; FedWatch cut odds and Brent add tilt. -100..+100, positive =
macro tailwind for gold.
"""
from __future__ import annotations

from typing import Any

from .. import db
from .base import DAY, clamp

def compute() -> dict[str, Any]:
    factors: list[dict[str, Any]] = []

    # 1) TIPS 10y — 5-day change in bp, inverted (yields up = gold down) --
    tips_d = db.delta("tips_10y", 5 * DAY)
    comp_tips = clamp(-((tips_d or 0.0) * 100.0) / 15.0)   # ±15bp = full
    factors.append({
        "name": "Real yields (5d)",
        "value": f"{tips_d*100:+.0f} bp" if tips_d is not None else "warming up…",
        "component": round(comp_tips, 3), "weight": 0.40,
        "note": "TIPS 10y — the single biggest gold driver",
    })

    # 2) DXY — 5-day % change, inverted ------------------------------------
    dxy_pct = db.pct_change("dxy", 5 * DAY)
    comp_dxy = clamp(-((dxy_pct or 0.0)) / 1.5)             # ±1.5% = full
    factors.append({
        "name": "Dollar (5d)",
        "value": f"{dxy_pct:+.2f}%" if dxy_pct is not None else "warming up…",
        "component": round(comp_dxy, 3), "weight": 0.35,
        "note": "broad USD index direction",
    })

    # 3) FedWatch — 5-day change in cut odds (odds up = gold up) -----------
    cut_d = db.delta("fedwatch_cut_prob", 5 * DAY)
    comp_cut = clamp((cut_d or 0.0) / 10.0)                 # ±10pp = full
    factors.append({
        "name": "Cut odds (5d)",
        "value": f"{cut_d:+.1f} pp" if cut_d is not None else "warming up…",
        "component": round(comp_cut, 3), "weight": 0.15,
        "note": "Dec FOMC cut probability drift",
    })

    # 4) Brent — 5-day % (inflation hedge tilt, small weight) --------------
    br_pct = db.pct_change("brent", 5 * DAY)
    comp_br = clamp((br_pct or 0.0) / 4.0)                  # ±4% = full
    factors.append({
        "name": "Brent (5d)",
        "value": f"{br_pct:+.1f}%" if br_pct is not None else "warming up…",
        "component": round(comp_br, 3), "weight": 0.10,
        "note": "energy-led inflation impulse",
    })

    score = sum(f["component"] * f["weight"] for f in factors) * 100.0
    score = round(clamp(score, -100, 100), 1)

    if score >= 40:   lean, tag = "Strong tailwind", "strong-bull"
    elif score >= 15: lean, tag = "Mild tailwind", "bull"
    elif score > -15: lean, tag = "Macro-neutral", "neutral"
    elif score > -40: lean, tag = "Mild headwind", "bear"
    else:             lean, tag = "Strong headwind", "strong-bear"

    return {"score": score, "lean": lean, "tag": tag, "factors": factors}


def combined(structural: dict[str, Any], macro: dict[str, Any]) -> dict[str, Any]:
    """Composite stance: 60% structural (slow truth) + 40% macro (fast lean)."""
    comp = round(structural["score"] * 0.6 + macro["score"] * 0.4, 1)
    if comp >= 35:    stance, tag = "LONG BIAS", "strong-bull"
    elif comp >= 12:  stance, tag = "MILD LONG", "bull"
    elif comp > -12:  stance, tag = "BALANCED / RANGE", "neutral"
    elif comp > -35:  stance, tag = "MILD SHORT", "bear"
    else:             stance, tag = "DEFENSIVE SHORT", "strong-bear"

    modifier = ""
    if structural["score"] >= 60:
        modifier = ("Event modifier: structural bid ≥ +60 — treat hot-data "
                    "dips (PCE/NFP beats) as buys near support, not breakdowns.")
    elif structural["score"] <= -60:
        modifier = ("Event modifier: structural damage ≤ −60 — fade cool-data "
                    "rallies until ETF flows and vault stocks stabilize.")

    return {"score": comp, "stance": stance, "tag": tag, "modifier": modifier,
            "confidence": round(min(100, abs(comp) * 1.4 + 20))}
