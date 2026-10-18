"""
Structural engine: tCISD and Fibonacci.

------------------------------------------------------------------------------
tCISD - time-anchored Change In State of Delivery
------------------------------------------------------------------------------
"CISD" comes from the ICT / order-flow literature: a market that is *delivering*
price in one direction (a sequence of same-side displacement candles) flips when
price closes back through the **open of the displacement candle that ran
liquidity**. Running a prior swing low and immediately reclaiming the down-candle
open is the classic signature of a stop-run being absorbed - sell-side delivery
has changed to buy-side delivery.

The prefix "t" is this engine's contribution: a raw CISD is a *state*, not a
*timing*. The same CISD is a great entry at bar t+1 and a poor one at t+15. So
each event carries a timestamp and the score decays:

    w_time = exp(-(t - t0) / tau)          half-life tau bars, hard expiry T_max
    w_dist = exp(-overshoot / lambda)      penalise chasing price away from the CISD level
    score  = 100 * w_time * w_dist * quality * htf_alignment

with `quality` from the size of the displacement body (in ATR) and
`htf_alignment` from the higher-timeframe bias. An event is killed instantly if
price closes back through the invalidation extreme (delivery reverted).

------------------------------------------------------------------------------
Fibonacci
------------------------------------------------------------------------------
Legs are taken from *confirmed* fractal swings. With a leg anchor -> tip:

    retr = (tip - price) / (tip - anchor)      # 0 at the tip, 1 at the anchor

so retr = 0.618..0.786 is the 62-79% retracement band (ICT's Optimal Trade
Entry). Entry quality is a gaussian centred on retr = 0.70, modulated by:
  * level confluence across the primary leg, a secondary leg and the HTF leg,
  * Fibonacci *time* zones (1,2,3,5,8,13,21,34,55 bars from the anchor),
  * premium / discount location relative to the 0.5 equilibrium.
Extensions (1.272 / 1.618 / 2.0 / 2.618) supply the take-profit ladder.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from . import indicators as ind
from .config import Params


# ----------------------------------------------------------------------------
# Fair value gaps / imbalances
# ----------------------------------------------------------------------------


@dataclass
class FVG:
    i: int
    ts: pd.Timestamp
    direction: int      # +1 bullish, -1 bearish
    lo: float
    hi: float
    size_atr: float
    filled_at: Optional[int] = None


def detect_fvgs(df: pd.DataFrame, atr_s: pd.Series, min_atr: float = 0.05) -> List[FVG]:
    """Three-candle imbalance: candle i's low above candle i-2's high (bull).

    `min_atr` filters out noise: only imbalances at least this fraction of an ATR
    wide are kept.
    """
    out: List[FVG] = []
    h, l = df["high"].values, df["low"].values
    av = atr_s.bfill().fillna(1e-9).values
    for i in range(2, len(df)):
        a = av[i] if np.isfinite(av[i]) and av[i] > 0 else 1e-9
        up = l[i] - h[i - 2]
        dn = l[i - 2] - h[i]
        if up > 0 and up / a >= min_atr:
            out.append(FVG(i, df.index[i], 1, float(h[i - 2]), float(l[i]), float(up / a)))
        if dn > 0 and dn / a >= min_atr:
            out.append(FVG(i, df.index[i], -1, float(h[i]), float(l[i - 2]), float(dn / a)))
    # mark fills
    for g in out:
        for j in range(g.i + 1, min(g.i + 400, len(df))):
            if g.direction == 1 and l[j] <= g.lo:
                g.filled_at = j
                break
            if g.direction == -1 and h[j] >= g.hi:
                g.filled_at = j
                break
    return out


# ----------------------------------------------------------------------------
# tCISD
# ----------------------------------------------------------------------------


@dataclass
class CISDEvent:
    direction: int                 # +1 long, -1 short
    sweep_i: int
    sweep_ts: pd.Timestamp
    sweep_price: float
    delivery_i: int                # displacement candle that ran liquidity
    delivery_open: float
    delivery_extreme: float        # invalidation level
    trigger_price: float           # close must overcome delivery_open
    confirmed_at: int              # bar index of confirmation
    confirmed_ts: pd.Timestamp
    fvg: Optional[FVG] = None
    mss: bool = False              # market structure shift confirmed
    body_atr: float = 0.0
    invalidated_at: Optional[int] = None
    quality: float = 0.0

    def as_dict(self) -> dict:
        return {
            "dir": "LONG" if self.direction > 0 else "SHORT",
            "sweep_ts": self.sweep_ts,
            "swept_level": self.sweep_price,
            "cisd_level": self.delivery_open,
            "invalidation": self.delivery_extreme,
            "confirmed": self.confirmed_ts,
            "mss": self.mss,
            "body_atr": round(self.body_atr, 2),
            "state": "INVALIDATED" if self.invalidated_at is not None else "ACTIVE",
        }


def detect_cisd(
    df: pd.DataFrame,
    p: Params,
    htf_bias: Optional[pd.Series] = None,
) -> Dict[str, object]:
    """Causal CISD detection. Returns per-bar scores plus the event list."""
    n = len(df)
    atr_s = ind.atr(df, p.atr_period)
    cs = ind.candle_stats(df, atr_s)
    sw = ind.swings(df, p.pivot_left, p.pivot_right)

    o, h, l, c = (df[k].values for k in ("open", "high", "low", "close"))

    # confirmed swing levels known *at* each bar
    swing_high = np.full(n, np.nan)
    swing_low = np.full(n, np.nan)
    for _, r in sw.iterrows():
        ka = int(min(r.known_at, n - 1))
        if r.kind == "H":
            swing_high[ka:] = r.price
        else:
            swing_low[ka:] = r.price

    events: List[CISDEvent] = []
    body_atr = cs["body_atr"].values
    dirn = cs["dir"].values

    i = p.pivot_left + p.pivot_right + 2
    while i < n - 1:
        # ---- bullish setup: sweep of a confirmed swing low, then reclaim ----
        sl = swing_low[i]
        sh = swing_high[i]
        if np.isfinite(sl) and l[i] < sl and c[i] > sl:
            wick = min(o[i], c[i]) - l[i]
            rng = h[i] - l[i]
            if rng > 0 and wick / rng >= p.sweep_wick_frac:
                # delivery candle: strongest bearish displacement around the sweep
                lo_r = max(0, i - 3)
                cand = [j for j in range(lo_r, min(i + 3, n)) if dirn[j] < 0]
                if cand:
                    j0 = max(cand, key=lambda j: body_atr[j])
                    lvl = o[j0]
                    extreme = min(l[j0], sl)
                    for j in range(i + 1, min(i + p.tcisd_max_bars, n)):
                        if c[j] < extreme:
                            break
                        if c[j] > lvl:
                            # optional market-structure shift check
                            mss = bool(np.isfinite(sh) and c[j] > sh)
                            disp = max(body_atr[i + 1: j + 1]) if j > i else body_atr[j]
                            q = float(np.clip(disp / 1.5, 0.20, 1.0))
                            if not mss:
                                q *= 0.85
                            ev = CISDEvent(
                                1, i, df.index[i], float(sl), j0, float(lvl), float(extreme),
                                float(lvl), j, df.index[j], None, mss, float(disp), None, q,
                            )
                            events.append(ev)
                            i = j
                            break
        # ---- bearish setup: sweep of a confirmed swing high, then reject ----
        sh = swing_high[i]
        if np.isfinite(sh) and h[i] > sh and c[i] < sh:
            wick = h[i] - max(o[i], c[i])
            rng = h[i] - l[i]
            if rng > 0 and wick / rng >= p.sweep_wick_frac:
                lo_r = max(0, i - 3)
                cand = [j for j in range(lo_r, min(i + 3, n)) if dirn[j] > 0]
                if cand:
                    j0 = max(cand, key=lambda j: body_atr[j])
                    lvl = o[j0]
                    extreme = max(h[j0], sh)
                    for j in range(i + 1, min(i + p.tcisd_max_bars, n)):
                        if c[j] > extreme:
                            break
                        if c[j] < lvl:
                            mss = bool(np.isfinite(swing_low[j]) and c[j] < swing_low[j])
                            disp = max(body_atr[i + 1: j + 1]) if j > i else body_atr[j]
                            q = float(np.clip(disp / 1.5, 0.20, 1.0))
                            if not mss:
                                q *= 0.85
                            ev = CISDEvent(
                                -1, i, df.index[i], float(sh), j0, float(lvl), float(extreme),
                                float(lvl), j, df.index[j], None, mss, float(disp), None, q,
                            )
                            events.append(ev)
                            i = j
                            break
        i += 1

    # invalidate events once price closes back through the extreme
    for ev in events:
        seg = range(ev.confirmed_at + 1, n)
        for j in seg:
            if ev.direction > 0 and c[j] < ev.delivery_extreme:
                ev.invalidated_at = j
                break
            if ev.direction < 0 and c[j] > ev.delivery_extreme:
                ev.invalidated_at = j
                break

    # attach the fair value gap created on the confirmation bar, if any
    fvgs = detect_fvgs(df, atr_s)
    fvg_by_bar = {g.i: g for g in fvgs}
    for ev in events:
        for k in range(ev.confirmed_at, max(ev.confirmed_at - 3, -1), -1):
            g = fvg_by_bar.get(k)
            if g and g.direction == ev.direction:
                ev.fvg = g
                break

    # ---- per-bar time-decayed scoring -------------------------------------
    long_s = np.zeros(n)
    short_s = np.zeros(n)
    bars_since = np.full(n, np.nan)
    active_level = np.full(n, np.nan)
    atr_v = atr_s.values
    close_v = c
    tau = max(1e-6, p.tcisd_half_life / math.log(2.0))   # exp(-dt/tau) halves at half_life

    for ev in events:
        end = ev.invalidated_at if ev.invalidated_at is not None else n
        start = ev.confirmed_at
        lvl = ev.delivery_open
        for j in range(start, min(end, min(n, start + p.tcisd_max_bars))):
            dt = j - start
            w_time = math.exp(-dt / tau)
            a = atr_v[j] if np.isfinite(atr_v[j]) and atr_v[j] > 0 else 1.0
            d = (close_v[j] - lvl) / a * ev.direction      # >0 = price ran in our favour
            over = max(0.0, d)
            under = max(0.0, -d - 0.5)                      # allow 0.5 ATR of pullback
            w_dist = math.exp(-over / p.tcisd_dist_lambda) * math.exp(-under / (2 * p.tcisd_dist_lambda))
            align = 0.70
            if htf_bias is not None and j < len(htf_bias):
                b = htf_bias.iloc[j]
                if np.isfinite(b):
                    align = 1.0 if np.sign(b) == ev.direction and abs(b) > 0.25 else (
                        0.6 if abs(b) <= 0.25 else 0.25)
            sc = 100.0 * w_time * w_dist * ev.quality * align
            if ev.direction > 0:
                if sc > long_s[j]:
                    long_s[j] = sc
                    bars_since[j] = dt
                    active_level[j] = lvl
            else:
                if sc > short_s[j]:
                    short_s[j] = sc
                    bars_since[j] = dt
                    active_level[j] = lvl

    out = pd.DataFrame(
        {
            "tcisd_long": long_s,
            "tcisd_short": short_s,
            "tcisd_bars_since": bars_since,
            "tcisd_level": active_level,
        },
        index=df.index,
    )
    return {"frame": out, "events": events, "atr": atr_s, "candles": cs, "fvg": fvgs}


# ----------------------------------------------------------------------------
# Fibonacci
# ----------------------------------------------------------------------------


@dataclass
class Leg:
    anchor_i: int
    anchor_price: float
    tip_i: int
    tip_price: float
    direction: int   # +1 impulse up, -1 impulse down
    kind: str = "primary"

    @property
    def size(self) -> float:
        return abs(self.tip_price - self.anchor_price)

    def retr(self, price: float) -> float:
        if self.size <= 0:
            return np.nan
        return (self.tip_price - price) / (self.tip_price - self.anchor_price)

    def price_at_retr(self, r: float) -> float:
        return self.tip_price - r * (self.tip_price - self.anchor_price)

    def ext_price(self, ratio: float) -> float:
        return self.tip_price + (ratio - 1.0) * (self.tip_price - self.anchor_price)

    def levels(self, ratios) -> Dict[float, float]:
        return {r: self.price_at_retr(r) for r in ratios}


def build_legs(sw: pd.DataFrame, lookback: int = 220) -> Dict[str, Leg]:
    """Primary (most recent completed impulse), secondary, and macro legs."""
    legs: Dict[str, Leg] = {}
    if sw.empty or len(sw) < 4:
        return legs
    s = sw.tail(8)
    # primary: last two opposite pivots
    last = s.iloc[-1]
    prev = None
    for k in range(len(s) - 2, -1, -1):
        if s.iloc[k].kind != last.kind:
            prev = s.iloc[k]
            break
    if prev is not None:
        if last.kind == "H":
            legs["primary"] = Leg(int(prev.i), float(prev.price), int(last.i), float(last.price), +1, "primary")
        else:
            legs["primary"] = Leg(int(prev.i), float(prev.price), int(last.i), float(last.price), -1, "primary")
    # secondary: the impulse before that
    if len(s) >= 5:
        p2, p1 = s.iloc[-3], s.iloc[-2]
        if p1.kind != p2.kind:
            if p1.kind == "H":
                legs["secondary"] = Leg(int(p2.i), float(p2.price), int(p1.i), float(p1.price), +1, "secondary")
            else:
                legs["secondary"] = Leg(int(p2.i), float(p2.price), int(p1.i), float(p1.price), -1, "secondary")
    # macro: extreme range over the lookback
    if len(sw) >= 6:
        w = sw[sw.i >= sw.i.max() - lookback]
        if len(w) >= 2:
            hi = w[w.kind == "H"]
            lo = w[w.kind == "L"]
            if len(hi) and len(lo):
                hrow = hi.loc[hi.price.idxmax()]
                lrow = lo.loc[lo.price.idxmin()]
                if hrow.i > lrow.i:
                    legs["macro"] = Leg(int(lrow.i), float(lrow.price), int(hrow.i), float(hrow.price), +1, "macro")
                else:
                    legs["macro"] = Leg(int(hrow.i), float(hrow.price), int(lrow.i), float(lrow.price), -1, "macro")
    return legs


def fib_frame(
    df: pd.DataFrame,
    legs: Dict[str, Leg],
    p: Params,
    atr_s: pd.Series,
) -> pd.DataFrame:
    """Per-bar Fibonacci location / timing scores."""
    n = len(df)
    close = df["close"].values
    atr_v = atr_s.values
    long_s = np.zeros(n)
    short_s = np.zeros(n)
    retr_out = np.full(n, np.nan)
    conf_out = np.zeros(n)
    tz_out = np.zeros(n)

    if not legs:
        return pd.DataFrame(
            {"fib_long": long_s, "fib_short": short_s, "fib_retr": retr_out,
             "fib_confluence": conf_out, "fib_timezone": tz_out}, index=df.index
        )

    prim = legs.get("primary")
    for j in range(n):
        a = atr_v[j] if np.isfinite(atr_v[j]) and atr_v[j] > 0 else 1.0
        px = close[j]
        best_long = 0.0
        best_short = 0.0
        best_conf = 0.0
        best_retr = np.nan
        for name, leg in legs.items():
            w = 1.0 if name == "primary" else (0.7 if name == "secondary" else 0.5)
            r = leg.retr(px)
            if not np.isfinite(r):
                continue
            # gaussian centred on the 70% retracement (middle of the OTE band)
            g = math.exp(-0.5 * ((r - 0.70) / 0.18) ** 2)
            # discount / premium: reward being on the correct side of equilibrium
            if leg.direction > 0:
                side_long = 1.15 if r > 0.45 else 0.65      # buying below equilibrium
                side_short = 1.15 if r < 0.45 else 0.65
            else:
                side_long = 1.15 if r < 0.45 else 0.65
                side_short = 1.15 if r > 0.45 else 0.65
            # level confluence: how many fib ratios sit within tolerance
            conf = 0.0
            for ratio in p.fib_levels:
                lp = leg.price_at_retr(ratio)
                if abs(px - lp) <= p.fib_level_atr_tol * a:
                    conf += 1.0
            for other_name, oleg in legs.items():
                if other_name == name:
                    continue
                for ratio in p.fib_levels:
                    lp = oleg.price_at_retr(ratio)
                    if abs(px - lp) <= p.fib_confluence_atr * a:
                        conf += 0.5
            conf = min(conf, 4.0)
            # fibonacci time zones measured from the leg anchor
            dt = j - leg.anchor_i
            tz = 0.0
            if dt >= 0:
                for node in p.fib_time_nodes:
                    if abs(dt - node) <= 1:
                        tz = 1.0
                        break
            base = 100.0 * g * side_long * (0.75 + 0.06 * conf + 0.15 * tz)
            sc_l = base * w
            base_s = 100.0 * g * side_short * (0.75 + 0.06 * conf + 0.15 * tz)
            sc_s = base_s * w
            if sc_l > best_long:
                best_long = sc_l
                best_retr = r if name == "primary" else best_retr
            if sc_s > best_short:
                best_short = sc_s
            best_conf = max(best_conf, conf)
        if prim is not None:
            rr = prim.retr(px)
            if np.isfinite(rr):
                retr_out[j] = rr
            tz_out[j] = 1.0 if any(abs((j - prim.anchor_i) - node) <= 1 for node in p.fib_time_nodes) else 0.0
        long_s[j] = min(100.0, best_long)
        short_s[j] = min(100.0, best_short)
        conf_out[j] = best_conf

    return pd.DataFrame(
        {
            "fib_long": long_s,
            "fib_short": short_s,
            "fib_retr": retr_out,
            "fib_confluence": conf_out,
            "fib_timezone": tz_out,
        },
        index=df.index,
    )


def fib_levels_for_plot(legs: Dict[str, Leg], p: Params) -> List[dict]:
    """Retracement + extension lines for charting."""
    out = []
    for name, leg in legs.items():
        if name == "macro":
            continue
        for r in p.fib_levels:
            out.append({"kind": "retr", "leg": name, "ratio": r, "price": leg.price_at_retr(r)})
        for r in p.fib_ext:
            out.append({"kind": "ext", "leg": name, "ratio": r, "price": leg.ext_price(r)})
    return out
