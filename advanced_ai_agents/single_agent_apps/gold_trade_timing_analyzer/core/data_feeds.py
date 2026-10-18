"""
Market data layer.

The engine never assumes *where* price data comes from. It walks an ordered
chain of providers and uses the first one that answers, recording the provenance
of every series it returns:

    tier 1  server-side HTTP (Yahoo, Stooq, Twelve Data, Alpha Vantage, Polygon,
            Finnhub, Tiingo, OANDA ...) - used when the process has outbound net.
    tier 2  *browser bridge* - the Streamlit component evaluates a fetch() inside
            the user's browser, which has unrestricted internet even when the
            server does not. This is what makes the app work inside a locked
            sandbox. Tokenised-gold venues (PAXG / XAUt) give real, liquid,
            gold-linked OHLCV; CORS relays give the macro universe.
    tier 3  cached CSV on disk (drop your broker export in `data/`).
    tier 4  synthetic series, always clearly labelled SYNTHETIC.

`kind` is carried on every Frame so the UI can never imply synthetic numbers
are real.
"""
from __future__ import annotations

import io
import json
import math
import os
import time
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .config import ASSETS, SYNTH

# ----------------------------------------------------------------------------
# Optional Streamlit import - the engine must import head-less for tests.
# ----------------------------------------------------------------------------
try:  # pragma: no cover
    import streamlit as st
except Exception:  # pragma: no cover
    st = None

try:
    import requests
except Exception:  # pragma: no cover
    requests = None


OHLC = ["open", "high", "low", "close", "volume"]


@dataclass
class Frame:
    """An OHLCV series plus its provenance."""

    df: pd.DataFrame
    asset: str
    interval: str
    source: str
    kind: str = "real"           # 'real' | 'proxy' | 'synthetic'
    note: str = ""
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def is_real(self) -> bool:
        return self.kind in ("real", "proxy")

    def __len__(self) -> int:
        return len(self.df)


@dataclass
class FetchLog:
    rows: List[dict] = field(default_factory=list)

    def add(self, asset: str, provider: str, ok: bool, detail: str = "", kind: str = ""):
        self.rows.append(
            {
                "asset": asset,
                "provider": provider,
                "status": "ok" if ok else "fail",
                "kind": kind,
                "detail": detail[:200],
                "ts": datetime.now(timezone.utc).strftime("%H:%M:%S"),
            }
        )

    def to_df(self) -> pd.DataFrame:
        return pd.DataFrame(self.rows)


# ----------------------------------------------------------------------------
# Transport
# ----------------------------------------------------------------------------

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

_CORS_RELAYS = [
    "https://api.allorigins.win/raw?url={u}",
    "https://api.codetabs.com/v1/proxy?quest={u}",
    "https://corsproxy.io/?url={u}",
]


def server_get(url: str, timeout: float = 12.0) -> Optional[str]:
    """Plain HTTP GET from the Python process. Returns None on any failure."""
    if requests is None:
        return None
    try:
        r = requests.get(url, headers={"User-Agent": _UA}, timeout=timeout)
        if r.status_code >= 400:
            return None
        return r.text
    except Exception:
        return None


_JS_FETCH = """(async () => {{
  const ac = new AbortController();
  const to = setTimeout(() => ac.abort(), {timeout});
  try {{
    const r = await fetch("{url}", {{signal: ac.signal, cache: "no-store", credentials: "omit"}});
    const t = await r.text();
    clearTimeout(to);
    if (!r.ok) return "__BRIDGE_ERR__HTTP" + r.status;
    return t;
  }} catch (e) {{
    clearTimeout(to);
    return "__BRIDGE_ERR__" + (e && e.message ? e.message : String(e));
  }}
}})()"""


def bridge_available() -> bool:
    """True when the browser bridge can be used.

    `GOLD_TIMING_NO_BRIDGE=1` disables it outright (CI / head-less AppTest runs,
    where there is no browser to fetch anything).
    """
    if os.environ.get("GOLD_TIMING_NO_BRIDGE"):
        return False
    try:
        import streamlit_js_eval  # noqa: F401
        return True
    except Exception:
        return False


#: Installed by the Streamlit layer. Called when a bridge value has not arrived
#: yet; it is expected to `st.rerun()` (which raises) or mark the wait as failed
#: and return normally. Keeping it out of this module means the data layer stays
#: importable head-less.
_WAIT_HOOK = None


