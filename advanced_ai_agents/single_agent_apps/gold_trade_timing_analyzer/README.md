## 🥇 Gold Trade Entry Timing Accuracy Analyzer (XAU/USD)

An AI research agent for gold that answers the question nobody's indicator stack answers:
**not** "is gold bullish?" but *"is this bar a good moment to enter, and what is the evidence?"*

It pulls **real market data and a live spot price**, scans the macro news flow, and fuses four
model families — **tCISD**, **SSMT**, **Fibonacci** and **market-neutral statistics** — with
**reversal risk** and **exhaustion / divergence** overlays into one **Entry Timing Score**.
Then it grades itself: every signal is re-run with a 0–12-bar entry lag, bucketed by score,
and attributed model-by-model so you can see which component actually carries the alpha.

### Features

- **Full-stack web app** — FastAPI backend + React dashboard on one port, Docker-ready
- **Real-time alerts** — webhook / Discord / Telegram, tier + score gated, per-direction
  cooldown, pushed to open tabs over WebSocket, every alert journalled and graded
- **Live XAU/USD spot ticker** with session clock, session change and data-provenance badge
- **tCISD** — time-anchored *Change In State of Delivery*: liquidity sweep → delivery-candle
  open reclaim, decayed by an exponential half-life so a stale trigger scores near zero
- **SSMT** — statistical intermarket divergence: gold vs silver / DXY / real yields / VIX /
  equities, correlation-weighted and Fisher-tested, plus a beta-hedged relative-strength spread
- **Fibonacci** — retracement with a unified OTE convention, level confluence across three legs,
  **Fibonacci time zones**, and 1.272 / 1.618 extensions as the target ladder
- **Market-neutral models** — rolling ridge + recursive-least-squares betas against a driver
  basket, idiosyncratic residual and level-space fair-value mispricing, with OU half-life,
  Hurst and ADF deciding whether the residual is *faded or followed*
- **Reversal risk** — walk-forward L2 logistic regression with **out-of-sample AUC shown**
- **Exhaustion index** — nine weighted climax symptoms; **divergence** confirmed by Spearman
  rank correlation with a p-value, not by eyeballing pivot slopes
- **Entry Timing Score** with hard gates (structural trigger required, R:R ≥ 1.5, reversal < 75 %)
- **Timing accuracy lab** — MAE/MFE paths, entry efficiency, clean-entry rate, **optimal entry
  lag curve**, score calibration, per-model IC attribution, gate funnel, threshold sensitivity
- **Research agent** — RSS scan of metals/macro desks, a gold-specific sentiment lexicon that
  runs with no API key, optional LLM brief (OpenAI / Groq / OpenRouter / Anthropic / Gemini /
  Ollama) with catalyst maps, event-risk guidance and an explicit contradictions list
- **Multi-source data layer** — Yahoo / Stooq server-side, a **browser bridge** for locked-down
  networks, broker CSV upload, and a clearly labelled synthetic fallback

---

## ⚡ The full-stack app (FastAPI + React, with real-time alerts)

The Streamlit app is the research console. **This** is the deployable product: a single-port
web application with a JSON/WebSocket API, a React dashboard, a SQLite signal journal and a
push-alert engine. The analysis engine is *identical* — `server/` is a wrapper around `core/`,
not a rewrite — so the web app and the research console can never drift apart.

```
browser  ──fetch real market data (CORS-friendly venues)──▶  POST /api/market/ingest
         ◀── WebSocket /ws : analysis, price ticks, alerts ──  FastAPI  ──▶  core/ engine
                                                                  │
                                                            SQLite journal
                                                                  │
                                                     webhook · Telegram · Discord
```

**Why the browser fetches the data.** A server in a locked-down network (this sandbox is one)
cannot reach market hosts, but the browser always can. So the SPA pulls OHLCV itself and POSTs
it in. When the server *does* have outbound internet, `/api/market/refresh` fetches directly.
`/api/status` reports which path is in play and the UI badges every series with its provenance:
`real`, `proxy` (tokenised gold — a real, liquid, exchange-traded stand-in for spot) or
`synthetic` (offline development only, never substituted silently).

### Real-time alerts

A signal only becomes an alert when it clears the conviction bar you set, and **only once per
setup**. The cooldown matters: the same structural trigger stays live for many bars, and a desk
that fires on every one of them is a desk that gets muted.

- **Thresholds** — minimum tier (A/B/C), minimum score, direction filter, cooldown in bars
- **Channels** — generic webhook (Zapier / Make / n8n / your own endpoint), Discord, Telegram,
  or journal-only. Each has a **Test** button that sends a real message.
- **Arming** — the engine re-evaluates on every ingest, on every parameter change and on a
  background poll timer. Alerts are pushed to open tabs over WebSocket, so the page rings
  immediately instead of waiting for the next refresh.
- **Auditable** — every alert is written to the journal *first*, so the message you received and
  the track record you can audit later are the same object. A background grader walks forward
  through new bars and marks each one hit-stop / hit-target / expired, in R.

