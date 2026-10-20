"""Gold Trace — FastAPI app with Full Automation System.

Lifespan: init DB → seed 90d history → seed default automation rules → scheduler (ingest + automation brain)
Bootstrap: one aggregate call powers dashboard + autopilot command center.
"""
from __future__ import annotations
from contextlib import asynccontextmanager
from typing import Any, Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from . import config, db, scheduler, seed
from .engines import anomalies, events as events_engine, journal, macro as macro_engine, structural

STATIC_DIR = config.BASE_DIR / "static"

@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    seed.seed_if_empty()
    # ensure automation seed
    try:
        from .engines.automation import ensure_default_rules
        ensure_default_rules()
        # seed autopilot state defaults if missing
        if db.get_state("autopilot_enabled") is None:
            db.set_state("autopilot_enabled", "1" if config.AUTOPILOT_ENABLED else "0")
        if db.get_state("autopilot_mode") is None:
            db.set_state("autopilot_mode", config.AUTOPILOT_MODE)
        if db.get_state("paper_enabled") is None:
            db.set_state("paper_enabled", "1" if config.PAPER_ENABLED else "0")
        if db.get_state("auto_report_enabled") is None:
            db.set_state("auto_report_enabled", "1" if config.AUTO_REPORT_ENABLED else "0")
    except Exception:
        pass
    scheduler.start()
    yield
    scheduler.stop()

app = FastAPI(
    title="Gold Trace — Full Automation System",
    description="Layered gold intelligence with autonomous autopilot: rules engine, auto-signals, paper trading, auto-reports, Telegram/webhook.",
    version="2.0.0",
    lifespan=lifespan,
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
DAY = 86400

def _series_payload(name: str, points: int = 260) -> dict[str, Any]:
    rows = db.series_window(name, points=points)
    last = rows[-1] if rows else None
    return {"name": name, "points": [{"t": r["ts"], "v": r["value"]} for r in rows], "last": last["value"] if last else None, "mode": last["mode"] if last else None, "extra": last["extra"] if last else None}

@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True, "db": config.DB_PATH, "telegram": config.telegram_configured(), "autopilot": db.get_state("autopilot_enabled","0"), "mode": db.get_state("autopilot_mode","assist")}

@app.get("/api/status")
def api_status() -> dict:
    return {"scheduler": scheduler.status(), "ingest": db.ingest_status(), "counts": {"observations": db.table_count("observations"), "whales": db.table_count("whale_transfers"), "alerts": db.table_count("alerts"), "journal": db.table_count("journal"), "automation_rules": db.table_count("automation_rules"), "signals": db.table_count("signals"), "paper_positions": db.table_count("paper_positions")}, "automation": {"enabled": db.get_state("autopilot_enabled","0"), "mode": db.get_state("autopilot_mode","assist"), "paper_enabled": db.get_state("paper_enabled","0"), "auto_report": db.get_state("auto_report_enabled","0")}}

@app.get("/api/layers")
def api_layers() -> dict:
    from .ingestors import LAYER_META
    ingest = db.ingest_status()
    out=[]
    for m in LAYER_META:
        st=ingest.get(m["source"],{})
        enabled = db.get_state(f"ingestor:{m['source']}:enabled", "1")
        out.append({**m, "last_ts":st.get("last_ts"), "mode":st.get("mode","—"), "status":st.get("status","pending"), "latency_ms":st.get("latency_ms"), "enabled": enabled=="1"})
    return {"layers": out}

@app.get("/api/scores")
def api_scores() -> dict:
    s=structural.compute(); m=macro_engine.compute(); c=macro_engine.combined(s,m)
    return {"structural":s,"macro":m,"composite":c}

@app.get("/api/series/{name}")
def api_series(name: str, points: int = 260) -> dict:
    payload=_series_payload(name, min(points,4000))
    if not payload["points"]: raise HTTPException(404, f"unknown series: {name}")
    return payload