def set_wait_hook(fn) -> None:
    global _WAIT_HOOK
    _WAIT_HOOK = fn


def bridge_get(url: str, key: str, timeout_ms: int = 15000, wait: bool = False) -> Optional[str]:
    """Fetch inside the user's browser and return the body as text.

    Returns None while the value is still in flight (the component posts its
    result and Streamlit reruns the script), or if the bridge is unavailable.
    With `wait=True` a Streamlit-supplied hook keeps the app alive until the
    payload lands or the wait budget is exhausted.
    """
    if not bridge_available():
        return None
    try:
        from streamlit_js_eval import streamlit_js_eval

        js = _JS_FETCH.format(url=url, timeout=int(timeout_ms))
        val = streamlit_js_eval(js_expressions=js, key=key)
    except Exception:
        return None
    if val is None and wait and _WAIT_HOOK is not None:
        _WAIT_HOOK(key, timeout_ms / 1000.0)
        try:
            val = streamlit_js_eval(js_expressions=js, key=key)
        except Exception:
            val = None
    if val is None:
        return None
    if isinstance(val, dict):
        val = val.get("value")
    if val is None:
        return None
    if isinstance(val, (list, dict)):
        val = json.dumps(val)
    if isinstance(val, str) and val.startswith("__BRIDGE_ERR__"):
        return None
    return str(val)


_JS_FETCH_MULTI = """(async () => {{
  const ac = new AbortController();
  const to = setTimeout(() => ac.abort(), {timeout});
  const urls = {mapping};
  const out = {{}};
  const jobs = Object.keys(urls).map(async (k) => {{
    try {{
      const r = await fetch(urls[k], {{signal: ac.signal, cache: "no-store", credentials: "omit"}});
      out[k] = r.ok ? await r.text() : "";
    }} catch (e) {{ out[k] = ""; }}
  }});
  try {{ await Promise.all(jobs); }} catch (e) {{}}
  clearTimeout(to);
  return JSON.stringify(out);
}})()"""


def bridge_get_multi(mapping: Dict[str, str], key: str, timeout_ms: int = 25000,
                     wait: bool = False) -> Optional[Dict[str, str]]:
    """Fetch many URLs concurrently inside the browser in one round trip."""
    if not bridge_available() or not mapping:
        return None
    try:
        from streamlit_js_eval import streamlit_js_eval

        js = _JS_FETCH_MULTI.format(mapping=json.dumps(mapping), timeout=int(timeout_ms))
        val = streamlit_js_eval(js_expressions=js, key=key)
    except Exception:
        return None
    if val is None and wait and _WAIT_HOOK is not None:
        _WAIT_HOOK(key, timeout_ms / 1000.0)
        try:
            val = streamlit_js_eval(js_expressions=js, key=key)
        except Exception:
            val = None
    if isinstance(val, dict):
        val = val.get("value")
    if not val:
        return None
    if isinstance(val, str):
        try:
            val = json.loads(val)
        except Exception:
            return None
    return {k: v for k, v in val.items() if v}


def get_text(
    url: str,
    key: str,
    log: Optional[FetchLog] = None,
    asset: str = "",
    provider: str = "",
    use_browser: bool = True,
    timeout_ms: int = 15000,
    wait: bool = False,
) -> Tuple[Optional[str], str]:
    """Try server-side first, then the browser bridge. Returns (text, via)."""
    txt = server_get(url, timeout=timeout_ms / 1000)
    if txt:
        if log:
            log.add(asset, provider + " (server)", True, f"{len(txt)} bytes", "real")
        return txt, "server"
    if use_browser:
        txt = bridge_get(url, key=key, timeout_ms=timeout_ms, wait=wait)
        if txt:
            if log:
                log.add(asset, provider + " (browser)", True, f"{len(txt)} bytes", "real")
            return txt, "browser"
    if log:
        log.add(asset, provider, False, "no response")
    return None, ""


def relay_urls(url: str) -> List[str]:
    enc = urllib.parse.quote(url, safe="")
    return [tpl.format(u=enc) for tpl in _CORS_RELAYS]


# ----------------------------------------------------------------------------
# Parsers
# ----------------------------------------------------------------------------


