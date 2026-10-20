"""Tiny threaded scheduler — one loop, per-layer intervals.

Runs each ingestor on its own cadence (live layers every few seconds,
slow layers every few minutes in demo time), then re-evaluates the
anomaly engine and resolves due calendar events. Swap for APScheduler /
cron in production; the job functions stay identical.
"""
from __future__ import annotations

import threading
import time
import traceback

from . import config, db
from .engines import anomalies, events
from .ingestors import JOBS, LAYER_META

_thread: threading.Thread | None = None
_stop = threading.Event()
_last_run: dict[str, float] = {}


def _source_of(fn) -> str:
    return fn.__module__.rsplit(".", 1)[-1]


def _tick() -> None:
    intervals = config.INTERVALS
    while not _stop.is_set():
        t0 = time.monotonic()
        for fn in JOBS:
            src = _source_of(fn)
            every = intervals.get(src, 60.0)
            last = _last_run.get(src, 0.0)
            if t0 - last >= every:
                _last_run[src] = t0
                try:
                    fn()
                except Exception:
                    db.log_ingest(src, "error", "exception", 0,
                                  traceback.format_exc(limit=3))
        # engines run after every sweep
        try:
            events.resolve_due_events()
            anomalies.evaluate()
        except Exception:
            traceback.print_exc()
        _stop.wait(max(0.4, 2.0 - (time.monotonic() - t0)))


def start() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_tick, name="gold-trace-scheduler",
                               daemon=True)
    _thread.start()


def stop() -> None:
    _stop.set()


def status() -> dict:
    now = time.monotonic()
    meta = {m["source"]: m for m in LAYER_META}
    out = []
    for src, last in sorted(_last_run.items()):
        m = meta.get(src, {})
        out.append({
            "source": src,
            "layer": m.get("layer", src),
            "speed": m.get("speed", ""),
            "detail": m.get("detail", ""),
            "interval_s": config.INTERVALS.get(src),
            "age_s": round(now - last, 1),
        })
    return {"jobs": out, "running": bool(_thread and _thread.is_alive())}
