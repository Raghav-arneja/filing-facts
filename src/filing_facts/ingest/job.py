"""Stage 1 ingest: fetch one daily Companies House ZIP, store it unmodified, record the run.

Invariants this module guarantees:
  * At most one raw object per source file (create-only store writes).
  * At most one 'succeeded' run record per source file (dedupe key on the ledger).
  * Every outcome, including failure, is recorded. Nothing is dropped silently.
"""

from __future__ import annotations

import tempfile
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import structlog

from filing_facts.config import Settings
from filing_facts.events.messages import LifecycleEvent
from filing_facts.events.publisher import EventPublisher, publish_after_success
from filing_facts.sources import companies_house as ch
from filing_facts.storage.protocols import (
    AlreadyExistsError,
    RawStore,
    RunLog,
    RunRecord,
    RunStatus,
)
from filing_facts.telemetry import tracer

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class RunOutcome:
    status: RunStatus
    record: RunRecord | None


def run(
    settings: Settings,
    *,
    client: httpx.Client,
    store: RawStore,
    runlog: RunLog,
    target_date: date,
    now: datetime | None = None,
    publisher: EventPublisher | None = None,
) -> RunOutcome:
    started_at = now or datetime.now(UTC)
    run_id = str(uuid.uuid4())
    key = ch.source_key(target_date)
    url = ch.source_url(settings.source_base_url, target_date)
    with tracer(__name__).start_as_current_span("ingest.run") as span:
        span.set_attributes({"run_id": run_id, "source_key": key})
        outcome = _run(settings, client, store, runlog, key, url, run_id, started_at, publisher)
        span.set_attribute("status", outcome.status)
        return outcome


def _run(
    settings: Settings,
    client: httpx.Client,
    store: RawStore,
    runlog: RunLog,
    key: str,
    url: str,
    run_id: str,
    started_at: datetime,
    publisher: EventPublisher | None,
) -> RunOutcome:
    bound = log.bind(run_id=run_id, source_key=key, source_url=url)

    if runlog.has_succeeded(key):
        bound.info("already_ingested")
        return RunOutcome("skipped_existing", None)

    def finish(status: RunStatus, **fields: object) -> RunOutcome:
        record = RunRecord(
            run_id=run_id,
            source_key=key,
            source_url=url,
            status=status,
            started_at=started_at,
            finished_at=datetime.now(UTC),
            **fields,  # type: ignore[arg-type]
        )
        written = runlog.record(record)
        bound.info("run_recorded", status=status, written=written)
        return RunOutcome(status, record)

    with tempfile.TemporaryDirectory(prefix="filing_facts_") as tmp:
        dest = Path(tmp) / key
        try:
            result = ch.download(client, url, dest, max_bytes=settings.max_bytes)
            members = ch.validate_zip(result.path)
        except ch.NotPublishedError:
            bound.info("not_published")
            return finish("not_published")
        except ch.SourceError as exc:
            bound.error("download_failed", error=str(exc))
            return finish("failed", error=f"{type(exc).__name__}: {exc}")

        bound.info(
            "downloaded", byte_count=result.byte_count, sha256=result.sha256, members=members
        )
        try:
            raw_uri = store.put(key, result.path, result.sha256)
        except AlreadyExistsError as exc:
            # Object landed on an earlier run that died before recording. Accept it only if
            # the bytes we just fetched match what is stored; otherwise stop and record.
            if exc.existing_sha256 != result.sha256:
                msg = f"existing object sha256={exc.existing_sha256} != downloaded {result.sha256}"
                bound.error("hash_mismatch", error=msg)
                return finish("failed", error=f"HashMismatch: {msg}")
            bound.info("object_already_present", sha256=result.sha256)
            raw_uri = store.uri_for(key)

    outcome = finish(
        "succeeded", raw_uri=raw_uri, sha256=result.sha256, byte_count=result.byte_count
    )
    publish_after_success(
        publisher,
        LifecycleEvent(
            event="ingested", source_key=key, run_id=run_id, occurred_at=datetime.now(UTC)
        ),
    )
    return outcome