def _clean_df(df: pd.DataFrame, interval: str) -> pd.DataFrame:
    df = df.copy()
    df = df[~df.index.duplicated(keep="last")].sort_index()
    for c in OHLC:
        if c not in df.columns:
            df[c] = np.nan
        df[c] = pd.to_numeric(df[c], errors="coerce")
    if df["volume"].isna().all():
        df["volume"] = 0.0
    df = df.dropna(subset=["open", "high", "low", "close"])
    df = df[(df[["open", "high", "low", "close"]] > 0).all(axis=1)]
    df["high"] = df[["high", "open", "close"]].max(axis=1)
    df["low"] = df[["low", "open", "close"]].min(axis=1)
    return df[OHLC]


def parse_yahoo_chart(txt: str) -> Optional[pd.DataFrame]:
    try:
        j = json.loads(txt)
        res = j["chart"]["result"][0]
        ts = res["timestamp"]
        q = res["indicators"]["quote"][0]
        df = pd.DataFrame(
            {
                "open": q["open"],
                "high": q["high"],
                "low": q["low"],
                "close": q["close"],
                "volume": q.get("volume", [0] * len(ts)),
            },
            index=pd.to_datetime(ts, unit="s", utc=True),
        )
        return df
    except Exception:
        return None


def parse_binance(txt: str) -> Optional[pd.DataFrame]:
    try:
        raw = json.loads(txt)
        rows = [
            (
                pd.Timestamp(int(r[0]), unit="ms", tz="UTC"),
                float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]),
            )
            for r in raw
        ]
        return pd.DataFrame(rows, columns=["ts"] + OHLC).set_index("ts")
    except Exception:
        return None


def parse_coinbase(txt: str) -> Optional[pd.DataFrame]:
    """Coinbase returns [time, low, high, open, close, volume] newest-first."""
    try:
        raw = json.loads(txt)
        rows = [
            (
                pd.Timestamp(int(r[0]), unit="s", tz="UTC"),
                float(r[3]), float(r[2]), float(r[1]), float(r[4]), float(r[5]),
            )
            for r in raw
        ]
        return pd.DataFrame(rows, columns=["ts"] + OHLC).set_index("ts")
    except Exception:
        return None


def parse_kraken(txt: str) -> Optional[pd.DataFrame]:
    try:
        j = json.loads(txt)
        if j.get("error"):
            return None
        key = [k for k in j["result"].keys() if k != "last"]
        if not key:
            return None
        raw = j["result"][key[0]]
        rows = [
            (
                pd.Timestamp(int(r[0]), unit="s", tz="UTC"),
                float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[6]),
            )
            for r in raw
        ]
        return pd.DataFrame(rows, columns=["ts"] + OHLC).set_index("ts")
    except Exception:
        return None


def parse_bitfinex(txt: str) -> Optional[pd.DataFrame]:
    """[MTS, OPEN, CLOSE, HIGH, LOW, VOLUME] ascending."""
    try:
        raw = json.loads(txt)
        rows = [
            (
                pd.Timestamp(int(r[0]), unit="ms", tz="UTC"),
                float(r[1]), float(r[3]), float(r[4]), float(r[2]), float(r[5]),
            )
            for r in raw
        ]
        return pd.DataFrame(rows, columns=["ts"] + OHLC).set_index("ts")
    except Exception:
        return None


def parse_coingecko_chart(txt: str) -> Optional[pd.DataFrame]:
    """Close-only series -> conservative OHLC (high/low bracketed by open/close)."""
    try:
        j = json.loads(txt)
        prices = j.get("prices") or []
        if len(prices) < 10:
            return None
        idx = pd.to_datetime([p[0] for p in prices], unit="ms", utc=True)
        c = np.array([p[1] for p in prices], dtype=float)
        o = np.concatenate([[c[0]], c[:-1]])
        return pd.DataFrame(
            {"open": o, "high": np.maximum(o, c), "low": np.minimum(o, c), "close": c, "volume": 0.0},
            index=idx,
        )
    except Exception:
        return None


