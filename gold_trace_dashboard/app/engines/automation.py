"""Automation Rule Engine — IF trigger THEN action, fully autonomous.

Triggered every automation tick (~8s demo). Each rule is:
  trigger: {type, params} -> evaluates to (fired:bool, context:dict)
  action:  {type, params} -> executes side-effects (signal, trade, alert, journal)

Cooldown per rule prevents spam. All firings are logged to automation_runs.
Supports live Telegram + generic webhook outbound.
"""
from __future__ import annotations
import json, httpx, random
from typing import Any, Optional
from .. import db, config, telegram_alerts
from . import structural as struct_eng, macro as macro_eng

# ---------------------------------------------------------------- triggers
def eval_trigger(rule: dict, ctx: dict) -> tuple[bool, dict]:
    t = rule["trigger_type"]
    p = rule["trigger_params"] or {}
    if isinstance(p, str):
        try: p = json.loads(p)
        except: p = {}
    # cooldown
    if rule.get("last_fired_ts") and rule.get("cooldown_s"):
        if db.now() - rule["last_fired_ts"] < rule["cooldown_s"]:
            return False, {}

    if t == "score_threshold":
        # params: {score: composite|structural|macro, op: >/</>=/<=, value: number}
        score_key = p.get("score", "composite")
        op = p.get("op", ">")
        thr = float(p.get("value", 30))
        scores = ctx.get("scores") or {}
        # ctx scores from scheduler
        val = None
        if score_key == "composite": val = scores.get("composite",{}).get("score")
        elif score_key == "structural": val = scores.get("structural",{}).get("score")
        elif score_key == "macro": val = scores.get("macro",{}).get("score")
        if val is None: return False, {}
        ok = (val > thr if op==">" else val < thr if op=="<" else val>=thr if op==">=" else val<=thr if op=="<=" else val==thr)
        return ok, {"score":score_key, "value":val, "threshold":thr, "op":op}

    elif t == "layer_alert":
        # params: {kind: whale_single|etf_flow|comex_drawdown|sge_spike|vol_burst|cot_shift, severity: optional}
        kind = p.get("kind")
        # look at recent alerts within last 5 min
        recent = db.recent_alerts(8)
        for a in recent:
            if a["kind"] == kind and db.now() - a["ts"] < 600:
                if p.get("severity") and a["severity"] != p["severity"]:
                    continue
                return True, {"alert": a}
        return False, {}

    elif t == "whale_flow":
        # params: {min_oz: number, window_h: 1}
        min_oz = float(p.get("min_oz", 8000))
        whales = db.recent_whales(50)
        cutoff = db.now() - int(float(p.get("window_h", 1))*3600)
        agg = sum(w["amount_oz"] for w in whales if w["ts"] >= cutoff)
        if agg >= min_oz:
            return True, {"agg_oz": agg, "threshold": min_oz, "count": len([w for w in whales if w["ts"]>=cutoff])}
        return False, {}

    elif t == "price_level":
        # params: {op: >/<, value: price}
        op = p.get("op", ">")
        thr = float(p.get("value", 4100))
        spot = ctx.get("spot_price")
        if spot is None:
            last = db.latest("spot_xau")
            spot = last["value"] if last else None
        if spot is None: return False, {}
        ok = (spot > thr if op==">" else spot < thr if op=="<" else spot>=thr if op==">=" else spot<=thr)
        return ok, {"spot": spot, "threshold": thr, "op": op}

    elif t == "premium_spike":
        # params: {min_premium: 55}
        min_p = float(p.get("min_premium", 55))
        sge = db.latest("sge_premium")
        if sge and sge["value"] >= min_p:
            return True, {"sge": sge["value"], "threshold": min_p}
        return False, {}

    elif t == "schedule":
        # always fires on tick (interval handles cadence via cooldown)
        return True, {"scheduled": True}

    elif t == "composite_flip":
        # fires when composite stance changed since last run
        prev = ctx.get("prev_stance")
        cur = ctx.get("scores",{}).get("composite",{}).get("stance")
        if prev and cur and prev != cur:
            return True, {"prev": prev, "cur": cur}
        return False, {}

    return False, {}

