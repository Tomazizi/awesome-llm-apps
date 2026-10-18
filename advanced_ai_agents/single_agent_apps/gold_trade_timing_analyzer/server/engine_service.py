"""
Bridge between the `core/` engine and the HTTP API: turns DataFrames into
JSON-safe dictionaries the SPA can render, and keeps parameter handling in one
place so the web app and the Streamlit app can never drift apart.
"""
from __future__ import annotations

import os
import sys
from typing import Dict, Optional

import numpy as np
import pandas as pd

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

from core.config import HTF_MAP, Params  # noqa: E402
from core import signal_engine as se  # noqa: E402
from core import backtest as bt  # noqa: E402
from core import intermarket as imk  # noqa: E402
from core.data_feeds import align_universe  # noqa: E402

from .store import json_safe  # noqa: E402

PRIMARY = "XAUUSD"
PROXY_SYMBOLS = ("XAGUSD", "DXY", "SPX", "VIX", "WTI", "COPPER", "US10Y")

PANEL_COLS = [
    "close", "atr", "htf_bias", "prevailing", "session", "session_w",
    "tcisd_long", "tcisd_short", "tcisd_bars_since", "tcisd_level",
    "fib_long", "fib_short", "fib_retr", "fib_confluence", "fib_timezone",
    "mn_long", "mn_short", "mn_z", "mn_fv_z",
    "ssmt_long", "ssmt_short", "ssmt_div", "ssmt_rel",
    "exh_up", "exh_dn", "reversal_risk", "div_bear", "div_bull",
]


def params_from(values: Optional[dict]) -> Params:
    p = Params()
    if values:
        for k, v in values.items():
            if hasattr(p, k):
                cur = getattr(p, k)
                try:
                    if isinstance(cur, bool):
                        setattr(p, k, bool(v))
                    elif isinstance(cur, int) and not isinstance(cur, bool):
                        setattr(p, k, int(round(float(v))))
                    elif isinstance(cur, float):
                        setattr(p, k, float(v))
                    else:
                        setattr(p, k, v)
                except Exception:
                    pass
    return p


def resample_htf(df: pd.DataFrame, interval: str) -> pd.DataFrame:
    htf_iv = HTF_MAP.get(interval, "4h")
    if htf_iv == interval:
        return df
    rule = htf_iv.replace("m", "min").replace("1d", "1D")
    return (df.resample(rule)
              .agg({"open": "first", "high": "max", "low": "min",
                    "close": "last", "volume": "sum"}).dropna())


def prepare(frames: Dict[str, pd.DataFrame], interval: str):
    """Split the loaded universe into primary / proxies / driver matrix."""
    df = frames.get(PRIMARY)
    if df is None or len(df) < 60:
        return None
    htf = resample_htf(df, interval)
    prices = align_universe(frames, PRIMARY)
    proxies = {}
    for a in PROXY_SYMBOLS:
        if a in frames and a != PRIMARY:
            sub = frames[a].reindex(df.index).ffill()
            if sub["close"].notna().sum() > 60:
                proxies[a] = sub
    return df, htf, prices, proxies


