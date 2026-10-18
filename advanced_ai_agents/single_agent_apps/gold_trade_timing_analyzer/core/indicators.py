"""
Core technical / statistical primitives.

Everything is written as a pure function over numpy arrays or pandas Series so
it can be unit tested without a UI, and every indicator that a model consumes is
computed with *only* information available at that bar (no look-ahead). Pivots
are marked at their confirmation bar, not their formation bar.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


# ----------------------------------------------------------------------------
# Moving averages / volatility
# ----------------------------------------------------------------------------


def ema(x: pd.Series, n: int) -> pd.Series:
    return x.ewm(span=n, adjust=False).mean()


def sma(x: pd.Series, n: int) -> pd.Series:
    return x.rolling(n).mean()


def rma(x: pd.Series, n: int) -> pd.Series:
    """Wilder's smoothing."""
    return x.ewm(alpha=1.0 / n, adjust=False).mean()


def true_range(df: pd.DataFrame) -> pd.Series:
    pc = df["close"].shift(1)
    return pd.concat(
        [df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1
    ).max(axis=1)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    return rma(true_range(df).fillna(df["high"] - df["low"]), n)


def realised_vol(close: pd.Series, n: int = 20, bars_per_year: float = 252 * 24) -> pd.Series:
    r = np.log(close).diff()
    return r.rolling(n).std() * math.sqrt(bars_per_year)


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up = rma(d.clip(lower=0), n)
    dn = rma((-d).clip(lower=0), n)
    rs = up / dn.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.fillna(50.0)


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    line = ema(close, fast) - ema(close, slow)
    sig = line.ewm(span=signal, adjust=False).mean()
    return pd.DataFrame({"macd": line, "signal": sig, "hist": line - sig})


def stoch(df: pd.DataFrame, n: int = 14, k: int = 3) -> pd.Series:
    hh = df["high"].rolling(n).max()
    ll = df["low"].rolling(n).min()
    return (100 * (df["close"] - ll) / (hh - ll).replace(0, np.nan)).rolling(k).mean()


def williams_r(df: pd.DataFrame, n: int = 14) -> pd.Series:
    hh = df["high"].rolling(n).max()
    ll = df["low"].rolling(n).min()
    return -100 * (hh - df["close"]) / (hh - ll).replace(0, np.nan)


def zscore(series: pd.Series, n: int) -> pd.Series:
    mu = series.rolling(n).mean()
    sd = series.rolling(n).std().replace(0, np.nan)
    return ((series - mu) / sd).replace([np.inf, -np.inf], np.nan)


def pct_rank(series: pd.Series, n: int) -> pd.Series:
    return series.rolling(n).apply(lambda v: (v[:-1] < v[-1]).mean() if len(v) > 1 else np.nan, raw=True)


# ----------------------------------------------------------------------------
# Structure
# ----------------------------------------------------------------------------


def pivot_indices(high: np.ndarray, low: np.ndarray, left: int = 5, right: int = 5
                  ) -> Tuple[List[int], List[int]]:
    """Fractal pivots. A pivot at bar i is only returned once bar i+right exists,
    i.e. the pivot is *confirmed* at i+right - no look-ahead in live usage."""
    n = len(high)
    ph, pl = [], []
    for i in range(left, n - right):
        seg_h = high[i - left: i + right + 1]
        seg_l = low[i - left: i + right + 1]
        if high[i] == seg_h.max() and np.sum(seg_h == high[i]) == 1:
            ph.append(i)
        if low[i] == seg_l.min() and np.sum(seg_l == low[i]) == 1:
            pl.append(i)
    return ph, pl


def swings(df: pd.DataFrame, left: int = 5, right: int = 5) -> pd.DataFrame:
    """Confirmed swing points with the bar at which they became known."""
    h, l = df["high"].values, df["low"].values
    ph, pl = pivot_indices(h, l, left, right)
    rows = [{"i": i, "ts": df.index[i], "kind": "H", "price": h[i], "known_at": i + right} for i in ph]
    rows += [{"i": i, "ts": df.index[i], "kind": "L", "price": l[i], "known_at": i + right} for i in pl]
    out = pd.DataFrame(rows)
    if not out.empty:
        out = out.sort_values("i").reset_index(drop=True)
    return out


def market_structure(sw: pd.DataFrame) -> str:
    """Classify the last confirmed swing sequence: up / down / range."""
    if len(sw) < 4:
        return "range"
    last = sw.tail(4)
    highs = last[last.kind == "H"]["price"].tolist()
    lows = last[last.kind == "L"]["price"].tolist()
    if len(highs) >= 2 and len(lows) >= 2:
        hh = highs[-1] > highs[-2]
        hl = lows[-1] > lows[-2]
        lh = highs[-1] < highs[-2]
        ll = lows[-1] < lows[-2]
        if hh and hl:
            return "up"
        if lh and ll:
            return "down"
    return "range"


# ----------------------------------------------------------------------------
# Candle anatomy
# ----------------------------------------------------------------------------


def candle_stats(df: pd.DataFrame, atr_series: pd.Series) -> pd.DataFrame:
    o, h, l, c = df["open"], df["high"], df["low"], df["close"]
    rng = (h - l).replace(0, np.nan)
    body = (c - o).abs()
    out = pd.DataFrame(index=df.index)
    out["range"] = rng
    out["body"] = body
    out["dir"] = np.sign(c - o).replace(0, 1.0)
    out["body_frac"] = (body / rng).fillna(0)
    out["body_atr"] = (body / atr_series).fillna(0)
    out["range_atr"] = (rng / atr_series).fillna(0)
    out["upper_wick"] = (h - np.maximum(o, c)) / atr_series
    out["lower_wick"] = (np.minimum(o, c) - l) / atr_series
    out["close_pos"] = ((c - l) / rng).fillna(0.5)
    return out


# ----------------------------------------------------------------------------
# Statistics
# ----------------------------------------------------------------------------


def _norm_sf(x: float) -> float:
    """Upper tail of the standard normal."""
    return 0.5 * math.erfc(x / math.sqrt(2.0))


def spearman(x: np.ndarray, y: np.ndarray) -> Tuple[float, float]:
    """Rank correlation and an approximate two-sided p-value."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    m = np.isfinite(x) & np.isfinite(y)
    x, y = x[m], y[m]
    n = len(x)
    if n < 5:
        return np.nan, np.nan
    rx = np.array(pd.Series(x).rank().values, dtype=float)
    ry = np.array(pd.Series(y).rank().values, dtype=float)
    rx = rx - rx.mean()
    ry = ry - ry.mean()
    den = np.sqrt((rx**2).sum() * (ry**2).sum())
    r = float((rx * ry).sum() / den) if den > 0 else np.nan
    if not np.isfinite(r) or abs(r) >= 0.999999:
        return r, 0.0
    t = r * math.sqrt((n - 2) / (1 - r**2))
    return r, float(2 * _norm_sf(abs(t)))


def pearson(x: np.ndarray, y: np.ndarray) -> float:
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    m = np.isfinite(x) & np.isfinite(y)
    if m.sum() < 5:
        return np.nan
    return float(np.corrcoef(x[m], y[m])[0, 1])


def adf_stat(series: pd.Series, lags: int = 1) -> Tuple[float, float]:
    """Augmented Dickey-Fuller t-statistic on the lag coefficient.

    Implemented directly so the engine has no hard statsmodels dependency.
    Critical values (MacKinnon, no constant, n->inf): 1% -2.58, 5% -1.95,
    10% -1.62. We report the statistic and an approximate p-value band.
    """
    s = pd.Series(series).dropna()
    if len(s) < 30 + lags:
        return np.nan, np.nan
    y = s.diff().dropna().values
    X = [s.shift(1).dropna().values[len(s) - len(y) - 1:][-len(y):]]
    # align properly
    d = pd.DataFrame({"y": s.diff(), "yl": s.shift(1)})
    for k in range(1, lags + 1):
        d[f"d{k}"] = s.diff().shift(k)
    d = d.dropna()
    if len(d) < 30:
        return np.nan, np.nan
    Y = d["y"].values
    Xm = np.column_stack([d["yl"].values] + [d[f"d{k}"].values for k in range(1, lags + 1)])
    Xm = np.column_stack([Xm, np.ones(len(Xm))])
    beta, *_ = np.linalg.lstsq(Xm, Y, rcond=None)
    resid = Y - Xm @ beta
    dof = len(Y) - Xm.shape[1]
    s2 = float(resid @ resid / dof)
    if s2 <= 0:
        return np.nan, np.nan
    cov = s2 * np.linalg.pinv(Xm.T @ Xm)
    se = math.sqrt(max(cov[0, 0], 1e-18))
    t = beta[0] / se
    p = 2 * _norm_sf(abs(t))  # conservative approximation
    return float(t), float(p)


def ou_halflife(series: pd.Series) -> Tuple[float, float]:
    """AR(1) half-life in bars plus the AR coefficient. NaN if not estimable."""
    s = pd.Series(series).dropna()
    if len(s) < 40:
        return np.nan, np.nan
    y = s.diff().dropna().values
    x = s.shift(1).dropna().values[-len(y):]
    if len(y) < 30:
        return np.nan, np.nan
    X = np.column_stack([x, np.ones(len(x))])
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    phi = float(beta[0])
    if phi >= 0 or (1 + phi) <= 0:
        return (np.inf if phi >= 0 else np.nan), phi
    return float(-math.log(2) / math.log(1 + phi)), phi


def hurst(series: pd.Series, max_lag: int = 40) -> float:
    """Hurst exponent via the variance-of-log-differences (rescaled) estimator.

    H < 0.5 -> mean reverting, H ~ 0.5 -> random walk, H > 0.5 -> trending.
    """
    s = pd.Series(series).dropna().values
    if len(s) < 100:
        return np.nan
    lags = np.unique(np.logspace(0.5, math.log10(min(max_lag, len(s) // 4)), 12).astype(int))
    tau = []
    for lag in lags:
        if lag < 2:
            continue
        d = np.log(s[lag:] / s[:-lag]) if np.all(s > 0) else s[lag:] - s[:-lag]
        tau.append(np.std(d))
    tau = np.array(tau)
    lags = np.array([l for l in lags if l >= 2])
    good = np.isfinite(tau) & (tau > 0)
    if good.sum() < 4:
        return np.nan
    slope = np.polyfit(np.log(lags[good]), np.log(tau[good]), 1)[0]
    return float(np.clip(slope, 0.0, 1.0)) if np.isfinite(slope) else np.nan


def rolling_beta(y: pd.Series, X: pd.DataFrame, window: int = 250, ridge: float = 1e-3):
    """Rolling ridge regression. Returns (beta DataFrame, fitted, residual)."""
    idx = y.index
    Yv = y.values.astype(float)
    Xv = X.values.astype(float)
    n, k = Xv.shape
    betas = np.full((n, k), np.nan)
    fitted = np.full(n, np.nan)
    R = ridge * np.eye(k)
    for i in range(window, n):
        yw = Yv[i - window: i]
        xw = Xv[i - window: i]
        m = np.isfinite(yw) & np.isfinite(xw).all(axis=1)
        if m.sum() < max(30, k * 5):
            continue
        G = xw[m].T @ xw[m]
        # scale-aware ridge: shrink relative to the average diagonal of X'X so
        # the penalty behaves identically whether returns are in bp or percent
        A = G + ridge * np.eye(k) * (np.trace(G) / k + 1e-18)
        b = xw[m].T @ yw[m]
        try:
            beta = np.linalg.solve(A, b)
        except np.linalg.LinAlgError:
            continue
        betas[i] = beta
        fitted[i] = float(np.dot(Xv[i], beta)) if np.all(np.isfinite(Xv[i])) else np.nan
    bdf = pd.DataFrame(betas, index=idx, columns=[f"b_{c}" for c in X.columns])
    fit = pd.Series(fitted, index=idx)
    return bdf, fit, y - fit


def rls_beta(y: pd.Series, X: pd.DataFrame, forget: float = 0.995):
    """Recursive least squares with exponential forgetting -> time-varying beta."""
    Yv = y.values.astype(float)
    Xv = X.values.astype(float)
    n, k = Xv.shape
    P = np.eye(k) * 10.0
    beta = np.zeros(k)
    out = np.full((n, k), np.nan)
    for i in range(n):
        x, yt = Xv[i], Yv[i]
        if not (np.all(np.isfinite(x)) and np.isfinite(yt)):
            continue
        P = P / forget
        denom = forget + float(x @ P @ x)
        K = (P @ x) / denom
        err = yt - float(x @ beta)
        beta = beta + K * err
        P = P - np.outer(K, x @ P)
        out[i] = beta
    return pd.DataFrame(out, index=y.index, columns=[f"b_{c}" for c in X.columns])


def logistic_fit(X: np.ndarray, y: np.ndarray, l2: float = 1e-2, iters: int = 60) -> np.ndarray:
    """L2-regularised logistic regression via Newton-IRLS. No sklearn needed.

    X must already include an intercept column and be standardised.
    """
    n, k = X.shape
    beta = np.zeros(k)
    for _ in range(iters):
        z = X @ beta
        p = 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))
        w = np.clip(p * (1 - p), 1e-6, None)
        grad = X.T @ (y - p) - l2 * beta
        H = -(X.T * w) @ X - l2 * np.eye(k)
        try:
            step = np.linalg.solve(H, -grad)
        except np.linalg.LinAlgError:
            step = -grad / (np.abs(H).sum() + 1e-9)
        beta = beta + step
        if np.max(np.abs(step)) < 1e-7:
            break
    return beta


def roc_auc(y: np.ndarray, p: np.ndarray) -> float:
    y = np.asarray(y, float)
    p = np.asarray(p, float)
    m = np.isfinite(p) & np.isfinite(y)
    y, p = y[m], p[m]
    n1, n0 = y.sum(), (1 - y).sum()
    if n1 == 0 or n0 == 0:
        return np.nan
    order = np.argsort(p)
    ranks = np.empty(len(p), float)
    ranks[order] = np.arange(1, len(p) + 1)
    # average ranks for ties
    df = pd.DataFrame({"p": p, "r": ranks})
    ranks = df.groupby("p")["r"].transform("mean").values
    return float((ranks[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


# ----------------------------------------------------------------------------
# Session clock
# ----------------------------------------------------------------------------


def session_of(ts: pd.Timestamp) -> str:
    from .config import SESSIONS

    h = int(pd.Timestamp(ts).hour)
    for name, (a, b) in SESSIONS.items():
        if a <= h < b:
            return name
    return "Late"


def session_series(index: pd.DatetimeIndex) -> pd.Series:
    return pd.Series([session_of(t) for t in index], index=index, name="session")
