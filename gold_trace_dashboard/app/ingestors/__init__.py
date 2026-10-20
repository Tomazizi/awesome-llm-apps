"""Ingestor registry — one small scheduled job per source layer.

Ordered by update speed; `LAYER_META` drives the dashboard's layer map.
"""
from __future__ import annotations

from . import (price_market, onchain, macro, funds, exchange_stock,
               asia_demand, positioning, central_banks, trade_flows,
               institutions)

JOBS = [
    price_market.run,
    onchain.run,
    macro.run,
    funds.run,
    exchange_stock.run,
    asia_demand.run,
    positioning.run,
    central_banks.run,
    trade_flows.run,
    institutions.run,
]

LAYER_META = [
    {"source": "price_market",   "layer": "Price & market",   "speed": "Live",
     "detail": "Spot XAU · COMEX GC futures · options open interest"},
    {"source": "onchain",        "layer": "On-chain whales",  "speed": "Live",
     "detail": "PAXG & XAUT transfers (Etherscan / Alchemy)"},
    {"source": "macro",          "layer": "Macro",            "speed": "Live → monthly",
     "detail": "TIPS 10y (FRED) · DXY · Brent · FedWatch · PCE/NFP calendar"},
    {"source": "funds",          "layer": "Funds",            "speed": "Daily",
     "detail": "GLD & IAU holdings and flows"},
    {"source": "exchange_stock", "layer": "Exchange stock",   "speed": "Daily",
     "detail": "COMEX warehouse registered / eligible stocks"},
    {"source": "asia_demand",    "layer": "Asia demand",      "speed": "Daily → weekly",
     "detail": "SGE premium & withdrawals · India premium"},
    {"source": "positioning",    "layer": "Funds positioning","speed": "Weekly",
     "detail": "CFTC COT — managed money vs. commercials"},
    {"source": "central_banks",  "layer": "Central banks",    "speed": "Monthly (lag 1–2m)",
     "detail": "WGC · IMF IFS · PBoC announcements"},
    {"source": "trade_flows",    "layer": "Trade flows",      "speed": "Monthly",
     "detail": "Swiss exports · UK & China customs"},
    {"source": "institutions",   "layer": "Institutions",     "speed": "Quarterly",
     "detail": "13F filings (SEC EDGAR)"},
]
