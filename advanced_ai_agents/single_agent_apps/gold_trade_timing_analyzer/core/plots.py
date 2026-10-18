"""Plotly figure builders. Kept separate so the analytics stay UI-free."""
from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

GOLD = "#F5C542"
GREEN = "#26A69A"
RED = "#EF5350"
BLUE = "#42A5F5"
GRID = "rgba(255,255,255,0.07)"
PAPER = "rgba(0,0,0,0)"

_LAYOUT = dict(
    template="plotly_dark",
    paper_bgcolor=PAPER,
    plot_bgcolor=PAPER,
    margin=dict(l=8, r=8, t=30, b=8),
    font=dict(size=11),
    hovermode="x unified",
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
)


def _apply(fig, height: int = 460, **kw):
    lay = dict(_LAYOUT)
    lay.update(kw)
    fig.update_layout(height=height, **lay)
    fig.update_xaxes(gridcolor=GRID, showspikes=False)
    fig.update_yaxes(gridcolor=GRID)
    return fig


# ----------------------------------------------------------------------------


def chart_price(
    df: pd.DataFrame,
    bundle: Dict[str, object],
    trade=None,
    lookback: int = 220,
    show_fvg: bool = True,
    show_fib: bool = True,
    show_cisd: bool = True,
) -> go.Figure:
    from .structure import fib_levels_for_plot
    from .config import Params

    p: Params = bundle.get("params", Params())
    d = df.tail(lookback)
    panel = bundle["panel"].reindex(d.index)

    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True, row_heights=[0.78, 0.22], vertical_spacing=0.02
    )
    fig.add_trace(
        go.Candlestick(x=d.index, open=d["open"], high=d["high"], low=d["low"], close=d["close"],
                       increasing_line_color=GREEN, decreasing_line_color=RED, name="XAU/USD"),
        row=1, col=1,
    )

    if show_fvg:
        for g in (bundle.get("fvg") or [])[-40:]:
            if g.ts < d.index[0]:
                continue
            x1 = g.ts
            x2 = df.index[min(g.filled_at if g.filled_at else len(df) - 1, len(df) - 1)]
            if x2 < x1:
                x2 = d.index[-1]
            fig.add_shape(
                type="rect", x0=x1, x1=x2, y0=g.lo, y1=g.hi, row=1, col=1,
                fillcolor="rgba(66,165,245,0.16)" if g.direction > 0 else "rgba(239,83,80,0.14)",
                line=dict(width=0), layer="below",
            )

    if show_fib:
        for lv in fib_levels_for_plot(bundle.get("legs", {}), p):
            if lv["leg"] != "primary":
                continue
            if not (d["low"].min() * 0.97 <= lv["price"] <= d["high"].max() * 1.03):
                continue
            col = "rgba(245,197,66,0.65)" if lv["kind"] == "retr" else "rgba(156,204,101,0.5)"
            fig.add_hline(
                y=lv["price"], row=1, col=1, line=dict(color=col, width=1,
                                                       dash="dot" if lv["kind"] == "retr" else "dash"),
                annotation_text=f"{lv['ratio']}" , annotation_position="right",
                annotation_font_size=9,
            )
        prim = (bundle.get("legs") or {}).get("primary")
        if prim is not None:
            for node in p.fib_time_nodes:
                ts_i = prim.anchor_i + node
                if ts_i < len(df) and df.index[ts_i] >= d.index[0]:
                    fig.add_vline(x=df.index[ts_i], row=1, col=1,
                                  line=dict(color="rgba(255,255,255,0.10)", width=1))

    if show_cisd:
        evs = [e for e in (bundle.get("cisd", {}).get("events") or []) if e.sweep_ts >= d.index[0]]
        for e in evs[-12:]:
            col = GREEN if e.direction > 0 else RED
            fig.add_trace(
                go.Scatter(
                    x=[e.sweep_ts], y=[e.sweep_price], mode="markers",
                    marker=dict(symbol="diamond", size=11, color=col,
                                line=dict(color="white", width=1)),
                    name=f"liquidity sweep ({'buy' if e.direction>0 else 'sell'} side)",
                    showlegend=False,
                ), row=1, col=1,
            )
            if e.invalidated_at is None:
                fig.add_hline(y=e.delivery_open, row=1, col=1,
                              line=dict(color=col, width=1.4),
                              annotation_text="CISD", annotation_position="left",
                              annotation_font_size=9)

    if trade is not None:
        col = GREEN if trade.direction > 0 else RED
        fig.add_hline(y=trade.entry, row=1, col=1, line=dict(color=BLUE, width=1.6),
                      annotation_text=f"ENTRY {trade.entry:.2f}", annotation_position="left")
        fig.add_hline(y=trade.stop, row=1, col=1, line=dict(color=RED, width=1.4, dash="dash"),
                      annotation_text=f"STOP {trade.stop:.2f}", annotation_position="left")
        fig.add_hline(y=trade.tp1, row=1, col=1, line=dict(color=GREEN, width=1.2, dash="dash"),
                      annotation_text=f"TP1 {trade.tp1:.2f}", annotation_position="left")
        fig.add_hline(y=trade.tp2, row=1, col=1, line=dict(color=GREEN, width=1.0, dash="dot"),
                      annotation_text=f"TP2 {trade.tp2:.2f}", annotation_position="left")

    # volume / activity pane
    vol = d["volume"] if "volume" in d else pd.Series(0, index=d.index)
    if vol.abs().sum() > 0:
        colors = [GREEN if c >= o else RED for o, c in zip(d["open"], d["close"])]
        fig.add_trace(go.Bar(x=d.index, y=vol, marker_color=colors, opacity=0.5,
                             showlegend=False), row=2, col=1)
    else:
        fig.add_trace(go.Scatter(x=d.index, y=panel["atr"], line=dict(color=GOLD, width=1),
                                 name="ATR(14)"), row=2, col=1)

    return _apply(fig, height=560, xaxis_rangeslider_visible=False,
                  hovermode="x unified", title=None)


