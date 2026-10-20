"""Layer 9 — Institutions (QUARTERLY): 13F filings.

Live: SEC EDGAR full-text + 13F holdings API (no key needed, UA header
required). Aggregate GLD/IAU callers & shares for the latest quarter.
Sim: quarterly bars — institutional gold allocation as % of 13F AUM and
number of reporting funds holding gold ETFs.
"""
from __future__ import annotations

import random
from typing import Optional

from .. import config, db
from .base import SimSeries, bucket_ts, record, try_live

SOURCE = "institutions"

_alloc = SimSeries("inst_gold_alloc_pct", base=0.82, sigma=0.03, reversion=0.2,
                   floor=0.1)
_funds = SimSeries("inst_gold_fund_count", base=1180, sigma=0.02, reversion=0.2,
                   floor=300)


def _live() -> Optional[dict]:
    # SEC EDGAR: https://efts.sec.gov/LATEST/search-index?q=%22GLD%22&forms=13F-HR
    # Wire aggregation here; mind the 10 req/s UA limit.
    return None


def run() -> None:
    ts = bucket_ts("quarter", db.now() - 50 * 86400)
    live = try_live(_live)
    mode, latency, status = ("live", live.get("_latency_ms", 0), "ok") if live \
        else ("sim", 0, "ok(fallback)")

    alloc = _alloc.step()
    funds = _funds.step()
    db.upsert_obs("inst_gold_alloc_pct", ts, round(alloc, 3), mode,
                  {"top_holders": ["Bridgewater", "Renaissance", "Citadel",
                                   "Millennium", "Soros FM"][:random.randint(3, 5)]})
    db.upsert_obs("inst_gold_fund_count", ts, int(funds), mode)
    record(SOURCE, mode, status, latency,
           f"13F: {int(funds)} funds hold gold ETFs, alloc={alloc:.2f}% of AUM")
