"""Stage 2 parse job: one ingested ZIP in; documents, facts and quarantine rows out.

Invariants:
  * Selection is deterministic: members are ranked by a hash of their document id and the
    first `cap` are taken, so raising the cap later adds documents without reshuffling.
  * Members already in documents or quarantine for this source are never reprocessed.
  * A batch is pinned in the ledger (a `started` row listing its members) before any data
    write. A replay resumes that exact batch, so every load-job id is identical and the
    sink's idempotency holds whatever happened in between.
  * Every member in a batch ends up in exactly one of documents or quarantine.
  * Rows are spooled to disk as they are produced; the job holds one filing in memory.
"""

from __future__ import annotations

import hashlib
import tempfile
import uuid
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import structlog

from filing_facts.config import Settings
from filing_facts.parse.document_key import DocumentKey, document_key
from filing_facts.parse.errors import ParseError
from filing_facts.parse.ixbrl import parse_ixbrl_tree
from filing_facts.parse.rows import (
    DocumentRow,
    FactRow,
    ParseRunRecord,
    ParseStatus,
    QuarantineRow,
)
from filing_facts.parse.spool import Spool
from filing_facts.parse.text import render_text_tree
from filing_facts.parse.xml import parse_tree
from filing_facts.storage.memory import stable_order
from filing_facts.storage.protocols import ParseSink, RawStore, RunLog

log = structlog.get_logger(__name__)
STAGE = "parse"


@dataclass(frozen=True)
class ParseOutcome:
    status: ParseStatus
    record: ParseRunRecord


@dataclass(frozen=True)
class Selected:
    member: str
    key: DocumentKey


@dataclass(frozen=True)
class _Counts:
    parsed: int = 0
    quarantined: int = 0
    facts: int = 0


def select_members(names: list[str], cap: int) -> tuple[list[Selected], list[str]]:
    """Deterministic top-`cap` by document-id hash; unrecognised names, also capped, apart."""
    keyed: list[Selected] = []
    unknown: list[str] = []
    for n in names:
        if n.endswith("/"):
            continue  # directory entries are not records
        try:
            keyed.append(Selected(n, document_key(n)))
        except ParseError:
            unknown.append(n)
    keyed.sort(key=lambda s: stable_order(s.key.document_id))
    return keyed[:cap], sorted(unknown)[:cap]


def pending_source_keys(runlog: RunLog, sink: ParseSink, cap: int) -> list[str]:
    """Ingested sources with no succeeded parse at this cap or higher. Raising the cap re-queues."""
    caps = sink.succeeded_caps()
    return [k for k in runlog.succeeded_keys() if caps.get(k, -1) < cap]


def batch_id_for(members: list[str]) -> str:
    return hashlib.sha256("\n".join(sorted(members)).encode()).hexdigest()[:16]


