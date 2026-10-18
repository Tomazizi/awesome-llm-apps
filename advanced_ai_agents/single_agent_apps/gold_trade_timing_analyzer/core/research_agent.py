"""
Market & news research agent.

Two layers:

1. Deterministic layer (always runs, no key needed)
   - pulls RSS from metals / macro desks,
   - classifies each headline against a gold-specific lexicon,
   - detects scheduled-event risk (FOMC, CPI, NFP, geopolitical shocks) and
     returns a bias in [-1, 1] plus a position-size penalty.

2. LLM layer (optional)
   - given the headlines *and* the quantitative context (price, regime, driver
     z-scores, the active CISD, reversal risk), asks a model for a structured
     research brief: macro narrative, per-headline catalyst scoring, event flags,
     a directional bias with confidence, and - importantly - an explicit list of
     reasons the technical signal could be wrong.

The LLM never overrides the engine. It contributes one weighted component
(`w_news`) and an event-risk multiplier.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .config import NEWS_FEEDS, EVENT_KEYWORDS
from .data_feeds import fetch_feed

# ----------------------------------------------------------------------------
# RSS
# ----------------------------------------------------------------------------


def _txt(node) -> str:
    return (node.text or "").strip() if node is not None else ""


def parse_feed(xml: str, source: str, limit: int = 25) -> List[dict]:
    out: List[dict] = []
    if not xml:
        return out
    try:
        root = ET.fromstring(xml)
    except Exception:
        # tolerate feeds with a leading BOM or stray prolog
        try:
            root = ET.fromstring(xml.lstrip("\ufeff").strip())
        except Exception:
            return out
    ns = {"a": "http://www.w3.org/2005/Atom"}
    items = root.findall(".//item")
    if not items:
        items = root.findall(".//a:entry", ns)
    for it in items[:limit]:
        def find(tag):
            for t in (tag, f"a:{tag}"):
                n = it.find(t, ns) if "a:" in t else it.find(t)
                if n is not None:
                    return n
            return None
        title = _txt(find("title"))
        link = _txt(find("link"))
        if not link:
            ln = find("link")
            link = ln.get("href", "") if ln is not None else ""
        pub = _txt(find("pubDate")) or _txt(find("updated")) or _txt(find("published"))
        desc = _txt(find("description")) or _txt(find("summary"))
        desc = re.sub(r"<[^>]+>", " ", desc)
        desc = re.sub(r"\s+", " ", desc).strip()
        ts = None
        for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z",
                    "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S"):
            try:
                ts = datetime.strptime(pub.strip(), fmt)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                break
            except Exception:
                continue
        if title:
            out.append({"source": source, "title": title, "summary": desc[:400],
                        "link": link, "ts": ts})
    return out


def fetch_news(
    feeds: Optional[List[Tuple[str, str]]] = None,
    use_browser: bool = True,
    limit_per_feed: int = 18,
    max_total: int = 90,
) -> Tuple[pd.DataFrame, List[str]]:
    feeds = feeds or NEWS_FEEDS
    rows: List[dict] = []
    errors: List[str] = []
    for name, url in feeds:
        key = "feed_" + re.sub(r"[^A-Za-z0-9]", "", name)[:24]
        try:
            xml = fetch_feed(url, key=key, use_browser=use_browser, timeout_ms=12000)
        except Exception as e:
            xml = None
            errors.append(f"{name}: {e}")
        items = parse_feed(xml or "", name, limit_per_feed)
        if not items:
            errors.append(f"{name}: no items")
        rows.extend(items)
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.drop_duplicates(subset=["title"]).sort_values("ts", ascending=False)
        df = df.head(max_total).reset_index(drop=True)
    return df, errors


# ----------------------------------------------------------------------------
# Deterministic classification
# ----------------------------------------------------------------------------

#: gold-specific lexicon. Positive weights are bullish XAU/USD.
LEXICON: Dict[str, float] = {
    "rate cut": +2.0, "dovish": +2.0, "easing": +1.6, "stimulus": +1.2,
    "inflation": +1.2, "cpi rose": +0.8, "hot inflation": +1.0, "stagflation": +1.2,
    "safe haven": +2.0, "safe-haven": +2.0, "geopolitical": +1.5, "war": +1.6,
    "conflict": +1.3, "uncertainty": +1.0, "recession": +1.4, "crisis": +1.4,
    "central bank buying": +2.4, "gold reserves": +1.8, "de-dollarisation": +2.0,
    "dedollarization": +2.0, "weaker dollar": +1.8, "dollar falls": +1.8,
    "dollar slips": +1.5, "yield falls": +1.4, "yields fall": +1.4,
    "real yields fall": +2.0, "etf inflow": +1.6, "record high": +1.2,
    "risk-off": +1.6, "bank failure": +1.8, "debt ceiling": +1.4,
    "rate hike": -2.0, "hawkish": -2.0, "tightening": -1.6, "higher for longer": -1.8,
    "strong dollar": -1.8, "dollar rallies": -1.6, "dollar surges": -1.8,
    "yields rise": -1.4, "yield rises": -1.4, "real yields rise": -2.0,
    "disinflation": -1.0, "soft landing": -0.8, "risk-on": -1.2,
    "profit taking": -1.2, "sell-off in gold": -2.2, "gold falls": -1.5,
    "etf outflow": -1.6, "ceasefire": -1.2, "peace deal": -1.2,
    "gold retreats": -1.4, "bearish": -1.6, "bullish": +1.6,
}


def score_headline(text: str) -> float:
    t = (text or "").lower()
    s = 0.0
    hits = 0
    for term, w in LEXICON.items():
        if term in t:
            s += w
            hits += 1
    if hits == 0:
        return 0.0
    return float(np.tanh(s / (2.0 * math.sqrt(hits))))


import math  # noqa: E402  (kept local to the scoring helper above)


def classify_news(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df.assign(score=0.0, events="")
    out = df.copy()
    out["text"] = (out["title"].fillna("") + " " + out["summary"].fillna("")).str.lower()
    out["score"] = out["text"].apply(score_headline)
    out["events"] = out["text"].apply(lambda t: ", ".join(
        ev for ev, kws in EVENT_KEYWORDS.items() if any(k in t for k in kws)))
    return out.drop(columns=["text"])


def detect_events(df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate event categories present in the news with an impact estimate."""
    rows = []
    if df.empty:
        return pd.DataFrame(columns=["event", "mentions", "avg_score", "impact"])
    for ev, kws in EVENT_KEYWORDS.items():
        mask = df["title"].fillna("").str.lower().apply(
            lambda t: any(k in t for k in kws)) | df["summary"].fillna("").str.lower().apply(
            lambda t: any(k in t for k in kws))
        n = int(mask.sum())
        if n:
            rows.append({
                "event": ev,
                "mentions": n,
                "avg_score": float(df.loc[mask, "score"].mean()) if "score" in df else 0.0,
                "impact": float(min(1.0, n / 6.0)),
            })
    out = pd.DataFrame(rows)
    if not out.empty:
        out = out.sort_values("mentions", ascending=False).reset_index(drop=True)
    return out


