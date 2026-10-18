# System design — Gold (XAU/USD) Trade Entry Timing Accuracy Analyzer

> Most trading tools answer *"is gold bullish?"*. That question is cheap and mostly
> useless: a directional view taken at the wrong moment is a losing trade. This system
> answers a harder, more valuable question — **"is this specific bar a good moment to
> enter, and what is the evidence?"**

---

## 1. Thesis

Entry timing is not direction. The same setup has positive expectancy one bar after it
triggers and negative expectancy fifteen bars later, because by then the stop is in the
obvious place, the imbalance has been filled, and the move has been distributed. Yet
almost no tooling measures this. This system treats **time-since-trigger as a first-class
feature**, decays every structural signal against it, and then *empirically measures*
whether the decay is right by re-running each historical signal with a 0…12-bar entry lag
and comparing expectancy.

Four model families feed one score; two risk overlays constrain it; one research layer
contextualises it; and a validation layer grades all of it out-of-sample.

```
                 ┌──────────────────────────────────────────────┐
  market data ──►│  tCISD   SSMT   Fibonacci   Market-Neutral    │
  + drivers      │        (four independent model families)      │
                 └───────────────────┬──────────────────────────┘
                                     │  each emits a [0,1] reading per direction
                                     ▼
                        ┌────────────────────────────┐
                        │  Entry Timing Score (ETS)   │  weighted, weights → 1
                        └───┬────────────────────┬───┘
                            │                    │
              ┌─────────────▼──────┐   ┌─────────▼──────────┐
              │ Reversal risk      │   │ Exhaustion /        │
              │ (walk-forward      │   │ Divergence          │
              │  logistic)         │   │ (Spearman-tested)   │
              └─────────────┬──────┘   └─────────┬──────────┘
                            └──────────┬─────────┘
                                       ▼
                        ┌────────────────────────────┐
                        │  Gates → Trade card → Size  │
                        └──────────────┬─────────────┘
                                       ▼
                        ┌────────────────────────────┐
                        │  Timing accuracy research   │
                        │  MAE/MFE · delay curve · IC │
                        └────────────────────────────┘
```

---

## 2. Model definitions

### 2.1 tCISD — time-anchored Change In State of Delivery

**Origin.** "CISD" is an order-flow / ICT concept. A market that is *delivering* price in
one direction (a run of same-side displacement candles that take liquidity) flips when
price closes back through the **open of the displacement candle that ran that liquidity**.
Running a prior swing low and instantly reclaiming the down-candle's open is the classic
signature of a stop-run being absorbed: sell-side delivery has changed to buy-side.

**Formalisation.** For a long setup at bar `i`:

| step | condition |
|---|---|
| 1. liquidity sweep | `low[i] < confirmedSwingLow[i]` and `close[i] > confirmedSwingLow[i]`, with the rejection wick ≥ 35 % of the bar range |
| 2. delivery candle | the largest-bodied opposite-direction candle within ±3 bars of the sweep; its **open** becomes the CISD level, its extreme becomes the invalidation |
| 3. state change | the first bar `j > i` with `close[j] > open[delivery]` confirms the event |
| 4. structure bonus | if `close[j]` also clears the prior lower-high, a market-structure shift (MSS) is logged and quality is scaled up |

**The "t".** A raw CISD is a state, not a timing. Each event carries `t0` (confirmation
bar) and its score decays:

```
w_time = exp(-(t - t0) / τ)          τ = half_life / ln 2,  hard expiry at T_max bars
w_dist = exp(-overshoot / λ) · exp(-underreach / 2λ)      λ ≈ 0.6 ATR, 0.5 ATR of free pullback
score  = 100 · w_time · w_dist · quality · htf_alignment
```

* `quality = clip(displacement_body / 1.5 ATR, 0.20, 1)` — a weak displacement is a weak
  signal, and a non-confirmed MSS scales it by 0.85.
* `htf_alignment ∈ {1.0 aligned, 0.70 neutral, 0.25 against}` from the higher timeframe.
* **Hard invalidation:** if price closes back through the delivery candle's extreme before
  expiry, the event is killed and scores zero from that bar forward.

This is what makes the score an *entry timing* instrument rather than a bias indicator: the
identical CISD scores ~90 on the confirmation bar and ~10 six half-lives later.

---

### 2.2 SSMT — Statistical Smart-Money Technique

**Origin.** "SMT" in the ICT lexicon is an intermarket divergence: gold prints a new high
while silver — its closest correlated sibling — fails to confirm, implying the move was
engineered to run stops rather than to continue.