def chart_components(panel: pd.DataFrame, scored: pd.DataFrame, lookback: int = 240) -> go.Figure:
    idx = panel.index[-lookback:]
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.55, 0.45],
                        vertical_spacing=0.05, subplot_titles=("Entry Timing Score", "Model components (long / short)"))
    fig.add_trace(go.Scatter(x=idx, y=scored["ets_long"].reindex(idx), name="ETS long",
                             line=dict(color=GREEN, width=1.6)), row=1, col=1)
    fig.add_trace(go.Scatter(x=idx, y=scored["ets_short"].reindex(idx), name="ETS short",
                             line=dict(color=RED, width=1.6)), row=1, col=1)
    fig.add_hline(y=55, line=dict(color="rgba(255,255,255,0.25)", dash="dash"),
                  annotation_text="min conviction", row=1, col=1)
    fig.add_hline(y=75, line=dict(color="rgba(245,197,66,0.4)", dash="dash"),
                  annotation_text="tier A", row=1, col=1)

    for name, col in [("tCISD", GOLD), ("Fibonacci", BLUE), ("MarketNeutral", "#AB47BC"),
                      ("SSMT", "#26C6DA"), ("ExhaustionInv", "#FFA726"), ("ReversalInv", "#8D6E63")]:
        if name == "ExhaustionInv":
            y = 100 - panel["exh_up"].reindex(idx) - panel["exh_dn"].reindex(idx) / 2
        elif name == "ReversalInv":
            y = (1 - panel["reversal_risk"].reindex(idx)) * 100
        else:
            y = panel[f"{'tcisd_long' if name=='tCISD' else 'fib_long' if name=='Fibonacci' else 'mn_long' if name=='MarketNeutral' else 'ssmt_long'}"].reindex(idx)
        fig.add_trace(go.Scatter(x=idx, y=y, name=name, line=dict(width=1.1, color=col),
                                 opacity=0.85), row=2, col=1)
    return _apply(fig, height=480)


def chart_equity(trades: pd.DataFrame) -> go.Figure:
    if trades.empty:
        return _apply(go.Figure(), height=300)
    eq = trades.set_index("ts")["r"].cumsum()
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=eq.index, y=eq.values, mode="lines", line=dict(color=GOLD, width=2),
                             fill="tozeroy", fillcolor="rgba(245,197,66,0.10)", name="Cumulative R"))
    peak = np.maximum.accumulate(eq.values)
    fig.add_trace(go.Scatter(x=eq.index, y=eq.values - peak, mode="lines",
                             line=dict(color=RED, width=1), name="Drawdown (R)"))
    return _apply(fig, height=340, title="Equity curve in R-multiples (net of spread + commission)")


