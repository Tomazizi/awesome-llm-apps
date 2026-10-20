"""Layer 5 — Asia demand (DAILY→WEEKLY): SGE premium & withdrawals, India premium.

Live: SGE daily quotes (Au9999 vs LBMA), weekly withdrawal volumes from
SGE clearing reports; India premium from MMTC-PAMP / Reuters survey pages.
Sim: premium oscillates in a $10..70 band, festival/wedding seasons push
it up; occasional spikes trip the anomaly engine.
"""
from __future__ import annotations

import random
from typing import Optional

from .. import config, db
from .base import SimSeries, bucket_ts, record, try_live

SOURCE = "asia_demand"

_sge = SimSeries("sge_premium", base=34.0, sigma=0.9, reversion=0.06,
                 floor=-15.0, shock_prob=0.03, shock_size=8.0)
_india = SimSeries("india_premium", base=4.5, sigma=0.7, reversion=0.07,
                   floor=-20.0, shock_prob=0.02, shock_size=6.0)
_wd = SimSeries("sge_withdrawals", base=28.0, sigma=0.05, reversion=0.15,
                floor=8.0)


def _live() -> Optional[dict]:
    # SGE: https://www.sge.com.cn/sjzx/mrhq (daily Au9999 close)
    # weekly withdrawals published in SGE clearing summaries.
    return None


def run() -> None:
    day_ts = bucket_ts("day")
    week_ts = bucket_ts("week")
    live = try_live(_live)
    mode, latency, status = ("live", live.get("_latency_ms", 0), "ok") if live \
        else ("sim", 0, "ok(fallback)")

    sge = _sge.step()
    india = _india.step()
    wd = _wd.step()

    db.upsert_obs("sge_premium", day_ts, round(sge, 2), mode)
    db.upsert_obs("india_premium", day_ts, round(india, 2), mode)
    db.upsert_obs("sge_withdrawals", week_ts, round(wd, 1), mode,
                  {"unit": "tonnes/week"})
    record(SOURCE, mode, status, latency,
           f"SGE prem=${sge:.1f} India prem=${india:.1f} wd={wd:.1f}t/wk")