def _candles(df: pd.DataFrame, limit: int = 600) -> list:
    d = df.tail(limit)
    return [
        [int(t.value // 1_000_000), float(o), float(h), float(l), float(c),
         float(v) if np.isfinite(v) else 0.0]
        for t, o, h, l, c, v in zip(d.index, d["open"], d["high"], d["low"],
                                    d["close"], d["volume"])
    ]


def _panel_tail(panel: pd.DataFrame, limit: int = 400) -> dict:
    d = panel.tail(limit)
    cols = [c for c in PANEL_COLS if c in d.columns]
    out = {"ts": [int(t.value // 1_000_000) for t in d.index]}
    for c in cols:
        out[c] = json_safe(d[c].tolist())
    return out


def analyse(frames: Dict[str, pd.DataFrame], interval: str, params: Params,
            spot: Optional[float] = None, news_bias: float = 0.0,
            event_penalty: float = 1.0, lookback: int = 400) -> dict:
    prep = prepare(frames, interval)
    if prep is None:
        return {"error": "No primary XAUUSD series loaded (need >= 60 bars)."}
    df, htf, prices, proxies = prep

    bundle = se.build_panel(df, htf, proxies=proxies, prices=prices, p=params)
    sig = se.current_signal(bundle, df, params, news_bias=news_bias,
                            event_penalty=event_penalty, live_price=spot)
    panel: pd.DataFrame = bundle["panel"]

    trade = sig["trade"]
    trade_json = None
    if trade is not None:
        trade_json = {
            "direction": "LONG" if trade.direction > 0 else "SHORT",
            "entry": trade.entry, "stop": trade.stop,
            "tp1": trade.tp1, "tp2": trade.tp2, "tp3": trade.tp3,
            "rr": trade.rr, "size": trade.size, "risk_usd": trade.risk_usd,
            "risk_atr": trade.risk_atr, "tier": trade.tier,
            "score": trade.score, "notes": trade.notes,
            "components": json_safe(trade.components),
        }

    events = []
    for e in (bundle["cisd"]["events"] or [])[-14:]:
        events.append({
            "dir": "LONG" if e.direction > 0 else "SHORT",
            "sweep_ts": int(e.sweep_ts.value // 1_000_000),
            "confirmed_ts": int(e.confirmed_ts.value // 1_000_000),
            "swept_level": e.sweep_price,
            "cisd_level": e.delivery_open,
            "invalidation": e.delivery_extreme,
            "mss": bool(e.mss),
            "body_atr": e.body_atr,
            "quality": e.quality,
            "state": "INVALIDATED" if e.invalidated_at is not None else "ACTIVE",
        })

    fvgs = []
    for g in (bundle["fvg"] or [])[-60:]:
        if g.ts < df.index[-lookback]:
            continue
        fvgs.append({
            "ts": int(g.ts.value // 1_000_000),
            "dir": g.direction, "lo": g.lo, "hi": g.hi,
            "size_atr": g.size_atr,
            "filled_ts": int(df.index[g.filled_at].value // 1_000_000)
            if g.filled_at is not None and g.filled_at < len(df) else None,
        })

    legs = []
    for name, lg in (bundle["legs"] or {}).items():
        retr = lg.retr(float(df["close"].iloc[-1]))
        legs.append({
            "leg": name,
            "dir": "up" if lg.direction > 0 else "down",
            "anchor": lg.anchor_price, "tip": lg.tip_price, "size": lg.size,
            "anchor_ts": int(df.index[min(lg.anchor_i, len(df) - 1)].value // 1_000_000),
            "tip_ts": int(df.index[min(lg.tip_i, len(df) - 1)].value // 1_000_000),
            "retr": json_safe(retr),
            "in_ote": bool(np.isfinite(retr) and 0.618 <= retr <= 0.786),
            "retr_levels": {str(r): lg.price_at_retr(r) for r in params.fib_levels},
            "ext_levels": {str(r): lg.ext_price(r) for r in params.fib_ext},
        })

    mn = bundle["mn"]
    ssmt = bundle["ssmt"]
    rev = bundle["rev"] or {}

    result = {
        "meta": {
            "interval": interval,
            "htf": HTF_MAP.get(interval, "4h"),
            "bars": int(len(df)),
            "last_bar": df.index[-1].isoformat(),
            "spot": spot,
            "n_proxies": len(proxies),
            "n_factors": int(mn.get("n_factors", 0) or 0),
        },
        "signal": {
            "direction": "LONG" if sig["direction"] > 0 else "SHORT",
            "score": sig["score"],
            "score_long": sig["score_long"],
            "score_short": sig["score_short"],
            "tier": sig["tier"],
            "prevailing": "up" if sig["prevailing"] > 0 else "down" if sig["prevailing"] < 0 else "flat",
            "atr": sig["atr"],
            "bar": df.index[-1].isoformat(),
            "trade": trade_json,
            "gates": [{"name": g[0], "passed": bool(g[1]), "detail": str(g[2])}
                      for g in sig["gates"]],
            "components": json_safe(sig["components"]),
        },
        "candles": _candles(df, min(700, max(200, lookback))),
        "panel": _panel_tail(panel, min(500, max(200, lookback))),
        "structure": {"events": events, "fvgs": fvgs, "legs": legs},
        "models": {
            "tcisd": {
                "long": _last(panel, "tcisd_long"),
                "short": _last(panel, "tcisd_short"),
                "bars_since": _last(panel, "tcisd_bars_since"),
                "level": _last(panel, "tcisd_level"),
                "half_life": params.tcisd_half_life,
                "expiry": params.tcisd_max_bars,
                "event_count": len(bundle["cisd"]["events"] or []),
                "active_events": sum(1 for e in (bundle["cisd"]["events"] or [])
                                     if e.invalidated_at is None),
                "open_fvg": sum(1 for g in (bundle["fvg"] or []) if g.filled_at is None),
            },
            "ssmt": {
                "div": ssmt["ssmt_div"],
                "rel_strength": ssmt["ssmt_rel_strength"],
                "long": ssmt["ssmt_long"] * 100,
                "short": ssmt["ssmt_short"] * 100,
                "breakdown": json_safe(
                    ssmt["breakdown"].to_dict(orient="records")
                    if ssmt["breakdown"] is not None and not ssmt["breakdown"].empty else []),
            },
            "fib": {
                "retr": _last(panel, "fib_retr"),
                "long": _last(panel, "fib_long"),
                "short": _last(panel, "fib_short"),
                "confluence": _last(panel, "fib_confluence"),
                "timezone": bool(_last(panel, "fib_timezone") or 0),
                "ote": [params.ote_lo, params.ote_hi],
                "legs": legs,
            },
            "market_neutral": {
                "z": _last(panel, "mn_z"),
                "fv_z": _last(panel, "mn_fv_z"),
                "long": _last(panel, "mn_long") * 100 if "mn_long" in panel else None,
                "halflife": mn.get("halflife"),
                "hurst": mn.get("hurst"),
                "hurst_level": mn.get("hurst_lvl"),
                "adf_t": mn.get("adf_t"),
                "adf_p": mn.get("adf_p"),
                "r2": mn.get("r2"),
                "w_meanrev": mn.get("w_meanrev"),
                "betas": json_safe(mn.get("betas") or {}),
                "factors": mn.get("factors") or [],
            },
            "reversal": {
                "risk": _last(panel, "reversal_risk"),
                "auc": rev.get("auc"),
                "n_oos": rev.get("n_oos", 0),
                "base_rate": rev.get("base_rate"),
                "gate": params.rev_gate,
                "horizon": params.rev_horizon,
                "move_atr": params.rev_move_atr,
                "coefficients": json_safe(_coef_list(rev.get("coefs"))),
            },
            "exhaustion": {
                "up": _last(panel, "exh_up"),
                "down": _last(panel, "exh_dn"),
                "atr_ratio": _last(bundle["exh"], "atr_ratio"),
                "stack_up": _last(bundle["exh"], "stack_fvg_up"),
                "stack_down": _last(bundle["exh"], "stack_fvg_dn"),
                "div_bear": _last(panel, "div_bear"),
                "div_bull": _last(panel, "div_bull"),
            },
        },
        "weights": json_safe(params.normalised_weights()),
    }

    # Scrub the payload, then re-attach the raw bundle (DataFrames) for the
    # caller - it is popped before the response is serialised. Starlette
    # serialises with allow_nan=False, so a single un-scrubbed NaN would
    # otherwise 500 the whole endpoint.
    safe = json_safe(result)
    safe["bundle"] = bundle
    return safe


def _coef_list(coefs) -> list:
    """`coefs` is a pandas Series - `or {}` on a Series raises, so test first."""
    if coefs is None:
        return []
    try:
        if len(coefs) == 0:
            return []
        return [{"feature": str(k), "coef": float(v)} for k, v in coefs.items()]
    except Exception:
        return []


def _last(panel: pd.DataFrame, col: str):
    if col not in panel.columns:
        return None
    v = panel[col].iloc[-1]
    return json_safe(v)


def run_backtest(frames: Dict[str, pd.DataFrame], interval: str, params: Params) -> dict:
    prep = prepare(frames, interval)
    if prep is None:
        return {"error": "No primary XAUUSD series loaded (need >= 60 bars)."}
    df, htf, prices, proxies = prep
    bundle = se.build_panel(df, htf, proxies=proxies, prices=prices, p=params)

    sigs = bt.generate_signals(df, bundle, params)
    trades = bt.run_backtest(df, sigs, params)
    metrics = bt.headline_metrics(trades, params, freq=interval) if not trades.empty else {}
    delay = bt.delay_curve(df, sigs, params, atr=bundle["panel"]["atr"])
    funnel = bt.gate_funnel(df, bundle, params)
    cal = bt.calibration(trades)
    att = bt.attribution(trades)
    sens = bt.threshold_sensitivity(df, bundle, params)

    def recs(d, n=None):
        if d is None or d.empty:
            return []
        out = d.head(n) if n else d
        return json_safe(out.to_dict(orient="records"))

    result = {
        "meta": {"interval": interval, "bars": int(len(df)), "signals": len(sigs)},
        "metrics": json_safe(metrics),
        "delay": recs(delay),
        "funnel": recs(funnel),
        "calibration": recs(cal),
        "attribution": recs(att),
        "sensitivity": recs(sens),
        "trades": recs(trades.tail(400)),
        "equity": json_safe(
            [{"ts": int(t.value // 1_000_000), "r": r}
             for t, r in zip(trades["ts"], trades["r"].cumsum())]
            if not trades.empty else []),
    }
    return json_safe(result)


def grade_open_signals(frames: Dict[str, pd.DataFrame], interval: str,
                       params: Params, journal) -> int:
    """Close out journal entries whose holds have completed, using real prices.

    This is what turns the alert feed into an auditable track record: every
    alerted signal is later marked to what actually happened.
    """
    df = frames.get(PRIMARY)
    if df is None or len(df) < 20:
        return 0
    graded = 0
    hi, lo = df["high"].values, df["low"].values
    cl = df["close"].values
    idx = {int(t.value // 1_000_000): i for i, t in enumerate(df.index)}

    for s in journal.open_signals(PRIMARY):
        try:
            start_i = idx.get(int(pd.Timestamp(s["ts"]).value // 1_000_000))
        except Exception:
            start_i = None
        if start_i is None:
            # fall back to the nearest bar at or after the signal timestamp
            cands = [i for i, t in enumerate(df.index)
                     if t >= pd.Timestamp(s["ts"])]
            start_i = cands[0] if cands else None
        if start_i is None:
            continue
        end = min(start_i + params.bt_max_hold, len(df) - 1)
        if end <= start_i + 1:
            continue        # not enough history yet

        entry = float(s["entry"]); stop = float(s["stop"])
        tp1 = float(s["tp1"] or 0); tp2 = float(s["tp2"] or 0); tp3 = float(s["tp3"] or 0)
        direction = 1 if s["direction"] == "LONG" else -1
        risk = abs(entry - stop)
        if risk <= 0:
            continue
        cur_stop = stop
        realised, remaining = 0.0, 1.0
        hit1 = hit2 = False
        mae = mfe = 0.0
        exit_kind = "TIME"
        for j in range(start_i, end + 1):
            if direction > 0:
                mae = min(mae, (lo[j] - entry) / risk)
                mfe = max(mfe, (hi[j] - entry) / risk)
                if lo[j] <= cur_stop:
                    realised += remaining * (cur_stop - entry) / risk
                    remaining = 0.0; exit_kind = "STOP"; break
                if not hit1 and hi[j] >= tp1:
                    hit1 = True; realised += 0.5 * (tp1 - entry) / risk
                    remaining -= 0.5; cur_stop = entry
                elif hit1 and not hit2 and hi[j] >= tp2:
                    hit2 = True; realised += 0.3 * (tp2 - entry) / risk
                    remaining -= 0.3; cur_stop = tp1
                elif hit2 and hi[j] >= tp3:
                    realised += 0.2 * (tp3 - entry) / risk
                    remaining = 0.0; exit_kind = "TP3"; break
            else:
                mae = min(mae, (entry - hi[j]) / risk)
                mfe = max(mfe, (entry - lo[j]) / risk)
                if hi[j] >= cur_stop:
                    realised += remaining * (entry - cur_stop) / risk
                    remaining = 0.0; exit_kind = "STOP"; break
                if not hit1 and lo[j] <= tp1:
                    hit1 = True; realised += 0.5 * (entry - tp1) / risk
                    remaining -= 0.5; cur_stop = entry
                elif hit1 and not hit2 and lo[j] <= tp2:
                    hit2 = True; realised += 0.3 * (entry - tp2) / risk
                    remaining -= 0.3; cur_stop = tp1
                elif hit2 and lo[j] <= tp3:
                    realised += 0.2 * (entry - tp3) / risk
                    remaining = 0.0; exit_kind = "TP3"; break
        if remaining > 0:
            realised += remaining * (cl[end] - entry) / risk * direction
        cost = (params.spread_usd + params.commission_usd) / risk
        r = realised - cost
        status = "closed" if (end - start_i) >= params.bt_max_hold or remaining == 0 else "open"
        journal.update_outcome(s["id"], {
            "exit": exit_kind, "r": r, "mae": mae, "mfe": mfe,
            "bars": int(end - start_i),
            "tp1": hit1, "tp2": hit2,
            "efficiency": (mfe / (mfe + abs(mae))) if (mfe > 0 and (mfe + abs(mae)) > 0) else 0.0,
        }, r, status)
        graded += 1
    return graded
