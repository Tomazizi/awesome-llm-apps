"""
Backtest and - the point of the whole system - the *entry timing* analysis.

A strategy backtest tells you whether an edge exists. It does not tell you
whether you are entering at the right moment. This module measures the second
thing directly:

  MAE / MFE          maximum adverse / favourable excursion in R, and when each
                     occurred. A good entry shows small MAE and early MFE.
  entry efficiency   MFE / (MFE + |MAE|) - the share of the move you captured
                     versus the drawdown you tolerated to capture it.
  clean entry        reached +1R before -1R.
  delay curve        re-runs every signal entering k bars later (k = 0,1,2,3,4,
                     6,8,12) to find the empirically optimal entry lag. This is
                     the single most actionable output: it tells you whether to
                     take the CISD on the close, wait for the retrace into the
                     FVG, or skip it entirely.
  score calibration  deciles of the Entry Timing Score against realised R, plus
                     the Spearman information coefficient and its t-statistic.
  attribution        IC of each sub-model on its own, so we can see which of
                     tCISD / Fibonacci / market-neutral / SSMT / exhaustion
                     actually carries the alpha.

All of it is out-of-sample in the sense that the reversal model is fit on an
expanding window, and every signal is taken at the *next bar's open*.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from . import indicators as ind
from .signal_engine import construct_trade, composite, tier_of
from .config import Params


# ----------------------------------------------------------------------------
# Signal generation
# ----------------------------------------------------------------------------


def generate_signals(
    df: pd.DataFrame,
    bundle: Dict[str, object],
    p: Params,
    min_score: Optional[float] = None,
) -> List[dict]:
    """Walk bars, apply the gates, build a trade card wherever one qualifies."""
    min_score = p.min_conviction if min_score is None else min_score
    panel: pd.DataFrame = bundle["panel"]
    scored = composite(panel, p)
    n = len(df)
    out: List[dict] = []
    cooldown = 0
    for i in range(p.bt_warmup, n - 2):
        if cooldown > 0:
            cooldown -= 1
            continue
        long_s = float(scored["ets_long"].iloc[i])
        short_s = float(scored["ets_short"].iloc[i])
        direction = 1 if long_s >= short_s else -1
        score = max(long_s, short_s)
        if score < min_score:
            continue
        sfx = "long" if direction > 0 else "short"
        if float(panel[f"tcisd_{sfx}"].iloc[i]) <= 0:
            continue
        if float(panel["reversal_risk"].iloc[i]) >= 0.75:
            continue
        atr_v = float(panel["atr"].iloc[i])
        tr = construct_trade(df, i, direction, score, panel, bundle["legs"], p, atr_v)
        if tr is None:
            continue
        out.append(
            {
                "i": i,
                "trade": tr,
                "direction": direction,
                "score": score,
                "components": {
                    "tCISD": float(panel[f"tcisd_{sfx}"].iloc[i]),
                    "Fibonacci": float(panel[f"fib_{sfx}"].iloc[i]),
                    "MarketNeutral": float(panel[f"mn_{sfx}"].iloc[i]) * 100,
                    "SSMT": float(panel[f"ssmt_{sfx}"].iloc[i]) * 100,
                    "ExhaustionInv": 100.0 - float(panel["exh_up" if direction > 0 else "exh_dn"].iloc[i]),
                    "ReversalInv": (1.0 - float(panel["reversal_risk"].iloc[i])) * 100,
                    "Session": float(panel["session_w"].iloc[i]) * 100,
                },
                "session": str(panel["session"].iloc[i]),
                "hour": int(pd.Timestamp(df.index[i]).hour),
            }
        )
        cooldown = p.bt_cooldown
    return out


def gate_funnel(df: pd.DataFrame, bundle: Dict[str, object], p: Params,
                min_score: Optional[float] = None) -> pd.DataFrame:
    """How many bars survive each gate. Makes the filtering auditable."""
    min_score = p.min_conviction if min_score is None else min_score
    from .signal_engine import construct_trade

    panel: pd.DataFrame = bundle["panel"]
    scored = composite(panel, p)
    n = len(df)
    counts = {"Bars in sample": max(0, n - p.bt_warmup),
              "CISD trigger present": 0,
              "+ reversal risk < 75%": 0,
              f"+ conviction >= {min_score:.0f}": 0,
              "+ trade constructible": 0,
              f"+ R:R >= {p.min_rr}": 0}
    cooldown = 0
    for i in range(p.bt_warmup, n - 2):
        if cooldown > 0:
            cooldown -= 1
            continue
        ls = float(scored["ets_long"].iloc[i])
        ss = float(scored["ets_short"].iloc[i])
        direction = 1 if ls >= ss else -1
        score = max(ls, ss)
        sfx = "long" if direction > 0 else "short"
        if float(panel[f"tcisd_{sfx}"].iloc[i]) <= 0:
            continue
        counts["CISD trigger present"] += 1
        if float(panel["reversal_risk"].iloc[i]) >= 0.75:
            continue
        counts["+ reversal risk < 75%"] += 1
        if score < min_score:
            continue
        counts[f"+ conviction >= {min_score:.0f}"] += 1
        tr = construct_trade(df, i, direction, score, panel, bundle["legs"], p,
                             float(panel["atr"].iloc[i]))
        if tr is None:
            continue
        counts["+ trade constructible"] += 1
        if tr.rr < p.min_rr:
            continue
        counts[f"+ R:R >= {p.min_rr}"] += 1
        cooldown = p.bt_cooldown
    rows = []
    prev = None
    for k, v in counts.items():
        rows.append({"gate": k, "surviving": v,
                     "pct_of_bars": 100 * v / max(1, counts["Bars in sample"]),
                     "step_efficiency": (v / prev) if prev else None})
        if k != "Bars in sample":
            prev = max(prev or 0, v)
    return pd.DataFrame(rows)


def threshold_sensitivity(df: pd.DataFrame, bundle: Dict[str, object], p: Params,
                          thresholds=(45, 50, 55, 60, 65, 70)) -> pd.DataFrame:
    """Trade count / expectancy as the conviction threshold is raised."""
    rows = []
    for th in thresholds:
        sigs = generate_signals(df, bundle, p, min_score=th)
        tr = run_backtest(df, sigs, p)
        m = headline_metrics(tr, p)
        rows.append({
            "threshold": th,
            "signals": m.get("trades", 0),
            "win_rate": m.get("win_rate", np.nan),
            "expectancy_r": m.get("expectancy_r", np.nan),
            "profit_factor": m.get("profit_factor", np.nan),
            "sharpe_r": m.get("sharpe_r", np.nan),
            "clean_entry_rate": m.get("clean_entry_rate", np.nan),
        })
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------
# Trade simulation
# ----------------------------------------------------------------------------


@dataclass
class Result:
    r: float
    exit: str
    bars: int
    mae: float
    mfe: float
    mae_bar: int
    mfe_bar: int
    entry_eff: float
    clean: int
    path: Optional[np.ndarray] = None


def _simulate_one(
    o: np.ndarray, h: np.ndarray, l: np.ndarray, c: np.ndarray,
    start: int, direction: int, entry: float, stop: float,
    tp1: float, tp2: float, tp3: float, risk: float,
    p: Params, max_hold: int, cost_r: float,
) -> Result:
    n = len(c)
    end = min(start + max_hold, n - 1)
    remaining = 1.0
    realised = 0.0
    cur_stop = stop
    tp1_hit = tp2_hit = False
    mae = 0.0
    mfe = 0.0
    mae_bar = 0
    mfe_bar = 0
    path = []
    hit_tp1_at = None
    hit_stop_at = None

    for j in range(start, end + 1):
        if direction > 0:
            fav = (h[j] - entry) / risk
            adv = (l[j] - entry) / risk
            if l[j] <= cur_stop:
                hit_stop_at = j
                break
            if not tp1_hit and h[j] >= tp1:
                tp1_hit = True
                hit_tp1_at = j
                realised += 0.5 * ((tp1 - entry) / risk)
                remaining -= 0.5
                cur_stop = entry
            elif tp1_hit and not tp2_hit and h[j] >= tp2:
                tp2_hit = True
                realised += 0.3 * ((tp2 - entry) / risk)
                remaining -= 0.3
                cur_stop = tp1
            elif tp2_hit and h[j] >= tp3:
                realised += 0.2 * ((tp3 - entry) / risk)
                remaining = 0.0
                path.append(realised)
                return Result(realised - cost_r, "TP3", j - start + 1, mae, max(mfe, fav),
                              mae_bar, mfe_bar, 0.0, 0, np.array(path))
        else:
            fav = (entry - l[j]) / risk
            adv = (entry - h[j]) / risk
            if h[j] >= cur_stop:
                hit_stop_at = j
                break
            if not tp1_hit and l[j] <= tp1:
                tp1_hit = True
                hit_tp1_at = j
                realised += 0.5 * ((entry - tp1) / risk)
                remaining -= 0.5
                cur_stop = entry
            elif tp1_hit and not tp2_hit and l[j] <= tp2:
                tp2_hit = True
                realised += 0.3 * ((entry - tp2) / risk)
                remaining -= 0.3
                cur_stop = tp1
            elif tp2_hit and l[j] <= tp3:
                realised += 0.2 * ((entry - tp3) / risk)
                remaining = 0.0
                path.append(realised)
                return Result(realised - cost_r, "TP3", j - start + 1, mae, max(mfe, fav),
                              mae_bar, mfe_bar, 0.0, 0, np.array(path))

        if adv < mae:
            mae = adv
            mae_bar = j - start
        if fav > mfe:
            mfe = fav
            mfe_bar = j - start
        path.append(realised + remaining * ((c[j] - entry) / risk * direction))

    if hit_stop_at is not None:
        realised += remaining * ((cur_stop - entry) / risk * direction)
        r = realised - cost_r
        return Result(r, "STOP", hit_stop_at - start + 1, mae, mfe, mae_bar, mfe_bar, 0.0, 0,
                      np.array(path))

    # time exit
    last = end
    realised += remaining * ((c[last] - entry) / risk * direction)
    r = realised - cost_r
    eff = (mfe / (mfe + abs(mae))) if (mfe > 0 and (mfe + abs(mae)) > 0) else 0.0
    clean = 1 if (mfe >= 1.0 and (mae_bar == 0 or mfe_bar <= mae_bar or mae > -1.0)) else 0
    return Result(r, "TIME", last - start + 1, mae, mfe, mae_bar, mfe_bar, eff, clean,
                  np.array(path))


def run_backtest(
    df: pd.DataFrame, signals: List[dict], p: Params
) -> pd.DataFrame:
    o, h, l, c = (df[k].values for k in ("open", "high", "low", "close"))
    rows = []
    for s in signals:
        tr = s["trade"]
        start = tr.idx
        if start >= len(c) - 1:
            continue
        risk = abs(tr.entry - tr.stop)
        if risk <= 0:
            continue
        cost_r = (p.spread_usd + p.commission_usd) / risk
        res = _simulate_one(o, h, l, c, start, tr.direction, tr.entry, tr.stop,
                            tr.tp1, tr.tp2, tr.tp3, risk, p, p.bt_max_hold, cost_r)
        # net of spread already handled via cost_r
        r_usd = res.r * risk * p.contract_size * tr.size
        row = {
            "ts": tr.ts,
            "i": s["i"],
            "dir": "LONG" if tr.direction > 0 else "SHORT",
            "session": s["session"],
            "hour": s["hour"],
            "score": s["score"],
            "tier": tr.tier,
            "entry": tr.entry,
            "stop": tr.stop,
            "risk_atr": tr.risk_atr,
            "r": res.r,
            "pnl_usd": r_usd,
            "exit": res.exit,
            "bars": res.bars,
            "mae": res.mae,
            "mfe": res.mfe,
            "mae_bar": res.mae_bar,
            "mfe_bar": res.mfe_bar,
            "entry_eff": res.entry_eff if res.entry_eff else (
                (res.mfe / (res.mfe + abs(res.mae))) if (res.mfe > 0 and (res.mfe + abs(res.mae)) > 0) else 0.0
            ),
            "clean": 1 if (res.mfe >= 1.0 and res.mae > -1.0) else 0,
            "size": tr.size,
        }
        row.update({f"c_{k}": v for k, v in s["components"].items()})
        rows.append(row)

    out = pd.DataFrame(rows)
    if not out.empty:
        out["cum_r"] = out["r"].cumsum()
    return out


# ----------------------------------------------------------------------------
# Entry-delay experiment
# ----------------------------------------------------------------------------


def delay_curve(
    df: pd.DataFrame, signals: List[dict], p: Params, atr: Optional[pd.Series] = None
) -> pd.DataFrame:
    """Re-enter each signal k bars later and see what it does to expectancy.

    A delayed entry that has drifted to within a fraction of an ATR of the
    structural stop is discarded: the R-multiple would be dominated by a
    vanishing denominator rather than by the entry decision.
    """
    o, h, l, c = (df[k].values for k in ("open", "high", "low", "close"))
    atr_v = atr.values if atr is not None else np.full(len(c), np.nan)
    rows = []
    for k in p.bt_delay_grid:
        rs = []
        maes = []
        skips = 0
        for s in signals:
            tr = s["trade"]
            start = tr.idx + k
            if start >= len(c) - 1:
                continue
            entry = float(o[start])
            direction = tr.direction
            # keep the *structural* stop; re-derive targets from the new entry
            stop = tr.stop
            if (entry - stop) * direction <= 0:
                skips += 1
                continue          # price already through the invalidation
            risk = abs(entry - stop)
            a = atr_v[start] if np.isfinite(atr_v[start]) and atr_v[start] > 0 else np.nan
            if np.isfinite(a) and risk < 0.5 * a:
                skips += 1
                continue          # too close to invalidation to be a fair test
            tp1 = entry + direction * p.tp1_r * risk
            tp2 = entry + direction * p.tp2_r * risk
            tp3 = entry + direction * p.tp3_r * risk
            cost_r = (p.spread_usd + p.commission_usd) / risk
            res = _simulate_one(o, h, l, c, start, direction, entry, stop, tp1, tp2, tp3,
                                risk, p, p.bt_max_hold, cost_r)
            rs.append(res.r)
            maes.append(res.mae)
        if rs:
            rows.append(
                {
                    "delay_bars": k,
                    "n": len(rs),
                    "mean_r": float(np.mean(rs)),
                    "median_r": float(np.median(rs)),
                    "win_rate": float(np.mean(np.array(rs) > 0)),
                    "mean_mae_r": float(np.mean(maes)),
                    "skipped": skips,
                }
            )
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------
# Calibration / attribution
# ----------------------------------------------------------------------------


def _ic(x: np.ndarray, y: np.ndarray) -> Dict[str, float]:
    r, pv = ind.spearman(x, y)
    n = len(x)
    tstat = np.nan
    if np.isfinite(r) and n > 5 and abs(r) < 0.999:
        tstat = r * math.sqrt((n - 2) / (1 - r**2))
    return {"ic": r, "ic_t": tstat, "p": pv, "n": n}


def calibration(trades: pd.DataFrame, buckets: int = 5) -> pd.DataFrame:
    if trades.empty or trades["score"].nunique() < 3:
        return pd.DataFrame()
    try:
        q = pd.qcut(trades["score"], buckets, duplicates="drop")
    except Exception:
        return pd.DataFrame()
    g = trades.groupby(q, observed=True).agg(
        n=("r", "size"), mean_r=("r", "mean"), median_r=("r", "median"),
        win_rate=("r", lambda s: float((s > 0).mean())),
        mean_mae=("mae", "mean"), mean_mfe=("mfe", "mean"),
        clean_rate=("clean", "mean"),
    ).reset_index()
    g["bucket"] = g["score"].astype(str)
    g["score_lo"] = [iv.left for iv in g["score"]]
    g["score_hi"] = [iv.right for iv in g["score"]]
    return g.drop(columns=["score"])


def attribution(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()
    rows = []
    y = trades["r"].values.astype(float)
    rows.append({"model": "COMPOSITE ETS", **_ic(trades["score"].values.astype(float), y)})
    for c in [c for c in trades.columns if c.startswith("c_")]:
        rows.append({"model": c[2:], **_ic(trades[c].values.astype(float), y)})
    out = pd.DataFrame(rows)
    return out.sort_values("ic", key=lambda s: s.abs(), ascending=False)


def headline_metrics(trades: pd.DataFrame, p: Params, freq: str = "1h") -> Dict[str, float]:
    if trades.empty:
        return {}
    r = trades["r"].values
    n = len(r)
    wins = r[r > 0]
    losses = r[r <= 0]
    pf = wins.sum() / abs(losses.sum()) if losses.sum() != 0 else np.inf
    # R-based Sharpe, annualised by the signal frequency
    per_year = max(1.0, 252 * 24 / 1.0)
    try:
        from .config import BARS_PER_YEAR
        per_year = BARS_PER_YEAR.get(freq, 252 * 24)
    except Exception:
        pass
    trades_per_year = n / max(1.0, (trades["i"].max() - trades["i"].min()) / per_year) if n > 1 else n
    sharpe = (r.mean() / r.std() * math.sqrt(trades_per_year)) if r.std() > 0 else np.nan
    downside = r[r < 0].std()
    sortino = (r.mean() / downside * math.sqrt(trades_per_year)) if downside and downside > 0 else np.nan
    eq = np.cumsum(r)
    peak = np.maximum.accumulate(eq)
    dd = np.min(eq - peak) if len(eq) else 0.0
    return {
        "trades": n,
        "win_rate": float((r > 0).mean()),
        "expectancy_r": float(r.mean()),
        "median_r": float(np.median(r)),
        "profit_factor": float(pf),
        "best_r": float(r.max()),
        "worst_r": float(r.min()),
        "sharpe_r": float(sharpe) if np.isfinite(sharpe) else np.nan,
        "sortino_r": float(sortino) if np.isfinite(sortino) else np.nan,
        "max_dd_r": float(abs(dd)),
        "mean_mae_r": float(trades["mae"].mean()),
        "mean_mfe_r": float(trades["mfe"].mean()),
        "entry_efficiency": float(trades["entry_eff"].mean()),
        "clean_entry_rate": float(trades["clean"].mean()),
        "avg_bars_held": float(trades["bars"].mean()),
        "tp_rate": float((trades["exit"].isin(["TP2", "TP3"])).mean()),
        "stop_rate": float((trades["exit"] == "STOP").mean()),
    }


def equity_curve(trades: pd.DataFrame, p: Params) -> pd.Series:
    if trades.empty:
        return pd.Series(dtype=float)
    return trades.set_index("ts")["r"].cumsum()
