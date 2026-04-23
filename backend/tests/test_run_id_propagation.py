"""Tests for Run-ID correlation (Task 2A.4, spec §10a).

Covers:
- RunIdMiddleware: generate when absent, echo when client-supplied.
- Contextvar helpers: round-trip set/get/reset.
- RunIdLoggerAdapter: prefix when run_id set, no prefix otherwise.

Why not a full integration test against /api/schedule: that path
exercises the DB and solver. Task 2A.4 scope is the plumbing itself
— the correlation layer. Solver-path smoke is covered by the
existing parity + full suite.
"""

from __future__ import annotations

import logging
import re
import uuid

import pytest
from fastapi.testclient import TestClient

from app.infrastructure.logging import (
    get_run_id,
    get_run_logger,
    reset_run_id,
    set_run_id,
)
from app.main import app

_client = TestClient(app)
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


# ---------------------------------------------------------------------------
# Middleware tests
# ---------------------------------------------------------------------------


def test_middleware_generates_run_id_when_absent() -> None:
    """Client did not send X-Run-Id → server generates UUID4 and echoes it.

    This is the common KBI-internal traffic pattern: no request-id
    plumbing on the frontend yet, so the middleware is the source
    of truth.
    """
    response = _client.get("/api/health")
    assert response.status_code == 200
    header = response.headers.get("X-Run-Id")
    assert header, "X-Run-Id header must be present on every response"
    assert _UUID_RE.match(header), f"X-Run-Id must be a UUID-36 string, got: {header!r}"


def test_middleware_echoes_client_run_id() -> None:
    """Client sent X-Run-Id → server preserves it verbatim on the response.

    Needed for end-to-end request tracing when a future frontend
    correlates its own telemetry with server-side run_ids.
    """
    supplied = "client-supplied-run-id-12345"
    response = _client.get("/api/health", headers={"X-Run-Id": supplied})
    assert response.status_code == 200
    assert response.headers.get("X-Run-Id") == supplied


# ---------------------------------------------------------------------------
# Contextvar round-trip
# ---------------------------------------------------------------------------


def test_get_run_id_returns_none_outside_context() -> None:
    """Pytest main thread has no middleware, no solver — no run_id."""
    # Defensive reset in case a prior test in the same session leaked.
    assert get_run_id() is None


def test_set_and_get_run_id_roundtrip() -> None:
    """set() → get() returns the value → reset(token) → get() returns None."""
    assert get_run_id() is None
    rid = str(uuid.uuid4())
    token = set_run_id(rid)
    try:
        assert get_run_id() == rid
    finally:
        reset_run_id(token)
    assert get_run_id() is None


# ---------------------------------------------------------------------------
# LoggerAdapter behavior
# ---------------------------------------------------------------------------


def test_logger_adapter_prefixes_when_run_id_set(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """With run_id in the contextvar, log records carry [run_id=...] prefix.

    Uses ``caplog`` so we capture the adapter's rewritten message
    regardless of project-wide logging handler config.
    """
    rid = "11111111-2222-3333-4444-555555555555"
    token = set_run_id(rid)
    try:
        logger = get_run_logger("test_run_id_propagation")
        with caplog.at_level(logging.INFO, logger="test_run_id_propagation"):
            logger.info("hello from solver")
    finally:
        reset_run_id(token)

    assert any(
        f"[run_id={rid}] hello from solver" in rec.getMessage()
        for rec in caplog.records
    ), (
        "Expected '[run_id=...] hello from solver' in captured log, "
        f"got: {[r.getMessage() for r in caplog.records]}"
    )


def test_logger_adapter_no_prefix_when_run_id_unset(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Without run_id in the contextvar, records emit unchanged.

    Keeps grep-friendly output for non-solve codepaths (health,
    equipment CRUD, etc.).
    """
    # Guard: ensure no leaked run_id from a prior test.
    assert get_run_id() is None

    logger = get_run_logger("test_run_id_propagation_noprefix")
    with caplog.at_level(logging.INFO, logger="test_run_id_propagation_noprefix"):
        logger.info("plain message")

    msgs = [r.getMessage() for r in caplog.records]
    assert any("plain message" == m for m in msgs), (
        f"Expected exact 'plain message' (no prefix), got: {msgs}"
    )
    # And explicitly assert no prefix snuck in.
    assert not any("[run_id=" in m for m in msgs), (
        f"Unexpected [run_id=...] prefix when contextvar unset: {msgs}"
    )
