"""Signals + Paper Trading + Auto-Reports engine.

Runs on scheduler ticks: manages open paper positions (auto SL/TP), generates
proactive signals on strong composite flips, and produces daily auto-reports.
"""
from __future__ import annotations
from typing import Any
from .. import db, config

def evaluate_positions():
    """Auto-close paper positions on SL/TP or trailing score flip."""
    spot = db.latest("spot_xau")
    if not spot:
        return []
    price = spot["value"]
    closed=[]
    for pos in db.open_positions():
        sl = pos["stop_loss"]
        tp = pos["take_profit"]
        hit=None
        if pos["direction"]=="LONG":
            if sl and price <= sl: hit="SL"
            elif tp and price >= tp: hit="TP"
        else:
            if sl and price >= sl: hit="SL"
            elif tp and price <= tp: hit="TP"
        if hit:
            p = db.close_position(pos["id"], price)
            if p:
                db.log_run(None, "paper_sl_tp", f"Position #{pos['id']} closed on {hit} @ {price}", "ok",
                           context={"pos":pos, "hit":hit, "price":price}, result={"pnl":p["pnl"]})
                closed.append(p)
    return closed

def maybe_generate_composite_signal(scores: dict, spot_price: float | None) -> int | None:
    """Heuristic: strong composite + high confidence auto-generates a signal if no recent one."""
    comp = scores.get("composite",{})
    score = comp.get("score",0)
    conf = comp.get("confidence",0)
    # need strong conviction
    if abs(score) < 32 or conf < 70:
        return None
    # cooldown: don't spam if last signal < 30 min ago for same direction
    recent = db.recent_signals(5)
    direction = "LONG" if score>0 else "SHORT"
    if recent:
        last = recent[0]
        if last["direction"]==direction and db.now() - last["ts"] < 1800:
            return None
    # create signal if autopilot in assist/full_auto
    if db.get_state("autopilot_enabled","1")=="0":
        return None
    spot = spot_price or (db.latest("spot_xau") or {}).get("value") or 0
    reason = f"Auto signal: composite {score} ({comp.get('stance')}) conf {conf}%"
    sid = db.insert_signal(db.now(), "auto_composite", direction, abs(score), spot,
                           score, scores.get("structural",{}).get("score"),
                           scores.get("macro",{}).get("score"),
                           reason)
    return sid

def generate_daily_report() -> dict[str,Any]:
    """Compile a snapshot payload for auto report."""
    from ..engines import structural as struct_eng, macro as macro_eng, journal as jmod
    s = struct_eng.compute()
    m = macro_eng.compute()
    comp = macro_eng.combined(s,m)
    whales = db.recent_whales(5)
    alerts = db.recent_alerts(5)
    signals = db.recent_signals(5)
    positions = db.all_positions(20)
    pnl = db.paper_pnl_summary()
    payload = {
        "scores": {"structural": s["score"], "macro": m["score"], "composite": comp["score"], "stance": comp["stance"]},
        "alerts_count": len(alerts),
        "whales_24h": len([w for w in whales if db.now()-w["ts"]<86400]),
        "signals": len(signals),
        "pnl": pnl,
        "open_positions": len([p for p in positions if p["status"]=="open"]),
    }
    rid = db.insert_report("daily", f"Daily Gold Trace – {comp['stance']}", payload)
    payload["report_id"]=rid
    return payload
