"""Tests for the HTTP layer."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main
from app.main import app

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def upload(client: TestClient, name: str, content: bytes):
    return client.post("/api/analyze", files={"file": (name, io.BytesIO(content), "text/plain")})


def test_health(client: TestClient) -> None:
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_index_is_served(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_analyze_returns_the_expected_statistics(client: TestClient) -> None:
    response = upload(client, "access.log", (FIXTURES / "sample.log").read_bytes())

    assert response.status_code == 200
    body = response.json()
    assert body["total_requests"] == 20
    assert body["malformed_lines"] == 5
    assert body["unique_visitors"] == 7
    assert body["timing_available"] is True
    assert body["top_endpoints"][0] == {"path": "/index.html", "count": 4}


def test_response_matches_the_documented_schema(client: TestClient) -> None:
    """The response contains every field used by the frontend."""
    body = upload(client, "access.log", (FIXTURES / "sample.log").read_bytes()).json()

    expected = {
        "total_requests", "malformed_lines", "unique_visitors", "total_bytes",
        "error_rate", "first_timestamp", "last_timestamp", "status_counts",
        "status_classes", "top_endpoints", "top_errors", "timing_available",
        "avg_response_time", "slowest_requests", "timeline", "timeline_granularity",
    }
    assert expected <= body.keys()


def test_log_without_timing_reports_it_as_unavailable(client: TestClient) -> None:
    body = upload(client, "plain.log", (FIXTURES / "no_timing.log").read_bytes()).json()

    assert body["timing_available"] is False
    assert body["avg_response_time"] is None
    assert body["slowest_requests"] == []


def test_unparseable_text_is_a_200_with_zero_requests_not_a_500(client: TestClient) -> None:
    """Unparseable text is reported as an empty analysis."""
    response = upload(client, "notes.txt", b"hello\nthis is not a log\n")

    assert response.status_code == 200
    body = response.json()
    assert body["total_requests"] == 0
    assert body["malformed_lines"] == 2
    assert body["timeline"] == []


def test_empty_file(client: TestClient) -> None:
    response = upload(client, "empty.log", b"")

    assert response.status_code == 200
    assert response.json()["total_requests"] == 0


def test_binary_file_does_not_crash_the_decoder(client: TestClient) -> None:
    """Binary uploads should not raise a decoding error."""
    payload = bytes(range(256)) * 40

    response = upload(client, "image.log", payload)

    assert response.status_code == 200
    assert response.json()["total_requests"] == 0


def test_oversized_upload_is_rejected_with_413(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(main, "MAX_UPLOAD_BYTES", 1024)

    response = upload(client, "big.log", b"x" * 5000)

    assert response.status_code == 413
    assert "limit" in response.json()["detail"].lower()


def test_missing_file_field_is_a_422(client: TestClient) -> None:
    """FastAPI validates the multipart form from the type hints."""
    assert client.post("/api/analyze").status_code == 422
