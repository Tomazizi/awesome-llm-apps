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
METALS_API_KEY = os.environ.get("METALS_API_KEY", "")
GOLDAPI_IO_KEY = os.environ.get("GOLDAPI_IO_KEY", "")
ETHERSCAN_API_KEY = os.environ.get("ETHERSCAN_API_KEY", "")
ALCHEMY_API_KEY = os.environ.get("ALCHEMY_API_KEY", "")
FRED_API_KEY = os.environ.get("FRED_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# Webhook for automation (generic outbound)
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")

# ---------------------------------------------------------------- run mode
DEMO_SPEEDUP = float(os.environ.get("DEMO_SPEEDUP", "1"))
SIM_SHOCKS = os.environ.get("SIM_SHOCKS", "1") == "1"

# ---------------------------------------------------------------- autopilot
AUTOPILOT_ENABLED = os.environ.get("AUTOPILOT_ENABLED", "1") == "1"
AUTOPILOT_MODE = os.environ.get("AUTOPILOT_MODE", "assist")  # off | assist | full_auto
PAPER_ENABLED = os.environ.get("PAPER_ENABLED", "1") == "1"
PAPER_INITIAL_BALANCE = float(os.environ.get("PAPER_INITIAL_BALANCE", "50000"))
PAPER_RISK_PER_TRADE = float(os.environ.get("PAPER_RISK_PER_TRADE", "0.012"))  # 1.2% per trade
PAPER_MAX_OPEN = int(os.environ.get("PAPER_MAX_OPEN", "3"))
AUTO_REPORT_ENABLED = os.environ.get("AUTO_REPORT_ENABLED", "1") == "1"

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
    # automation loops
    "automation":      max(4.0,  8   / DEMO_SPEEDUP),
    "signals":         max(5.0,  10  / DEMO_SPEEDUP),
    "paper_trading":   max(5.0,  10  / DEMO_SPEEDUP),
}

PAXG_ADDRESS = "0x45804880De22913dAFE09f4980848ECE6EcbAf78"
XAUT_ADDRESS = "0x68749665FF8D2d112Fa859AA293F07A622782F38"

GLD_ETF = "GLD"
IAU_ETF = "IAU"

def telegram_configured() -> bool:
    return bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)
