"""Storage boundaries. The job talks only to these; GCP and local backends implement them."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol

if TYPE_CHECKING:
    from filing_facts.extract.rows import DocumentText, ExtractRunRecord
    from filing_facts.parse.rows import ParseRunRecord
    from filing_facts.parse.spool import Spool

RunStatus = Literal["succeeded", "skipped_existing", "not_published", "failed"]


class AlreadyExistsError(Exception):
    """A create-only write found an existing object. Carries the existing object's sha256."""

    def __init__(self, key: str, existing_sha256: str | None) -> None:
        super().__init__(key)
        self.key = key
        self.existing_sha256 = existing_sha256


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    source_key: str
    source_url: str
    status: RunStatus
    started_at: datetime
    finished_at: datetime
    raw_uri: str | None = None
    sha256: str | None = None
    byte_count: int | None = None
    error: str | None = None


class RawStore(Protocol):
    """Immutable raw object store keyed by source_key."""

    def exists(self, key: str) -> bool: ...

    def uri_for(self, key: str) -> str: ...

    def fetch(self, key: str, dest: Path) -> None:
        """Copy the object to a local path. Raises KeyError if absent."""
        ...

    def put(self, key: str, path: Path, sha256: str) -> str:
        """Create-only write. Returns the object URI. Raises AlreadyExistsError if present."""
        ...


class RunLog(Protocol):
    """Append-only run ledger with idempotent writes."""

    def has_succeeded(self, source_key: str) -> bool: ...

    def succeeded_keys(self) -> list[str]:
        """Every source_key with a succeeded ingest, oldest first."""
        ...

    def record(self, record: RunRecord) -> bool:
        """Persist the record. Returns False if an identical dedupe key was already written."""
        ...


class ParseSink(Protocol):
    """Destination for parse output. Every write is idempotent on batch_id.

    Batches are pinned in the ledger before any data write (a `started` row listing the
    members), so a replay resumes exactly the same batch and hits the same load-job ids.
    """

    def processed_members(self, source_key: str) -> set[str]:
        """Member names already in documents or quarantine for this source."""
        ...

    def open_batch(self, source_key: str) -> tuple[str, list[str]] | None:
        """The most recent started batch with no succeeded row, as (batch_id, members)."""
        ...

    def succeeded_caps(self) -> dict[str, int]:
        """Highest cap at which each source has a succeeded or skipped_existing run."""
        ...

    def write_quarantine(self, batch_id: str, spool: Spool) -> bool: ...

    def write_facts(self, batch_id: str, spool: Spool) -> bool: ...

    def write_documents(self, batch_id: str, spool: Spool) -> bool: ...

    def record_run(self, record: ParseRunRecord) -> bool: ...


class ExtractSink(Protocol):
    """Destination for extraction output, and the source of the documents to extract from.

    Processed-ness is per (document, model, prompt): a document is pending for a model and
    prompt until it has an extraction row or an extract-stage quarantine row for that pair.
    """

    def pending_documents(self, model: str, prompt_id: str, cap: int) -> list[DocumentText]:
        """Up to `cap` unprocessed documents in a deterministic order."""
        ...

    def documents_by_id(self, ids: list[str]) -> list[DocumentText]: ...

    def processed_ids(self, model: str, prompt_id: str) -> set[str]:
        """Documents with an extraction or extract-stage quarantine row for this pair."""
        ...

    def open_batch(self, model: str, prompt_id: str) -> tuple[str, list[str]] | None: ...

    def write_quarantine(self, batch_id: str, spool: Spool) -> bool: ...

    def write_extractions(self, batch_id: str, spool: Spool) -> bool: ...

    def record_run(self, record: ExtractRunRecord) -> bool: ...
