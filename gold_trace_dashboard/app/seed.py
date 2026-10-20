"""Backfill history so every chart, score and report works on first boot.

Generates a plausible 90-day world: intraday spot/GC for the last 10 days,
daily bars for funds/vaults/Asia, weekly COT & withdrawals, monthly central
bank + trade flows, quarterly 13F aggregates, a whale tape for the last 24h,
a handful of alerts, 8 released events, and ~5 weeks of journal entries.

The ingestor simulators are anchored to the last seeded value, so live
ticks continue the history seamlessly.
"""
from __future__ import annotations

import json
import math
import random
import time
from datetime import datetime, timedelta, timezone

from . import db
from .engines import events as events_engine
from .ingestors import (asia_demand, central_banks, exchange_stock, funds,
                        institutions, macro, onchain, positioning,
                        price_market, trade_flows)

DAY = 86400
MIN5 = 300


def _walk(n: int, start: float, sigma: float, drift: float = 0.0,
          reversion: float = 0.0, anchor_end: float | None = None,
          floor: float | None = None) -> list[float]:
    out, v = [], start
    for _ in range(n):
        v += (start - v) * reversion
        v *= 1.0 + drift + random.gauss(0, sigma)
        if floor is not None:
            v = max(v, floor)
        out.append(v)
    if anchor_end:
        k = anchor_end / out[-1]
        out = [x * k for x in out]
    return out


def _anchored(n: int, end: float, sigma: float, drift: float = 0.0,
              reversion: float = 0.02, floor: float | None = None) -> list[float]:
    return _walk(n, end * (1 - drift * n * 0.4), sigma, drift, reversion,
                 anchor_end=end, floor=floor)


