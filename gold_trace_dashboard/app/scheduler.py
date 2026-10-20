"""Scheduler — per-layer ingestors + full automation brain.

One threaded loop drives:
  • 10 ingest jobs on their natural cadence (live 4s → quarterly 480s)
  • automation rule engine (~8s)
  • signals & paper trading manager (~10s)
  • auto-report heartbeat (~60s)

Autopilot master switch + mode live in automation_state; toggling from
dashboard or API takes effect on next tick with no restart.
Swap for APScheduler/cron in production — job functions stay identical.
"""
from __future__ import annotations
import threading, time, traceback
from . import config, db
from .engines import anomalies, events

_thread: threading.Thread | None = None
_stop = threading.Event()
_last_run: dict[str, float] = {}
_prev_stance: str | None = None

def _source_of(fn) -> str:
    return fn.__module__.rsplit(".", 1)[-1]

def _tick() -> None:
    global _prev_stance
    from .ingestors import JOBS
    intervals = config.INTERVALS
    while not _stop.is_set():
        t0 = time.monotonic()
        # ingestors with per-source enable toggle
        for fn in JOBS:
            src = _source_of(fn)
            # check kill-switch state ingestor:<src>:enabled
            st = db.get_state(f"ingestor:{src}:enabled", None)
            if st == "0":
                continue
            every = intervals.get(src, 60.0)
            last = _last_run.get(src, 0.0)
            if t0 - last >= every:
                _last_run[src] = t0
                try:
                    fn()
                except Exception:
                    db.log_ingest(src, "error", "exception", 0, traceback.format_exc(limit=3))
        # engines
        try:
            events.resolve_due_events()
            anomalies.evaluate()
        except Exception:
            traceback.print_exc()
        # ---- automation brain ----
        # compute scores once per loop for automation context
        try:
            if t0 - _last_run.get("automation", -1e9) >= intervals.get("automation", 8):
                _last_run["automation"] = t0
                from .engines import structural as struct_eng, macro as macro_eng, automation as auto_eng
                s = struct_eng.compute()
                m = macro_eng.compute()
                comp = macro_eng.combined(s, m)
                scores = {"structural": s, "macro": m, "composite": comp}
                spot = db.latest("spot_xau")
                spot_price = spot["value"] if spot else None
                # ensure default rules exist
                try: auto_eng.ensure_default_rules()
                except Exception: traceback.print_exc()
                # fire rules
                try: auto_eng.run_cycle(scores, spot_price, prev_stance=_prev_stance)
                except Exception: traceback.print_exc()
                _prev_stance = comp.get("stance")
                # signals & paper positions
                try:
                    from .engines import signals as sig_eng
                    sig_eng.evaluate_positions()
                    # opportunistic signal if no rule fired recently
                    sig_eng.maybe_generate_composite_signal(scores, spot_price)
                except Exception: traceback.print_exc()
        except Exception:
            traceback.print_exc()
        # auto report heartbeat (coarse)
        try:
            if t0 - _last_run.get("auto_report", -1e9) >= 60.0:
                _last_run["auto_report"] = t0
                if db.get_state("auto_report_enabled", "1") == "1":
                    # daily report every 2h in demo (real: 24h)
                    last_rep = db.get_state("last_auto_report_ts", "0")
                    try:
                        if db.now() - int(last_rep) > 7200:
                            from .engines.signals import generate_daily_report
                            generate_daily_report()
                            db.set_state("last_auto_report_ts", str(db.now()))
                    except Exception:
                        pass
        except Exception:
            pass

        _stop.wait(max(0.4, 2.0 - (time.monotonic() - t0)))

def start() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_tick, name="gold-trace-scheduler", daemon=True)
    _thread.start()

def stop() -> None:
    _stop.set()

def status() -> dict:
    now = time.monotonic()
    from .ingestors import LAYER_META
    meta = {m["source"]: m for m in LAYER_META}
    out=[]
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
    # ensure _prev_stance accessible for API
    return {"jobs": out, "running": bool(_thread and _thread.is_alive()), "prev_stance": _prev_stance}