### Run it

```bash
# 1. backend
pip install -r requirements-server.txt

# 2. frontend
cd web && npm install && npm run build && cd ..

# 3. one port, everything served from it
uvicorn server.main:app --host 0.0.0.0 --port 8000
# → http://localhost:8000        API docs: /api/docs
```

Or with Docker, which does both builds for you:

```bash
docker compose up --build      # → http://localhost:8000
```

For frontend development, run both — Vite proxies `/api` and `/ws` to the backend:

```bash
uvicorn server.main:app --port 8000 &
cd web && npm run dev         # → http://localhost:5173
```

### API surface

| group | endpoints |
|---|---|
| health | `GET /api/health` · `GET /api/status` · `GET /api/events` |
| market | `POST /api/market/ingest` · `POST /api/spot` · `POST /api/market/refresh` · `DELETE /api/market/clear` |
| analysis | `POST /api/analyze` · `GET /api/analysis` · `GET`/`POST /api/params` · `POST`/`GET /api/backtest` |
| research | `POST /api/news/ingest` · `GET /api/news` · `POST /api/research` |
| journal | `GET /api/journal` · `POST /api/journal/grade` · `DELETE /api/journal` |
| alerts | `GET /api/alerts` · `POST /api/alerts/config` · `POST /api/alerts/test` · `POST /api/alerts/check` |
| live | `WS /ws` — `hello`, `price`, `analysis`, `backtest`, `news`, `alert`, `journal` |

On a cold start the app loads its first sample by itself, so the dashboard is populated the
moment you open it.

---

### How to get Started?

1. Clone the GitHub repository

```bash
git clone https://github.com/Shubhamsaboo/awesome-llm-apps.git
cd advanced_ai_agents/single_agent_apps/gold_trade_timing_analyzer
```

2. Install the required dependencies

```bash
pip install -r requirements.txt
```

3. Run the Streamlit App

```bash
streamlit run gold_trade_timing_analyzer.py
```

No API key is required — the quantitative engine and the deterministic news layer run
immediately. Market data needs *either* server-side internet *or* a browser: the app detects
which it has and adapts.

4. *(Optional)* Add an LLM key for the research brief

Pick a provider in the sidebar (**Groq** and **OpenRouter** have free tiers), paste a key, and
press **🔎 Run market research**. Keys live in the Streamlit session only.

5. *(Optional)* Use your broker's data

Export `ts,open,high,low,close,volume` as CSV and upload it in the sidebar — it is saved to
`data/XAUUSD_<timeframe>.csv` and becomes the highest-priority source.

### How it Works?

**1 · Data.** The engine walks an ordered provider chain and records the provenance of every
series. Server-side HTTP (Yahoo `GC=F` and the macro universe, Stooq) is tried first. If the
process has no outbound internet — locked sandboxes, Streamlit Cloud, corporate networks — the
**browser bridge** takes over: `streamlit-js-eval` runs a `fetch()` inside *your* browser,
pulling real tokenised-gold OHLCV from Binance / Coinbase / Kraken / Bitfinex (PAXG, XAUt) and
the macro universe through CORS relays, batched into a single round trip. Then local CSV. Only
if all of those fail does it fall back to a seeded synthetic series — always badged
**SYNTHETIC** and announced in the UI.

**2 · Structure (tCISD).** Confirmed fractal swings → liquidity sweep (wick rejection through a
swing level) → the delivery candle whose **open** is the CISD level → confirmation on the close
through that open. Each event then decays: `exp(-Δt/τ)` in time and `exp(-overshoot/λ)` in
price distance, times displacement quality and higher-timeframe alignment. Closing back through
the invalidation kills it outright.

**3 · Intermarket (SSMT) and the neutral models.** Gold is regressed on its drivers with both a
windowed ridge and a forgetting-factor RLS. The residual — gold's move that the dollar, real
yields, risk appetite, the precious complex and reflation do **not** explain — is z-scored and
tested. Hurst decides whether that residual gets faded or followed.

**4 · Location (Fibonacci) and risk.** Retracement position against the OTE band, confluence
across legs, and fib time nodes set the location score. On top sit the reversal probability,
the exhaustion index and statistically confirmed divergence.

**5 · The score and the gates.** The weighted ETS in [0, 100] drives a tier (A ≥ 75, B ≥ 62,
C ≥ 55) and a conviction-scaled position size. But **no score opens a trade on its own** — a
tCISD trigger must exist, reversal risk must be under 75 %, and the target ladder must clear
1.5R.

**6 · The timing lab.** Because "entry timing" is the whole claim, it gets measured: MAE/MFE
paths, entry efficiency, the **delay curve** (re-enter every signal 0–12 bars later), score
calibration by quintile, and Spearman IC per sub-model so you can see whether tCISD or the
market-neutral model is actually carrying the edge — or whether it is all Fibonacci and hope.