def run(
    settings: Settings,
    *,
    store: RawStore,
    sink: ParseSink,
    source_key: str,
    cap: int | None = None,
    now: datetime | None = None,
) -> ParseOutcome:
    started = now or datetime.now(UTC)
    run_id = str(uuid.uuid4())
    cap = settings.parse_cap if cap is None else cap
    bound = log.bind(run_id=run_id, source_key=source_key, cap=cap)

    def record(
        status: ParseStatus,
        batch_id: str | None,
        selected: int,
        counts: _Counts,
        error: str | None = None,
        members: list[str] | None = None,
    ) -> ParseRunRecord:
        rec = ParseRunRecord(
            run_id=run_id,
            source_key=source_key,
            batch_id=batch_id,
            status=status,
            cap=cap,
            selected=selected,
            parsed=counts.parsed,
            quarantined=counts.quarantined,
            facts=counts.facts,
            started_at=started,
            finished_at=datetime.now(UTC),
            error=error,
            members=members or [],
        )
        sink.record_run(rec)
        return rec

    batch_id: str | None = None
    members: list[str] = []
    try:
        with tempfile.TemporaryDirectory(prefix="filing_facts_parse_") as tmp:
            zip_path = Path(tmp) / source_key
            store.fetch(source_key, zip_path)
            with zipfile.ZipFile(zip_path) as zf:
                resumed = sink.open_batch(source_key)
                if resumed is not None:
                    batch_id, members = resumed
                    bound.info("resuming_batch", batch_id=batch_id, members=len(members))
                else:
                    selected, unknown = select_members(zf.namelist(), cap)
                    done = sink.processed_members(source_key)
                    members = [m for m in [s.member for s in selected] + unknown if m not in done]
                    if not members:
                        bound.info("already_parsed", selected=len(selected))
                        rec = record("skipped_existing", None, 0, _Counts())
                        return ParseOutcome("skipped_existing", rec)
                    batch_id = batch_id_for(members)
                    record("started", batch_id, len(members), _Counts(), members=members)
                counts = _process(zf, sink, source_key, batch_id, members, Path(tmp), bound)
        rec = record("succeeded", batch_id, len(members), counts)
        bound.info("parse_succeeded", batch_id=batch_id, **counts.__dict__)
        return ParseOutcome("succeeded", rec)
    except Exception as exc:
        bound.error("parse_failed", batch_id=batch_id, error=str(exc))
        rec = record(
            "failed", batch_id, len(members), _Counts(), error=f"{type(exc).__name__}: {exc}"
        )
        return ParseOutcome("failed", rec)


def _process(
    zf: zipfile.ZipFile,
    sink: ParseSink,
    source_key: str,
    batch_id: str,
    members: list[str],
    tmp: Path,
    bound: structlog.stdlib.BoundLogger,
) -> _Counts:
    now = datetime.now(UTC)
    documents = Spool(tmp / "documents.ndjson", source_key)
    facts = Spool(tmp / "facts.ndjson", source_key)
    quarantine = Spool(tmp / "quarantine.ndjson", source_key)

    def quarantined(member: str, document_id: str | None, reason: str, error: str) -> None:
        quarantine.append(
            QuarantineRow(
                document_id=document_id,
                source_key=source_key,
                member_name=member,
                stage=STAGE,
                reason=reason,
                error=error[:1000],
                batch_id=batch_id,
                quarantined_at=now,
            )
        )

    for member in members:
        try:
            key = document_key(member)
        except ParseError as exc:
            quarantined(member, None, type(exc).__name__, str(exc))
            continue
        if key.is_cic_archive:
            quarantined(
                member, key.document_id, "nested_archive", "community interest company archive"
            )
            continue
        try:
            data = zf.read(member)
            root = parse_tree(data)
            parsed = parse_ixbrl_tree(root)
            text = render_text_tree(root)
        except ParseError as exc:
            quarantined(member, key.document_id, type(exc).__name__, str(exc))
            continue
        except Exception as exc:
            bound.warning("unexpected_parse_error", member=member, error=str(exc))
            quarantined(member, key.document_id, f"Unexpected:{type(exc).__name__}", str(exc))
            continue

        for f in parsed.facts:
            facts.append(
                FactRow.from_fact(key.document_id, f, parsed.contexts.get(f.context_id), batch_id)
            )
        documents.append(
            DocumentRow(
                document_id=key.document_id,
                source_key=source_key,
                member_name=member,
                company_number=key.company_number,
                period_end=key.period_end,
                byte_count=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
                ix_namespace=parsed.ix_namespace,
                entity_identifier=parsed.entity_identifier,
                fact_count=len(parsed.facts),
                text=text,
                batch_id=batch_id,
                parsed_at=now,
            )
        )
        del data, root, parsed, text

    for spool in (documents, facts, quarantine):
        spool.close()
    counts = _Counts(parsed=documents.count, quarantined=quarantine.count, facts=facts.count)
    bound.info("parsed_batch", batch_id=batch_id, members=len(members), **counts.__dict__)
    written = {
        "quarantine": sink.write_quarantine(batch_id, quarantine),
        "facts": sink.write_facts(batch_id, facts),
        "documents": sink.write_documents(batch_id, documents),
    }
    bound.info("batch_written", batch_id=batch_id, **written)
    return counts
