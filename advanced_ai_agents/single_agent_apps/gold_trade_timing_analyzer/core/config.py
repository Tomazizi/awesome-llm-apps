"""
Configuration for the Gold (XAUUSD) Trade Entry Timing Accuracy Analyzer.

Every number that a researcher would want to turn lives here so the engine is
fully parameterised and reproducible. Nothing in this module touches the
network or Streamlit.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Dict, List

# ----------------------------------------------------------------------------
# Instrument / universe
# ----------------------------------------------------------------------------

PRIMARY = "XAUUSD"

#: Logical asset -> how each upstream venue names it.
#: `yahoo`   : Yahoo Finance symbol (GC=F is the front-month COMEX future, the
#:             conventional proxy for spot XAU/USD).
#: `stooq`   : Stooq symbol.
#: `crypto`  : tokenised-gold venues, used as a *spot proxy* when the sandbox /
#:             browser cannot reach a traditional market data API.
ASSETS: Dict[str, dict] = {
    "XAUUSD": {
        "label": "Gold (XAU/USD)",
        "yahoo": "GC=F",
        "stooq": "xauusd",
        "binance": "PAXGUSDT",
        "coinbase": "PAXG-USD",
        "kraken": "PAXGUSD",
        "bitfinex": "tXAUUSD",
        "coingecko": "pax-gold",
        "pip": 0.01,
        "digits": 2,
    },
    "XAGUSD": {
        "label": "Silver (XAG/USD)",
        "yahoo": "SI=F",
        "stooq": "xagusd",
        "binance": None,
        "coingecko": None,
        "digits": 3,
    },
    "DXY": {"label": "US Dollar Index", "yahoo": "DX-Y.NYB", "stooq": "dx.f"},
    "US10Y": {"label": "US 10Y Yield", "yahoo": "^TNX", "stooq": "10usy.b"},
    "VIX": {"label": "Volatility Index", "yahoo": "^VIX", "stooq": "vix"},
    "SPX": {"label": "S&P 500", "yahoo": "^GSPC", "stooq": "^spx"},
    "WTI": {"label": "WTI Crude", "yahoo": "CL=F", "stooq": "cl.f"},
    "COPPER": {"label": "Copper", "yahoo": "HG=F", "stooq": "hg.f"},
    "IEF": {"label": "7-10Y Nominal Treasuries", "yahoo": "IEF"},
    "TIP": {"label": "TIPS (Inflation Linked)", "yahoo": "TIP"},
}

#: Factors used by the market-neutral model. Each is a (asset, expected sign)
#: pair. `RY` is a *derived* real-yield proxy = log(IEF/TIP) which rises when
#: nominal yields outstrip inflation expectations (i.e. real yields rise).
FACTORS: List[tuple] = [
    ("DXY", -1),   # stronger dollar -> weaker gold
    ("RY", -1),    # higher real yields -> weaker gold
    ("VIX", +1),   # fear -> stronger gold
    ("SPX", -1),   # risk-on rotation out of gold
    ("XAGUSD", +1),  # precious complex beta
    ("WTI", +1),   # inflation impulse
    ("COPPER", +1),  # global growth / reflation
]

DERIVED = {"RY": ("IEF", "TIP")}  # RY = log(IEF / TIP)

# ----------------------------------------------------------------------------
# Sessions (UTC) - gold liquidity is highly session dependent, which matters a
# great deal for *entry timing*.
# ----------------------------------------------------------------------------

SESSIONS: Dict[str, tuple] = {
    "Asia": (0, 7),
    "London": (7, 12),
    "New York": (12, 20),
    "Late": (20, 24),
}

#: Empirical session tendency for trend continuation (used as a *timing* weight,
#: never as a standalone trade reason).
SESSION_TREND_WEIGHT: Dict[str, float] = {
    "Asia": 0.55,
    "London": 1.00,
    "New York": 0.95,
    "Late": 0.60,
}

# ----------------------------------------------------------------------------
# Tunable engine parameters
# ----------------------------------------------------------------------------


@dataclass
class Params:
    # ---- structure / tCISD -------------------------------------------------
    pivot_left: int = 5
    pivot_right: int = 5
    atr_period: int = 14
    disp_body_atr: float = 1.1        # displacement body must exceed this x ATR
    disp_body_frac: float = 0.50      # body / range floor for a displacement
    fvg_min_atr: float = 0.05         # ignore micro imbalances
    tcisd_half_life: float = 6.0      # bars: half-life of the time decay
    tcisd_max_bars: int = 24          # hard expiry of a CISD event
    tcisd_dist_lambda: float = 0.60   # ATR: price-distance decay
    sweep_wick_frac: float = 0.35     # wick must exceed this fraction of range
    htf_bias_ema: int = 50

    # ---- SSMT / intermarket ------------------------------------------------
    ssmt_lookback: int = 120          # window to locate comparable swings
    ssmt_z_window: int = 100          # window for return z-scores
    ssmt_min_abs_corr: float = 0.15   # ignore uncorrelated proxies
    ssmt_z_div_threshold: float = 0.85

    # ---- Fibonacci ---------------------------------------------------------
    fib_lookback: int = 220
    fib_levels: tuple = (0.236, 0.382, 0.5, 0.618, 0.705, 0.786, 0.886)
    fib_ext: tuple = (1.272, 1.618, 2.0, 2.618)
    ote_lo: float = 0.618
    ote_hi: float = 0.786
    fib_time_nodes: tuple = (1, 2, 3, 5, 8, 13, 21, 34, 55)
    fib_confluence_atr: float = 0.25
    fib_level_atr_tol: float = 0.30

    # ---- market neutral statistics ----------------------------------------
    mn_window: int = 250              # rolling OLS window
    mn_ridge: float = 1e-3
    mn_rls_forget: float = 0.995      # forgetting factor for time-varying beta
    mn_z_window: int = 120
    mn_hurst_window: int = 250
    mn_min_obs: int = 120

    # ---- exhaustion / divergence ------------------------------------------
    rsi_period: int = 14
    div_swing_lookback: int = 90
    div_min_price_move_atr: float = 1.0
    div_spearman_threshold: float = -0.25
    div_max_pvalue: float = 0.20
    exhaustion_climax_range_atr: float = 2.0
    exhaustion_vol_z: float = 1.5
    exhaustion_stretch_bars: int = 5
    exhaustion_atr_expand: float = 1.7

    # ---- reversal risk -----------------------------------------------------
    rev_horizon: int = 12             # bars: forward window for the label
    rev_move_atr: float = 1.0         # adverse move that counts as a reversal
    rev_train_window: int = 750       # expanding training window
    rev_refit_every: int = 60         # refit cadence (walk-forward)
    rev_l2: float = 1e-2
    rev_stretch_window: int = 250
    rev_gate: float = 0.75           # signal is vetoed above this reversal probability

    # ---- signals -----------------------------------------------------------
    min_conviction: int = 55          # below this: no trade
    tier_a: int = 75
    tier_b: int = 62
    min_rr: float = 1.5
    stop_buffer_atr: float = 0.25
    tp1_r: float = 1.5
    tp2_r: float = 2.5
    tp3_r: float = 4.0

    # ---- risk sizing -------------------------------------------------------
    equity: float = 100_000.0
    risk_per_trade_pct: float = 0.005   # 0.5% of equity per trade
    max_risk_pct: float = 0.012
    contract_size: float = 100.0        # 100 oz (standard gold lot)
    spread_usd: float = 0.35            # round-trip cost in USD per oz
    commission_usd: float = 0.10

    # ---- backtest ----------------------------------------------------------
    bt_max_hold: int = 72
    bt_cooldown: int = 6              # bars to wait after a trade closes
    bt_warmup: int = 300
    bt_delay_grid: tuple = (0, 1, 2, 3, 4, 6, 8, 12)

    # weights of the composite Entry Timing Score (each component in [0,1])
    w_tcisd: float = 0.24
    w_fib: float = 0.16
    w_mn: float = 0.16
    w_ssmt: float = 0.14
    w_exhaust: float = 0.12
    w_reversal: float = 0.10
    w_session: float = 0.04
    w_news: float = 0.04

    def as_dict(self) -> dict:
        return asdict(self)

    def normalised_weights(self) -> Dict[str, float]:
        keys = ["tcisd", "fib", "mn", "ssmt", "exhaust", "reversal", "session", "news"]
        raw = {k: max(0.0, float(getattr(self, f"w_{k}"))) for k in keys}
        tot = sum(raw.values()) or 1.0
        return {k: v / tot for k, v in raw.items()}


DEFAULTS = Params()

# ----------------------------------------------------------------------------
# Timeframes
# ----------------------------------------------------------------------------

TF_MINUTES: Dict[str, int] = {
    "5m": 5, "15m": 15, "30m": 30, "1h": 60, "2h": 120, "4h": 240, "1d": 1440,
}

#: Higher timeframe used for bias, given the execution timeframe.
HTF_MAP: Dict[str, str] = {
    "5m": "1h", "15m": "4h", "30m": "4h", "1h": "4h", "2h": "1d", "4h": "1d", "1d": "1d",
}

BARS_PER_YEAR: Dict[str, float] = {
    "5m": 252 * 24 * 12, "15m": 252 * 24 * 4, "30m": 252 * 24 * 2,
    "1h": 252 * 24, "2h": 252 * 12, "4h": 252 * 6, "1d": 252.0,
}

# ----------------------------------------------------------------------------
# News / research
# ----------------------------------------------------------------------------

NEWS_FEEDS: List[tuple] = [
    ("Kitco News", "https://www.kitco.com/rss/KitcoNewsRSS.xml"),
    ("FXStreet", "https://www.fxstreet.com/rss/news"),
    ("Investing.com Metals", "https://www.investing.com/rss/news_11.rss"),
    ("Mining.com", "https://www.mining.com/feed/"),
    ("CNBC Economy", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=20910258"),
    ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
    ("Trading Economics", "https://tradingeconomics.com/rss/news.aspx"),
    ("Central Banking", "https://www.centralbanking.com/rss/"),
]

#: Free, key-less spot endpoints. Tried in order for the live spot ticker.
SPOT_ENDPOINTS: List[tuple] = [
    ("gold-api.com", "https://api.gold-api.com/price/XAU"),
    ("Binance PAXG", "https://api.binance.com/api/v3/ticker/price?symbol=PAXGUSDT"),
    ("Coinbase PAXG", "https://api.coinbase.com/v2/prices/PAXG-USD/spot"),
    ("Kraken PAXG", "https://api.kraken.com/0/public/Ticker?pair=PAXGUSD"),
    ("CoinGecko PAXG", "https://api.coingecko.com/api/v3/simple/price?ids=pax-gold&vs_currencies=usd"),
]

#: Recurring macro events that change the risk profile of an entry.
EVENT_KEYWORDS: Dict[str, List[str]] = {
    "FOMC / Fed decision": ["fomc", "federal reserve", "fed decision", "rate decision", "powell", "jerome powell"],
    "CPI / Inflation": ["cpi", "consumer price", "inflation", "pce", "ppi"],
    "Labour market": ["nonfarm", "non-farm", "nfp", "payrolls", "jobless", "unemployment"],
    "Geopolitics": ["war", "conflict", "strike", "sanction", "attack", "military", "tensions", "ceasefire"],
    "Central bank gold demand": ["central bank", "gold reserve", "imf", "gold buying", "bullion reserves"],
    "Dollar / rates": ["dollar", "treasury", "yield", "dxy", "greenback"],
    "Trade policy": ["tariff", "trade deal", "export control", "import duty"],
}

# ----------------------------------------------------------------------------
# Synthetic fallback generator (clearly labelled, never disguised)
# ----------------------------------------------------------------------------

SYNTH = {
    "start_price": 2000.0,
    "annual_drift": 0.06,
    "annual_vol": 0.15,
    "jump_prob_per_bar": 0.004,
    "jump_scale_atr": 3.0,
    "seed": 20240118,
}