**7 · The research agent.** Headlines are parsed from metals and macro RSS, scored by a
gold-specific lexicon, and scanned for scheduled-event language that shrinks size around FOMC,
CPI and NFP. With an LLM key the model receives the headlines *and* the live quantitative
context and returns a structured brief — regime, narrative, catalyst map, event guidance, bias
with confidence, and the reasons the technical signal could be wrong.

### Reading the output

| panel | good sign | bad sign |
|---|---|---|
| tCISD | high, and the event is 0–3 bars old | high score on an event 15+ bars old |
| SSMT | divergence agrees with your direction | gold's high unconfirmed by silver |
| Fibonacci | price in the 62–79 % OTE, at a time node | buying at the tip (retracement < 0.2) |
| Market-neutral | \|z\| > 2 with Hurst < 0.5 if fading | \|z\| extreme but Hurst > 0.5 (trending) |
| Reversal risk | low, AUC well above 0.5 | high, or AUC ≈ 0.5 (model has no skill) |
| Exhaustion | low against your direction | > 70 against your direction |
| Delay curve | expectancy peaks at your chosen lag | immediate entry is the worst lag |
| Calibration | mean R rises monotonically with score | top bucket is not the best bucket |

### Project structure

```
gold_trade_timing_analyzer/
├── gold_trade_timing_analyzer.py   # Streamlit app: UI, orchestration, sizing
├── core/
│   ├── config.py                   # every tunable parameter, in one place
│   ├── data_feeds.py               # provider chain, browser bridge, synthetic fallback
│   ├── indicators.py               # ATR/RSI/swings + ADF, OU, Hurst, ridge, RLS, IRLS, AUC
│   ├── structure.py                # tCISD engine, fair value gaps, Fibonacci legs
│   ├── intermarket.py              # SSMT + market-neutral return/level models
│   ├── risk.py                     # divergence, exhaustion, walk-forward reversal model
│   ├── signal_engine.py            # composite ETS, gates, trade construction
│   ├── backtest.py                 # MAE/MFE, delay curve, calibration, attribution
│   ├── research_agent.py           # RSS, lexicon scoring, LLM brief
│   ├── llm.py                      # six-provider HTTP client
│   └── plots.py                    # plotly figures
├── server/                         # ── the full-stack app ──
│   ├── main.py                     # FastAPI app, 29 routes, WebSocket hub, static SPA
│   ├── engine_service.py           # engine → JSON bridge (the only place frames become dicts)
│   ├── alerts.py                   # gating, cooldown, dispatch to webhook/Telegram/Discord
│   ├── store.py                    # in-memory market store + SQLite signal journal
│   └── models.py                   # pydantic schemas
├── web/                            # React + Vite dashboard
│   ├── src/marketData.js           # browser-side fetch: tokenised gold, Yahoo via CORS relay
│   ├── src/PriceChart.jsx          # candles + FVG / fib / CISD / trade-level overlays (SVG)
│   ├── src/panels/                 # EntryTiming · Models · Backtest · Alerts · Journal · News · Data
│   └── dist/                       # build output, served by FastAPI
├── tests/                          # engine tests, Streamlit smoke test, API integration test
├── data/                           # drop broker CSV exports here
├── Dockerfile                      # two-stage: node build → python serve, one port
├── docker-compose.yml
├── requirements-server.txt         # backend-only dependencies (no Streamlit)
├── DESIGN.md                       # full system design and model mathematics
└── requirements.txt                # Streamlit app dependencies
```

Run the tests with:

```bash
python tests/test_engine.py              # analytic core
GOLD_TIMING_NO_BRIDGE=1 python tests/test_app_smoke.py   # whole Streamlit app, head-less
pytest tests/test_api.py -q              # full-stack API end-to-end (ingest → alerts)
```

### What It Can (and Cannot) Do

**Can do**

- Detect and **time** a change in state of delivery, with an explicit decay model
- Quantify intermarket non-confirmation statistically rather than visually
- Price gold against its drivers and isolate the unexplained (market-neutral) component
- Estimate reversal probability with a walk-forward model and report its own AUC
- Tell you, empirically, whether to enter now or wait for the retrace
- Attribute realised P&L to individual sub-models via information coefficients
- Run entirely offline, with internet, or behind a locked network via the browser bridge
- Push a qualified setup to your phone the bar it qualifies, and grade it afterwards

**Cannot do**

- Predict news. It scores the flow that has already printed and flags scheduled events.
- Model your fill quality, slippage in gaps, or your broker's book.
- See order-book depth, COT positioning or real ETF flows from the free feeds used here.
- Turn a low-conviction setup into a good trade. Below the gates it stays flat by design.
- Trade tokenised-gold venues as if they were spot — they carry a small, variable premium.

### Disclaimer

Research and education only. **Not investment advice.** Backtested statistics are computed on
historical data under explicit assumptions about spread, commission and bar-level fills, and
they are not a forecast. Gold is leveraged, gap-prone and event-driven. Never risk capital you
cannot afford to lose.
