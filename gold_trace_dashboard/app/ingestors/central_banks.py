"""Layer 7 — Central banks (MONTHLY, lagging 1–2 months): WGC / IMF IFS / PBoC.

Live: IMF IFS JSON API + WGC monthly PDF/CSV; PBoC reserve announcements
via the SAFE website. All are lagged — the structural engine therefore
uses the trend of the last 3 monthly prints, not the freshest number.
Sim: monthly net-purchase bars in the +20..+70t accumulation regime with
occasional pause/rotation months.
"""
from __future__ import annotations

import random
from typing import Optional

from .. import config, db
from .base import SimSeries, bucket_ts, record, try_live

SOURCE = "central_banks"

_cb = SimSeries("cb_net_purchases", base=42.0, sigma=0.09, reversion=0.10,
                floor=-30.0, shock_prob=0.05, shock_size=0.5)

_BUYERS = ["PBoC", "NBP Poland", "RBI India", "Central Bank of Turkey",
           "NBP", "Czech CNB", "Bank of Ghana", "MAS Singapore",
           "Banca Serbia", "UBS/PBoC via SGE"]


def _live() -> Optional[dict]:
    # IMF IFS: https://dataservices.imf.org/REST/SDMX_JSON.svc/compact_data/IFS/...
    # Requires registration key; wire here when deploying.
    return None


def run() -> None:
    # monthly prints land lagged: newest bar = previous month bucket
    ts = bucket_ts("month", db.now() - 35 * 86400)
    live = try_live(_live)
    mode, latency, status = ("live", live.get("_latency_ms", 0), "ok") if live \
        else ("sim", 0, "ok(fallback)")

    net = _cb.step()
    buyers = random.sample(_BUYERS, k=random.randint(2, 4))
    db.upsert_obs("cb_net_purchases", ts, round(net, 1), mode,
                  {"notable_buyers": buyers, "lag_months": 1})
    record(SOURCE, mode, status, latency,
           f"net={net:+.1f}t (lagged 1-2mo), buyers: {', '.join(buyers[:2])}")
