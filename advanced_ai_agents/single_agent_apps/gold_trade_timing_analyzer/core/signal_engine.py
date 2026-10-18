"""
Signal engine: turns the four model families into one Entry Timing Score (ETS)
and then into a fully specified trade.

    ETS_d = 100 * [ w_tcisd   * tCISD_d
                  + w_fib     * Fib_d
                  + w_mn      * MarketNeutral_d
                  + w_ssmt    * SSMT_d
                  + w_exhaust * (1 - Exhaustion_against_d)
                  + w_reversal* ReversalComponent_d
                  + w_session * Session_d
                  + w_news    * NewsAlignment_d ]        * event_penalty

Every component lives in [0, 1] and the weights are renormalised to sum to 1, so
the ETS is itself a calibrated 0-100 reading of *how good this bar is to enter in
direction d*. Three hard gates sit on top of the score:

    1. a structural trigger must exist (tCISD > 0) - Fib / stats alone never fire
    2. the trade must clear the minimum reward:risk
    3. reversal risk must not be extreme

Those gates are what separate "a model produced a number" from "there is a trade
here". The score is then mapped to a conviction tier and a position size.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from . import indicators as ind
from . import structure as stx
from . import intermarket as imk
from . import risk as rsk
from .config import Params, SESSION_TREND_WEIGHT


# ----------------------------------------------------------------------------
# Panel assembly
# ----------------------------------------------------------------------------


def htf_bias_series(htf: pd.DataFrame, p: Params) -> pd.Series:
    """Higher-timeframe directional bias in [-1, 1]."""
    close = htf["close"]
    ema = ind.ema(close, p.htf_bias_ema)
    atr_s = ind.atr(htf, p.atr_period)
    trend = np.tanh((close - ema) / (2 * atr_s.replace(0, np.nan)))
    sw = ind.swings(htf, p.pivot_left, p.pivot_right)
    struct = np.sign(close.diff(20)).fillna(0)
    bias = 0.65 * trend.fillna(0) + 0.35 * struct
    return bias.clip(-1, 1).rename("htf_bias")


def build_panel(
    df: pd.DataFrame,
    htf: pd.DataFrame,
    proxies: Optional[Dict[str, pd.DataFrame]] = None,
    prices: Optional[pd.DataFrame] = None,
    p: Params = Params(),
    with_reversal_model: bool = True,
) -> Dict[str, object]:
    """Compute every sub-model on the execution timeframe. Causal throughout."""
    proxies = proxies or {}
    prices = prices if prices is not None else pd.DataFrame({"XAUUSD": df["close"]})

    bias_h = htf_bias_series(htf, p)
    bias = bias_h.reindex(df.index, method="ffill")

    cisd = stx.detect_cisd(df, p, htf_bias=bias)
    cs = cisd["candles"]
    fvgs = cisd["fvg"]
    atr_s = cisd["atr"]

    sw = ind.swings(df, p.pivot_left, p.pivot_right)
    legs = stx.build_legs(sw, p.fib_lookback)
    fib = stx.fib_frame(df, legs, p, atr_s)

    div = rsk.divergence_frame(df, p, swings=sw)
    exh = rsk.exhaustion_frame(df, p, div, fvgs)

    mn = imk.market_neutral(df, prices, p)
    ssmt = imk.ssmt_aggregate(df, proxies, p)

    panel = pd.DataFrame(index=df.index)
    panel = panel.join(cisd["frame"])
    panel = panel.join(fib)
    panel = panel.join(div)
    panel = panel.join(exh)
    panel["mn_long"] = mn["mn_long"].reindex(df.index).fillna(0.5)
    panel["mn_short"] = mn["mn_short"].reindex(df.index).fillna(0.5)
    panel["mn_z"] = mn["mn_z"].reindex(df.index)
    panel["mn_fv_z"] = mn["mn_fv_z"].reindex(df.index)
    panel["ssmt_long"] = float(ssmt["ssmt_long"])
    panel["ssmt_short"] = float(ssmt["ssmt_short"])
    panel["ssmt_div"] = float(ssmt["ssmt_div"])
    panel["ssmt_rel"] = float(ssmt["ssmt_rel_strength"])
    panel["htf_bias"] = bias
    panel["atr"] = atr_s
    panel["session"] = ind.session_series(df.index)
    panel["session_w"] = panel["session"].map(SESSION_TREND_WEIGHT).fillna(0.7)
    panel["close"] = df["close"]

    rev = None
    if with_reversal_model and len(df) > 400:
        try:
            rev = rsk.reversal_model(df, p, div, exh)
            panel["reversal_risk"] = rev["prob"].reindex(df.index)
        except Exception:
            rev = None
    if "reversal_risk" not in panel:
        panel["reversal_risk"] = 0.35

    # prevailing move direction, used to apply reversal risk on the right side
    panel["prevailing"] = np.sign(df["close"].diff(20)).fillna(0)

    return {
        "panel": panel,
        "cisd": cisd,
        "legs": legs,
        "swings": sw,
        "fib": fib,
        "div": div,
        "exh": exh,
        "mn": mn,
        "ssmt": ssmt,
        "rev": rev,
        "atr": atr_s,
        "candles": cs,
        "fvg": fvgs,
        "htf_bias": bias,
    }


# ----------------------------------------------------------------------------
# Composite score
# ----------------------------------------------------------------------------


def composite(
    panel: pd.DataFrame,
    p: Params,
    news_bias: float = 0.0,
    event_penalty: float = 1.0,
) -> pd.DataFrame:
    w = p.normalised_weights()
    out = pd.DataFrame(index=panel.index)

    for d, sfx in ((1, "long"), (-1, "short")):
        tcisd = panel[f"tcisd_{sfx}"] / 100.0
        fib = panel[f"fib_{sfx}"] / 100.0
        mn = panel[f"mn_{sfx}"]
        ssmt = panel[f"ssmt_{sfx}"]
        exh_against = panel["exh_up"] if d > 0 else panel["exh_dn"]
        exh_c = 1.0 - exh_against / 100.0

        rev = panel["reversal_risk"]
        aligned = np.sign(panel["prevailing"]) == d
        rev_c = np.where(aligned, 1.0 - rev, 0.5 + 0.5 * rev)

        sess = panel["session_w"]
        sess_c = np.where(aligned, sess, 0.5 + 0.5 * sess)

        news_c = 0.5 + 0.5 * float(np.clip(news_bias, -1, 1)) * d

        score = 100.0 * (
            w["tcisd"] * tcisd
            + w["fib"] * fib
            + w["mn"] * mn
            + w["ssmt"] * ssmt
            + w["exhaust"] * exh_c
            + w["reversal"] * rev_c
            + w["session"] * sess_c
            + w["news"] * news_c
        ) * float(event_penalty)

        out[f"ets_{sfx}"] = score.clip(0, 100)
        out[f"c_tcisd_{sfx}"] = tcisd * 100
        out[f"c_fib_{sfx}"] = fib * 100
        out[f"c_mn_{sfx}"] = mn * 100
        out[f"c_ssmt_{sfx}"] = ssmt * 100
        out[f"c_exh_{sfx}"] = exh_c * 100
        out[f"c_rev_{sfx}"] = rev_c * 100
        out[f"c_sess_{sfx}"] = sess_c * 100
        out[f"c_news_{sfx}"] = news_c * 100
    return out


# ----------------------------------------------------------------------------
# Trade construction
# ----------------------------------------------------------------------------


@dataclass
class Trade:
    ts: pd.Timestamp
    idx: int
    direction: int
    entry: float
    stop: float
    tp1: float
    tp2: float
    tp3: float
    score: float
    tier: str
    risk_atr: float
    rr: float
    size: float
    risk_usd: float
    components: Dict[str, float] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)
    bar_delay: int = 0

    def as_row(self) -> dict:
        return {
            "ts": self.ts,
            "dir": "LONG" if self.direction > 0 else "SHORT",
            "entry": round(self.entry, 2),
            "stop": round(self.stop, 2),
            "tp1": round(self.tp1, 2),
            "tp2": round(self.tp2, 2),
            "tp3": round(self.tp3, 2),
            "score": round(self.score, 1),
            "tier": self.tier,
            "R": round(self.risk_atr, 2),
            "rr": round(self.rr, 2),
            "size": round(self.size, 3),
            "risk_usd": round(self.risk_usd, 0),
        }


def tier_of(score: float, p: Params) -> str:
    if score >= p.tier_a:
        return "A"
    if score >= p.tier_b:
        return "B"
    if score >= p.min_conviction:
        return "C"
    return "-"


def construct_trade(
    df: pd.DataFrame,
    i: int,
    direction: int,
    score: float,
    panel: pd.DataFrame,
    legs: Dict[str, stx.Leg],
    p: Params,
    atr_value: float,
) -> Optional[Trade]:
    """Structure-based stop, Fibonacci extension targets, fractional sizing."""
    # Historical bars enter at the NEXT bar's open - entering on the signal bar's
    # own close is the classic backtest lie. On the live bar there is no next bar,
    # so the reference is the current close (a market order, right now).
    if i + 1 < len(df):
        entry_ref = float(df["open"].iloc[i + 1])
        entry_ts = df.index[i + 1]
        entry_idx = i + 1
    else:
        entry_ref = float(df["close"].iloc[i])
        entry_ts = df.index[i]
        entry_idx = i
    a = atr_value if atr_value and np.isfinite(atr_value) and atr_value > 0 else 1e-6
    notes: List[str] = []

    # structural anchor: the CISD invalidation level, else the recent swing
    lvl = panel["tcisd_level"].iloc[i]
    look = min(60, max(20, p.pivot_left * 4))
    seg = df.iloc[max(0, i - look): i + 1]
    if direction > 0:
        struct_stop = float(seg["low"].min())
        if np.isfinite(lvl):
            struct_stop = min(struct_stop, float(lvl) * 0.999)
    else:
        struct_stop = float(seg["high"].max())
        if np.isfinite(lvl):
            struct_stop = max(struct_stop, float(lvl) * 1.001)

    stop = struct_stop - direction * p.stop_buffer_atr * a
    risk = abs(entry_ref - stop)
    if risk < 0.25 * a:
        stop = entry_ref - direction * max(0.6 * a, risk)
        risk = abs(entry_ref - stop)
        notes.append("stop widened to minimum 0.6 ATR")
    if risk > 3.5 * a:
        stop = entry_ref - direction * 3.5 * a
        risk = 3.5 * a
        notes.append("stop capped at 3.5 ATR")

    # targets: R-multiple ladder, upgraded by Fibonacci extensions where valid
    tp1 = entry_ref + direction * p.tp1_r * risk
    tp2 = entry_ref + direction * p.tp2_r * risk
    tp3 = entry_ref + direction * p.tp3_r * risk
    leg = legs.get("primary")
    if leg is not None and leg.direction == direction and leg.size > 0:
        ext = leg.ext_price(1.272)
        if np.isfinite(ext) and (ext - entry_ref) * direction > 0:
            tp2 = float(ext)
            notes.append("TP2 = 1.272 Fibonacci extension")
        ext2 = leg.ext_price(1.618)
        if np.isfinite(ext2) and (ext2 - entry_ref) * direction > 0:
            tp3 = float(ext2)
            notes.append("TP3 = 1.618 Fibonacci extension")

    rr = abs(tp1 - entry_ref) / risk if risk > 0 else 0.0
    if rr < p.min_rr:
        tp1 = entry_ref + direction * p.min_rr * risk
        rr = p.min_rr
        notes.append(f"TP1 raised to satisfy min R:R {p.min_rr}")

    rev = float(panel["reversal_risk"].iloc[i])
    size_factor = float(np.clip(score / 100.0, 0.0, 1.0)) * float(np.clip(1 - rev, 0.1, 1.0))
    risk_pct = p.risk_per_trade_pct * (0.6 + 0.8 * size_factor)
    risk_pct = min(risk_pct, p.max_risk_pct)
    risk_usd = p.equity * risk_pct
    dollar_risk_per_lot = risk * p.contract_size
    size = risk_usd / dollar_risk_per_lot if dollar_risk_per_lot > 0 else 0.0

    comp = {
        "tCISD": float(panel[f"tcisd_{'long' if direction>0 else 'short'}"].iloc[i]),
        "Fibonacci": float(panel[f"fib_{'long' if direction>0 else 'short'}"].iloc[i]),
        "MarketNeutral": float(panel[f"mn_{'long' if direction>0 else 'short'}"].iloc[i]) * 100,
        "SSMT": float(panel[f"ssmt_{'long' if direction>0 else 'short'}"].iloc[i]) * 100,
        "Exhaustion": float(panel["exh_up" if direction > 0 else "exh_dn"].iloc[i]),
        "ReversalRisk": rev * 100,
        "Session": float(panel["session_w"].iloc[i]) * 100,
    }

    return Trade(
        ts=entry_ts, idx=entry_idx, direction=direction,
        entry=float(entry_ref), stop=float(stop), tp1=float(tp1), tp2=float(tp2),
        tp3=float(tp3), score=float(score), tier=tier_of(score, p),
        risk_atr=risk / a, rr=rr, size=float(size), risk_usd=float(risk_usd),
        components=comp, notes=notes,
    )


# ----------------------------------------------------------------------------
# Live ("now") signal
# ----------------------------------------------------------------------------


def current_signal(
    bundle: Dict[str, object],
    df: pd.DataFrame,
    p: Params,
    news_bias: float = 0.0,
    event_penalty: float = 1.0,
    live_price: Optional[float] = None,
) -> Dict[str, object]:
    """Evaluate the latest bar and build the trade card."""
    panel: pd.DataFrame = bundle["panel"]
    scored = composite(panel, p, news_bias=news_bias, event_penalty=event_penalty)
    i = len(df) - 1

    long_s = float(scored["ets_long"].iloc[i])
    short_s = float(scored["ets_short"].iloc[i])
    direction = 1 if long_s >= short_s else -1
    score = max(long_s, short_s)
    sfx = "long" if direction > 0 else "short"

    gates = []
    tcisd_v = float(panel[f"tcisd_{sfx}"].iloc[i])
    if tcisd_v <= 0:
        gates.append(("Structural trigger (tCISD)", False, "no active CISD event - no delivery change to trade"))
    else:
        gates.append(("Structural trigger (tCISD)", True, f"{tcisd_v:.0f}/100, {int(panel['tcisd_bars_since'].iloc[i])} bars old"))

    rev = float(panel["reversal_risk"].iloc[i])
    base_rate = float((bundle.get("rev") or {}).get("base_rate", 0.35))
    gates.append((f"Reversal risk < {p.rev_gate:.0%}", rev < p.rev_gate,
                f"{rev:.0%} (base rate {base_rate:.0%})"))
    gates.append((f"Conviction >= {p.min_conviction}", score >= p.min_conviction, f"{score:.0f}/100"))
    rr_ok = True

    trade = None
    atr_v = float(panel["atr"].iloc[i])
    if tcisd_v > 0 and rev < p.rev_gate:
        trade = construct_trade(df, i, direction, score, panel, bundle["legs"], p, atr_v)
        if trade is not None:
            rr_ok = trade.rr >= p.min_rr
            gates.append((f"R:R >= {p.min_rr}", rr_ok, f"{trade.rr:.2f}"))
            if live_price:
                trade.entry = float(live_price)
                risk = abs(trade.entry - trade.stop)
                trade.risk_atr = risk / atr_v if atr_v else np.nan
                trade.tp1 = trade.entry + direction * p.tp1_r * risk
                trade.tp2 = trade.entry + direction * p.tp2_r * risk
                trade.tp3 = trade.entry + direction * p.tp3_r * risk
                trade.size = trade.risk_usd / (risk * p.contract_size) if risk > 0 else 0.0
    else:
        gates.append((f"R:R >= {p.min_rr}", False, "not evaluated"))

    all_pass = all(g[1] for g in gates)
    if trade is not None and not all_pass:
        trade = None

    return {
        "direction": direction,
        "score_long": long_s,
        "score_short": short_s,
        "score": score,
        "tier": tier_of(score, p) if (trade is not None) else "-",
        "trade": trade,
        "gates": gates,
        "components": {
            "tCISD": float(panel[f"tcisd_{sfx}"].iloc[i]),
            "Fibonacci": float(panel[f"fib_{sfx}"].iloc[i]),
            "MarketNeutral": float(panel[f"mn_{sfx}"].iloc[i]) * 100,
            "SSMT": float(panel[f"ssmt_{sfx}"].iloc[i]) * 100,
            "ExhaustionRisk": float(panel["exh_up" if direction > 0 else "exh_dn"].iloc[i]),
            "ReversalRisk": rev * 100,
            "Session": float(panel["session_w"].iloc[i]) * 100,
            "NewsAlignment": (0.5 + 0.5 * float(np.clip(news_bias, -1, 1)) * direction) * 100,
        },
        "scored": scored,
        "bar": df.index[i],
        "prevailing": float(panel["prevailing"].iloc[i]),
        "atr": atr_v,
    }
