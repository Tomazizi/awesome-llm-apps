"""
Intermarket layer: SSMT divergence and the market-neutral statistical models.

------------------------------------------------------------------------------
SSMT - Statistical Smart-Money Technique
------------------------------------------------------------------------------
"SMT" (Smart Money Technique) in the ICT lexicon is an intermarket divergence:
gold prints a new high while silver - its closest correlated sibling - fails to
confirm, which usually means the move was engineered to run stops rather than to
continue. This engine makes the idea *statistical*:

  * swings are located on both series over a common lookback;
  * each leg is converted to a z-score of that asset's own returns, so "made a
    new high" is judged against the asset's own volatility, not the eye;
  * every proxy is weighted by its rolling correlation with gold (Fisher-tested),
    so uncorrelated assets cannot vote;
  * a continuousised form is computed as the z-score of the beta-hedged spread
      s_t = r_gold - beta * r_proxy
    which is the market-neutral measure of gold's *relative* strength.

------------------------------------------------------------------------------
Market-neutral models
------------------------------------------------------------------------------
Two complementary specifications, both beta-neutralised against the driver
basket (USD, real yields, risk appetite, precious complex, reflation):

  1. RETURN model  r_gold,t = a + B' F_t + e_t
     e_t is the idiosyncratic gold return - the part no driver explains.

  2. LEVEL model   log(P_gold,t) = a + B' G_t + u_t
     u_t is the mispricing vs the driver-implied fair value (the classic
     gold fair-value framework).

Both residuals are z-scored and tested (ADF, OU half-life, Hurst). The Hurst
exponent decides whether the residual should be *faded* (mean reverting) or
*followed* (trending) instead of hard-coding a mean-reversion assumption.
Betas are also estimated recursively (RLS with forgetting) so the hedge ratio
adapts instead of breaking on regime change.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from . import indicators as ind
from .config import Params, FACTORS, DERIVED


# ----------------------------------------------------------------------------
# Driver matrix
# ----------------------------------------------------------------------------


def driver_matrix(prices: pd.DataFrame) -> pd.DataFrame:
    """Wide price frame -> driver frame (levels) including derived series."""
    out = pd.DataFrame(index=prices.index)
    for a in prices.columns:
        out[a] = prices[a]
    for name, (num, den) in DERIVED.items():
        if num in prices.columns and den in prices.columns:
            # rises when nominal yields outrun inflation-linked yields
            out[name] = np.log(prices[num] / prices[den])
    return out


def factor_returns(prices: pd.DataFrame) -> pd.DataFrame:
    dm = driver_matrix(prices)
    r = np.log(dm).diff()
    r = r.replace([np.inf, -np.inf], np.nan)
    return r


def available_factors(prices: pd.DataFrame, target: str = "XAUUSD") -> List[Tuple[str, int]]:
    """FACTORS entries that can actually be built from the loaded universe.

    A derived factor (e.g. the real-yield proxy RY = log(IEF/TIP)) only counts if
    *both* of its inputs were fetched.
    """
    present = set(prices.columns)
    present |= {k for k, (n, d) in DERIVED.items() if n in present and d in present}
    out = []
    for name, sign in FACTORS:
        if name == target:
            continue
        if name in present:
            out.append((name, sign))
    return out


# ----------------------------------------------------------------------------
# SSMT
# ----------------------------------------------------------------------------


def _last_two_swings(series: pd.DataFrame, lookback: int, left: int, right: int, kind: str):
    df = series.tail(lookback + left + right)
    sw = ind.swings(df, left, right)
    sw = sw[sw.kind == kind]
    if len(sw) < 2:
        return None
    return sw.iloc[-2], sw.iloc[-1]


def ssmt_pair(
    target: pd.DataFrame,
    proxy: pd.DataFrame,
    p: Params,
    lookback: Optional[int] = None,
) -> Dict[str, float]:
    """Divergence stats between gold and one proxy, evaluated on the last bar."""
    look = lookback or p.ssmt_lookback
    res = {"divergence": 0.0, "conf": 0.0, "corr": np.nan, "corr_p": np.nan,
           "spread_z": np.nan, "detail": ""}

    rt = np.log(target["close"]).diff().dropna()
    rp = np.log(proxy["close"]).diff().dropna()
    joined = pd.concat([rt.rename("t"), rp.rename("p")], axis=1).dropna().tail(p.ssmt_z_window)
    if len(joined) < 30:
        return res

    corr = float(joined["t"].corr(joined["p"]))
    n = len(joined)
    res["corr"] = corr
    if np.isfinite(corr) and abs(corr) < 0.999:
        # Fisher z test for rho != 0
        zf = 0.5 * math.log((1 + corr) / (1 - corr)) * math.sqrt(n - 3)
        res["corr_p"] = 2 * 0.5 * math.erfc(abs(zf) / math.sqrt(2))

    # beta-hedged spread in return space
    beta = float(np.polyfit(joined["p"], joined["t"], 1)[0]) if joined["p"].std() > 0 else 0.0
    spread = joined["t"] - beta * joined["p"]
    sz = float((spread.iloc[-1] - spread.mean()) / (spread.std() or 1e-12))
    res["spread_z"] = sz

    # swing non-confirmation
    th = _last_two_swings(target, look, p.pivot_left, p.pivot_right, "H")
    tl = _last_two_swings(target, look, p.pivot_left, p.pivot_right, "L")
    ph = _last_two_swings(proxy, look, p.pivot_left, p.pivot_right, "H")
    pl = _last_two_swings(proxy, look, p.pivot_left, p.pivot_right, "L")

    div = 0.0
    conf = 0.0
    detail = ""
    if th is not None and ph is not None:
        t_hh = th[1].price > th[0].price
        p_lh = ph[1].price < ph[0].price
        if t_hh and p_lh:
            zt = float((th[1].price - th[0].price) / (target["close"].tail(p.ssmt_z_window).std() or 1e-9))
            zp = float((ph[1].price - ph[0].price) / (proxy["close"].tail(p.ssmt_z_window).std() or 1e-9))
            if abs(zt) + abs(zp) >= p.ssmt_z_div_threshold:
                div = -1.0
                conf = min(1.0, (abs(zt) + abs(zp)) / 3.0)
                detail = f"gold HH / proxy LH (z {zt:+.2f} vs {zp:+.2f}) -> bearish non-confirmation"
    if tl is not None and pl is not None and div == 0.0:
        t_ll = tl[1].price < tl[0].price
        p_hl = pl[1].price > pl[0].price
        if t_ll and p_hl:
            zt = float((tl[1].price - tl[0].price) / (target["close"].tail(p.ssmt_z_window).std() or 1e-9))
            zp = float((pl[1].price - pl[0].price) / (proxy["close"].tail(p.ssmt_z_window).std() or 1e-9))
            if abs(zt) + abs(zp) >= p.ssmt_z_div_threshold:
                div = +1.0
                conf = min(1.0, (abs(zt) + abs(zp)) / 3.0)
                detail = f"gold LL / proxy HL (z {zt:+.2f} vs {zp:+.2f}) -> bullish non-confirmation"

    res["divergence"] = div
    res["conf"] = conf
    res["detail"] = detail or "no structural non-confirmation"
    return res


def ssmt_aggregate(
    target: pd.DataFrame,
    proxies: Dict[str, pd.DataFrame],
    p: Params,
) -> Dict[str, object]:
    """Correlation-weighted SSMT across every available proxy.

    Returns signed divergence in [-1, 1] (positive = bullish for gold) plus the
    per-proxy breakdown and the continuous relative-strength reading.
    """
    rows = []
    num = 0.0
    den = 0.0
    spread_zs = []
    for name, pdf in proxies.items():
        if name == "XAUUSD" or pdf is None or len(pdf) < 60:
            continue
        st = ssmt_pair(target, pdf, p)
        c = st["corr"]
        if not np.isfinite(c):
            continue
        w = abs(c)
        if w < p.ssmt_min_abs_corr:
            continue
        num += w * st["divergence"] * max(0.3, st["conf"] if st["conf"] > 0 else 0.5)
        den += w
        if np.isfinite(st["spread_z"]):
            spread_zs.append(st["spread_z"])
        rows.append({"proxy": name, **st})

    raw = float(num / den) if den > 0 else 0.0
    raw = float(np.clip(raw, -1, 1))
    cont = float(np.tanh(np.mean(spread_zs) / 2.0)) if spread_zs else 0.0
    return {
        "ssmt_div": raw,
        "ssmt_rel_strength": cont,
        "ssmt_long": float(np.clip(0.5 + 0.5 * raw, 0, 1)),
        "ssmt_short": float(np.clip(0.5 - 0.5 * raw, 0, 1)),
        "breakdown": pd.DataFrame(rows),
    }


# ----------------------------------------------------------------------------
# Market-neutral models
# ----------------------------------------------------------------------------


def market_neutral(
    target: pd.DataFrame,
    prices: pd.DataFrame,
    p: Params,
) -> Dict[str, object]:
    """Return-space and level-space neutral models plus residual diagnostics."""
    out: Dict[str, object] = {
        "mn_long": None, "mn_short": None, "mn_z": None, "mn_fv_z": None,
        "halflife": np.nan, "hurst": np.nan, "adf_t": np.nan, "adf_p": np.nan,
        "r2": np.nan, "betas": {}, "rls": None, "resid": None, "fv_resid": None,
        "n_factors": 0, "hurst_lvl": np.nan, "halflife_lvl": np.nan,
    }

    facs = available_factors(prices)
    if not facs:
        n = len(target)
        out["mn_long"] = pd.Series(0.5, index=target.index)
        out["mn_short"] = pd.Series(0.5, index=target.index)
        out["mn_z"] = pd.Series(0.0, index=target.index)
        out["mn_fv_z"] = pd.Series(0.0, index=target.index)
        return out

    fmat = factor_returns(prices)[[f for f, _ in facs]].dropna(how="all")
    rt = np.log(target["close"]).diff()

    joined = pd.concat([rt.rename("y"), fmat], axis=1)
    joined = joined.dropna()
    if len(joined) < p.mn_min_obs:
        n = len(target)
        out["mn_long"] = pd.Series(0.5, index=target.index)
        out["mn_short"] = pd.Series(0.5, index=target.index)
        out["mn_z"] = pd.Series(0.0, index=target.index)
        out["mn_fv_z"] = pd.Series(0.0, index=target.index)
        return out

    y = joined["y"]
    X = joined[[f for f, _ in facs]]

    betas, fitted, resid = ind.rolling_beta(y, X, window=p.mn_window, ridge=p.mn_ridge)
    resid_aligned = resid.reindex(target.index)

    # The per-bar return residual is white noise by construction, so every
    # mean-reversion statistic has to be measured on the *cumulative* residual -
    # the part of the gold price path that no driver explains. That is the series
    # that can actually be stationary / trending.
    cum_resid = resid_aligned.fillna(0.0).cumsum()
    z = ind.zscore(cum_resid, p.mn_z_window)
    z_ret = ind.zscore(resid_aligned, p.mn_z_window)

    # level-space fair-value model
    lv = np.log(target["close"])
    lmat = driver_matrix(prices)[[f for f, _ in facs]]
    lj = pd.concat([lv.rename("y"), lmat], axis=1).dropna()
    fv_z = pd.Series(np.nan, index=target.index)
    fv_resid = None
    hl_lvl, hurst_lvl = np.nan, np.nan
    if len(lj) > p.mn_min_obs:
        b2, f2, r2 = ind.rolling_beta(lj["y"], lj[[f for f, _ in facs]],
                                      window=min(p.mn_window * 2, max(120, len(lj) // 3)),
                                      ridge=p.mn_ridge)
        fv_resid = r2.reindex(target.index)
        fv_z = ind.zscore(fv_resid, min(p.mn_z_window * 2, 250))
        hl_lvl, _ = ind.ou_halflife(fv_resid)
        hurst_lvl = ind.hurst(fv_resid, max_lag=60)

    hl, phi = ind.ou_halflife(cum_resid)
    hurst = ind.hurst(cum_resid, max_lag=60)
    adf_t, adf_p = ind.adf_stat(cum_resid, lags=1)

    ss_res = float(np.nanvar(resid_aligned.dropna()))
    ss_tot = float(np.nanvar(y.reindex(resid_aligned.index).dropna()))
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else np.nan

    rls = ind.rls_beta(y, X, forget=p.mn_rls_forget)
    last_b = betas.dropna().iloc[-1] if betas.dropna().shape[0] else pd.Series(dtype=float)

    # ---- blend mean-reversion vs momentum on the residual -------------------
    H = hurst if np.isfinite(hurst) else 0.5
    w_mr = float(np.clip(1.0 - (H - 0.35) / 0.25, 0.0, 1.0))
    strength = np.tanh(z / 2.0)
    strength_fv = np.tanh(fv_z / 2.0)
    sig = np.tanh((strength.abs() + strength_fv.abs()) / 1.5)

    mr_long = np.clip(0.5 - 0.5 * strength, 0, 1) * 0.6 + np.clip(0.5 - 0.5 * strength_fv, 0, 1) * 0.4
    mo_long = np.clip(0.5 + 0.5 * strength, 0, 1) * 0.6 + np.clip(0.5 + 0.5 * strength_fv, 0, 1) * 0.4
    mn_long = (w_mr * mr_long + (1 - w_mr) * mo_long)
    mn_long = 0.5 + (mn_long - 0.5) * sig          # shrink to neutral when |z| small
    mn_long = mn_long.fillna(0.5)
    mn_short = 1.0 - mn_long

    out.update(
        {
            "mn_long": mn_long.clip(0, 1),
            "mn_short": mn_short.clip(0, 1),
            "mn_z": z,
            "mn_ret_z": z_ret,
            "cum_resid": cum_resid,
            "mn_fv_z": fv_z,
            "halflife": hl,
            "hurst": hurst,
            "adf_t": adf_t,
            "adf_p": adf_p,
            "r2": r2,
            "betas": {k: float(last_b.get(f"b_{k}", np.nan)) for k, _ in facs},
            "rls": rls,
            "resid": resid_aligned,
            "fv_resid": fv_resid,
            "n_factors": len(facs),
            "hurst_lvl": hurst_lvl,
            "halflife_lvl": hl_lvl,
            "w_meanrev": w_mr,
            "phi": phi,
            "factors": [f for f, _ in facs],
        }
    )
    return out


def relative_value(target: pd.DataFrame, other: pd.DataFrame, window: int = 250) -> Dict[str, float]:
    """Cointegration-flavoured diagnostics for a gold vs proxy spread."""
    a = np.log(target["close"])
    b = np.log(other["close"])
    j = pd.concat([a, b], axis=1).dropna()
    if len(j) < 60:
        return {}
    beta = float(np.polyfit(j.iloc[:, 1], j.iloc[:, 0], 1)[0])
    spread = j.iloc[:, 0] - beta * j.iloc[:, 1]
    z = float((spread.iloc[-1] - spread.tail(window).mean()) / (spread.tail(window).std() or 1e-12))
    hl, _ = ind.ou_halflife(spread)
    t, pv = ind.adf_stat(spread, lags=1)
    return {"beta": beta, "z": z, "halflife": hl, "adf_t": t, "adf_p": pv,
            "corr": float(j.iloc[:, 0].tail(window).corr(j.iloc[:, 1].tail(window)))}