def seed_if_empty() -> bool:
    if db.table_count("observations") > 0:
        return False
    random.seed(20261020)
    now = db.now()
    today0 = now - (now % DAY)

    # ---------------------------------------------------------- live intraday
    n_tick = 10 * 288                       # 10 days of 5-min bars
    spot_hist = _anchored(n_tick, 4085.0, 0.00075, 0.000012, 0.004)
    gc_hist = [s + 22 + math.sin(i / 90) * 4 for i, s in enumerate(spot_hist)]
    t0 = now - n_tick * MIN5
    rows = []
    for i in range(n_tick):
        ts = t0 + i * MIN5
        rows.append(("spot_xau", ts, round(spot_hist[i], 2), None, "sim"))
        rows.append(("comex_gc", ts, round(gc_hist[i], 2),
                     json.dumps({"basis": round(gc_hist[i] - spot_hist[i], 2)}), "sim"))
    db.insert_obs_many(rows)
    price_market._spot.value = spot_hist[-1]
    price_market._gc.value = gc_hist[-1]

    # options OI — daily, 90d
    call_hist = _anchored(90, 182000, 0.012, 0.0006, 0.05)
    put_hist = _anchored(90, 96000, 0.014, 0.0004, 0.05)
    rows = []
    for i in range(90):
        ts = today0 - (89 - i) * DAY
        c, p = int(call_hist[i]), int(put_hist[i])
        rows.append(("options_oi", ts, c + p,
                     json.dumps({"call_oi": c, "put_oi": p,
                                 "pcr": round(p / c, 3)}), "sim"))
    db.insert_obs_many(rows)
    price_market._call_oi.value = call_hist[-1]
    price_market._put_oi.value = put_hist[-1]

    # ---------------------------------------------------------- macro daily
    tips = _anchored(90, 1.86, 0.008, -0.0006, 0.03)
    dxy = _anchored(90, 98.4, 0.0022, -0.0004, 0.02)
    brent = _anchored(90, 68.5, 0.009, 0.0002, 0.02, floor=55)
    cutp = _anchored(90, 64.0, 0.9, 0.05, 0.02, floor=5)
    rows = []
    for i in range(90):
        ts = today0 - (89 - i) * DAY
        rows.append(("tips_10y", ts, round(tips[i], 3), None, "sim"))
        rows.append(("dxy", ts, round(dxy[i], 3), None, "sim"))
        rows.append(("brent", ts, round(brent[i], 2), None, "sim"))
        rows.append(("fedwatch_cut_prob", ts, round(min(95, max(5, cutp[i])), 1),
                     json.dumps({"meeting": "Dec 2026 FOMC"}), "sim"))
    db.insert_obs_many(rows)
    macro._tips.value, macro._dxy.value = tips[-1], dxy[-1]
    macro._brent.value, macro._cut.value = brent[-1], cutp[-1]

    # ---------------------------------------------------------- funds daily
    gld = _anchored(90, 1042.0, 0.0021, 0.00035, 0.01)
    iau = _anchored(90, 562.0, 0.0019, 0.00028, 0.01)
    rows = []
    for i in range(90):
        ts = today0 - (89 - i) * DAY
        flow = round(gld[i] - gld[i - 1], 2) if i else 0.0
        rows.append(("gld_tonnes", ts, round(gld[i], 2),
                     json.dumps({"flow_t": flow}), "sim"))
        rows.append(("iau_tonnes", ts, round(iau[i], 2), None, "sim"))
    db.insert_obs_many(rows)
    funds._gld.value, funds._iau.value = gld[-1], iau[-1]

    # --------------------------------------------------- COMEX stock daily
    reg = _anchored(90, 24.8e6, 0.004, -0.0009, 0.004, floor=15e6)
    elig = _anchored(90, 452e6, 0.0018, 0.00015, 0.003, floor=380e6)
    rows = []
    for i in range(90):
        ts = today0 - (89 - i) * DAY
        d = reg[i] - reg[i - 1] if i else 0.0
        rows.append(("comex_registered", ts, round(reg[i]),
                     json.dumps({"delta_oz": round(d)}), "sim"))
        rows.append(("comex_eligible", ts, round(elig[i]), None, "sim"))
    db.insert_obs_many(rows)
    exchange_stock._reg.value, exchange_stock._elig.value = reg[-1], elig[-1]

    # ------------------------------------------------------- Asia demand
    sge = _anchored(90, 34.0, 1.6, 0.02, 0.03, floor=-12)
    ind = _anchored(90, 4.5, 1.2, 0.0, 0.03, floor=-18)
    rows = []
    for i in range(90):
        ts = today0 - (89 - i) * DAY
        rows.append(("sge_premium", ts, round(sge[i], 2), None, "sim"))
        rows.append(("india_premium", ts, round(ind[i], 2), None, "sim"))
    db.insert_obs_many(rows)
    asia_demand._sge.value, asia_demand._india.value = sge[-1], ind[-1]

    wd = _anchored(13, 28.0, 0.06, 0.0, 0.1, floor=10)
    rows = []
    for i in range(13):
        ts = today0 - (12 - i) * 7 * DAY
        rows.append(("sge_withdrawals", ts, round(wd[i], 1),
                     json.dumps({"unit": "tonnes/week"}), "sim"))
    db.insert_obs_many(rows)
    asia_demand._wd.value = wd[-1]

    # ------------------------------------------------------------- COT weekly
    mm = _anchored(13, 198000, 0.03, 0.002, 0.03)
    comm = _anchored(13, -72000, 0.028, -0.001, 0.03)
    rows = []
    for i in range(13):
        ts = today0 - (12 - i) * 7 * DAY
        d = mm[i] - mm[i - 1] if i else 0.0
        rows.append(("cot_mm_net", ts, round(mm[i]),
                     json.dumps({"delta": round(d)}), "sim"))
        rows.append(("cot_comm_net", ts, round(comm[i]), None, "sim"))
    db.insert_obs_many(rows)
    positioning._mm.value, positioning._comm.value = mm[-1], comm[-1]

    # ------------------------------------------------- central banks monthly
    cb_months = [38, 45, 51, 29, 47, 55, 42, 49]
    rows = []
    for i, v in enumerate(cb_months):
        ts = db.now() - 35 * DAY - (len(cb_months) - 1 - i) * 30 * DAY
        rows.append(("cb_net_purchases", ts - (ts % DAY), float(v),
                     json.dumps({"notable_buyers": ["PBoC", "NBP Poland", "RBI India"][:2 + i % 2],
                                 "lag_months": 1}), "sim"))
    db.insert_obs_many(rows)
    central_banks._cb.value = float(cb_months[-1])

    # --------------------------------------------------- trade flows monthly
    swiss = [104, 121, 133, 110, 126, 141, 118, 124]
    china = [82, 95, 108, 88, 101, 112, 96, 103]
    rows = []
    for i in range(len(swiss)):
        ts = db.now() - 42 * DAY - (len(swiss) - 1 - i) * 30 * DAY
        ts -= ts % DAY
        rows.append(("swiss_gold_exports", ts, float(swiss[i]),
                     json.dumps({"to_india_t": round(swiss[i] * 0.27, 1),
                                 "to_china_t": round(swiss[i] * 0.36, 1)}), "sim"))
        rows.append(("china_gold_imports", ts, float(china[i]),
                     json.dumps({"india_imports_t": round(china[i] * 0.62, 1)}), "sim"))
    db.insert_obs_many(rows)
    trade_flows._swiss.value, trade_flows._china.value = float(swiss[-1]), float(china[-1])

    # ------------------------------------------------- institutions quarterly
    alloc = [0.68, 0.71, 0.75, 0.74, 0.79, 0.82]
    fcount = [980, 1030, 1090, 1105, 1150, 1180]
    rows = []
    for i in range(len(alloc)):
        ts = db.now() - 50 * DAY - (len(alloc) - 1 - i) * 91 * DAY
        ts -= ts % DAY
        rows.append(("inst_gold_alloc_pct", ts, alloc[i],
                     json.dumps({"top_holders": ["Bridgewater", "Renaissance", "Citadel"]}), "sim"))
        rows.append(("inst_gold_fund_count", ts, float(fcount[i]), None, "sim"))
    db.insert_obs_many(rows)
    institutions._alloc.value, institutions._funds.value = alloc[-1], float(fcount[-1])

    # ------------------------------------------------------ whale tape (24h)
    labels = ["Binance hot", "Coinbase Prime", "Paxos vault", "Tether treasury",
              "Wintermute", "Unknown EOA", "Cumberland", "Anonymous vault"]
    for i in range(26):
        ts = now - random.randint(120, 22 * 3600)
        big = random.random() < 0.15
        amt = round(random.uniform(5200, 19000), 1) if big else round(random.uniform(1000, 4900), 1)
        db.insert_whale(random.choice(["PAXG", "XAUT"]),
                        random.choice(labels), random.choice(labels),
                        amt, round(amt * spot_hist[-1]),
                        "0x" + "".join(random.choice("0123456789abcdef") for _ in range(64)),
                        "", amt >= 5000, "sim", ts)

    # ------------------------------------------------------ released events
    released = [
        ("Nonfarm Payrolls", 65, 119, "Bearish impulse — but check revisions & U3", -26, 1, 45),
        ("Core PCE m/m", 0.3, 0.3, "Chop, mean-revert", 4, 1, 33),
        ("Nonfarm Payrolls", 75, 22, "Bullish impulse, $30–60; strongest if U3 +0.2pp", 41, 1, 26),
        ("Core PCE m/m", 0.3, 0.4, "Bearish impulse, $20–40 flush", -31, 1, 19),
        ("Nonfarm Payrolls", 60, 58, "Chop", -5, 0, 12),
        ("Core PCE m/m", 0.2, 0.1, "Bullish impulse, $25–50", 37, 1, 8),
    ]
    with db.connect() as conn:
        for name, cons, actual, reaction, move, hit, days_ago in released:
            ts = now - days_ago * DAY
            unit = "% m/m" if "PCE" in name else "k jobs"
            conn.execute(
                """INSERT INTO events(name, ts, unit, consensus, actual,
                           reaction, reaction_usd, matrix_hit, status)
                   VALUES(?,?,?,?,?,?,?,?, 'released')""",
                (name, ts, unit, float(cons), float(actual), reaction,
                 float(move), hit))
    events_engine.ensure_calendar()

    # ------------------------------------------------------------- journal
    setups = [
        ("Whale Follow", "LONG", "SGE premium spike + 8k oz PAXG to vault", 1.8),
        ("Whale Follow", "SHORT", "22k oz XAUT → Binance hot wallet", -1.0),
        ("ETF Flow Fade", "SHORT", "GLD −6.4t redemption day, fade AM pop", 1.2),
        ("ETF Flow Fade", "LONG", "GLD +5.1t creation, buy retest of breakout", 2.1),
        ("Vault Drawdown", "LONG", "COMEX registered −3.1%/wk, long EFP spread", 2.6),
        ("Vault Drawdown", "LONG", "Registered −2.8%/wk + SGE prem $58", 1.9),
        ("SGE Premium", "LONG", "Premium $61 → long Au9999/spot arb leg", 1.4),
        ("SGE Premium", "SHORT", "Premium collapse $52→$18, fade spot breakout", -1.3),
        ("COT Squeeze", "SHORT", "MM net +34k wk at 260k extreme, fade strength", 2.2),
        ("COT Squeeze", "LONG", "MM flush −41k to 148k, contrarian long", -1.0),
        ("Event Reaction", "LONG", "Cool PCE 0.1 vs 0.3, buy pre-event high break", 2.8),
        ("Event Reaction", "SHORT", "Hot NFP + U3 down, short impulse per matrix", 1.5),
        ("Event Reaction", "LONG", "NFP miss −38k, buy flush reversal at VWAP", -0.8),
        ("Breakout Retest", "LONG", "4,120 shelf retest after vault alert", 1.7),
        ("Breakout Retest", "SHORT", "Failed retest of 4,050 breakdown", 1.1),
        ("Breakout Retest", "LONG", "Retest of 3,980 flag breakout", -1.0),
        ("Whale Follow", "LONG", "Paxos mint queue + 12k oz PAXG burn", 1.3),
        ("ETF Flow Fade", "SHORT", "IAU −3.8t + premium fade", 0.0),
    ]
    with db.connect() as conn:
        for i in range(40):
            stype, direction, trigger, r = setups[i % len(setups)]
            r_adj = round(r + random.gauss(0, 0.6), 1)
            if r_adj > 0.15: result = "Win"
            elif r_adj < -0.15: result = "Loss"
            else: result, r_adj = "BE", 0.0
            days_ago = random.randint(1, 55)
            d = datetime.now(timezone.utc) - timedelta(days=days_ago)
            px = spot_hist[-1] * (1 - 0.0009 * (50 - days_ago) / 5 + random.gauss(0, 0.004))
            conn.execute(
                """INSERT INTO journal(created_ts, trade_date, setup_type,
                           direction, level, trigger, result, r_multiple,
                           notes, screenshot)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (int(d.timestamp()), d.strftime("%Y-%m-%d"), stype, direction,
                 round(px, 1), trigger, result, r_adj,
                 random.choice(["Followed the layer stack, no anticipation.",
                                "Waited for the retest — clean fill.",
                                "Cut early, thesis invalidated intraday.",
                                "Held to target, trailed behind 5m structure.",
                                "Size halved — event risk the next morning."]),
                 f"chart-{d.strftime('%Y%m%d')}-{i:02d}.png"))
    # ------------------------------------------------------------- alerts
    seeded_alerts = [
        ("sge_spike", "high", "Shanghai premium spike: $58/oz",
         "SGE over LBMA at $58.2 (5d Δ +27.1). Chinese physical demand pulling metal east.", 18),
        ("whale_single", "high", "🐋 12,400 oz PAXG transfer",
         "Paxos vault → Unknown EOA · ~$50.6M. Watch for exchange inflow follow-through.", 14),
        ("etf_flow", "medium", "GLD creation 🟢: +6.2 t today",
         "GLD holdings 1,039.4 t. Inflow supports the structural score.", 9),
        ("comex_drawdown", "high", "COMEX registered down 2.9% in 7 days",
         "Registered stock now 25.1 Moz. Deliverable supply tightening.", 5),
        ("cot_shift", "low", "Managed money +33,200 contracts this week",
         "COT MM net now 204,100. Crowding building — squeeze fuel both ways.", 2),
    ]
    for kind, sev, title, body, hours_ago in seeded_alerts:
        db.insert_alert(kind, sev, title, body, None, None,
                        "queued (no bot token)", now - hours_ago * 3600)

    # ------------------------------------------------------------- autopilot seed
    try:
        from .engines.automation import ensure_default_rules
        ensure_default_rules()
        # autopilot state
        if db.get_state("autopilot_enabled") is None:
            db.set_state("autopilot_enabled","1")
        if db.get_state("autopilot_mode") is None:
            db.set_state("autopilot_mode","full_auto")
        if db.get_state("paper_enabled") is None:
            db.set_state("paper_enabled","1")
        if db.get_state("auto_report_enabled") is None:
            db.set_state("auto_report_enabled","1")
        # seed 12 signals (mix of rule + auto_composite)
        base_price = spot_hist[-1]
        for i in range(12):
            ts = now - (12-i)*7200 - random.randint(0,1800)
            direction = random.choice(["LONG","SHORT","LONG","LONG","SHORT"])
            comp = round(random.uniform(-48,62),1)
            struct = round(random.uniform(-20,55),1)
            macro_v = round(random.uniform(-30,40),1)
            strength = abs(comp)
            price = round(base_price + random.uniform(-28,28),1)
            reason = random.choice([f"Composite {comp} stance shift", "SGE premium spike → LONG", "Whale sweep 12k oz", "Structural trap SHORT", "Auto composite signal"])
            db.insert_signal(ts, random.choice(["rule","auto_composite","paper_trade"]), direction, strength, price, comp, struct, macro_v, reason, status=random.choice(["active","active","closed","active"]))
        # seed 3 open + 9 closed paper positions
        for i in range(12):
            sig = random.randint(1,12)
            direction = random.choice(["LONG","SHORT"])
            entry = round(base_price + random.uniform(-35,35),1)
            size = round(random.uniform(4.0,18.0),1)
            sl = round(entry * (0.988 if direction=="LONG" else 1.012),1)
            tp = round(entry * (1.024 if direction=="LONG" else 0.976),1)
            if i < 3:
                # open
                db.open_position(sig, direction, entry, size, stop_loss=sl, take_profit=tp, note="seed open")
            else:
                pid = db.open_position(sig, direction, entry, size, stop_loss=sl, take_profit=tp, note="seed closed")
                # close with pnl
                exit_price = round(entry + random.uniform(-18, 28) * (1 if direction=="LONG" else -1),1)
                db.close_position(pid, exit_price)
        # automation runs log
        rules = db.all_automation_rules()
        for i in range(14):
            r = random.choice(rules) if rules else None
            ts = now - random.randint(600, 86400)
            db.log_run(r["id"] if r else None, r["trigger_type"] if r else "score_threshold", f"Rule '{r['name']}' fired" if r else "Auto composite signal", "ok", context={"seed":True}, result={"signal_id": random.randint(1,5)})
        # auto reports
        for d in [1,2,3]:
            db.insert_report("daily", f"Daily Gold Trace – {'LONG BIAS' if random.random()>0.5 else 'BALANCED'}", {"seed":True,"scores":{"composite": round(random.uniform(10,45),1)}})
    except Exception as e:
        # don't fail seed on automation
        pass
    return True
