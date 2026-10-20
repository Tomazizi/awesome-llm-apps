"""Gold Trace configuration.

Every secret/endpoint is optional. When a key is missing (or the network
call fails) the matching ingestor transparently falls back to the built-in
simulator so the dashboard is always alive. Deploy with real keys and the
same code path starts writing LIVE observations.
"""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = os.environ.get("GOLD_TRACE_DB", str(DATA_DIR / "gold_trace.db"))

# ---------------------------------------------------------------- live keys
# Price / market
METALS_API_KEY = os.environ.get("METALS_API_KEY", "")            # metals-api.com / goldapi.io
GOLDAPI_IO_KEY = os.environ.get("GOLDAPI_IO_KEY", "")            # goldapi.io XAU
# On-chain whales
ETHERSCAN_API_KEY = os.environ.get("ETHERSCAN_API_KEY", "")      # PAXG / XAUT ERC-20 transfers
ALCHEMY_API_KEY = os.environ.get("ALCHEMY_API_KEY", "")          # fallback RPC
# Macro (FRED)
FRED_API_KEY = os.environ.get("FRED_API_KEY", "")                # DFII10 (TIPS), DTWEXBGS (DXY), DCOILBRENTEU
# Alerts
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# ---------------------------------------------------------------- run mode
# DEMO_SPEEDUP compresses the "daily/weekly/monthly" ingest cadence so a
# fresh visitor sees every layer tick within minutes instead of days.
DEMO_SPEEDUP = float(os.environ.get("DEMO_SPEEDUP", "1"))        # 1 = demo cadence below
SIM_SHOCKS = os.environ.get("SIM_SHOCKS", "1") == "1"            # inject anomalies in sim mode

# tick intervals in seconds (already demo-compressed; real deploy would use
# cron-style schedules: live=5s..1m, daily=1h, weekly=daily, monthly=daily)
INTERVALS = {
    "price_market":   max(3.0,  4   / DEMO_SPEEDUP),
    "onchain":        max(4.0,  6   / DEMO_SPEEDUP),
    "macro":          max(6.0,  12  / DEMO_SPEEDUP),
    "funds":          max(15.0, 45  / DEMO_SPEEDUP),
    "exchange_stock": max(15.0, 50  / DEMO_SPEEDUP),
    "asia_demand":    max(20.0, 60  / DEMO_SPEEDUP),
    "positioning":    max(30.0, 120 / DEMO_SPEEDUP),
    "central_banks":  max(45.0, 240 / DEMO_SPEEDUP),
    "trade_flows":    max(60.0, 300 / DEMO_SPEEDUP),
    "institutions":   max(90.0, 480 / DEMO_SPEEDUP),
}

# ---------------------------------------------------------------- assets
PAXG_ADDRESS = "0x45804880De22913dAFE09f4980848ECE6EcbAf78"
XAUT_ADDRESS = "0x68749665FF8D2d112Fa859AA293F07A622782F38"

GLD_ETF = "GLD"
IAU_ETF = "IAU"

def telegram_configured() -> bool:
    return bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)
