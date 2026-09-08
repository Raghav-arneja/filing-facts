# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from filing_facts.config import Settings
from filing_facts.dispatcher.app import create_app
from filing_facts.dispatcher.runner import FakeRunner
from filing_facts.events.messages import LifecycleEvent, LifecycleKind


def envelope(payload: bytes | dict[str, Any], message_id: str = "m1") -> dict[str, Any]:
    raw = payload if isinstance(payload, bytes) else json.dumps(payload, default=str).encode()
    return {
        "message": {"data": base64.b64encode(raw).decode(), "messageId": message_id},
        "subscription": "s",
    }


def event(kind: LifecycleKind, **extra: Any) -> dict[str, Any]:
    return LifecycleEvent(
        event=kind,
        source_key="Accounts_Bulk_Data-2026-09-08.zip",
        run_id="r1",
        occurred_at=datetime.now(UTC),
        **extra,
    ).model_dump(mode="json")  # type: ignore[arg-type]


@pytest.fixture
def runner() -> FakeRunner:
    return FakeRunner()


def client(runner: FakeRunner, **settings: Any) -> TestClient:
    return TestClient(create_app(Settings(**settings), runner), raise_server_exceptions=False)


def test_ingested_starts_parse_for_that_source(runner: FakeRunner) -> None:
    r = client(runner).post("/pubsub", json=envelope(event("ingested")))
    assert r.status_code == 204
    assert runner.calls == [("parse", ["--source-key", "Accounts_Bulk_Data-2026-09-08.zip"])]


def test_parsed_is_acknowledged_without_action_unless_extraction_is_enabled(
    runner: FakeRunner,
) -> None:
    assert (
        client(runner).post("/pubsub", json=envelope(event("parsed", batch_id="b"))).status_code
        == 204
    )
    assert runner.calls == []
    r = client(runner, extract_on_event=True, extract_cap=42).post(
        "/pubsub", json=envelope(event("parsed", batch_id="b"))
    )
    assert r.status_code == 204
    assert runner.calls == [("extract", ["--cap", "42"])]


def test_extracted_needs_no_action(runner: FakeRunner) -> None:
    assert (
        client(runner)
        .post("/pubsub", json=envelope(event("extracted", batch_id="b", model="m", prompt_id="p")))
        .status_code
        == 204
    )
    assert runner.calls == []


@pytest.mark.parametrize(
    "payload",
    [
        b"not json",
        {"event": "ingested"},
        {
            **LifecycleEvent(
                event="ingested", source_key="x", run_id="r", occurred_at=datetime.now(UTC)
            ).model_dump(mode="json"),
            "version": 9,
        },
    ],
)
def test_malformed_events_are_rejected_permanently(
    runner: FakeRunner, payload: bytes | dict[str, Any]
) -> None:
    r = client(runner).post("/pubsub", json=envelope(payload))
    assert r.status_code == 400
    assert runner.calls == []


def test_missing_or_bad_base64_is_rejected(runner: FakeRunner) -> None:
    assert client(runner).post("/pubsub", json={"message": {"messageId": "m"}}).status_code == 400
    assert client(runner).post("/pubsub", json={"message": {"data": "@@@"}}).status_code == 400


def test_job_start_failure_asks_for_retry() -> None:
    runner = FakeRunner(fail=True)
    assert client(runner).post("/pubsub", json=envelope(event("ingested"))).status_code == 503


def test_duplicate_delivery_dispatches_twice_and_relies_on_job_idempotency(
    runner: FakeRunner,
) -> None:
    c = client(runner)
    c.post("/pubsub", json=envelope(event("ingested"), message_id="m1"))
    c.post("/pubsub", json=envelope(event("ingested"), message_id="m1"))
    assert len(runner.calls) == 2, "at-least-once delivery is passed through; the parse job skips"


def test_healthz(runner: FakeRunner) -> None:
    assert client(runner).get("/healthz").json() == {"status": "ok"}


def test_busy_job_asks_for_retry_instead_of_a_concurrent_execution() -> None:
    runner = FakeRunner(running={"parse"})
    r = client(runner).post("/pubsub", json=envelope(event("ingested")))
    assert r.status_code == 503
    assert runner.calls == []