def parse_stooq_csv(txt: str) -> Optional[pd.DataFrame]:
    try:
        df = pd.read_csv(io.StringIO(txt))
        cols = {c.lower(): c for c in df.columns}
        if "date" not in cols:
            return None
        df = df.rename(
            columns={
                cols["date"]: "ts",
                cols.get("open", "Open"): "open",
                cols.get("high", "High"): "high",
                cols.get("low", "Low"): "low",
                cols.get("close", "Close"): "close",
            }
        )
        df["ts"] = pd.to_datetime(df["ts"], utc=True, errors="coerce")
        df = df.dropna(subset=["ts"]).set_index("ts")
        df["volume"] = df[cols["volume"]] if "volume" in cols else 0.0
        return df[OHLC]
    except Exception:
        return None


# ----------------------------------------------------------------------------
# Provider URL builders
# ----------------------------------------------------------------------------

_YAHOO_INTERVAL = {"5m": "5m", "15m": "15m", "30m": "30m", "1h": "60m", "2h": "60m", "4h": "60m", "1d": "1d"}
_YAHOO_RANGE = {"5m": "60d", "15m": "60d", "30m": "60d", "1h": "730d", "2h": "730d", "4h": "730d", "1d": "5y"}
_BINANCE_INTERVAL = {"5m": "5m", "15m": "15m", "30m": "30m", "1h": "1h", "2h": "2h", "4h": "4h", "1d": "1d"}
_CB_GRAN = {"5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "2h": 7200, "4h": 14400, "1d": 86400}
_KRAKEN_INTERVAL = {"5m": 5, "15m": 15, "30m": 30, "1h": 60, "2h": 120, "4h": 240, "1d": 1440}
_BFX_INTERVAL = {"5m": "5m", "15m": "15m", "30m": "30m", "1h": "1h", "2h": "2h", "4h": "4h", "1d": "1D"}


def yahoo_url(symbol: str, interval: str) -> str:
    iv = _YAHOO_INTERVAL.get(interval, "60m")
    rg = _YAHOO_RANGE.get(interval, "730d")
    return f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}?interval={iv}&range={rg}&includePrePost=false"


def binance_url(symbol: str, interval: str, limit: int = 1000) -> str:
    return f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={_BINANCE_INTERVAL.get(interval,'1h')}&limit={limit}"


def coinbase_url(symbol: str, interval: str) -> str:
    return f"https://api.exchange.coinbase.com/products/{symbol}/candles?granularity={_CB_GRAN.get(interval,3600)}"


def kraken_url(symbol: str, interval: str) -> str:
    return f"https://api.kraken.com/0/public/OHLC?pair={symbol}&interval={_KRAKEN_INTERVAL.get(interval,60)}"


def bitfinex_url(symbol: str, interval: str, limit: int = 1000) -> str:
    return f"https://api-pub.bitfinex.com/v2/candles/trade:{_BFX_INTERVAL.get(interval,'1h')}:{symbol}/hist?limit={limit}"


def coingecko_url(coin: str, days: int = 90, interval: str = "hourly") -> str:
    return f"https://api.coingecko.com/api/v3/coins/{coin}/market_chart?vs_currency=usd&days={days}&interval={interval}"


# ----------------------------------------------------------------------------
# Universe loading
# ----------------------------------------------------------------------------


