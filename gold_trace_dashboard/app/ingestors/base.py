"""Ingestor base: live-first fetch with simulator fallback.

Every source module exposes `run()`. `run()` tries the configured live
endpoint; on any failure (missing key, network, schema drift) it falls
back to `SimSeries` random walks so the layered pipeline never stalls.
Each observation is tagged mode='live' or mode='sim' end-to-end.
"""
from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

import httpx

from .. import config, db

DAY = 86400


# ---------------------------------------------------------------- simulator
@dataclass
class SimSeries:
    """Mean-reverting random walk with drift + optional shock injection."""
    name: str
    base: float
    sigma: float                 # per-tick relative volatility
    drift: float = 0.0           # per-tick relative drift
    reversion: float = 0.05      # pull toward base per tick (0..1)
    floor: Optional[float] = None
    shock_prob: float = 0.0      # chance per tick of a jump
    shock_size: float = 0.0      # relative jump magnitude
    value: float = field(init=False)

    def __post_init__(self) -> None:
        self.value = self.base

    def step(self, scale: float = 1.0) -> float:
        v = self.value
        v += (self.base - v) * self.reversion
        v *= 1.0 + self.drift * scale
        v *= 1.0 + random.gauss(0.0, self.sigma) * scale
        if config.SIM_SHOCKS and self.shock_prob and random.random() < self.shock_prob * scale:
            v *= 1.0 + random.choice((-1, 1)) * self.shock_size * random.uniform(0.6, 1.4)
        if self.floor is not None:
            v = max(v, self.floor)
        self.value = v
        return v


def bucket_ts(period: str, ts: Optional[int] = None) -> int:
    """Bucket a timestamp to its period start (UTC)."""
    ts = ts or db.now()
    if period == "tick":
        return ts
    if period == "day":
        return ts - (ts % DAY)
    if period == "week":
        days = (ts // DAY + 4) % 7          # Monday buckets
        return ts - (ts % DAY) - days * DAY
    if period == "month":
        # bucket to 1st of month 00:00 UTC
        t = time.gmtime(ts)
        return int(time.mktime(time.strptime(f"{t.tm_year}-{t.tm_mon:02d}-01",
                                             "%Y-%m-%d")) - time.timezone)
    if period == "quarter":
        t = time.gmtime(ts)
        q_month = ((t.tm_mon - 1) // 3) * 3 + 1
        return int(time.mktime(time.strptime(f"{t.tm_year}-{q_month:02d}-01",
                                             "%Y-%m-%d")) - time.timezone)
    raise ValueError(period)


# ---------------------------------------------------------------- live fetch
def try_live(fn: Callable[[], dict], timeout: float = 8.0) -> Optional[dict]:
    """Run a live fetch fn; return None on any failure (fallback to sim)."""
    t0 = time.monotonic()
    try:
        out = fn()
        out["_latency_ms"] = int((time.monotonic() - t0) * 1000)
        return out
    except Exception:
        return None


def http_get_json(url: str, params: dict | None = None,
                  headers: dict | None = None) -> dict:
    with httpx.Client(timeout=8.0) as client:
        r = client.get(url, params=params, headers=headers)
        r.raise_for_status()
        return r.json()


def fred_series(series_id: str) -> Optional[float]:
    if not config.FRED_API_KEY:
        return None
    data = http_get_json(
        "https://api.stlouisfed.org/fred/series/observations",
        params={"series_id": series_id, "api_key": config.FRED_API_KEY,
                "file_type": "json", "sort_order": "desc", "limit": 1},
    )
    obs = data.get("observations") or []
    for o in obs:
        if o.get("value") not in (".", "", None):
            return float(o["value"])
    return None


def record(source: str, mode: str, status: str, latency_ms: int = 0,
           detail: str = "") -> None:
    db.log_ingest(source, mode, status, latency_ms, detail)


def clamp(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def norm(x: Optional[float], scale: float) -> float:
    """Normalize a delta to [-1, 1] with scale = full-deflection size."""
    if x is None or scale == 0:
        return 0.0
    return clamp(x / scale)


def smooth_noise(t: float, seed: int) -> float:
    """Deterministic smooth pseudo-noise in [-1,1] for seeded history."""
    return math.sin(t * 0.7 + seed) * 0.5 + math.sin(t * 0.31 + seed * 2.7) * 0.3 \
        + math.sin(t * 1.73 + seed * 5.1) * 0.2