@app.get("/api/whales")
def api_whales(limit: int = 40) -> dict:
    spot=db.latest("spot_xau"); px=spot["value"] if spot else None
    rows=db.recent_whales(min(limit,200)); agg_24h=sum(r["amount_oz"] for r in rows if r["ts"]>=db.now()-DAY)
    return {"transfers":rows,"spot":px,"agg_24h_oz":round(agg_24h)}

@app.get("/api/events")
def api_events() -> dict:
    return {"upcoming":events_engine.upcoming(),"history":events_engine.history(),"credibility":events_engine.matrix_credibility()}

@app.get("/api/alerts")
def api_alerts(limit: int = 60) -> dict:
    return {"alerts": db.recent_alerts(min(limit,200)), "telegram_configured": config.telegram_configured()}

class JournalIn(BaseModel):
    trade_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    setup_type: str
    direction: str = Field(pattern=r"^(LONG|SHORT)$")
    level: Optional[float]=None
    trigger: str=""
    result: str = Field(pattern=r"^(Win|Loss|BE)$")
    r_multiple: Optional[float]=None
    notes: str=""
    screenshot: str=""

@app.get("/api/journal")
def api_journal(limit: int = 50) -> dict:
    return {"entries": journal.entries(min(limit,300)), "report": journal.weekly_report(), "setup_types": journal.SETUP_TYPES}

@app.post("/api/journal")
def api_journal_add(payload: JournalIn) -> dict:
    jid=journal.add_setup(payload.trade_date,payload.setup_type,payload.direction,payload.level,payload.trigger,payload.result,payload.r_multiple,payload.notes,payload.screenshot)
    return {"ok":True,"id":jid}

@app.get("/api/report")
def api_report() -> dict:
    return journal.weekly_report()

# -------------------- Full Automation API
class AutopilotStateIn(BaseModel):
    enabled: Optional[bool]=None
    mode: Optional[str]=None  # off|assist|full_auto
    paper_enabled: Optional[bool]=None

@app.get("/api/automation/state")
def get_autopilot_state() -> dict:
    return {"enabled": db.get_state("autopilot_enabled","0")=="1", "mode": db.get_state("autopilot_mode","assist"), "paper_enabled": db.get_state("paper_enabled","0")=="1", "auto_report_enabled": db.get_state("auto_report_enabled","0")=="1", "webhook_url": config.WEBHOOK_URL or ""}

@app.post("/api/automation/state")
def set_autopilot_state(payload: AutopilotStateIn) -> dict:
    if payload.enabled is not None:
        db.set_state("autopilot_enabled","1" if payload.enabled else "0")
    if payload.mode is not None:
        if payload.mode not in ("off","assist","full_auto"): raise HTTPException(400,"mode must be off|assist|full_auto")
        db.set_state("autopilot_mode", payload.mode)
    if payload.paper_enabled is not None:
        db.set_state("paper_enabled","1" if payload.paper_enabled else "0")
    return get_autopilot_state()

class RuleIn(BaseModel):
    name: str
    description: str = ""
    trigger_type: str
    trigger_params: dict = Field(default_factory=dict)
    action_type: str
    action_params: dict = Field(default_factory=dict)
    enabled: bool = True
    cooldown_s: int = 600
    priority: int = 5

@app.get("/api/automation/rules")
def list_rules(enabled_only: bool=False) -> dict:
    return {"rules": db.all_automation_rules(enabled_only=enabled_only)}

@app.post("/api/automation/rules")
def create_rule(payload: RuleIn) -> dict:
    rid=db.insert_rule(payload.name, payload.trigger_type, payload.trigger_params, payload.action_type, payload.action_params, description=payload.description, enabled=payload.enabled, cooldown_s=payload.cooldown_s, priority=payload.priority)
    return {"ok":True,"id":rid,"rule": db.get_rule(rid)}

@app.get("/api/automation/rules/{rule_id}")
def get_rule(rule_id:int) -> dict:
    r=db.get_rule(rule_id)
    if not r: raise HTTPException(404,"rule not found")
    return {"rule": r}