**Statistical formalisation.** Eyeballed divergence is not reproducible, so:

1. Swings are located on both series over a common lookback.
2. Each leg is converted to a **z-score of that asset's own returns**, so "made a new high"
   is judged against that asset's volatility instead of the naked eye.
3. A non-confirmation must clear a z-distance threshold before it counts.
4. Every proxy is weighted by its **rolling correlation with gold**, Fisher-z tested, and
   dropped below |ρ| = 0.15 — uncorrelated assets cannot vote.
5. A continuousised form is carried alongside: the z-score of the **beta-hedged spread**
   `s = r_gold − β·r_proxy`, i.e. gold's relative strength with the common factor removed.

Output: `ssmt_div ∈ [−1, +1]` (−1 = bearish non-confirmation of a high, +1 = bullish), plus
a per-proxy breakdown with ρ, p(ρ≠0), spread-z and a plain-English reading.

Typical proxies: XAG/USD (silver), DXY (dollar), the derived real-yield proxy, VIX, S&P 500,
WTI, copper.

---

### 2.3 Fibonacci — location, and time

Legs are taken from confirmed fractal swings. With a leg `anchor → tip`:

```
retr = (tip − price) / (tip − anchor)        # 0 at the tip, 1 at the anchor
```

so `retr ∈ [0.618, 0.786]` is the 62–79 % retracement band (ICT's Optimal Trade Entry) for
**both** directions — a single convention that removes the usual up/down sign confusion.

* **Location score** = gaussian centred on `retr = 0.70` (σ = 0.18), scaled by whether the
  price sits on the correct side of the 0.5 equilibrium for that direction.
* **Confluence** counts how many retracement ratios from *different* legs (primary,
  secondary, macro) sit within 0.25 ATR of price.
* **Time zones**: vertical nodes at 1, 2, 3, 5, 8, 13, 21, 34, 55 bars from the leg anchor.
  Price arriving at a retracement level *and* a fib time node is a materially better entry;
  the score reflects that.
* **Extensions** (1.272 / 1.618 / 2.0 / 2.618) supply the take-profit ladder.

---

### 2.4 Market-neutral statistical models

Two beta-neutralised specifications run in parallel against a driver basket
(USD index, derived real-yield proxy, VIX, S&P 500, silver, WTI, copper):

```
RETURN model :  r_gold,t = α + B′·F_t + ε_t          (rolling ridge, 250-bar window)
LEVEL  model :  log P_t  = α + B′·G_t + u_t          (driver-implied fair value)
```

* `ε_t` is the idiosyncratic gold return — the part no driver explains.
* `u_t` is the mispricing versus the driver-implied fair value (the classic gold
  fair-value framework).
* Betas are **time-varying**: a recursive-least-squares pass with a 0.995 forgetting factor
  runs alongside the windowed ridge so the hedge adapts instead of breaking on regime change.
* The ridge penalty is **scale-aware** (scaled by the mean diagonal of X′X), so the estimate
  does not collapse when returns are quoted in basis points rather than percent.

**Critical detail.** The per-bar return residual is white noise by construction, so testing
it for mean reversion is meaningless. All stationarity statistics are therefore computed on
the **cumulative** residual — the portion of the gold price path that no driver explains:

| statistic | purpose |
|---|---|
| OU half-life, AR(1) φ | how fast a dislocation closes |
| Hurst exponent | **decides whether the residual is faded or followed** |
| ADF t-statistic | stationarity of the unexplained path |
| driver R² | how much of gold the basket explains right now |

If H < 0.5 the residual is mean-reverting and extreme z-scores are faded; if H > 0.5 it is
trending and they are followed. The blend weight is continuous, not a hard switch, and the
whole signal is shrunk toward neutral when |z| is small.

---

### 2.5 Reversal risk — a walk-forward statistical model

A causal L2 logistic regression estimates

```
P( price trades ≥ k·ATR against the prevailing move within h bars )
```

* **Expanding-window walk-forward**, refit every 60 bars, predictions strictly out-of-sample.
* Features: stretch z-score vs the HTF mean, distance from the long EMA, momentum (10 and
  20-bar, ATR-normalised), RSI, ATR expansion ratio, realised-vol percentile, wick geometry,
  signed exhaustion, signed divergence, session, and the interaction of direction with
  stretch and exhaustion.
* **Out-of-sample AUC is reported in the UI.** A risk model that will not show its score is
  decoration, not risk management.

The probability is direction-aware: for a trade with the prevailing move it is a penalty;
for a counter-trend trade (which by definition *wants* the reversal) it is softened.

---

### 2.6 Exhaustion and divergence

**Divergence** is confirmed statistically, not visually. For each candidate (price higher-high
+ momentum lower-high, across RSI, MACD histogram and the stochastic), a **Spearman rank
correlation** is computed over the segment; the pattern must be negative *and* significant
(ρ ≤ −0.25, p ≤ 0.20) to score. Hidden divergence is the mirror case.

**Exhaustion** (0–100, split by direction) sums nine weighted symptoms:

| weight | symptom |
|---|---|
| 0.16 | climactic range (≥ 2 ATR) with volume z ≥ 1.5 and a close in the extreme quartile |
| 0.12 | ≥ 4 of the last 5 closes in the same direction |
| 0.10 | momentum decelerating while price still extends |
| 0.10 | wick rejection at the extreme (≥ 0.5 ATR wick) |
| 0.10 | three or more stacked unfilled imbalances in the same direction ("three pushes") |
| 0.12 | oscillator pinning (Stoch ≥ 90 / RSI ≥ 75) |
| 0.14 | statistically confirmed divergence |
| 0.08 | ATR expansion ≥ 1.7× with deceleration |
| 0.08 | extension ≥ 2 ATR from the HTF mean |

---

## 3. Composite score, gates and trade construction

```
ETS_d = 100 · [ w·tCISD_d + w·Fib_d + w·MN_d + w·SSMT_d
              + w·(1 − Exhaustion_against_d) + w·ReversalComponent_d
              + w·Session_d + w·News_d ] · event_penalty
```

Every component is in [0, 1] and the weights are renormalised to sum to 1, so the ETS is
itself a calibrated 0–100 answer to *"how good is this bar to enter?"*. Weights are live
sliders; the default allocation is tCISD 0.24, Fibonacci 0.16, market-neutral 0.16,
SSMT 0.14, exhaustion 0.12, reversal 0.10, session 0.04, news 0.04.

**Gates** — a score alone never opens a trade:

1. a **structural trigger must exist** (tCISD > 0). Fibonacci, the residual model and the
   news bias can modulate the score but can never, on their own, produce a trade.
2. **reversal risk < 75 %**
3. **conviction ≥ threshold** (default 55; tier A ≥ 75, B ≥ 62, C ≥ 55)
4. **R:R ≥ 1.5** after the target ladder is built

**Trade construction**

* Entry: next bar's open (never the signal bar's close — that is the single most common
  backtest lie).
