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

-- Full Automation System
CREATE TABLE IF NOT EXISTS automation_rules(
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    description     TEXT,
    trigger_type    TEXT NOT NULL,
    trigger_params  TEXT,
    action_type     TEXT NOT NULL,
    action_params   TEXT,
    enabled         INTEGER NOT NULL DEFAULT 1,
    cooldown_s      INTEGER NOT NULL DEFAULT 600,
    last_fired_ts   INTEGER,
    created_ts      INTEGER NOT NULL,
    updated_ts      INTEGER,
    priority        INTEGER NOT NULL DEFAULT 5
);
CREATE TABLE IF NOT EXISTS automation_runs(
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id         INTEGER REFERENCES automation_rules(id) ON DELETE SET NULL,
    ts              INTEGER NOT NULL,
    trigger_kind    TEXT,
    context_snapshot TEXT,
    action_result   TEXT,
    status          TEXT NOT NULL,
    title           TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_ts ON automation_runs(ts);

CREATE TABLE IF NOT EXISTS signals(
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              INTEGER NOT NULL,
    rule_id         INTEGER REFERENCES automation_rules(id) ON DELETE SET NULL,
    source          TEXT NOT NULL,
    direction       TEXT NOT NULL,
    strength        REAL,
    price           REAL,
    composite_score REAL,
    structural_score REAL,
    macro_score     REAL,
    reason          TEXT,
    status          TEXT NOT NULL DEFAULT 'active',
    extra           TEXT
);
CREATE INDEX IF NOT EXISTS idx_signals_ts ON signals(ts);

CREATE TABLE IF NOT EXISTS paper_positions(
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id       INTEGER REFERENCES signals(id) ON DELETE SET NULL,
    open_ts         INTEGER NOT NULL,
    close_ts        INTEGER,
    direction       TEXT NOT NULL,
    entry_price     REAL NOT NULL,
    exit_price      REAL,
    size_oz         REAL NOT NULL,
    stop_loss       REAL,
    take_profit     REAL,
    pnl             REAL,
    r_multiple      REAL,
    status          TEXT NOT NULL DEFAULT 'open',
    note            TEXT
);
CREATE INDEX IF NOT EXISTS idx_positions_status ON paper_positions(status);

CREATE TABLE IF NOT EXISTS automation_state(
    key             TEXT PRIMARY KEY,
    value           TEXT NOT NULL,
    updated_ts      INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS auto_reports(
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              INTEGER NOT NULL,
    period          TEXT NOT NULL,
    title           TEXT,
    payload         TEXT,
    status          TEXT NOT NULL DEFAULT 'generated'
);
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


# ---------------- automation helpers
def get_state(key: str, default: str | None = None) -> str | None:
    with _lock, connect() as conn:
        row = conn.execute("SELECT value FROM automation_state WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

def set_state(key: str, value: str) -> None:
    with _lock, connect() as conn:
        conn.execute(
            """INSERT INTO automation_state(key,value,updated_ts)
               VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_ts=excluded.updated_ts""",
            (key, value, now()),
        )

def all_automation_rules(enabled_only: bool = False) -> list[dict[str, Any]]:
    with _lock, connect() as conn:
        q = "SELECT * FROM automation_rules"
        if enabled_only:
            q += " WHERE enabled=1"
        q += " ORDER BY priority DESC, created_ts DESC"
        rows = conn.execute(q).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        for k in ("trigger_params","action_params"):
            if d[k]:
                try: d[k] = json.loads(d[k])
                except: pass
        d["enabled"] = bool(d["enabled"])
        out.append(d)
    return out

def get_rule(rid: int) -> Optional[dict[str,Any]]:
    with _lock, connect() as conn:
        row = conn.execute("SELECT * FROM automation_rules WHERE id=?", (rid,)).fetchone()
    if not row: return None
    d=dict(row)
    for k in ("trigger_params","action_params"):
        if d[k]:
            try: d[k]=json.loads(d[k])
            except: pass
    d["enabled"]=bool(d["enabled"])
    return d

def insert_rule(name: str, trigger_type: str, trigger_params: dict, action_type: str, action_params: dict,
                description: str = "", enabled: bool = True, cooldown_s: int = 600, priority: int = 5) -> int:
    with _lock, connect() as conn:
        cur = conn.execute(
            """INSERT INTO automation_rules(name,description,trigger_type,trigger_params,action_type,action_params,enabled,cooldown_s,created_ts,priority)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (name, description, trigger_type, json.dumps(trigger_params), action_type, json.dumps(action_params),
             1 if enabled else 0, cooldown_s, now(), priority),
        )
        return int(cur.lastrowid)

def update_rule(rid: int, **fields) -> None:
    if not fields: return
    # json-encode dict params
    for k in ("trigger_params","action_params"):
        if k in fields and isinstance(fields[k], dict):
            fields[k]=json.dumps(fields[k])
    sets = ", ".join(f"{k}=?" for k in fields)
    vals = list(fields.values()) + [now(), rid]
    sets += ", updated_ts=?"
    with _lock, connect() as conn:
        conn.execute(f"UPDATE automation_rules SET {sets} WHERE id=?", vals)

def delete_rule(rid: int) -> None:
    with _lock, connect() as conn:
        conn.execute("DELETE FROM automation_rules WHERE id=?", (rid,))

def log_run(rule_id: int | None, trigger_kind: str, title: str, status: str, context: dict | None = None, result: dict | None = None) -> int:
    with _lock, connect() as conn:
        cur=conn.execute(
            """INSERT INTO automation_runs(rule_id,ts,trigger_kind,context_snapshot,action_result,status,title)
               VALUES(?,?,?,?,?,?,?)""",
            (rule_id, now(), trigger_kind, json.dumps(context) if context else None,
             json.dumps(result) if result else None, status, title),
        )
        return int(cur.lastrowid)

def recent_runs(limit: int = 50) -> list[dict[str,Any]]:
    with _lock, connect() as conn:
        rows = conn.execute("SELECT * FROM automation_runs ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]

def insert_signal(ts:int, source:str, direction:str, strength:float, price:float, composite:float, structural:float, macro:float, reason:str, rule_id:int|None=None, status:str="active", extra:dict|None=None) -> int:
    with _lock, connect() as conn:
        cur=conn.execute(
            """INSERT INTO signals(ts,rule_id,source,direction,strength,price,composite_score,structural_score,macro_score,reason,status,extra)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (ts, rule_id, source, direction, strength, price, composite, structural, macro, reason, status, json.dumps(extra) if extra else None),
        )
        return int(cur.lastrowid)

def recent_signals(limit:int=40) -> list[dict[str,Any]]:
    with _lock, connect() as conn:
        rows=conn.execute("SELECT * FROM signals ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]

def update_signal_status(sid:int, status:str) -> None:
    with _lock, connect() as conn:
        conn.execute("UPDATE signals SET status=? WHERE id=?", (status, sid))

# paper positions
def open_position(signal_id:int, direction:str, entry_price:float, size_oz:float, stop_loss:float|None=None, take_profit:float|None=None, note:str="") -> int:
    with _lock, connect() as conn:
        cur=conn.execute(
            """INSERT INTO paper_positions(signal_id,open_ts,direction,entry_price,size_oz,stop_loss,take_profit,status,note)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (signal_id, now(), direction, entry_price, size_oz, stop_loss, take_profit, "open", note),
        )
        return int(cur.lastrowid)

def open_positions() -> list[dict[str,Any]]:
    with _lock, connect() as conn:
        rows=conn.execute("SELECT * FROM paper_positions WHERE status='open' ORDER BY open_ts DESC").fetchall()
    return [dict(r) for r in rows]

def all_positions(limit:int=60) -> list[dict[str,Any]]:
    with _lock, connect() as conn:
        rows=conn.execute("SELECT * FROM paper_positions ORDER BY open_ts DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]

def close_position(pid:int, exit_price:float) -> Optional[dict[str,Any]]:
    with _lock, connect() as conn:
        row=conn.execute("SELECT * FROM paper_positions WHERE id=?", (pid,)).fetchone()
        if not row: return None
        p=dict(row)
        pnl = (exit_price - p["entry_price"]) * p["size_oz"] if p["direction"]=="LONG" else (p["entry_price"]-exit_price)*p["size_oz"]
        risk = abs(p["entry_price"]-(p["stop_loss"] or p["entry_price"]*0.97))
        r = pnl / (risk*p["size_oz"]) if risk else 0
        conn.execute("UPDATE paper_positions SET close_ts=?, exit_price=?, pnl=?, r_multiple=?, status='closed' WHERE id=?",
                     (now(), exit_price, pnl, r, pid))
        p.update({"exit_price":exit_price,"pnl":pnl,"r_multiple":r,"status":"closed"})
        return p

def paper_pnl_summary() -> dict[str,Any]:
    with _lock, connect() as conn:
        rows=conn.execute("SELECT * FROM paper_positions").fetchall()
    closed=[dict(r) for r in rows if dict(r)["status"]=="closed"]
    total = sum(c["pnl"] or 0 for c in closed)
    wins=len([c for c in closed if (c["pnl"] or 0) > 0])
    losses=len([c for c in closed if (c["pnl"] or 0) < 0])
    net_r = sum(c["r_multiple"] or 0 for c in closed)
    return {"total_pnl": round(total,2), "closed": len(closed), "wins": wins, "losses": losses, "win_rate": round(wins/len(closed)*100,1) if closed else None, "net_r": round(net_r,2), "open": len([r for r in rows if dict(r)["status"]=="open"])}

def recent_reports(limit:int=20) -> list[dict[str,Any]]:
    with _lock, connect() as conn:
        rows=conn.execute("SELECT * FROM auto_reports ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]

def insert_report(period:str, title:str, payload:dict) -> int:
    with _lock, connect() as conn:
        cur=conn.execute("INSERT INTO auto_reports(ts,period,title,payload) VALUES(?,?,?,?)",
                         (now(), period, title, json.dumps(payload)))
        return int(cur.lastrowid)
