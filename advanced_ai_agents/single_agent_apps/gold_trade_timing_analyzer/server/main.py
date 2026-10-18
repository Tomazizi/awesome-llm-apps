"""
Gold Timing Desk — FastAPI backend.

Serves the analytics engine as a JSON/WebSocket API and the built SPA as static
files, so the whole app runs from a single port.

Data flow note (important in restricted networks)
-------------------------------------------------
The Python process may have no outbound internet. The browser always does. So
the SPA fetches market data itself and POSTs it to `/api/market/ingest`; when
the server *can* reach the internet (a normal local or cloud run) it uses the
direct providers instead. `/api/status` reports which path is in play, and the
UI shows it. Nothing is ever silently substituted.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

import pandas as pd
from fastapi import Body, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

HERE = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(HERE)
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

from core.config import ASSETS, Params  # noqa: E402
from core import data_feeds as dfeed  # noqa: E402
from core import research_agent as ra  # noqa: E402

from .models import (AlertConfig, AlertTestIn, IngestPayload, NewsIngestPayload,  # noqa: E402
                     ResearchIn)
from .store import Journal, MarketStore, json_safe  # noqa: E402
from .engine_service import (analyse, grade_open_signals, params_from,  # noqa: E402
                             run_backtest)
from .alerts import AlertManager  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("gold_timing")

DIST = os.path.join(APP_DIR, "web", "dist")

@asynccontextmanager
async def lifespan(_: FastAPI):
    tasks = [asyncio.create_task(alert_loop()), asyncio.create_task(grade_loop())]
    _log("Gold Timing Desk backend started")
    try:
        yield
    finally:
        for t in tasks:
            t.cancel()

app = FastAPI(title="Gold Trade Entry Timing Analyzer", version="1.0.0",
              docs_url="/api/docs", openapi_url="/api/openapi.json",
              lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                   allow_headers=["*"], allow_credentials=False)

store = MarketStore()
journal = Journal()
alerts = AlertManager(journal)

STATE: dict = {
    "params": {},
    "analysis": None,
    "backtest": None,
    "news": None,
    "bundle": None,
    "logs": [],            # recent server-side fetch attempts
    "events": [],          # recent WS-broadcast events, newest first
    "last_signal_ts": None,
}


def _log(msg: str, level: str = "info") -> None:
    entry = {"ts": datetime.now(timezone.utc).isoformat(), "level": level, "msg": msg}
    STATE["logs"].append(entry)
    STATE["logs"] = STATE["logs"][-200:]
    getattr(log, level if level in ("info", "warning", "error") else "info")(msg)


# ----------------------------------------------------------------------------
# WebSocket hub
# ----------------------------------------------------------------------------

class Hub:
    def __init__(self) -> None:
        self.clients: List[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.clients.append(ws)

    def disconnect(self, ws: WebSocket) -> None:
        if ws in self.clients:
            self.clients.remove(ws)

    async def broadcast(self, kind: str, data) -> None:
        msg = json.dumps({"type": kind, "data": json_safe(data),
                          "ts": datetime.now(timezone.utc).isoformat()},
                         default=str)
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send_text(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


hub = Hub()


def push_event(kind: str, data) -> None:
    """Record notable events so a freshly connected client gets context."""
    STATE["events"].insert(0, {"type": kind, "data": json_safe(data),
                               "ts": datetime.now(timezone.utc).isoformat()})
    STATE["events"] = STATE["events"][:60]


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await hub.connect(ws)
    try:
        await ws.send_text(json.dumps({
            "type": "hello",
            "data": {"status": store.status(), "events": STATE["events"][:20]},
            "ts": datetime.now(timezone.utc).isoformat(),
        }))
        while True:
            # client may send pings or a spot tick; keep the socket alive
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            if msg.get("type") == "ping":
                await ws.send_text(json.dumps({"type": "pong"}))
            elif msg.get("type") == "spot" and msg.get("price"):
                store.set_spot(float(msg["price"]), str(msg.get("source", "browser")),
                               str(msg.get("kind", "real")))
                await hub.broadcast("price", store.spot)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        hub.disconnect(ws)


# ----------------------------------------------------------------------------
# Health / status
# ----------------------------------------------------------------------------

@app.get("/api/health")
async def health():
    return {"ok": True, "version": "1.0.0",
            "server_time": datetime.now(timezone.utc).isoformat()}


@app.get("/api/status")
async def status():
    st = store.status()
    st["server_has_network"] = dfeed.server_has_network()
    st["providers"] = {
        "primary": ["yahoo:GC=F", "binance:PAXGUSDT", "bitfinex:tXAUUSD", "kraken:PAXGUSD",
                    "coinbase:PAXG-USD", "coingecko:pax-gold", "stooq:xauusd"],
        "universe": [f"{a}:{ASSETS[a].get('yahoo')}" for a in ASSETS
                     if a != "XAUUSD" and ASSETS[a].get("yahoo")],
    }
    st["journal"] = journal.stats()
    st["alerts"] = alerts.config_dict()
    st["signal_count"] = len(journal.list_signals(500))
    st["params"] = params_from(STATE["params"]).as_dict()
    return st


@app.get("/api/events")
async def events():
    return {"events": STATE["events"], "logs": STATE["logs"][-60:]}


# ----------------------------------------------------------------------------
# Market data
# ----------------------------------------------------------------------------

@app.post("/api/market/ingest")
async def ingest(payload: IngestPayload):
    """Accept OHLCV pushed by the browser (or any client) and recompute."""
    res = store.ingest(payload.interval, payload.series, payload.spot)
    _log(f"ingested {len(res['loaded'])} series: {', '.join(res['loaded']) or 'none'}")
    if res["loaded"]:
        await hub.broadcast("status", store.status())
        await recompute(reason="ingest")
    return {"ok": True, **res}


@app.post("/api/spot")
async def spot_update(body: dict = Body(...)):
    price = body.get("price")
    store.set_spot(float(price) if price else None, str(body.get("source", "browser")),
                   str(body.get("kind", "real")))
    await hub.broadcast("price", store.spot)
    return {"ok": True, "spot": store.spot}


@app.post("/api/market/refresh")
async def refresh(body: dict = Body(default={})):
    """Server-side fetch. Works when the process has outbound internet."""
    interval = str(body.get("interval") or store.interval or "1h")
    bars = int(body.get("bars") or 2200)
    universe = body.get("universe") or [a for a in ASSETS if a != "XAUUSD"]

    if not dfeed.server_has_network():
        return {"ok": False, "reason": "no_server_network",
                "message": "This server cannot reach market data hosts. "
                           "The browser will fetch instead."}

    flog = dfeed.FetchLog()
    frames = {}
    try:
        frames["XAUUSD"] = dfeed.fetch_asset("XAUUSD", interval, bars, flog,
                                             use_browser=False, csv_dir=os.path.join(APP_DIR, "data"))
        got = dfeed.fetch_yahoo_batch(universe, interval, flog, use_browser=False)
        frames.update(got)
    except Exception as e:
        return {"ok": False, "reason": "error", "message": str(e)}

    series = {}
    for sym, fr in frames.items():
        d = fr.df
        series[sym] = {
            "source": fr.source, "kind": fr.kind, "note": fr.note,
            "bars": [[int(t.value // 1_000_000), float(o), float(h), float(l), float(c), float(v)]
                     for t, o, h, l, c, v in zip(d.index, d["open"], d["high"], d["low"],
                                                 d["close"], d["volume"])],
        }
    res = store.ingest(interval, {k: type("S", (), {"source": v["source"], "kind": v["kind"],
                                                    "note": v["note"], "bars": v["bars"]})
                                  for k, v in series.items()})
    STATE["logs"].extend(flog.to_df().to_dict(orient="records"))
    STATE["logs"] = STATE["logs"][-200:]
    sp = dfeed.fetch_spot(use_browser=False)
    if sp.get("price"):
        store.set_spot(sp["price"], sp["source"], sp["kind"])
    if res["loaded"]:
        await recompute(reason="refresh")
    return {"ok": True, **res, "log": flog.to_df().to_dict(orient="records")}


@app.post("/api/market/clear")
async def clear_market():
    store.clear()
    STATE["analysis"] = STATE["backtest"] = STATE["bundle"] = None
    await hub.broadcast("status", store.status())
    return {"ok": True}


# ----------------------------------------------------------------------------
# Analysis
# ----------------------------------------------------------------------------

async def recompute(reason: str = "manual") -> Optional[dict]:
    """Re-run the engine, cache the result, grade the journal, evaluate alerts."""
    frames = store.snapshot()
    if "XAUUSD" not in frames:
        return None
    params = params_from(STATE["params"])
    news = STATE["news"] or {}
    try:
        res = await asyncio.to_thread(
            analyse, frames, store.interval, params,
            store.spot.get("price"), float(news.get("bias", 0.0) or 0.0),
            float(news.get("event_penalty", 1.0) or 1.0))
    except Exception as e:
        import traceback
        traceback.print_exc()
        _log(f"analysis failed: {e}", "error")
        return None

    STATE["bundle"] = res.pop("bundle", None)
    STATE["analysis"] = res
    await hub.broadcast("analysis", {k: v for k, v in res.items() if k != "candles"})

    # grade previously alerted signals against the newest prices
    try:
        graded = grade_open_signals(frames, store.interval, params, journal)
        if graded:
            push_event("graded", {"count": graded})
            await hub.broadcast("journal", {"graded": graded, "stats": journal.stats()})
    except Exception as e:
        _log(f"grading failed: {e}", "error")

    # alerts
    try:
        trade = alerts.should_fire(res, store.interval)
        if trade:
            sig_row = {
                "ts": res["signal"]["bar"], "symbol": "XAUUSD", "interval": store.interval,
                "direction": trade["direction"], "score": trade["score"], "tier": trade["tier"],
                "entry": trade["entry"], "stop": trade["stop"], "tp1": trade["tp1"],
                "tp2": trade["tp2"], "tp3": trade["tp3"], "rr": trade["rr"],
                "size": trade["size"], "risk_usd": trade["risk_usd"],
                "atr": res["signal"].get("atr"), "components": trade.get("components"),
                "gates": res["signal"].get("gates"),
                "news_bias": (STATE["news"] or {}).get("bias"),
                "created_at": time.time(),
                "bar_index": len(frames["XAUUSD"]) - 1,
            }
            sid = journal.save_signal(sig_row)
            alerts.mark(trade, res, sid)
            results = await alerts.dispatch(sid, trade, res)
            payload = {"signal": {**(sig_row), "id": sid}, "dispatch": results,
                       "message": alerts.format_message(trade, res)}
            push_event("alert", payload)
            await hub.broadcast("alert", payload)
            _log(f"ALERT {trade['direction']} tier {trade['tier']} "
                 f"score {trade['score']:.0f} -> {[r['channel'] for r in results]}")
    except Exception as e:
        import traceback
        traceback.print_exc()
        _log(f"alert evaluation failed: {e}", "error")
    return res


@app.post("/api/analyze")
async def analyze(body: dict = Body(default={})):
    if body.get("params"):
        STATE["params"] = body["params"]
    res = await recompute(reason="analyze")
    if res is None:
        raise HTTPException(400, "No XAUUSD data loaded. Ingest market data first.")
    return res


@app.get("/api/analysis")
async def get_analysis():
    if STATE["analysis"] is None:
        raise HTTPException(404, "No analysis yet.")
    return STATE["analysis"]


@app.post("/api/backtest")
async def backtest(body: dict = Body(default={})):
    if body.get("params"):
        STATE["params"] = body["params"]
    frames = store.snapshot()
    if "XAUUSD" not in frames:
        raise HTTPException(400, "No XAUUSD data loaded.")
    res = await asyncio.to_thread(run_backtest, frames, store.interval, params_from(STATE["params"]))
    STATE["backtest"] = res
    await hub.broadcast("backtest", {k: v for k, v in res.items() if k != "trades"})
    return res


@app.get("/api/backtest")
async def get_backtest():
    if STATE["backtest"] is None:
        raise HTTPException(404, "No backtest yet.")
    return STATE["backtest"]


@app.post("/api/params")
async def set_params(body: dict = Body(default={})):
    STATE["params"] = body.get("values") or {}
    p = params_from(STATE["params"])
    await recompute(reason="params")
    return {"ok": True, "params": p.as_dict()}


@app.get("/api/params")
async def get_params():
    return {"params": params_from(STATE["params"]).as_dict()}


# ----------------------------------------------------------------------------
# News + research
# ----------------------------------------------------------------------------

@app.post("/api/news/ingest")
async def news_ingest(payload: NewsIngestPayload):
    """Parse and score RSS bodies captured by the browser."""
    rows = []
    for name, xml in (payload.feeds or {}).items():
        try:
            rows.extend(ra.parse_feed(xml or "", name))
        except Exception as e:
            _log(f"feed parse failed {name}: {e}", "warning")
    if not rows:
        STATE["news"] = {"bias": 0.0, "confidence": 0.0, "event_penalty": 1.0,
                         "n": 0, "headlines": [], "events": [], "source": "none"}
        return {"ok": True, "news": STATE["news"]}
    df = pd.DataFrame(rows).drop_duplicates(subset=["title"]).sort_values("ts", ascending=False)
    df = ra.classify_news(df)
    det = ra.news_bias(df)
    det["headlines"] = json_safe(
        df.assign(ts=df["ts"].apply(lambda t: t.isoformat() if pd.notna(t) else None))
          .head(80).to_dict(orient="records"))
    det["events"] = json_safe(ra.detect_events(df).to_dict(orient="records"))
    det["source"] = "lexicon"
    STATE["news"] = det
    await hub.broadcast("news", det)
    return {"ok": True, "news": det}


@app.get("/api/news")
async def get_news():
    return {"news": STATE["news"]}


@app.post("/api/research")
async def research(body: ResearchIn):
    """LLM brief. Server-side when the process has internet.

    If the caller already made the call from the browser (`payload` set), the
    backend just parses and blends it, so a locked-down server still gets a
    full research brief.
    """
    brief = None
    if body.payload:
        brief = {"raw": body.payload.get("text", ""), "json": body.payload.get("json")}
    elif body.api_key:
        try:
            news_df = pd.DataFrame(STATE["news"].get("headlines", [])) if STATE.get("news") else pd.DataFrame()
            ctx = ra.build_context(STATE["analysis"] or {"score_long": 0, "score_short": 0},
                                   STATE["bundle"] or {"panel": pd.DataFrame()},
                                   store.spot.get("price"))
            brief = ra.llm_brief(body.provider, body.model or None, body.api_key,
                                 news_df, ctx, base_url=body.base_url)
        except Exception as e:
            return JSONResponse({"ok": False, "error": f"{type(e).__name__}: {e}"}, status_code=200)
    else:
        return JSONResponse({"ok": False, "error": "No API key and no browser payload."},
                            status_code=200)

    det = STATE.get("news") or {"bias": 0.0, "confidence": 0.0, "event_penalty": 1.0}
    resolved = ra.resolve_bias(brief, det)
    out = {"ok": True, "bias": resolved, "brief": brief}
    if STATE.get("news") is not None:
        STATE["news"].update(resolved)
    await hub.broadcast("news", STATE.get("news"))
    return out


# ----------------------------------------------------------------------------
# Journal
# ----------------------------------------------------------------------------

@app.get("/api/journal")
async def get_journal(limit: int = 200):
    return {"signals": json_safe(journal.list_signals(limit)), "stats": journal.stats()}


@app.post("/api/journal/grade")
async def grade_now():
    frames = store.snapshot()
    if "XAUUSD" not in frames:
        raise HTTPException(400, "No data loaded.")
    n = grade_open_signals(frames, store.interval, params_from(STATE["params"]), journal)
    return {"ok": True, "graded": n, "stats": journal.stats()}


@app.delete("/api/journal")
async def reset_journal():
    with journal._connect() as conn:
        conn.execute("DELETE FROM signals")
        conn.execute("DELETE FROM alerts")
        conn.commit()
    return {"ok": True, "stats": journal.stats()}


# ----------------------------------------------------------------------------
# Alerts
# ----------------------------------------------------------------------------

@app.get("/api/alerts")
async def get_alerts():
    return {"config": alerts.config_dict(),
            "log": json_safe(journal.list_alerts(100)),
            "stats": journal.stats()}


@app.post("/api/alerts/config")
async def set_alert_config(cfg: AlertConfig):
    saved = alerts.update(cfg)
    _log(f"alert config updated: tier>={saved.min_tier} score>={saved.min_score} "
         f"channels={[c.type for c in saved.channels]}")
    await hub.broadcast("alert_config", saved.model_dump())
    return {"ok": True, "config": saved.model_dump()}


@app.post("/api/alerts/test")
async def test_alert(body: AlertTestIn):
    res = await alerts.test(body.channel)
    await hub.broadcast("alert_test", res)
    return {"ok": res["ok"], **res}


@app.post("/api/alerts/check")
async def check_alerts():
    """Force an alert evaluation against the current analysis."""
    if STATE["analysis"] is None:
        await recompute(reason="alert-check")
    if STATE["analysis"] is None:
        raise HTTPException(400, "No analysis available.")
    trade = alerts.should_fire(STATE["analysis"], store.interval)
    return {"ok": True, "would_fire": trade is not None,
            "config": alerts.config_dict(),
            "current": (STATE["analysis"].get("signal") or {}).get("trade")}


# ----------------------------------------------------------------------------
# Background loops
# ----------------------------------------------------------------------------

async def alert_loop():
    while True:
        try:
            cfg = alerts.config
            if cfg.enabled and "XAUUSD" in store.frames:
                await recompute(reason="poll")
        except Exception as e:
            _log(f"alert loop error: {e}", "error")
        await asyncio.sleep(max(15, int(alerts.config.poll_seconds or 60)))


async def grade_loop():
    while True:
        await asyncio.sleep(300)
        try:
            if "XAUUSD" in store.frames:
                n = grade_open_signals(store.snapshot(), store.interval,
                                       params_from(STATE["params"]), journal)
                if n:
                    await hub.broadcast("journal", {"graded": n, "stats": journal.stats()})
        except Exception as e:
            _log(f"grade loop error: {e}", "error")



# ----------------------------------------------------------------------------
# Static SPA (served last so API routes win)
# ----------------------------------------------------------------------------

if os.path.isdir(DIST):
    app.mount("/assets", StaticFiles(directory=os.path.join(DIST, "assets")), name="assets")

    @app.get("/")
    async def index():
        return FileResponse(os.path.join(DIST, "index.html"))

    @app.get("/{full_path:path}")
    async def spa(full_path: str):
        f = os.path.join(DIST, full_path)
        if os.path.isfile(f):
            return FileResponse(f)
        return FileResponse(os.path.join(DIST, "index.html"))
else:
    @app.get("/")
    async def placeholder():
        return JSONResponse({
            "ok": False,
            "message": "Frontend not built. Run: cd web && npm install && npm run build",
            "api": "/api/docs",
        })