def news_bias(df: pd.DataFrame) -> Dict[str, float]:
    """Deterministic aggregate bias in [-1, 1] plus an event-risk multiplier."""
    if df.empty or "score" not in df.columns:
        return {"bias": 0.0, "confidence": 0.0, "event_penalty": 1.0, "n": 0}
    w = np.exp(-np.arange(len(df)) / 25.0)          # recency weighting
    s = df["score"].values
    bias = float(np.tanh(np.sum(w * s) / max(1e-9, np.sum(w) * 1.5)))
    confidence = float(np.clip(np.mean(np.abs(s)) / 1.5, 0, 1))
    ev = detect_events(df)
    penalty = 1.0
    if not ev.empty:
        hi = ev[ev["event"].isin(["FOMC / Fed decision", "CPI / Inflation", "Labour market"])]
        if not hi.empty:
            penalty = float(np.clip(1.0 - 0.20 * hi["impact"].max(), 0.7, 1.0))
    return {"bias": bias, "confidence": confidence, "event_penalty": penalty,
            "n": int(len(df))}


# ----------------------------------------------------------------------------
# LLM research brief
# ----------------------------------------------------------------------------

SYSTEM_PROMPT = """You are a sell-side precious-metals strategist and macro
trader. You are given live gold (XAU/USD) market structure readings from a
quantitative engine plus the current news flow. You are rigorous, numerate and
explicitly uncertain. You never invent data: if something is not supplied, say
so. You always answer with a single JSON object and nothing else.

The JSON schema is:
{
  "macro_regime": "one short sentence naming the dominant driver of gold right now",
  "narrative": ["3 to 5 bullets, each <= 25 words, each tied to supplied data or headlines"],
  "catalysts": [{"headline": "<exact headline text>", "direction": "bullish|bearish|neutral",
                 "magnitude": <0.0-1.0>, "novelty": <0.0-1.0>, "why": "<<= 15 words>"}],
  "event_risk": [{"event": "<name>", "timing": "<unknown|today|this week>", "impact": <0.0-1.0>,
                  "guidance": "stand aside|reduce size|trade through"}],
  "bias": <float -1.0 to 1.0, bullish gold positive>,
  "confidence": <float 0.0 to 1.0>,
  "contradictions": ["reasons the engine's technical signal could be wrong, <= 20 words each"],
  "what_would_change_my_mind": ["<= 20 words each"]
}"""

USER_TEMPLATE = """QUANTITATIVE CONTEXT (engine output, last completed bar)
{context}

HEADLINES (most recent first)
{headlines}

Produce the JSON brief now. Bias must respect the sign convention: positive =
bullish XAU/USD."""