* Stop: the structural invalidation (CISD delivery extreme or the recent swing), buffered by
  0.25 ATR, floored at 0.6 ATR and capped at 3.5 ATR.
* Targets: 50 % at 1.5R, 30 % at the 1.272 Fibonacci extension, 20 % at 1.618; the stop moves
  to break-even after TP1 and to TP1 after TP2.
* Size: fixed fractional risk scaled by conviction and by `1 − reversal risk`, capped at
  1.2 % of equity.

---

## 4. Entry timing accuracy research

This is the module the system is named for.

| output | what it answers |
|---|---|
| **MAE / MFE path** | how much pain before how much gain, in R, and on which bar each occurred |
| **Entry efficiency** = MFE / (MFE + \|MAE\|) | of the move that was available, how much did this entry capture per unit of drawdown tolerated |
| **Clean entry rate** | how often the trade reached +1R before −1R |
| **Delay curve** | every signal re-entered 0, 1, 2, 3, 4, 6, 8, 12 bars later — is it better to take the CISD on the close, wait for the retrace into the imbalance, or skip it? |
| **Score calibration** | quintiles of the ETS against realised R plus win rate — does a higher score actually pay? |
| **Attribution (Spearman IC)** | the IC of *each sub-model on its own* against realised R, with t-statistics — which model is carrying the alpha? |
| **Gate funnel** | how many bars survive each gate, so the filtering is auditable rather than magic |
| **Threshold sensitivity** | trade count and expectancy as the conviction threshold is raised |
| **Timing heatmap** | mean R by hour-of-day and direction |

All of it is computed with the reversal model fit on an expanding window and every entry
taken at the next bar's open net of spread and commission.

---

## 5. Data architecture

The engine never assumes where price comes from. It walks an ordered provider chain and
carries the provenance of every series (`real` / `proxy` / `synthetic`) so the UI can never
imply a synthetic number is real market data.

