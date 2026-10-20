"""SQLite storage for Gold Trace (WAL mode, one file, zero ops).

Swap for TimescaleDB later: only this module speaks SQL.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from typing import Any, Iterable, Optional

from . import config

_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS observations(
    series  TEXT NOT NULL,
    ts      INTEGER NOT NULL,
    value   REAL NOT NULL,
    extra   TEXT,
    mode    TEXT NOT NULL DEFAULT 'sim'
);
CREATE INDEX IF NOT EXISTS idx_obs_series_ts ON observations(series, ts);
CREATE UNIQUE INDEX IF NOT EXISTS idx_obs_bucket ON observations(series, ts);

CREATE TABLE IF NOT EXISTS whale_transfers(
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        INTEGER NOT NULL,
    token     TEXT NOT NULL,
    src       TEXT,
    dst       TEXT,
    amount_oz REAL NOT NULL,
    usd_value REAL,
    tx_hash   TEXT,
    label     TEXT,
    flagged   INTEGER NOT NULL DEFAULT 0,
    mode      TEXT NOT NULL DEFAULT 'sim'
);
CREATE INDEX IF NOT EXISTS idx_whale_ts ON whale_transfers(ts);

CREATE TABLE IF NOT EXISTS alerts(
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             INTEGER NOT NULL,
    kind           TEXT NOT NULL,
    severity       TEXT NOT NULL,
    title          TEXT NOT NULL,
    body           TEXT,
    value          REAL,
    threshold      REAL,
    telegram_status TEXT NOT NULL DEFAULT 'pending'
);
CREATE INDEX IF NOT EXISTS idx_alert_ts ON alerts(ts);

CREATE TABLE IF NOT EXISTS events(
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    name      TEXT NOT NULL,
    ts        INTEGER NOT NULL,
    unit      TEXT,
    consensus REAL,
    actual    REAL,
    reaction  TEXT,
    reaction_usd REAL,
    matrix_hit INTEGER,
    status    TEXT NOT NULL DEFAULT 'upcoming'
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);

CREATE TABLE IF NOT EXISTS journal(
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_ts  INTEGER NOT NULL,
    trade_date  TEXT NOT NULL,
    setup_type  TEXT NOT NULL,
    direction   TEXT NOT NULL,
    level       REAL,
    trigger     TEXT,
    result      TEXT NOT NULL,
    r_multiple  REAL,
    notes       TEXT,
    screenshot  TEXT
);
CREATE INDEX IF NOT EXISTS idx_journal_date ON journal(trade_date);

CREATE TABLE IF NOT EXISTS ingest_log(
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    source    TEXT NOT NULL,
    ts        INTEGER NOT NULL,
    mode      TEXT NOT NULL,
    status    TEXT NOT NULL,
    latency_ms INTEGER,
    detail    TEXT
);
CREATE INDEX IF NOT EXISTS idx_ingest_source_ts ON ingest_log(source, ts);
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db() -> None:
    with _lock, connect() as conn:
        conn.executescript(SCHEMA)


def now() -> int:
    return int(time.time())


# ------------------------------------------------------------ observations
def upsert_obs(series: str, ts: int, value: float, mode: str = "sim",
               extra: Optional[dict] = None) -> None:
    """Insert or replace the observation for (series, ts-bucket).

    Daily/weekly/monthly series are bucketed to their period start so a
    job that re-runs inside the same period updates today's bar instead
    of duplicating it.
    """
    with _lock, connect() as conn:
        conn.execute(
            """INSERT INTO observations(series, ts, value, extra, mode)
               VALUES(?,?,?,?,?)
               ON CONFLICT(series, ts) DO UPDATE SET
                 value=excluded.value, extra=excluded.extra, mode=excluded.mode""",
            (series, int(ts), float(value),
             json.dumps(extra) if extra else None, mode),
        )


def insert_obs_many(rows: Iterable[tuple]) -> None:
    with _lock, connect() as conn:
        conn.executemany(
            """INSERT INTO observations(series, ts, value, extra, mode)
               VALUES(?,?,?,?,?)
               ON CONFLICT(series, ts) DO UPDATE SET
                 value=excluded.value, extra=excluded.extra, mode=excluded.mode""",
            rows,
        )


def latest(series: str) -> Optional[dict[str, Any]]:
    with _lock, connect() as conn:
        row = conn.execute(
            "SELECT ts, value, extra, mode FROM observations "
            "WHERE series=? ORDER BY ts DESC LIMIT 1", (series,)
        ).fetchone()
    return dict(row) if row else None


def series_window(series: str, points: int = 400,
                  since_ts: Optional[int] = None) -> list[dict[str, Any]]:
    q = "SELECT ts, value, extra, mode FROM observations WHERE series=?"
    args: list[Any] = [series]
    if since_ts:
        q += " AND ts>=?"
        args.append(since_ts)
    q += " ORDER BY ts DESC LIMIT ?"
    args.append(points)
    with _lock, connect() as conn:
        rows = conn.execute(q, args).fetchall()
    out = [dict(r) for r in reversed(rows)]
    for r in out:
        if r["extra"]:
            try:
                r["extra"] = json.loads(r["extra"])
            except Exception:
                r["extra"] = None
    return out


def value_at_or_before(series: str, ts: int) -> Optional[float]:
    with _lock, connect() as conn:
        row = conn.execute(
            "SELECT value FROM observations WHERE series=? AND ts<=? "
            "ORDER BY ts DESC LIMIT 1", (series, ts)
        ).fetchone()
    return row["value"] if row else None


def pct_change(series: str, lookback_s: int) -> Optional[float]:
    last = latest(series)
    if not last:
        return None
    old = value_at_or_before(series, last["ts"] - lookback_s)
    if old in (None, 0):
        return None
    return (last["value"] - old) / abs(old) * 100.0


def delta(series: str, lookback_s: int) -> Optional[float]:
    last = latest(series)
    if not last:
        return None
    old = value_at_or_before(series, last["ts"] - lookback_s)
    if old is None:
        return None
    return last["value"] - old


# ------------------------------------------------------------ misc tables
def insert_whale(token: str, src: str, dst: str, amount_oz: float,
                 usd_value: float, tx_hash: str, label: str, flagged: bool,
                 mode: str = "sim", ts: Optional[int] = None) -> int:
    with _lock, connect() as conn:
        cur = conn.execute(
            """INSERT INTO whale_transfers(ts, token, src, dst, amount_oz,
                       usd_value, tx_hash, label, flagged, mode)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (ts or now(), token, src, dst, amount_oz, usd_value, tx_hash,
             label, 1 if flagged else 0, mode),
        )
        return int(cur.lastrowid)