def build_context(signal: dict, bundle: dict, spot: Optional[float] = None) -> str:
    panel = bundle["panel"]
    i = -1
    mn = bundle.get("mn", {})
    ssmt = bundle.get("ssmt", {})
    rev = bundle.get("rev") or {}
    lines = [
        f"Last bar: {panel.index[i]} UTC",
        f"Close: {panel['close'].iloc[i]:.2f}" + (f" | Live spot: {spot:.2f}" if spot else ""),
        f"Session: {panel['session'].iloc[i]} | ATR(14): {panel['atr'].iloc[i]:.2f}",
        f"HTF bias: {panel['htf_bias'].iloc[i]:+.2f} (-1 bearish .. +1 bullish)",
        f"ETS long / short: {signal.get('score_long', float('nan')):.1f} / {signal.get('score_short', float('nan')):.1f}",
        f"tCISD long / short: {panel['tcisd_long'].iloc[i]:.0f} / {panel['tcisd_short'].iloc[i]:.0f}",
        f"Fibonacci long / short: {panel['fib_long'].iloc[i]:.0f} / {panel['fib_short'].iloc[i]:.0f}",
        f"Market-neutral z: {panel['mn_z'].iloc[i] if 'mn_z' in panel else float('nan'):+.2f}"
        f" | fair-value z: {panel['mn_fv_z'].iloc[i] if 'mn_fv_z' in panel else float('nan'):+.2f}",
        f"Residual half-life: {mn.get('halflife', float('nan'))} bars | Hurst: {mn.get('hurst', float('nan'))}"
        f" | ADF t: {mn.get('adf_t', float('nan'))}",
        f"Driver R^2: {mn.get('r2', float('nan'))} | betas: {mn.get('betas', {})}",
        f"SSMT divergence: {ssmt.get('ssmt_div', 0.0):+.2f} (-1 bearish .. +1 bullish)"
        f" | relative strength: {ssmt.get('ssmt_rel_strength', 0.0):+.2f}",
        f"Exhaustion up / down: {panel['exh_up'].iloc[i]:.0f} / {panel['exh_dn'].iloc[i]:.0f}",
        f"Divergence bear / bull: {panel['div_bear'].iloc[i]:.2f} / {panel['div_bull'].iloc[i]:.2f}",
        f"Reversal risk: {panel['reversal_risk'].iloc[i]:.2%}"
        + (f" (model out-of-sample AUC {rev.get('auc', float('nan')):.3f})" if rev.get("auc") == rev.get("auc") else ""),
    ]
    return "\n".join(f"- {l}" for l in lines)


def llm_brief(
    provider: str,
    model: str,
    api_key: str,
    news_df: pd.DataFrame,
    context: str,
    base_url: Optional[str] = None,
) -> Dict[str, object]:
    from .llm import complete, extract_json

    if news_df.empty:
        headlines = "(no headlines available - the news transport is unreachable)"
    else:
        headlines = "\n".join(
            f"{k}. [{r['source']}] {r['title']} -- {(r['summary'] or '')[:180]}"
            for k, r in news_df.head(30).iterrows()
        )
    user = USER_TEMPLATE.format(context=context, headlines=headlines)
    text = complete(provider, model, api_key, SYSTEM_PROMPT, user, base_url=base_url)
    data = extract_json(text) or {}
    return {"raw": text, "json": data}


def resolve_bias(brief: Optional[dict], deterministic: Dict[str, float]) -> Dict[str, float]:
    """Blend the LLM bias with the lexicon bias; the model gets 65% when present."""
    if not brief or not brief.get("json"):
        return {
            "bias": deterministic["bias"],
            "confidence": deterministic["confidence"],
            "event_penalty": deterministic["event_penalty"],
            "source": "lexicon",
        }
    j = brief["json"]
    try:
        lb = float(np.clip(float(j.get("bias", 0.0)), -1, 1))
    except Exception:
        lb = 0.0
    try:
        lc = float(np.clip(float(j.get("confidence", 0.4)), 0, 1))
    except Exception:
        lc = 0.4
    w = 0.65 * lc
    bias = float(np.clip(w * lb + (1 - w) * deterministic["bias"], -1, 1))
    pen = deterministic["event_penalty"]
    for ev in (j.get("event_risk") or []):
        g = str(ev.get("guidance", "")).lower()
        imp = float(ev.get("impact", 0.5) or 0.5)
        if "stand aside" in g:
            pen = min(pen, 1.0 - 0.30 * imp)
        elif "reduce size" in g:
            pen = min(pen, 1.0 - 0.15 * imp)
    return {"bias": bias, "confidence": float(np.clip(w + 0.35 * lc, 0, 1)),
            "event_penalty": float(np.clip(pen, 0.6, 1.0)), "source": "llm+lexicon"}
