"""Internal data types and API response models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel


@dataclass(slots=True)
class LogEntry:
    """One successfully parsed log line."""

    ip: str
    timestamp: datetime  # always timezone-aware
    method: str
    path: str
    protocol: str
    status: int
    bytes_sent: int
    referrer: str | None
    user_agent: str | None
    request_time: float | None = None


class StatusCount(BaseModel):
    status: int
    count: int


class StatusClassCount(BaseModel):
    """Requests grouped into 2xx / 3xx / 4xx / 5xx."""

    label: str
    count: int


class EndpointCount(BaseModel):
    path: str
    count: int


class ErrorCount(BaseModel):
    status: int
    path: str
    count: int


class SlowRequest(BaseModel):
    request_time: float
    method: str
    path: str
    status: int
    timestamp: datetime


class TimeBucket(BaseModel):
    """One point on the traffic timeline."""

    timestamp: datetime
    requests: int
    errors: int


class Stats(BaseModel):
    """The complete analysis of one log file. This is the API response."""

    # Headline numbers
    total_requests: int
    malformed_lines: int
    unique_visitors: int
    total_bytes: int
    error_rate: float  # percentage of requests with status >= 400

    # Time span covered by the log (None if nothing parsed)
    first_timestamp: datetime | None
    last_timestamp: datetime | None

    # Breakdowns
    status_counts: list[StatusCount]
    status_classes: list[StatusClassCount]
    top_endpoints: list[EndpointCount]
    top_errors: list[ErrorCount]

    # Timing. ``timing_available`` is False for stock Combined Log Format,
    # which has no $request_time field; the frontend hides its timing panels.
    timing_available: bool
    avg_response_time: float | None
    slowest_requests: list[SlowRequest]

    # Traffic over time
    timeline: list[TimeBucket]
    timeline_granularity: str  # "minute" | "hour" | "day"