@app.put("/api/automation/rules/{rule_id}")
def update_rule(rule_id:int, payload: RuleIn) -> dict:
    if not db.get_rule(rule_id): raise HTTPException(404,"rule not found")
    db.update_rule(rule_id, name=payload.name, description=payload.description, trigger_type=payload.trigger_type, trigger_params=payload.trigger_params, action_type=payload.action_type, action_params=payload.action_params, enabled=1 if payload.enabled else 0, cooldown_s=payload.cooldown_s, priority=payload.priority)
    return {"ok":True,"rule": db.get_rule(rule_id)}

@app.patch("/api/automation/rules/{rule_id}/toggle")
def toggle_rule(rule_id:int) -> dict:
    r=db.get_rule(rule_id)
    if not r: raise HTTPException(404,"rule not found")
    db.update_rule(rule_id, enabled=0 if r["enabled"] else 1)
    return {"ok":True,"enabled": not r["enabled"]}

@app.delete("/api/automation/rules/{rule_id}")
def delete_rule(rule_id:int) -> dict:
    if not db.get_rule(rule_id): raise HTTPException(404,"rule not found")
    db.delete_rule(rule_id)
    return {"ok":True}

@app.post("/api/automation/rules/{rule_id}/test")
def test_rule(rule_id:int) -> dict:
    r=db.get_rule(rule_id)
    if not r: raise HTTPException(404,"rule not found")
    from .engines import automation as auto_eng, structural as struct_eng, macro as macro_eng
    s=struct_eng.compute(); m=macro_eng.compute(); comp=macro_eng.combined(s,m)
    scores={"structural":s,"macro":m,"composite":comp}
    spot=db.latest("spot_xau"); price=spot["value"] if spot else None
    # bypass cooldown for test
    orig_cd=r.get("cooldown_s"); orig_fired=r.get("last_fired_ts")
    r["cooldown_s"]=0; r["last_fired_ts"]=0
    ok, tctx = auto_eng.eval_trigger(r, {"scores":scores,"spot_price":price})
    fired=[]
    if ok:
        res=auto_eng.exec_action(r, tctx, scores, price)
        db.log_run(r["id"], r["trigger_type"], f"TEST Rule '{r['name']}'", "ok", context=tctx, result=res)
        fired.append(res)
    # restore (don't persist)
    return {"triggered": ok, "context": tctx, "results": fired}

@app.get("/api/automation/runs")
def list_runs(limit:int=50) -> dict:
    return {"runs": db.recent_runs(min(limit,200))}

@app.post("/api/automation/run-cycle")
def trigger_cycle() -> dict:
    from .engines import automation as auto_eng, structural as struct_eng, macro as macro_eng
    s=struct_eng.compute(); m=macro_eng.compute(); comp=macro_eng.combined(s,m)
    scores={"structural":s,"macro":m,"composite":comp}
    spot=db.latest("spot_xau"); price=spot["value"] if spot else None
    fired=auto_eng.run_cycle(scores, price, prev_stance=scheduler.status().get("prev_stance"))
    return {"fired": len(fired), "details": fired}

# signals & paper
@app.get("/api/signals")
def list_signals(limit:int=40) -> dict:
    return {"signals": db.recent_signals(min(limit,100))}

@app.post("/api/signals")
def create_signal_man(direction: str, price: float | None = None, reason: str = "manual") -> dict:
    spot=db.latest("spot_xau"); p=price or (spot["value"] if spot else 4085)
    from .engines import structural as struct_eng, macro as macro_eng
    s=struct_eng.compute(); m=macro_eng.compute(); comp=macro_eng.combined(s,m)
    sid=db.insert_signal(db.now(), "manual", direction, abs(comp["score"]), p, comp["score"], s["score"], m["score"], reason)
    return {"ok":True,"id":sid}