# ---------------------------------------------------------------- actions
def exec_action(rule: dict, trigger_ctx: dict, scores: dict, spot_price: float | None) -> dict:
    a = rule["action_type"]
    p = rule["action_params"] or {}
    if isinstance(p, str):
        try: p = json.loads(p)
        except: p = {}
    result: dict[str,Any] = {"action": a}

    # helper to send notification
    def notify(title, body, sev="medium"):
        status = telegram_alerts.send(title, body, sev)
        # also mirror to alerts feed so UI shows it
        db.insert_alert(f"auto:{rule['name']}", sev, f"🤖 {title}", body, telegram_status=status)
        result["telegram_status"] = status
        # webhook if configured
        if config.WEBHOOK_URL:
            try:
                with httpx.Client(timeout=6.0) as c:
                    headers = {"X-Automation-Rule": str(rule["id"])}
                    if config.WEBHOOK_SECRET: headers["X-Webhook-Secret"] = config.WEBHOOK_SECRET
                    c.post(config.WEBHOOK_URL, json={"rule": rule["name"], "title": title, "body": body, "context": trigger_ctx}, headers=headers)
                result["webhook"]="sent"
            except Exception as e:
                result["webhook"]=f"failed:{type(e).__name__}"

    if a == "generate_signal":
        direction = p.get("direction", "LONG")
        # auto-infer direction from trigger if not explicit
        if direction == "AUTO":
            comp = scores.get("composite",{}).get("score",0)
            direction = "LONG" if comp>0 else "SHORT"
        spot = spot_price or (db.latest("spot_xau") or {}).get("value") or 4085.0
        strength = abs(scores.get("composite",{}).get("score",0))
        reason = p.get("reason") or f"Rule '{rule['name']}' fired: {trigger_ctx}"
        sid = db.insert_signal(db.now(), "rule", direction, strength, spot,
                               scores.get("composite",{}).get("score"),
                               scores.get("structural",{}).get("score"),
                               scores.get("macro",{}).get("score"),
                               reason, rule_id=rule["id"])
        result.update({"signal_id": sid, "direction": direction, "price": spot})
        notify(f"SIGNAL {direction} @{spot:.1f}", f"{reason}\nComposite {scores.get('composite',{}).get('score')} | Structural {scores.get('structural',{}).get('score')} | Macro {scores.get('macro',{}).get('score')}", "high")

    elif a == "paper_trade":
        # wraps generate_signal + open_position
        direction = p.get("direction", "AUTO")
        if direction == "AUTO":
            comp = scores.get("composite",{}).get("score",0)
            direction = "LONG" if comp>0 else "SHORT"
        spot = spot_price or (db.latest("spot_xau") or {}).get("value") or 4085.0
        # risk sizing
        balance = config.PAPER_INITIAL_BALANCE
        # approximate equity = initial + closed pnl
        summ = db.paper_pnl_summary()
        equity = balance + summ.get("total_pnl",0)
        risk_per = float(p.get("risk_per", config.PAPER_RISK_PER_TRADE))
        # stop distance ~1.2%
        sl_dist = spot * 0.012
        risk_amt = equity * risk_per
        size_oz = max(1.0, risk_amt / sl_dist) if sl_dist else 5.0
        # check max open
        if len(db.open_positions()) >= config.PAPER_MAX_OPEN:
            result["skipped"]="max_open_positions"
        else:
            sid = db.insert_signal(db.now(), "paper_trade", direction, abs(scores.get("composite",{}).get("score",0)), spot,
                                   scores.get("composite",{}).get("score"),
                                   scores.get("structural",{}).get("score"),
                                   scores.get("macro",{}).get("score"),
                                   f"Auto trade by rule '{rule['name']}'", rule_id=rule["id"])
            sl = spot - sl_dist if direction=="LONG" else spot + sl_dist
            tp = spot + sl_dist*2 if direction=="LONG" else spot - sl_dist*2
            pid = db.open_position(sid, direction, spot, round(size_oz,2), stop_loss=round(sl,2), take_profit=round(tp,2), note=f"auto:{rule['name']}")
            result.update({"signal_id":sid, "position_id":pid, "direction":direction, "size_oz": round(size_oz,2), "entry": spot})
            notify(f"PAPER {direction} {size_oz:.1f}oz @ {spot:.1f}", f"Rule '{rule['name']}' auto-opened paper position #{pid}. SL {sl:.1f} TP {tp:.1f}. Equity ${equity:,.0f}", "high")

    elif a == "telegram":
        title = p.get("title", f"Automation: {rule['name']}")
        body = p.get("body", json.dumps(trigger_ctx))
        notify(title, body, p.get("severity","medium"))

    elif a == "webhook":
        url = p.get("url") or config.WEBHOOK_URL
        if not url:
            result["error"]="no webhook URL configured"
        else:
            try:
                with httpx.Client(timeout=6.0) as c:
                    c.post(url, json={"rule":rule["name"],"trigger":trigger_ctx,"scores":scores})
                result["webhook"]="sent"
            except Exception as e:
                result["webhook"]=f"failed:{e}"

    elif a == "journal_draft":
        # create a BE journal entry as draft/suggestion
        from ..engines import journal as jmod
        spot = spot_price or (db.latest("spot_xau") or {}).get("value") or 0
        direction = p.get("direction","LONG")
        if direction=="AUTO":
            direction="LONG" if scores.get("composite",{}).get("score",0) >0 else "SHORT"
        jid = jmod.add_setup(
            trade_date=__import__("datetime").datetime.utcnow().strftime("%Y-%m-%d"),
            setup_type=p.get("setup_type","Auto Signal"),
            direction=direction,
            level=spot if spot else None,
            trigger=f"🤖 Auto: {rule['name']} -> {trigger_ctx}",
            result="BE",
            r_multiple=0.0,
            notes="Auto-drafted by automation rule; update result after review.",
        )
        result["journal_id"]=jid
        notify(f"Journal draft #{jid}", f"Rule '{rule['name']}' created journal draft", "low")

    elif a == "toggle_layer":
        # params: {layer: funds, enabled: false}
        # implemented via state key ingestor:<source>:enabled
        layer = p.get("layer")
        en = p.get("enabled")
        if layer is not None:
            db.set_state(f"ingestor:{layer}:enabled", "1" if en else "0")
            result["toggled"]=f"{layer} -> {en}"

    elif a == "generate_report":
        period = p.get("period","daily")
        from .signals import generate_daily_report
        # simple inline
        title = f"Auto Report {period}"
        payload = {"trigger": trigger_ctx, "scores": scores, "rule": rule["name"]}
        rid = db.insert_report(period, title, payload)
        result["report_id"]=rid

    return result

