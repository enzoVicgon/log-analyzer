"""Aggregate parsed log entries in one streaming pass."""

from __future__ import annotations

import heapq
from collections import Counter
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone

from app.models import (
    EndpointCount,
    ErrorCount,
    LogEntry,
    SlowRequest,
    StatusClassCount,
    StatusCount,
    Stats,
    TimeBucket,
)
from app.parser import parse_line

#: A response is considered an error at or above this status code.
ERROR_STATUS = 400

_MINUTE_MAX_SPAN = timedelta(hours=3)
_HOUR_MAX_SPAN = timedelta(days=4)

_STEPS: dict[str, timedelta] = {
    "minute": timedelta(minutes=1),
    "hour": timedelta(hours=1),
    "day": timedelta(days=1),
}

_MAX_BUCKETS = 1000


def _truncate(ts: datetime, granularity: str) -> datetime:
    """Round a timestamp down to the start of its bucket."""
    if granularity == "minute":
        return ts.replace(second=0, microsecond=0)
    if granularity == "hour":
        return ts.replace(minute=0, second=0, microsecond=0)
    return ts.replace(hour=0, minute=0, second=0, microsecond=0)


def _pick_granularity(span: timedelta) -> str:
    if span < _MINUTE_MAX_SPAN:
        return "minute"
    if span < _HOUR_MAX_SPAN:
        return "hour"
    return "day"


def build_timeline(
    minute_requests: Counter[datetime],
    minute_errors: Counter[datetime],
    first: datetime | None,
    last: datetime | None,
) -> tuple[list[TimeBucket], str]:
    """Choose a timeline granularity, downsample, and fill gaps."""
    if first is None or last is None:
        return [], "minute"

    granularity = _pick_granularity(last - first)

    requests: Counter[datetime] = Counter()
    errors: Counter[datetime] = Counter()
    for ts, count in minute_requests.items():
        requests[_truncate(ts, granularity)] += count
    for ts, count in minute_errors.items():
        errors[_truncate(ts, granularity)] += count

    step = _STEPS[granularity]
    start = _truncate(first, granularity)
    end = _truncate(last, granularity)

    if int((end - start) / step) + 1 > _MAX_BUCKETS:
        return (
            [
                TimeBucket(timestamp=ts, requests=requests[ts], errors=errors[ts])
                for ts in sorted(requests)
            ],
            granularity,
        )

    buckets: list[TimeBucket] = []
    cursor = start
    while cursor <= end:
        buckets.append(
            TimeBucket(
                timestamp=cursor,
                requests=requests.get(cursor, 0),
                errors=errors.get(cursor, 0),
            )
        )
        cursor += step
    return buckets, granularity


def analyze(lines: Iterable[str], *, top_n: int = 10, slow_n: int = 10) -> Stats:
    """Consume an iterable of raw log lines and return the full statistics."""
    total = 0
    malformed = 0
    total_bytes = 0
    unique_ips: set[str] = set()

    status_counts: Counter[int] = Counter()
    endpoint_counts: Counter[str] = Counter()
    error_counts: Counter[tuple[int, str]] = Counter()

    minute_requests: Counter[datetime] = Counter()
    minute_errors: Counter[datetime] = Counter()

    first_ts: datetime | None = None
    last_ts: datetime | None = None

    timing_count = 0
    timing_total = 0.0

    slowest: list[tuple[float, int, LogEntry]] = []
    sequence = 0

    for line in lines:
        if not line.strip():
            continue

        entry = parse_line(line)
        if entry is None:
            malformed += 1
            continue

        total += 1
        total_bytes += entry.bytes_sent
        unique_ips.add(entry.ip)
        status_counts[entry.status] += 1

        endpoint = entry.path.split("?", 1)[0]
        endpoint_counts[endpoint] += 1

        ts = entry.timestamp.astimezone(timezone.utc)
        if first_ts is None or ts < first_ts:
            first_ts = ts
        if last_ts is None or ts > last_ts:
            last_ts = ts

        minute = ts.replace(second=0, microsecond=0)
        minute_requests[minute] += 1

        if entry.status >= ERROR_STATUS:
            error_counts[(entry.status, endpoint)] += 1
            minute_errors[minute] += 1

        if entry.request_time is not None:
            timing_count += 1
            timing_total += entry.request_time

            sequence += 1
            candidate = (entry.request_time, sequence, entry)
            if len(slowest) < slow_n:
                heapq.heappush(slowest, candidate)
            elif entry.request_time > slowest[0][0]:
                heapq.heapreplace(slowest, candidate)

    error_total = sum(c for status, c in status_counts.items() if status >= ERROR_STATUS)

    status_classes: Counter[str] = Counter()
    for status, count in status_counts.items():
        status_classes[f"{status // 100}xx"] += count

    timeline, granularity = build_timeline(
        minute_requests, minute_errors, first_ts, last_ts
    )

    return Stats(
        total_requests=total,
        malformed_lines=malformed,
        unique_visitors=len(unique_ips),
        total_bytes=total_bytes,
        error_rate=round(error_total / total * 100, 2) if total else 0.0,
        first_timestamp=first_ts,
        last_timestamp=last_ts,
        status_counts=[
            StatusCount(status=status, count=count)
            for status, count in sorted(status_counts.items())
        ],
        status_classes=[
            StatusClassCount(label=label, count=status_classes[label])
            for label in sorted(status_classes)
        ],
        top_endpoints=[
            EndpointCount(path=path, count=count)
            for path, count in endpoint_counts.most_common(top_n)
        ],
        top_errors=[
            ErrorCount(status=status, path=path, count=count)
            for (status, path), count in error_counts.most_common(top_n)
        ],
        timing_available=timing_count > 0,
        avg_response_time=(
            round(timing_total / timing_count, 4) if timing_count else None
        ),
        slowest_requests=[
            SlowRequest(
                request_time=entry.request_time,
                method=entry.method,
                path=entry.path,
                status=entry.status,
                timestamp=entry.timestamp,
            )
            for _, _, entry in sorted(slowest, key=lambda item: item[0], reverse=True)
            if entry.request_time is not None
        ],
        timeline=timeline,
        timeline_granularity=granularity,
    )