| tier | path | notes |
|---|---|---|
| 1 | **server-side HTTP** | Yahoo (`GC=F`, `SI=F`, `DX-Y.NYB`, `^TNX`, `^VIX`, `^GSPC`, `CL=F`, `HG=F`, `IEF`, `TIP`), Stooq, and keyed providers (Twelve Data, Alpha Vantage, Polygon, Finnhub, Tiingo, OANDA) |
| 2 | **browser bridge** | `streamlit-js-eval` evaluates a `fetch()` inside the *user's* browser, which has unrestricted internet even when the server does not. Tokenised-gold venues (PAXG / XAUt on Binance, Coinbase, Kraken, Bitfinex) return real, liquid, exchange-traded gold-linked OHLCV with permissive CORS; public CORS relays reach Yahoo for the macro universe. The whole universe is fetched in **one batched round trip**. |
| 3 | **local CSV** | drop a broker export in `data/` — it becomes the top-priority source |
| 4 | **synthetic** | a seeded GBM + jump series, always labelled `SYNTHETIC` and always announced in the UI |

Provider order adapts automatically: the app probes once for server-side internet and, if
there is none, tries CORS-native venues ahead of relay-dependent ones. A global wait budget
(default 60 s, configurable) prevents the bridge from stalling the app indefinitely, and a
one-click bridge diagnostic in the Data tab prints the raw response of a live fetch.

**Live spot** is a separate chain (gold-api.com → Binance → Coinbase → Kraken → CoinGecko)
so the header ticker updates independently of the OHLC load.

---

## 6. Research agent

Two layers, so the app degrades instead of breaking:

1. **Deterministic (always runs, no key).** RSS from metals and macro desks, parsed and
   scored against a gold-specific lexicon (rate cuts, real yields, safe-haven demand,
   central-bank buying, de-dollarisation, geopolitical shocks …) with recency weighting.
   Scheduled-event language (FOMC / CPI / NFP / geopolitics) is detected and converted into
   an **event-risk multiplier** that shrinks position size around known catalysts.
2. **LLM (optional).** Given the headlines *and* the quantitative context (price, session,
   HTF bias, active CISD, driver z-scores, residual half-life, Hurst, reversal probability
   and its AUC), the model returns a structured brief: macro regime, narrative, per-headline
   catalyst map with direction/magnitude/novelty, event-risk guidance, a bias with
   confidence, **an explicit list of contradictions**, and falsifiers. Six providers are
   supported (OpenAI, Groq, OpenRouter, Anthropic, Gemini, Ollama) over plain HTTP. The model
   gets 65 % of the bias weight scaled by its own stated confidence; it can never override
   the gates.

---

## 7. Engineering notes

* **No look-ahead.** Pivots are stamped with the bar at which they become *known*
  (`i + right`), not the bar at which they form. Entries are at the next bar's open.
  Rolling regressions use data strictly before the prediction bar.
* **Causality tests.** The test suite asserts that no pivot is ever known before it forms.
* **Performance.** Swings are computed once per series and filtered by confirmation bar
  rather than recomputed on a growing window (a 5× speedup on the divergence pass).
* **Caching.** The analytics bundle is cached on (data, parameters); data fetching is not,
  because the browser bridge is asynchronous.
* **Dependencies.** numpy / pandas / plotly / requests / streamlit plus two small
  Streamlit components. All statistics (ADF, OU half-life, Hurst, Spearman with p-values,
  ridge regression, RLS, logistic IRLS, ROC-AUC) are implemented in-repo so the engine has
  no statsmodels / scikit-learn dependency.

---

## 8. Known limitations

* **tCISD / SSMT / OTE are order-flow heuristics formalised with statistics.** The formalisation
  makes them reproducible and testable; it does not make the underlying premise a law.
* **Tokenised gold is not spot.** PAXG and XAUt track XAU/USD closely but carry a small,
  variable premium. They are labelled `LIVE PROXY` wherever they are used.
* **Bar-level fills.** The backtest assumes a stop or target inside a bar is filled at that
  price, which is optimistic in gaps and around data releases.
* **The reversal model needs history.** Below roughly 1,000 bars its AUC is not meaningful
  and the app falls back to the base rate.
* **Short samples make the calibration and attribution tables noisy.** Treat an IC from
  fewer than ~50 trades as a hint, not a result.
* **News latency.** RSS is minutes-scale; it is a context layer, never an execution trigger.
* **Not investment advice.** Research and education only.
