"""
Head-less tests for the analytic core (no Streamlit, no network).

Run with:  python -m pytest tests/test_engine.py -q
        or python tests/test_engine.py
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import Params  # noqa: E402
from core import indicators as ind  # noqa: E402
from core import structure as stx  # noqa: E402
from core import intermarket as imk  # noqa: E402
from core import risk as rsk  # noqa: E402
from core import signal_engine as se  # noqa: E402
from core import backtest as bt  # noqa: E402
from core.data_feeds import _synthetic  # noqa: E402


def make_universe(n: int = 1600, seed: int = 7):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    px = 2000 + np.cumsum(rng.standard_normal(n) * 3.0)
    df = pd.DataFrame(
        {
            "open": px + rng.standard_normal(n),
            "high": px + np.abs(rng.standard_normal(n)) * 4,
            "low": px - np.abs(rng.standard_normal(n)) * 4,
            "close": px,
            "volume": np.abs(rng.standard_normal(n)) * 1000 + 500,
        },
        index=idx,
    )
    prices = pd.DataFrame({"XAUUSD": df["close"]})
    # correlated driver basket
    r = np.log(prices["XAUUSD"]).diff().fillna(0)
    for name, sgn, vol in [("DXY", -1, 0.0009), ("XAGUSD", 1, 0.004), ("VIX", -1, 0.006),
                           ("SPX", -1, 0.0012), ("WTI", 1, 0.002), ("COPPER", 1, 0.0015),
                           ("IEF", -1, 0.0008), ("TIP", -1, 0.0007), ("US10Y", -1, 0.002)]:
        shock = sgn * r * (0.6 + 0.4 * rng.random()) + rng.standard_normal(n) * vol
        prices[name] = 100 * np.exp(np.cumsum(shock))
    return df, prices


def test_indicators_basic():
    df, _ = make_universe(400)
    a = ind.atr(df, 14)
    r = ind.rsi(df["close"], 14)
    assert a.dropna().min() > 0
    assert r.dropna().between(0, 100).all()
    ph, pl = ind.pivot_indices(df["high"].values, df["low"].values, 3, 3)
    assert len(ph) > 0 and len(pl) > 0
    sw = ind.swings(df, 3, 3)
    assert set(sw["kind"]) <= {"H", "L"}
    # no look-ahead: a pivot may never be known before it forms
    assert (sw["known_at"] >= sw["i"]).all()


def test_statistics():
    rng = np.random.default_rng(0)
    x = np.cumsum(rng.standard_normal(400))
    hl, phi = ind.ou_halflife(pd.Series(x))
    assert not np.isfinite(hl) or hl > 0
    # mean reverting series must have a finite half life
    mr = pd.Series(np.sin(np.arange(800) / 12.0) + rng.standard_normal(800) * 0.15)
    hl2, phi2 = ind.ou_halflife(mr)
    assert np.isfinite(hl2) and phi2 < 0
    h = ind.hurst(mr)
    assert np.isfinite(h)
    t, p = ind.adf_stat(mr)
    assert np.isfinite(t)
    a = np.arange(50.0)
    r, pv = ind.spearman(a, a)
    assert r > 0.99 and pv < 0.01
    b = np.arange(50.0)[::-1]
    r2, _ = ind.spearman(a, b)
    assert r2 < -0.99
    y = (np.arange(100) > 50).astype(float)
    assert ind.roc_auc(y, np.arange(100.0)) > 0.99


def test_structure_fib_and_cisd():
    df, _ = make_universe(900)
    p = Params()
    cisd = stx.detect_cisd(df, p)
    fr = cisd["frame"]
    assert set(["tcisd_long", "tcisd_short"]).issubset(fr.columns)
    assert fr["tcisd_long"].between(0, 100).all()
    assert fr["tcisd_short"].between(0, 100).all()
    assert len(cisd["events"]) > 0, "engine should find CISD events in a noisy series"

    legs = stx.build_legs(cisd_swings(df, p), p.fib_lookback)
    fib = stx.fib_frame(df, legs, p, cisd["atr"])
    assert fib["fib_long"].between(0, 100).all()
    assert fib["fib_short"].between(0, 100).all()

    # Fibonacci maths: the 62-79% band must sit between the anchor and the tip
    leg = stx.Leg(0, 1900.0, 10, 2000.0, +1)
    assert abs(leg.retr(leg.price_at_retr(0.705)) - 0.705) < 1e-9
    assert leg.price_at_retr(0.618) < leg.price_at_retr(0.5)
    assert leg.ext_price(1.272) > leg.tip_price


def cisd_swings(df, p):
    return ind.swings(df, p.pivot_left, p.pivot_right)


def test_intermarket_and_neutral():
    df, prices = make_universe(1200)
    p = Params()
    proxies = {"XAGUSD": pd.DataFrame({"close": prices["XAGUSD"], "open": prices["XAGUSD"],
                                       "high": prices["XAGUSD"] * 1.001,
                                       "low": prices["XAGUSD"] * 0.999,
                                       "volume": 1.0}, index=prices.index),
               "DXY": pd.DataFrame({"close": prices["DXY"], "open": prices["DXY"],
                                    "high": prices["DXY"] * 1.001, "low": prices["DXY"] * 0.999,
                                    "volume": 1.0}, index=prices.index)}
    ssmt = imk.ssmt_aggregate(df, proxies, p)
    assert -1 <= ssmt["ssmt_div"] <= 1
    mn = imk.market_neutral(df, prices, p)
    assert mn["mn_long"].between(0, 1).all()
    assert mn["mn_short"].between(0, 1).all()
    assert mn["n_factors"] >= 5
    assert np.isfinite(mn["r2"])
    assert mn["r2"] > 0.0, "the driver basket should explain some variance by construction"


def test_risk_models():
    df, _ = make_universe(900)
    p = Params()
    div = rsk.divergence_frame(df, p)
    assert div["div_bear"].between(0, 1).all()
    exh = rsk.exhaustion_frame(df, p, div, [])
    assert exh["exh_up"].between(0, 100).all()
    rev = rsk.reversal_model(df, p, div, exh)
    assert rev["prob"].between(0, 1).all()
    assert rev["n_oos"] > 50
    assert 0.4 <= rev["auc"] <= 1.0


def test_full_pipeline_and_backtest():
    df, prices = make_universe(1500)
    p = Params()
    htf = df.resample("4h").agg({"open": "first", "high": "max", "low": "min",
                                 "close": "last", "volume": "sum"}).dropna()
    proxies = {
        "XAGUSD": pd.DataFrame({"close": prices["XAGUSD"], "open": prices["XAGUSD"],
                                "high": prices["XAGUSD"] * 1.001, "low": prices["XAGUSD"] * 0.999,
                                "volume": 1.0}, index=prices.index),
        "DXY": pd.DataFrame({"close": prices["DXY"], "open": prices["DXY"],
                             "high": prices["DXY"] * 1.001, "low": prices["DXY"] * 0.999,
                             "volume": 1.0}, index=prices.index),
    }
    bundle = se.build_panel(df, htf, proxies=proxies, prices=prices, p=p)
    bundle["params"] = p
    panel = bundle["panel"]
    assert panel["tcisd_long"].notna().all()
    sigs = bt.generate_signals(df, bundle, p)
    assert len(sigs) > 0, "pipeline should produce at least a few gated signals"
    trades = bt.run_backtest(df, sigs, p)
    assert not trades.empty
    assert trades["mae"].max() <= 0.05
    assert trades["mfe"].min() >= -0.05
    m = bt.headline_metrics(trades, p)
    assert m["trades"] == len(trades)
    cal = bt.calibration(trades)
    assert not cal.empty
    att = bt.attribution(trades)
    assert (att["model"] == "COMPOSITE ETS").any()
    delay = bt.delay_curve(df, sigs, p)
    assert not delay.empty

    now = se.current_signal(bundle, df, p)
    assert "components" in now and len(now["components"]) >= 6


def test_synthetic_feed_is_labelled():
    fr = _synthetic("XAUUSD", "1h", 300)
    assert fr.kind == "synthetic"
    assert not fr.is_real
    assert len(fr.df) == 300
    assert (fr.df["high"] >= fr.df["low"]).all()


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except Exception as e:
            failed += 1
            import traceback
            print(f"FAIL  {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(fns)-failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