@app.get("/api/paper/positions")
def list_positions(limit:int=60) -> dict:
    return {"positions": db.all_positions(min(limit,100)), "open": db.open_positions(), "summary": db.paper_pnl_summary()}

@app.post("/api/paper/positions/open")
def open_position_api(direction: str, entry_price: float | None = None, size_oz: float = 5.0, stop_loss: float | None = None, take_profit: float | None = None, signal_id: int | None = None) -> dict:
    spot=db.latest("spot_xau"); price=entry_price or (spot["value"] if spot else 4085)
    if len(db.open_positions()) >= config.PAPER_MAX_OPEN:
        raise HTTPException(400, f"max open positions {config.PAPER_MAX_OPEN} reached")
    # need a signal anchor
    sid=signal_id
    if sid is None:
        from .engines import structural as struct_eng, macro as macro_eng
        s=struct_eng.compute(); m=macro_eng.compute(); comp=macro_eng.combined(s,m)
        sid=db.insert_signal(db.now(),"manual_trade",direction,abs(comp["score"]),price,comp["score"],s["score"],m["score"],"Manual paper entry")
    pid=db.open_position(sid, direction, price, size_oz, stop_loss=stop_loss, take_profit=take_profit, note="manual")
    return {"ok":True,"position_id":pid}

@app.post("/api/paper/positions/{pid}/close")
def close_position_api(pid:int, exit_price: float | None = None) -> dict:
    spot=db.latest("spot_xau"); price=exit_price or (spot["value"] if spot else 4085)
    p=db.close_position(pid, price)
    if not p: raise HTTPException(404,"position not found")
    return {"ok":True,"position":p}

@app.get("/api/paper/pnl")
def paper_pnl() -> dict:
    return db.paper_pnl_summary()

@app.get("/api/automation/reports")
def list_reports(limit:int=20) -> dict:
    return {"reports": db.recent_reports(min(limit,50))}

@app.post("/api/automation/reports/generate")
def generate_report_api(period: str="daily") -> dict:
    from .engines.signals import generate_daily_report
    payload=generate_daily_report()
    return {"ok":True,"report":payload}

@app.patch("/api/layers/{source}/toggle")
def toggle_layer(source:str) -> dict:
    cur=db.get_state(f"ingestor:{source}:enabled","1")
    newv="0" if cur=="1" else "1"
    db.set_state(f"ingestor:{source}:enabled", newv)
    return {"ok":True,"source":source,"enabled": newv=="1"}

@app.get("/api/bootstrap")
def api_bootstrap() -> dict:
    scores=api_scores(); ingest=db.ingest_status()
    charts={name:_series_payload(name, points=pts) for name,pts in [("spot_xau",500),("comex_gc",260),("tips_10y",120),("dxy",120),("brent",120),("fedwatch_cut_prob",120),("gld_tonnes",120),("iau_tonnes",120),("comex_registered",120),("comex_eligible",120),("sge_premium",120),("india_premium",120),("sge_withdrawals",26),("cot_mm_net",26),("cot_comm_net",26),("cb_net_purchases",12),("swiss_gold_exports",12),("china_gold_imports",12),("inst_gold_alloc_pct",8),("inst_gold_fund_count",8),("options_oi",120)]}
    return {"now":db.now(),"layers":api_layers()["layers"],"scores":scores,"charts":charts,"whales":api_whales(30),"events":api_events(),"alerts":api_alerts(40),"journal":api_journal(40),"telegram_configured":config.telegram_configured(),"mode_note":("LIVE keys configured" if config.telegram_configured() or config.FRED_API_KEY else "SIM mode — add API keys (.env) to switch layers live"),"autopilot":get_autopilot_state(),"automation":{"rules": db.all_automation_rules(), "runs": db.recent_runs(12), "signals": db.recent_signals(12), "positions": db.all_positions(20), "pnl": db.paper_pnl_summary(), "reports": db.recent_reports(5)}, "ingest_status": ingest}

@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
