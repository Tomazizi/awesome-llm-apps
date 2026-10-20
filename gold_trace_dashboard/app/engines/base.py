"""Shared helpers for the scoring engines."""
from __future__ import annotations

DAY = 86400
HOUR = 3600


def clamp(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def band(score: float) -> str:
    if score >= 55:  return "strong-bull"
    if score >= 25:  return "bull"
    if score > -25:  return "neutral"
    if score > -55:  return "bear"
    return "strong-bear"
