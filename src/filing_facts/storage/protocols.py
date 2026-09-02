"""Storage boundaries. The job talks only to these; GCP and local backends implement them."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol

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

    def put(self, key: str, path: Path, sha256: str) -> str:
        """Create-only write. Returns the object URI. Raises AlreadyExistsError if present."""
        ...


class RunLog(Protocol):
    """Append-only run ledger with idempotent writes."""

    def has_succeeded(self, source_key: str) -> bool: ...

    def record(self, record: RunRecord) -> bool:
        """Persist the record. Returns False if an identical dedupe key was already written."""
        ...
