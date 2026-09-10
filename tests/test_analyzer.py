"""Tests for streaming log aggregation."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.analyzer import analyze

FIXTURES = Path(__file__).parent / "fixtures"


def make_line(
    ts: str,
    *,
    path: str = "/",
    status: int = 200,
    ip: str = "10.0.0.1",
    sent: str = "100",
    request_time: str | None = None,
) -> str:
    """Build one Combined Log Format line. ``ts`` is like '10/Oct/2000:13:55:36'."""
    base = (
        f'{ip} - - [{ts} +0000] "GET {path} HTTP/1.1" {status} {sent} "-" "UA"'
    )
    return base if request_time is None else f"{base} {request_time}"


@pytest.fixture
def sample_stats():
    return analyze((FIXTURES / "sample.log").read_text(encoding="utf-8").splitlines())


def test_counts_parsed_and_malformed_lines(sample_stats) -> None:
    assert sample_stats.total_requests == 20
    assert sample_stats.malformed_lines == 5


def test_blank_lines_are_skipped_not_counted_as_malformed(sample_stats) -> None:
    """The fixture has one blank line; 26 lines total, 20 + 5 accounted for."""
    assert sample_stats.total_requests + sample_stats.malformed_lines == 25


def test_unique_visitors_and_bytes(sample_stats) -> None:
    assert sample_stats.unique_visitors == 7
    assert sample_stats.total_bytes == 20925


def test_error_rate(sample_stats) -> None:
    """6 of 20 requests are 4xx or 5xx."""
    assert sample_stats.error_rate == pytest.approx(30.0)


def test_status_counts(sample_stats) -> None:
    counts = {sc.status: sc.count for sc in sample_stats.status_counts}
    assert counts == {200: 11, 204: 1, 301: 1, 304: 1, 401: 1, 403: 1, 404: 2, 500: 2}


def test_status_classes(sample_stats) -> None:
    classes = {sc.label: sc.count for sc in sample_stats.status_classes}
    assert classes == {"2xx": 12, "3xx": 2, "4xx": 4, "5xx": 2}


def test_top_endpoint(sample_stats) -> None:
    top = sample_stats.top_endpoints[0]
    assert top.path == "/index.html"
    assert top.count == 4


def test_query_strings_are_stripped_for_endpoint_grouping(sample_stats) -> None:
    """/api/users?page=1 and ?page=2 must aggregate into one endpoint."""
    endpoints = {e.path: e.count for e in sample_stats.top_endpoints}
    assert endpoints["/api/users"] == 2
    assert not any("?" in path for path in endpoints)


def test_top_errors_exclude_successful_requests(sample_stats) -> None:
    errors = {(e.status, e.path): e.count for e in sample_stats.top_errors}
    assert errors == {
        (404, "/missing"): 2,
        (500, "/api/report"): 2,
        (401, "/api/login"): 1,
        (403, "/admin"): 1,
    }


def test_timing_stats(sample_stats) -> None:
    assert sample_stats.timing_available is True
    assert sample_stats.avg_response_time == pytest.approx(0.3315)


def test_slowest_requests_are_ordered_descending(sample_stats) -> None:
    slowest = sample_stats.slowest_requests
    assert [s.request_time for s in slowest[:3]] == pytest.approx([3.1, 2.5, 0.21])
    assert slowest[0].path == "/api/report"
    assert slowest[0].status == 500


def test_slowest_heap_is_capped_and_still_correct() -> None:
    """With far more rows than slots, the bounded heap must still find the top N."""
    lines = [
        make_line(f"10/Oct/2000:13:55:{i:02d}", path=f"/p{i}", request_time=f"{i / 100:.2f}")
        for i in range(60)
    ]
    stats = analyze(lines, slow_n=5)

    assert len(stats.slowest_requests) == 5
    assert [s.request_time for s in stats.slowest_requests] == pytest.approx(
        [0.59, 0.58, 0.57, 0.56, 0.55]
    )


def test_timing_unavailable_for_stock_combined_format() -> None:
    stats = analyze((FIXTURES / "no_timing.log").read_text(encoding="utf-8").splitlines())

    assert stats.total_requests == 4
    assert stats.timing_available is False
    assert stats.avg_response_time is None
    assert stats.slowest_requests == []


def test_timeline_uses_minute_granularity_for_a_short_log(sample_stats) -> None:
    assert sample_stats.timeline_granularity == "minute"
    assert len(sample_stats.timeline) == 5
    assert [b.requests for b in sample_stats.timeline] == [5, 5, 4, 2, 4]
    assert [b.errors for b in sample_stats.timeline] == [0, 1, 3, 2, 0]


def test_timeline_is_normalised_to_utc(sample_stats) -> None:
    """The fixture is -0700, so 13:55 local is 20:55 UTC."""
    first = sample_stats.timeline[0].timestamp
    assert first.hour == 20
    assert first.minute == 55


def test_timeline_fills_gaps_with_zeros() -> None:
    """An outage must appear as zero buckets, not vanish between two points."""
    lines = [
        make_line("10/Oct/2000:10:00:00"),
        make_line("10/Oct/2000:10:01:00"),
        # ... four idle minutes ...
        make_line("10/Oct/2000:10:06:00"),
    ]
    stats = analyze(lines)

    assert [b.requests for b in stats.timeline] == [1, 1, 0, 0, 0, 0, 1]


@pytest.mark.parametrize(
    ("last_ts", "expected"),
    [
        ("10/Oct/2000:12:00:00", "minute"),  # 2h span
        ("10/Oct/2000:18:00:00", "hour"),  # 8h span
        ("20/Oct/2000:10:00:00", "day"),  # 10d span
    ],
)
def test_granularity_adapts_to_span(last_ts: str, expected: str) -> None:
    stats = analyze([make_line("10/Oct/2000:10:00:00"), make_line(last_ts)])

    assert stats.timeline_granularity == expected
    assert stats.timeline[0].requests == 1
    assert stats.timeline[-1].requests == 1


def test_empty_input_does_not_divide_by_zero() -> None:
    stats = analyze([])

    assert stats.total_requests == 0
    assert stats.error_rate == 0.0
    assert stats.first_timestamp is None
    assert stats.timeline == []


def test_input_with_no_parseable_lines() -> None:
    stats = analyze(["garbage", "more garbage", "", "   "])

    assert stats.total_requests == 0
    assert stats.malformed_lines == 2
    assert stats.timeline == []
