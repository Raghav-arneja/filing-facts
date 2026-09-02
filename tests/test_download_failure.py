from __future__ import annotations

from collections.abc import Callable

import httpx

from filing_facts.config import Settings
from filing_facts.ingest.job import run
from filing_facts.storage.memory import MemoryRawStore, MemoryRunLog
from tests.conftest import TARGET_DATE, Handler, make_zip, serve_bytes


def test_server_error_is_recorded_as_failed_and_stores_nothing(
    settings: Settings,
    store: MemoryRawStore,
    runlog: MemoryRunLog,
    client_for: Callable[[Handler], httpx.Client],
) -> None:
    client = client_for(serve_bytes(b"upstream broke", status=503))
    outcome = run(settings, client=client, store=store, runlog=runlog, target_date=TARGET_DATE)

    assert outcome.status == "failed"
    assert outcome.record is not None
    assert outcome.record.error is not None
    assert "503" in outcome.record.error
    assert store.objects == {}
    assert [r.status for r in runlog.records] == ["failed"]


def test_transport_error_is_recorded_as_failed(
    settings: Settings,
    store: MemoryRawStore,
    runlog: MemoryRunLog,
    client_for: Callable[[Handler], httpx.Client],
) -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    outcome = run(
        settings, client=client_for(boom), store=store, runlog=runlog, target_date=TARGET_DATE
    )
    assert outcome.status == "failed"
    assert store.objects == {}
    assert len(runlog.records) == 1


def test_not_found_means_not_published_not_failure(
    settings: Settings,
    store: MemoryRawStore,
    runlog: MemoryRunLog,
    client_for: Callable[[Handler], httpx.Client],
) -> None:
    client = client_for(serve_bytes(b"", status=404))
    outcome = run(settings, client=client, store=store, runlog=runlog, target_date=TARGET_DATE)

    assert outcome.status == "not_published"
    assert store.objects == {}
    assert [r.status for r in runlog.records] == ["not_published"]


def test_failure_then_success_yields_one_success_row(
    settings: Settings,
    store: MemoryRawStore,
    runlog: MemoryRunLog,
    client_for: Callable[[Handler], httpx.Client],
) -> None:
    run(
        settings,
        client=client_for(serve_bytes(b"", status=500)),
        store=store,
        runlog=runlog,
        target_date=TARGET_DATE,
    )
    run(
        settings,
        client=client_for(serve_bytes(make_zip())),
        store=store,
        runlog=runlog,
        target_date=TARGET_DATE,
    )

    assert [r.status for r in runlog.records] == ["failed", "succeeded"]
    assert len(store.objects) == 1
