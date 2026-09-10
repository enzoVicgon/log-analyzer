"""Parse nginx and Apache access-log lines."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from app.models import LogEntry

# Compiled once at import. `re` does cache patterns internally, but the cache
# lookup still costs a hash per call, and this runs once per line of a file
# that may have hundreds of thousands of them.
#
# Note the quoted fields use `[^"]*` rather than `.*`. `.*` is greedy and would
# happily swallow everything from the opening quote of the referer to the last
# quote on the line, silently merging two fields into one. `[^"]*` cannot cross
# a quote, so it cannot make that mistake.
_LOG_PATTERN = re.compile(
    r"^(?P<ip>\S+)"  # %h  client address (IPv4, IPv6 or hostname)
    r"\s+(?P<ident>\S+)"  # %l  identd -- effectively always "-"
    r"\s+(?P<user>\S+)"  # %u  auth user, or "-"
    r"\s+\[(?P<timestamp>[^\]]+)\]"  # %t  [10/Oct/2000:13:55:36 -0700]
    r'\s+"(?P<request>[^"]*)"'  # %r  the request line
    r"\s+(?P<status>\d{3})"  # %>s final status code
    r"\s+(?P<bytes>\d+|-)"  # %b  bytes sent -- "-" means zero
    r'(?:\s+"(?P<referrer>[^"]*)"'  # optional: Combined-only fields
    r'\s+"(?P<user_agent>[^"]*)")?'
    r"(?:\s+(?P<request_time>\d+(?:\.\d+)?))?"  # optional: $request_time
    r"\s*$"
)

# The request line is the field an attacker controls most directly, so it gets
# validated separately rather than being split naively on whitespace. The
# protocol is matched strictly as HTTP/<digits> so that a path containing a raw
# space (which is invalid -- it should be percent-encoded) is rejected as
# malformed instead of silently having its tail parsed as the protocol.
#
# The protocol group is optional to allow HTTP/0.9 style request lines ("GET /").
_REQUEST_PATTERN = re.compile(
    r"^(?P<method>[A-Z]+)"
    r"\s+(?P<path>\S+)"
    r"(?:\s+(?P<protocol>HTTP/[\d.]+))?$"
)

_TIMESTAMP_FORMAT = "%d/%b/%Y:%H:%M:%S %z"

# Timestamp parsing was measured at ~60% of total analysis time when done with
# datetime.strptime: it rebuilds a format cache and, worse, calls getlocale() on
# every single invocation. This dedicated parser handles the one fixed shape
# that Apache and nginx actually emit, and falls back to strptime for anything
# it does not recognise -- so it is a pure speedup, never a behaviour change.
_TIMESTAMP_PATTERN = re.compile(
    r"^(\d{1,2})/([A-Za-z]{3})/(\d{4}):(\d{2}):(\d{2}):(\d{2}) ([+-])(\d{2})(\d{2})$"
)

_MONTHS = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}  # fmt: skip

# A log file usually contains only one or two UTC offsets, so building the
# tzinfo object once per distinct offset avoids ~n allocations.
_TZ_CACHE: dict[str, timezone] = {}


def _timezone_for(sign: str, hours: str, minutes: str) -> timezone:
    key = f"{sign}{hours}{minutes}"
    cached = _TZ_CACHE.get(key)
    if cached is None:
        offset = timedelta(hours=int(hours), minutes=int(minutes))
        cached = timezone(-offset if sign == "-" else offset)
        _TZ_CACHE[key] = cached
    return cached


def _parse_timestamp(raw: str) -> datetime | None:
    """Parse a log timestamp into an aware datetime."""
    match = _TIMESTAMP_PATTERN.match(raw)
    if match is None:
        try:
            return datetime.strptime(raw, _TIMESTAMP_FORMAT)
        except ValueError:
            return None

    day, month_name, year, hour, minute, second, sign, tz_h, tz_m = match.groups()
    month = _MONTHS.get(month_name.title())
    if month is None:
        return None

    try:
        return datetime(
            int(year),
            month,
            int(day),
            int(hour),
            int(minute),
            int(second),
            tzinfo=_timezone_for(sign, tz_h, tz_m),
        )
    except ValueError:
        # Out-of-range components, e.g. hour 99 or 31 February.
        return None


def _optional(value: str | None) -> str | None:
    """Map the log's several spellings of "absent" onto ``None``."""
    if value is None or value == "" or value == "-":
        return None
    return value


def parse_line(line: str) -> LogEntry | None:
    """Parse one log line, returning ``None`` when it is invalid."""
    # Callers typically iterate a file, so lines arrive with their newline
    # attached. `$` happens to tolerate a single trailing newline, but relying
    # on that is fragile, so strip it explicitly.
    match = _LOG_PATTERN.match(line.rstrip("\r\n"))
    if match is None:
        return None

    request_match = _REQUEST_PATTERN.match(match["request"])
    if request_match is None:
        # Covers "-", raw binary from vulnerability scanners, and paths with
        # unencoded spaces. Treated as malformed rather than partially parsed,
        # because there is no sensible path to attribute the request to.
        return None

    timestamp = _parse_timestamp(match["timestamp"])
    if timestamp is None:
        return None

    # "-" in the bytes field means zero bytes sent, not "missing". Matching only
    # \d+ here would reclassify every 304 and most 204s as a malformed line.
    raw_bytes = match["bytes"]
    bytes_sent = 0 if raw_bytes == "-" else int(raw_bytes)

    raw_request_time = match["request_time"]
    request_time = float(raw_request_time) if raw_request_time is not None else None

    return LogEntry(
        ip=match["ip"],
        timestamp=timestamp,
        method=request_match["method"],
        path=request_match["path"],
        protocol=request_match["protocol"] or "",
        status=int(match["status"]),
        bytes_sent=bytes_sent,
        referrer=_optional(match["referrer"]),
        user_agent=_optional(match["user_agent"]),
        request_time=request_time,
    )
