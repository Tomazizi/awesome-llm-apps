"""
Shared application state: the in-memory market store, the SQLite signal journal
and persisted settings.

SQLite is used through the standard library so the backend has no ORM
dependency; the journal is what turns a signal generator into something with a
verifiable track record.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(HERE)
DB_PATH = os.environ.get("GOLD_TIMING_DB", os.path.join(APP_DIR, "journal.db"))

OHLC = ["open", "high", "low", "close", "volume"]


# ----------------------------------------------------------------------------
# JSON helpers - the engine leaks numpy scalars and NaN everywhere
# ----------------------------------------------------------------------------


def json_safe(obj):
    if obj is None:
        return None
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating, float)):
        v = float(obj)
        return None if (np.isnan(v) or np.isinf(v)) else v
    if isinstance(obj, (np.bool_, bool)):
        return bool(obj)
    if isinstance(obj, (np.integer, int)):
        return int(obj)
    if isinstance(obj, np.ndarray):
        return json_safe(obj.tolist())
    if isinstance(obj, (pd.Timestamp,)):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {str(k): json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [json_safe(v) for v in obj]
    if isinstance(obj, pd.Series):
        return json_safe(obj.tolist())
    if isinstance(obj, pd.DataFrame):
        return json_safe(obj.to_dict(orient="list"))
    if obj is pd.NaT:
        return None
    try:
        if pd.isna(obj):
            return None
    except (TypeError, ValueError):
        pass
    # catch-all: numpy scalars of exotic dtypes, pandas intervals, anything else
    for conv in (float, int, str):
        try:
            v = conv(obj)
            if isinstance(v, float) and (np.isnan(v) or np.isinf(v)):
                return None
            return v
        except Exception:
            continue
    return str(obj)


# ----------------------------------------------------------------------------
# Market store
# ----------------------------------------------------------------------------


@dataclass
class SeriesMeta:
    symbol: str
    source: str = "unknown"
    kind: str = "real"
    note: str = ""
    bars: int = 0
    updated_at: float = field(default_factory=time.time)


class MarketStore:
    """Thread-safe holder for ingested OHLCV frames."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.frames: Dict[str, pd.DataFrame] = {}
        self.meta: Dict[str, SeriesMeta] = {}
        self.interval: str = "1h"
        self.spot: dict = {"price": None, "source": "unavailable", "kind": "none", "ts": None}
        self.updated_at: float = 0.0

    # -- write ------------------------------------------------------------
    def ingest(self, payload_interval: str, series: Dict[str, "SeriesIn"], spot=None) -> dict:
        with self._lock:
            loaded, skipped = [], []
            for sym, s in (series or {}).items():
                if not s.bars or len(s.bars) < 30:
                    skipped.append(sym)
                    continue
                try:
                    arr = np.asarray(s.bars, dtype=float)
                    if arr.ndim != 2 or arr.shape[1] < 5:
                        skipped.append(sym)
                        continue
                    idx = pd.to_datetime(arr[:, 0], unit="ms", utc=True)
                    df = pd.DataFrame(
                        {
                            "open": arr[:, 1], "high": arr[:, 2],
                            "low": arr[:, 3], "close": arr[:, 4],
                            "volume": arr[:, 5] if arr.shape[1] > 5 else 0.0,
                        },
                        index=idx,
                    )
                    df = df[~df.index.duplicated(keep="last")].sort_index()
                    df = df.dropna(subset=["open", "high", "low", "close"])
                    df = df[(df[["open", "high", "low", "close"]] > 0).all(axis=1)]
                    df["high"] = df[["high", "open", "close"]].max(axis=1)
                    df["low"] = df[["low", "open", "close"]].min(axis=1)
                    if len(df) < 30:
                        skipped.append(sym)
                        continue
                    self.frames[sym] = df
                    self.meta[sym] = SeriesMeta(sym, s.source, s.kind, s.note, len(df))
                    loaded.append(sym)
                except Exception:
                    skipped.append(sym)
            if payload_interval:
                self.interval = payload_interval
            if spot is not None:
                self.set_spot(spot.price, spot.source, spot.kind)
            if loaded:
                self.updated_at = time.time()
            return {"loaded": loaded, "skipped": skipped, "interval": self.interval}

    def set_spot(self, price, source="browser", kind="real"):
        from datetime import datetime, timezone

        if price and np.isfinite(price) and price > 0:
            with self._lock:
                self.spot = {
                    "price": float(price), "source": source, "kind": kind,
                    "ts": datetime.now(timezone.utc).isoformat(),
                }

    def clear(self) -> None:
        with self._lock:
            self.frames.clear()
            self.meta.clear()
            self.spot = {"price": None, "source": "unavailable", "kind": "none", "ts": None}

    # -- read -------------------------------------------------------------
    def get(self, symbol: str) -> Optional[pd.DataFrame]:
        with self._lock:
            df = self.frames.get(symbol)
            return df.copy() if df is not None else None

    def snapshot(self) -> Dict[str, pd.DataFrame]:
        with self._lock:
            return {k: v.copy() for k, v in self.frames.items()}

    def status(self) -> dict:
        with self._lock:
            rows = []
            for sym, m in self.meta.items():
                df = self.frames.get(sym)
                rows.append({
                    "symbol": sym, "source": m.source, "kind": m.kind, "note": m.note,
                    "bars": m.bars,
                    "from": df.index[0].isoformat() if df is not None and len(df) else None,
                    "to": df.index[-1].isoformat() if df is not None and len(df) else None,
                    "last": float(df["close"].iloc[-1]) if df is not None and len(df) else None,
                })
            return {
                "interval": self.interval,
                "series": rows,
                "spot": self.spot,
                "updated_at": self.updated_at,
                "has_primary": "XAUUSD" in self.frames,
            }


