"""Layer 6 — Funds positioning (WEEKLY): CFTC COT, managed money vs commercials.

Live: CFTC public Socrata API (series f2t8-fqve, legacy futures-only,
gold: contract 'GC' / market_and_exchange_names containing 'GOLD').
Released Fridays 15:30 ET for Tuesday's data.
Sim: weekly bars; managed-money net drifts, occasional ±30k contract
positioning shifts trip the anomaly engine.
"""
from __future__ import annotations

import random
from typing import Optional

from .. import config, db
from .base import SimSeries, bucket_ts, record, try_live

SOURCE = "positioning"

_mm = SimSeries("cot_mm_net", base=198000, sigma=0.02, reversion=0.05,
                shock_prob=0.08, shock_size=0.09)
_comm = SimSeries("cot_comm_net", base=-72000, sigma=0.018, reversion=0.05,
                  shock_prob=0.06, shock_size=0.08)


def _live() -> Optional[dict]:
    # no key required, but blocked in this sandbox; enable in production:
    if config.__dict__.get("COT_DISABLED"):
        return None
    from .base import http_get_json
    d = http_get_json(
        "https://publicreporting.cftc.gov/resource/f2t8-fqve.json",
        params={"$where": "upper(market_and_exchange_names) like 'GOLD%'",
                "$order": "report_date_as_yyyy_mm_dd DESC", "$limit": 1},
    )
    if not d:
        return None
    row = d[0]
    mm = float(row["noncomm_positions_long_all"] or 0) - \
        float(row["noncomm_positions_short_all"] or 0)
    comm = float(row["comm_positions_long_all"] or 0) - \
        float(row["comm_positions_short_all"] or 0)
    return {"mm": mm, "comm": comm}


def run() -> None:
    ts = bucket_ts("week")
    live = try_live(_live)
    mode, latency, status = ("live", live.get("_latency_ms", 0), "ok") if live \
        else ("sim", 0, "ok(fallback)")

    if live:
        _mm.value = live["mm"]
        _comm.value = live["comm"]
        mm, comm = live["mm"], live["comm"]
    else:
        mm, comm = _mm.step(), _comm.step()

    prev = db.latest("cot_mm_net")
    d = mm - prev["value"] if prev else 0.0
    db.upsert_obs("cot_mm_net", ts, round(mm, 0), mode, {"delta": round(d, 0)})
    db.upsert_obs("cot_comm_net", ts, round(comm, 0), mode)
    record(SOURCE, mode, status, latency, f"MM net={mm:,.0f} ({d:+,.0f}) COMM net={comm:,.0f}")
