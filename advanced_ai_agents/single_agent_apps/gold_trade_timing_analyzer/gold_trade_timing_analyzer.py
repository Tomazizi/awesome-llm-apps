"""
🥇 Gold Trade Entry Timing Accuracy Analyzer (XAU/USD)
=====================================================

A research and execution workstation for gold that answers one question the
usual indicator stack never does: *not* "is gold bullish?" but **"is this bar a
good moment to enter, and how do we know?"**

Four model families feed a single Entry Timing Score (ETS):

    tCISD            time-anchored Change In State of Delivery
    SSMT             statistical intermarket (smart-money technique) divergence
    Fibonacci        retracement / OTE / extension / time zones
    Market-neutral   beta-hedged residual + fair-value mispricing models

with reversal risk, exhaustion and divergence as risk overlays, and a news /
macro research agent as a final context layer.

Run:  streamlit run gold_trade_timing_analyzer.py
"""
from __future__ import annotations

import io
import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import pandas as pd
import streamlit as st

from core.config import ASSETS, DEFAULTS, HTF_MAP, Params, TF_MINUTES  # noqa: E402
from core import data_feeds as dfeed  # noqa: E402
from core import signal_engine as se  # noqa: E402
from core import backtest as bt  # noqa: E402
from core import research_agent as ra  # noqa: E402
from core import plots as P  # noqa: E402
from core import intermarket as imk  # noqa: E402
from core.llm import PROVIDERS  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")

