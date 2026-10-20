# 🪙 Gold Trace

**Layered gold-market intelligence.** No data source tracks physical gold
live — vault and bank data arrive with delays — so Gold Trace is built in
**layers ordered by update speed**, feeding scoring engines, an event
reaction matrix, anomaly/whale alerts, and a trading journal. Output: a
canvas dashboard + Telegram alerts.

![stack](https://img.shields.io/badge/stack-FastAPI_·_SQLite_·_vanilla_canvas-gold)

## The ten layers

| # | Layer | Sources | Speed |
|---|-------|---------|-------|
| 1 | Price & market | Spot XAU · COMEX GC futures · options open interest | **Live** |
| 2 | On-chain whales | PAXG & XAUT transfers (Etherscan / Alchemy) | **Live** |
| 3 | Macro | TIPS 10y (FRED) · DXY · Brent · FedWatch · PCE/NFP calendar | Live → monthly |
| 4 | Funds | GLD & IAU holdings and flows | Daily |
| 5 | Exchange stock | COMEX warehouse registered / eligible | Daily |
| 6 | Asia demand | SGE premium & withdrawals · India premium | Daily → weekly |
| 7 | Funds positioning | CFTC COT — managed money vs commercials | Weekly |
| 8 | Central banks | WGC · IMF IFS · PBoC announcements | Monthly, lagged 1–2 mo |
| 9 | Trade flows | Swiss exports · UK & China customs | Monthly |
| 10 | Institutions | 13F filings (SEC EDGAR) | Quarterly |

One small scheduled job per layer writes to SQLite (swap in TimescaleDB —
only `app/db.py` speaks SQL). Every observation is tagged `mode=live|sim`:
**without API keys the ingestors fall back to a built-in simulator so the
whole pipeline is demonstrable end-to-end**; add keys and the same code
paths go live, layer by layer.

## Engines

- **Structural score** (−100…+100): central-bank 3-month trend + ETF 5-day
  flows + COMEX registered 20-day change + SGE premium → the long-term bias.
- **Macro score**: TIPS 5-day Δ (bp, inverted) + DXY 5-day Δ% (inverted) +
  FedWatch cut-odds drift + Brent tilt → the directional lean.
- **Composite** = 0.6·structural + 0.4·macro → `LONG BIAS / BALANCED /
  DEFENSIVE SHORT`, plus an event modifier when structure is extreme.
- **Event engine**: PCE/NFP countdown, consensus vs actual, and a written
  **reaction matrix** (surprise bucket → yield/DXY chain → expected gold
  move → the play). Releases score matrix credibility over time.
- **Anomaly & whale alerts**: ≥5k oz single PAXG/XAUT transfers, ≥15k oz
  hourly aggregate, |GLD daily flow| ≥ 4t, COMEX registered −2.5%/7d,
  SGE premium ≥ $55 or +$25/5d, |spot 1h| ≥ 1.2%, |COT weekly Δ| ≥ 30k.
  Each trigger → dashboard feed + Telegram (real send when
  `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` are set, visible outbox otherwise).
- **Journal**: log each setup (date, type, direction, level, trigger,
  result, R, notes, screenshot ref) → weekly win-rate report by setup type
  and by ISO week.

## Run it

```bash
cd gold_trace_dashboard
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
# → http://localhost:8000   (API docs at /docs)
```

First boot backfills 90 days of history (intraday spot for 10 days, daily
fund/vault/Asia bars, weekly COT, monthly central-bank & trade prints,
quarterly 13F aggregates, a whale tape, released events, and ~5 weeks of
journal entries) so every chart, score and report works immediately.

### Going live

Copy `.env.example` → `.env` (or export vars) and fill any subset:

```
GOLDAPI_IO_KEY=…        # spot XAU live
ETHERSCAN_API_KEY=…     # PAXG/XAUT whale tape live
FRED_API_KEY=…          # TIPS / DXY / Brent live
TELEGRAM_BOT_TOKEN=…    # real alert delivery
TELEGRAM_CHAT_ID=…
```

Layers flip from `SIM` to `LIVE` chips on the dashboard as keys appear.
For production cadence, point cron/systemd timers at the same
`app.ingestors.*.run()` functions and drop `DEMO_SPEEDUP` scheduling.

## The dashboard

Vanilla HTML/CSS/JS — **no CDN dependencies**. All charts and the three
score gauges are hand-rolled canvas renderers (DPR-aware, phone-tuned font
sizes). The hero is a staged canvas animation that loops four stages,
mirroring the system itself:

1. **grow** — recursive branching with randomized angles & lengths (node
   budget capped: ≤200 segments desktop / ≤110 phone)
2. **strike** — a lightning bolt finds the crown, staged flicker frames
   flash every branch
3. **weave** — a spider hub ties satellites (layers) together with spokes,
   web rings and pulse waves
4. **trace** — gold motes ride the branch paths with overlay label chips
   (`XAU $4,085`, `SGE +$34`, `GLD +6.2 t`, …)

`prefers-reduced-motion` renders one static composed frame. The whole page
polls a single aggregate endpoint (`/api/bootstrap`) every 5s — one request
per refresh on phones.

## API

| Endpoint | What |
|----------|------|
| `GET /api/bootstrap` | everything the UI needs, one call |
| `GET /api/layers` | per-layer last update, mode, status, latency |
| `GET /api/scores` | structural + macro + composite with factor breakdown |
| `GET /api/series/{name}?points=N` | any raw series |
| `GET /api/whales` | whale tape + 24h aggregate |
| `GET /api/events` | countdown, matrix, history, credibility |
| `GET /api/alerts` | alert feed with Telegram delivery status |
| `GET/POST /api/journal` · `GET /api/report` | journal + weekly win-rate |
| `GET /api/status` | scheduler & ingest health |

## Layout

```
gold_trace_dashboard/
├── app/
│   ├── config.py            # optional keys, demo cadence
│   ├── db.py                # SQLite (WAL) — the only SQL in the codebase
│   ├── scheduler.py         # one loop, per-layer intervals
│   ├── seed.py              # 90-day backfill on first boot
│   ├── telegram_alerts.py   # real send + outbox fallback
│   ├── ingestors/           # one module per layer (live-first, sim fallback)
│   └── engines/             # structural · macro · events · anomalies · journal
└── static/                  # index.html · style.css · app.js · trace.js
```

> ⚠️ Educational tooling, not investment advice. Simulated observations are
> clearly tagged `SIM` everywhere in the UI.
