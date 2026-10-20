"""Layer 3 — Funds (DAILY): GLD & IAU holdings and flows.

Live: scrape worldgoldcouncil.org ETF holdings / ssga.com & blackrock.com
daily tonnage files. Sim: daily bars with slow accumulation regime and
occasional large redemption/creation days that trip the anomaly engine.
"""
from __future__ import annotations

import random
from typing import Optional

from .. import config, db
from .base import SimSeries, bucket_ts, record, try_live

SOURCE = "funds"

_gld = SimSeries("gld_tonnes", base=1042.0, sigma=0.0016, drift=0.00004,
                 reversion=0.015, shock_prob=0.03, shock_size=0.006)
_iau = SimSeries("iau_tonnes", base=562.0, sigma=0.0014, drift=0.00003,
                 reversion=0.015, shock_prob=0.02, shock_size=0.005)


def _wgc_enabled() -> bool:
    return bool(config.__dict__.get("WGC_API_KEY"))


def _live() -> Optional[dict]:
    if not _wgc_enabled():
        return None
    return None


def run() -> None:
    ts = bucket_ts("day")
    live = try_live(_live)
    mode, latency, status = ("live", live.get("_latency_ms", 0), "ok") if live \
        else ("sim", 0, "ok(fallback)")

    gld = _gld.step()
    iau = _iau.step()
    prev_gld = db.latest("gld_tonnes")
    flow = round(gld - prev_gld["value"], 2) if prev_gld else 0.0

    db.upsert_obs("gld_tonnes", ts, round(gld, 2), mode, {"flow_t": flow})
    db.upsert_obs("iau_tonnes", ts, round(iau, 2), mode)
    record(SOURCE, mode, status, latency, f"GLD={gld:.1f}t ({flow:+.1f}t) IAU={iau:.1f}t")
