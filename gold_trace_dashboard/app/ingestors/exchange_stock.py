"""Layer 4 — Exchange stock (DAILY): COMEX warehouse registered/eligible.

Live: CME Group daily bulletin PDF/CSV (registered & eligible gold stocks).
Sim: registered drifts down slowly (delivery/withdrawal regime) with rare
sharp drawdown days; eligible is stickier.
"""
from __future__ import annotations

import random
from typing import Optional

from .. import config, db
from .base import SimSeries, bucket_ts, record, try_live

SOURCE = "exchange_stock"

_reg = SimSeries("comex_registered", base=24.8e6, sigma=0.0022, drift=-0.00012,
                 reversion=0.008, floor=8e6, shock_prob=0.025, shock_size=0.014)
_elig = SimSeries("comex_eligible", base=452e6, sigma=0.0009, drift=0.00002,
                  reversion=0.006, floor=150e6)


def _live() -> Optional[dict]:
    # CME bulletin: https://www.cmegroup.com/delivery_reports/Gold_stocks.xls
    # Wire a parser here when deploying; needs no key but a UA header.
    return None


def run() -> None:
    ts = bucket_ts("day")
    live = try_live(_live)
    mode, latency, status = ("live", live.get("_latency_ms", 0), "ok") if live \
        else ("sim", 0, "ok(fallback)")

    reg = _reg.step()
    elig = _elig.step()
    prev = db.latest("comex_registered")
    d = reg - prev["value"] if prev else 0.0

    db.upsert_obs("comex_registered", ts, round(reg, 0), mode, {"delta_oz": round(d, 0)})
    db.upsert_obs("comex_eligible", ts, round(elig, 0), mode)
    record(SOURCE, mode, status, latency,
           f"registered={reg/1e6:.2f}Moz ({d/1e3:+.0f}koz) eligible={elig/1e6:.1f}Moz")
