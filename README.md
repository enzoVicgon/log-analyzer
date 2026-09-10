# log-analyzer

A small web application for analyzing nginx/Apache access logs. Upload a log
file and get traffic statistics, status-code breakdowns, error hotspots,
latency outliers, and a traffic timeline.

<!-- TODO: add a screenshot here -- run the app, upload samples/access.log,
     and drop the image in as docs/screenshot.png -->

## Quickstart

```bash
python -m venv .venv
.venv/Scripts/activate          # Windows;  source .venv/bin/activate on Linux/macOS
pip install -r requirements.txt

# Generate a realistic 50k-line sample log
python scripts/generate_log.py --lines 50000 --hours 24

uvicorn app.main:app --reload
```

Then open <http://127.0.0.1:8000> and upload `samples/access.log`.

Interactive API documentation is at <http://127.0.0.1:8000/docs>.

## Supported log formats

**Combined Log Format** (the Apache and nginx default):

```
127.0.0.1 - frank [10/Oct/2000:13:55:36 -0700] "GET /apache_pb.gif HTTP/1.0" 200 2326 "http://example.com/start.html" "Mozilla/4.08 [en] (Win98; I ;Nav)"
```

**Common Log Format** (the same without the referrer and user-agent) is also
accepted.

### Response times

Combined Log Format has no response-time field, so "slowest requests" cannot be
computed from a stock log. An optional trailing `$request_time` is supported:

```nginx
log_format extended '$remote_addr - $remote_user [$time_local] "$request" '
                    '$status $body_bytes_sent "$http_referer" '
                    '"$http_user_agent" $request_time';
```

When it is absent the API returns `timing_available: false` and the UI hides its
latency panels with an explanation, rather than showing zeros or an empty table.

## Architecture

The log analysis is a **pure Python library that knows nothing about the web**.
FastAPI is only an adapter in front of it.

```
static/app.js ──POST multipart──▶ app/main.py ──▶ app/analyzer.py ──▶ app/parser.py
     ▲                            (thin HTTP)     (statistics)        (log format)
     └────────── Stats JSON ──────────┘
```

| Module | Responsibility |
|---|---|
| `app/parser.py` | One regex. `parse_line(str) -> LogEntry \| None`. Never raises. |
| `app/analyzer.py` | Single streaming pass over lines to a `Stats` object. |
| `app/models.py` | `LogEntry` dataclass + the Pydantic API models. |
| `app/main.py` | Three routes and an upload size cap. ~90 lines. |
| `static/` | Plain HTML/CSS/JS. No build step, no `node_modules`. |

Because the core is web-free, every interesting code path is unit-testable
without starting a server, and a CLI front-end would be about 15 lines.

## Design decisions

**No pandas.** The aggregation is the interesting part of the project, and
`collections.Counter` plus `heapq` do it natively. Writing it by hand also keeps
memory bounded: frequency counters grow with the number of *distinct* paths and
status codes rather than the number of lines, and the "slowest requests" list is
a fixed-size min-heap, so finding the top N costs O(n log N) time and O(N)
memory instead of sorting every row.

**No database.** The app is stateless: upload, analyze, return. Nothing needs to
outlive the request. The cost is that a page refresh loses the results and
re-analyzing means re-uploading — an acceptable trade for a tool of this size.

**No frontend framework.** Three charts and two tables do not justify a build
toolchain. One `<script>` tag for Chart.js, and everything else is `fetch` and
the DOM.

**Pydantic at the boundary, dataclasses in the hot loop.** A large upload
produces millions of `LogEntry` objects, so that type is a
`@dataclass(slots=True)` with no validation — the regex already guaranteed the
shape. `Stats` and its children are Pydantic, because they are the API contract
and generate the OpenAPI schema.

**The endpoint is `def`, not `async def`.** `analyze()` is CPU-bound and can run
for seconds. An `async def` endpoint executes on the event loop and would freeze
the server for every other request; a plain `def` endpoint is run by FastAPI in
a worker threadpool.

**Timestamps are parsed by hand.** `datetime.strptime` measured at ~60% of total
analysis time — it rebuilds a format cache and calls `getlocale()` on *every*
invocation. A dedicated parser for the one fixed shape Apache and nginx emit
roughly doubled throughput, with a test pinning it to `strptime` so the
optimization cannot silently change behaviour. It falls back to `strptime` for
anything it does not recognise.

**Timeline buckets are chosen after the fact.** Bucket size should depend on the
log's span, but the span is only known once every line has been read. So the
streaming pass accumulates per-minute counts and downsamples at the end
(minute → hour → day). Buckets with no traffic are still emitted with zero
counts: if only populated buckets were returned, a two-hour outage would render
as a straight line between the surrounding points and the chart would lie.

**Log data is treated as hostile.** Paths, referrers, and user-agents are
attacker-controlled — someone can request `/<img src=x onerror=...>` precisely
so it lands in your logs. All log-derived text enters the DOM through
`textContent`, never `innerHTML`.

## Testing

```bash
pytest -v
```

62 tests covering the parser (malformed lines, `-` byte counts, quoted-field
bleed, timezone handling), the analyzer (hand-computed fixture totals, bucket
granularity, gap filling, the bounded heap), and the API (413 on oversized
uploads, 200-with-zero-requests on garbage input, binary files).

The fixture totals in `tests/test_analyzer.py` were computed by hand from
`tests/fixtures/sample.log`, not read off the implementation.

You can also cross-check against the Unix tools:

```bash
wc -l samples/access.log
awk '{print $9}' samples/access.log | sort | uniq -c | sort -rn
```

Note that `awk` will disagree slightly, and it is the one that is wrong: on a
line with a malformed timestamp the extra space shifts every field right by
one, so `$9` reads the wrong column. That discrepancy is a decent argument for
the project existing.

## Performance

~90,000 lines/second on a single core. The 8.4 MB / 50,000-line sample analyzes
in about 0.55s locally, or ~0.75s end-to-end over HTTP. Memory stays flat
regardless of file size, because the upload is iterated lazily and every
accumulator is bounded — the one exception is the unique-visitor set, which
grows with the number of distinct client addresses.

Uploads are capped at 50 MB.

## Project layout

```
app/
  main.py          FastAPI routes, upload handling
  analyzer.py      streaming aggregation
  parser.py        Combined Log Format regex
  models.py        LogEntry dataclass + Pydantic API models
static/
  index.html       layout
  style.css        design tokens, light + dark
  app.js           fetch, DOM rendering, Chart.js config
scripts/
  generate_log.py  synthetic log generator (stdlib only)
tests/
  fixtures/        hand-written logs with known totals
  test_parser.py   test_analyzer.py  test_api.py
```

## Possible extensions

- A CLI front-end (`python -m app.cli access.log`) reusing `analyzer.analyze`
- Export results as JSON or CSV
- Client-side filtering by status class or time range
- JSON-lines log support — sniff the first line and dispatch to a second parser

## License

MIT — see [LICENSE](LICENSE).
