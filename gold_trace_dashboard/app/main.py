"""Gold Trace — FastAPI app.

Serves the dashboard (static/) and a small JSON API. One aggregate
endpoint (/api/bootstrap) feeds the whole UI so phones make a single
request per refresh cycle.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config, db, scheduler, seed
from .engines import anomalies, events as events_engine, journal, macro as macro_engine, structural

STATIC_DIR = config.BASE_DIR / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    seeded = seed.seed_if_empty()
    scheduler.start()
    yield
    scheduler.stop()


app = FastAPI(
    title="Gold Trace API",
    description="Layered gold-market intelligence: no data source tracks "
                "physical gold live, so every source runs on its own update "
                "speed and feeds the structural/macro scores, event engine, "
                "anomaly alerts and trading journal.",
    version="1.0.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

DAY = 86400


# ------------------------------------------------------------------ helpers
def _series_payload(name: str, points: int = 260) -> dict[str, Any]:
    rows = db.series_window(name, points=points)
    last = rows[-1] if rows else None
    return {
        "name": name,
        "points": [{"t": r["ts"], "v": r["value"]} for r in rows],
        "last": last["value"] if last else None,
        "mode": last["mode"] if last else None,
        "extra": last["extra"] if last else None,
    }


# ------------------------------------------------------------------ routes
@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True, "db": config.DB_PATH,
            "telegram": config.telegram_configured()}


@app.get("/api/status")
def api_status() -> dict:
    return {
        "scheduler": scheduler.status(),
        "ingest": db.ingest_status(),
        "counts": {
            "observations": db.table_count("observations"),
            "whales": db.table_count("whale_transfers"),
            "alerts": db.table_count("alerts"),
            "journal": db.table_count("journal"),
        },
    }


@app.get("/api/layers")
def api_layers() -> dict:
    from .ingestors import LAYER_META
    ingest = db.ingest_status()
    out = []
    for m in LAYER_META:
        st = ingest.get(m["source"], {})
        out.append({**m,
                    "last_ts": st.get("last_ts"),
                    "mode": st.get("mode", "—"),
                    "status": st.get("status", "pending"),
                    "latency_ms": st.get("latency_ms")})
    return {"layers": out}


@app.get("/api/scores")
def api_scores() -> dict:
    s = structural.compute()
    m = macro_engine.compute()
    c = macro_engine.combined(s, m)
    return {"structural": s, "macro": m, "composite": c}


@app.get("/api/series/{name}")
def api_series(name: str, points: int = 260) -> dict:
    payload = _series_payload(name, min(points, 4000))
    if not payload["points"]:
        raise HTTPException(404, f"unknown series: {name}")
    return payload


@app.get("/api/whales")
def api_whales(limit: int = 40) -> dict:
    spot = db.latest("spot_xau")
    px = spot["value"] if spot else None
    rows = db.recent_whales(min(limit, 200))
    agg_24h = sum(r["amount_oz"] for r in rows if r["ts"] >= db.now() - DAY)
    return {"transfers": rows, "spot": px, "agg_24h_oz": round(agg_24h)}


@app.get("/api/events")
def api_events() -> dict:
    return {
        "upcoming": events_engine.upcoming(),
        "history": events_engine.history(),
        "credibility": events_engine.matrix_credibility(),
    }


@app.get("/api/alerts")
def api_alerts(limit: int = 60) -> dict:
    return {"alerts": db.recent_alerts(min(limit, 200)),
            "telegram_configured": config.telegram_configured()}


class JournalIn(BaseModel):
    trade_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    setup_type: str
    direction: str = Field(pattern=r"^(LONG|SHORT)$")
    level: Optional[float] = None
    trigger: str = ""
    result: str = Field(pattern=r"^(Win|Loss|BE)$")
    r_multiple: Optional[float] = None
    notes: str = ""
    screenshot: str = ""


@app.get("/api/journal")
def api_journal(limit: int = 50) -> dict:
    return {"entries": journal.entries(min(limit, 300)),
            "report": journal.weekly_report(),
            "setup_types": journal.SETUP_TYPES}


@app.post("/api/journal")
def api_journal_add(payload: JournalIn) -> dict:
    jid = journal.add_setup(
        payload.trade_date, payload.setup_type, payload.direction,
        payload.level, payload.trigger, payload.result, payload.r_multiple,
        payload.notes, payload.screenshot)
    return {"ok": True, "id": jid}


@app.get("/api/report")
def api_report() -> dict:
    return journal.weekly_report()


@app.get("/api/bootstrap")
def api_bootstrap() -> dict:
    """Everything the dashboard needs, in one request."""
    scores = api_scores()
    charts = {
        name: _series_payload(name, points=pts)
        for name, pts in [
            ("spot_xau", 500), ("comex_gc", 260),
            ("tips_10y", 120), ("dxy", 120), ("brent", 120),
            ("fedwatch_cut_prob", 120),
            ("gld_tonnes", 120), ("iau_tonnes", 120),
            ("comex_registered", 120), ("comex_eligible", 120),
            ("sge_premium", 120), ("india_premium", 120),
            ("sge_withdrawals", 26),
            ("cot_mm_net", 26), ("cot_comm_net", 26),
            ("cb_net_purchases", 12),
            ("swiss_gold_exports", 12), ("china_gold_imports", 12),
            ("inst_gold_alloc_pct", 8), ("inst_gold_fund_count", 8),
            ("options_oi", 120),
        ]
    }
    return {
        "now": db.now(),
        "layers": api_layers()["layers"],
        "scores": scores,
        "charts": charts,
        "whales": api_whales(30),
        "events": api_events(),
        "alerts": api_alerts(40),
        "journal": api_journal(40),
        "telegram_configured": config.telegram_configured(),
        "mode_note": ("LIVE keys configured" if config.telegram_configured()
                      or config.FRED_API_KEY else
                      "SIM mode — add API keys (.env) to switch layers live"),
    }


# ------------------------------------------------------------------- static
@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
