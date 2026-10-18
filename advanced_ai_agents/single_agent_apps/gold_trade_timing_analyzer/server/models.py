"""Pydantic schemas for the Gold Timing Desk API."""
from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class SeriesIn(BaseModel):
    """One instrument's OHLCV history plus its provenance.

    `bars` is a compact list of [epoch_ms, open, high, low, close, volume] rows.
    """

    source: str = "unknown"
    kind: str = "real"                     # real | proxy | synthetic
    note: str = ""
    bars: List[List[float]] = Field(default_factory=list)


class SpotIn(BaseModel):
    price: Optional[float] = None
    source: str = "unknown"
    kind: str = "real"
    ts: Optional[str] = None


class IngestPayload(BaseModel):
    interval: str = "1h"
    series: Dict[str, SeriesIn] = Field(default_factory=dict)
    spot: Optional[SpotIn] = None


class ParamsIn(BaseModel):
    values: Dict[str, float] = Field(default_factory=dict)


class NewsIngestPayload(BaseModel):
    """Raw RSS/Atom bodies captured by the browser, keyed by feed name."""

    feeds: Dict[str, str] = Field(default_factory=dict)


class ResearchIn(BaseModel):
    provider: str = "groq"
    model: str = ""
    api_key: str = ""
    base_url: Optional[str] = None
    payload: Optional[dict] = None     # when set, the browser already called the LLM


class AlertChannel(BaseModel):
    type: str = "webhook"              # webhook | telegram | discord | log
    enabled: bool = True
    url: str = ""
    token: str = ""
    chat_id: str = ""


class AlertConfig(BaseModel):
    enabled: bool = True
    min_tier: str = "A"                # A | B | C
    min_score: float = 70.0
    directions: List[str] = Field(default_factory=lambda: ["LONG", "SHORT"])
    cooldown_bars: int = 12
    poll_seconds: int = 60
    channels: List[AlertChannel] = Field(default_factory=list)


class AlertTestIn(BaseModel):
    channel: AlertChannel