def _synthetic(asset: str, interval: str, bars: int, start: Optional[datetime] = None) -> Frame:
    """Deterministic GBM + jump series. Calibrated so the analytics pipeline is
    fully exercisable offline. Always labelled SYNTHETIC."""
    from .config import TF_MINUTES

    rng = np.random.default_rng(SYNTH["seed"] + sum(ord(c) for c in asset))
    mins = TF_MINUTES.get(interval, 60)
    if start is None:
        start = datetime.now(timezone.utc) - pd.Timedelta(minutes=mins * bars)
    idx = pd.date_range(start, periods=bars, freq=f"{mins}min", tz="UTC")

    bars_per_year = 252 * 1440 / mins
    dt = 1.0 / bars_per_year
    mu, sig = SYNTH["annual_drift"], SYNTH["annual_vol"]
    if asset in ("VIX", "US10Y"):
        # mean reverting proxies instead of trending ones
        x = np.zeros(bars)
        lvl = 18.0 if asset == "VIX" else 4.0
        kappa, theta, vol = 0.02, lvl, lvl * 0.35 * math.sqrt(dt)
        for i in range(1, bars):
            x[i] = x[i - 1] + kappa * (theta - x[i - 1]) + vol * rng.standard_normal()
        price = np.maximum(lvl * 0.4, lvl + x)
        if asset == "DXY":
            price = 100 + x
    else:
        p0 = SYNTH["start_price"] if asset == "XAUUSD" else 100.0
        if asset == "XAGUSD":
            p0 = 24.0
        if asset in ("SPX",):
            p0 = 5200.0
        if asset in ("WTI",):
            p0 = 78.0
        if asset in ("COPPER",):
            p0 = 4.2
        if asset in ("DXY",):
            p0 = 104.0
        if asset in ("IEF", "TIP"):
            p0 = 95.0
        shocks = rng.standard_normal(bars) * sig * math.sqrt(dt)
        jumps = (rng.random(bars) < SYNTH["jump_prob_per_bar"]) * rng.standard_normal(bars) * SYNTH["jump_scale_atr"] * sig * math.sqrt(dt) * 8
        logret = (mu - 0.5 * sig**2) * dt + shocks + jumps
        # add a slow regime wave so trends/mean-reversion both appear
        wave = np.sin(np.arange(bars) / (bars / 7.0)) * sig * 0.9 * math.sqrt(dt)
        price = p0 * np.exp(np.cumsum(logret + wave))

    intrabar = np.abs(rng.standard_normal(bars)) * price * 0.0009
    o = np.concatenate([[price[0]], price[:-1]])
    hi = np.maximum(o, price) + intrabar
    lo = np.minimum(o, price) - intrabar
    vol = np.abs(rng.standard_normal(bars)) * 1000 + 500
    df = pd.DataFrame({"open": o, "high": hi, "low": lo, "close": price, "volume": vol}, index=idx)
    return Frame(df, asset, interval, "synthetic-generator", "synthetic",
                 "SYNTHETIC series - no live feed reachable. Not real market data.")


def _resample(df: pd.DataFrame, interval: str) -> pd.DataFrame:
    from .config import TF_MINUTES

    rule = f"{TF_MINUTES.get(interval, 60)}min"
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    out = df.resample(rule).agg(agg).dropna(subset=["close"])
    return out[OHLC]


def _providers_for(asset: str, interval: str, bars: int, browser_first: bool = False
                   ) -> List[Tuple[str, str, str]]:
    """Return [(provider_name, url, parser)] ordered by preference.

    With `browser_first` the venues that send permissive CORS headers (and so
    work from a browser with no relay) are tried ahead of Yahoo, which needs a
    relay. This is chosen automatically once the process discovers it has no
    outbound internet of its own.
    """
    meta = ASSETS.get(asset, {})
    out: List[Tuple[str, str, str]] = []

    ysym = meta.get("yahoo")
    if ysym:
        out.append((f"yahoo:{ysym}", yahoo_url(ysym, interval), "yahoo"))

    if asset == "XAUUSD":
        if meta.get("binance"):
            out.append((f"binance:{meta['binance']}", binance_url(meta["binance"], interval), "binance"))
        if meta.get("bitfinex"):
            out.append((f"bitfinex:{meta['bitfinex']}", bitfinex_url(meta["bitfinex"], interval), "bitfinex"))
        if meta.get("kraken"):
            out.append((f"kraken:{meta['kraken']}", kraken_url(meta["kraken"], interval), "kraken"))
        if meta.get("coinbase"):
            out.append((f"coinbase:{meta['coinbase']}", coinbase_url(meta["coinbase"], interval), "coinbase"))
        if meta.get("coingecko"):
            out.append((f"coingecko:{meta['coingecko']}", coingecko_url(meta["coingecko"]), "coingecko"))

    if meta.get("stooq"):
        out.append((f"stooq:{meta['stooq']}", f"https://stooq.com/q/d/l/?s={meta['stooq']}&i=d", "stooq"))

    if browser_first:
        native = [p for p in out if p[2] in ("binance", "bitfinex", "kraken", "coinbase", "coingecko")]
        out = native + [p for p in out if p not in native]
    return out


_SERVER_NET: Optional[bool] = None


def server_has_network(probe: str = "https://query1.finance.yahoo.com/v8/finance/chart/AAPL?range=1d&interval=1d",
                       timeout: float = 6.0) -> bool:
    """One-shot probe: does the Python process itself have outbound internet?"""
    global _SERVER_NET
    if _SERVER_NET is None:
        _SERVER_NET = server_get(probe, timeout=timeout) is not None
    return _SERVER_NET