def chart_mae_mfe(trades: pd.DataFrame) -> go.Figure:
    if trades.empty:
        return _apply(go.Figure(), height=340)
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=-trades["mae"], y=trades["mfe"], mode="markers",
        marker=dict(size=8, color=trades["r"], colorscale="RdYlGn", cmid=0,
                    line=dict(width=0.5, color="white"), showscale=True,
                    colorbar=dict(title="R")),
        text=[f"{t}<br>score {s:.0f}" for t, s in zip(trades["dir"], trades["score"])],
        name="trades",
    ))
    fig.add_shape(type="line", x0=0, y0=0, x1=1.2, y1=1.2,
                  line=dict(color="rgba(255,255,255,0.25)", dash="dash"))
    return _apply(fig, height=380, title="Entry quality: adverse excursion vs favourable excursion (R)",
                  xaxis_title="MAE (R, adverse)", yaxis_title="MFE (R, favourable)")


def chart_delay(delay: pd.DataFrame) -> go.Figure:
    if delay.empty:
        return _apply(go.Figure(), height=300)
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(go.Bar(x=delay["delay_bars"], y=delay["mean_r"], name="Mean R",
                         marker_color=[GREEN if v > 0 else RED for v in delay["mean_r"]]),
                  secondary_y=False)
    fig.add_trace(go.Scatter(x=delay["delay_bars"], y=delay["mean_mae_r"], name="Mean MAE (R)",
                             line=dict(color=GOLD, width=2, dash="dot")), secondary_y=True)
    fig.add_hline(y=0, line=dict(color="rgba(255,255,255,0.3)"))
    return _apply(fig, height=340, title="Optimal entry lag: expectancy vs bars waited after the signal",
                  xaxis_title="bars delayed", yaxis_title="mean R")


def chart_calibration(cal: pd.DataFrame) -> go.Figure:
    if cal.empty:
        return _apply(go.Figure(), height=300)
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    labels = [f"{lo:.0f}-{hi:.0f}" for lo, hi in zip(cal["score_lo"], cal["score_hi"])]
    fig.add_trace(go.Bar(x=labels, y=cal["mean_r"], name="Mean R",
                         marker_color=GOLD), secondary_y=False)
    fig.add_trace(go.Scatter(x=labels, y=cal["win_rate"], name="Win rate",
                             line=dict(color=BLUE, width=2)), secondary_y=True)
    return _apply(fig, height=330, title="Score calibration: does a higher Entry Timing Score pay?",
                  xaxis_title="score bucket", yaxis_title="mean R")


def chart_attribution(attr: pd.DataFrame) -> go.Figure:
    if attr.empty:
        return _apply(go.Figure(), height=300)
    d = attr.dropna(subset=["ic"])
    fig = go.Figure()
    fig.add_trace(go.Bar(
        y=d["model"], x=d["ic"], orientation="h",
        marker_color=[GREEN if v > 0 else RED for v in d["ic"]],
        text=[f"IC {v:+.3f} (t {t:+.1f})" if np.isfinite(t) else f"IC {v:+.3f}"
              for v, t in zip(d["ic"], d["ic_t"])],
        textposition="outside",
    ))
    fig.add_vline(x=0, line=dict(color="rgba(255,255,255,0.3)"))
    return _apply(fig, height=340, title="Alpha attribution: Spearman IC of each model vs realised R",
                  xaxis_title="information coefficient")


def chart_residual(mn: Dict[str, object], lookback: int = 300) -> go.Figure:
    z = mn.get("mn_z")
    if z is None:
        return _apply(go.Figure(), height=260)
    z = z.dropna().tail(lookback)
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=z.index, y=z.values, line=dict(color="#AB47BC", width=1.6),
                             name="market-neutral residual z"))
    fv = mn.get("mn_fv_z")
    if fv is not None:
        fv = fv.dropna().tail(lookback)
        fig.add_trace(go.Scatter(x=fv.index, y=fv.values, line=dict(color=BLUE, width=1.2),
                                 name="fair-value residual z"))
    for lvl in (2, -2):
        fig.add_hline(y=lvl, line=dict(color="rgba(255,255,255,0.2)", dash="dash"))
    fig.add_hline(y=0, line=dict(color="rgba(255,255,255,0.35)"))
    return _apply(fig, height=300, title="Market-neutral residual (beta-hedged against the driver basket)")


