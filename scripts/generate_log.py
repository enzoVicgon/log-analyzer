#!/usr/bin/env python3
"""Generate synthetic access logs for local testing."""

from __future__ import annotations

import argparse
import random
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path

TIMESTAMP_FORMAT = "%d/%b/%Y:%H:%M:%S %z"

# Relative endpoint weights.
ENDPOINTS: list[tuple[str, int]] = [
    ("/", 25),
    ("/index.html", 15),
    ("/static/app.js", 12),
    ("/static/style.css", 10),
    ("/favicon.ico", 8),
    ("/api/users", 8),
    ("/api/products", 7),
    ("/search", 6),
    ("/api/orders", 5),
    ("/login", 4),
    ("/api/report", 3),
    ("/logout", 2),
    ("/admin", 1),
]

QUERY_TEMPLATES: dict[str, list[str]] = {
    "/search": ["?q=laptop", "?q=usb+cable", "?q=monitor", "?q=keyboard"],
    "/api/users": ["?page=1", "?page=2", "?page=3", "?limit=50"],
    "/api/products": ["?category=audio", "?category=video", "?sort=price"],
}

USER_AGENTS = [
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148",
    "curl/7.68.0",
    "python-requests/2.31.0",
    "Googlebot/2.1 (+http://www.google.com/bot.html)",
    "kube-probe/1.28",
]

REFERRERS = [
    "-",
    "https://www.google.com/",
    "https://example.com/",
    "https://example.com/index.html",
    "https://news.ycombinator.com/",
]

METHODS = [("GET", 85), ("POST", 10), ("PUT", 3), ("DELETE", 2)]

MALFORMED_SAMPLES = [
    "",
    "-- connection reset by peer --",
    '203.0.113.7 - - [invalid timestamp] "GET / HTTP/1.1" 200 100 "-" "UA"',
    '203.0.113.8 - - [{ts}] "-" 400 0 "-" "-"',
    '203.0.113.9 - - [{ts}] "GET /truncated',
]


def build_ip_pool(rng: random.Random, size: int = 400) -> list[str]:
    """Build a pool with a few high-volume clients."""
    pool = [f"192.0.2.{rng.randrange(1, 255)}" for _ in range(size // 2)]
    pool += [f"198.51.100.{rng.randrange(1, 255)}" for _ in range(size // 2)]
    heavy = pool[:5] * 40  # a few clients generate a lot of traffic
    return pool + heavy


def pick_status(rng: random.Random, path: str, in_error_burst: bool) -> int:
    if in_error_burst and rng.random() < 0.40:
        return rng.choices([500, 502, 503], weights=[6, 2, 2])[0]
    if path == "/admin":
        return rng.choices([403, 401, 200], weights=[6, 3, 1])[0]
    if path == "/login":
        return rng.choices([200, 401, 302], weights=[6, 3, 1])[0]
    return rng.choices(
        [200, 304, 301, 404, 403, 500],
        weights=[820, 90, 20, 50, 10, 10],
    )[0]


def pick_request_time(rng: random.Random, path: str, status: int) -> float:
    """Log-normal-ish latency: mostly fast, with a fat tail."""
    if path == "/api/report":
        base = rng.lognormvariate(0.4, 0.5)
    elif path.startswith("/static/") or path == "/favicon.ico":
        base = rng.lognormvariate(-5.5, 0.4)  # static files are quick
    elif path.startswith("/api/"):
        base = rng.lognormvariate(-3.2, 0.7)
    else:
        base = rng.lognormvariate(-2.6, 0.6)
    if status >= 500:
        base *= rng.uniform(1.5, 4.0)  # failures are often slow failures
    return round(min(base, 30.0), 3)


def pick_bytes(rng: random.Random, path: str, status: int) -> str:
    if status in (204, 304):
        return "-"
    if status >= 400:
        return str(rng.randrange(120, 900))
    if path.startswith("/static/"):
        return str(rng.randrange(2_000, 90_000))
    if path.startswith("/api/"):
        return str(rng.randrange(200, 12_000))
    return str(rng.randrange(800, 25_000))


def generate(
    *,
    count: int,
    hours: int,
    seed: int,
    include_timing: bool,
) -> Iterator[str]:
    rng = random.Random(seed)
    ip_pool = build_ip_pool(rng)

    end = datetime.now(timezone.utc).replace(microsecond=0)
    start = end - timedelta(hours=hours)
    span = (end - start).total_seconds()

    spike_start = span * 0.60
    spike_end = spike_start + min(20 * 60, span * 0.06)
    burst_start = span * 0.35
    burst_end = burst_start + min(15 * 60, span * 0.05)

    offsets = [
        rng.uniform(spike_start, spike_end)
        if rng.random() < 0.15
        else rng.uniform(0, span)
        for _ in range(count)
    ]
    offsets.sort()

    paths, path_weights = zip(*ENDPOINTS)
    methods, method_weights = zip(*METHODS)

    for offset in offsets:
        timestamp = (start + timedelta(seconds=offset)).strftime(TIMESTAMP_FORMAT)

        if rng.random() < 0.002:
            template = rng.choice(MALFORMED_SAMPLES)
            yield template.format(ts=timestamp)
            continue

        path = rng.choices(paths, weights=path_weights)[0]
        if path in QUERY_TEMPLATES and rng.random() < 0.6:
            path += rng.choice(QUERY_TEMPLATES[path])

        method = "GET" if path.startswith("/static/") else rng.choices(methods, weights=method_weights)[0]
        status = pick_status(rng, path.split("?", 1)[0], burst_start <= offset <= burst_end)

        line = (
            f"{rng.choice(ip_pool)} - - [{timestamp}] "
            f'"{method} {path} HTTP/1.1" '
            f"{status} {pick_bytes(rng, path, status)} "
            f'"{rng.choice(REFERRERS)}" "{rng.choice(USER_AGENTS)}"'
        )
        if include_timing:
            line += f" {pick_request_time(rng, path.split('?', 1)[0], status)}"
        yield line


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a synthetic nginx access log.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--lines", type=int, default=50_000, help="number of lines")
    parser.add_argument("--hours", type=int, default=24, help="time span to cover")
    parser.add_argument("--seed", type=int, default=42, help="RNG seed (reproducible)")
    parser.add_argument(
        "--out", type=Path, default=Path("samples/access.log"), help="output file"
    )
    parser.add_argument(
        "--no-timing",
        action="store_true",
        help="emit stock Combined Log Format, with no $request_time field",
    )
    args = parser.parse_args()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8", newline="\n") as handle:
        for line in generate(
            count=args.lines,
            hours=args.hours,
            seed=args.seed,
            include_timing=not args.no_timing,
        ):
            handle.write(line + "\n")

    size_mb = args.out.stat().st_size / 1_048_576
    print(f"Wrote {args.lines:,} lines to {args.out} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