# ----------------------------------------------------------------------------
# Journal (SQLite)
# ----------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT NOT NULL,
    symbol        TEXT NOT NULL DEFAULT 'XAUUSD',
    interval      TEXT NOT NULL DEFAULT '1h',
    direction     TEXT NOT NULL,
    score         REAL,
    tier          TEXT,
    entry         REAL,
    stop          REAL,
    tp1           REAL, tp2 REAL, tp3 REAL,
    rr            REAL,
    size          REAL,
    risk_usd      REAL,
    atr           REAL,
    components    TEXT,
    gates         TEXT,
    news_bias     REAL,
    created_at    REAL,
    status        TEXT DEFAULT 'open',
    outcome       TEXT,
    outcome_r     REAL,
    outcome_at    REAL,
    bar_index     INTEGER,
    UNIQUE(ts, direction, tier)
);
CREATE TABLE IF NOT EXISTS alerts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id  INTEGER,
    channel    TEXT,
    status     TEXT,
    detail     TEXT,
    created_at REAL
);
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE INDEX IF NOT EXISTS idx_signals_created ON signals(created_at);
CREATE INDEX IF NOT EXISTS idx_signals_status ON signals(status);
"""


class Journal:
    def __init__(self, path: str = DB_PATH) -> None:
        self.path = path
        self._lock = threading.RLock()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with self._connect() as c:
            c.executescript(_SCHEMA)

    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        return conn

    # -- signals ----------------------------------------------------------
    def save_signal(self, row: dict) -> Optional[int]:
        cols = ["ts", "symbol", "interval", "direction", "score", "tier", "entry", "stop",
                "tp1", "tp2", "tp3", "rr", "size", "risk_usd", "atr", "components",
                "gates", "news_bias", "created_at", "bar_index"]
        vals = []
        for c in cols:
            v = row.get(c)
            if c in ("components", "gates") and not isinstance(v, str):
                v = json.dumps(json_safe(v) or {})
            vals.append(json_safe(v))
        ph = ",".join("?" * len(cols))
        with self._lock, self._connect() as conn:
            try:
                cur = conn.execute(
                    f"INSERT OR IGNORE INTO signals ({','.join(cols)}) VALUES ({ph})", vals)
                conn.commit()
                return cur.lastrowid or None
            except sqlite3.Error:
                return None

    def open_signals(self, symbol: str = "XAUUSD") -> List[dict]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM signals WHERE status='open' AND symbol=? ORDER BY created_at",
                (symbol,)).fetchall()
        return [self._row(r) for r in rows]

    def list_signals(self, limit: int = 200, symbol: str = "XAUUSD") -> List[dict]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM signals WHERE symbol=? ORDER BY created_at DESC LIMIT ?",
                (symbol, limit)).fetchall()
        return [self._row(r) for r in rows]

    def update_outcome(self, sid: int, outcome: dict, r: float, status: str = "closed") -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE signals SET status=?, outcome=?, outcome_r=?, outcome_at=? WHERE id=?",
                (status, json.dumps(json_safe(outcome)), json_safe(r), time.time(), sid))
            conn.commit()

    def stats(self, symbol: str = "XAUUSD") -> dict:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT outcome_r, tier, direction FROM signals "
                "WHERE symbol=? AND outcome_r IS NOT NULL", (symbol,)).fetchall()
        rs = [r["outcome_r"] for r in rows if r["outcome_r"] is not None]
        if not rs:
            return {"graded": 0}
        wins = [x for x in rs if x > 0]
        return {
            "graded": len(rs),
            "win_rate": len(wins) / len(rs),
            "expectancy_r": float(np.mean(rs)),
            "total_r": float(np.sum(rs)),
            "best_r": float(np.max(rs)),
            "worst_r": float(np.min(rs)),
        }

    # -- alerts -----------------------------------------------------------
    def log_alert(self, signal_id, channel: str, status: str, detail: str = "") -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO alerts (signal_id, channel, status, detail, created_at) "
                "VALUES (?,?,?,?,?)",
                (signal_id, channel, status, (detail or "")[:500], time.time()))
            conn.commit()

    def list_alerts(self, limit: int = 100) -> List[dict]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT a.*, s.direction, s.tier, s.score FROM alerts a "
                "LEFT JOIN signals s ON s.id=a.signal_id "
                "ORDER BY a.created_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    # -- settings ---------------------------------------------------------
    def get_setting(self, key: str, default=None):
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        if not row:
            return default
        try:
            return json.loads(row["value"])
        except Exception:
            return default

    def set_setting(self, key: str, value) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)",
                         (key, json.dumps(json_safe(value))))
            conn.commit()

    @staticmethod
    def _row(r: sqlite3.Row) -> dict:
        d = dict(r)
        for k in ("components", "gates", "outcome"):
            if d.get(k):
                try:
                    d[k] = json.loads(d[k])
                except Exception:
                    pass
        return d