st.set_page_config(
    page_title="Gold Entry Timing Analyzer — XAU/USD",
    page_icon="🥇",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ----------------------------------------------------------------------------
# Styling
# ----------------------------------------------------------------------------

st.markdown(
    """
<style>
  .block-container {padding-top: 1.1rem; padding-bottom: 2rem; max-width: 1650px;}
  .hero {border:1px solid rgba(245,197,66,.22); border-radius:14px; padding:14px 18px;
         background:linear-gradient(135deg, rgba(245,197,66,.08), rgba(0,0,0,0));}
  .spot {font-size:44px; font-weight:750; letter-spacing:-1px; line-height:1.05;}
  .spot-sub {font-size:12px; opacity:.72;}
  .badge {display:inline-block; padding:3px 10px; border-radius:999px; font-size:11px;
          font-weight:700; letter-spacing:.4px;}
  .b-real {background:rgba(38,166,154,.18); color:#4fd1b3; border:1px solid rgba(38,166,154,.4);}
  .b-proxy{background:rgba(66,165,245,.16); color:#7fc4f7; border:1px solid rgba(66,165,245,.4);}
  .b-synth{background:rgba(239,83,80,.16); color:#ff8a80; border:1px solid rgba(239,83,80,.45);}
  .card {border:1px solid rgba(255,255,255,.10); border-radius:12px; padding:13px 15px;
         background:rgba(255,255,255,.022); height:100%;}
  .card h4 {margin:0 0 8px 0; font-size:12px; letter-spacing:.9px; text-transform:uppercase; opacity:.62;}
  .long  {color:#4fd1b3; font-weight:700;}
  .short {color:#ff8a80; font-weight:700;}
  .kpi {font-size:26px; font-weight:700; line-height:1.1;}
  .kpi-l {font-size:11px; opacity:.6; text-transform:uppercase; letter-spacing:.7px;}
  .mono {font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size:12px;}
  .warn {border-left:3px solid #EF5350; padding:6px 10px; background:rgba(239,83,80,.07);
         border-radius:4px; font-size:12.5px;}
  div[data-testid="stMetricValue"] {font-size: 22px;}
</style>
""",
    unsafe_allow_html=True,
)


# ----------------------------------------------------------------------------
# Session state
# ----------------------------------------------------------------------------

def _ss(key, default):
    if key not in st.session_state:
        st.session_state[key] = default
    return st.session_state[key]


_ss("market", None)          # dict(frames=, log=, spot=, ts=, cfg=)
_ss("research", None)
_ss("bridge_cap", 90.0)
_ss("refresh_nonce", 0)


def _wait_hook(key: str, timeout_s: float) -> None:
    """Keep the app alive while a browser-bridge fetch is in flight.

    streamlit-js-eval returns None until its component posts a value back; that
    post triggers a rerun, so we sleep briefly and rerun until either the payload
    arrives or this (or the global) budget is exhausted.
    """
    waits = _ss("_bridge_wait", {})
    budget = _ss("_bridge_budget", {"start": time.time()})
    rec = waits.setdefault(key, {"t0": time.time(), "failed": False})
    cap = float(st.session_state.get("bridge_cap", 90.0))
    if rec["failed"] or (time.time() - budget["start"] > cap):
        rec["failed"] = True
        return
    if time.time() - rec["t0"] > timeout_s:
        rec["failed"] = True
        return
    time.sleep(0.55)
    st.rerun()


dfeed.set_wait_hook(_wait_hook)


# ----------------------------------------------------------------------------
# Sidebar
# ----------------------------------------------------------------------------

def sidebar() -> dict:
    st.sidebar.markdown("## 🥇 XAU/USD Timing Desk")
    st.sidebar.caption("Entry-timing research, not a signal service.")

    st.sidebar.markdown("### Market data")
    interval = st.sidebar.selectbox("Execution timeframe", list(TF_MINUTES.keys()), index=3)
    n_bars = st.sidebar.slider("Bars to load", 600, 6000, 2200, step=200)
    use_bridge = st.sidebar.toggle(
        "Browser bridge",
        value=True,
        help="Fetch through the user's browser when the server has no outbound "
             "internet (works inside locked sandboxes / Streamlit Cloud).",
    )
    st.session_state["bridge_cap"] = st.sidebar.slider(
        "Bridge wait budget (s)", 10, 180, 60, step=10,
        help="Total time the app will wait for browser-side fetches before falling back."
    )
    universe = st.sidebar.multiselect(
        "Driver universe (for SSMT + market-neutral)",
        [a for a in ASSETS if a != "XAUUSD"],
        default=[a for a in ASSETS if a != "XAUUSD"],
        format_func=lambda a: ASSETS[a]["label"],
    )
    auto = st.sidebar.number_input("Auto refresh (s, 0 = off)", 0, 600, 0, step=15)
    if auto > 0:
        try:
            from streamlit_autorefresh import st_autorefresh
            st_autorefresh(interval=int(auto * 1000), key="auto_refresh")
        except Exception:
            pass

    c1, c2 = st.sidebar.columns(2)
    if c1.button("↻ Reload data", use_container_width=True):
        st.session_state["market"] = None
        st.session_state["_bridge_wait"] = {}
        st.session_state["_bridge_budget"] = {"start": time.time()}
        st.rerun()
    if c2.button("↻ Re-run research", use_container_width=True):
        st.session_state["research"] = None

    up = st.sidebar.file_uploader(
        "Broker CSV (ts,open,high,low,close,volume)", type=["csv"],
        help="Saved to data/XAUUSD_<tf>.csv and used as the top-priority source."
    )
    if up is not None:
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            raw = pd.read_csv(io.BytesIO(up.getvalue()))
            cols = {c.lower(): c for c in raw.columns}
            tcol = cols.get("ts") or cols.get("time") or cols.get("date") or cols.get("datetime")
            if not tcol:
                st.sidebar.error("CSV needs a ts / time / date column")
            else:
                raw = raw.rename(columns={tcol: "ts"})
                raw["ts"] = pd.to_datetime(raw["ts"], utc=True, errors="coerce")
                for k in ("open", "high", "low", "close"):
                    if k in cols:
                        raw = raw.rename(columns={cols[k]: k})
                raw = raw.dropna(subset=["ts", "close"]).set_index("ts").sort_index()
                if "volume" not in raw.columns:
                    raw["volume"] = 0.0
                path = os.path.join(DATA_DIR, f"XAUUSD_{interval}.csv")
                raw[["open", "high", "low", "close", "volume"]].to_csv(path)
                st.sidebar.success(f"Saved {len(raw)} bars → data/XAUUSD_{interval}.csv")
                st.session_state["market"] = None
        except Exception as e:
            st.sidebar.error(f"CSV error: {e}")

    # ---------------- model parameters ----------------
    p = Params()
    with st.sidebar.expander("Model parameters", expanded=False):
        p.pivot_left = p.pivot_right = st.slider("Swing pivot (left/right bars)", 2, 12, 5)
        p.atr_period = st.slider("ATR period", 7, 30, 14)
        p.disp_body_atr = st.slider("Displacement body (x ATR)", 0.6, 3.0, 1.1, 0.1)
        p.tcisd_half_life = st.slider("tCISD half-life (bars)", 1.0, 24.0, 6.0, 0.5)
        p.tcisd_max_bars = st.slider("tCISD hard expiry (bars)", 6, 60, 24)
        p.ssmt_z_div_threshold = st.slider("SSMT divergence z threshold", 0.2, 3.0, 0.85, 0.05)
        p.mn_window = st.slider("Market-neutral window", 100, 500, 250, 25)
        p.rev_horizon = st.slider("Reversal horizon (bars)", 4, 40, 12)
        p.min_conviction = st.slider("Minimum conviction", 40, 85, 55)
        p.min_rr = st.slider("Minimum R:R", 1.0, 4.0, 1.5, 0.1)
        p.bt_max_hold = st.slider("Max hold (bars)", 12, 200, 72, 4)
        p.bt_cooldown = st.slider("Cooldown between trades (bars)", 0, 30, 6)

    with st.sidebar.expander("Entry Timing Score weights", expanded=False):
        st.caption("Normalised to sum to 1.")
        p.w_tcisd = st.slider("tCISD", 0.0, 0.5, 0.24, 0.01)
        p.w_fib = st.slider("Fibonacci", 0.0, 0.5, 0.16, 0.01)
        p.w_mn = st.slider("Market-neutral", 0.0, 0.5, 0.16, 0.01)
        p.w_ssmt = st.slider("SSMT", 0.0, 0.5, 0.14, 0.01)
        p.w_exhaust = st.slider("Exhaustion", 0.0, 0.5, 0.12, 0.01)
        p.w_reversal = st.slider("Reversal risk", 0.0, 0.5, 0.10, 0.01)
        p.w_session = st.slider("Session", 0.0, 0.3, 0.04, 0.01)
        p.w_news = st.slider("News", 0.0, 0.3, 0.04, 0.01)

    with st.sidebar.expander("Risk & sizing", expanded=False):
        p.equity = st.number_input("Account equity (USD)", 1_000, 10_000_000, 100_000, 5_000)
        p.risk_per_trade_pct = st.slider("Risk per trade (%)", 0.1, 3.0, 0.5, 0.05) / 100
        p.contract_size = st.number_input("Contract size (oz per lot)", 1, 1000, 100)
        p.spread_usd = st.number_input("Spread (USD/oz)", 0.0, 5.0, 0.35, 0.05)
        p.commission_usd = st.number_input("Commission (USD/oz)", 0.0, 5.0, 0.10, 0.05)

    # ---------------- research agent ----------------
    with st.sidebar.expander("Research agent (LLM)", expanded=False):
        prov = st.selectbox("Provider", list(PROVIDERS.keys()),
                            format_func=lambda k: PROVIDERS[k]["label"], index=1)
        models = PROVIDERS[prov]["models"]
        model = st.selectbox("Model", models)
        api_key = st.text_input("API key", type="password",
                                help="Stored in the session only, never written to disk.")
        base_url = st.text_input("Base URL override (optional)", value="")
        run_research = st.button("🔎 Run market research", use_container_width=True)
        st.caption("No key? The deterministic news layer still runs.")

    return {
        "interval": interval, "bars": n_bars, "bridge": use_bridge,
        "universe": list(universe), "params": p,
        "llm": {"provider": prov, "model": model, "key": api_key,
                "base": base_url or None, "run": run_research},
    }


# ----------------------------------------------------------------------------
# Data loading
# ----------------------------------------------------------------------------

@st.cache_data(show_spinner=False, max_entries=6)
def compute_bundle(df_json: str, htf_json: str, prices_json: str, proxies_json: str,
                   pkey: tuple):
    """Heavy analytics. Cached on (data, parameters)."""
    df = pd.read_json(io.StringIO(df_json), orient="split")
    htf = pd.read_json(io.StringIO(htf_json), orient="split")
    prices = pd.read_json(io.StringIO(prices_json), orient="split")
    proxies = {}
    if proxies_json and proxies_json not in ("{}", ""):
        for a, blob in json.loads(proxies_json).items():
            proxies[a] = pd.read_json(io.StringIO(blob), orient="split")
    for frame in [df, htf, prices] + list(proxies.values()):
        if frame is not None and len(frame):
            frame.index = pd.to_datetime(frame.index, utc=True)
    params = Params(**dict(pkey))
    bundle = se.build_panel(df, htf, proxies=proxies, prices=prices, p=params)
    bundle["params"] = params
    return bundle


@st.cache_data(show_spinner=False, max_entries=4, ttl=1800)
def compute_all(df_json: str, htf_json: str, prices_json: str, proxies_json: str,
                pkey: tuple, interval: str) -> dict:
    """Bundle + every backtest artefact, cached together.

    The timing lab is the expensive part of the app (a second pass over every
    bar for the delay experiment, plus six more for the threshold sweep), so it
    is cached on the same key as the analytics instead of being recomputed on
    every widget interaction.
    """
    df = pd.read_json(io.StringIO(df_json), orient="split")
    df.index = pd.to_datetime(df.index, utc=True)
    params = Params(**dict(pkey))
    bundle = compute_bundle(df_json, htf_json, prices_json, proxies_json, pkey)
    sigs = bt.generate_signals(df, bundle, params)
    trades = bt.run_backtest(df, sigs, params)
    return {
        "df": df,
        "bundle": bundle,
        "signals": sigs,
        "trades": trades,
        "funnel": bt.gate_funnel(df, bundle, params),
        "delay": bt.delay_curve(df, sigs, params, atr=bundle["panel"]["atr"]),
        "cal": bt.calibration(trades),
        "att": bt.attribution(trades),
        "sens": bt.threshold_sensitivity(df, bundle, params),
        "metrics": bt.headline_metrics(trades, params, freq=interval),
    }


def _key(df: pd.DataFrame) -> str:
    return df.to_json(orient="split", date_format="iso")


def load_market(cfg: dict) -> dict:
    """Fetch the universe. Not cached - the browser bridge may trigger reruns."""
    if st.session_state.get("market") is not None:
        return st.session_state["market"]

    log = dfeed.FetchLog()
    st.session_state.setdefault("_bridge_budget", {"start": time.time()})
    with st.spinner("Connecting to market data…"):
        primary = dfeed.fetch_asset("XAUUSD", cfg["interval"], cfg["bars"], log,
                                    use_browser=cfg["bridge"], csv_dir=DATA_DIR)
        frames = {"XAUUSD": primary}

        macro = [a for a in cfg["universe"] if a != "XAUUSD"]
        if macro:
            got = dfeed.fetch_yahoo_batch(macro, cfg["interval"], log,
                                          use_browser=cfg["bridge"],
                                          bridge_only=not dfeed.server_has_network())
            for a in macro:
                if a in got:
                    frames[a] = got[a]

        spot = dfeed.fetch_spot(use_browser=cfg["bridge"], log=log)

    market = {"frames": frames, "log": log, "spot": spot,
              "ts": datetime.now(timezone.utc), "cfg": {
                  "interval": cfg["interval"], "bars": cfg["bars"],
                  "bridge": cfg["bridge"], "universe": cfg["universe"]}}
    st.session_state["market"] = market
    return market


# ----------------------------------------------------------------------------
# Render helpers
# ----------------------------------------------------------------------------

def provenance_badge(frame) -> str:
    if frame.kind == "synthetic":
        return '<span class="badge b-synth">SYNTHETIC</span>'
    if frame.kind == "proxy":
        return '<span class="badge b-proxy">LIVE PROXY</span>'
    return '<span class="badge b-real">LIVE MARKET DATA</span>'


def render_header(market: dict, cfg: dict):
    frames = market["frames"]
    xau = frames["XAUUSD"]
    d = xau.df
    spot = market["spot"]
    price = spot.get("price")
    last_close = float(d["close"].iloc[-1])
    prev_close = float(d["close"].iloc[-2]) if len(d) > 1 else last_close
    px = price if price else last_close
    chg = px - prev_close
    pct = 100 * chg / prev_close if prev_close else 0.0
    now = datetime.now(timezone.utc)
    hour = now.hour
    sess = ("Asia" if hour < 7 else "London" if hour < 12 else
            "New York" if hour < 20 else "Late")

    st.markdown(
        f"""
<div class="hero">
  <div style="display:flex; gap:26px; align-items:flex-end; flex-wrap:wrap;">
    <div>
      <div class="spot">${px:,.2f}</div>
      <div class="spot-sub">XAU/USD &nbsp;•&nbsp; {'live spot' if price else 'last bar close'}
        &nbsp;•&nbsp; {spot.get('source','—')}
        {'&nbsp;•&nbsp;' + spot['ts'].strftime('%H:%M:%S UTC') if spot.get('ts') else ''}</div>
    </div>
    <div>
      <div class="kpi" style="color:{'#4fd1b3' if chg>=0 else '#ff8a80'}">
        {chg:+,.2f} ({pct:+.2f}%)</div>
      <div class="kpi-l">session change</div>
    </div>
    <div>
      <div class="kpi">{sess}</div>
      <div class="kpi-l">session &nbsp;•&nbsp; {now.strftime('%H:%M')} UTC</div>
    </div>
    <div>
      <div class="kpi">{cfg['interval']}</div>
      <div class="kpi-l">{len(d):,} bars &nbsp;•&nbsp; HTF {HTF_MAP[cfg['interval']]}</div>
    </div>
    <div style="margin-left:auto; text-align:right;">
      {provenance_badge(xau)}
      <div class="spot-sub" style="margin-top:6px; max-width:330px;">{xau.source}</div>
      <div class="spot-sub" style="opacity:.5;">{xau.note}</div>
    </div>
  </div>
</div>
""",
        unsafe_allow_html=True,
    )
    if xau.kind == "synthetic":
        st.markdown(
            '<div class="warn"><b>No live feed reachable.</b> The engine is running on a '
            'clearly-labelled synthetic series so every model stays exercisable. '
            'Run this app with unrestricted internet (or upload a broker CSV in the '
            'sidebar) for real market data.</div>', unsafe_allow_html=True)


def st_df(data, **kwargs):
    """dataframe that works on both the old and the new width API."""
    kwargs.pop("use_container_width", None)
    try:
        st.dataframe(data, width="stretch", **kwargs)
    except TypeError:
        st.dataframe(data, use_container_width=True, **kwargs)


def st_plot(fig, **kwargs):
    """plotly_chart that works on both the old and the new width API."""
    kwargs.pop("use_container_width", None)
    try:
        st.plotly_chart(fig, width="stretch", **kwargs)
    except TypeError:
        st.plotly_chart(fig, use_container_width=True, **kwargs)


def kpi(label, value, sub=""):
    st.markdown(
        f'<div class="card"><h4>{label}</h4><div class="kpi">{value}</div>'
        f'<div class="kpi-l">{sub}</div></div>', unsafe_allow_html=True)


def comp_bar(name: str, value: float, colour: str):
    st.markdown(
        f"""
<div style="margin:7px 0;">
  <div style="display:flex; justify-content:space-between; font-size:12px;">
    <span>{name}</span><span class="mono">{value:.0f}</span>
  </div>
  <div style="height:6px; background:rgba(255,255,255,.07); border-radius:4px; margin-top:3px;">
    <div style="width:{max(0,min(100,value)):.1f}%; height:6px; background:{colour}; border-radius:4px;"></div>
  </div>
</div>""",
        unsafe_allow_html=True,
    )


# ----------------------------------------------------------------------------
# Tabs
# ----------------------------------------------------------------------------

def tab_signal(market: dict, bundle: dict, cfg: dict, news: dict):
    p: Params = cfg["params"]
    df = market["frames"]["XAUUSD"].df
    nb = news or {"bias": 0.0, "confidence": 0.0, "event_penalty": 1.0, "source": "none"}
    sig = se.current_signal(bundle, df, p, news_bias=nb["bias"],
                            event_penalty=nb["event_penalty"],
                            live_price=market["spot"].get("price"))
    tr = sig["trade"]
    d = sig["direction"]
    cls = "long" if d > 0 else "short"

    left, right = st.columns([1.15, 1])

    with left:
        st.markdown("### Signal card")
        st.markdown(
            f"""
<div class="card">
  <div style="display:flex; align-items:baseline; gap:14px; flex-wrap:wrap;">
    <div style="font-size:34px;" class="{cls}">{'LONG' if d>0 else 'SHORT'} 🥇</div>
    <div style="font-size:13px; opacity:.7;">Entry Timing Score</div>
    <div class="kpi">{sig['score']:.0f}<span style="font-size:15px; opacity:.5;">/100</span></div>
    <div><span class="badge {'b-real' if sig['tier'] in ('A','B') else 'b-synth'}">
      TIER {sig['tier'] if sig['tier'] != '-' else 'NO TRADE'}</span></div>
  </div>
  <div style="margin-top:6px; font-size:12px; opacity:.6;">
    bar {sig['bar'].strftime('%Y-%m-%d %H:%M')} UTC &nbsp;•&nbsp;
    long {sig['score_long']:.1f} / short {sig['score_short']:.1f} &nbsp;•&nbsp;
    prevailing move {'up' if sig['prevailing']>0 else 'down' if sig['prevailing']<0 else 'flat'}
  </div>
</div>""", unsafe_allow_html=True)

        st.markdown("")
        if tr is None:
            st.markdown(
                '<div class="warn"><b>No trade.</b> One or more gates failed - the engine '
                'would rather be flat than force a setup. See the gate checklist.</div>',
                unsafe_allow_html=True)
        else:
            e, s, t1, t2, t3 = tr.entry, tr.stop, tr.tp1, tr.tp2, tr.tp3
            risk = abs(e - s)
            rows = [
                ("ENTRY", f"{e:,.2f}", "market / next open"),
                ("STOP", f"{s:,.2f}", f"{tr.risk_atr:.2f} ATR • {risk:,.2f} USD"),
                ("TP1 (50%)", f"{t1:,.2f}", f"{p.tp1_r:.1f}R"),
                ("TP2 (30%)", f"{t2:,.2f}", f"{abs(t2-e)/risk:.1f}R"),
                ("TP3 (20%)", f"{t3:,.2f}", f"{abs(t3-e)/risk:.1f}R"),
            ]
            tbl = "".join(
                f"<tr><td style='opacity:.65;'>{a}</td><td class='mono' style='text-align:right;'>{b}</td>"
                f"<td style='opacity:.55; text-align:right; font-size:11px;'>{c}</td></tr>"
                for a, b, c in rows)
            st.markdown(
                f"""
<div class="card">
  <h4>Execution plan</h4>
  <table style="width:100%; border-collapse:collapse;">{tbl}</table>
  <div style="display:flex; gap:20px; margin-top:12px; flex-wrap:wrap;">
    <div><div class="kpi">{tr.rr:.2f}</div><div class="kpi-l">reward : risk</div></div>
    <div><div class="kpi">{tr.size:.2f}</div><div class="kpi-l">lots ({p.contract_size} oz)</div></div>
    <div><div class="kpi">${tr.risk_usd:,.0f}</div><div class="kpi-l">capital at risk</div></div>
    <div><div class="kpi">{float(bundle['panel']['atr'].iloc[-1]):,.2f}</div><div class="kpi-l">ATR(14)</div></div>
  </div>
</div>""", unsafe_allow_html=True)
            if tr.notes:
                st.caption("• " + "  • ".join(tr.notes))

        st.markdown("")
        st.markdown("#### Gates")
        for name, ok, detail in sig["gates"]:
            st.markdown(
                f"{'🟢' if ok else '🔴'} **{name}** — <span class='mono' style='opacity:.7'>{detail}</span>",
                unsafe_allow_html=True)

    with right:
        st.markdown("#### Model contribution")
        cols = {
            "tCISD": "#F5C542", "Fibonacci": "#42A5F5",
            "MarketNeutral": "#AB47BC", "SSMT": "#26C6DA",
            "ExhaustionRisk": "#EF5350", "ReversalRisk": "#FF7043",
            "Session": "#9CCC65", "NewsAlignment": "#FFD54F",
        }
        for k, v in sig["components"].items():
            comp_bar(k, v, cols.get(k, "#8D6E63"))
        st.caption(
            "ExhaustionRisk and ReversalRisk are shown as risk readings (higher = worse for "
            "this direction); all other bars are contributions where higher is better.")

        st.markdown("#### News / macro overlay")
        st.markdown(
            f"Bias <b>{nb['bias']:+.2f}</b> "
            f"(confidence {nb['confidence']:.0%}, source: {nb.get('source','—')}) &nbsp;•&nbsp; "
            f"event-risk multiplier <b>{nb['event_penalty']:.2f}</b>")
        st.progress(min(1.0, abs(nb["bias"])), text="news alignment with the engine's direction")

    st.markdown("---")
    st_plot(P.chart_price(df, bundle, tr), use_container_width=True)


def tab_models(market: dict, bundle: dict, cfg: dict):
    p: Params = cfg["params"]
    df = market["frames"]["XAUUSD"].df
    panel = bundle["panel"]
    mn = bundle["mn"]
    ssmt = bundle["ssmt"]
    rev = bundle["rev"]

    t1, t2, t3, t4, t5 = st.tabs(
        ["tCISD", "SSMT", "Fibonacci", "Market-neutral", "Reversal / Exhaustion"])

    with t1:
        c1, c2 = st.columns([1.3, 1])
        with c1:
            st_plot(P.chart_price(df, bundle, None, lookback=180,
                                          show_fib=False), use_container_width=True)
        with c2:
            ev = bundle["cisd"]["events"]
            live = [e for e in ev if e.invalidated_at is None][-8:]
            st.markdown("#### Active CISD events")
            if live:
                st_df(pd.DataFrame([e.as_dict() for e in live]),
                             use_container_width=True, hide_index=True)
            else:
                st.info("No active CISD event. Price has not changed state of delivery "
                        "recently - the engine stays flat rather than trading a guess.")
            st.markdown(
                f"""
<div class="card" style="margin-top:8px;">
  <h4>Reading</h4>
  <div class="mono" style="font-size:12px; line-height:1.65;">
    tCISD long <b>{panel['tcisd_long'].iloc[-1]:.0f}</b>/100 &nbsp; short <b>{panel['tcisd_short'].iloc[-1]:.0f}</b>/100<br>
    half-life <b>{p.tcisd_half_life:.0f}</b> bars &nbsp;•&nbsp; expiry <b>{p.tcisd_max_bars}</b> bars<br>
    events found <b>{len(ev)}</b> &nbsp;•&nbsp; open imbalances <b>{len([g for g in bundle['fvg'] if g.filled_at is None])}</b>
  </div>
</div>""", unsafe_allow_html=True)

    with t2:
        bd = ssmt["breakdown"]
        c1, c2 = st.columns([1, 1.25])
        with c1:
            st.markdown("#### Intermarket divergence")
            st.markdown(
                f"""
<div class="card">
  <h4>SSMT aggregate</h4>
  <div class="kpi" style="color:{'#4fd1b3' if ssmt['ssmt_div']>0 else '#ff8a80'}">
    {ssmt['ssmt_div']:+.2f}</div>
  <div class="kpi-l">-1 bearish non-confirmation … +1 bullish</div>
  <div style="margin-top:10px;" class="mono">
    relative strength (beta-hedged): <b>{ssmt['ssmt_rel_strength']:+.2f}</b><br>
    long score <b>{ssmt['ssmt_long']*100:.0f}</b> &nbsp; short score <b>{ssmt['ssmt_short']*100:.0f}</b>
  </div>
</div>""", unsafe_allow_html=True)
            st.caption(
                "A gold high that silver (or the dollar, or real yields) refuses to confirm is "
                "usually a liquidity run rather than a trend. Correlation-weighted, so "
                "uncorrelated assets cannot vote.")
        with c2:
            if bd is not None and not bd.empty:
                show = bd[["proxy", "corr", "corr_p", "divergence", "spread_z", "detail"]].copy()
                show.columns = ["proxy", "ρ", "p(ρ≠0)", "div", "spread z", "reading"]
                st_df(show.style.format({"ρ": "{:.2f}", "p(ρ≠0)": "{:.3f}",
                                                "div": "{:+.0f}", "spread z": "{:+.2f}"}),
                             use_container_width=True, hide_index=True)
            else:
                st.info("No correlated proxies loaded. Enable the driver universe in the sidebar.")

    with t3:
        legs = bundle["legs"]
        c1, c2 = st.columns([1.35, 1])
        with c1:
            st_plot(P.chart_price(df, bundle, None, lookback=200,
                                          show_cisd=False), use_container_width=True)
        with c2:
            st.markdown("#### Active legs")
            if legs:
                rows = []
                for name, lg in legs.items():
                    retr = lg.retr(float(df["close"].iloc[-1]))
                    rows.append({
                        "leg": name,
                        "dir": "up" if lg.direction > 0 else "down",
                        "anchor": round(lg.anchor_price, 2),
                        "tip": round(lg.tip_price, 2),
                        "size": round(lg.size, 2),
                        "retr": round(retr, 3) if np.isfinite(retr) else None,
                        "in OTE": "yes" if np.isfinite(retr) and 0.618 <= retr <= 0.786 else "no",
                    })
                st_df(pd.DataFrame(rows), use_container_width=True, hide_index=True)
            st.markdown(
                f"""
<div class="card" style="margin-top:8px;">
  <h4>Fibonacci state</h4>
  <div class="mono" style="font-size:12px; line-height:1.7;">
    retracement of primary leg <b>{panel['fib_retr'].iloc[-1]:.3f}</b><br>
    long score <b>{panel['fib_long'].iloc[-1]:.0f}</b> &nbsp; short <b>{panel['fib_short'].iloc[-1]:.0f}</b><br>
    level confluence <b>{panel['fib_confluence'].iloc[-1]:.1f}</b><br>
    fibonacci time node <b>{'yes' if panel['fib_timezone'].iloc[-1] else 'no'}</b>
  </div>
</div>""", unsafe_allow_html=True)
            st.caption("OTE band = 61.8–78.6% retracement; equilibrium = 50%.")

    with t4:
        c1, c2 = st.columns([1.3, 1])
        with c1:
            st_plot(P.chart_residual(mn), use_container_width=True)
            st_plot(P.chart_betas(mn.get("rls")), use_container_width=True)
        with c2:
            hl = mn.get("halflife")
            hl_s = f"{hl:.1f} bars" if np.isfinite(hl) else "not mean-reverting"
            st.markdown(
                f"""
<div class="card">
  <h4>Beta-neutralised residual</h4>
  <div class="mono" style="font-size:12px; line-height:1.8;">
    return residual z <b>{panel['mn_z'].iloc[-1] if 'mn_z' in panel and pd.notna(panel['mn_z'].iloc[-1]) else float('nan'):+.2f}</b><br>
    fair-value (level) z <b>{float(mn.get('mn_fv_z').dropna().iloc[-1]) if mn.get('mn_fv_z') is not None and mn['mn_fv_z'].dropna().size else float('nan'):+.2f}</b><br>
    OU half-life <b>{hl_s}</b><br>
    Hurst <b>{mn.get('hurst', float('nan')):.3f}</b> → {'mean reverting' if (mn.get('hurst') or .5) < .5 else 'trending'}<br>
    ADF t <b>{mn.get('adf_t', float('nan')):.2f}</b> (p≈{mn.get('adf_p', float('nan')):.3f})<br>
    driver R² <b>{mn.get('r2', float('nan')):.3f}</b> &nbsp;•&nbsp; factors <b>{mn.get('n_factors', 0)}</b><br>
    mean-reversion weight <b>{mn.get('w_meanrev', float('nan')):.2f}</b>
  </div>
</div>""", unsafe_allow_html=True)
            st.markdown("")
            bets = mn.get("betas") or {}
            if bets:
                st_df(
                    pd.DataFrame({"factor": list(bets.keys()),
                                  "beta": [round(v, 4) for v in bets.values()]}),
                    use_container_width=True, hide_index=True)
            st.caption(
                "The residual is the part of gold's move that the dollar, real yields, risk "
                "appetite, the precious complex and reflation do not explain. Hurst decides "
                "whether that residual should be faded or followed.")

    with t5:
        c1, c2 = st.columns([1.3, 1])
        with c1:
            st_plot(P.chart_exhaustion(bundle["exh"]), use_container_width=True)
            st_plot(P.chart_rsi_divergence(df, bundle["div"]), use_container_width=True)
        with c2:
            auc = (rev or {}).get("auc", float("nan"))
            st.markdown(
                f"""
<div class="card">
  <h4>Reversal risk model</h4>
  <div class="mono" style="font-size:12px; line-height:1.8;">
    P(reversal ≥ {p.rev_move_atr:.1f} ATR in {p.rev_horizon} bars) <b>{panel['reversal_risk'].iloc[-1]:.1%}</b><br>
    out-of-sample AUC <b>{auc:.3f}</b> (n={(rev or {}).get('n_oos', 0)})<br>
    base rate <b>{(rev or {}).get('base_rate', float('nan')):.1%}</b><br>
    refit every {p.rev_refit_every} bars on a {p.rev_train_window}-bar expanding window
  </div>
</div>""", unsafe_allow_html=True)
            st.markdown("")
            st.markdown("#### Feature weights (walk-forward logistic)")
            co = (rev or {}).get("coefs")
            if co is not None and len(co):
                cdf = pd.DataFrame({"feature": co.index, "mean coefficient": co.values})
                st_df(cdf.style.format({"mean coefficient": "{:+.3f}"}),
                             use_container_width=True, hide_index=True)
            st.markdown(
                f"""
<div class="card" style="margin-top:8px;">
  <h4>Exhaustion now</h4>
  <div class="mono" style="font-size:12px; line-height:1.8;">
    up-move exhaustion <b>{panel['exh_up'].iloc[-1]:.0f}</b>/100<br>
    down-move exhaustion <b>{panel['exh_dn'].iloc[-1]:.0f}</b>/100<br>
    ATR expansion <b>{bundle['exh']['atr_ratio'].iloc[-1]:.2f}×</b><br>
    stacked unfilled imbalances up/down
      <b>{int(bundle['exh']['stack_fvg_up'].iloc[-1])}/{int(bundle['exh']['stack_fvg_dn'].iloc[-1])}</b><br>
    divergence bear/bull <b>{panel['div_bear'].iloc[-1]:.2f}/{panel['div_bull'].iloc[-1]:.2f}</b>
  </div>
</div>""", unsafe_allow_html=True)


def tab_timing(market: dict, bundle: dict, cfg: dict, res: dict):
    p: Params = cfg["params"]
    df = market["frames"]["XAUUSD"].df
    funnel, delay = res["funnel"], res["delay"]
    cal, att, m = res["cal"], res["att"], res["metrics"]
    trades = res["trades"]

    st.markdown("### Entry timing accuracy")
    if trades.empty:
        st.warning(
            "No signals cleared the gates on this sample. Lower **Minimum conviction** in the "
            "sidebar, load more bars, or widen the driver universe. The funnel below shows "
            "exactly where setups are being filtered out.")
        st_df(funnel, use_container_width=True, hide_index=True)
        return

    c = st.columns(7)
    c[0].markdown(f'<div class="card"><h4>Trades</h4><div class="kpi">{m["trades"]}</div>'
                  f'<div class="kpi-l">after all gates</div></div>', unsafe_allow_html=True)
    c[1].markdown(f'<div class="card"><h4>Expectancy</h4><div class="kpi">{m["expectancy_r"]:+.3f}R</div>'
                  f'<div class="kpi-l">net of costs</div></div>', unsafe_allow_html=True)
    c[2].markdown(f'<div class="card"><h4>Win rate</h4><div class="kpi">{m["win_rate"]:.0%}</div>'
                  f'<div class="kpi-l">PF {m["profit_factor"]:.2f}</div></div>', unsafe_allow_html=True)
    c[3].markdown(f'<div class="card"><h4>Mean MAE</h4><div class="kpi">{m["mean_mae_r"]:.2f}R</div>'
                  f'<div class="kpi-l">adverse excursion</div></div>', unsafe_allow_html=True)
    c[4].markdown(f'<div class="card"><h4>Mean MFE</h4><div class="kpi">{m["mean_mfe_r"]:.2f}R</div>'
                  f'<div class="kpi-l">favourable excursion</div></div>', unsafe_allow_html=True)
    c[5].markdown(f'<div class="card"><h4>Entry efficiency</h4><div class="kpi">{m["entry_efficiency"]:.0%}</div>'
                  f'<div class="kpi-l">MFE / (MFE+|MAE|)</div></div>', unsafe_allow_html=True)
    c[6].markdown(f'<div class="card"><h4>Clean entries</h4><div class="kpi">{m["clean_entry_rate"]:.0%}</div>'
                  f'<div class="kpi-l">+1R before -1R</div></div>', unsafe_allow_html=True)

    st.markdown("")
    a, b = st.columns([1.25, 1])
    with a:
        st_plot(P.chart_delay(delay), use_container_width=True)
        best = delay.loc[delay["mean_r"].idxmax()] if not delay.empty else None
        if best is not None:
            st.markdown(
                f"**Optimal entry lag on this sample: `{int(best['delay_bars'])}` bars** "
                f"(mean R {best['mean_r']:+.3f} vs {delay.iloc[0]['mean_r']:+.3f} entering "
                f"immediately). Entering immediately is "
                f"{'best' if int(best['delay_bars'])==0 else 'NOT optimal here - waiting for the retrace adds edge'}.")
    with b:
        st_plot(P.chart_mae_mfe(trades), use_container_width=True)

    st.markdown("")
    a, b = st.columns([1, 1])
    with a:
        st_plot(P.chart_calibration(cal), use_container_width=True)
    with b:
        st_plot(P.chart_attribution(att), use_container_width=True)

    st.markdown("")
    a, b, c2 = st.columns([1, 1, 1.1])
    with a:
        st.markdown("#### Gate funnel")
        st_df(funnel.style.format({"pct_of_bars": "{:.2f}%",
                                          "step_efficiency": "{:.2f}"}),
                     use_container_width=True, hide_index=True)
    with b:
        st.markdown("#### Threshold sensitivity")
        with st.spinner("…"):
            sens = bt.threshold_sensitivity(df, bundle, p)
        st_df(sens.style.format({
            "win_rate": "{:.0%}", "expectancy_r": "{:+.3f}", "profit_factor": "{:.2f}",
            "sharpe_r": "{:.2f}", "clean_entry_rate": "{:.0%}"}),
            use_container_width=True, hide_index=True)
    with c2:
        st.markdown("#### Timing heatmap")
        st_plot(P.chart_timing_heatmap(trades), use_container_width=True)

    st.markdown("")
    st_plot(P.chart_equity(trades), use_container_width=True)
    st.markdown("#### Trade log")
    st_df(trades[["ts", "dir", "session", "score", "tier", "entry", "stop", "tp1",
                         "risk_atr", "r", "exit", "bars", "mae", "mfe", "entry_eff", "clean"]]
                 .sort_values("ts", ascending=False)
                 .style.format({"entry": "{:.2f}", "stop": "{:.2f}", "tp1": "{:.2f}",
                                "risk_atr": "{:.2f}", "r": "{:+.2f}", "mae": "{:.2f}",
                                "mfe": "{:.2f}", "entry_eff": "{:.2f}", "score": "{:.1f}"}),
                 use_container_width=True, hide_index=True)


def tab_research(market: dict, bundle: dict, cfg: dict, news_state: dict):
    p: Params = cfg["params"]
    llm = cfg["llm"]
    df = market["frames"]["XAUUSD"].df

    st.markdown("### Market & news research agent")
    c1, c2 = st.columns([1.5, 1])

    with c1:
        if st.session_state.get("research") is None or llm["run"]:
            with st.spinner("Scanning metals & macro news wires…"):
                news_df, errors = ra.fetch_news(use_browser=cfg["bridge"])
            if not news_df.empty:
                news_df = ra.classify_news(news_df)
            det = ra.news_bias(news_df) if not news_df.empty else {
                "bias": 0.0, "confidence": 0.0, "event_penalty": 1.0, "n": 0}
            brief = None
            if llm["run"] and llm["key"]:
                with st.spinner(f"Asking {llm['provider']}:{llm['model']} for a research brief…"):
                    try:
                        sig = se.current_signal(bundle, df, p)
                        ctx = ra.build_context(sig, bundle, market["spot"].get("price"))
                        brief = ra.llm_brief(llm["provider"], llm["model"], llm["key"],
                                             news_df, ctx, base_url=llm["base"])
                    except Exception as e:
                        st.error(f"LLM call failed: {e}")
            elif llm["run"]:
                st.info("Add an API key in the sidebar for the LLM layer - running the "
                        "deterministic layer only.")
            st.session_state["research"] = {
                "news": news_df, "det": det, "brief": brief, "errors": errors,
                "ts": datetime.now(timezone.utc),
            }

        res = st.session_state.get("research") or {}
        news_df = res.get("news", pd.DataFrame())
        brief = res.get("brief")
        det = res.get("det", {"bias": 0.0, "confidence": 0.0, "event_penalty": 1.0})
        resolved = ra.resolve_bias(brief, det)

        st.markdown(
            f"""
<div class="card">
  <div style="display:flex; gap:26px; align-items:baseline; flex-wrap:wrap;">
    <div><div class="kpi" style="color:{'#4fd1b3' if resolved['bias']>0 else '#ff8a80'}">
      {resolved['bias']:+.2f}</div><div class="kpi-l">macro bias (bullish gold +)</div></div>
    <div><div class="kpi">{resolved['confidence']:.0%}</div><div class="kpi-l">confidence</div></div>
    <div><div class="kpi">{resolved['event_penalty']:.2f}</div><div class="kpi-l">event-risk multiplier</div></div>
    <div><div class="kpi">{len(news_df)}</div><div class="kpi-l">headlines scanned</div></div>
    <div style="margin-left:auto; font-size:11px; opacity:.55;">
      source: {resolved['source']} &nbsp;•&nbsp; {res.get('ts', datetime.now(timezone.utc)).strftime('%H:%M:%S UTC')}
    </div>
  </div>
</div>""", unsafe_allow_html=True)

        st.markdown("")
        if brief and brief.get("json"):
            j = brief["json"]
            if j.get("macro_regime"):
                st.markdown("#### Macro regime")
                st.write(j["macro_regime"])
            if j.get("narrative"):
                st.markdown("#### Narrative")
                for b in j["narrative"]:
                    st.markdown(f"• {b}")
            if j.get("catalysts"):
                st.markdown("#### Catalyst map")
                st_df(pd.DataFrame(j["catalysts"]), use_container_width=True,
                             hide_index=True)
            if j.get("event_risk"):
                st.markdown("#### Event risk")
                st_df(pd.DataFrame(j["event_risk"]), use_container_width=True,
                             hide_index=True)
            if j.get("contradictions"):
                st.markdown("#### What would make the technical signal wrong")
                for b in j["contradictions"]:
                    st.markdown(f"• {b}")
            if j.get("what_would_change_my_mind"):
                st.markdown("#### Falsifiers")
                for b in j["what_would_change_my_mind"]:
                    st.markdown(f"• {b}")
            with st.expander("Raw model output"):
                st.code(brief.get("raw", "")[:4000])

        st.markdown("")
        st.markdown("#### Headlines")
        if not news_df.empty:
            show = news_df.copy()
            show["ts"] = show["ts"].apply(
                lambda t: t.strftime("%d %b %H:%M") if isinstance(t, datetime) else "")
            st_df(show[["ts", "source", "title", "score", "events"]].style.format(
                {"score": "{:+.2f}"}), use_container_width=True, hide_index=True, height=420)
        else:
            st.info("No headlines reachable. The news RSS layer needs either server-side "
                    "internet or the browser bridge.")
            if res.get("errors"):
                with st.expander("Feed errors"):
                    for e in res["errors"][:20]:
                        st.code(e)

    with c2:
        st.markdown("#### Event detection")
        if not news_df.empty:
            ev = ra.detect_events(news_df)
            if not ev.empty:
                st_df(ev.style.format({"avg_score": "{:+.2f}", "impact": "{:.2f}"}),
                             use_container_width=True, hide_index=True)
            else:
                st.caption("No scheduled-event language in the current flow.")
        st.markdown("#### Deterministic scoring")
        st.caption(
            "A gold-specific lexicon scores every headline (rate cuts, real yields, "
            "safe-haven demand, central-bank buying …). It runs with or without an LLM and "
            "is blended 35/65 into the final bias when a model is available.")
        st.markdown("#### Context sent to the model")
        try:
            sig = se.current_signal(bundle, df, p)
            st.code(ra.build_context(sig, bundle, market["spot"].get("price")), language="text")
        except Exception as e:
            st.code(str(e))


def tab_data(market: dict, bundle: dict, cfg: dict):
    st.markdown("### Data provenance & diagnostics")
    frames = market["frames"]
    rows = []
    for a, fr in frames.items():
        rows.append({
            "asset": ASSETS.get(a, {}).get("label", a),
            "source": fr.source,
            "kind": fr.kind.upper(),
            "bars": len(fr.df),
            "from": str(fr.df.index[0])[:16],
            "to": str(fr.df.index[-1])[:16],
            "last": round(float(fr.df["close"].iloc[-1]), 3),
            "note": fr.note,
        })
    st_df(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    st.markdown("")
    a, b = st.columns([1, 1])
    with a:
        st.markdown("#### Provider log (this load)")
        lg = market["log"].to_df()
        st_df(lg if not lg.empty else pd.DataFrame([{"info": "no attempts logged"}]),
                     use_container_width=True, hide_index=True, height=300)
    with b:
        st.markdown("#### Transport")
        st.markdown(
            f"""
<div class="card">
  <div class="mono" style="font-size:12px; line-height:1.8;">
    server-side internet: <b>{'available' if dfeed.server_has_network() else 'BLOCKED'}</b><br>
    browser bridge: <b>{'available' if dfeed.bridge_available() else 'not installed'}</b><br>
    loaded at: {market['ts'].strftime('%Y-%m-%d %H:%M:%S UTC')}<br>
    interval: {market['cfg']['interval']} &nbsp;•&nbsp; requested bars: {market['cfg']['bars']}
  </div>
</div>""", unsafe_allow_html=True)
        st.caption(
            "When the server has no outbound internet the browser bridge fetches through the "
            "user's browser: tokenised-gold venues (PAXG / XAUt) for OHLCV and CORS relays "
            "for the macro universe. With unrestricted internet the app prefers direct "
            "Yahoo / Stooq / broker endpoints server-side.")

    st.markdown("#### Browser bridge diagnostic")
    st.caption(
        "Runs one small fetch inside your browser and prints the raw response. "
        "This is the definitive test of whether the bridge can reach market data "
        "from your network - some exchanges geo-block, and corporate proxies "
        "sometimes strip CORS headers.")
    bc1, bc2, bc3 = st.columns([1, 1, 2])
    if bc1.button("Test: Binance PAXG ticker"):
        st.session_state["_bridge_wait"] = {}
        st.session_state["_bridge_budget"] = {"start": time.time()}
        st.session_state["_btest"] = ("binance", st.session_state.get("_bn", 0) + 1)
        st.session_state["_bn"] = st.session_state.get("_bn", 0) + 1
        st.rerun()
    if bc2.button("Test: Yahoo via CORS relay"):
        st.session_state["_bridge_wait"] = {}
        st.session_state["_bridge_budget"] = {"start": time.time()}
        st.session_state["_btest"] = ("relay", st.session_state.get("_bn", 0) + 1)
        st.session_state["_bn"] = st.session_state.get("_bn", 0) + 1
        st.rerun()
    bt_ = st.session_state.get("_btest")
    if bt_:
        kind, nonce = bt_
        if kind == "binance":
            url = "https://api.binance.com/api/v3/ticker/price?symbol=PAXGUSDT"
        else:
            url = ("https://api.allorigins.win/raw?url=" +
                   dfeed.urllib.parse.quote(
                       "https://query1.finance.yahoo.com/v8/finance/chart/GC%3DF"
                       "?interval=60m&range=5d", safe=""))
        txt = dfeed.bridge_get(url, key=f"btest_{kind}_{nonce}", timeout_ms=10000, wait=True)
        if txt:
            st.success(f"Bridge OK — {len(txt)} bytes received")
            st.code(txt[:400])
        elif not dfeed.bridge_available():
            st.error("Bridge unavailable: `streamlit-js-eval` is not installed "
                     "or GOLD_TIMING_NO_BRIDGE is set.")
        else:
            st.error("Bridge returned nothing. The fetch was blocked (CORS, geo-block, "
                     "ad-blocker or offline browser). Try the other test, or run the app "
                     "where the server itself has internet.")

    st.markdown("")
    st.markdown("#### Recent bars")
    d = frames["XAUUSD"].df.tail(40).copy()
    d.index = d.index.strftime("%Y-%m-%d %H:%M")
    st_df(d[["open", "high", "low", "close", "volume"]].round(2),
                 use_container_width=True)

    st.markdown("---")
    st.markdown(
        """
<div class="warn">
<b>Research and education only. Not investment advice.</b>
This system quantifies entry timing; it does not know your fill quality, your broker's book,
your tax situation, or whether you will actually follow the stop. Backtested statistics are
computed on historical data with explicit assumptions about spread, commission and bar-level
fills, and they are not a forecast. Tokenised-gold venues track spot but are not spot.
Never risk capital you cannot afford to lose.
</div>""", unsafe_allow_html=True)


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------

def main():
    cfg = sidebar()
    market = load_market(cfg)
    frames = market["frames"]
    xau = frames["XAUUSD"]

    df = xau.df
    interval = cfg["interval"]
    htf_iv = HTF_MAP[interval]
    if htf_iv == interval:
        htf = df
    else:
        htf = (df.resample(htf_iv.replace("m", "min").replace("1d", "1D"))
                 .agg({"open": "first", "high": "max", "low": "min",
                       "close": "last", "volume": "sum"}).dropna())

    # wide price matrix on the primary index
    prices = dfeed.align_universe({k: v.df for k, v in frames.items()}, "XAUUSD")
    proxies = {}
    for a in ("XAGUSD", "DXY", "SPX", "VIX", "WTI", "COPPER", "US10Y"):
        if a in frames and a != "XAUUSD":
            sub = frames[a].df.reindex(df.index).ffill()
            proxies[a] = sub

    p: Params = cfg["params"]
    pkey = tuple(sorted(p.as_dict().items()))

    try:
        proxies_json = json.dumps({k: _key(v) for k, v in proxies.items()}) if proxies else "{}"
        with st.spinner("Running models and walk-forward timing research…"):
            res = compute_all(_key(df), _key(htf), _key(prices), proxies_json, pkey, interval)
        bundle = res["bundle"]
    except Exception as e:
        import traceback
        st.error(f"Engine error: {e}")
        st.code(traceback.format_exc())
        return

    render_header(market, cfg)
    st.markdown("")

    news_state = st.session_state.get("research")
    news_bias = ra.resolve_bias(
        (news_state or {}).get("brief"), (news_state or {}).get("det", {}) or
        {"bias": 0.0, "confidence": 0.0, "event_penalty": 1.0}) if news_state else None

    t1, t2, t3, t4, t5 = st.tabs(
        ["🎯 Signal desk", "🧬 Models", "📊 Timing accuracy", "🗞️ Research agent",
         "🔧 Data & diagnostics"])

    with t1:
        tab_signal(market, bundle, cfg, news_bias)
    with t2:
        tab_models(market, bundle, cfg)
    with t3:
        tab_timing(market, bundle, cfg, res)
    with t4:
        tab_research(market, bundle, cfg, news_bias)
    with t5:
        tab_data(market, bundle, cfg)


if __name__ == "__main__":
    main()
