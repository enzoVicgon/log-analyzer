"""HTTP layer.

This module is deliberately thin. All it does is accept an upload, hand a
stream of lines to ``analyzer.analyze``, and return the result. Every
interesting decision lives in ``parser.py`` and ``analyzer.py``, which know
nothing about HTTP -- which is why they can be tested without a server.
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.analyzer import analyze
from app.models import Stats

MAX_UPLOAD_BYTES = 50 * 1024 * 1024

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(
    title="Log Analyzer",
    version="1.0.0",
    description=(
        "Upload an nginx/Apache access log and get back traffic statistics. "
        "Combined and Common Log Format are supported, with an optional "
        "trailing $request_time field for latency analysis."
    ),
)


@app.get("/api/health")
def health() -> dict[str, str]:
    """Liveness check."""
    return {"status": "ok"}


def _iter_lines(upload: UploadFile) -> Iterator[str]:
    """Yield decoded upload lines without reading the whole file."""
    stream = io.TextIOWrapper(upload.file, encoding="utf-8", errors="replace")
    consumed = 0
    for line in stream:
        consumed += len(line)
        if consumed > MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413, detail="Log file exceeds the 50 MB limit."
            )
        yield line


# NOTE: this is `def`, not `async def`, and that is deliberate.
#
# analyze() is CPU-bound and blocking -- it can run for seconds on a large
# file. An `async def` endpoint runs directly on the event loop, so it would
# freeze the entire server for every other request for that whole time.
# Declaring it `def` makes FastAPI run it in a worker threadpool instead,
# leaving the event loop free. For blocking work, sync is the correct choice.
@app.post("/api/analyze", response_model=Stats)
def analyze_upload(file: UploadFile) -> Stats:
    """Parse and analyze an uploaded access log."""
    if file.size is not None and file.size > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Log file is {file.size / 1_048_576:.1f} MB; the limit is 50 MB.",
        )
    return analyze(_iter_lines(file))


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")