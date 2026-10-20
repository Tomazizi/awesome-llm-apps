"""Anomaly & whale alert engine.

Triggers:
  * large single PAXG/XAUT transfers (>= 5,000 oz) and 1h whale aggregates
  * ETF creation/redemption days (|GLD daily flow| >= 4 t)
  * sharp COMEX registered drawdowns (7d <= -2.5%)
  * Shanghai premium spikes (>= $55/oz or +$25 in 5d)
  * spot volatility bursts (|1h move| >= 1.2%)
  * COT positioning shifts (|weekly Δ managed money| >= 30k contracts)

Every trigger writes an `alerts` row and hands it to the Telegram sender.
Same-kind alerts are de-duplicated with a cooldown.
"""
from __future__ import annotations

from typing import Any, Optional

from .. import db, telegram_alerts
from .base import DAY, HOUR

COOLDOWN_S = 10 * 60          # demo: 10 min between same-kind alerts


def _cooled(kind: str) -> bool:
    last = db.last_alert_ts(kind)
    return last is None or (db.now() - last) >= COOLDOWN_S


def _fire(kind: str, severity: str, title: str, body: str,
          value: Optional[float] = None, threshold: Optional[float] = None,
          ts: Optional[int] = None) -> Optional[int]:
    if not _cooled(kind):
        return None
    status = telegram_alerts.send(title, body, severity)
    return db.insert_alert(kind, severity, title, body, value, threshold,
                           telegram_status=status, ts=ts)


def evaluate() -> list[int]:
    fired: list[int] = []

    # 1) whale transfers -------------------------------------------------
    whales = db.recent_whales(limit=25)
    cutoff_h = db.now() - HOUR
    recent = [w for w in whales if w["ts"] >= cutoff_h]
    for w in recent:
        if w["flagged"] and w["amount_oz"] >= 5000:
            aid = _fire(
                f"whale_single", "high",
                f"🐋 {w['amount_oz']:,.0f} oz {w['token']} transfer",
                f"{w['src']} → {w['dst']} · ~${(w['usd_value'] or 0)/1e6:.1f}M · "
                f"tx {w['tx_hash'][:14]}…  Check destination: exchange inflow = "
                f"sell pressure, vault/custodian = neutral-to-bullish.",
                value=w["amount_oz"], threshold=5000, ts=w["ts"])
            if aid: fired.append(aid)
            break
    agg = sum(w["amount_oz"] for w in recent)
    if agg >= 15000:
        aid = _fire("whale_aggregate", "medium",
                    f"On-chain whale flow: {agg:,.0f} oz in the last hour",
                    f"{len(recent)} PAXG/XAUT transfers ≥1k oz. Sustained token "
                    f"redemption can precede physical creation/destruction — "
                    f"watch Paxos mint/burn queue.",
                    value=agg, threshold=15000)
        if aid: fired.append(aid)

    # 2) ETF flows ---------------------------------------------------------
    gld = db.latest("gld_tonnes")
    if gld and gld.get("extra"):
        flow = (gld["extra"] or {}).get("flow_t") if isinstance(gld["extra"], dict) else None
        if flow is None:
            import json
            try: flow = json.loads(gld["extra"]).get("flow_t")
            except Exception: flow = None
        if flow is not None and abs(flow) >= 4.0:
            side = "creation 🟢" if flow > 0 else "redemption 🔴"
            aid = _fire("etf_flow", "medium" if abs(flow) < 8 else "high",
                        f"GLD {side}: {flow:+.1f} t today",
                        f"GLD holdings {gld['value']:,.1f} t. Large single-day "
                        f"{'inflow supports' if flow > 0 else 'outflow pressures'} "
                        f"the structural score.",
                        value=float(flow), threshold=4.0)
            if aid: fired.append(aid)

    # 3) COMEX registered drawdown ------------------------------------------
    reg_pct = db.pct_change("comex_registered", 7 * DAY)
    if reg_pct is not None and reg_pct <= -2.5:
        reg = db.latest("comex_registered")
        aid = _fire("comex_drawdown", "high",
                    f"COMEX registered down {reg_pct:.1f}% in 7 days",
                    f"Registered stock now {reg['value']/1e6:.2f} Moz. Fast vault "
                    f"drawdowns tighten deliverable supply — historically "
                    f"accompany squeeze setups and rising EFP.",
                    value=reg_pct, threshold=-2.5)
        if aid: fired.append(aid)

    # 4) Shanghai premium spike ---------------------------------------------
    sge = db.latest("sge_premium")
    sge5 = db.delta("sge_premium", 5 * DAY)
    if sge and (sge["value"] >= 55 or (sge5 is not None and sge5 >= 25)):
        aid = _fire("sge_spike", "high",
                    f"Shanghai premium spike: ${sge['value']:.0f}/oz",
                    f"SGE over LBMA at ${sge['value']:.1f} (5d Δ {sge5 if sge5 is not None else 0:+.1f}). "
                    f"Chinese physical demand is pulling metal east — one of the "
                    f"most reliable leading reads for trend continuation.",
                    value=sge["value"], threshold=55)
        if aid: fired.append(aid)

    # 5) spot volatility burst -------------------------------------------------
    spot_1h = db.pct_change("spot_xau", HOUR)
    if spot_1h is not None and abs(spot_1h) >= 1.2:
        spot = db.latest("spot_xau")
        aid = _fire("vol_burst", "medium",
                    f"XAU {spot_1h:+.2f}% in 1h — vol burst",
                    f"Spot at ${spot['value']:,.2f}. Check which layer moved "
                    f"first: macro (TIPS/DXY), on-chain whale flow, or "
                    f"positioning. Don't chase the candle — trade the cause.",
                    value=spot_1h, threshold=1.2)
        if aid: fired.append(aid)

    # 6) COT positioning shift --------------------------------------------------
    cot = db.latest("cot_mm_net")
    if cot and isinstance(cot.get("extra"), dict):
        d = cot["extra"].get("delta")
        if d is not None and abs(d) >= 30000:
            aid = _fire("cot_shift", "low",
                        f"Managed money {d:+,.0f} contracts this week",
                        f"COT MM net now {cot['value']:,.0f}. Crowding shifts "
                        f"change how far squeezes can run — extremes are "
                        f"counter-trend fuel, not entries by themselves.",
                        value=float(d), threshold=30000)
            if aid: fired.append(aid)

    return fired