_PARSERS = {
    "yahoo": parse_yahoo_chart,
    "binance": parse_binance,
    "coinbase": parse_coinbase,
    "kraken": parse_kraken,
    "bitfinex": parse_bitfinex,
    "coingecko": parse_coingecko_chart,
    "stooq": parse_stooq_csv,
}


def fetch_yahoo_batch(
    assets: List[str],
    interval: str,
    log: FetchLog,
    use_browser: bool = True,
    bridge_only: bool = False,
    timeout_ms: int = 25000,
) -> Dict[str, Frame]:
    """Pull the macro universe in as few round trips as possible.

    Server-side: one request per asset. Browser: a single batched fetch of every
    symbol (optionally through a CORS relay), which keeps a 10-symbol universe to
    one component round trip instead of ten.
    """
    out: Dict[str, Frame] = {}
    todo = [a for a in assets if ASSETS.get(a, {}).get("yahoo")]
    if not todo:
        return out

    if not bridge_only:
        for a in list(todo):
            txt, via = get_text(yahoo_url(ASSETS[a]["yahoo"], interval), key=f"y_{a}_{interval}",
                                log=log, asset=a, provider=f"yahoo:{ASSETS[a]['yahoo']}",
                                use_browser=False, timeout_ms=timeout_ms)
            df = parse_yahoo_chart(txt) if txt else None
            if df is not None and len(df) > 30:
                df = _clean_df(df, interval)
                out[a] = Frame(df, a, interval, f"yahoo:{ASSETS[a]['yahoo']} [{via}]", "real",
                               "Yahoo Finance (server-side).")
                todo.remove(a)

    if todo and use_browser and bridge_available():
        for relay_i in range(len(_CORS_RELAYS) + 1):
            mapping: Dict[str, str] = {}
            for a in todo:
                u = yahoo_url(ASSETS[a]["yahoo"], interval)
                mapping[a] = u if relay_i == 0 else _CORS_RELAYS[relay_i - 1].format(
                    u=urllib.parse.quote(u, safe=""))
            got = bridge_get_multi(mapping, key=f"ybatch_{interval}_{relay_i}",
                                   timeout_ms=timeout_ms, wait=True)
            if not got:
                continue
            for a, txt in got.items():
                df = parse_yahoo_chart(txt) if txt else None
                if df is None or len(df) <= 30:
                    continue
                df = _clean_df(df, interval)
                tag = "direct" if relay_i == 0 else f"relay#{relay_i}"
                out[a] = Frame(df, a, interval, f"yahoo:{ASSETS[a]['yahoo']} [{tag}, browser]",
                               "real", "Yahoo Finance via the browser bridge.")
                log.add(a, f"yahoo batch {tag}", True, f"{len(df)} bars", "real")
            todo = [a for a in todo if a not in out]
            if not todo:
                break
    for a in todo:
        log.add(a, "yahoo batch", False, "no response")
    return out


