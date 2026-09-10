"""Tests for the log line parser."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.parser import parse_line

PDT = timezone(timedelta(hours=-7))

CANONICAL = (
    '127.0.0.1 - frank [10/Oct/2000:13:55:36 -0700] "GET /apache_pb.gif HTTP/1.0" '
    '200 2326 "http://example.com/start.html" "Mozilla/4.08 [en] (Win98; I ;Nav)"'
)


def test_canonical_combined_line() -> None:
    entry = parse_line(CANONICAL)

    assert entry is not None
    assert entry.ip == "127.0.0.1"
    assert entry.timestamp == datetime(2000, 10, 10, 13, 55, 36, tzinfo=PDT)
    assert entry.method == "GET"
    assert entry.path == "/apache_pb.gif"
    assert entry.protocol == "HTTP/1.0"
    assert entry.status == 200
    assert entry.bytes_sent == 2326
    assert entry.referrer == "http://example.com/start.html"
    assert entry.user_agent == "Mozilla/4.08 [en] (Win98; I ;Nav)"
    assert entry.request_time is None


def test_timestamp_is_timezone_aware() -> None:
    """Parsed timestamps must remain timezone-aware."""
    entry = parse_line(CANONICAL)

    assert entry is not None
    assert entry.timestamp.tzinfo is not None
    assert entry.timestamp.utcoffset() == timedelta(hours=-7)


def test_extended_format_captures_request_time() -> None:
    entry = parse_line(CANONICAL + " 0.043")

    assert entry is not None
    assert entry.request_time == pytest.approx(0.043)


def test_common_log_format_without_referrer_and_agent() -> None:
    """Common Log Format omits the referrer and user-agent."""
    entry = parse_line(
        '127.0.0.1 - - [10/Oct/2000:13:55:36 -0700] "GET /a HTTP/1.1" 200 512'
    )

    assert entry is not None
    assert entry.path == "/a"
    assert entry.bytes_sent == 512
    assert entry.referrer is None
    assert entry.user_agent is None


def test_dash_in_bytes_field_means_zero() -> None:
    """A dash in the bytes field represents zero bytes."""
    entry = parse_line(
        '127.0.0.1 - - [10/Oct/2000:13:55:36 -0700] "GET /f.ico HTTP/1.1" 304 - "-" "UA"'
    )

    assert entry is not None
    assert entry.bytes_sent == 0


@pytest.mark.parametrize("raw", ["-", ""])
def test_absent_referrer_spellings_become_none(raw: str) -> None:
    entry = parse_line(
        f'127.0.0.1 - - [10/Oct/2000:13:55:36 -0700] "GET /a HTTP/1.1" 200 1 "{raw}" "UA"'
    )

    assert entry is not None
    assert entry.referrer is None


def test_query_string_is_preserved_verbatim() -> None:
    """The parser preserves the query string."""
    entry = parse_line(
        '127.0.0.1 - - [10/Oct/2000:13:55:36 -0700] "GET /s?q=1&r=2 HTTP/1.1" 200 1 "-" "UA"'
    )

    assert entry is not None
    assert entry.path == "/s?q=1&r=2"


def test_http_0_9_request_line_without_protocol() -> None:
    entry = parse_line(
        '127.0.0.1 - - [10/Oct/2000:13:55:36 -0700] "GET /a" 200 1 "-" "UA"'
    )

    assert entry is not None
    assert entry.method == "GET"
    assert entry.path == "/a"
    assert entry.protocol == ""


def test_quoted_fields_do_not_bleed_into_each_other() -> None:
    """Quoted fields must not consume neighboring fields."""
    entry = parse_line(
        '127.0.0.1 - - [10/Oct/2000:13:55:36 -0700] "GET /a HTTP/1.1" 200 1 '
        '"http://ref/" "Mozilla/5.0 (X11; Linux) Gecko"'
    )

    assert entry is not None
    assert entry.referrer == "http://ref/"
    assert entry.user_agent == "Mozilla/5.0 (X11; Linux) Gecko"


def test_trailing_newline_is_tolerated() -> None:
    """File iteration includes the line ending."""
    assert parse_line(CANONICAL + "\n") is not None
    assert parse_line(CANONICAL + "\r\n") is not None


@pytest.mark.parametrize(
    "raw",
    [
        "10/Oct/2000:13:55:36 -0700",
        "01/Jan/1999:00:00:00 +0000",
        "31/Dec/2024:23:59:59 +0530",
        "29/Feb/2024:12:00:00 -0300",  # leap day
        "05/Aug/2025:07:08:09 +1245",  # non-hour-aligned offset
    ],
)
def test_fast_timestamp_parser_agrees_with_strptime(raw: str) -> None:
    """The fast timestamp path must match ``strptime``."""
    from app.parser import _TIMESTAMP_FORMAT, _parse_timestamp

    assert _parse_timestamp(raw) == datetime.strptime(raw, _TIMESTAMP_FORMAT)


@pytest.mark.parametrize(
    "raw",
    [
        "32/Oct/2000:13:55:36 -0700",  # no such day
        "10/Xyz/2000:13:55:36 -0700",  # no such month
        "10/Oct/2000:25:00:00 -0700",  # no such hour
        "29/Feb/2023:12:00:00 +0000",  # 2023 was not a leap year
        "10/Oct/2000:13:55:36",  # missing offset entirely
        "not a timestamp",
    ],
)
def test_invalid_timestamps_return_none(raw: str) -> None:
    from app.parser import _parse_timestamp

    assert _parse_timestamp(raw) is None


@pytest.mark.parametrize(
    ("label", "line"),
    [
        ("blank", ""),
        ("whitespace only", "   "),
        ("free text", "this is not a log line"),
        ("truncated mid-request", '127.0.0.1 - - [10/Oct/2000:13:55:36 -0700] "GET /a'),
        (
            "impossible date",
            '127.0.0.1 - - [99/Xyz/2000:99:99:99 -0700] "GET /a HTTP/1.1" 200 1 "-" "UA"',
        ),
        (
            "empty request line",
            '127.0.0.1 - - [10/Oct/2000:13:55:36 -0700] "-" 400 0 "-" "-"',
        ),
        (
            "unencoded space in path",
            '127.0.0.1 - - [10/Oct/2000:13:55:36 -0700] "GET /foo bar HTTP/1.1" 200 1 "-" "UA"',
        ),
        (
            "non-numeric status",
            '127.0.0.1 - - [10/Oct/2000:13:55:36 -0700] "GET /a HTTP/1.1" OK 1 "-" "UA"',
        ),
        ("binary garbage", "\x00\x01\x02\xff scanner noise"),
    ],
)
def test_unparseable_lines_return_none(label: str, line: str) -> None:
    """Invalid input returns ``None``."""
    assert parse_line(line) is None, label