def chart_rsi_divergence(df: pd.DataFrame, div: pd.DataFrame, lookback: int = 220) -> go.Figure:
    d = df.tail(lookback)
    dv = div.reindex(d.index)
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.6, 0.4],
                        vertical_spacing=0.03)
    fig.add_trace(go.Scatter(x=d.index, y=d["close"], line=dict(color=GOLD, width=1.6),
                             name="close"), row=1, col=1)
    bear = dv[dv["div_bear"] > 0]
    bull = dv[dv["div_bull"] > 0]
    fig.add_trace(go.Scatter(x=bear.index, y=d["close"].reindex(bear.index), mode="markers",
                             marker=dict(symbol="triangle-down", size=11, color=RED),
                             name="bearish divergence"), row=1, col=1)
    fig.add_trace(go.Scatter(x=bull.index, y=d["close"].reindex(bull.index), mode="markers",
                             marker=dict(symbol="triangle-up", size=11, color=GREEN),
                             name="bullish divergence"), row=1, col=1)
    fig.add_trace(go.Scatter(x=d.index, y=dv["rsi"], line=dict(color=BLUE, width=1.3),
                             name="RSI(14)"), row=2, col=1)
    fig.add_hline(y=70, row=2, col=1, line=dict(color="rgba(255,255,255,0.2)", dash="dash"))
    fig.add_hline(y=30, row=2, col=1, line=dict(color="rgba(255,255,255,0.2)", dash="dash"))
    return _apply(fig, height=430, title="Momentum divergence (Spearman-tested)")


def chart_exhaustion(exh: pd.DataFrame, lookback: int = 240) -> go.Figure:
    d = exh.tail(lookback)
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=d.index, y=d["exh_up"], line=dict(color=RED, width=1.4),
                             fill="tozeroy", fillcolor="rgba(239,83,80,0.12)",
                             name="up-move exhaustion"))
    fig.add_trace(go.Scatter(x=d.index, y=-d["exh_dn"], line=dict(color=GREEN, width=1.4),
                             fill="tozeroy", fillcolor="rgba(38,166,154,0.12)",
                             name="down-move exhaustion"))
    fig.add_hline(y=70, line=dict(color="rgba(255,255,255,0.25)", dash="dash"),
                  annotation_text="climax threshold")
    fig.add_hline(y=-70, line=dict(color="rgba(255,255,255,0.25)", dash="dash"))
    return _apply(fig, height=300, title="Exhaustion index (0-100, direction split)")


def chart_timing_heatmap(trades: pd.DataFrame) -> go.Figure:
    if trades.empty:
        return _apply(go.Figure(), height=300)
    piv = trades.pivot_table(index="hour", columns="dir", values="r", aggfunc="mean")
    cnt = trades.pivot_table(index="hour", columns="dir", values="r", aggfunc="size")
    fig = go.Figure(go.Heatmap(
        z=piv.values, x=list(piv.columns), y=list(piv.index),
        colorscale="RdYlGn", zmid=0, colorbar=dict(title="mean R"),
        text=cnt.reindex(piv.index).reindex(columns=piv.columns).values,
        texttemplate="n=%{text}",
    ))
    return _apply(fig, height=360, title="Mean R by hour of day and direction",
                  xaxis_title="direction", yaxis_title="hour (UTC)")


def chart_betas(rls: pd.DataFrame, lookback: int = 400) -> go.Figure:
    if rls is None or rls.empty:
        return _apply(go.Figure(), height=280)
    d = rls.dropna().tail(lookback)
    fig = go.Figure()
    for c in d.columns:
        fig.add_trace(go.Scatter(x=d.index, y=d[c], name=c.replace("b_", ""), line=dict(width=1.2)))
    fig.add_hline(y=0, line=dict(color="rgba(255,255,255,0.25)"))
    return _apply(fig, height=300, title="Time-varying driver betas (RLS, forgetting 0.995)")


def chart_gauge(value: float, title: str, color: str = GOLD) -> go.Figure:
    fig = go.Figure(go.Indicator(
        mode="gauge+number", value=float(value),
        number={"suffix": ""},
        gauge={
            "axis": {"range": [0, 100], "tickwidth": 1},
            "bar": {"color": color},
            "steps": [
                {"range": [0, 45], "color": "rgba(239,83,80,0.18)"},
                {"range": [45, 62], "color": "rgba(255,167,38,0.18)"},
                {"range": [62, 75], "color": "rgba(66,165,245,0.18)"},
                {"range": [75, 100], "color": "rgba(38,166,154,0.22)"},
            ],
            "threshold": {"line": {"color": "white", "width": 2}, "value": 55},
        },
        title={"text": title, "font": {"size": 12}},
    ))
    return _apply(fig, height=190, margin=dict(l=18, r=18, t=30, b=10))
