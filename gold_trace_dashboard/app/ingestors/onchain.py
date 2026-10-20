"""Layer 2 — On-chain whales (LIVE): PAXG & XAUT transfers.

Live: Etherscan ERC-20 tokentx feed (ETHERSCAN_API_KEY) filtered to
transfers >= WHALE_MIN_OZ; Alchemy as RPC fallback.
Sim: poisson-ish arrival of whale transfers, rare large 'exchange <->
vault' moves that trip the anomaly engine.
"""
from __future__ import annotations

import random
from typing import Optional

from .. import config, db
from .base import http_get_json, record, try_live

SOURCE = "onchain"
WHALE_MIN_OZ = 1000.0          # report transfers >= 1k oz
FLAG_MIN_OZ = 5000.0           # anomaly threshold (single transfer)

TOKENS = {"PAXG": config.PAXG_ADDRESS, "XAUT": config.XAUT_ADDRESS}

_LABELS = ["Binance hot", "Coinbase Prime", "Paxos vault", "Tether treasury",
           "Wintermute", "Jump", "Unknown EOA", "GSR", "Cumberland",
           "Anonymous vault", "Kraken", "OKX"]


def _addr() -> str:
    return "0x" + "".join(random.choice("0123456789abcdef") for _ in range(40))


def _tx() -> str:
    return "0x" + "".join(random.choice("0123456789abcdef") for _ in range(64))


def _sim_transfers(now_ts: int) -> list[dict]:
    out: list[dict] = []
    # most ticks: nothing. sometimes a mid whale, rarely a monster.
    r = random.random()
    if r < 0.10:
        n = random.choice([1, 1, 1, 2])
        for _ in range(n):
            big = random.random() < 0.06
            amt = random.uniform(5000, 24000) if big else random.uniform(1000, 4800)
            token = random.choice(list(TOKENS))
            out.append({
                "token": token,
                "src": random.choice(_LABELS),
                "dst": random.choice(_LABELS),
                "amount_oz": round(amt, 1),
                "flagged": amt >= FLAG_MIN_OZ,
                "ts": now_ts - random.randint(0, 90),
            })
    return out


def _live() -> Optional[dict]:
    if not config.ETHERSCAN_API_KEY:
        return None
    transfers: list[dict] = []
    spot = db.latest("spot_xau")
    px = spot["value"] if spot else 4000.0
    for token, contract in TOKENS.items():
        d = http_get_json("https://api.etherscan.io/api", params={
            "module": "account", "action": "tokentx",
            "contractaddress": contract, "page": 1, "offset": 40,
            "sort": "desc", "apikey": config.ETHERSCAN_API_KEY,
        })
        for tx in d.get("result", []):
            try:
                amt = int(tx["value"]) / 1e18      # PAXG & XAUT are 18/6 dec
                if token == "XAUT":
                    amt = int(tx["value"]) / 1e6
            except Exception:
                continue
            if amt >= WHALE_MIN_OZ:
                transfers.append({
                    "token": token,
                    "src": tx["from"][:10] + "…",
                    "dst": tx["to"][:10] + "…",
                    "amount_oz": round(amt, 1),
                    "usd_value": round(amt * px, 0),
                    "tx_hash": tx["hash"],
                    "flagged": amt >= FLAG_MIN_OZ,
                    "ts": int(tx["timeStamp"]),
                })
    return {"transfers": transfers[:15]}


def run() -> None:
    now_ts = db.now()
    live = try_live(_live)
    if live:
        rows, mode, latency, status = live["transfers"], "live", live.get("_latency_ms", 0), "ok"
    else:
        rows, mode, latency, status = _sim_transfers(now_ts), "sim", 0, "ok(fallback)"

    spot = db.latest("spot_xau")
    px = spot["value"] if spot else 4000.0
    for t in rows:
        db.insert_whale(
            token=t["token"], src=t.get("src") or _addr()[:12],
            dst=t.get("dst") or _addr()[:12], amount_oz=t["amount_oz"],
            usd_value=t.get("usd_value") or round(t["amount_oz"] * px, 0),
            tx_hash=t.get("tx_hash") or _tx(), label="",
            flagged=t.get("flagged", False), mode=mode, ts=t.get("ts", now_ts),
        )
    big = sum(1 for t in rows if t.get("flagged"))
    record(SOURCE, mode, status, latency,
           f"{len(rows)} whale tx{'' if len(rows)==1 else 's'}, {big} flagged")
