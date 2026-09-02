"""Replaying the same source file must not create a second object or a second success row."""

from __future__ import annotations

from collections.abc import Callable

import httpx

from filing_facts.config import Settings
from filing_facts.ingest.job import run
from filing_facts.storage.memory import MemoryRawStore, MemoryRunLog
from tests.conftest import TARGET_DATE, Handler, make_zip, serve_bytes


def test_second_run_is_a_no_op(
    settings: Settings,
    store: MemoryRawStore,
    runlog: MemoryRunLog,
    client_for: Callable[[Handler], httpx.Client],
) -> None:
    body = make_zip()
    calls = 0

    def counting(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return serve_bytes(body)(request)

    client = client_for(counting)
    first = run(settings, client=client, store=store, runlog=runlog, target_date=TARGET_DATE)
    second = run(settings, client=client, store=store, runlog=runlog, target_date=TARGET_DATE)

    assert first.status == "succeeded"
    assert second.status == "skipped_existing"
    assert calls == 1, "replay must not re-download"
    assert len(store.objects) == 1
    assert [r.status for r in runlog.records] == ["succeeded"]


def test_object_present_but_unrecorded_is_repaired_not_duplicated(
    settings: Settings,
    store: MemoryRawStore,
    runlog: MemoryRunLog,
    client_for: Callable[[Handler], httpx.Client],
) -> None:
    """Simulates a crash after the GCS write and before the BigQuery write."""
    body = make_zip()
    client = client_for(serve_bytes(body))
    run(settings, client=client, store=store, runlog=runlog, target_date=TARGET_DATE)
    runlog.records.clear()
    runlog._dedupe.clear()  # pyright: ignore[reportPrivateUsage]

    outcome = run(settings, client=client, store=store, runlog=runlog, target_date=TARGET_DATE)

    assert outcome.status == "succeeded"
    assert len(store.objects) == 1
    assert len(runlog.records) == 1
    assert runlog.records[0].raw_uri == store.uri_for(runlog.records[0].source_key)


def test_existing_object_with_different_bytes_is_a_recorded_failure(
    settings: Settings,
    store: MemoryRawStore,
    runlog: MemoryRunLog,
    client_for: Callable[[Handler], httpx.Client],
) -> None:
    run(
        settings,
        client=client_for(serve_bytes(make_zip(2))),
        store=store,
        runlog=runlog,
        target_date=TARGET_DATE,
    )
    runlog.records.clear()
    runlog._dedupe.clear()  # pyright: ignore[reportPrivateUsage]

    outcome = run(
        settings,
        client=client_for(serve_bytes(make_zip(5))),
        store=store,
        runlog=runlog,
        target_date=TARGET_DATE,
    )

    assert outcome.status == "failed"
    assert outcome.record is not None
    assert "HashMismatch" in (outcome.record.error or "")
    assert len(store.objects) == 1, "the original object must be untouched"


def test_ledger_rejects_duplicate_success_even_if_precheck_is_bypassed(
    runlog: MemoryRunLog,
) -> None:
    from datetime import UTC, datetime

    from filing_facts.storage.protocols import RunRecord

    now = datetime.now(UTC)
    rec = RunRecord("a", "k.zip", "u", "succeeded", now, now)
    assert runlog.record(rec) is True
    assert runlog.record(RunRecord("b", "k.zip", "u", "succeeded", now, now)) is False
    assert len(runlog.records) == 1
