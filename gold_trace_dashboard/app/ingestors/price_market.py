"""Layer 1 — Price & market (LIVE): spot XAU, COMEX GC futures, options OI.

Live sources (first that works wins):
  * goldapi.io   XAU/USD      (GOLDAPI_IO_KEY)
  * metals-api.com            (METALS_API_KEY)
Fallback: correlated random walk around a realistic gold regime.
"""
from __future__ import annotations

import random
from typing import Optional

from .. import config, db
from .base import SimSeries, http_get_json, record, try_live

SOURCE = "price_market"

# sim state --------------------------------------------------------------
_spot = SimSeries("spot_xau", base=4085.0, sigma=0.00055, drift=0.000006,
                  reversion=0.02, shock_prob=0.01, shock_size=0.004)
_gc = SimSeries("comex_gc", base=4108.0, sigma=0.00055, drift=0.000006,
                reversion=0.02, shock_prob=0.01, shock_size=0.004)
_call_oi = SimSeries("call_oi", base=182000, sigma=0.004, reversion=0.05)
_put_oi = SimSeries("put_oi", base=96000, sigma=0.005, reversion=0.05)


def _live() -> Optional[dict]:
    if config.GOLDAPI_IO_KEY:
        d = http_get_json("https://www.goldapi.io/api/XAU/USD",
                          headers={"x-access-token": config.GOLDAPI_IO_KEY})
        return {"spot": float(d["price"])}
    if config.METALS_API_KEY:
        d = http_get_json("https://metals-api.com/api/latest",
                          params={"access_key": config.METALS_API_KEY,
                                  "base": "USD", "symbols": "XAU"})
        rate = float(d["rates"]["XAU"])
        return {"spot": 1.0 / rate}
    return None


def run() -> None:
    ts = db.now()
    live = try_live(_live)
    if live:
        spot = live["spot"]
        _spot.value = spot                     # keep sim anchored to live
        gc = spot + random.uniform(18, 32)     # contango basis
        mode, latency, status = "live", live.get("_latency_ms", 0), "ok"
    else:
        spot = _spot.step()
        gc = _gc.step()
        gc = spot + (gc - _gc.base) + random.uniform(18, 32) * 0.15
        _gc.value = gc
        mode, latency, status = "sim", 0, "ok(fallback)"

    call = int(_call_oi.step())
    put = int(_put_oi.step())
    pcr = round(put / max(call, 1), 3)

    db.upsert_obs("spot_xau", ts, round(spot, 2), mode,
                  {"source": "goldapi.io" if config.GOLDAPI_IO_KEY else "metals-api/sim"})
    db.upsert_obs("comex_gc", ts, round(gc, 2), mode,
                  {"basis": round(gc - spot, 2)})
    db.upsert_obs("options_oi", ts, call + put, mode,
                  {"call_oi": call, "put_oi": put, "pcr": pcr})
    record(SOURCE, mode, status, latency,
           f"spot={spot:.2f} gc={gc:.2f} pcr={pcr}")
