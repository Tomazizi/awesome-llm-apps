"""Layer 8 — Trade flows (MONTHLY): Swiss exports, UK & China customs.

Live: Swiss EZV/FOCBS monthly trade statistics, UK HMRC trade API,
China GACC customs releases. All lagged ~4-6 weeks.
Sim: monthly tonnes; Swiss exports to Asia are the key read for physical
demand routing.
"""
from __future__ import annotations

import random
from typing import Optional

from .. import config, db
from .base import SimSeries, bucket_ts, record, try_live

SOURCE = "trade_flows"

_swiss = SimSeries("swiss_gold_exports", base=118.0, sigma=0.07, reversion=0.08,
                   floor=40.0, shock_prob=0.05, shock_size=0.25)
_china = SimSeries("china_gold_imports", base=96.0, sigma=0.08, reversion=0.08,
                   floor=30.0, shock_prob=0.04, shock_size=0.2)


def _live() -> Optional[dict]:
    # Swiss: https://www.ezv.admin.ch/ezv/en/pages/ds-home.html (monthly CSV)
    return None


def run() -> None:
    ts = bucket_ts("month", db.now() - 42 * 86400)
    live = try_live(_live)
    mode, latency, status = ("live", live.get("_latency_ms", 0), "ok") if live \
        else ("sim", 0, "ok(fallback)")

    swiss = _swiss.step()
    china = _china.step()
    india = round(random.uniform(42, 78), 1)
    db.upsert_obs("swiss_gold_exports", ts, round(swiss, 1), mode,
                  {"to_india_t": round(swiss * random.uniform(0.22, 0.32), 1),
                   "to_china_t": round(swiss * random.uniform(0.30, 0.42), 1)})
    db.upsert_obs("china_gold_imports", ts, round(china, 1), mode,
                  {"india_imports_t": india})
    record(SOURCE, mode, status, latency,
           f"Swiss exports={swiss:.0f}t China imports={china:.0f}t India={india:.0f}t")