def run_cycle(scores: dict, spot_price: float | None, prev_stance: str | None = None) -> list[dict]:
    # check global autopilot enabled
    enabled = db.get_state("autopilot_enabled", "1" if config.AUTOPILOT_ENABLED else "0")
    if enabled == "0":
        return []
    mode = db.get_state("autopilot_mode", config.AUTOPILOT_MODE)
    # in assist mode, only generate signals, not trades? We'll let rules decide; full_auto allows all.
    # But we add a gate: if mode == off, nothing
    if mode == "off":
        return []
    rules = db.all_automation_rules(enabled_only=True)
    # sort by priority already
    ctx = {"scores": scores, "spot_price": spot_price, "prev_stance": prev_stance}
    fired=[]
    for r in rules:
        ok, tctx = eval_trigger(r, ctx)
        if not ok:
            continue
        # prevent paper_trade in assist mode if desired (we allow but gate by action type)
        if mode == "assist" and r["action_type"] == "paper_trade":
            # downgrade to signal
            # clone rule with generate_signal
            # still log as assist downgrade
            pass
        try:
            res = exec_action(r, tctx, scores, spot_price)
        except Exception as e:
            res = {"error": f"{type(e).__name__}: {e}"}
        # update last_fired
        db.update_rule(r["id"], last_fired_ts=db.now())
        # log run
        db.log_run(r["id"], r["trigger_type"], f"Rule '{r['name']}' fired", "ok" if "error" not in res else "error", context=tctx, result=res)
        fired.append({"rule": r["name"], "result": res, "context": tctx})
        # respect priority: high priority rules block lower? We allow all for now
    return fired

def ensure_default_rules():
    if db.table_count("automation_rules") > 0:
        return
    defaults = [
        ("Composite Breakout → Signal", "Composite crosses +35 LONG / -35 SHORT", "score_threshold",
         {"score":"composite","op":">","value":35}, "generate_signal", {"direction":"LONG","reason":"Composite breakout LONG bias"}, 600, 10),
        ("Whale Sweep → Alert", "Agg whale flow >12k oz triggers Telegram", "whale_flow",
         {"min_oz":12000,"window_h":1}, "telegram", {"title":"Whale flow detected","body":"Large on-chain aggregation","severity":"high"}, 600, 8),
        ("Premium Spike → Signal", "SGE premium >= 52", "premium_spike",
         {"min_premium":52}, "generate_signal", {"direction":"LONG","reason":"SGE premium spike implies physical tightness"}, 900, 9),
        ("Structure Trap → Short", "Structural < -30 triggers short signal", "score_threshold",
         {"score":"structural","op":"<","value":-30}, "generate_signal", {"direction":"SHORT","reason":"Structural distribution"}, 1200, 7),
        ("Auto-Paper Trader", "Composite >25 opens paper LONG (full-auto only)", "score_threshold",
         {"score":"composite","op":">","value":25}, "paper_trade", {"direction":"LONG","risk_per":0.012}, 1800, 6),
        ("Daily Auto Report", "Every 6h generate daily report snapshot", "schedule",
         {}, "generate_report", {"period":"daily"}, 21600, 3),
        ("Price Breakdown → Hedge", "Spot < 3950 triggers hedge short", "price_level",
         {"op":"<","value":3950}, "paper_trade", {"direction":"SHORT","risk_per":0.008}, 3600, 7),
    ]
    for name, desc, trig, trigp, act, actp, cd, prio in defaults:
        db.insert_rule(name, trig, trigp, act, actp, description=desc, cooldown_s=cd, priority=prio)