def recent_whales(limit: int = 40) -> list[dict[str, Any]]:
    with _lock, connect() as conn:
        rows = conn.execute(
            "SELECT * FROM whale_transfers ORDER BY ts DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def insert_alert(kind: str, severity: str, title: str, body: str,
                 value: float | None = None, threshold: float | None = None,
                 telegram_status: str = "pending",
                 ts: Optional[int] = None) -> int:
    with _lock, connect() as conn:
        cur = conn.execute(
            """INSERT INTO alerts(ts, kind, severity, title, body, value,
                       threshold, telegram_status)
               VALUES(?,?,?,?,?,?,?,?)""",
            (ts or now(), kind, severity, title, body, value, threshold,
             telegram_status),
        )
        return int(cur.lastrowid)


def update_alert_telegram(alert_id: int, status: str) -> None:
    with _lock, connect() as conn:
        conn.execute("UPDATE alerts SET telegram_status=? WHERE id=?",
                     (status, alert_id))


def recent_alerts(limit: int = 60) -> list[dict[str, Any]]:
    with _lock, connect() as conn:
        rows = conn.execute(
            "SELECT * FROM alerts ORDER BY ts DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def last_alert_ts(kind: str) -> Optional[int]:
    with _lock, connect() as conn:
        row = conn.execute(
            "SELECT MAX(ts) AS m FROM alerts WHERE kind=?", (kind,)
        ).fetchone()
    return row["m"] if row and row["m"] is not None else None


def log_ingest(source: str, mode: str, status: str, latency_ms: int,
               detail: str = "") -> None:
    with _lock, connect() as conn:
        conn.execute(
            """INSERT INTO ingest_log(source, ts, mode, status, latency_ms, detail)
               VALUES(?,?,?,?,?,?)""",
            (source, now(), mode, status, latency_ms, detail),
        )


def ingest_status() -> dict[str, dict[str, Any]]:
    with _lock, connect() as conn:
        rows = conn.execute(
            """SELECT source, MAX(ts) AS last_ts FROM ingest_log
               GROUP BY source"""
        ).fetchall()
        out: dict[str, dict[str, Any]] = {}
        for r in rows:
            last = conn.execute(
                "SELECT mode, status, latency_ms FROM ingest_log "
                "WHERE source=? ORDER BY ts DESC LIMIT 1", (r["source"],)
            ).fetchone()
            out[r["source"]] = {
                "last_ts": r["last_ts"],
                "mode": last["mode"],
                "status": last["status"],
                "latency_ms": last["latency_ms"],
            }
    return out


def table_count(table: str) -> int:
    with _lock, connect() as conn:
        return conn.execute(f"SELECT COUNT(*) c FROM {table}").fetchone()["c"]