def fetch_asset(
    asset: str,
    interval: str,
    bars: int,
    log: FetchLog,
    use_browser: bool = True,
    use_relay: bool = True,
    bridge_only: bool = False,
    csv_dir: Optional[str] = None,
) -> Frame:
    """Walk the provider chain until one returns a usable series."""
    # 1) local CSV cache / broker export
    if csv_dir:
        for fn in (f"{asset}_{interval}.csv", f"{asset}.csv"):
            p = os.path.join(csv_dir, fn)
            if os.path.exists(p):
                try:
                    df = pd.read_csv(p, parse_dates=["ts"]).set_index("ts")
                    df.index = pd.to_datetime(df.index, utc=True)
                    df = _clean_df(df, interval)
                    if len(df) > 30:
                        log.add(asset, f"csv:{fn}", True, f"{len(df)} bars", "real")
                        return Frame(df.tail(int(bars * 1.4)), asset, interval, f"local CSV ({fn})", "real",
                                     "Loaded from local CSV export.")
                except Exception as e:
                    log.add(asset, f"csv:{fn}", False, str(e))

    browser_first = not server_has_network()
    providers = _providers_for(asset, interval, bars, browser_first=browser_first)
    for name, url, parser in providers:
        txt, via = get_text(url, key=f"f_{asset}_{parser}_{interval}", log=log, asset=asset,
                            provider=name, use_browser=use_browser, wait=True)
        if txt is None and use_relay and use_browser:
            # the Yahoo/stooq endpoints have no CORS headers; a public relay is
            # the last resort when the server itself cannot reach the internet.
            for i, rurl in enumerate(relay_urls(url)):
                txt, via = get_text(rurl, key=f"r{i}_{asset}_{parser}_{interval}", log=log, asset=asset,
                                    provider=f"{name} via relay#{i+1}", use_browser=True, wait=True)
                if txt:
                    name = f"{name} (CORS relay #{i+1})"
                    break
        if not txt:
            continue
        try:
            df = _PARSERS[parser](txt)
        except Exception:
            df = None
        if df is None or len(df) < 30:
            log.add(asset, name, False, "unparsable payload")
            continue
        df = _clean_df(df, interval)
        if len(df) < 30:
            continue
        kind = "proxy" if parser in ("binance", "kraken", "coinbase", "bitfinex", "coingecko") else "real"
        note = ""
        if kind == "proxy":
            note = "Tokenised-gold venue (PAXG/XAUt) - a real, liquid, exchange-traded proxy for XAU/USD spot."
        elif parser == "yahoo":
            note = "COMEX front-month / cash proxy from Yahoo Finance."
        return Frame(df.tail(int(bars * 1.4)), asset, interval, f"{name} [{via}]", kind, note)

    log.add(asset, "synthetic", True, "fallback generator", "synthetic")
    return _synthetic(asset, interval, int(bars * 1.3))


def load_universe(
    assets: List[str],
    interval: str,
    bars: int,
    use_browser: bool = True,
    csv_dir: Optional[str] = None,
) -> Tuple[Dict[str, Frame], FetchLog]:
    log = FetchLog()
    out: Dict[str, Frame] = {}
    for a in assets:
        out[a] = fetch_asset(a, interval, bars, log, use_browser=use_browser, csv_dir=csv_dir)
    return out, log


def align_universe(frames: Dict[str, pd.DataFrame], primary: str = "XAUUSD") -> pd.DataFrame:
    """Wide close-price matrix on the primary's index (forward-filled)."""
    if primary not in frames:
        return pd.DataFrame()
    idx = frames[primary].index
    data = {}
    for k, df in frames.items():
        s = df["close"].reindex(idx.union(df.index)).ffill().reindex(idx)
        data[k] = s
    out = pd.DataFrame(data, index=idx)
    return out.dropna(how="all")


# ----------------------------------------------------------------------------
# Live spot
# ----------------------------------------------------------------------------

_SPOT_PARSERS = {
    "gold-api.com": lambda t: float(json.loads(t)["price"]),
    "Binance PAXG": lambda t: float(json.loads(t)["price"]),
    "Coinbase PAXG": lambda t: float(json.loads(t)["data"]["amount"]),
    "Kraken PAXG": lambda t: float(list(json.loads(t)["result"].values())[0]["c"][0]),
    "CoinGecko PAXG": lambda t: float(json.loads(t)["pax-gold"]["usd"]),
}


def fetch_spot(use_browser: bool = True, log: Optional[FetchLog] = None) -> dict:
    """Best-effort live spot. Returns dict(price, source, ts, kind)."""
    from .config import SPOT_ENDPOINTS

    for name, url in SPOT_ENDPOINTS:
        txt, via = get_text(url, key=f"spot_{name.replace(' ', '_')}", log=log, asset="XAUUSD",
                            provider=f"spot:{name}", use_browser=use_browser, timeout_ms=8000)
        if not txt:
            continue
        try:
            px = _SPOT_PARSERS[name](txt)
        except Exception:
            continue
        if px and px > 0:
            return {
                "price": float(px),
                "source": f"{name} [{via}]",
                "ts": datetime.now(timezone.utc),
                "kind": "real" if name == "gold-api.com" else "proxy",
            }
    return {"price": None, "source": "unavailable", "ts": None, "kind": "none"}


# ----------------------------------------------------------------------------
# News (RSS) - pulled through the same transport, so it works server-side or in
# the browser depending on what the environment allows.
# ----------------------------------------------------------------------------


def fetch_feed(url: str, key: str, use_browser: bool = True, timeout_ms: int = 12000) -> Optional[str]:
    txt, _via = get_text(url, key=key, use_browser=use_browser, timeout_ms=timeout_ms)
    return txt
