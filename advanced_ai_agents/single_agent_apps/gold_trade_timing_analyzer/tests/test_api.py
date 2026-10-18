"""
End-to-end API test for the Gold Timing Desk backend.

Runs the FastAPI app in-process through Starlette's TestClient against a
generated but structurally realistic XAU/USD series, and exercises the whole
path a browser would: ingest -> analyse -> backtest -> params -> news -> alerts.

    python -m pytest tests/test_api.py -q

The generator deliberately produces real wicks (high/low drawn from a
half-normal around the close). A wickless series yields zero CISD events and the
test would pass for the wrong reason.
"""
from __future__ import annotations

import os
import sys
import tempfile

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(HERE)
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

os.environ.setdefault("GOLD_TIMING_DB", os.path.join(tempfile.mkdtemp(), "test_journal.db"))

from fastapi.testclient import TestClient  # noqa: E402

from server.main import app  # noqa: E402

client = TestClient(app)
N = 3000
IV = "1h"

UNIVERSE = ["XAGUSD", "DXY", "US10Y", "VIX", "SPX", "WTI", "COPPER", "IEF", "TIP"]


def make_bars(n: int, start: float, vol: float, seed: int, drift: float = 0.0) -> list:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq=IV, tz="UTC")
    step = rng.normal(drift, vol, n)
    # regimes so the series actually trends and mean-reverts
    for _ in range(6):
        a, b = rng.integers(0, n - 200, 2)
        a, b = int(min(a, b)), int(max(a, b))
        step[a:b] += float(rng.normal(0, vol * 2.2))
    close = start * np.exp(np.cumsum(step))
    open_ = np.concatenate([[start], close[:-1]])
    wick = np.abs(rng.normal(0, 1, n)) * close * 0.0018
    high = np.maximum(open_, close) + wick * rng.uniform(0.4, 1.6, n)
    low = np.minimum(open_, close) - wick * rng.uniform(0.4, 1.6, n)
    volume = rng.lognormal(6, 0.4, n)
    return [[int(t.value // 1_000_000), float(o), float(h), float(l), float(c), float(v)]
            for t, o, h, l, c, v in zip(idx, open_, high, low, close, volume)]


def build_payload() -> dict:
    series = {"XAUUSD": {"source": "test:generator", "kind": "synthetic",
                         "note": "structurally realistic synthetic sample",
                         "bars": make_bars(N, 2000.0, 0.0022, 7)}}
    vols = {"XAGUSD": 0.004, "DXY": 0.0012, "US10Y": 0.006, "VIX": 0.02, "SPX": 0.003,
            "WTI": 0.005, "COPPER": 0.004, "IEF": 0.0015, "TIP": 0.0013}
    starts = {"XAGUSD": 24.0, "DXY": 104.0, "US10Y": 4.2, "VIX": 16.0, "SPX": 5000.0,
              "WTI": 78.0, "COPPER": 4.1, "IEF": 95.0, "TIP": 108.0}
    for i, sym in enumerate(UNIVERSE):
        series[sym] = {"source": "test:generator", "kind": "synthetic", "note": "",
                       "bars": make_bars(N, starts[sym], vols[sym], 100 + i)}
    return {"interval": IV, "series": series,
            "spot": {"price": 2410.55, "source": "test", "kind": "real"}}


PAYLOAD = build_payload()


def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["ok"] is True


def test_ingest():
    r = client.post("/api/market/ingest", json=PAYLOAD)
    assert r.status_code == 200
    body = r.json()
    assert "XAUUSD" in body["loaded"], body
    assert len(body["loaded"]) >= 8


def test_analyze():
    r = client.post("/api/analyze", json={})
    assert r.status_code == 200, r.text
    a = r.json()
    assert "error" not in a, a
    for model in ("tcisd", "fib", "market_neutral", "reversal", "ssmt", "exhaustion"):
        assert model in a["models"], (model, list(a["models"]))
    assert a["signal"]["tier"] in ("A", "B", "C", "-")
    assert len(a["candles"]) > 100
    assert len(a["panel"]["ts"]) > 100
    assert len(a["structure"]["legs"]) > 0
    assert client.get("/api/analysis").status_code == 200


def test_params():
    r = client.get("/api/params")
    assert r.status_code == 200 and "min_conviction" in r.json()["params"]
    r = client.post("/api/params", json={"values": {"min_conviction": 45, "rev_gate": 0.9}})
    assert r.status_code == 200
    assert r.json()["params"]["min_conviction"] == 45
    client.post("/api/params", json={"values": {}})   # restore defaults


def test_backtest():
    r = client.post("/api/backtest", json={})
    assert r.status_code == 200, r.text
    b = r.json()
    assert "error" not in b, b
    assert b["meta"]["bars"] == N
    assert isinstance(b["funnel"], list) and b["funnel"]
    assert isinstance(b["delay"], list) and b["delay"]
    assert isinstance(b["trades"], list)
    assert isinstance(b["sensitivity"], list)
    if b["trades"]:
        assert "r" in b["trades"][0]
        assert {"trades", "win_rate", "expectancy_r"} <= set(b["metrics"])
    assert client.get("/api/backtest").status_code == 200


def test_news_lexicon():
    feed = """<rss><channel><item>
        <title>Fed signals slower pace of rate cuts; dollar firms</title>
        <pubDate>Mon, 14 Oct 2024 12:00:00 GMT</pubDate>
        <description>Gold slips as real yields rise after hawkish Fed commentary.</description>
    </item><item>
        <title>CPI通胀数据提振避险需求，黄金上涨</title>
        <pubDate>Mon, 14 Oct 2024 13:00:00 GMT</pubDate>
        <description>Safe-haven demand supports bullion.</description>
    </item></channel></rss>"""
    r = client.post("/api/news/ingest", json={"feeds": {"test": feed}})
    assert r.status_code == 200
    n = r.json()["news"]
    assert n["n"] == 2
    assert -1.0 <= n["bias"] <= 1.0
    assert 0.6 <= n["event_penalty"] <= 1.0
    assert client.get("/api/news").status_code == 200


def test_alerts_config_and_log_channel():
    cfg = {"enabled": True, "min_tier": "C", "min_score": 0.0,
           "directions": ["LONG", "SHORT"], "cooldown_bars": 0,
           "poll_seconds": 60,
           "channels": [{"type": "log", "enabled": True, "url": "", "token": "", "chat_id": ""}]}
    r = client.post("/api/alerts/config", json=cfg)
    assert r.status_code == 200
    assert r.json()["config"]["min_tier"] == "C"

    # force an evaluation; whether it fires depends on the sample, but the
    # endpoint must always answer
    r = client.post("/api/alerts/check", json={})
    assert r.status_code == 200
    assert "would_fire" in r.json()

    r = client.post("/api/alerts/test", json={"channel": cfg["channels"][0]})
    assert r.status_code == 200 and r.json()["ok"] is True

    a = client.get("/api/alerts").json()
    assert a["config"]["min_tier"] == "C"
    assert isinstance(a["log"], list)
    assert a["log"], "a test dispatch should be logged"


def test_journal_roundtrip():
    j = client.get("/api/journal").json()
    assert "signals" in j and "stats" in j
    assert client.post("/api/journal/grade").status_code == 200
    assert client.delete("/api/journal").status_code == 200
    assert client.get("/api/journal").json()["signals"] == []


def test_websocket_hub():
    with client.websocket_connect("/ws") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello"
        assert "status" in hello["data"]
        ws.send_json({"type": "ping"})
        assert ws.receive_json()["type"] == "pong"
        ws.send_json({"type": "spot", "price": 2411.0, "source": "test", "kind": "real"})
        msg = ws.receive_json()
        assert msg["type"] == "price" and msg["data"]["price"] == 2411.0


def test_no_nan_in_any_payload():
    """Starlette serialises with allow_nan=False, so a stray NaN is a 500."""
    for path in ("/api/analysis", "/api/backtest", "/api/status", "/api/news", "/api/alerts"):
        r = client.get(path)
        assert r.status_code == 200, path
        assert "NaN" not in r.text and "Infinity" not in r.text, path
