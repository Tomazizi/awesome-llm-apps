"""
Reversal risk, exhaustion and divergence.

------------------------------------------------------------------------------
Divergence
------------------------------------------------------------------------------
Regular divergence (price HH + momentum LH) is a *warning*; hidden divergence
(price HL + momentum LL) is a continuation signature. Rather than eyeballing
pivot-to-pivot slopes, each candidate divergence is scored with a Spearman rank
correlation between price and momentum over the segment, so a "divergence" must
be statistically negative to count.

------------------------------------------------------------------------------
Exhaustion
------------------------------------------------------------------------------
Eight independent symptoms of a climactic move are summed into a 0-100 index:
climax range + volume, consecutive-close stretch, ATR expansion with a
decelerating slope, momentum deceleration, wick rejection, stacked unfilled
imbalances (three pushes), overbought pinning, and a detected divergence.

------------------------------------------------------------------------------
Reversal risk (statistical model)
------------------------------------------------------------------------------
A walk-forward L2 logistic regression estimates

    P( price trades >= k*ATR against the prevailing move within h bars )

trained on an expanding window and refit every `rev_refit_every` bars, with
strictly causal features (stretch z-score, RSI, ATR expansion, momentum,
distance from the HTF mean, exhaustion, divergence, realised-vol percentile,
session, wick geometry, time-in-move). Out-of-sample AUC is reported so the
number can be judged rather than trusted.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from . import indicators as ind
from .config import Params


# ----------------------------------------------------------------------------
# Divergence
# ----------------------------------------------------------------------------


def _swing_index(sw: pd.DataFrame, kind: str) -> Tuple[np.ndarray, np.ndarray]:
    """(known_at, i) arrays for one swing kind, sorted by formation bar."""
    s = sw[sw.kind == kind].sort_values("i")
    return s["known_at"].values.astype(int), s["i"].values.astype(int)


def _last_two_known(known: np.ndarray, idx: np.ndarray, j: int, lookback: int):
    """Last two swings of this kind that were *already confirmed* at bar j."""
    k = int(np.searchsorted(known, j, side="right"))
    if k < 2:
        return None
    a = idx[:k]
    ok = np.flatnonzero(a >= j - lookback)
    if len(ok) < 2:
        return None
    return int(a[ok[-2]]), int(a[ok[-1]])


def divergence_frame(df: pd.DataFrame, p: Params,
                     swings: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """Per-bar divergence readings from RSI, MACD histogram and the stochastic.

    Swings are located once for the whole series and then filtered by their
    confirmation bar, which is both much faster and exactly equivalent to
    recomputing pivots on a growing window (a pivot at bar i is only ever known
    at bar i + right).
    """
    n = len(df)
    rsi_s = ind.rsi(df["close"], p.rsi_period)
    mac = ind.macd(df["close"])
    st = ind.stoch(df, 14, 3)
    atr_s = ind.atr(df, p.atr_period)

    bull = np.zeros(n)
    bear = np.zeros(n)
    rho = np.full(n, np.nan)
    pv = np.full(n, np.nan)

    mom = {"rsi": rsi_s, "macd": mac["hist"], "stoch": st}
    sw = swings if swings is not None else ind.swings(df, p.pivot_left, p.pivot_right)
    hk, hi_ = _swing_index(sw, "H")
    lk, li_ = _swing_index(sw, "L")
    start = 1

    for j in range(start, n):
        ph = _last_two_known(hk, hi_, j, p.div_swing_lookback)
        pl = _last_two_known(lk, li_, j, p.div_swing_lookback)
        score_bear = 0.0
        score_bull = 0.0
        best_rho, best_p = np.nan, np.nan
        a_atr = float(atr_s.iloc[j]) or 1.0

        for name, m in mom.items():
            if ph is not None:
                i0, i1 = ph
                dp = df["high"].iloc[i1] - df["high"].iloc[i0]
                if dp / a_atr >= p.div_min_price_move_atr:
                    dm = float(m.iloc[i1] - m.iloc[i0])
                    if dm < 0:
                        r, pv_ = ind.spearman(
                            df["high"].iloc[i0: i1 + 1].values, m.iloc[i0: i1 + 1].values
                        )
                        if np.isfinite(r) and r <= p.div_spearman_threshold and pv_ <= p.div_max_pvalue:
                            score_bear += 1.0 / 3.0
                            best_rho = r if not np.isfinite(best_rho) else min(best_rho, r)
                            best_p = pv_ if not np.isfinite(best_p) else max(best_p, pv_)
            if pl is not None:
                i0, i1 = pl
                dp = df["low"].iloc[i0] - df["low"].iloc[i1]
                if dp / a_atr >= p.div_min_price_move_atr:
                    dm = float(m.iloc[i1] - m.iloc[i0])
                    if dm > 0:
                        r, pv_ = ind.spearman(
                            df["low"].iloc[i0: i1 + 1].values, m.iloc[i0: i1 + 1].values
                        )
                        if np.isfinite(r) and r <= p.div_spearman_threshold and pv_ <= p.div_max_pvalue:
                            score_bull += 1.0 / 3.0
                            best_rho = r if not np.isfinite(best_rho) else min(best_rho, r)
                            best_p = pv_ if not np.isfinite(best_p) else max(best_p, pv_)

        bear[j] = float(np.clip(score_bear, 0, 1))
        bull[j] = float(np.clip(score_bull, 0, 1))
        rho[j] = best_rho
        pv[j] = best_p

    return pd.DataFrame(
        {"div_bear": bear, "div_bull": bull, "div_rho": rho, "div_p": pv,
         "rsi": rsi_s, "stoch": st, "macd_hist": mac["hist"]},
        index=df.index,
    )


# ----------------------------------------------------------------------------
# Exhaustion
# ----------------------------------------------------------------------------


def exhaustion_frame(
    df: pd.DataFrame,
    p: Params,
    div: Optional[pd.DataFrame] = None,
    fvgs: Optional[List] = None,
) -> pd.DataFrame:
    """0-100 exhaustion of the prevailing move, split by direction."""
    n = len(df)
    atr14 = ind.atr(df, p.atr_period)
    atr100 = ind.atr(df, min(100, max(30, n // 6)))
    cs = ind.candle_stats(df, atr14)
    close = df["close"]
    rng = cs["range"]
    vol = df["volume"] if "volume" in df else pd.Series(0.0, index=df.index)
    vol_z = ind.zscore(vol.rolling(20).mean(), 100)
    rsi_s = ind.rsi(close, p.rsi_period)
    st = ind.stoch(df, 14, 3)
    ema50 = ind.ema(close, p.htf_bias_ema)
    slope = close.diff(5)

    up = np.zeros(n)
    dn = np.zeros(n)

    # stacked unfilled imbalances in each direction
    stack_up = np.zeros(n)
    stack_dn = np.zeros(n)
    if fvgs:
        for g in fvgs:
            if g.filled_at is None or g.filled_at > g.i:
                end = g.filled_at if g.filled_at is not None else n
                seg = slice(g.i, min(end, n))
                if g.direction > 0:
                    stack_up[seg] += 1
                else:
                    stack_dn[seg] += 1

    atr_ratio = (atr14 / atr100.replace(0, np.nan)).fillna(1.0)
    consec_up = (np.sign(close.diff()) > 0).rolling(p.exhaustion_stretch_bars).sum()
    consec_dn = (np.sign(close.diff()) < 0).rolling(p.exhaustion_stretch_bars).sum()

    for j in range(1, n):
        a = float(atr14.iloc[j]) or 1e-9
        recent = slice(max(0, j - 3), j + 1)
        # 1. climactic range + volume
        climax_up = (
            (rng.iloc[recent] >= p.exhaustion_climax_range_atr * a).any()
            and float(cs["close_pos"].iloc[j]) >= 0.75
            and (float(vol_z.iloc[j]) if np.isfinite(vol_z.iloc[j]) else 0.0) >= p.exhaustion_vol_z
        )
        climax_dn = (
            (rng.iloc[recent] >= p.exhaustion_climax_range_atr * a).any()
            and float(cs["close_pos"].iloc[j]) <= 0.25
            and (float(vol_z.iloc[j]) if np.isfinite(vol_z.iloc[j]) else 0.0) >= p.exhaustion_vol_z
        )
        # 2. consecutive-close stretch
        stretch_up = float(consec_up.iloc[j] or 0) >= p.exhaustion_stretch_bars - 1
        stretch_dn = float(consec_dn.iloc[j] or 0) >= p.exhaustion_stretch_bars - 1
        # 3. volatility expansion
        vol_exp = float(atr_ratio.iloc[j] or 1.0) >= p.exhaustion_atr_expand
        # 4. momentum deceleration while still extending
        decel_up = bool(float(slope.iloc[j]) > 0 and float(slope.iloc[j]) < float(slope.iloc[j - 1]))
        decel_dn = bool(float(slope.iloc[j]) < 0 and float(slope.iloc[j]) > float(slope.iloc[j - 1]))
        # 5. wick rejection at the extreme
        wick_up = float(cs["upper_wick"].iloc[j] or 0) >= 0.5
        wick_dn = float(cs["lower_wick"].iloc[j] or 0) >= 0.5
        # 6. three pushes (stacked unfilled imbalances)
        pushes_up = stack_up[j] >= 3
        pushes_dn = stack_dn[j] >= 3
        # 7. oscillator pinning
        pin_up = float(st.iloc[j] if np.isfinite(st.iloc[j]) else 50) >= 90 or float(rsi_s.iloc[j]) >= 75
        pin_dn = float(st.iloc[j] if np.isfinite(st.iloc[j]) else 50) <= 10 or float(rsi_s.iloc[j]) <= 25
        # 8. divergence
        dv_up = float(div["div_bear"].iloc[j]) if div is not None else 0.0
        dv_dn = float(div["div_bull"].iloc[j]) if div is not None else 0.0
        # 9. extension from the mean
        ext = (float(close.iloc[j]) - float(ema50.iloc[j])) / a if np.isfinite(ema50.iloc[j]) else 0.0
        ext_up = 1.0 if ext >= 2.0 else (0.5 if ext >= 1.0 else 0.0)
        ext_dn = 1.0 if ext <= -2.0 else (0.5 if ext <= -1.0 else 0.0)

        w = {
            "climax": 0.16, "stretch": 0.12, "volexp": 0.08, "decel": 0.10,
            "wick": 0.10, "pushes": 0.10, "pin": 0.12, "div": 0.14, "ext": 0.08,
        }
        su = (
            w["climax"] * climax_up + w["stretch"] * stretch_up + w["volexp"] * (vol_exp and decel_up)
            + w["decel"] * decel_up + w["wick"] * wick_up + w["pushes"] * pushes_up
            + w["pin"] * pin_up + w["div"] * dv_up + w["ext"] * ext_up
        )
        sd = (
            w["climax"] * climax_dn + w["stretch"] * stretch_dn + w["volexp"] * (vol_exp and decel_dn)
            + w["decel"] * decel_dn + w["wick"] * wick_dn + w["pushes"] * pushes_dn
            + w["pin"] * pin_dn + w["div"] * dv_dn + w["ext"] * ext_dn
        )
        up[j] = 100 * float(np.clip(su, 0, 1))
        dn[j] = 100 * float(np.clip(sd, 0, 1))

    return pd.DataFrame(
        {"exh_up": up, "exh_dn": dn, "atr_ratio": atr_ratio,
         "stack_fvg_up": stack_up, "stack_fvg_dn": stack_dn},
        index=df.index,
    )


# ----------------------------------------------------------------------------
# Reversal risk
# ----------------------------------------------------------------------------


def _features(df: pd.DataFrame, p: Params, div: pd.DataFrame, exh: pd.DataFrame) -> pd.DataFrame:
    close = df["close"]
    atr14 = ind.atr(df, p.atr_period)
    atr100 = ind.atr(df, min(100, max(30, len(df) // 6)))
    ema50 = ind.ema(close, p.htf_bias_ema)
    ema200 = ind.ema(close, min(200, max(60, len(df) // 5)))
    rsi_s = ind.rsi(close, p.rsi_period)
    rv = ind.realised_vol(close, 20)
    cs = ind.candle_stats(df, atr14)
    sess = ind.session_series(df.index)
    direction = np.sign(close.diff(20).fillna(0))

    stretch = (close - ema50) / atr14
    stretch_z = ind.zscore(stretch, p.rev_stretch_window)
    dist200 = ((close - ema200) / atr14)
    dist200_z = ind.zscore(dist200, p.rev_stretch_window)
    mom10 = close.diff(10) / atr14
    mom_z = ind.zscore(close.diff(20) / atr14, p.rev_stretch_window)
    vol_pct = ind.pct_rank(rv, p.rev_stretch_window)
    rsi_n = (rsi_s - 50) / 50.0
    atr_r = atr14 / atr100.replace(0, np.nan)
    wick = (cs["upper_wick"] - cs["lower_wick"])
    ex_signed = (exh["exh_up"] - exh["exh_dn"]) / 100.0
    div_signed = div["div_bear"] - div["div_bull"]

    f = pd.DataFrame(
        {
            "stretch_z": stretch_z,
            "dist200_z": dist200_z,
            "mom10": mom10,
            "mom_z": mom_z,
            "rsi": rsi_n,
            "atr_ratio": atr_r,
            "vol_pct": vol_pct,
            "wick": wick,
            "exh_signed": ex_signed,
            "div_signed": div_signed,
        },
        index=df.index,
    )
    f["sess_asia"] = (sess == "Asia").astype(float)
    f["sess_ny"] = (sess == "New York").astype(float)
    f["direction"] = direction
    f["exh_abs"] = (exh["exh_up"] + exh["exh_dn"]) / 100.0
    f["dir_x_stretch"] = direction * stretch_z
    f["dir_x_exh"] = direction * ex_signed
    return f.replace([np.inf, -np.inf], np.nan)


def reversal_labels(df: pd.DataFrame, p: Params) -> pd.Series:
    """1 if price trades >= k*ATR against the prevailing move within h bars."""
    close = df["close"].values
    h = df["high"].values
    l = df["low"].values
    atr = ind.atr(df, p.atr_period).values
    n = len(df)
    y = np.zeros(n)
    for t in range(n - p.rev_horizon):
        a = atr[t]
        if not np.isfinite(a) or a <= 0:
            continue
        d = np.sign(close[t] - close[max(0, t - 20)]) or 1.0
        fwd_h = h[t + 1: t + 1 + p.rev_horizon].max()
        fwd_l = l[t + 1: t + 1 + p.rev_horizon].min()
        adv = (close[t] - fwd_l) if d > 0 else (fwd_h - close[t])
        y[t] = 1.0 if adv >= p.rev_move_atr * a else 0.0
    y[n - p.rev_horizon:] = np.nan
    return pd.Series(y, index=df.index)


def reversal_model(
    df: pd.DataFrame, p: Params, div: pd.DataFrame, exh: pd.DataFrame
) -> Dict[str, object]:
    """Walk-forward expanding-window logistic model. Returns OOS probabilities."""
    n = len(df)
    F = _features(df, p, div, exh)
    cols = [c for c in F.columns if c != "direction"]
    y = reversal_labels(df, p)

    X_all = F[cols]
    prob = pd.Series(np.nan, index=df.index, name="reversal_risk")
    coefs: List[pd.Series] = []
    train_start = max(p.mn_min_obs, 200)
    next_fit = train_start
    beta = None
    mu = None
    sd = None

    for t in range(train_start, n):
        if beta is None or (t >= next_fit):
            lo = max(0, t - p.rev_train_window)
            seg = slice(lo, t - p.rev_horizon - 1)
            Xs = X_all.iloc[seg]
            ys = y.iloc[seg]
            m = Xs.notna().all(axis=1) & ys.notna()
            if m.sum() < 120:
                prob.iloc[t] = np.nan
                continue
            Xm = Xs[m].values
            ym = ys[m].values
            mu = Xm.mean(axis=0)
            sd = Xm.std(axis=0)
            sd[sd == 0] = 1.0
            Xz = (Xm - mu) / sd
            Xz = np.column_stack([np.ones(len(Xz)), Xz])
            try:
                beta = ind.logistic_fit(Xz, ym, l2=p.rev_l2)
            except Exception:
                beta = None
            next_fit = t + p.rev_refit_every
            coefs.append(pd.Series(beta, index=["intercept"] + cols))

        if beta is not None:
            xv = X_all.iloc[t].values.astype(float)
            if np.all(np.isfinite(xv)):
                xz = (xv - mu) / sd
                xz = np.concatenate([[1.0], xz])
                prob.iloc[t] = float(1.0 / (1.0 + np.exp(-float(np.clip(xz @ beta, -30, 30)))))

    oos = prob.dropna()
    yy = y.reindex(oos.index)
    auc = ind.roc_auc(yy.values, oos.values) if len(oos) > 100 else np.nan

    # heuristic fallback when the model has not produced enough samples yet
    base = float(np.nanmean(yy.values)) if len(yy) and np.isfinite(yy.values).any() else 0.35

    return {
        "prob": prob.ffill().fillna(base),
        "raw_prob": prob,
        "auc": auc,
        "base_rate": base,
        "n_oos": int(len(oos)),
        "coefs": pd.DataFrame(coefs).mean().sort_values() if coefs else pd.Series(dtype=float),
        "features": F,
        "label": y,
    }


def reversal_now(prob: pd.Series, direction: int, exh: pd.DataFrame, div: pd.DataFrame,
                 p: Params) -> Dict[str, float]:
    """Blend the model probability with the raw exhaustion/divergence read."""
    pr = float(prob.dropna().iloc[-1]) if prob.dropna().size else 0.35
    ex = float(exh["exh_up"].iloc[-1] if direction > 0 else exh["exh_dn"].iloc[-1])
    dv = float(div["div_bear"].iloc[-1] if direction > 0 else div["div_bull"].iloc[-1])
    # directional adjustment: exhaustion *against* our side raises our risk
    blended = 0.55 * pr + 0.30 * (ex / 100.0) + 0.15 * dv
    return {"reversal_risk": float(np.clip(blended, 0.02, 0.97)),
            "model_p": pr, "exhaustion": ex, "divergence": dv}
